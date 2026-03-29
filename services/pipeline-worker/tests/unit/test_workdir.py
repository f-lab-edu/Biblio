import pytest

from src.utils.workdir import WorkdirManager


def test_temporary_creates_and_cleans(tmp_path) -> None:
    manager = WorkdirManager(base_dir=tmp_path)
    with manager.temporary("video-123") as workdir:
        assert workdir.exists()
        (workdir / "stage1.txt").write_text("ok")
    assert not workdir.exists()


def test_temporary_cleans_on_exception(tmp_path) -> None:
    manager = WorkdirManager(base_dir=tmp_path)
    bad_path = tmp_path / "pipeline_worker_workdirs" / "bad_id"
    with pytest.raises(RuntimeError):
        with manager.temporary("bad/id") as workdir:
            assert workdir.exists()
            raise RuntimeError("boom")
    assert not bad_path.exists()
