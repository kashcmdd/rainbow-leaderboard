"""Schema bootstrap for Alembic.

Fresh databases are created with the baseline migration. Existing databases that
were built by the old ``Base.metadata.create_all`` path have no alembic_version
row: they are adopted (stamped) only after a compatibility check, and the known
``matches.tournament_id`` type change is applied explicitly. Anything else that
looks stale fails loudly instead of being silently "stamped".
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import inspect, text

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.database import Base, engine

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LOCK_ID = 918273645


async def _inspect_schema(conn):
    def _get(sync_conn):
        inspector = inspect(sync_conn)
        tables = set(inspector.get_table_names())
        missing_columns = {}
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            missing = {c.name for c in table.columns} - existing
            if missing:
                missing_columns[table.name] = sorted(missing)
        return tables, missing_columns

    return await conn.run_sync(_get)


async def _run_alembic(*args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", *args,
        cwd=str(ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(args)} failed:\n{out.decode(errors='replace')}\n{err.decode(errors='replace')}"
        )


async def ensure_schema() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
        try:
            tables, missing_columns = await _inspect_schema(conn)

            if "alembic_version" in tables:
                await _run_alembic("upgrade", "head")
                return

            if not tables:
                logger.info("Empty database: applying Alembic migrations")
                await _run_alembic("upgrade", "head")
                return

            # Adopt a database created before Alembic was wired.
            if missing_columns:
                details = "; ".join(
                    f"{t}: {', '.join(cols)}" for t, cols in missing_columns.items()
                )
                raise RuntimeError(
                    "Existing database schema is behind the models and has no "
                    f"alembic_version row. Missing columns: {details}. "
                    "Back up the database and add them manually (or restore a "
                    "fresh volume) before starting."
                )

            tournament_type = (await conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'matches' AND column_name = 'tournament_id'"
            ))).scalar()
            if tournament_type and tournament_type != "uuid":
                logger.warning("Adopting schema: converting matches.tournament_id to uuid")
                await conn.execute(text(
                    "ALTER TABLE matches ALTER COLUMN tournament_id TYPE uuid "
                    "USING tournament_id::uuid"
                ))
                await conn.commit()

            logger.warning("Adopting existing schema: stamping the Alembic baseline")
            await _run_alembic("stamp", "head")
            await _run_alembic("upgrade", "head")
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
