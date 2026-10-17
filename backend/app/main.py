import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.routing import Route

from .api import audit_api, auth, demo_api, imports, intake, portfolio, settings_api, templates, usecases
from .auth import client_ip, hash_password
from .config import get_settings
from .db import SessionLocal, init_db
from .mcp_server import build_asgi_app, build_server
from .models import AnalysisRun, ImportJob, Role, UseCase, User
from .services import audit
from .services import llm_settings
from .services.templates import sync_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("usecasehub")


async def _recover_orphaned_jobs(session) -> None:
    """Jobs left 'running' by a previous process can never finish: mark them so they can be re-run (F-08)."""
    now = datetime.now(timezone.utc)
    n = 0
    for run in (await session.scalars(select(AnalysisRun).where(AnalysisRun.status == "running"))).all():
        run.status, run.error, run.finished_at = "failed", "interrupted by restart", now
        n += 1
    for job in (await session.scalars(select(ImportJob).where(ImportJob.status == "running"))).all():
        job.status = "failed"
        job.errors = [*(job.errors or []), {"row": 0, "error": "interrupted by restart; re-run the import"}]
        n += 1
    if n:
        await audit.record(session, None, "system.recover_jobs", details={"count": n}, channel="system")
        log.warning("Marked %d interrupted job(s) as failed", n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await init_db()
    async with SessionLocal() as session:
        await sync_templates(session)
        admin = await session.scalar(select(User).where(User.role == Role.admin.value))
        if not admin:
            session.add(User(email=s.admin_email.lower(), full_name="Administrator", role=Role.admin.value,
                             password_hash=hash_password(s.admin_password), must_change_password=not s.is_dev))
            await audit.record(session, None, "system.bootstrap_admin", details={"email": s.admin_email},
                               channel="system")
            await session.commit()
            log.info("Created initial admin %s", s.admin_email)
        await _recover_orphaned_jobs(session)
        await llm_settings.load(session)
        await session.commit()
        n = await session.scalar(select(func.count()).select_from(UseCase))
        if s.seed_demo_data and s.is_dev and (n or 0) == 0:
            from .seed import seed_demo

            await seed_demo(session)
    async with mcp_server.session_manager.run():
        yield


settings = get_settings()
app = FastAPI(title="UseCase Hub", version="0.2.0", lifespan=lifespan,
              description="AI use case intake, normalization, registry and portfolio intelligence.",
              docs_url="/docs" if settings.is_dev else None, redoc_url=None,
              openapi_url="/openapi.json" if settings.is_dev else None)

mcp_server = build_server()
app.router.routes.append(Route("/mcp", endpoint=build_asgi_app(mcp_server), methods=["GET", "POST", "DELETE"]))

app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Correlation id + client IP for the audit trail; security headers on every response."""
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:32]
    audit.set_request_context(request_id=rid, client_ip=client_ip(request),
                              channel="mcp" if request.url.path == "/mcp" else "api")
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """No stack traces or provider messages reach clients (F-23); the request id links to server logs."""
    rid = audit.request_context.get().get("request_id", "")
    log.exception("Unhandled error on %s %s (request_id=%s)", request.method, request.url.path, rid)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": rid})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": [{"loc": e.get("loc"), "msg": e.get("msg")}
                                                             for e in exc.errors()]})


for r in (auth.router, auth.users_router, templates.router, usecases.router, intake.router,
          portfolio.router, imports.router, audit_api.router, settings_api.router, demo_api.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
async def health():
    """Liveness + DB readiness. No configuration details are exposed."""
    try:
        async with SessionLocal() as session:
            await session.execute(select(1))
        db = "ok"
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unreachable"})
    return {"status": "ok", "db": db}
