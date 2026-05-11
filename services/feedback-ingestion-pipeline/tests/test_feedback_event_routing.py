import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


COMPONENT_ROOT = Path(__file__).resolve().parents[1]


def test_valid_fixture_routes_only_to_raw_sink() -> None:
    output = run_fixture_smoke("fixtures/feedback_event.valid.jsonl")

    assert len(output.raw_events) == 1
    assert output.error_events == []
    assert output.raw_events[0]["event_id"] == "evt_test_001"
    assert "error_code" not in output.raw_events[0]


def test_unsupported_schema_routes_only_to_error_sink() -> None:
    output = run_fixture_smoke("fixtures/feedback_event.unsupported_schema.jsonl")

    assert output.raw_events == []
    assert len(output.error_events) == 1
    error_event = output.error_events[0]
    assert error_event["error_code"] == "unsupported_schema_version"
    assert error_event["event_id"] == "evt_test_unsupported_001"
    assert error_event["trace_id"] == "trace_test_unsupported_001"
    assert "original_payload" in error_event


def test_missing_required_field_routes_only_to_error_sink() -> None:
    output = run_fixture_smoke("fixtures/feedback_event.missing_required_field.jsonl")

    assert output.raw_events == []
    assert len(output.error_events) == 1
    error_event = output.error_events[0]
    assert error_event["error_code"] == "malformed_feedback_event"
    assert error_event["event_id"] == "evt_test_missing_001"
    assert error_event["trace_id"] == "trace_test_missing_001"
    assert "original_payload" in error_event


def test_malformed_json_routes_only_to_error_sink_with_original_payload() -> None:
    output = run_fixture_smoke("fixtures/feedback_event.malformed.jsonl")

    assert output.raw_events == []
    assert len(output.error_events) == 1
    error_event = output.error_events[0]
    assert error_event["error_code"] == "malformed_feedback_event"
    assert "original_payload" in error_event
    assert '"req_id":' in error_event["original_payload"]


def test_duplicate_delivery_preserves_each_raw_log_entry() -> None:
    output = run_fixture_smoke("fixtures/feedback_event.duplicate.jsonl")

    assert len(output.raw_events_with_duplicates) == 2
    assert output.error_events == []
    assert {event["event_id"] for event in output.raw_events_with_duplicates} == {
        "evt_test_duplicate_001",
    }


class FixtureSmokeOutput:
    def __init__(
        self,
        raw_events: list[dict],
        error_events: list[dict],
        raw_events_with_duplicates: list[dict],
    ) -> None:
        self.raw_events = raw_events
        self.error_events = error_events
        self.raw_events_with_duplicates = raw_events_with_duplicates


def run_fixture_smoke(fixture_path: str) -> FixtureSmokeOutput:
    vector_probe = subprocess.run(
        ["vector", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if vector_probe.returncode != 0:
        pytest.skip("Vector CLI is not available to this test process")

    with tempfile.TemporaryDirectory() as output_dir:
        with tempfile.TemporaryDirectory() as vector_data_dir:
            environment = os.environ.copy()
            environment["FIP_FIXTURE_INPUT_PATH"] = f"./{fixture_path}"
            environment["FIP_LOCAL_OUTPUT_DIR"] = output_dir
            environment["FIP_VECTOR_DATA_DIR"] = vector_data_dir
            environment["FIP_SMOKE_TIMEOUT_SEC"] = "2"
            command = ["./scripts/smoke_fixture_local.sh"]
            subprocess.run(
                command,
                cwd=COMPONENT_ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            return FixtureSmokeOutput(
                raw_events=read_jsonl(Path(output_dir) / "raw-events.jsonl"),
                error_events=read_jsonl(Path(output_dir) / "error-events.jsonl"),
                raw_events_with_duplicates=read_jsonl_preserving_duplicates(
                    Path(output_dir) / "raw-events.jsonl",
                ),
            )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unique_events = {}
    for event in events:
        event_key = event.get("event_id") or event.get("original_payload") or json.dumps(
            event,
            sort_keys=True,
        )
        unique_events[event_key] = event
    return list(unique_events.values())


def read_jsonl_preserving_duplicates(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
