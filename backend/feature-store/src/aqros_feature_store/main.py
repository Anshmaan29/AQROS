"""Entrypoint: run the feature-store service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_feature_store.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_feature_store.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
