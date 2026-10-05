"""Configuration for the Strategy Engine service."""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "strategy-engine"
    port: int = 8011

    model_registry_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8004"),
        description="Base URL of the Model Registry Service's REST API.",
    )
    model_registry_request_timeout_seconds: float = 30.0

    feature_store_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8003"),
        description="Base URL of the Feature Store Service's REST API.",
    )
    feature_store_request_timeout_seconds: float = 30.0

    inference_service_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8014"),
        description="Base URL of the Inference Service's REST API.",
    )
    inference_request_timeout_seconds: float = 15.0

    risk_precheck_enabled: bool = True
    max_batch_size: int = 100
    signal_history_retention_days: int = 90

    metrics_enabled: bool = True
    event_publishing_enabled: bool = True
    audit_enabled: bool = True
