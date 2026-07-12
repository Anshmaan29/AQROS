# feature-store

Point-in-time feature serving (offline + online).

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_feature_store.main` (listens on port 8003).
