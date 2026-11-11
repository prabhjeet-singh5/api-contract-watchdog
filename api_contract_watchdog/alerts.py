"""Send one alert per run that has failures or recoveries: n8n first, Slack as fallback."""

from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from .history import RunRecord, StateChange
from .results import AlertOutcome
from .settings import Settings


class AlertChange(BaseModel):
    endpoint: str
    url: str
    change: Literal["failed", "recovered"]
    at: datetime
    failing_since: datetime
    failed_checks: list[str]
    # The failed checks' plain-language messages; they never contain response values.
    details: list[str]


class AlertPayload(BaseModel):
    version: Literal[1] = 1
    source: Literal["api-contract-watchdog"] = "api-contract-watchdog"
    run_id: str
    run_url: str | None
    finished_at: datetime
    summary: str
    text: str
    changes: list[AlertChange]


def format_duration(duration: timedelta) -> str:
    seconds = max(int(duration.total_seconds()), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def build_payload(run: RunRecord, run_url: str | None) -> AlertPayload:
    results = {result.url: result for result in run.results}
    changes: list[AlertChange] = []
    lines: list[str] = []
    for change in run.changes or []:
        details = [
            f"{check.check_type}: {check.message}"
            for check in results[change.url].checks
            if check.status == "failed"
        ]
        changes.append(AlertChange(**change.model_dump(), details=details))
        lines.append(describe(change, details))

    failed = sum(change.change == "failed" for change in changes)
    recovered = len(changes) - failed
    summary = f"{failed} endpoint(s) failed, {recovered} recovered."
    text = "\n".join(["API contract watchdog: " + summary, *lines])
    if run_url:
        text += f"\nRun: {run_url}"
    return AlertPayload(
        run_id=run.run_id,
        run_url=run_url,
        finished_at=run.finished_at,
        summary=summary,
        text=text,
        changes=changes,
    )


def describe(change: StateChange, details: list[str]) -> str:
    if change.change == "recovered":
        downtime = format_duration(change.at - change.failing_since)
        return f"RECOVERED {change.endpoint} ({change.url}) after {downtime}."
    lines = [f"FAILED {change.endpoint} ({change.url}): {', '.join(change.failed_checks)}."]
    lines.extend(f"  - {detail}" for detail in details)
    return "\n".join(lines)


def post(client: httpx.Client, url: str, body: Any, timeout: float) -> str | None:
    """Return None on success, or a short error that never includes the URL."""
    try:
        response = client.post(url, json=body, timeout=timeout, follow_redirects=False)
    except httpx.RequestError as exc:
        return f"request failed ({type(exc).__name__})"
    if not response.is_success:
        return f"HTTP {response.status_code}"
    return None


def send_alert(client: httpx.Client, settings: Settings, run: RunRecord) -> AlertOutcome:
    run_url = str(settings.run_url) if settings.run_url else None
    payload = build_payload(run, run_url)
    errors: list[str] = []

    if settings.n8n_webhook_url:
        error = post(
            client, str(settings.n8n_webhook_url),
            payload.model_dump(mode="json"), settings.request_timeout_seconds,
        )
        if error is None:
            return AlertOutcome(status="sent", channel="n8n", message="Alert delivered to n8n.")
        errors.append(f"n8n: {error}")

    if settings.slack_webhook_url:
        error = post(
            client, str(settings.slack_webhook_url),
            {"text": payload.text}, settings.request_timeout_seconds,
        )
        if error is None:
            fallback = f" after {errors[0]}" if errors else ""
            return AlertOutcome(
                status="sent", channel="slack", message=f"Alert delivered to Slack{fallback}."
            )
        errors.append(f"Slack: {error}")

    if not errors:
        return AlertOutcome(status="skipped", message="No alert webhook is configured.")
    return AlertOutcome(status="failed", message="Alert not delivered: " + "; ".join(errors) + ".")
