from __future__ import annotations

from app.orchestrator.service import create_orchestrator_app
from app.settings import get_settings

settings = get_settings()
app = create_orchestrator_app(settings)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=settings.orchestrator_host,
        port=settings.orchestrator_port,
        reload=False,
    )


if __name__ == '__main__':
    main()
