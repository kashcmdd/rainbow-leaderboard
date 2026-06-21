# Rainbow Leaderboard 🏆

> **Your community's ranked ladder — up and running in 2 commands.**

![Screenshot](assets/screenshot.png)

A competitive ranking and match-tracking system for **Rainbow Six Siege** built for communities that want to run their own ranked ladder. Features an ELO system with provisional ratings, seasons, tournaments, Discord authentication, and a full admin panel — all wrapped in a clean glassmorphism UI with dark mode.

## ✨ What It Can Do

- **ELO Rating System** — Glicko-style with provisional ratings, margin-of-victory weighting, and multi-format support (1v1 through 5v5). Decay keeps inactive players from camping the top.
- **Player Profiles** — Each player gets a dashboard with ELO history chart, win/loss stats, rank icon, and a feed of their recent matches.
- **Match Reporting** — Report results and let the algorithm handle the math. Configurable score formats for any game mode.
- **Player Comparison** — Put any two players side by side with head-to-head stats and time-filtered match history.
- **Tournaments** — Brackets with visual connector lines, champion highlights, and ELO integration.
- **Seasons** — Time-based resets with archived leaderboards you can browse anytime.
- **Admin Panel** — Manage players, edit matches, set ELO values, bulk import from CSV, add notes, and control seasons. All with an audit log.
- **Discord Auth** — Log in with Discord. Granular admin roles.
- **Rank Icons** — 41 custom SVG icons from Bronze through Champion, each tier with its own distinct shape (shields, crystals, crowns, etc.).
- **Dark Mode** — Moon/sun toggle, saved to your browser. Looks great either way.
- **Download Data** — Export the leaderboard as CSV or JSON with one click.

## 🧱 Tech Stack

| Layer | Tech |
|---|---|
| **Backend** | Python 3.12 + FastAPI |
| **Database** | PostgreSQL 16 via asyncpg + SQLAlchemy (async) |
| **Frontend** | Server-rendered Jinja2 + vanilla JS + Tailwind |
| **Auth** | Discord OAuth2 |
| **Scheduling** | APScheduler for ELO decay |
| **Container** | Docker + Docker Compose |
| **Migrations** | Alembic |

## 🚀 Getting Started

### You'll Need

- Docker & Docker Compose

### Let's Go

```bash
git clone https://github.com/Martty12212/rainbow-leaderboard.git
cd rainbow-leaderboard
docker compose up -d --build
```

Open `http://localhost:8000` and you're live.

### Configuration

Copy `.env.example` to `.env` and fill in your details:

```env
DATABASE_URL=postgresql+asyncpg://rainbow:rainbow@db:5432/rainbow
SECRET_KEY=your-secret-key-here
DISCORD_CLIENT_ID=your-discord-client-id
DISCORD_CLIENT_SECRET=your-discord-client-secret
DISCORD_REDIRECT_URI=http://your-domain:8000/auth/callback
```

To make yourself an admin, add your Discord user ID to `admin_discord_ids` in `app/config.py`.

## 📖 API Routes

| Method | Path | Description |
|---|---|---|
| GET | `/leaderboard` | View the leaderboard |
| GET | `/players/{id}` | Player profile and stats |
| GET | `/matches` | Match history |
| GET | `/tournaments` | Tournament listings |
| POST | `/api/matches` | Report a match result (admin) |
| POST | `/api/players` | Create a new player (admin) |

Full API docs at `/docs` (Swagger) or `/redoc`.

## 📁 Project Layout

```
rainbow-leaderboard/
├── app/
│   ├── routers/       # API route handlers
│   ├── templates/     # Jinja2 HTML templates
│   ├── static/        # CSS, icons, avatars
│   ├── config.py      # App configuration
│   ├── database.py    # DB connection & session
│   ├── models.py      # SQLAlchemy models
│   ├── schemas.py     # Pydantic schemas
│   ├── elo.py         # ELO rating logic
│   └── ranks.py       # Rank definitions
├── main.py            # FastAPI app entrypoint
├── Dockerfile         # API container build
├── docker-compose.yml # Service orchestration
└── requirements.txt   # Python dependencies
```

## 📄 License

Built for fun, so go ahead — use it, tweak it, break it, make it yours.
