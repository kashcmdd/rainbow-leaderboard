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
        team2 = detail2.json()
        assert team2["matches_played"] == 2
        assert len(team2["history"]) == 2
        assert len(team2["form"]) == 2
        assert team2["rd"] < settings.starting_rd
        assert team2["movement_7d"] == team2["history"][-1]["elo"] - 500

        board2 = (await client.get("/api/leaderboard/1v1/team")).json()
        rated = next(e for e in board2 if e["team_id"] == team_id)
        assert rated["movement_7d"] == team2["movement_7d"]
        assert rated["form"] == team2["form"]


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


class TestTeamDeltas:
    async def test_individual_team_deltas_and_zero_sum(self, client, admin_headers):
        a1 = await _create_player(client, admin_headers, "tmt-d-a1")
        a2 = await _create_player(client, admin_headers, "tmt-d-a2")
        b1 = await _create_player(client, admin_headers, "tmt-d-b1")
        b2 = await _create_player(client, admin_headers, "tmt-d-b2")

        async with async_session() as db:
            for player, elo in ((a1, 1200), (a2, 2200), (b1, 1200), (b2, 2200)):
                db.add(Rating(
                    player_id=uuid.UUID(player["id"]), format="2v2", elo=elo,
                    wins=10, losses=10, matches_played=20,
                ))
            await db.commit()

        report = await client.post(
            "/api/matches",
            json={
                "format": "2v2",
                "team_a_player_names": [a1["username"], a2["username"]],
                "team_b_player_names": [b1["username"], b2["username"]],
                "score_a": 3,
                "score_b": 1,
            },
            headers=admin_headers,
        )
        assert report.status_code == 200
        match_id = uuid.UUID(report.json()["match_id"])

        async with async_session() as db:
            rows = (await db.execute(
                select(RatingHistory).where(
                    RatingHistory.match_id == match_id,
                    RatingHistory.player_id.isnot(None),
                )
            )).scalars().all()
            deltas = {str(r.player_id): r.elo_after - r.elo_before for r in rows}

        # The lowest-rated winner should gain more than the highest-rated one.
        assert deltas[a1["id"]] > deltas[a2["id"]] > 0
        assert deltas[b1["id"]] < 0 and deltas[b2["id"]] < 0

        detail = (await client.get(f"/api/matches/{match_id}")).json()
        expected_average = round((deltas[a1["id"]] + deltas[a2["id"]]) / 2)
        assert detail["elo_delta_a"] == expected_average


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


class TestBracketProgression:
    async def test_semis_fill_the_final_and_winner_is_listed(self, client, admin_headers, admin_allowlist):
        ids = []
        for name in ("bracket-a", "bracket-b", "bracket-c", "bracket-d"):
            p = await _create_player(client, admin_headers, name)
            ids.append(p["id"])

        create = await client.post(
            "/api/tournaments",
            json={"name": "Progression Cup", "format": "1v1"},
            headers=admin_headers,
        )
        tid = create.json()["id"]
        await client.post(
            f"/api/tournaments/{tid}/add-players",
            json={"player_ids": ids},
            headers=admin_headers,
        )
        start = await client.post(f"/api/tournaments/{tid}/start", headers=admin_headers)
        assert start.status_code == 200

        detail = await client.get(f"/api/tournaments/{tid}")
        semis = [m for m in detail.json()["matches"] if m["round"] == 1]
        assert len(semis) == 2
        winners = []
        for match in semis:
            winners.append(match["team_a_name"])
            report = await client.post(
                f"/api/tournaments/{tid}/report-match",
                json={"match_id": match["id"], "score_a": 3, "score_b": 1},
                headers=admin_headers,
            )
            assert report.status_code == 200

        # The final must show the two semi-final winners, not TBD.
        detail = await client.get(f"/api/tournaments/{tid}")
        final = next(m for m in detail.json()["matches"] if m["round"] == 2)
        assert final["team_a_name"] != "TBD"
        assert final["team_b_name"] != "TBD"
        assert {final["team_a_name"], final["team_b_name"]} == set(winners)

        report = await client.post(
            f"/api/tournaments/{tid}/report-match",
            json={"match_id": final["id"], "score_a": 3, "score_b": 1},
            headers=admin_headers,
        )
        assert report.status_code == 200

        listed = await client.get("/api/tournaments")
        row = next(t for t in listed.json() if t["id"] == tid)
        assert row["status"] == "completed"
        assert row["winner"] == final["team_a_name"]


