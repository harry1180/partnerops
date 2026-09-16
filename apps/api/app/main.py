"""Cloud PartnerOps API — FastAPI application factory.

Security posture (see docs/security-and-tenant-isolation.md):
- correlation IDs on every request/response + log line
- secure headers (CSP, nosniff, frame-ancestors, referrer policy, HSTS in prod)
- CORS with explicit origin allowlist + credentials
- CSRF double-submit enforced on session-authenticated unsafe methods
- rate limiting on login (Redis-backed, memory fallback)
- no stack traces to clients; structured error codes
- RLS-scoped DB sessions bound per request to the caller's org subtree
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging, correlation_id_var, get_logger, new_correlation_id
from app.core.state import STATE

settings = get_settings()
log = get_logger("cpo.api")

DESCRIPTION = """
Cloud PartnerOps — PartnerOps & cloud financial management platform.

All monetary values are fixed-precision Decimal serialized as strings.
Every tenant-scoped endpoint applies row-level security: unauthenticated
callers receive 401, unauthorized callers 403, and cross-tenant objects 404
(no existence leakage).
"""



@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    try:
        STATE["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
        await STATE["redis"].ping()
    except Exception as exc:  # redis optional locally
        log.warning("redis_unavailable", error=str(exc))
        STATE["redis"] = None
    try:
        from app.services.storage import get_storage

        get_storage().ensure_bucket()
    except Exception as exc:
        log.warning("object_storage_unavailable", error=str(exc))
    log.info("api_started", env=settings.app_env, version=settings.version)
    yield
    if STATE.get("redis"):
        await STATE["redis"].aclose()
    await engine.dispose()


app = FastAPI(
    title="Cloud PartnerOps API",
    description=DESCRIPTION,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("x-request-id") or new_correlation_id()
    correlation_id_var.set(cid)
    request.state.correlation_id = cid
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = cid
    if request.url.path.startswith("/api/"):
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 1),
            actor=getattr(getattr(request.state, "principal", None), "email", "anonymous"),
        )
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    if not settings.is_local:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "validation_error",
            "detail": [
                {"loc": list(e.get("loc", [])), "msg": str(e.get("msg", ""))[:200]} for e in exc.errors()[:10]
            ],
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    cid = getattr(request.state, "correlation_id", "")
    log.error("unhandled_error", error=str(exc), exc_info=True, correlation_id=cid)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"code": "internal_error", "correlation_id": cid},
    )


app.include_router(api_router, prefix="/api/v1")


# ------------------------------------------------------------------ health
@app.get("/health/live", tags=["health"])
async def health_live():
    return {"status": "ok", "version": settings.version}


@app.get("/health/ready", tags=["health"])
async def health_ready():
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        r = STATE.get("redis")
        checks["redis"] = "ok" if r and await r.ping() else "error"
    except Exception:
        checks["redis"] = "error"
    try:
        from app.services.storage import get_storage

        get_storage().list_prefix("_healthcheck", limit=1)
        checks["object_storage"] = "ok"
    except Exception:
        checks["object_storage"] = "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )


Instrumentator().instrument(app).expose(app, endpoint="/metrics")

__all__ = ["app", "STATE", "uuid"]
