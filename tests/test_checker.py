"""Exercise real HTTPX requests through an offline MockTransport."""

import json
from itertools import count
from pathlib import Path

import httpx
import pytest

from api_contract_watchdog import checker
from api_contract_watchdog.contracts import Product
from api_contract_watchdog.endpoints import Endpoint, dummyjson_endpoints
from api_contract_watchdog.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def product():
    return json.loads((FIXTURES / "product_good.json").read_text(encoding="utf-8"))


@pytest.fixture
def endpoint():
    return Endpoint("single_product", "https://watchdog.invalid/products/1", Product)


@pytest.fixture
def settings(tmp_path):
    return Settings(dummyjson_base_url="https://watchdog.invalid", state_dir=tmp_path)


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    # Each request takes exactly 1 ms; no sleep or real-clock assertions.
    ticks = count()
    monkeypatch.setattr(checker, "perf_counter", lambda: next(ticks) / 1000)


def outcome(result, check_type):
    return next(check for check in result.checks if check.check_type == check_type)


def test_good_response_passes(product, endpoint, settings):
    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == endpoint.url
        assert request.extensions["timeout"]["read"] == 10
        return httpx.Response(200, json=product)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert result.passed
    assert result.status_code == 200
    assert result.response_time_ms == 1
    assert result.checked_at.utcoffset().total_seconds() == 0
    assert len(result.checks) == 4
    assert result.payload == product
    output = json.loads(result.model_dump_json())
    assert output["passed"] is True
    assert "payload" not in output


@pytest.mark.parametrize("break_type", ["missing", "wrong_type", "null"])
def test_contract_break_has_field_path(product, endpoint, settings, break_type):
    if break_type == "missing":
        del product["price"]
    elif break_type == "wrong_type":
        product["price"] = "PRIVATE-RESPONSE-VALUE"
    else:
        product["price"] = None
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=product)
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert not result.passed
    assert outcome(result, "status_code").status == "passed"
    assert outcome(result, "contract").errors[0].path == ["price"]
    assert result.payload == product
    assert "PRIVATE-RESPONSE-VALUE" not in result.model_dump_json()


@pytest.mark.parametrize("status", [201, 301, 401, 404, 500])
def test_unexpected_status_fails_even_with_good_body(product, endpoint, settings, status):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=product)
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert not result.passed
    assert result.status_code == status
    assert outcome(result, "status_code").status == "failed"
    assert outcome(result, "contract").status == "passed"


def test_redirect_is_not_followed(product, endpoint, settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "/other"}, json=product)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert len(calls) == 1
    assert result.status_code == 302
    assert not result.passed


@pytest.mark.parametrize("elapsed_ms,expected", [(1999, "passed"), (2000, "passed"), (2000.0001, "failed")])
def test_response_time_boundary(product, endpoint, settings, monkeypatch, elapsed_ms, expected):
    ticks = iter([0, elapsed_ms / 1000])
    monkeypatch.setattr(checker, "perf_counter", lambda: next(ticks))
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=product)
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert outcome(result, "response_time").status == expected
    assert result.passed == (expected == "passed")


def test_all_failures_are_reported_together(product, endpoint, settings, monkeypatch):
    product["price"] = None
    ticks = iter([0, 3])
    monkeypatch.setattr(checker, "perf_counter", lambda: next(ticks))
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, json=product)
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert {check.check_type for check in result.checks if check.status == "failed"} == {
        "status_code", "response_time", "contract"
    }


@pytest.mark.parametrize("content", [b"<html>Error</html>", b"", b"\xff"])
def test_invalid_json_fails_contract(endpoint, settings, content):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content)
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert not result.passed
    assert outcome(result, "contract").message == "Response is not valid JSON."


@pytest.mark.parametrize("payload", [None, [], 1, "wrong shape"])
def test_valid_json_with_wrong_root_shape_fails(endpoint, settings, payload):
    # Explicit content ensures None becomes JSON null rather than an empty body.
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps(payload))
    )) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert not result.passed
    assert outcome(result, "contract").errors[0].path == []


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout])
def test_network_failures_return_a_result(endpoint, settings, error_type):
    def handler(request):
        raise error_type("PRIVATE-EXCEPTION-DETAIL", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert not result.passed
    assert result.status_code is None
    assert result.response_time_ms == 1
    assert outcome(result, "network").status == "failed"
    assert all(check.status == "skipped" for check in result.checks if check.check_type != "network")
    assert error_type.__name__ in outcome(result, "network").message
    assert "PRIVATE-EXCEPTION-DETAIL" not in result.model_dump_json()


def test_configured_limits_reach_the_request(product, endpoint):
    settings = Settings(request_timeout_seconds=4, max_response_time_ms=0.5)

    def handler(request):
        assert set(request.extensions["timeout"].values()) == {4.0}
        return httpx.Response(200, json=product)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = checker.check_endpoint(client, endpoint, settings)
    assert outcome(result, "response_time").status == "failed"


def test_endpoint_builder_preserves_base_path():
    endpoints = dummyjson_endpoints(Settings(dummyjson_base_url="https://watchdog.invalid/api/"))
    assert [endpoint.url for endpoint in endpoints] == [
        "https://watchdog.invalid/api/products",
        "https://watchdog.invalid/api/products/1",
        "https://watchdog.invalid/api/carts",
        "https://watchdog.invalid/api/users",
    ]


@pytest.mark.parametrize("first_fails", [False, True])
def test_check_all_checks_every_endpoint(settings, first_fails):
    samples = {
        "/products": "products_good.json",
        "/products/1": "product_good.json",
        "/carts": "carts_good.json",
        "/users": "users_good.json",
    }
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if first_fails and request.url.path == "/products":
            raise httpx.ReadTimeout("Simulated timeout", request=request)
        payload = json.loads((FIXTURES / samples[request.url.path]).read_text(encoding="utf-8"))
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = checker.check_all(client, settings)
    assert calls == list(samples)
    assert len(results) == 4
    assert results[0].passed == (not first_fails)
    assert all(result.passed for result in results[1:])
