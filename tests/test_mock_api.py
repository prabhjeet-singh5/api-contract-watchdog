"""Mock API routes, admin auth, and the checker running against it in-process."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api_contract_watchdog import checker
from api_contract_watchdog.history import RunRecord, RunStore, record_run
from api_contract_watchdog.settings import Settings
from mock_api.app import create_app

TOKEN = "test-admin-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE_URL = "http://mock.invalid"


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN), base_url=BASE_URL) as client:
        yield client


def test_data_routes_and_health(client):
    for path in ["/products", "/products/1", "/carts", "/users", "/health"]:
        assert client.get(path).status_code == 200
    assert client.get("/").json()["endpoints"] == ["/products", "/products/1", "/carts", "/users"]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token-0123456789"}])
def test_admin_requires_token(client, headers):
    assert client.get("/admin/breaks", headers=headers).status_code == 401
    response = client.put("/admin/breaks/products", json={"mode": "server_error"}, headers=headers)
    assert response.status_code == 401
    assert client.get("/products").status_code == 200


def test_admin_is_disabled_without_token(monkeypatch):
    monkeypatch.delenv("MOCK_API_ADMIN_TOKEN", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/admin/breaks", headers=AUTH).status_code == 503
        assert client.get("/products").status_code == 200


def test_short_token_is_rejected():
    with pytest.raises(ValueError, match="at least"):
        create_app("short")


def test_set_list_and_clear_breaks(client):
    assert client.put(
        "/admin/breaks/carts", json={"mode": "new_null"}, headers=AUTH
    ).json()["carts"]["mode"] == "new_null"
    assert client.get("/carts").json()["carts"][0]["total"] is None
    assert list(client.get("/admin/breaks", headers=AUTH).json()) == ["carts"]
    assert client.delete("/admin/breaks/carts", headers=AUTH).json() == {}
    assert client.get("/carts").json()["carts"][0]["total"] is not None


@pytest.mark.parametrize("change,status", [
    ({"mode": "unknown"}, 422), ({"mode": "slow", "delay_ms": 60_000}, 422),
])
def test_invalid_break_is_rejected(client, change, status):
    assert client.put("/admin/breaks/users", json=change, headers=AUTH).status_code == status
    assert client.put("/admin/breaks/missing", json={"mode": "slow"}, headers=AUTH).status_code == 422


def run_checker(client, store, max_ms=2000):
    settings = Settings(dummyjson_base_url=BASE_URL, state_dir=store.state_path.parent,
                        max_response_time_ms=max_ms)
    now = datetime.now(timezone.utc)
    run = RunRecord(started_at=now, finished_at=now, results=checker.check_all(client, settings))
    record_run(run, store)
    return run


def failed_checks(run, endpoint):
    result = next(r for r in run.results if r.endpoint == endpoint)
    return {c.check_type for c in result.checks if c.status == "failed"}


@pytest.mark.parametrize("change,expected", [
    ({"mode": "remove_field"}, {"contract", "diff"}),
    ({"mode": "type_change"}, {"contract", "diff"}),
    ({"mode": "new_null"}, {"contract", "diff"}),
    ({"mode": "server_error"}, {"status_code", "contract", "diff"}),
    ({"mode": "invalid_json"}, {"contract"}),
    ({"mode": "slow", "delay_ms": 50}, {"response_time"}),
])
def test_checker_detects_each_break_and_recovery(client, tmp_path, change, expected):
    store = RunStore(tmp_path)
    # A generous limit for the baseline run; the slow break run uses a tight one.
    assert run_checker(client, store, max_ms=10_000).passed

    client.put("/admin/breaks/single_product", json=change, headers=AUTH)
    broken = run_checker(client, store, max_ms=25 if change["mode"] == "slow" else 10_000)
    assert failed_checks(broken, "single_product") == expected
    assert [(c.endpoint, c.change) for c in broken.changes] == [("single_product", "failed")]
    assert all(r.passed for r in broken.results if r.endpoint != "single_product")

    client.delete("/admin/breaks", headers=AUTH)
    recovered = run_checker(client, store, max_ms=10_000)
    assert recovered.passed
    assert [(c.endpoint, c.change) for c in recovered.changes] == [("single_product", "recovered")]


def test_added_field_is_reported_without_failing(client, tmp_path):
    store = RunStore(tmp_path)
    run_checker(client, store, max_ms=10_000)
    client.put("/admin/breaks/products", json={"mode": "added_field"}, headers=AUTH)
    run = run_checker(client, store, max_ms=10_000)
    assert run.passed
    products = next(r for r in run.results if r.endpoint == "products")
    assert products.diff.added_fields == ['$["products"][*]["mockExtra"]']
