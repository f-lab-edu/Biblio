import pytest

from adapters.media.ffmpeg_client import FFmpegClient


class CapturingRunner:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def __call__(self, cmd: list[str], *, check: bool, timeout: float):
        self.calls.append({"cmd": cmd, "check": check, "timeout": timeout})


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
