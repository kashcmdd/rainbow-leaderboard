# Vantage

A self-hosted competitive ladder for Rainbow Six communities.

Glicko-2 ratings, seasons, tournaments, confirmed match reporting, Rainbow Six match metadata, managed teams, and a full admin panel — server-rendered with FastAPI and PostgreSQL.

![Vantage leaderboard](assets/screenshot.png)

[![CI](https://github.com/kashcmdd/rainbow-leaderboard/actions/workflows/ci.yml/badge.svg)](https://github.com/kashcmdd/rainbow-leaderboard/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Status: active and deployed privately on a tailnet. There is no public demo; run it locally or ask for a walkthrough.

## What it does

**Ratings**
- Glicko-2 under the hood, shown as ELO. New players start at 500 with RD 350 and a provisional badge.
- Win/loss only: score margin does not change the swing.
- Per-player updates inside team formats, 1v1 through 5v5.
- Inactivity inflates RD instead of decaying the rating, so returning players move more.
- Top positions, streaks, last-five form, weekly movement and progress to the next rank.

**Match integrity**
- Player reports are `pending` until the opponent confirms; admins can confirm directly.
- Disputes keep the result off the ladder until an admin resolves it.
- Evidence link, duplicate guard, rate limiting and an audit log.
- Optional map, side (attack/defense) and operator bans.

**Competition**
- Seasons with archived standings snapshots and champions. Closing a season resets 1v1 only.
- Tournaments with seeded brackets, automatic winner advancement and a champion on the index.
- Managed teams with a captain, roster editor and reuse of a named lineup across matches.
- Head-to-head records on every profile and a two-player compare page.

**Experience**
- #1 spotlight, rank art for every tier, dark and light themes, responsive from 390px up.
- Generated 1200×630 match share cards at `/og/match/{id}.png`.
- CSV/JSON leaderboard export and an optional Discord webhook.

**Admin**
- Player management, ELO adjustments with reasons, match editing, notes, bulk CSV import.
- Season and tournament control, audit log with filters.
- Integrity checks for repeated pairings, one-sided records and reporter bursts.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async |
| Database | PostgreSQL 16, Alembic migrations |
| Views | Jinja2, vanilla JS, Tailwind 3 (committed build) |
| Ratings | Glicko-2 (`app/glicko2.py`) |
| Jobs | APScheduler (RD inflation, season auto-close) |
| Runtime | Docker + Docker Compose |

## Run it

Requirements: Docker and Docker Compose.

```bash
git clone https://github.com/kashcmdd/rainbow-leaderboard.git
cd rainbow-leaderboard
cp .env.example .env
# set SECRET_KEY and POSTGRES_PASSWORD in .env
docker compose up -d --build
```

Open `http://localhost:8090`. Migrations run automatically at startup.

### Configuration

`.env.example` documents every setting. The ones that matter first:

```env
SECRET_KEY=change-me
POSTGRES_PASSWORD=change-me
SITE_NAME=Vantage
COOKIE_SECURE=false            # true behind HTTPS
PUBLIC_BASE_URL=http://localhost:8090
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=http://localhost:8090/auth/callback
DISCORD_WEBHOOK_URL=           # optional match announcements
REQUIRE_MATCH_CONFIRMATION=true
AUTO_CLOSE_SEASONS=false
```

To make yourself an admin, either promote the player from the admin panel or set `ADMIN_DISCORD_IDS` to your Discord user ID.

### Local sign-in without Discord

For a local or tailnet test, set `DEV_LOGIN_DISCORD_ID` and open `/auth/dev`. Set `DEV_LOGIN_ADMIN=true` for admin rights. Unset both in production — while they are set, anyone who can reach the site can use that login.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q            # 50 tests
.venv/bin/ruff check .
npm install
npm run tw:build               # after template or input.css changes
```

CI runs lint, the CSS drift check, `alembic upgrade head` and the test suite on every push.

### Project layout

```
app/
├── routers/          # pages, matches, players, teams, tournaments, admin, OG
├── templates/        # Jinja2 pages
├── static/           # compiled CSS, rank icons, avatars
├── glicko2.py        # rating maths
├── season_service.py # snapshot + reset
├── models.py         # SQLAlchemy models
├── schema.py         # Alembic bootstrap/adoption
└── config.py         # settings
alembic/              # migrations
tests/                # pytest suite
main.py               # FastAPI entrypoint
```

`PRODUCT.md` holds the product context and `DESIGN.md` the design system (tokens, type, components, motion).

## API

Interactive docs are at `/docs` (Swagger) and `/redoc`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/leaderboard/{format}` | Standings (`?season=` for archives) |
| GET | `/api/matches` | Confirmed match history |
| GET | `/api/matches/{id}` | Match detail, status and metadata |
| POST | `/api/matches/preview` | ELO preview without writing |
| POST | `/api/matches` | Report a result |
| POST | `/api/matches/{id}/confirm` | Confirm a pending result |
| POST | `/api/matches/{id}/dispute` | Dispute a result |
| GET | `/api/players/{id}` | Profile and ratings |
| GET | `/api/players/{id}/h2h` | Head-to-head record |
| GET | `/api/teams` | Managed teams |
| POST | `/api/teams` | Create a team |
| PATCH | `/api/teams/{id}` | Edit name, roster or captain |
| GET | `/api/admin/integrity` | Repeated pairings, one-sided records, reporter bursts |
| GET | `/og/match/{id}.png` | Share card |
| GET | `/healthz` | Liveness + database check |

## License

MIT. See [LICENSE](LICENSE).
