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


async def run_rd_inflation():
    """Inactive players keep their rating but their RD grows."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app import glicko2
        from app.database import async_session
        from app.models import Rating

        now = datetime.now(timezone.utc)
        async with async_session() as db:
            result = await db.execute(
                select(Rating).where(
                    Rating.rd_updated_at.isnot(None),
                    Rating.rd_updated_at < now - timedelta(days=1),
                )
            )
            for rating in result.scalars().all():
                days = (now - rating.rd_updated_at).days
                if days <= 0:
                    continue
                inflated = glicko2.inflate_rd(rating.rd or glicko2.DEFAULT_RD, days)
                if inflated > (rating.rd or 0):
                    rating.rd = inflated
                rating.rd_updated_at = now
            await db.commit()
    except Exception as e:
        logger.error("RD inflation job failed: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.schema import ensure_schema
    await ensure_schema()
    scheduler.add_job(run_rd_inflation, 'interval', hours=24, id='rd_inflation', max_instances=1, misfire_grace_time=3600)
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

