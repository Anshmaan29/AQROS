"""Entrypoint: run the risk-engine service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_risk_engine.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_risk_engine.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
