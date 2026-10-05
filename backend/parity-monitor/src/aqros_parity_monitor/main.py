"""Entrypoint: run the parity-monitor service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_parity_monitor.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "aqros_parity_monitor.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
