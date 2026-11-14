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
        from app.models import AuditLog, Rating
        from app.ranks import RANKS

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
                for _name, threshold, _ in reversed(RANKS):
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


app = FastAPI(title="Rainbow Leaderboard", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "app" / "templates")
templates.env.globals["site_name"] = settings.site_name
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

