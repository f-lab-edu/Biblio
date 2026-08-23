import json

from loguru import logger

from src.utils.logging import configure_logging


def test_configured_logger_writes_cloud_logging_compatible_json(capsys) -> None:
    configure_logging()
    try:
        logger.bind(
            log_schema_version=2,
            event_name="pipeline.work.started",
            trace_id="trace-1",
            video_id="video-1",
            stage="TRANSCRIBE_PART",
            work_id="part-1",
            work_attempt=1,
        ).info("pipeline.work.started")
        payload = json.loads(capsys.readouterr().err)
    finally:
        logger.remove()

    assert payload["log_schema_version"] == 2
    assert payload["event_name"] == "pipeline.work.started"
    assert payload["trace_id"] == "trace-1"
    assert payload["stage"] == "TRANSCRIBE_PART"
    assert payload["work_id"] == "part-1"
    assert payload["message"] == "pipeline.work.started"
