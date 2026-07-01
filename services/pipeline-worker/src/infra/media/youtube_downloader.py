import asyncio
from pathlib import Path
from typing import Any, Callable, Protocol


class DownloadError(Exception):
    pass


class YoutubeDownloader(Protocol):
    async def download(self, source_url: str, destination: Path) -> Path:
        pass


class YtDlpYoutubeDownloader:
    def __init__(
        self,
        *,
        max_duration_sec: int,
        max_filesize_bytes: int,
        max_height: int,
        timeout_sec: int,
        youtube_dl_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._max_duration_sec = max_duration_sec
        self._max_filesize_bytes = max_filesize_bytes
        self._max_height = max_height
        self._timeout_sec = timeout_sec
        self._youtube_dl_factory = youtube_dl_factory or self._load_youtube_dl_factory()

    async def download(self, source_url: str, destination: Path) -> Path:
        try:
            return await asyncio.to_thread(self._download_sync, source_url, destination)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(str(exc)) from exc

    def _download_sync(self, source_url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        info = self._extract_info(source_url, self._metadata_options(), download=False)
        self._validate_metadata(info)

        output_template = str(destination.with_suffix("")) + ".%(ext)s"
        self._extract_info(source_url, self._download_options(output_template), download=True)
        if not destination.exists():
            raise DownloadError(f"Downloaded file was not created at {destination}")
        return destination

    def _extract_info(self, source_url: str, options: dict[str, Any], *, download: bool) -> dict[str, Any]:
        with self._youtube_dl_factory(options) as youtube_dl:
            info = youtube_dl.extract_info(source_url, download=download)
        return info or {}

    def _metadata_options(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": self._timeout_sec,
        }

    def _download_options(self, output_template: str) -> dict[str, Any]:
        return {
            **self._metadata_options(),
            "format": self._format_selector(),
            "merge_output_format": "mp4",
            "max_filesize": self._max_filesize_bytes,
            "outtmpl": output_template,
        }

    def _format_selector(self) -> str:
        max_height = self._max_height
        return (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={max_height}][ext=mp4]/"
            f"best[height<={max_height}]"
        )

    def _validate_metadata(self, info: dict[str, Any]) -> None:
        duration = info.get("duration")
        if duration is not None and float(duration) > self._max_duration_sec:
            raise DownloadError(f"YouTube video duration exceeds {self._max_duration_sec} seconds.")

        filesize = info.get("filesize") or info.get("filesize_approx")
        if filesize is not None and int(filesize) > self._max_filesize_bytes:
            raise DownloadError(f"YouTube video size exceeds {self._max_filesize_bytes} bytes.")

    @staticmethod
    def _load_youtube_dl_factory() -> Callable[[dict[str, Any]], Any]:
        from yt_dlp import YoutubeDL

        return YoutubeDL


class InMemoryYoutubeDownloader:
    def __init__(
        self,
        initial_objects: dict[str, bytes] | None = None,
        *,
        default_content: bytes = b"video",
    ) -> None:
        self.objects = dict(initial_objects or {})
        self.default_content = default_content
        self.errors: dict[str, Exception] = {}
        self.downloads: list[tuple[str, Path]] = []

    async def download(self, source_url: str, destination: Path) -> Path:
        error = self.errors.get(source_url)
        if error is not None:
            raise error
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects.get(source_url, self.default_content))
        self.downloads.append((source_url, destination))
        return destination
