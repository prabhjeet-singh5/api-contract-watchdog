"""Alert payloads and webhook delivery, using offline HTTPX transports."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from api_contract_watchdog.alerts import build_payload, format_duration, send_alert
from api_contract_watchdog.history import RunRecord, RunStore, record_run
from api_contract_watchdog.results import AlertOutcome, CheckOutcome, EndpointResult
from api_contract_watchdog.settings import Settings

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
N8N = "https://n8n.invalid/webhook/SECRET-N8N-PATH"
SLACK = "https://hooks.slack.invalid/services/SECRET-SLACK-PATH"


def result(passed, minute=0):
    return EndpointResult(
        endpoint="users",
        url="https://watchdog.invalid/users",
        checked_at=START + timedelta(minutes=minute),
        response_time_ms=1,
        checks=[
            CheckOutcome(check_type="network", status="passed", message="HTTP response received."),
            CheckOutcome(
                check_type="contract",
                status="passed" if passed else "failed",
                message="Response matches its contract." if passed else "1 contract validation error(s).",
            ),
        ],
        payload={"private": "DO-NOT-SEND"},
    )


def recorded(store, *results, notify=None):
    run = RunRecord(started_at=START, finished_at=START, results=list(results))
    record_run(run, store, notify=notify)
    return run


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path)


def client_for(handlers, seen):
    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        reply = handlers[request.url.host]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(reply)

    return httpx.Client(transport=httpx.MockTransport(handler))


def failed_run(store):
    recorded(store, result(True))
    return recorded(store, result(False, 1))


@pytest.mark.parametrize("seconds,text", [(42, "42s"), (312, "5m 12s"), (7260, "2h 1m")])
def test_format_duration(seconds, text):
    assert format_duration(timedelta(seconds=seconds)) == text


def test_payload_describes_failure_and_recovery(store):
    failed = failed_run(store)
    payload = build_payload(failed, "https://ci.invalid/runs/7")
    assert payload.summary == "1 endpoint(s) failed, 0 recovered."
    assert payload.changes[0].failed_checks == ["contract"]
    assert payload.changes[0].details == ["contract: 1 contract validation error(s)."]
    assert "FAILED users" in payload.text
    assert payload.text.endswith("Run: https://ci.invalid/runs/7")
    assert "DO-NOT-SEND" not in payload.model_dump_json()

    recovered = recorded(store, result(True, 6))
    payload = build_payload(recovered, None)
    assert payload.summary == "0 endpoint(s) failed, 1 recovered."
    assert "RECOVERED users (https://watchdog.invalid/users) after 5m 0s." in payload.text
    assert "Run:" not in payload.text


def test_n8n_receives_full_payload(store):
    seen = []
    with client_for({"n8n.invalid": 200}, seen) as client:
        outcome = send_alert(client, Settings(n8n_webhook_url=N8N, slack_webhook_url=SLACK),
                             failed_run(store))
    assert outcome == AlertOutcome(status="sent", channel="n8n", message="Alert delivered to n8n.")
    assert len(seen) == 1
    assert seen[0][1]["source"] == "api-contract-watchdog"
    assert seen[0][1]["changes"][0]["change"] == "failed"


@pytest.mark.parametrize("n8n_reply", [500, httpx.ConnectError("Simulated")])
def test_slack_is_fallback_when_n8n_fails(store, n8n_reply):
    seen = []
    with client_for({"n8n.invalid": n8n_reply, "hooks.slack.invalid": 200}, seen) as client:
        outcome = send_alert(client, Settings(n8n_webhook_url=N8N, slack_webhook_url=SLACK),
                             failed_run(store))
    assert outcome.status == "sent"
    assert outcome.channel == "slack"
    assert "n8n:" in outcome.message
    assert list(seen[-1][1]) == ["text"]
    assert "SECRET" not in outcome.message


def test_slack_only(store):
    seen = []
    with client_for({"hooks.slack.invalid": 200}, seen) as client:
        outcome = send_alert(client, Settings(slack_webhook_url=SLACK), failed_run(store))
    assert (outcome.status, outcome.channel) == ("sent", "slack")
    assert outcome.message == "Alert delivered to Slack."


def test_all_webhooks_failing_is_a_failure_without_secrets(store):
    seen = []
    with client_for({"n8n.invalid": 503, "hooks.slack.invalid": 404}, seen) as client:
        outcome = send_alert(client, Settings(n8n_webhook_url=N8N, slack_webhook_url=SLACK),
                             failed_run(store))
    assert outcome.status == "failed"
    assert outcome.message == "Alert not delivered: n8n: HTTP 503; Slack: HTTP 404."


def test_no_webhook_is_skipped(store):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("No request"))) as client:
        outcome = send_alert(client, Settings(), failed_run(store))
    assert outcome.status == "skipped"


def test_no_changes_means_no_alert(store):
    calls = []
    run = recorded(store, result(True), notify=lambda r: calls.append(r))
    run = recorded(store, result(True, 1), notify=lambda r: calls.append(r))
    assert calls == []
    assert run.alert is None


def test_failed_alert_keeps_state_so_the_change_is_retried(store):
    recorded(store, result(True))
    failing = AlertOutcome(status="failed", message="Alert not delivered.")
    first = recorded(store, result(False, 1), notify=lambda r: failing)
    assert first.alert == failing
    assert store.load_states()["https://watchdog.invalid/users"].status == "passing"

    sent = AlertOutcome(status="sent", channel="n8n", message="Alert delivered to n8n.")
    retry = recorded(store, result(False, 2), notify=lambda r: sent)
    assert [c.change for c in retry.changes] == ["failed"]
    assert store.load_states()["https://watchdog.invalid/users"].status == "failing"
    history = store.history_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(history[1])["alert"]["status"] == "failed"
    assert "SECRET" not in "".join(history)