class TestRatingsPage:
    async def test_explains_the_system(self, anon_client):
        res = await anon_client.get("/ratings")
        assert res.status_code == 200
        assert "How ratings work" in res.text
        assert "Provisional" in res.text


class TestHeadToHead:
    async def test_empty_history_returns_an_empty_list(self, client, admin_headers):
        player = await _create_player(client, admin_headers, "h2h-empty")
        res = await client.get(f"/api/players/{player['id']}/h2h")
        assert res.status_code == 200
        assert res.json() == {"opponents": []}




def _session_client(id_, username, is_admin=False):
    import json as _json

    from httpx import ASGITransport, AsyncClient
    from itsdangerous import TimestampSigner
    from starlette.middleware.sessions import b64encode

    from main import app

    session = {"user": {"id": id_, "username": username, "avatar": None, "is_admin": is_admin}}
    signer = TimestampSigner(str(settings.secret_key))
    data = b64encode(_json.dumps(session, separators=(",", ":")).encode("utf-8"))
    cookie = signer.sign(data).decode("utf-8")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.cookies.set("session", cookie)
    return client


async def _csrf(client):
    token = (await client.get("/api/csrf/token")).json()["csrf_token"]
    return {"X-CSRF-Token": token, "Content-Type": "application/json"}


class TestMatchConfirmation:
    async def test_player_report_waits_for_opponent(self, client, admin_headers, admin_allowlist):
        a = await _create_player(client, admin_headers, "confirm-a")
        b = await _create_player(client, admin_headers, "confirm-b")
        await client.patch(f"/api/players/{a['id']}", json={"discord_id": "confirm-reporter"}, headers=admin_headers)
        await client.patch(f"/api/players/{b['id']}", json={"discord_id": "confirm-opponent"}, headers=admin_headers)

        reporter = _session_client("confirm-reporter", "Reporter")
        opponent = _session_client("confirm-opponent", "Opponent")
        try:
            headers = await _csrf(reporter)
            report = await reporter.post(
                "/api/matches",
                json={
                    "format": "1v1",
                    "team_a_player_names": ["confirm-a"],
                    "team_b_player_names": ["confirm-b"],
                    "score_a": 3,
                    "score_b": 1,
                },
                headers=headers,
            )
            assert report.status_code == 200
            data = report.json()
            assert data["status"] == "pending"
            match_id = data["match_id"]

            detail = await reporter.get(f"/api/matches/{match_id}")
            assert detail.json()["status"] == "pending"
            assert detail.json()["can_confirm"] is False
            assert detail.json()["elo_delta_a"] is None

            # No rating moved yet.
            before = await client.get(f"/api/players/{a['id']}")
            assert before.json()["ratings"][0]["elo"] == 500

            opponent_headers = await _csrf(opponent)
            confirm = await opponent.post(f"/api/matches/{match_id}/confirm", headers=opponent_headers)
            assert confirm.status_code == 200
            assert confirm.json()["status"] == "confirmed"

            after = await client.get(f"/api/players/{a['id']}")
            assert after.json()["ratings"][0]["elo"] > 500
            detail = await client.get(f"/api/matches/{match_id}")
            assert detail.json()["status"] == "confirmed"
            assert detail.json()["confirmed_by"] == "Opponent"
        finally:
            await reporter.aclose()
            await opponent.aclose()

    async def test_dispute_stops_the_result(self, client, admin_headers, admin_allowlist):
        a = await _create_player(client, admin_headers, "dispute-a")
        b = await _create_player(client, admin_headers, "dispute-b")
        await client.patch(f"/api/players/{a['id']}", json={"discord_id": "dispute-reporter"}, headers=admin_headers)
        await client.patch(f"/api/players/{b['id']}", json={"discord_id": "dispute-opponent"}, headers=admin_headers)

        reporter = _session_client("dispute-reporter", "Reporter")
        opponent = _session_client("dispute-opponent", "Opponent")
        try:
            report = await reporter.post(
                "/api/matches",
                json={"format": "1v1", "team_a_player_names": ["dispute-a"], "team_b_player_names": ["dispute-b"], "score_a": 3, "score_b": 1},
                headers=await _csrf(reporter),
            )
            match_id = report.json()["match_id"]
            dispute = await opponent.post(
                f"/api/matches/{match_id}/dispute",
                json={"reason": "Wrong score"},
                headers=await _csrf(opponent),
            )
            assert dispute.status_code == 200
            detail = await client.get(f"/api/matches/{match_id}")
            assert detail.json()["status"] == "disputed"
            assert detail.json()["dispute_reason"] == "Wrong score"
        finally:
            await reporter.aclose()
            await opponent.aclose()


