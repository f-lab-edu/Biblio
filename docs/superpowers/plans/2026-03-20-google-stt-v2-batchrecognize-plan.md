# Google STT v2 BatchRecognize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate pipeline-worker STT from local-file input to Google Speech-to-Text v2 `BatchRecognize` using a canonical GCS audio artifact and long-running operation handling.

**Architecture:** Keep FFmpeg extraction local, upload `audio.flac` once to object storage as the canonical audio artifact, and make STT consume the stored `gs://...` URI instead of a local temp path. Normalize batch operation results back into `STTTranscriptionResult`, preserve transcript reuse semantics, and avoid re-downloading audio during resume paths.

**Tech Stack:** Python 3.11, asyncio, Pydantic settings, Google Speech-to-Text v2 SDK, existing storage/DB adapters, pytest

---

### Task 1: Introduce canonical audio reference and storage URI support

**Files:**
- Modify: `services/pipeline-worker/src/adapters/storage/client.py`
- Modify: `services/pipeline-worker/src/adapters/storage/gcs_client.py`
- Modify: `services/pipeline-worker/src/adapters/storage/inmemory_storage.py`
- Modify: `services/pipeline-worker/tests/unit/test_storage_clients.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_inmemory_storage_builds_gs_uri() -> None:
    storage = InMemoryStorageClient(bucket_name="bucket")
    assert storage.object_uri("artifacts/video/audio.flac") == "gs://bucket/artifacts/video/audio.flac"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/pipeline-worker/tests/unit/test_storage_clients.py -v`
Expected: FAIL because `object_uri` and `bucket_name` support do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class StorageClient(Protocol):
    async def download_object(self, storage_path: str, destination: Path) -> None: ...
    async def upload_object(self, source: Path, storage_path: str) -> None: ...
    async def delete_object(self, storage_path: str) -> None: ...
    def object_uri(self, storage_path: str) -> str: ...
```

```python
class GCSStorageClient(StorageClient):
    def __init__(self, bucket_factory: Callable[[], Any], *, bucket_name: str) -> None:
        self._bucket_name = bucket_name

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self._bucket_name}/{storage_path}"
```

```python
class InMemoryStorageClient(StorageClient):
    def __init__(self, initial_objects: dict[str, bytes] | None = None, *, bucket_name: str = "test-bucket") -> None:
        self.bucket_name = bucket_name

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self.bucket_name}/{storage_path}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest services/pipeline-worker/tests/unit/test_storage_clients.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/pipeline-worker/src/adapters/storage/client.py services/pipeline-worker/src/adapters/storage/gcs_client.py services/pipeline-worker/src/adapters/storage/inmemory_storage.py services/pipeline-worker/tests/unit/test_storage_clients.py
git commit -m "refactor: add storage object uri support"
```

### Task 2: Refactor GoogleSTTAdapter for BatchRecognize input/output semantics

**Files:**
- Modify: `services/pipeline-worker/src/adapters/ai/google_stt_adapter.py`
- Modify: `services/pipeline-worker/tests/support.py`
- Modify: `services/pipeline-worker/tests/unit/test_google_stt_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_google_stt_adapter_accepts_audio_uri() -> None:
    adapter = build_stt_adapter()
    result = await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-1")
    assert result.stt_model_version == "chirp_2"
```

```python
@pytest.mark.asyncio
async def test_google_stt_adapter_retries_submit_failures_only() -> None:
    adapter = build_stt_adapter(fail_submit_times=1)
    result = await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-2")
    assert len(result.segments) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest services/pipeline-worker/tests/unit/test_google_stt_adapter.py -v`
Expected: FAIL because adapter expects `audio_path` and current mock client shape is wrong.

- [ ] **Step 3: Write minimal implementation**

```python
STTCallable = Callable[[str, str], Awaitable[dict[str, Any] | STTTranscriptionResult]]

