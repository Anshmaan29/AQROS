# risk-engine

Pre-trade checks and the sovereign hard risk kernel.

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_risk_engine.main` (listens on port 8005).
