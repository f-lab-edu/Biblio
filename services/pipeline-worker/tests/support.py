from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adapters.ai.embedding_client import EmbeddingClient
from adapters.ai.google_stt_adapter import GoogleSTTAdapter
from adapters.db.models import Base
from adapters.media.ffmpeg_adapter import FFmpegAdapter


def build_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


class RecordingFFmpegRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], *, check: bool, timeout: float) -> None:
        self.commands.append(command)
        Path(command[-1]).write_bytes(b"generated-artifact")


def build_ffmpeg_adapter() -> tuple[FFmpegAdapter, RecordingFFmpegRunner]:
    runner = RecordingFFmpegRunner()
    return FFmpegAdapter(runner=runner), runner


def build_embedding_client(
    *,
    model_version: str = "v001",
    embeddings_factory: Callable[[list[str]], list[list[float]]] | None = None,
    fail_embed_times: int = 0,
) -> EmbeddingClient:
    state = {"failures": fail_embed_times}

    def factory(texts: list[str]) -> list[list[float]]:
        if embeddings_factory is not None:
            return embeddings_factory(texts)
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "model_version": model_version})
        if request.url.path == "/embed":
            if state["failures"] > 0:
                state["failures"] -= 1
                return httpx.Response(503, json={"code": "SERVICE_UNAVAILABLE"})
            payload = json.loads(request.content.decode())
            texts = payload["texts"]
            return httpx.Response(200, json={"embeddings": factory(texts)})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    return EmbeddingClient(
        base_url="https://embedding.local",
        timeout_sec=5,
        max_retries=2,
        client=httpx.AsyncClient(transport=transport),
    )


def build_stt_adapter(
    *,
    fail_times: int = 0,
    model_version: str = "google-stt-v1",
) -> GoogleSTTAdapter:
    state = {"failures": fail_times}

    async def client(audio_path: str, trace_id: str) -> dict:
        await asyncio.sleep(0)
        if state["failures"] > 0:
            state["failures"] -= 1
            raise TimeoutError("temporary timeout")
        return {
            "segments": [
                {"text": "Alpha sentence. Beta sentence.", "start_ms": 0, "end_ms": 1000},
                {"text": "Gamma sentence.", "start_ms": 1000, "end_ms": 2000},
            ],
            "stt_model_version": model_version,
        }

    return GoogleSTTAdapter(client=client, timeout_sec=5, max_retries=2)
