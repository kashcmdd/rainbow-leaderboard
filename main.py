from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.csrf import router as csrf_router
from app.database import engine
from app.routers import activity, admin, auth, leaderboard, matches, pages, players, stats, teams, tournaments

scheduler = AsyncIOScheduler()


async def run_elo_decay():
    import logging
    logger = logging.getLogger(__name__)
    try:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.database import async_session
        from app.models import AuditLog, Rating, RatingHistory

        now = datetime.now(timezone.utc)
        async with async_session() as db:
            cutoff = now - timedelta(days=settings.decay_days)
            result = await db.execute(
                select(Rating).where(
                    Rating.last_active.isnot(None),
                    Rating.last_active < cutoff,
                    Rating.player_id.isnot(None),
                )
            )
            ratings = result.scalars().all()
            for r in ratings:
                inactive_days = (now - r.last_active).days
                if inactive_days <= settings.decay_days:
                    continue

                # Total decay owed since the player was last active, capped.
                # decay_applied makes this idempotent across daily runs.
                target_decay = min(
                    (inactive_days - settings.decay_days) * settings.decay_per_day,
                    settings.max_decay,
                )
                applied = r.decay_applied or 0
                increment = target_decay - applied
                if increment <= 0:
                    continue

                before = r.elo
                r.elo = max(before - increment, settings.rating_floor)
                r.decay_applied = target_decay
                actual_decay = before - r.elo
                if actual_decay > 0:
                    r.is_decaying = True
                    db.add(RatingHistory(
                        rating_id=r.id,
                        player_id=r.player_id,
                        team_id=None,
                        elo_before=before,
                        elo_after=r.elo,
                        match_id=None,
                        changed_at=now,
                    ))
                    db.add(AuditLog(
                        admin_id="system",
                        admin_name="System",
                        action="elo_decay",
                        target_id=str(r.player_id) if r.player_id else None,
                        details=f"Decayed {actual_decay} ELO after {inactive_days} days inactive",
                    ))
            await db.commit()

            # Recalculate exclusive top positions for every rated format
            from app.ranks import recalculate_top_positions
            for fmt in settings.format_k_multipliers:
                await recalculate_top_positions(db, fmt)
            await db.commit()
    except Exception as e:
        logger.error("ELO decay job failed: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.schema import ensure_schema
    await ensure_schema()
    scheduler.add_job(run_elo_decay, 'interval', hours=24, id='elo_decay', max_instances=1, misfire_grace_time=3600)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=settings.site_name, lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "app" / "templates")
templates.env.globals["site_name"] = settings.site_name
templates.env.globals["public_base_url"] = settings.public_base_url
templates.env.globals["dev_login_enabled"] = bool(settings.dev_login_discord_id)
templates.env.globals["dev_login_username"] = settings.dev_login_username
# NOTE: Set COOKIE_SECURE=true behind TLS (reverse proxy or Coolify SSL)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, https_only=settings.cookie_secure)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; frame-src 'none'; object-src 'none'"
        return response


app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(request, "error.html", {"code": 404, "message": "Page not found"}, status_code=404)


@app.exception_handler(500)
async def server_error(request: Request, exc):
    return templates.TemplateResponse(request, "error.html", {"code": 500, "message": "Something went wrong"}, status_code=500)


@app.get("/healthz")
async def healthz():
    from fastapi.responses import JSONResponse
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Health check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return JSONResponse(content={"status": "ok", "database": "ok"}, headers={"Cache-Control": "no-store"})

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
app.include_router(teams.router)
app.include_router(activity.router)
app.include_router(stats.router)
app.include_router(csrf_router)

# Make csrf_input available in all template environments

