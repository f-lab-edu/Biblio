import httpx


class TestHealthReady:
    async def test_returns_200_with_status_and_model_version(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["model_version"]  # non-empty string

    async def test_response_has_trace_id_header(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.get("/health")

        assert "x-trace-id" in response.headers


class TestHealthNotReady:
    async def test_returns_503(self, not_ready_client: httpx.AsyncClient):
        response = await not_ready_client.get("/health")

        assert response.status_code == 503

    async def test_response_has_error_shape(
        self, not_ready_client: httpx.AsyncClient
    ):
        response = await not_ready_client.get("/health")

        body = response.json()
        assert "code" in body
        assert "message" in body
        assert "trace_id" in body
