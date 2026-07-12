"""Entrypoint: run the portfolio service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_portfolio.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_portfolio.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
