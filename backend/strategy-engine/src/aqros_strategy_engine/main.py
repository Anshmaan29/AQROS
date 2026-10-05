"""Entrypoint: run the Strategy Engine with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_strategy_engine.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "aqros_strategy_engine.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
