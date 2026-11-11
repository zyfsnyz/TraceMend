import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import models  # noqa: F401
from .api import router
from .config import Settings
from .db import Base, make_engine, make_session_factory
from .mcp import router as mcp_router
from .proxy import router as proxy_router


def create_app() -> FastAPI:
    settings = Settings()
    engine = make_engine(settings.db_url)
    Base.metadata.create_all(engine)
    application = FastAPI(title="AgentTrace", version="0.1.0")
    application.state.settings = settings
    application.state.engine = engine
    application.state.session_factory = make_session_factory(engine)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    application.include_router(proxy_router)
    application.include_router(mcp_router)

    @application.middleware("http")
    async def admin_auth(request: Request, call_next):
        if (
            settings.admin_key
            and request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and request.method != "OPTIONS"
            and not secrets.compare_digest(
                request.headers.get("X-Admin-Key", ""), settings.admin_key
            )
        ):
            return JSONResponse(status_code=401, content={"detail": "管理密钥无效"})
        return await call_next(request)

    @application.get("/api/health")
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok", "version": "0.1.0"}

    return application


app = create_app()
