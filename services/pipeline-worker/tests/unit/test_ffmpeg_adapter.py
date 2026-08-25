import subprocess
from types import SimpleNamespace

import pytest

from src.infra.media.ffmpeg_client import FFmpegClient


class CapturingRunner:
    def __init__(self):
        self.calls: list[dict[str, object]] = []
        self.stdout = "12.345\n"

    def __call__(self, cmd: list[str], **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        return SimpleNamespace(stdout=self.stdout)


def test_extract_audio_runs_flac_command(tmp_path):
    runner = CapturingRunner()
    adapter = FFmpegClient(ffmpeg_path="ffmpeg", runner=runner)
    input_file = tmp_path / "input.mp4"
    output_file = tmp_path / "output.flac"
    input_file.write_text("dummy")

    adapter.extract_audio(input_file, output_file)

    assert runner.calls, "Runner not invoked"
    recorded = runner.calls[-1]
    assert recorded["check"] is True
    assert recorded["timeout"] == pytest.approx(120.0)
    assert str(input_file) in recorded["cmd"]
    assert "-vn" in recorded["cmd"]
    assert "-ac" in recorded["cmd"]
    assert "16000" in recorded["cmd"]
    assert "s16" in recorded["cmd"]
    assert "flac" in recorded["cmd"]
    assert str(output_file) == recorded["cmd"][-1]


def test_extract_keyframe_uses_select_filter(tmp_path):
    runner = CapturingRunner()
    adapter = FFmpegClient(ffmpeg_path="ffmpeg", runner=runner)
    adapter.extract_keyframe("video.mp4", "frame.jpg", offset_sec=12.5, timeout=10.0)

    assert runner.calls, "Runner not invoked"
    recorded = runner.calls[-1]
    assert "-ss" in recorded["cmd"]
    assert "12.5" in recorded["cmd"]
    assert "-vf" in recorded["cmd"]
    assert "select='eq(pict_type,I)'" in recorded["cmd"]
    assert "-frames:v" in recorded["cmd"]
    assert recorded["timeout"] == pytest.approx(10.0)


def test_probe_duration_returns_milliseconds(tmp_path):
    runner = CapturingRunner()
    adapter = FFmpegClient(ffprobe_path="custom-ffprobe", runner=runner)
    input_file = tmp_path / "input.mp4"

    duration_ms = adapter.probe_duration_ms(input_file, timeout=7.0)

    assert duration_ms == 12345
    recorded = runner.calls[-1]
    assert recorded["cmd"][0] == "custom-ffprobe"
    assert "format=duration" in recorded["cmd"]
    assert str(input_file) == recorded["cmd"][-1]
    assert recorded["capture_output"] is True
    assert recorded["text"] is True
    assert recorded["timeout"] == pytest.approx(7.0)


def test_extract_frame_candidate_seeks_before_opening_input() -> None:
    runner = CapturingRunner()
    adapter = FFmpegClient(runner=runner)

    adapter.extract_frame_candidate(
        "video.mp4",
        "frame-00000.jpg",
        timestamp_ms=90_000,
        max_width=1280,
    )

    command = runner.calls[0]["cmd"]
    assert command[command.index("-ss") + 1] == "90.000"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-i") + 1] == "video.mp4"
    assert command[command.index("-vf") + 1] == "scale='min(1280,iw)':-2"
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[-1] == "frame-00000.jpg"
    assert runner.calls[0]["timeout"] == pytest.approx(30.0)
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("timestamp_ms", "max_width", "message"),
    [(-1, 1280, "timestamp_ms"), (0, 0, "max_width")],
)
def test_extract_frame_candidate_rejects_invalid_request(
    timestamp_ms: int,
    max_width: int,
    message: str,
) -> None:
    adapter = FFmpegClient(runner=CapturingRunner())

    with pytest.raises(ValueError, match=message):
        adapter.extract_frame_candidate(
            "video.mp4",
            "frame.jpg",
            timestamp_ms=timestamp_ms,
            max_width=max_width,
        )


def test_extract_audio_part_uses_requested_interval(tmp_path):
    runner = CapturingRunner()
    adapter = FFmpegClient(ffmpeg_path="ffmpeg", runner=runner)
    input_file = tmp_path / "input.flac"
    output_file = tmp_path / "part.flac"

    adapter.extract_audio_part(
        input_file,
        output_file,
        start_ms=895000,
        end_ms=1800000,
        timeout=180.0,
    )

    recorded = runner.calls[-1]
    command = recorded["cmd"]
    assert command[command.index("-ss") + 1] == "895.000"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-t") + 1] == "905.000"
    assert str(output_file) == command[-1]
    assert recorded["timeout"] == pytest.approx(180.0)


def test_media_failure_does_not_expose_signed_url() -> None:
    signed_url = "https://storage.test/video.mp4?secret=token"

    def failing_runner(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=signed_url)

    adapter = FFmpegClient(runner=failing_runner)

    with pytest.raises(RuntimeError) as error:
        adapter.probe_duration_ms(signed_url)

    assert str(error.value) == "media command failed"
    assert signed_url not in str(error.value)


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(-1, 1000), (1000, 1000), (2000, 1000)],
)
def test_extract_audio_part_rejects_invalid_interval(start_ms: int, end_ms: int) -> None:
    adapter = FFmpegClient(runner=CapturingRunner())

    with pytest.raises(ValueError, match="0 <= start_ms < end_ms"):
        adapter.extract_audio_part(
            "input.flac",
            "part.flac",
            start_ms=start_ms,
            end_ms=end_ms,
        )
