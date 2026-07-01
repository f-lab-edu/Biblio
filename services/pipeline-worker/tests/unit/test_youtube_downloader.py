from pathlib import Path

import pytest

from src.infra.media.youtube_downloader import DownloadError, YtDlpYoutubeDownloader


class FakeYoutubeDL:
    calls: list[bool] = []
    metadata: dict[str, int] = {}

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, source_url: str, *, download: bool):
        self.calls.append(download)
        if download:
            Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
        return self.metadata


@pytest.fixture(autouse=True)
def reset_fake_youtube_dl() -> None:
    FakeYoutubeDL.calls = []
    FakeYoutubeDL.metadata = {}


def build_downloader() -> YtDlpYoutubeDownloader:
    return YtDlpYoutubeDownloader(
        max_duration_sec=1800,
        max_filesize_bytes=500,
        max_height=720,
        timeout_sec=600,
        youtube_dl_factory=FakeYoutubeDL,
    )


@pytest.mark.asyncio
async def test_youtube_downloader_rejects_duration_over_limit_without_download(tmp_path) -> None:
    FakeYoutubeDL.metadata = {"duration": 1801}
    downloader = build_downloader()

    with pytest.raises(DownloadError):
        await downloader.download("https://youtu.be/too-long", tmp_path / "source.mp4")

    assert FakeYoutubeDL.calls == [False]


@pytest.mark.asyncio
async def test_youtube_downloader_rejects_filesize_over_limit(tmp_path) -> None:
    FakeYoutubeDL.metadata = {"duration": 30, "filesize": 501}
    downloader = build_downloader()

    with pytest.raises(DownloadError):
        await downloader.download("https://youtu.be/too-large", tmp_path / "source.mp4")

    assert FakeYoutubeDL.calls == [False]


@pytest.mark.asyncio
async def test_youtube_downloader_writes_requested_mp4_path(tmp_path) -> None:
    FakeYoutubeDL.metadata = {"duration": 30, "filesize": 499}
    destination = tmp_path / "source.mp4"
    downloader = build_downloader()

    result = await downloader.download("https://youtu.be/ok", destination)

    assert result == destination
    assert destination.read_bytes() == b"video"
    assert FakeYoutubeDL.calls == [False, True]
