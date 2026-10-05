from __future__ import annotations

import uvicorn

from aqros_risk_engine.config import Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(
        "aqros_risk_engine.app:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )
