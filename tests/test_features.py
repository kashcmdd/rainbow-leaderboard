import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models import Rating, RatingHistory, Season, SeasonSnapshot

ADMIN_DISCORD_ID = "1415420243836407878"


@pytest.fixture
def admin_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "admin_discord_ids", [ADMIN_DISCORD_ID])


async def _create_player(client, headers, name):
    r = await client.post("/api/players", json={"username": name}, headers=headers)
    assert r.status_code == 200
    return r.json()


async def _report(client, headers, a, b, score_a=3, score_b=1):
    return await client.post(
        "/api/matches",
        json={
            "format": "1v1",
            "team_a_player_names": [a],
            "team_b_player_names": [b],
            "score_a": score_a,
            "score_b": score_b,
        },
        headers=headers,
    )


class TestTeams:
    async def test_team_leaderboard_and_detail(self, client, admin_headers):
        await _create_player(client, admin_headers, "team-alpha")
        await _create_player(client, admin_headers, "team-beta")
        report = await _report(client, admin_headers, "team-alpha", "team-beta")
        assert report.status_code == 200

        board = await client.get("/api/leaderboard/1v1/team")
        assert board.status_code == 200
        entries = board.json()
        assert entries, "a reported match should create a team rating"
        team_id = entries[0]["team_id"]
        assert team_id

        detail = await client.get(f"/api/teams/{team_id}")
        assert detail.status_code == 200
        team = detail.json()
        assert team["format"] == "1v1"
        assert team["matches_played"] == 1
        assert len(team["members"]) == 1
        assert team["members"][0]["username"] in ("team-alpha", "team-beta")
        assert len(team["recent_matches"]) == 1
        assert team["recent_matches"][0]["opponent"] in ("team-alpha", "team-beta")

        # Reusing the same roster must accumulate, not create a second team.
        await client.post(
            "/api/matches",
            json={
                "format": "1v1",
                "team_a_player_names": ["team-alpha"],
                "team_b_player_names": ["team-beta"],
                "score_a": 2,
                "score_b": 4,
            },
            headers=admin_headers,
        )
        detail2 = await client.get(f"/api/teams/{team_id}")
        assert detail2.json()["matches_played"] == 2


class TestMatchDetail:
    async def test_detail_preview_and_duplicate_guard(self, client, admin_headers):
        await _create_player(client, admin_headers, "detail-a")
        await _create_player(client, admin_headers, "detail-b")
        first = await _report(client, admin_headers, "detail-a", "detail-b", 5, 2)
        assert first.status_code == 200
        match_id = first.json()["match_id"]

        detail = await client.get(f"/api/matches/{match_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["team_a_players"] and data["team_b_players"]
        assert data["winner_name"] == data["team_a_name"]

        # Preview must not write a match.
        before = len((await client.get("/api/matches?limit=100")).json())
        preview = await client.post(
            "/api/matches/preview",
            json={
                "format": "1v1",
                "team_a_player_names": ["detail-b"],
                "team_b_player_names": ["detail-a"],
                "score_a": 3,
                "score_b": 1,
            },
            headers=admin_headers,
        )
        assert preview.status_code == 200
        assert preview.json()["duplicate"] is False
        assert "elo_deltas" in preview.json()
        after = len((await client.get("/api/matches?limit=100")).json())
        assert before == after

        # The exact same result twice is rejected.
        duplicate = await _report(client, admin_headers, "detail-a", "detail-b", 5, 2)
        assert duplicate.status_code == 409


class TestLeaderboardSignals:
    async def test_form_and_weekly_movement(self, client, admin_headers):
        await _create_player(client, admin_headers, "signal-a")
        b = await _create_player(client, admin_headers, "signal-b")

        # Give the loser a rating above the floor so losses are recorded.
        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == uuid.UUID(b["id"]), Rating.format == "1v1")
            )).scalar_one()
            rating.elo = 1200
            await db.commit()

        assert (await _report(client, admin_headers, "signal-a", "signal-b", 3, 1)).status_code == 200
        assert (await _report(client, admin_headers, "signal-a", "signal-b", 4, 2)).status_code == 200

        board = await client.get("/api/leaderboard/1v1")
        assert board.status_code == 200
        entry = next(e for e in board.json() if e["name"] == "signal-a")
        assert entry["form"][0] == "W"
        assert 1 <= len(entry["form"]) <= 5
        assert entry["movement_7d"] > 0

        loser = next(e for e in board.json() if e["name"] == "signal-b")
        assert loser["form"][0] == "L"
        assert loser["movement_7d"] < 0


class TestPlayerHistory:
    async def test_history_reports_opponent_and_result(self, client, admin_headers):
        a = await _create_player(client, admin_headers, "history-a")
        await _create_player(client, admin_headers, "history-b")
        report = await _report(client, admin_headers, "history-a", "history-b")
        assert report.status_code == 200

        history = await client.get(f"/api/players/{a['id']}/history")
        assert history.status_code == 200
        rows = history.json()
        assert len(rows) == 1
        assert rows[0]["opponent"] == "history-b"
        assert rows[0]["result"] == "win"


