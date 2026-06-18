from __future__ import annotations

from app.settings import get_settings
from app.specialist.configs import get_specialist_config
from app.specialist.service import create_specialist_app

settings = get_settings()
config = get_specialist_config(settings.specialist_type)
app = create_specialist_app(settings, config)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=settings.specialist_host,
        port=settings.specialist_port,
        reload=False,
    )


if __name__ == '__main__':
    main()
