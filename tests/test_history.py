"""Run history and failure/recovery transitions, using temporary directories."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api_contract_watchdog.history import (
    HistoryError, RunRecord, RunStore, record_run, track_states,
)
from api_contract_watchdog.results import CheckOutcome, EndpointResult

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def result(passed, minute=0, url="https://watchdog.invalid/demo"):
    return EndpointResult(
        endpoint="demo",
        url=url,
        checked_at=START + timedelta(minutes=minute),
        response_time_ms=1,
        checks=[
            CheckOutcome(check_type="network", status="passed", message="Simulated."),
            CheckOutcome(
                check_type="status_code",
                status="passed" if passed else "failed",
                message="Simulated.",
            ),
        ],
        payload={"private": "DO-NOT-SAVE"},
    )


def run(store, *results):
    record = RunRecord(started_at=START, finished_at=START, results=list(results))
    record_run(record, store)
    return record


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path)


def test_first_passing_run_has_no_changes(store):
    assert run(store, result(True)).changes == []
    state = store.load_states()["https://watchdog.invalid/demo"]
    assert state.status == "passing"
    assert state.consecutive_failures == 0


def test_first_failing_run_is_a_failure(store):
    record = run(store, result(False))
    assert [c.change for c in record.changes] == ["failed"]
    assert record.changes[0].failed_checks == ["status_code"]


def test_failure_is_reported_once_then_recovery_reports_outage_start(store):
    run(store, result(True, 0))
    failed = run(store, result(False, 5))
    assert [c.change for c in failed.changes] == ["failed"]
    assert failed.changes[0].failing_since == START + timedelta(minutes=5)

    still_failing = run(store, result(False, 10))
    assert still_failing.changes == []
    state = store.load_states()["https://watchdog.invalid/demo"]
    assert state.consecutive_failures == 2
    assert state.since == START + timedelta(minutes=5)

    recovered = run(store, result(True, 15))
    assert [c.change for c in recovered.changes] == ["recovered"]
    assert recovered.changes[0].failing_since == START + timedelta(minutes=5)
    assert recovered.changes[0].at == START + timedelta(minutes=15)
    assert recovered.changes[0].failed_checks == []
    assert store.load_states()["https://watchdog.invalid/demo"].consecutive_failures == 0


def test_endpoints_are_tracked_independently():
    a, b = "https://watchdog.invalid/a", "https://watchdog.invalid/b"
    states, _ = track_states({}, [result(True, url=a), result(True, url=b)])
    states, changes = track_states(states, [result(False, 1, url=a), result(True, 1, url=b)])
    assert [(c.url, c.change) for c in changes] == [(a, "failed")]
    assert states[b].status == "passing"


def test_history_appends_one_line_per_run_without_payloads(store):
    first = run(store, result(True))
    second = run(store, result(False, 1))
    lines = store.history_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run_id"] for line in lines] == [first.run_id, second.run_id]
    assert json.loads(lines[1])["passed"] is False
    assert all("DO-NOT-SAVE" not in line for line in lines)
    assert "DO-NOT-SAVE" not in store.state_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", [
    "not json", '{"version": 999, "endpoints": {}}',
    '{"version": 1, "endpoints": {"https://other.invalid": {"endpoint": "demo", '
    '"url": "https://watchdog.invalid/demo", "status": "passing", '
    '"since": "2026-01-01T00:00:00Z", "last_checked_at": "2026-01-01T00:00:00Z", '
    '"consecutive_failures": 0}}}',
])
def test_corrupt_state_is_an_error_and_not_overwritten(store, content):
    store.state_path.write_text(content, encoding="utf-8")
    record = RunRecord(started_at=START, finished_at=START, results=[result(True)])
    with pytest.raises(HistoryError):
        record_run(record, store)
    assert record.changes is None
    assert store.state_path.read_text(encoding="utf-8") == content
    assert not store.history_path.exists()


def test_failed_state_save_keeps_previous_state(store, monkeypatch):
    run(store, result(True))
    before = store.state_path.read_bytes()

    def fail_replace(*args):
        raise PermissionError("Simulated write failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(HistoryError, match="run state"):
        run(store, result(False, 1))
    assert store.state_path.read_bytes() == before
    assert list(store.state_path.parent.glob("*.tmp")) == []
