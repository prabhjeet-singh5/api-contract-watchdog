"""The shipped n8n workflow and example payload stay in step with the alert format."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api_contract_watchdog.alerts import AlertPayload

N8N = Path(__file__).parent.parent / "n8n"
WORKFLOW = json.loads((N8N / "watchdog_alerts.workflow.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((N8N / "example_payload.json").read_text(encoding="utf-8"))

# Runs the Code node's JavaScript the way n8n does: $input.first().json.body.
HARNESS = """
let data = '';
process.stdin.on('data', (chunk) => (data += chunk)).on('end', () => {
  const { code, body } = JSON.parse(data);
  const $input = { first: () => ({ json: { body } }) };
  try {
    process.stdout.write(JSON.stringify(new Function('$input', code)($input)));
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: error.message }));
  }
});
"""


def node(name):
    return next(n for n in WORKFLOW["nodes"] if n["name"] == name)


def run_code(body):
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    completed = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps({"code": node("Format Slack message")["parameters"]["jsCode"], "body": body}),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )
    return json.loads(completed.stdout)


def test_example_payload_matches_the_alert_model():
    payload = AlertPayload.model_validate(EXAMPLE)
    assert {change.change for change in payload.changes} == {"failed", "recovered"}


def test_workflow_is_a_connected_three_node_chain():
    names = [n["name"] for n in WORKFLOW["nodes"]]
    assert names == ["Watchdog webhook", "Format Slack message", "Post to Slack"]
    for source, target in zip(names, names[1:]):
        assert WORKFLOW["connections"][source]["main"][0][0]["node"] == target
    webhook = node("Watchdog webhook")["parameters"]
    assert (webhook["httpMethod"], webhook["responseMode"]) == ("POST", "lastNode")


def test_workflow_ships_without_secrets():
    slack = node("Post to Slack")["parameters"]
    assert "REPLACE" in slack["url"]
    assert "credentials" not in json.dumps(WORKFLOW)


def test_code_node_formats_failures_recoveries_and_link():
    [item] = run_code(EXAMPLE)
    text = item["json"]["text"]
    assert text.splitlines() == [
        "*API contract watchdog:* 1 endpoint(s) failed, 1 recovered.",
        ":red_circle: *FAILED* `carts`: status_code, contract",
        "    • status_code: Expected 200, received 500.",
        "    • contract: 4 contract validation error(s).",
        ":large_green_circle: *RECOVERED* `users` after 5m 12s",
        "<https://github.com/prabhjeet-singh5/api-contract-watchdog/actions|View the run>",
    ]


def test_code_node_escapes_slack_markup():
    body = json.loads(json.dumps(EXAMPLE))
    body["changes"][0]["details"] = ["contract: <b>&</b>"]
    text = run_code(body)[0]["json"]["text"]
    assert "contract: &lt;b&gt;&amp;&lt;/b&gt;" in text


@pytest.mark.parametrize("body", [None, {}, {**EXAMPLE, "source": "other"}, {**EXAMPLE, "version": 2}])
def test_code_node_rejects_other_payloads(body):
    assert "error" in run_code(body)
