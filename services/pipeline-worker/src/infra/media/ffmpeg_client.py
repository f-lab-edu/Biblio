import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


RunnerType = Callable[..., object]


@dataclass
class FFmpegClient:
    """Execute FFmpeg commands for media inspection and extraction."""

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    runner: RunnerType | None = None

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = subprocess.run

    def _run(
        self,
        command: list[str],
        timeout: float,
        *,
        capture_output: bool = False,
    ) -> object:
        if self.runner is None:
            raise RuntimeError("No runner configured for FFmpegClient")
        if capture_output:
            return self.runner(
                command,
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        return self.runner(command, check=True, timeout=timeout)

    def probe_duration_ms(self, input_file: Path | str, timeout: float = 30.0) -> int:
        """Return the media duration reported by ffprobe in milliseconds."""

        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_file),
        ]
        result = self._run(command, timeout, capture_output=True)
        duration_text = str(getattr(result, "stdout", "")).strip()
        try:
            duration_sec = float(duration_text)
        except ValueError as exc:
            raise RuntimeError(f"ffprobe returned an invalid duration: {duration_text!r}") from exc
        if duration_sec < 0:
            raise RuntimeError(f"ffprobe returned a negative duration: {duration_text!r}")
        return round(duration_sec * 1000)

    def extract_audio(self, input_file: Path | str, output_file: Path | str, timeout: float = 120.0) -> None:
        """Extract mono FLAC audio spec'd in the design."""

        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-i",
            str(input_file),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            "-sample_fmt",
            "s16",
            "-f",
            "flac",
            str(output_file),
        ]
        self._run(command, timeout)

    def extract_audio_part(
        self,
        input_file: Path | str,
        output_file: Path | str,
        *,
        start_ms: int,
        end_ms: int,
        timeout: float = 120.0,
    ) -> None:
        """Extract a mono FLAC interval using millisecond media offsets."""

        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("Audio part must satisfy 0 <= start_ms < end_ms")
        duration_ms = end_ms - start_ms
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-i",
            str(input_file),
            "-ss",
            self._format_milliseconds(start_ms),
            "-t",
            self._format_milliseconds(duration_ms),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            "-sample_fmt",
            "s16",
            "-f",
            "flac",
            str(output_file),
        ]
        self._run(command, timeout)

    @staticmethod
    def _format_milliseconds(milliseconds: int) -> str:
        return f"{milliseconds / 1000:.3f}"

    def extract_keyframe(
        self,
        input_file: Path | str,
        output_file: Path | str,
        *,
        offset_sec: float | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Extract a representative keyframe for the video chunk."""

        command = [self.ffmpeg_path, "-hide_banner", "-y"]
        if offset_sec is not None:
            command.extend(["-ss", str(offset_sec)])
        command.extend(
            [
                "-i",
                str(input_file),
                "-vf",
                "select='eq(pict_type,I)'",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_file),
            ]
        )
        self._run(command, timeout)
