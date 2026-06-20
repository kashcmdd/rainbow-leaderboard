class TestCSRFIntegration:
    async def test_get_token_returns_token(self, client):
        r = await client.get("/api/csrf/token")
        assert r.status_code == 200
        data = r.json()
        assert "csrf_token" in data
        assert len(data["csrf_token"]) > 0

    async def test_token_is_persisted_in_session(self, client):
        r1 = await client.get("/api/csrf/token")
        t1 = r1.json()["csrf_token"]
        r2 = await client.get("/api/csrf/token")
        t2 = r2.json()["csrf_token"]
        assert t1 == t2

    async def test_missing_token_on_mutating_request_returns_403(self, client):
        r = await client.post("/api/players", json={"username": "csrf-test"})
        assert r.status_code == 403

    async def test_valid_token_succeeds(self, client):
        token_r = await client.get("/api/csrf/token")
        token = token_r.json()["csrf_token"]
        r = await client.post(
            "/api/players",
            json={"username": "csrf-test-ok"},
            headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        )
        assert r.status_code in (200, 201, 409)

    async def test_wrong_token_returns_403(self, client):
        await client.get("/api/csrf/token")
        r = await client.post(
            "/api/players",
            json={"username": "csrf-test-wrong"},
            headers={"X-CSRF-Token": "wrong-token", "Content-Type": "application/json"},
        )
        assert r.status_code == 403
