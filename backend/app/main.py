import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from .api import auth, imports, intake, portfolio, templates, usecases
from .auth import hash_password
from .config import get_settings
from .db import SessionLocal, init_db
from .models import Role, UseCase, User
from .services.templates import sync_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("usecasehub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await init_db()
    async with SessionLocal() as session:
        await sync_templates(session)
        admin = await session.scalar(select(User).where(User.role == Role.admin.value))
        if not admin:
            session.add(User(email=s.admin_email.lower(), full_name="Administrator", role=Role.admin.value,
                             password_hash=hash_password(s.admin_password)))
            await session.commit()
            log.info("Created initial admin %s", s.admin_email)
        n = await session.scalar(select(func.count()).select_from(UseCase))
        if s.seed_demo_data and (n or 0) == 0:
            from .seed import seed_demo

            await seed_demo(session)
    yield


app = FastAPI(title="UseCase Hub", version="0.1.0", lifespan=lifespan,
              description="AI use case intake, normalization, registry and portfolio intelligence.")

app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, auth.users_router, templates.router, usecases.router, intake.router,
          portfolio.router, imports.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"status": "ok", "llm_model": s.llm_model, "embedding_model": s.embedding_model}
