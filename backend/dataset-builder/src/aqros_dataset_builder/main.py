"""Entrypoint: run the dataset-builder service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_dataset_builder.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("aqros_dataset_builder.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