class TestSeasonHistory:
    async def test_snapshots_are_ordered_newest_first(self, client, admin_headers):
        player = await _create_player(client, admin_headers, "season-player")
        pid = uuid.UUID(player["id"])
        now = datetime.now(timezone.utc)

        async with async_session() as db:
            older = Season(name="Old Season", start_date=now - timedelta(days=200),
                           end_date=now - timedelta(days=120), status="archived")
            newer = Season(name="New Season", start_date=now - timedelta(days=110),
                           end_date=now - timedelta(days=10), status="archived")
            db.add_all([older, newer])
            await db.flush()
            for season, elo in ((older, 1200), (newer, 1500)):
                db.add(SeasonSnapshot(
                    season_id=season.id, player_id=pid, player_name="season-player",
                    elo=elo, rank_title="Gold III", rank_color="#FFD700",
                    wins=1, losses=1, position=1,
                ))
            await db.commit()

        r = await client.get(f"/api/players/{pid}/seasons")
        assert r.status_code == 200
        names = [row["season_name"] for row in r.json()]
        assert names == ["New Season", "Old Season"]


class TestStats:
    async def test_top_player_ignores_non_1v1_ratings(self, client, admin_headers):
        await _create_player(client, admin_headers, "stats-a")
        b = await _create_player(client, admin_headers, "stats-b")
        report = await _report(client, admin_headers, "stats-a", "stats-b")
        winner_id = report.json()["winner_player_id"]

        # Give the loser a huge 2v2 rating; the headline player is still 1v1.
        async with async_session() as db:
            db.add(Rating(player_id=uuid.UUID(b["id"]), format="2v2", elo=4000,
                          wins=10, losses=0, matches_played=10))
            await db.commit()

        stats = await client.get("/api/stats")
        assert stats.status_code == 200
        assert stats.json()["top_player"]["id"] == winner_id


class TestTournaments:
    async def test_full_tournament_flow(self, client, admin_headers, admin_allowlist):
        ids = []
        for name in ("tmt-a", "tmt-b"):
            p = await _create_player(client, admin_headers, name)
            ids.append(p["id"])

        create = await client.post(
            "/api/tournaments",
            json={"name": "Regression Cup", "format": "1v1"},
            headers=admin_headers,
        )
        assert create.status_code == 200
        tid = create.json()["id"]

        add = await client.post(
            f"/api/tournaments/{tid}/add-players",
            json={"player_ids": ids},
            headers=admin_headers,
        )
        assert add.status_code == 200

        start = await client.post(f"/api/tournaments/{tid}/start", headers=admin_headers)
        assert start.status_code == 200

        # This is the endpoint that used to 500 on every request.
        detail = await client.get(f"/api/tournaments/{tid}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["status"] == "active"
        assert len(data["matches"]) >= 1

        match = data["matches"][0]
        report = await client.post(
            f"/api/tournaments/{tid}/report-match",
            json={"match_id": match["id"], "score_a": 3, "score_b": 1},
            headers=admin_headers,
        )
        assert report.status_code == 200

        complete = await client.post(
            f"/api/tournaments/{tid}/complete", json={}, headers=admin_headers
        )
        assert complete.status_code == 200


class TestDevLogin:
    async def test_disabled_by_default(self, anon_client, monkeypatch):
        monkeypatch.setattr(settings, "dev_login_discord_id", "")
        r = await anon_client.get("/auth/dev", follow_redirects=False)
        assert r.status_code == 404

    async def test_enabled_creates_admin_and_reuses_player(self, anon_client, monkeypatch):
        monkeypatch.setattr(settings, "dev_login_discord_id", "dev-test-id")
        monkeypatch.setattr(settings, "dev_login_admin", True)

        first = await anon_client.get("/auth/dev", follow_redirects=False)
        assert first.status_code == 302
        assert (await anon_client.get("/admin")).status_code == 200

        second = await anon_client.get("/auth/dev", follow_redirects=False)
        assert second.status_code == 302
        players = (await anon_client.get("/api/players")).json()
        assert [p["username"] for p in players] == ["DevAdmin"]
        assert players[0]["is_admin"] is True


class TestDecay:
    async def test_decay_is_idempotent_and_historied(self, client, admin_headers):
        player = await _create_player(client, admin_headers, "decay-player")
        pid = uuid.UUID(player["id"])

        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            rating.elo = 2150
            rating.last_active = datetime.now(timezone.utc) - timedelta(days=40)
            await db.commit()

        from main import run_elo_decay
        await run_elo_decay()

        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            first = rating.elo
            assert first == 2100  # 10 days past the grace period; floor stops at the tier
            assert rating.decay_applied == 100
            history = (await db.execute(
                select(RatingHistory).where(
                    RatingHistory.rating_id == rating.id,
                    RatingHistory.match_id.is_(None),
                )
            )).scalars().all()
            assert len(history) == 1
            assert history[0].elo_before == 2150
            assert history[0].elo_after == 2100

        await run_elo_decay()
        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            assert rating.elo == first, "a second run must not decay again"

        rows = (await client.get(f"/api/players/{pid}/history")).json()
        assert any(row["format"] == "decay" for row in rows)


class TestAdminAccess:
    async def test_admin_page_uses_allowlist(self, client, admin_allowlist):
        r = await client.get("/admin")
        assert r.status_code == 200

    async def test_healthz_reports_database(self, client):
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["database"] == "ok"
