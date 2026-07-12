# auth

Identity for humans (OIDC) and services (mTLS); RBAC.

Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). Business logic is added in later phases under `api/`, `domain/`, `adapters/`.

Run locally: `uv run python -m aqros_auth.main` (listens on port 8001).
