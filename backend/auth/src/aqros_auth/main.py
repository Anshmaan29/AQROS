"""Entrypoint: run the auth service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_auth.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_auth.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
