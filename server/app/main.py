"""FastAPI application factory with middleware and router wiring."""

import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.api.v1 import api_v1
from app.config import get_settings
from app.core.rate_limit import limiter
from app.logging import RequestIdMiddleware, configure_logging

_DEFAULT_DEV_SECRET = "dev-secret-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager: startup and shutdown hooks."""
    s = get_settings()

    # Startup
    configure_logging(s.app_env)
    log = structlog.get_logger()
    log.info("startup", app_env=s.app_env)

    # Guard: refuse to boot in production with dev secret
    if s.app_env == "production" and s.jwt_secret == _DEFAULT_DEV_SECRET:
        log.error(
            "startup_failed",
            error="production mode with dev secret",
            app_env=s.app_env,
        )
        raise RuntimeError(
            "production mode with dev secret: set jwt_secret in .env"
        )

    yield

    # Shutdown
    log.info("shutdown")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    s = get_settings()

    app = FastAPI(
        title=s.app_name,
        lifespan=lifespan,
    )

    # Rate limiting: slowapi integration
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware: order matters (bottom-to-top execution)
    # RequestIdMiddleware adds x-request-id to all responses
    app.add_middleware(RequestIdMiddleware)

    # CORS: open in dev, restricted in prod
    if s.app_env == "development":
        allow_origins = ["*"]
    else:
        # In production, specify explicit origins in config
        allow_origins = ["https://redactly.ai"]  # fallback; override in prod .env if needed

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(api_v1, prefix=s.api_v1_prefix)

    return app


app = create_app()
