from pathlib import Path

import pytest

from src.infra.media.youtube_downloader import DownloadError, YtDlpYoutubeDownloader


class FakeYoutubeDL:
    calls: list[bool] = []
    metadata: dict[str, int] = {}
    options: list[dict] = []
    error: Exception | None = None

    def __init__(self, options):
        self.options = options
        self.__class__.options.append(options)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, source_url: str, *, download: bool):
        self.calls.append(download)
        if self.error is not None:
            raise self.error
        if download:
            Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
        return self.metadata


@pytest.fixture(autouse=True)
def reset_fake_youtube_dl() -> None:
    FakeYoutubeDL.calls = []
    FakeYoutubeDL.metadata = {}
    FakeYoutubeDL.options = []
    FakeYoutubeDL.error = None


def build_downloader(*, proxy_url: str = "") -> YtDlpYoutubeDownloader:
    return YtDlpYoutubeDownloader(
        max_duration_sec=1800,
        max_filesize_bytes=500,
        max_height=720,
        timeout_sec=600,
        proxy_url=proxy_url,
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


@pytest.mark.asyncio
async def test_youtube_downloader_omits_proxy_from_options_when_unset(tmp_path) -> None:
    FakeYoutubeDL.metadata = {"duration": 30, "filesize": 499}
    downloader = build_downloader()

    await downloader.download("https://youtu.be/direct", tmp_path / "source.mp4")

    assert FakeYoutubeDL.options[0] == {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 600,
    }
    assert "proxy" not in FakeYoutubeDL.options[1]


@pytest.mark.asyncio
async def test_youtube_downloader_adds_proxy_to_metadata_and_download_options(tmp_path) -> None:
    FakeYoutubeDL.metadata = {"duration": 30, "filesize": 499}
    proxy_url = "http://proxy.test:8080"
    downloader = build_downloader(proxy_url=proxy_url)

    await downloader.download("https://youtu.be/proxied", tmp_path / "source.mp4")

    assert [options["proxy"] for options in FakeYoutubeDL.options] == [proxy_url, proxy_url]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_category"),
    [
        ("Unable to download webpage: ProxyError: tunnel connection failed", "proxy_error"),
        ("Sign in to confirm you're not a bot", "youtube_block"),
        ("Video unavailable: This video is private", "video_unavailable"),
        ("Unexpected extractor failure", "unknown"),
    ],
)
async def test_youtube_downloader_classifies_failures(message, expected_category, tmp_path) -> None:
    FakeYoutubeDL.error = RuntimeError(message)
    downloader = build_downloader(proxy_url="http://proxy.test:8080")

    with pytest.raises(DownloadError) as exc_info:
        await downloader.download("https://youtu.be/failure", tmp_path / "source.mp4")

    assert exc_info.value.category == expected_category
    assert str(exc_info.value) == message
    assert FakeYoutubeDL.calls == [False]
