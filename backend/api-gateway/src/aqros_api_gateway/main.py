"""Entrypoint: run the api-gateway service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_api_gateway.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_api_gateway.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
