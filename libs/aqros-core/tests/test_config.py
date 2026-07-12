"""Tests for typed configuration loading."""

from __future__ import annotations

import pytest

from aqros_core.config import BaseServiceSettings, Environment


def test_defaults() -> None:
    settings = BaseServiceSettings()
    assert settings.environment is Environment.DEV
    assert settings.port == 8000
    assert settings.is_production is False


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AQROS_PORT", "9123")
    monkeypatch.setenv("AQROS_ENVIRONMENT", "prod")
    settings = BaseServiceSettings()
    assert settings.port == 9123
    assert settings.environment is Environment.PROD
    assert settings.is_production is True
