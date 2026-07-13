"""Entrypoint: run the training-pipeline service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_training_pipeline.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_training_pipeline.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
