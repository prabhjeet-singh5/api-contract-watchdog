"""Configuration tests isolated from the shell and real .env files."""

import pytest
import os
from pathlib import Path
from pydantic import ValidationError

from api_contract_watchdog.settings import load_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # .env loading may introduce variables; restore the entire mapping afterward.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.chdir(tmp_path)
    for name in (
        "DUMMYJSON_BASE_URL", "REQUEST_TIMEOUT_SECONDS", "MAX_RESPONSE_TIME_MS", "STATE_DIR",
        "N8N_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "RUN_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    settings = load_settings()
    assert str(settings.dummyjson_base_url) == "https://dummyjson.com/"
    assert settings.request_timeout_seconds == 10
    assert settings.max_response_time_ms == 2000
    assert settings.state_dir == Path("data")


def test_state_directory_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "custom-state"))
    assert load_settings().state_dir == tmp_path / "custom-state"


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_state_directory_is_rejected(monkeypatch, value):
    monkeypatch.setenv("STATE_DIR", value)
    with pytest.raises(ValidationError):
        load_settings()


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("DUMMYJSON_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("MAX_RESPONSE_TIME_MS", "1500")
    settings = load_settings()
    assert str(settings.dummyjson_base_url) == "http://localhost:8000/"
    assert settings.request_timeout_seconds == 5.5
    assert settings.max_response_time_ms == 1500


def test_dotenv_loads_without_overwriting_environment(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "REQUEST_TIMEOUT_SECONDS=4\nMAX_RESPONSE_TIME_MS=1000\n", encoding="utf-8"
    )
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "7")
    settings = load_settings()
    assert settings.request_timeout_seconds == 7
    assert settings.max_response_time_ms == 1000


@pytest.mark.parametrize("name", ["REQUEST_TIMEOUT_SECONDS", "MAX_RESPONSE_TIME_MS"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "oops", ""])
def test_invalid_timing_setting_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize("value", [
    "not-a-url",
    "ftp://example.invalid",
    "",
    "https://example.invalid?limit=1",
    "https://example.invalid#fragment",
    "https://demo@example.invalid",
    "https://demo:demo@example.invalid",
])
def test_invalid_base_url_is_rejected(monkeypatch, value):
    monkeypatch.setenv("DUMMYJSON_BASE_URL", value)
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize("name", ["N8N_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "RUN_URL"])
def test_alert_urls_are_optional_and_blank_means_unset(monkeypatch, name):
    field = name.lower()
    assert getattr(load_settings(), field) is None
    monkeypatch.setenv(name, "  ")
    assert getattr(load_settings(), field) is None
    monkeypatch.setenv(name, "https://hooks.invalid/path")
    assert str(getattr(load_settings(), field)) == "https://hooks.invalid/path"


def test_invalid_webhook_is_rejected_without_echoing_it(monkeypatch, capsys):
    from api_contract_watchdog import __main__ as cli

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "not-a-url-SECRET")
    assert cli.main() == 2
    captured = capsys.readouterr()
    assert "slack_webhook_url" in captured.err
    assert "SECRET" not in captured.err
