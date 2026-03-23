import json

import httpx
import pytest

from adapters.ai.gemini_vision_adapter import GeminiVisionAdapter


@pytest.mark.asyncio
async def test_gemini_vision_adapter_sends_user_role_in_request(tmp_path) -> None:
    captured: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"caption":"cap","ocr":"ocr","scene_tags":"tag"}'},
                            ]
                        }
                    }
                ]
            },
        )

    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"jpg-bytes")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
    adapter = GeminiVisionAdapter(
        project_id="test-project",
        location="us-central1",
        model="gemini-2.5-flash",
        timeout_sec=5,
        client=client,
    )
    async def fake_get_token() -> str:
        return "fake-token"

    adapter._get_token = fake_get_token  # type: ignore[method-assign]

    try:
        result = await adapter._analyze(str(image_path), "trace-vision-3")
    finally:
        await adapter.aclose()

    assert result.visual_caption == "cap"
    assert captured
    assert captured[0]["contents"][0]["role"] == "user"
