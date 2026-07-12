"""Entrypoint: run the model-registry service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_model_registry.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_model_registry.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
