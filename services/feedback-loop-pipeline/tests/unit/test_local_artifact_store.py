from pathlib import Path

import pytest

from src.infra.storage.local import LocalArtifactStore


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.asyncio
async def test_local_artifact_store_copy_prefix_preserves_relative_paths(tmp_path) -> None:
    _write_file(tmp_path / "models/active/config.json", b"config")
    _write_file(tmp_path / "models/active/nested/model.bin", b"model")
    _write_file(tmp_path / "models/active-extra/ignored.txt", b"ignored")
    store = LocalArtifactStore(root_dir=tmp_path)

    await store.copy_prefix("models/active", "models/candidate")

    assert (tmp_path / "models/candidate/config.json").read_bytes() == b"config"
    assert (tmp_path / "models/candidate/nested/model.bin").read_bytes() == b"model"
    assert not (tmp_path / "models/candidate/ignored.txt").exists()


@pytest.mark.asyncio
async def test_local_artifact_store_copy_prefix_fails_when_source_is_empty(tmp_path) -> None:
    store = LocalArtifactStore(root_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        await store.copy_prefix("models/missing/", "models/candidate/")


@pytest.mark.asyncio
async def test_local_artifact_store_copy_prefix_fails_when_target_exists(tmp_path) -> None:
    _write_file(tmp_path / "models/active/config.json", b"config")
    _write_file(tmp_path / "models/candidate/config.json", b"existing")
    store = LocalArtifactStore(root_dir=tmp_path)

    with pytest.raises(FileExistsError):
        await store.copy_prefix("models/active/", "models/candidate/")

    assert (tmp_path / "models/candidate/config.json").read_bytes() == b"existing"


@pytest.mark.asyncio
async def test_local_artifact_store_copy_prefix_rejects_target_outside_root(tmp_path) -> None:
    _write_file(tmp_path / "models/active/config.json", b"config")
    store = LocalArtifactStore(root_dir=tmp_path)

    with pytest.raises(ValueError):
        await store.copy_prefix("models/active/", "../candidate/")
