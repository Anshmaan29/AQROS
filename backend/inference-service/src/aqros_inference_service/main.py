"""Entrypoint: run the inference service with uvicorn."""

from __future__ import annotations

import uvicorn

from aqros_inference_service.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "aqros_inference_service.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
