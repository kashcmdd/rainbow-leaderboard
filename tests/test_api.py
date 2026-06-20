class TestPlayerAPI:
    async def test_list_players(self, client):
        r = await client.get("/api/players")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_player_valid(self, client, admin_headers):
        r = await client.post(
            "/api/players",
            json={"username": "test-player-1"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "test-player-1"
        assert "id" in data

    async def test_create_duplicate_player(self, client, admin_headers):
        await client.post(
            "/api/players",
            json={"username": "dup-player"},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/players",
            json={"username": "dup-player"},
            headers=admin_headers,
        )
        assert r.status_code == 409

    async def test_get_player(self, client, admin_headers):
        create = await client.post(
            "/api/players",
            json={"username": "get-test"},
            headers=admin_headers,
        )
        pid = create.json()["id"]
        r = await client.get(f"/api/players/{pid}")
        assert r.status_code == 200
        assert r.json()["username"] == "get-test"

    async def test_get_nonexistent_player(self, client):
        r = await client.get("/api/players/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    async def test_invalid_uuid(self, client):
        r = await client.get("/api/players/not-a-uuid")
        assert r.status_code == 422


class TestMatchAPI:
    async def test_report_1v1_match(self, client, admin_headers):
        p1 = (await client.post("/api/players", json={"username": "match-p1"}, headers=admin_headers)).json()
        p2 = (await client.post("/api/players", json={"username": "match-p2"}, headers=admin_headers)).json()
        r = await client.post(
            "/api/matches",
            json={
                "format": "1v1",
                "team_a_player_names": [p1["username"]],
                "team_b_player_names": [p2["username"]],
                "score_a": 7,
                "score_b": 3,
            },
            headers=admin_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["winner"] in ("team_a", "team_b")
        assert "match_id" in data
        assert "elo_deltas" in data
        assert data["score"] == "7-3"

    async def test_list_matches(self, client):
        r = await client.get("/api/matches?limit=5")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestLeaderboardAPI:
    async def test_leaderboard_returns_list(self, client):
        r = await client.get("/api/leaderboard/1v1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestAdminAPI:
    async def test_admin_page_redirects_anon(self, anon_client):
        r = await anon_client.get("/admin", follow_redirects=False)
        assert r.status_code in (302, 303)

    async def test_csrf_endpoint(self, client):
        r = await client.get("/api/csrf/token")
        assert r.status_code == 200
        assert "csrf_token" in r.json()


class TestHealth:
    async def test_index_returns_html(self, client):
        r = await client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