class TestMatchMetadata:
    async def test_r6_fields_round_trip(self, client, admin_headers):
        await _create_player(client, admin_headers, "meta-a")
        await _create_player(client, admin_headers, "meta-b")
        report = await client.post(
            "/api/matches",
            json={
                "format": "1v1",
                "team_a_player_names": ["meta-a"],
                "team_b_player_names": ["meta-b"],
                "score_a": 3,
                "score_b": 2,
                "map_name": "Oregon",
                "side_a": "attack",
                "operator_bans": ["Thatcher", "Mira"],
                "evidence_url": "https://example.com/clip",
            },
            headers=admin_headers,
        )
        assert report.status_code == 200
        detail = await client.get(f"/api/matches/{report.json()['match_id']}")
        body = detail.json()
        assert body["map_name"] == "Oregon"
        assert body["side_a"] == "attack"
        assert body["operator_bans"] == ["Thatcher", "Mira"]
        assert body["evidence_url"] == "https://example.com/clip"


class TestManagedTeams:
    async def test_create_manage_and_reuse(self, client, admin_headers):
        a = await _create_player(client, admin_headers, "team-a1")
        b = await _create_player(client, admin_headers, "team-a2")
        await _create_player(client, admin_headers, "team-b1")
        await _create_player(client, admin_headers, "team-b2")

        created = await client.post(
            "/api/teams",
            json={"name": "Managed Duo", "format": "2v2", "player_ids": [a["id"], b["id"]]},
            headers=admin_headers,
        )
        assert created.status_code == 200
        team_id = created.json()["id"]

        listed = await client.get("/api/teams")
        row = next(t for t in listed.json() if t["id"] == team_id)
        assert row["name"] == "Managed Duo"
        assert row["managed"] is True
        assert {m["username"] for m in row["members"]} == {"team-a1", "team-a2"}

        detail = await client.get(f"/api/teams/{team_id}")
        assert detail.json()["can_manage"] is True
        assert detail.json()["captain"] in {"team-a1", "team-a2"}

        patch = await client.patch(
            f"/api/teams/{team_id}",
            json={"name": "Managed Duo Prime"},
            headers=admin_headers,
        )
        assert patch.status_code == 200

        # Reporting the same roster reuses the managed team, not a new shell.
        report = await client.post(
            "/api/matches",
            json={
                "format": "2v2",
                "team_a_player_names": ["team-a1", "team-a2"],
                "team_b_player_names": ["team-b1", "team-b2"],
                "score_a": 3,
                "score_b": 1,
            },
            headers=admin_headers,
        )
        assert report.status_code == 200
        match = await client.get(f"/api/matches/{report.json()['match_id']}")
        assert match.json()["team_a_name"] == "Managed Duo Prime"

        deactivate = await client.delete(f"/api/teams/{team_id}", headers=admin_headers)
        assert deactivate.status_code == 200
        listed = await client.get("/api/teams")
        assert team_id not in {t["id"] for t in listed.json()}




class TestIntegrity:
    async def test_flags_repeat_pairings_and_lopsided_records(self, client, admin_headers, admin_allowlist):
        await _create_player(client, admin_headers, "integrity-a")
        await _create_player(client, admin_headers, "integrity-b")
        for score_a, score_b in ((3, 1), (3, 2), (3, 0), (2, 0), (4, 1)):
            res = await _report(client, admin_headers, "integrity-a", "integrity-b", score_a, score_b)
            assert res.status_code == 200

        data = (await client.get("/api/admin/integrity")).json()
        assert any(row["count"] >= 5 for row in data["repeat_pairings"])
        assert any(row["matches"] >= 5 for row in data["lopsided"])


class TestSeasonAutoclose:
    async def test_close_due_season_resets_and_snapshots(self, client, admin_headers):
        from app.season_service import close_season

        player = await _create_player(client, admin_headers, "season-auto")
        async with async_session() as db:
            db.add(Season(name="Due Season", status="active", end_date=datetime.now(timezone.utc) - timedelta(days=1)))
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == uuid.UUID(player["id"]), Rating.format == "1v1")
            )).scalar_one()
            rating.elo = 1200
            rating.wins = 10
            rating.losses = 2
            await db.commit()
            result = await close_season(db, "Due Season", "Scheduler", "system")
            assert result["status"] == "closed"

        async with async_session() as db:
            archived = (await db.execute(select(Season).where(Season.name == "Due Season"))).scalar_one()
            assert archived.status == "archived"
            next_season = (await db.execute(select(Season).where(Season.status == "active"))).scalar_one()
            assert next_season.end_date is not None
            snapshots = (await db.execute(
                select(SeasonSnapshot).where(SeasonSnapshot.season_id == archived.id)
            )).scalars().all()
            assert len(snapshots) == 1
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == uuid.UUID(player["id"]), Rating.format == "1v1")
            )).scalar_one()
            assert rating.elo == 500
            assert rating.wins == 0


