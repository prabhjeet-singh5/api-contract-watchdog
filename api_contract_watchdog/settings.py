"""Load checker configuration from environment variables and a local .env."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, validate_default=True)

    dummyjson_base_url: HttpUrl = HttpUrl("https://dummyjson.com")
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    max_response_time_ms: float = Field(default=2000.0, gt=0)
    state_dir: Path = Path("data")

    @field_validator("state_dir", mode="before")
    @classmethod
    def validate_state_dir(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("State directory must not be empty")
        return value

    @field_validator("dummyjson_base_url")
    @classmethod
    def validate_base_url(cls, value: HttpUrl) -> HttpUrl:
        # Endpoint paths are appended to this URL; queries/fragments break that.
        if value.query is not None or value.fragment is not None:
            raise ValueError("Base URL must not contain a query or fragment")
        if value.username is not None or value.password is not None:
            raise ValueError("Base URL must not contain embedded credentials")
        return value


def load_settings() -> Settings:
    # Explicit shell/CI variables take precedence over the local .env file.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    variables = {
        "dummyjson_base_url": "DUMMYJSON_BASE_URL",
        "request_timeout_seconds": "REQUEST_TIMEOUT_SECONDS",
        "max_response_time_ms": "MAX_RESPONSE_TIME_MS",
        "state_dir": "STATE_DIR",
    }
    values = {
        field: os.environ[variable]
        for field, variable in variables.items()
        if variable in os.environ
    }
    return Settings.model_validate(values)
