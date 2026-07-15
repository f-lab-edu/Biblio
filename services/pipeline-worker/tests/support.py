import asyncio
from collections.abc import Callable
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.infra.ai.embedding_client import EmbeddingClient
from src.infra.ai.google_stt_adapter import GoogleSTTAdapter
from src.infra.ai.retry_policy import JitterCallable, SleepCallable
from src.infra.db.models import Base
from src.infra.media.ffmpeg_client import FFmpegClient


from sqlalchemy.ext.asyncio import AsyncEngine


async def _no_retry_sleep(delay_seconds: float) -> None:
    del delay_seconds


def _zero_jitter() -> float:
    return 0.0


async def create_test_engine() -> AsyncEngine:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class RecordingFFmpegRunner:
    def __init__(self, *, duration_sec: float = 2.0) -> None:
        self.commands: list[list[str]] = []
        self.duration_sec = duration_sec

    def __call__(
        self,
        command: list[str],
        *,
        check: bool,
        timeout: float,
        capture_output: bool = False,
        text: bool = False,
    ) -> object:
        del check, timeout, text
        self.commands.append(command)
        if capture_output:
            return SimpleNamespace(stdout=f"{self.duration_sec}\n")
        Path(command[-1]).write_bytes(b"generated-artifact")
        return SimpleNamespace()


def build_ffmpeg_adapter(*, duration_sec: float = 2.0) -> tuple[FFmpegClient, RecordingFFmpegRunner]:
    runner = RecordingFFmpegRunner(duration_sec=duration_sec)
    return FFmpegClient(runner=runner), runner


def build_embedding_client(
    *,
    model_version: str = "v001",
    ready_model_versions: list[str] | None = None,
    health_payload: dict | None = None,
    embeddings_factory: Callable[[list[str]], list[list[float]]] | None = None,
    fail_embed_times: int = 0,
    embedding_model_version: str = "v001",
    max_retries: int = 2,
    sleep: SleepCallable = _no_retry_sleep,
    jitter: JitterCallable = _zero_jitter,
) -> EmbeddingClient:
    state = {"failures": fail_embed_times}

    def factory(texts: list[str]) -> list[list[float]]:
        if embeddings_factory is not None:
            return embeddings_factory(texts)
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json=health_payload
                or {
                    "status": "ok",
                    "ready_model_versions": ready_model_versions or [model_version],
                },
            )
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
        max_retries=max_retries,
        model_version=embedding_model_version,
        client=httpx.AsyncClient(transport=transport),
        sleep=sleep,
        jitter=jitter,
    )


def build_stt_adapter(
    *,
    fail_submit_times: int = 0,
    model_version: str = "chirp_2",
) -> GoogleSTTAdapter:
    state = {"failures": fail_submit_times}

    async def client(audio_uri: str, trace_id: str) -> dict:
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

    return GoogleSTTAdapter(client=client, max_retries=2)