async def transcribe(self, *, audio_uri: str, trace_id: str) -> STTTranscriptionResult:
    if not audio_uri.startswith("gs://"):
        raise ExternalAIAdapterError(...)
    response = await asyncio.wait_for(self._client(audio_uri, trace_id), timeout=self._operation_timeout_sec)
    return self._normalize(response, trace_id)
```

```python
def build_stt_adapter(...):
    async def client(audio_uri: str, trace_id: str) -> dict:
        return {
            "segments": [...],
            "stt_model_version": model_version,
        }
```

Implementation notes:
- Replace local file existence checks with URI validation.
- Split adapter timeout config into submit/operation waits if the SDK wrapper is added in this task; otherwise keep one internal timeout now and schedule the split in Task 5.
- Keep `_normalize()` as the single normalization boundary.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest services/pipeline-worker/tests/unit/test_google_stt_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/pipeline-worker/src/adapters/ai/google_stt_adapter.py services/pipeline-worker/tests/support.py services/pipeline-worker/tests/unit/test_google_stt_adapter.py
git commit -m "refactor: switch stt adapter to gcs uri input"
```

### Task 3: Rework orchestrator audio/transcript flow around the canonical GCS audio artifact

**Files:**
- Modify: `services/pipeline-worker/src/services/pipeline_orchestrator.py`
- Modify: `services/pipeline-worker/tests/integration/test_resume_flow.py`
- Modify: `services/pipeline-worker/tests/unit/test_process_video.py`
- Modify: `services/pipeline-worker/tests/conftest.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_resume_flow_reuses_audio_asset_uri_without_ffmpeg(...) -> None:
    ...
    result = await process_video_use_case.execute(video_id=video_id, trace_id="trace")
    assert result.action == "processed"
    assert len([cmd for cmd in runner.commands if "-vn" in cmd]) == 0
```

```python
@pytest.mark.asyncio
async def test_process_video_uploads_audio_once_then_transcribes_from_gcs_uri(...) -> None:
    ...
    assert "artifacts/{video_id}/audio.flac" in storage_client.objects
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest services/pipeline-worker/tests/integration/test_resume_flow.py services/pipeline-worker/tests/unit/test_process_video.py -v`
Expected: FAIL because orchestrator still downloads audio locally for STT and still calls `transcribe(audio_path=...)`.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(slots=True)
class AudioArtifactRef:
    local_path: Path | None
    storage_path: str
    object_uri: str
```

```python
async def _ensure_audio(...) -> AudioArtifactRef:
    if state.has_audio_asset and audio_asset is not None:
        return AudioArtifactRef(local_path=None, storage_path=audio_asset.storage_path, object_uri=self._storage_client.object_uri(audio_asset.storage_path))
    local_audio = workdir / "audio.flac"
    await asyncio.to_thread(self._ffmpeg_client.extract_audio, original, local_audio)
    storage_path = f"artifacts/{video.id}/audio.flac"
    await self._storage_client.upload_object(local_audio, storage_path)
    ...
    return AudioArtifactRef(local_path=local_audio, storage_path=storage_path, object_uri=self._storage_client.object_uri(storage_path))
```

```python
stt_result = await self._stt_adapter.transcribe(audio_uri=audio.object_uri, trace_id=trace_id)
```

Implementation notes:
- Do not re-download the audio artifact just to feed STT.
- Keep local extraction only for newly created audio.
- Preserve current transcript reuse semantics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest services/pipeline-worker/tests/integration/test_resume_flow.py services/pipeline-worker/tests/unit/test_process_video.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/pipeline-worker/src/services/pipeline_orchestrator.py services/pipeline-worker/tests/integration/test_resume_flow.py services/pipeline-worker/tests/unit/test_process_video.py services/pipeline-worker/tests/conftest.py
git commit -m "refactor: use canonical gcs audio artifact for stt"
```

### Task 4: Add settings and wiring for Google STT v2 batch operation behavior

