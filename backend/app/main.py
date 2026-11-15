import secrets

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import config, models  # noqa: F401
from .api import router
from .db import Base, make_engine, make_session_factory
from .gateway import CircuitRegistry
from .gateway_api import router as gateway_router
from .mcp import router as mcp_router
from .proxy import router as proxy_router
from .settings_api import router as settings_router


def admin_key_matches(candidate: str, expected: str) -> bool:
    """Constant-time comparison; non-ASCII candidates compare safely as false."""
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def create_app() -> FastAPI:
    settings = config.Settings(_env_file=config.env_files())
    engine = make_engine(settings.db_url)
    Base.metadata.create_all(engine)
    application = FastAPI(title="AgentTrace", version="0.2.0")
    application.state.settings = settings
    application.state.env_path = config.PROJECT_ENV
    application.state.bootstrap_env_path = config.BOOTSTRAP_ENV
    application.state.engine = engine
    application.state.session_factory = make_session_factory(engine)
    application.state.circuits = CircuitRegistry()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    application.include_router(settings_router)
    application.include_router(gateway_router)
    application.include_router(proxy_router)
    application.include_router(mcp_router)

    @application.exception_handler(RequestValidationError)
    async def validation_error_without_sensitive_input(_request, exc: RequestValidationError):
        # Pydantic's default ``input``/``ctx`` can contain the complete request body,
        # including API keys or URL passwords. Keep actionable locations/messages only.
        detail = [
            {key: value for key, value in error.items() if key not in {"input", "ctx"}}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": detail}),
        )

    @application.middleware("http")
    async def admin_auth(request: Request, call_next):
        current_settings = request.app.state.settings
        if (
            current_settings.admin_key
            and request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and request.method != "OPTIONS"
            and not admin_key_matches(
                request.headers.get("X-Admin-Key", ""), current_settings.admin_key
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
