"""Entrypoint: run the market-data service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_market_data.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_market_data.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