**Files:**
- Modify: `services/pipeline-worker/src/config/settings.py`
- Modify: `services/pipeline-worker/.env.example`
- Modify: `services/pipeline-worker/src/main.py`
- Modify: `services/pipeline-worker/tests/unit/test_settings.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_settings_read_stt_batch_timeouts() -> None:
    settings = Settings(
        ...,
        STT_SUBMIT_TIMEOUT_SEC=30,
        STT_OPERATION_TIMEOUT_SEC=900,
    )
    assert settings.stt_submit_timeout_sec == 30
    assert settings.stt_operation_timeout_sec == 900
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest services/pipeline-worker/tests/unit/test_settings.py -v`
Expected: FAIL because new settings fields do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class Settings(BaseSettings):
    stt_submit_timeout_sec: int = Field(default=30, alias="STT_SUBMIT_TIMEOUT_SEC", ge=1)
    stt_operation_timeout_sec: int = Field(default=900, alias="STT_OPERATION_TIMEOUT_SEC", ge=1)
```

Implementation notes:
- Keep `STT_MODEL_VERSION`.
- Do not add `STT_OUTPUT_BUCKET` in MVP plan unless GCS output mode is actually implemented.
- If runtime wiring for the real Google client is still deferred, add a `TODO` boundary in `main.py` instead of inventing half-wired production code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest services/pipeline-worker/tests/unit/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/pipeline-worker/src/config/settings.py services/pipeline-worker/.env.example services/pipeline-worker/src/main.py services/pipeline-worker/tests/unit/test_settings.py
git commit -m "chore: add stt batch timeout settings"
```

### Task 5: Update specs and add regression coverage for the new contract

**Files:**
- Modify: `docs/Tech_Spec/External_AI_Adapters_Spec.md`
- Modify: `docs/Tech_Spec/Pipeline_Worker_Spec.md`
- Modify: `services/pipeline-worker/tests/integration/test_process_flow.py`
- Modify: `services/pipeline-worker/tests/integration/test_consumer_flow.py`

- [ ] **Step 1: Write the failing/regression assertions**

```python
@pytest.mark.asyncio
async def test_process_flow_persists_audio_asset_before_stt(...) -> None:
    ...
    assert storage_client.objects[f"artifacts/{video_id}/audio.flac"] == b"generated-artifact"
```

- [ ] **Step 2: Run tests to verify current behavior is not fully covered**

Run: `pytest services/pipeline-worker/tests/integration/test_process_flow.py services/pipeline-worker/tests/integration/test_consumer_flow.py -v`
Expected: FAIL or insufficient assertions around canonical audio reuse and settings.

- [ ] **Step 3: Update docs and tests**

```markdown
- STT adapter input contract: `audio_uri` (`gs://...`)
- BatchRecognize returns long-running operation; worker waits for completion
- MVP output mode: single-file inline response
- Resume path reuses canonical audio artifact instead of re-downloading audio locally for STT
```

- [ ] **Step 4: Run focused integration tests**

Run: `pytest services/pipeline-worker/tests/integration/test_process_flow.py services/pipeline-worker/tests/integration/test_consumer_flow.py services/pipeline-worker/tests/integration/test_resume_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/Tech_Spec/External_AI_Adapters_Spec.md docs/Tech_Spec/Pipeline_Worker_Spec.md services/pipeline-worker/tests/integration/test_process_flow.py services/pipeline-worker/tests/integration/test_consumer_flow.py services/pipeline-worker/tests/integration/test_resume_flow.py
git commit -m "docs: align worker and adapter specs with batch stt flow"
```

### Final Verification

- [ ] Run the full pipeline-worker test target

Run: `pytest services/pipeline-worker/tests -v`
Expected: All pipeline-worker unit and integration tests PASS.

- [ ] Smoke-check plan assumptions against the current codebase

Run: `rg -n "audio_path=|audio_uri=|object_uri\\(|STT_TIMEOUT_SEC|STT_SUBMIT_TIMEOUT_SEC|STT_OPERATION_TIMEOUT_SEC" services/pipeline-worker`
Expected: no stale `audio_path` STT call sites remain; new timeout fields are wired.

- [ ] Prepare execution handoff

If implementation starts in a fresh session, begin with `superpowers:subagent-driven-development` for per-task execution and review.
