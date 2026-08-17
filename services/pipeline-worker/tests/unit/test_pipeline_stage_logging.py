import pytest
from loguru import logger

from src.services.pipeline_errors import PipelineStageError
from src.services.pipeline_orchestrator import _pipeline_stage


def _capture_stage_records() -> tuple[list[dict], int]:
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record))
    return records, sink_id


def test_pipeline_stage_logs_started_and_successful_finish() -> None:
    records, sink_id = _capture_stage_records()
    try:
        with _pipeline_stage(
            "DOWNLOAD",
            stage_name="download",
            trace_id="trace-stage-success",
            video_id="video-stage-success",
        ):
            pass
    finally:
        logger.remove(sink_id)

    stage_records = [record for record in records if record["message"].startswith("pipeline.stage ")]
    assert [record["extra"]["event"] for record in stage_records] == ["started", "finished"]
    assert [record["extra"]["status"] for record in stage_records] == ["running", "success"]
    assert all(record["extra"]["stage"] == "download" for record in stage_records)
    assert all(record["extra"]["trace_id"] == "trace-stage-success" for record in stage_records)
    assert all(record["extra"]["video_id"] == "video-stage-success" for record in stage_records)
    assert "stage=download event=started status=running" in stage_records[0]["message"]
    assert "stage=download event=finished status=success" in stage_records[1]["message"]


def test_pipeline_stage_logs_failed_finish_and_wraps_error() -> None:
    records, sink_id = _capture_stage_records()
    try:
        with pytest.raises(PipelineStageError) as raised:
            with _pipeline_stage(
                "STT",
                stage_name="stt",
                trace_id="trace-stage-failure",
                video_id="video-stage-failure",
            ):
                raise RuntimeError("STT unavailable")
    finally:
        logger.remove(sink_id)

    stage_records = [record for record in records if record["message"].startswith("pipeline.stage ")]
    assert raised.value.failed_stage == "STT"
    assert [record["extra"]["event"] for record in stage_records] == ["started", "finished"]
    assert [record["extra"]["status"] for record in stage_records] == ["running", "failed"]
    assert all(record["extra"]["stage"] == "stt" for record in stage_records)
    assert "stage=stt event=finished status=failed" in stage_records[1]["message"]
