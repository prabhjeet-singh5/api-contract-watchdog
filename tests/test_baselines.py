"""Persistence tests use temporary directories, never the project's data folder."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_contract_watchdog.baselines import Baseline, BaselineError, BaselineStore
from api_contract_watchdog.diff import response_shape


@pytest.fixture
def baseline():
    return Baseline(
        endpoint="demo",
        url="https://watchdog.invalid/demo",
        saved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        shape=response_shape({"private": "DO-NOT-SAVE"}),
    )


def test_save_and_load(baseline, tmp_path):
    store = BaselineStore(tmp_path)
    assert store.load(baseline.url) is None
    store.save(baseline)
    loaded = store.load(baseline.url)
    assert loaded == baseline
    stored = store.path_for(baseline.url).read_text(encoding="utf-8")
    assert "DO-NOT-SAVE" not in stored
    assert json.loads(stored)["version"] == 1
    assert list(store.directory.glob("*.tmp")) == []


def test_urls_have_separate_baselines(tmp_path, baseline):
    store = BaselineStore(tmp_path)
    store.save(baseline)
    other = baseline.model_copy(update={"url": "https://other.invalid/demo"})
    store.save(other)
    assert store.path_for(baseline.url) != store.path_for(other.url)
    assert store.load(other.url).url == other.url


@pytest.mark.parametrize("content", [
    "not json", "{}", '{"version": 999}',
])
def test_corrupt_or_unsupported_baseline_is_an_error(tmp_path, baseline, content):
    store = BaselineStore(tmp_path)
    store.directory.mkdir()
    store.path_for(baseline.url).write_text(content, encoding="utf-8")
    with pytest.raises(BaselineError):
        store.load(baseline.url)
    assert store.path_for(baseline.url).read_text(encoding="utf-8") == content


@pytest.mark.parametrize("shape", [{}, {"$": []}, {"$": ["invalid-type"]}])
def test_invalid_stored_shape_is_rejected(tmp_path, baseline, shape):
    store = BaselineStore(tmp_path)
    store.directory.mkdir()
    stored = baseline.model_dump(mode="json")
    stored["shape"] = shape
    store.path_for(baseline.url).write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(BaselineError):
        store.load(baseline.url)


def test_mismatched_url_is_rejected(tmp_path, baseline):
    store = BaselineStore(tmp_path)
    store.save(baseline)
    wrong = baseline.model_copy(update={"url": "https://other.invalid/demo"})
    store.path_for(baseline.url).write_text(wrong.model_dump_json(), encoding="utf-8")
    with pytest.raises(BaselineError, match="URL"):
        store.load(baseline.url)


def test_failed_atomic_replace_keeps_original(tmp_path, baseline, monkeypatch):
    store = BaselineStore(tmp_path)
    store.save(baseline)
    original = store.path_for(baseline.url).read_bytes()

    def fail_replace(*args):
        raise PermissionError("Simulated write failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    updated = baseline.model_copy(update={"shape": response_shape({"new": True})})
    with pytest.raises(BaselineError):
        store.save(updated)
    assert store.path_for(baseline.url).read_bytes() == original
    assert list(store.directory.glob("*.tmp")) == []
