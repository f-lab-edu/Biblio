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
        try:
            if capture_output:
                return self.runner(
                    command,
                    check=True,
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                )
            return self.runner(command, check=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("media command timed out") from None
        except subprocess.CalledProcessError:
            raise RuntimeError("media command failed") from None

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
            *self._input_arguments(input_file),
        ]
        result = self._run(command, timeout, capture_output=True)
        return self._parse_duration_ms(str(getattr(result, "stdout", "")))

    @staticmethod
    def _parse_duration_ms(duration_output: str) -> int:
        duration_text = duration_output.strip()
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
            *self._input_arguments(input_file),
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

        self._validate_audio_interval(start_ms, end_ms)
        duration_ms = end_ms - start_ms
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-ss",
            self._format_milliseconds(start_ms),
            *self._input_arguments(input_file),
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

    def extract_frame_candidate(
        self,
        input_file: Path | str,
        output_file: Path | str,
        *,
        timestamp_ms: int,
        max_width: int,
        timeout: float = 30.0,
    ) -> None:
        self._validate_frame_candidate_request(timestamp_ms, max_width)
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-ss",
            self._format_milliseconds(timestamp_ms),
            *self._input_arguments(input_file),
            "-vf",
            f"scale='min({max_width},iw)':-2",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_file),
        ]
        self._run(command, timeout)

    @staticmethod
    def _validate_audio_interval(start_ms: int, end_ms: int) -> None:
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("Audio part must satisfy 0 <= start_ms < end_ms")

    @staticmethod
    def _input_arguments(input_file: Path | str) -> list[str]:
        input_value = str(input_file)
        reconnect_options: list[str] = []
        if input_value.startswith(("http://", "https://")):
            reconnect_options = [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
            ]
        return [*reconnect_options, "-i", input_value]

    @staticmethod
    def _validate_frame_candidate_request(
        timestamp_ms: int,
        max_width: int,
    ) -> None:
        if timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if max_width <= 0:
            raise ValueError("max_width must be positive")

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
