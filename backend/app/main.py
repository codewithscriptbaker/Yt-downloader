from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging_config import setup_logging
from app.routes import router
from app.storage import get_storage


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    get_storage(settings).ensure_dirs()

    app = FastAPI(title="Media Downloader API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
