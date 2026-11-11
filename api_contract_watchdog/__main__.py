"""Run once with: python -m api_contract_watchdog."""

import sys
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from .checker import check_all
from .history import HistoryError, RunRecord, RunStore, record_run
from .settings import load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ValidationError as exc:
        print("Invalid checker configuration:", file=sys.stderr)
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"])
            print(f"  {field}: {error['msg']}", file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc)
    with httpx.Client() as client:
        results = check_all(client, settings)
    run = RunRecord(
        started_at=started_at, finished_at=datetime.now(timezone.utc), results=results
    )
    recorded = True
    try:
        record_run(run, RunStore(settings.state_dir))
    except HistoryError as exc:
        print(f"Run history was not recorded: {exc}", file=sys.stderr)
        recorded = False
    print(run.model_dump_json(indent=2))
    return 0 if run.passed and recorded else 1


if __name__ == "__main__":
    raise SystemExit(main())
