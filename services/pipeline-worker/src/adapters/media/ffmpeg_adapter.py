from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


RunnerType = Callable[..., object]


@dataclass
class FFmpegAdapter:
    """Execute FFmpeg commands for audio extraction and keyframe screenshots."""

    ffmpeg_path: str = "ffmpeg"
    runner: RunnerType | None = None

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = subprocess.run

    def _run(self, command: list[str], timeout: float) -> None:
        if self.runner is None:
            raise RuntimeError("No runner configured for FFmpegAdapter")
        self.runner(command, check=True, timeout=timeout)

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
