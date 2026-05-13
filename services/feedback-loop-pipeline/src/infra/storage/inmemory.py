from pathlib import Path
from typing import Sequence

from src.infra.storage.client import ArtifactStore


class InMemoryArtifactStore(ArtifactStore):
    def __init__(self, initial_objects: dict[str, bytes] | None = None, *, bucket_name: str = "test-bucket") -> None:
        self.objects: dict[str, bytes] = dict(initial_objects or {})
        self.bucket_name = bucket_name

    async def list_objects(self, prefix: str) -> Sequence[str]:
        return sorted(path for path in self.objects if path.startswith(prefix))

    async def download_object(self, storage_path: str, destination: Path) -> None:
        data = self.objects.get(storage_path)
        if data is None:
            raise FileNotFoundError(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    async def upload_object(self, source: Path, storage_path: str) -> None:
        self.objects[storage_path] = source.read_bytes()

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self.bucket_name}/{storage_path}"
