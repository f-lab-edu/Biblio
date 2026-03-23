"""Production VisionAdapter using Vertex AI Gemini REST API.

Makes a single API call per keyframe that extracts caption, OCR text, and scene tags.
Results are cached per keyframe_path so the three protocol methods share one call.
Auth uses Application Default Credentials (ADC).
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from adapters.ai.vision_adapter import VisionResult

_PROMPT = """\
Analyze this image and return a JSON object with exactly these three keys:
- "caption": A brief visual description of the scene (1-2 sentences, Korean preferred)
- "ocr": Any text visible in the image (empty string if none)
- "scene_tags": Comma-separated tags describing the scene content (Korean preferred)

Return ONLY valid JSON, no markdown fencing, no other text."""


class GeminiVisionAdapter:
    """Satisfies the VisionAdapter protocol with a real Gemini backend."""

    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        model: str,
        timeout_sec: int = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = (
            f"https://{location}-aiplatform.googleapis.com/v1"
            f"/projects/{project_id}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent"
        )
        self._timeout_sec = timeout_sec
        self._client = client or httpx.AsyncClient(timeout=timeout_sec)
        self._cache: dict[str, VisionResult] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._credentials: Any | None = None
        self._auth_request: Any | None = None

    async def _get_token(self) -> str:
        if self._credentials is None:
            import google.auth
            from google.auth.transport.requests import Request as GoogleAuthRequest

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self._credentials = creds
            self._auth_request = GoogleAuthRequest()

        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, self._auth_request)
        return self._credentials.token

    async def _analyze(self, keyframe_path: str, trace_id: str) -> VisionResult:
        if keyframe_path in self._cache:
            return self._cache[keyframe_path]

        lock = self._locks.setdefault(keyframe_path, asyncio.Lock())
        async with lock:
            if keyframe_path in self._cache:
                return self._cache[keyframe_path]

            result = await self._call_gemini(keyframe_path, trace_id)
            self._cache[keyframe_path] = result
            self._locks.pop(keyframe_path, None)
            return result

    async def _call_gemini(self, keyframe_path: str, trace_id: str) -> VisionResult:
        image_bytes = await asyncio.to_thread(Path(keyframe_path).read_bytes)
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        token = await self._get_token()

        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": _PROMPT},
                    {"inlineData": {"mimeType": "image/jpeg", "data": image_b64}},
                ],
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }

        try:
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.bind(trace_id=trace_id, keyframe_path=keyframe_path).warning(
                "Gemini HTTP error status={} body={}",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise
        except Exception as exc:
            logger.bind(trace_id=trace_id, keyframe_path=keyframe_path).warning(
                "Gemini request failed: {}",
                exc,
            )
            raise
        return self._parse_response(response.json(), trace_id)

    def _parse_response(self, payload: dict, trace_id: str) -> VisionResult:
        try:
            candidates = payload.get("candidates", [])
            if not candidates:
                logger.bind(trace_id=trace_id).warning("Gemini response contained no candidates")
                return VisionResult()
            text = candidates[0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            return VisionResult(
                visual_caption=str(data.get("caption", "")),
                ocr_text=str(data.get("ocr", "")),
                scene_tags=str(data.get("scene_tags", "")),
            )
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.bind(trace_id=trace_id).warning(
                "Gemini response parse failed: {} payload={}",
                exc,
                json.dumps(payload)[:500],
            )
            return VisionResult()

    # ------------------------------------------------------------------
    # VisionAdapter protocol methods
    # ------------------------------------------------------------------

    async def extract_caption(self, keyframe_path: str, *, trace_id: str) -> str:
        return (await self._analyze(keyframe_path, trace_id)).visual_caption

    async def extract_ocr(self, keyframe_path: str, *, trace_id: str) -> str:
        return (await self._analyze(keyframe_path, trace_id)).ocr_text

    async def extract_scene_tags(self, keyframe_path: str, *, trace_id: str) -> str:
        return (await self._analyze(keyframe_path, trace_id)).scene_tags

    async def aclose(self) -> None:
        await self._client.aclose()
