"""Record each run and detect when an endpoint fails or recovers."""

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .results import EndpointResult
from .storage import write_text_atomic


class HistoryError(Exception):
    """Run state or history could not be read or written safely."""


class EndpointState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    endpoint: str
    url: str
    status: Literal["passing", "failing"]
    # First check in the current status, so a recovery can report the outage start.
    since: datetime
    last_checked_at: datetime
    consecutive_failures: int = Field(ge=0)


class StateFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    endpoints: dict[str, EndpointState] = Field(default_factory=dict)


class StateChange(BaseModel):
    endpoint: str
    url: str
    change: Literal["failed", "recovered"]
    at: datetime
    failing_since: datetime
    failed_checks: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    version: Literal[1] = 1
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    started_at: datetime
    finished_at: datetime
    results: list[EndpointResult]
    # None when previous state could not be loaded, so changes are unknown.
    changes: list[StateChange] | None = None

    @computed_field
    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)


def track_states(
    states: dict[str, EndpointState], results: list[EndpointResult]
) -> tuple[dict[str, EndpointState], list[StateChange]]:
    updated = dict(states)
    changes: list[StateChange] = []
    for result in results:
        previous = updated.get(result.url)
        status = "passing" if result.passed else "failing"
        unchanged = previous is not None and previous.status == status
        if status == "failing" and not unchanged:
            # A first-ever run that fails is also a new failure worth reporting.
            changes.append(StateChange(
                endpoint=result.endpoint,
                url=result.url,
                change="failed",
                at=result.checked_at,
                failing_since=result.checked_at,
                failed_checks=[c.check_type for c in result.checks if c.status == "failed"],
            ))
        elif status == "passing" and previous is not None and not unchanged:
            changes.append(StateChange(
                endpoint=result.endpoint,
                url=result.url,
                change="recovered",
                at=result.checked_at,
                failing_since=previous.since,
            ))
        failures = 0
        if status == "failing":
            failures = (previous.consecutive_failures if unchanged else 0) + 1
        updated[result.url] = EndpointState(
            endpoint=result.endpoint,
            url=result.url,
            status=status,
            since=previous.since if unchanged else result.checked_at,
            last_checked_at=result.checked_at,
            consecutive_failures=failures,
        )
    return updated, changes


class RunStore:
    def __init__(self, state_dir: Path):
        self.state_path = state_dir / "state.json"
        self.history_path = state_dir / "history" / "runs.jsonl"

    def load_states(self) -> dict[str, EndpointState]:
        try:
            stored = StateFile.model_validate_json(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise HistoryError(f"Cannot read run state ({type(exc).__name__}).") from exc
        if any(url != state.url for url, state in stored.endpoints.items()):
            raise HistoryError("Run state URL keys do not match their entries.")
        return stored.endpoints

    def save_states(self, states: dict[str, EndpointState]) -> None:
        try:
            write_text_atomic(self.state_path, StateFile(endpoints=states).model_dump_json(indent=2))
        except OSError as exc:
            raise HistoryError(f"Cannot save run state ({type(exc).__name__}).") from exc

    def append_run(self, run: RunRecord) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(run.model_dump_json() + "\n")
        except OSError as exc:
            raise HistoryError(f"Cannot append run history ({type(exc).__name__}).") from exc


def record_run(run: RunRecord, store: RunStore) -> None:
    """Fill in the run's state changes, then persist history and state.

    History is appended before state is saved: if either write fails, the next
    run repeats the same transitions rather than silently missing one.
    """
    states = store.load_states()
    states, run.changes = track_states(states, run.results)
    store.append_run(run)
    store.save_states(states)
