from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def _sync_additive_columns(conn) -> list[str]:
    """Add columns that exist in the models but not in the database (additive-only, nullable or with a
    server default). Covers the gap until Alembic migrations are introduced; never drops or alters."""
    from sqlalchemy import inspect

    inspector = inspect(conn)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue  # created by create_all above
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(conn.dialect)}'
            default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            if default is not None and not col.nullable:
                literal = "true" if default is True else "false" if default is False else repr(default)
                ddl += f" NOT NULL DEFAULT {literal}"
            conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    return added


async def init_db() -> None:
    import logging

    from . import models  # noqa: F401  (register tables)

    async with engine.begin() as conn:
        if is_postgres():
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        added = await conn.run_sync(_sync_additive_columns)
        if added:
            logging.getLogger("usecasehub").warning("Schema sync added columns: %s", ", ".join(added))


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
