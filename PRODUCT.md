# PRODUCT.md

## What this is

Vantage is a community leaderboard and match tracker for Rainbow Six, with ELO
ratings across 1v1 through 5v5. Players report results, admins curate the
roster, seasons and tournaments, and everyone can read the ladder.

Public surfaces:

- **Home** — ladder summary and recent activity.
- **Leaderboard** — standings by format, with player and team views.
- **Player** — profile with last-five form, weekly ELO movement, rating
  deviation/provisional state, progress to the next tier, ELO trend, win rate,
  season history, match history.
- **Team** — roster, team rating and recent results for a repeated lineup.
- **Match** — shareable single-match report with both sides and ELO deltas.
- **Compare** — two-player head-to-head.
- **Seasons** — archived 1v1 standings snapshots (the season ladder is 1v1;
  other formats keep their ratings across seasons).
- **Tournaments** — brackets, participants, placements.
- **Login / 404 / 500** — minimal supporting screens.

Authenticated surfaces:

- **Report match** — preview the ELO swing, guard against double submits, then
  submit a result. Reporting is rate-limited per user.
- **Profile** — redirects to the signed-in player's page.
- **Admin** — player management, ratings, seasons, tournaments, audit log.

## Who uses it

- **Players** reporting results after a match, checking their own rating and
  history.
- **Admins** fixing scores, adjusting ELO, managing players, closing seasons,
  running brackets.
- **Spectators** reading the ladder and head-to-head records.

The dominant use is reading dense numbers quickly, often on a phone after a
match, and trusting that the standings are current. Mobile navigation uses a
five-item bottom bar (Ladder, Events, Seasons, Compare, More) rather than a
hamburger.

## Tone

A match dossier, not an arcade cabinet. Restrained, precise, competitive.
Data reads first; identity comes from structure, typography and one signal
colour rather than decoration.

## Constraints

- FastAPI + Jinja server-rendered pages; vanilla JS inline per page.
- Existing API contracts, routes, element IDs and copy stay intact.
- Tailwind 3 with a committed build (`app/static/css/tailwind.min.css`);
  `npm run tw:build` regenerates it.
- Dark and light themes, both designed; preference persists.
- The site name is configuration (`SITE_NAME`, default `Vantage`) because a
  rename is planned.
- Match results can be announced to an optional Discord webhook.
- Admins are verified against the database/allowlist on page routes as well as
  API routes.

## Non-goals

- Replacing Jinja with an SPA or adding a framework.
- Rewriting admin workflows.
- Changing rating maths, season semantics, or API shapes.
