"""End-to-end baseline lifecycle using saved samples and offline HTTPX."""

import copy
import json
from itertools import count
from pathlib import Path

import httpx
import pytest

from api_contract_watchdog import checker
from api_contract_watchdog.baselines import BaselineError, BaselineStore
from api_contract_watchdog.contracts import Product
from api_contract_watchdog.endpoints import Endpoint
from api_contract_watchdog.settings import Settings
from api_contract_watchdog.tracking import track_result


@pytest.fixture
def product():
    path = Path(__file__).parent / "fixtures" / "product_good.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path):
    return BaselineStore(tmp_path)


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    ticks = count()
    monkeypatch.setattr(checker, "perf_counter", lambda: next(ticks) / 1000)


def check(payload, store, status=200, max_ms=2000):
    endpoint = Endpoint("single_product", "https://watchdog.invalid/products/1", Product)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, content=json.dumps(payload))
    )) as client:
        result = checker.check_endpoint(client, endpoint, Settings(max_response_time_ms=max_ms))
    return track_result(result, store)


def test_first_good_response_creates_baseline_then_compares(product, store):
    first = check(product, store)
    assert first.passed
    assert first.diff is None
    assert first.checks[-2].status == "skipped"
    assert store.load(first.url).endpoint == first.endpoint
    second = check(product, store)
    assert second.passed
    assert second.diff.added_fields == []
    assert not second.diff.has_breaking_changes


@pytest.mark.parametrize("broken", ["missing", "type", "null", "status", "slow"])
def test_failed_response_keeps_baseline_and_good_response_recovers(product, store, broken):
    first = check(product, store)
    before = store.path_for(first.url).read_bytes()
    bad = copy.deepcopy(product)
    status = 200
    max_ms = 2000
    if broken == "missing":
        del bad["title"]
    elif broken == "type":
        bad["price"] = "9.99"
    elif broken == "null":
        bad["price"] = None
    elif broken == "status":
        status = 503
    else:
        max_ms = 0.5
    failed = check(bad, store, status=status, max_ms=max_ms)
    assert not failed.passed
    assert store.path_for(first.url).read_bytes() == before
    assert failed.checks[-1].status == "skipped"
    assert check(product, store).passed


def test_optional_field_removal_is_a_diff_failure(product, store):
    check(product, store)
    del product["brand"]
    result = check(product, store)
    assert next(c for c in result.checks if c.check_type == "contract").status == "passed"
    assert result.diff.removed_fields == ['$["brand"]']
    assert not result.passed


def test_optional_field_new_null_is_a_diff_failure(product, store):
    check(product, store)
    product["brand"] = None
    result = check(product, store)
    assert result.diff.new_nulls == ['$["brand"]']
    assert not result.passed


def test_addition_is_saved_and_then_no_longer_new(product, store):
    check(product, store)
    product["extra"] = {"flag": True}
    added = check(product, store)
    assert added.passed
    assert added.diff.added_fields == ['$["extra"]', '$["extra"]["flag"]']
    assert check(product, store).diff.added_fields == []


def test_existing_extra_field_type_change_breaks(product, store):
    product["extra"] = 1
    check(product, store)
    product["extra"] = "1"
    result = check(product, store)
    assert result.diff.type_changes[0].path == '$["extra"]'
    assert not result.passed


def test_empty_array_does_not_erase_item_expectations(product, store):
    first = check(product, store)
    shape = store.load(first.url).shape
    original = copy.deepcopy(product)
    product["reviews"] = []
    assert check(product, store).passed
    assert store.load(first.url).shape == shape
    assert check(original, store).diff.added_fields == []


@pytest.mark.parametrize("status,max_ms", [(503, 2000), (200, 0.5)])
def test_failure_cannot_initialize_baseline(product, store, status, max_ms):
    result = check(product, store, status=status, max_ms=max_ms)
    assert not result.passed
    assert store.load(result.url) is None


def test_json_null_is_compared_instead_of_mistaken_for_no_body(product, store):
    first = check(product, store)
    before = store.path_for(first.url).read_bytes()
    result = check(None, store)
    assert result.diff.new_nulls == ["$"]
    assert not result.passed
    assert store.path_for(first.url).read_bytes() == before


@pytest.mark.parametrize("response_type", ["timeout", "invalid_json"])
def test_no_json_does_not_touch_baseline(product, store, response_type):
    first = check(product, store)
    before = store.path_for(first.url).read_bytes()

    def handler(request):
        if response_type == "timeout":
            raise httpx.ReadTimeout("Simulated", request=request)
        return httpx.Response(200, content="not JSON")

    endpoint = Endpoint("single_product", first.url, Product)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = track_result(checker.check_endpoint(client, endpoint, Settings()), store)
    assert not result.passed
    assert result.diff is None
    assert result.checks[-2].status == "skipped"
    assert store.path_for(first.url).read_bytes() == before


@pytest.mark.parametrize("operation", ["load", "save"])
def test_storage_error_is_a_failed_result(product, store, monkeypatch, operation):
    def fail(*args):
        raise BaselineError("Simulated storage failure")

    monkeypatch.setattr(store, operation, fail)
    result = check(product, store)
    assert not result.passed
    assert result.checks[-1].check_type == "baseline"
    assert result.checks[-1].status == "failed"


def test_corrupt_baseline_is_not_overwritten(product, store):
    first = check(product, store)
    path = store.path_for(first.url)
    path.write_text("broken baseline", encoding="utf-8")
    result = check(product, store)
    assert not result.passed
    assert path.read_text(encoding="utf-8") == "broken baseline"


def test_check_all_continues_after_one_corrupt_baseline(tmp_path):
    settings = Settings(dummyjson_base_url="https://watchdog.invalid", state_dir=tmp_path)
    samples = {
        "/products": "products_good.json", "/products/1": "product_good.json",
        "/carts": "carts_good.json", "/users": "users_good.json",
    }
    fixtures = Path(__file__).parent / "fixtures"

    def handler(request):
        payload = json.loads((fixtures / samples[request.url.path]).read_text(encoding="utf-8"))
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = checker.check_all(client, settings)
        store = BaselineStore(tmp_path)
        store.path_for(first[0].url).write_text("broken", encoding="utf-8")
        second = checker.check_all(client, settings)
    assert not second[0].passed
    assert all(result.passed for result in second[1:])
