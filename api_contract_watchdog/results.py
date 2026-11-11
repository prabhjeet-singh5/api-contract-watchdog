"""Small, JSON-serializable result objects for each endpoint and check."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from .diff import ShapeDiff


class ContractError(BaseModel):
    path: list[str | int]
    error_type: str
    message: str


class CheckOutcome(BaseModel):
    check_type: Literal["network", "status_code", "response_time", "contract", "diff", "baseline"]
    status: Literal["passed", "failed", "skipped"]
    message: str
    errors: list[ContractError] = Field(default_factory=list)


class EndpointResult(BaseModel):
    endpoint: str
    url: str
    checked_at: datetime
    status_code: int | None = None
    response_time_ms: float
    checks: list[CheckOutcome] = Field(min_length=1)
    diff: ShapeDiff | None = None
    # Retain the original JSON for the diff phase, without printing the body.
    payload: Any = Field(default=None, exclude=True, repr=False)
    # Distinguish valid JSON null from a timeout or an undecodable response.
    payload_available: bool = Field(default=False, exclude=True, repr=False)

    @computed_field
    @property
    def passed(self) -> bool:
        return all(check.status != "failed" for check in self.checks)
