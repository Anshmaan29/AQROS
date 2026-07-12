# market-data

Vendor/venue feed boundary; normalize and publish ticks.

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_market_data.main` (listens on port 8002).
