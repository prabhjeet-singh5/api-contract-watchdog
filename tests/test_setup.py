"""Offline smoke tests for the Phase 1 development environment."""

import importlib
import sys

import pydantic
import pytest


def test_python_version_is_supported():
    assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"


@pytest.mark.parametrize(
    "module_name",
    ["api_contract_watchdog", "httpx", "pytest", "fastapi", "uvicorn", "dotenv"],
)
def test_required_modules_import(module_name):
    importlib.import_module(module_name)


def test_pydantic_v2_is_installed():
    assert pydantic.__version__.split(".")[0] == "2"
