# audit-ledger

Append-only, tamper-evident record of every action.

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_audit_ledger.main` (listens on port 8007).
