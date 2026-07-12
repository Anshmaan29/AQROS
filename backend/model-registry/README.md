# model-registry

Versioned, governed store of models and lineage.

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_model_registry.main` (listens on port 8004).
