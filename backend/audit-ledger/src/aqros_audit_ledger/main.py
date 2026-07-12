"""Entrypoint: run the audit-ledger service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_audit_ledger.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_audit_ledger.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
