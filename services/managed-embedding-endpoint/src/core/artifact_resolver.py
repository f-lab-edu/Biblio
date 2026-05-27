from pathlib import Path

from src.core.settings import Settings


class ModelArtifactResolver:
    """Resolve model versions to loadable artifact refs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, model_version: str) -> str:
        if self._settings.model_artifact_root:
            return str(Path(self._settings.model_artifact_root) / model_version)

        artifact_path = self._settings.model_artifact_path
        if Path(artifact_path).name == model_version:
            return artifact_path

        return model_version
