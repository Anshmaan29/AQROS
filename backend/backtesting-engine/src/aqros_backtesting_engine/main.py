"""Entrypoint: run the backtesting-engine service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_backtesting_engine.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "aqros_backtesting_engine.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
