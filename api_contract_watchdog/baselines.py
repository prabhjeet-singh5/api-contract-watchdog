"""Store one versioned, value-free baseline per endpoint URL."""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .diff import Shape
from .storage import write_text_atomic


class BaselineError(Exception):
    """A baseline could not be read or written safely."""


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    endpoint: str
    url: str
    saved_at: datetime
    shape: Shape

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: Shape) -> Shape:
        if "$" not in value or any(not types for types in value.values()):
            raise ValueError("Baseline must have a root and nonempty type lists")
        return value


class BaselineStore:
    def __init__(self, state_dir: Path):
        self.directory = state_dir / "baselines"

    def path_for(self, url: str) -> Path:
        # URL identity prevents different APIs with the same endpoint name mixing.
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / f"{key}.json"

    def load(self, url: str) -> Baseline | None:
        try:
            baseline = Baseline.model_validate_json(
                self.path_for(url).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise BaselineError(f"Cannot read baseline ({type(exc).__name__}).") from exc
        if baseline.url != url:
            raise BaselineError("Baseline URL does not match the requested endpoint.")
        return baseline

    def save(self, baseline: Baseline) -> None:
        try:
            write_text_atomic(self.path_for(baseline.url), baseline.model_dump_json(indent=2))
        except OSError as exc:
            raise BaselineError(f"Cannot save baseline ({type(exc).__name__}).") from exc