class TestShareCard:
    async def test_match_card_is_a_png(self, client, admin_headers):
        await _create_player(client, admin_headers, "card-a")
        await _create_player(client, admin_headers, "card-b")
        report = await _report(client, admin_headers, "card-a", "card-b")
        res = await client.get(f"/og/match/{report.json()['match_id']}.png")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        assert res.content.startswith(b"\x89PNG")


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


class TestScoreValidation:
    async def test_scores_above_the_cap_are_rejected(self, client, admin_headers):
        await _create_player(client, admin_headers, "cap-a")
        await _create_player(client, admin_headers, "cap-b")
        payload = {
            "format": "1v1",
            "team_a_player_names": ["cap-a"],
            "team_b_player_names": ["cap-b"],
            "score_a": 21,
            "score_b": 0,
        }
        report = await client.post("/api/matches", json=payload, headers=admin_headers)
        assert report.status_code == 400
        preview = await client.post("/api/matches/preview", json=payload, headers=admin_headers)
        assert preview.status_code == 400


class TestStartingRating:
    async def test_new_player_starts_at_configured_elo(self, client, admin_headers):
        player = await _create_player(client, admin_headers, "fresh-player")
        detail = (await client.get(f"/api/players/{player['id']}")).json()
        rating = next(r for r in detail["ratings"] if r["format"] == "1v1")
        assert rating["elo"] == settings.starting_elo
        assert rating["rd"] == settings.starting_rd
        assert rating["provisional"] is True

        board = (await client.get("/api/leaderboard/1v1")).json()
        entry = next(e for e in board if e["name"] == "fresh-player")
        assert entry["provisional"] is True
        assert entry["rd"] == settings.starting_rd

        async with async_session() as db:
            stored = (await db.execute(
                select(Rating).where(
                    Rating.player_id == uuid.UUID(player["id"]),
                    Rating.format == "1v1",
                )
            )).scalar_one()
            assert stored.rd == settings.starting_rd
            assert stored.volatility == settings.starting_volatility


class TestGlickoIntegration:
    async def test_match_moves_rating_and_shrinks_rd(self, client, admin_headers):
        a = await _create_player(client, admin_headers, "glicko-a")
        b = await _create_player(client, admin_headers, "glicko-b")
        report = await _report(client, admin_headers, "glicko-a", "glicko-b", 3, 1)
        assert report.status_code == 200

        async with async_session() as db:
            rows = (await db.execute(
                select(Rating).where(
                    Rating.player_id.in_([uuid.UUID(a["id"]), uuid.UUID(b["id"])]),
                    Rating.format == "1v1",
                )
            )).scalars().all()
            by_name = {str(r.player_id): r for r in rows}

        winner = by_name[a["id"]]
        loser = by_name[b["id"]]
        assert winner.elo > settings.starting_elo
        assert loser.elo < settings.starting_elo
        assert winner.rd < settings.starting_rd
        assert loser.rd < settings.starting_rd


class TestRdInflation:
    async def test_inactivity_inflates_rd_not_rating(self, client, admin_headers):
        player = await _create_player(client, admin_headers, "rd-player")
        pid = uuid.UUID(player["id"])

        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            rating.elo = 1800
            rating.rd = 80
            rating.volatility = 0.06
            rating.last_active = datetime.now(timezone.utc) - timedelta(days=40)
            rating.rd_updated_at = datetime.now(timezone.utc) - timedelta(days=40)
            await db.commit()

        from main import run_rd_inflation
        await run_rd_inflation()

        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            assert rating.elo == 1800, "inactivity must not decay the rating"
            assert rating.rd > 80
            first_rd = rating.rd

        await run_rd_inflation()
        async with async_session() as db:
            rating = (await db.execute(
                select(Rating).where(Rating.player_id == pid, Rating.format == "1v1")
            )).scalar_one()
            assert rating.rd == first_rd, "a second same-day run must not inflate again"


class TestAdminAccess:
    async def test_admin_page_uses_allowlist(self, client, admin_allowlist):
        r = await client.get("/admin")
        assert r.status_code == 200

    async def test_healthz_reports_database(self, client):
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["database"] == "ok"
