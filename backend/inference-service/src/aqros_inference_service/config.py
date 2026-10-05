"""Configuration for the inference service.

Extends :class:`BaseServiceSettings` with connections to the Model Registry,
Online Feature Store (Redis), and all inference tuning parameters.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, RedisDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """inference-service settings (override defaults via AQROS_* env vars)."""

    service_name: str = "inference-service"
    port: int = 8014

    # --- Model Registry REST client ---------------------------------------
    model_registry_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8004"),
        description="Base URL of the Model Registry Service's REST API.",
    )
    model_registry_request_timeout_seconds: float = 30.0

    # --- Online feature store ---------------------------------------------
    feature_store_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8003"),
        description="Base URL of the Feature Store Service's online REST API.",
    )
    feature_store_request_timeout_seconds: float = 10.0

    # --- Redis (feature cache / model metadata) ---------------------------
    redis_url: RedisDsn = Field(
        default=RedisDsn("redis://localhost:6379/2"),
        description="Redis connection string for feature cache.",
    )
    redis_pool_size: int = 5
    redis_connection_timeout_s: float = 5.0

    # --- Inference backend ------------------------------------------------
    inference_backend: str = Field(
        default="dummy",
        description="Inference backend: dummy, mlx, onnx.",
    )

    # --- Model management -------------------------------------------------
    model_cache_ttl_seconds: int = Field(
        default=3600,
        description="Time-to-live for cached model instances (seconds).",
    )
    hot_reload_poll_interval_seconds: int = Field(
        default=60,
        description="Interval for polling the model registry for new versions (seconds).",
    )
    max_loaded_models: int = Field(
        default=10,
        description="Maximum number of models kept in the cache simultaneously.",
    )

    # --- Prediction tuning ------------------------------------------------
    default_confidence_threshold: float = Field(
        default=0.5,
        description="Minimum confidence for a prediction to be considered valid.",
    )
    max_batch_size: int = Field(
        default=100,
        description="Maximum number of predictions in a single batch request.",
    )
    prediction_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout for a single prediction call.",
    )

    # --- Feature toggles --------------------------------------------------
    metrics_enabled: bool = True
    event_publishing_enabled: bool = True
    audit_enabled: bool = True
