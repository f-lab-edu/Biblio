from pathlib import Path

from src.core.artifact_resolver import ModelArtifactResolver
from src.core.settings import Settings


class TestModelArtifactResolver:
    def test_resolves_model_version_under_artifact_root(self, tmp_path: Path):
        root = tmp_path / "models"
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(root / "bge-m3-20260526T143000KST"),
            MODEL_ARTIFACT_ROOT=str(root),
        )
        resolver = ModelArtifactResolver(settings)

        assert resolver.resolve("bge-m3-20260526T144000KST") == str(
            root / "bge-m3-20260526T144000KST"
        )

    def test_uses_model_artifact_path_when_root_is_missing_and_version_matches(
        self,
        tmp_path: Path,
    ):
        artifact_path = tmp_path / "models" / "bge-m3-20260526T143000KST"
        settings = Settings(MODEL_ARTIFACT_PATH=str(artifact_path))
        resolver = ModelArtifactResolver(settings)

        assert resolver.resolve("bge-m3-20260526T143000KST") == str(artifact_path)

    def test_falls_back_to_version_as_artifact_ref_when_no_root_or_matching_path(self):
        settings = Settings(MODEL_ARTIFACT_PATH="/models/bge-m3-20260526T143000KST")
        resolver = ModelArtifactResolver(settings)

        assert resolver.resolve("BAAI/bge-m3") == "BAAI/bge-m3"
