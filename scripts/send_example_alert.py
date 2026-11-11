"""POST n8n/example_payload.json to N8N_WEBHOOK_URL to test the n8n workflow.

Run from the repository root: python scripts/send_example_alert.py
Prints only the HTTP status, never the webhook URL.
"""

import json
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_contract_watchdog.alerts import post  # noqa: E402
from api_contract_watchdog.settings import load_settings  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "n8n" / "example_payload.json"


def main() -> int:
    try:
        settings = load_settings()
    except ValidationError:
        print("Invalid configuration; run the checker to see which setting.", file=sys.stderr)
        return 2
    if settings.n8n_webhook_url is None:
        print("Set N8N_WEBHOOK_URL in .env first.", file=sys.stderr)
        return 2
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    with httpx.Client() as client:
        error = post(client, str(settings.n8n_webhook_url), payload, settings.request_timeout_seconds)
    if error:
        print(f"n8n did not accept the example alert: {error}.", file=sys.stderr)
        return 1
    print("n8n accepted the example alert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
