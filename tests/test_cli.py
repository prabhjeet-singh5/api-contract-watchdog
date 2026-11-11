"""CLI output and exit codes, with no live requests."""

import json
from datetime import datetime, timezone

import pytest
from api_contract_watchdog import __main__ as cli
from api_contract_watchdog.results import CheckOutcome, EndpointResult
from api_contract_watchdog.settings import Settings


@pytest.mark.parametrize("passed,exit_code", [(True, 0), (False, 1)])
def test_cli_prints_results_and_returns_exit_code(monkeypatch, capsys, tmp_path, passed, exit_code):
    result = EndpointResult(
        endpoint="demo",
        url="https://watchdog.invalid/demo",
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        response_time_ms=1,
        checks=[CheckOutcome(
            check_type="contract",
            status="passed" if passed else "failed",
            message="Simulated result.",
        )],
        payload={"private": "DO-NOT-PRINT"},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(state_dir=tmp_path))
    monkeypatch.setattr(cli, "check_all", lambda client, settings: [result])
    assert cli.main() == exit_code
    captured = capsys.readouterr()
    run = json.loads(captured.out)
    assert run["passed"] == passed
    assert run["results"][0]["passed"] == passed
    assert [change["change"] for change in run["changes"]] == ([] if passed else ["failed"])
    assert "DO-NOT-PRINT" not in captured.out
    assert captured.err == ""
    history = (tmp_path / "history" / "runs.jsonl").read_text(encoding="utf-8")
    assert json.loads(history)["run_id"] == run["run_id"]
    assert "DO-NOT-PRINT" not in history


def test_unrecorded_history_fails_a_passing_run(monkeypatch, capsys, tmp_path):
    result = EndpointResult(
        endpoint="demo",
        url="https://watchdog.invalid/demo",
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        response_time_ms=1,
        checks=[CheckOutcome(check_type="contract", status="passed", message="Simulated.")],
    )
    (tmp_path / "state.json").write_text("broken state", encoding="utf-8")
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(state_dir=tmp_path))
    monkeypatch.setattr(cli, "check_all", lambda client, settings: [result])
    assert cli.main() == 1
    captured = capsys.readouterr()
    run = json.loads(captured.out)
    assert run["passed"] is True
    assert run["changes"] is None
    assert "Run history was not recorded" in captured.err
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == "broken state"


def test_invalid_configuration_exits_before_requests(monkeypatch, capsys):
    def invalid_settings():
        return Settings(request_timeout_seconds=-1)

    def unexpected_call(*args):
        pytest.fail("Requests must not run with invalid configuration")

    monkeypatch.setattr(cli, "load_settings", invalid_settings)
    monkeypatch.setattr(cli, "check_all", unexpected_call)
    assert cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "request_timeout_seconds" in captured.err
