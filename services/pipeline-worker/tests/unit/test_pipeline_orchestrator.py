from src.services.pipeline_orchestrator import PipelineOrchestrator


def test_pipeline_orchestrator_defaults_chunk_concurrency_to_one() -> None:
    orchestrator = PipelineOrchestrator(
        video_repository=None,  # type: ignore[arg-type]
        artifact_repository=None,  # type: ignore[arg-type]
        storage_client=None,  # type: ignore[arg-type]
        ffmpeg_client=None,  # type: ignore[arg-type]
        stt_adapter=None,  # type: ignore[arg-type]
        embedding_client=None,  # type: ignore[arg-type]
        vision_adapter=None,  # type: ignore[arg-type]
        workdir_manager=None,  # type: ignore[arg-type]
        chunking_service=None,  # type: ignore[arg-type]
        embedding_batch_size=16,
        stt_model_version="chirp_2",
        embedding_model_version="fake-v001",
    )

    assert orchestrator._chunk_concurrency == 1
