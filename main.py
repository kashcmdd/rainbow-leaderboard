from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sqlalchemy import text

from app.config import settings
from app.database import init_db, engine
from app.routers import players, matches, leaderboard, pages, auth, admin, tournaments, activity

scheduler = AsyncIOScheduler()


async def run_elo_decay():
    from app.database import async_session
    from app.models import Rating, Player, AuditLog
    from app.ranks import RANKS
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select

    async with async_session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.decay_days)
        result = await db.execute(
            select(Rating).where(
                Rating.last_active.isnot(None),
                Rating.last_active < cutoff,
                Rating.player_id.isnot(None),
            )
        )
        ratings = result.scalars().all()
        for r in ratings:
            days_inactive = (datetime.now(timezone.utc) - r.last_active).days
            decay = min(days_inactive * settings.decay_per_day, settings.max_decay)
            min_elo = 0
            for name, threshold, _ in reversed(RANKS):
                if r.elo >= threshold:
                    min_elo = threshold
                    break
            new_elo = max(r.elo - decay, min_elo)
            if new_elo < r.elo:
                actual_decay = r.elo - new_elo
                r.is_decaying = True
                r.elo = new_elo
                db.add(AuditLog(
                    admin_id="system",
                    admin_name="System",
                    action="elo_decay",
                    target_id=str(r.player_id) if r.player_id else None,
                    details=f"Decayed {actual_decay} ELO after {days_inactive} days inactive",
                ))
        await db.commit()

        # Recalculate exclusive top positions after decay
        from app.ranks import recalculate_top_positions
        await recalculate_top_positions(db)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS top_position INTEGER"))
        await conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS streak INTEGER DEFAULT 0"))
        await conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS is_decaying BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS last_active TIMESTAMP WITH TIME ZONE"))
        await conn.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS bracket_round INTEGER"))
        await conn.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS bracket_position INTEGER"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id UUID PRIMARY KEY,
                admin_id VARCHAR(32) NOT NULL,
                admin_name VARCHAR(64) NOT NULL,
                action VARCHAR(256) NOT NULL,
                target_id VARCHAR(64),
                target_name VARCHAR(64),
                details TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id UUID PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                status VARCHAR(16) DEFAULT 'pending',
                format VARCHAR(8) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tournament_participants (
                id UUID PRIMARY KEY,
                tournament_id UUID REFERENCES tournaments(id) ON DELETE CASCADE,
                player_id UUID REFERENCES players(id) ON DELETE CASCADE,
                seed INTEGER,
                placement INTEGER
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS seasons (
                id UUID PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                start_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                end_date TIMESTAMP WITH TIME ZONE,
                status VARCHAR(16) DEFAULT 'active'
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS season_snapshots (
                id UUID PRIMARY KEY,
                season_id UUID REFERENCES seasons(id) ON DELETE CASCADE,
                player_id UUID REFERENCES players(id) ON DELETE CASCADE,
                player_name VARCHAR(64) NOT NULL,
                elo INTEGER NOT NULL,
                rank_title VARCHAR(32) NOT NULL,
                rank_color VARCHAR(16) NOT NULL,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                position INTEGER
            )
        """))
    scheduler.add_job(run_elo_decay, 'interval', hours=24, id='elo_decay')
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Rainbow Leaderboard", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, https_only=False)

static_dir = Path(__file__).parent / "app" / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(players.router)
app.include_router(matches.router)
app.include_router(leaderboard.router)
app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(tournaments.router)
app.include_router(activity.router)
