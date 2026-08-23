from pathlib import Path
from urllib.parse import quote

from src.infra.storage.client import MediaInput, StorageClient


class InMemoryStorageClient(StorageClient):
    def __init__(self, initial_objects: dict[str, bytes] | None = None, *, bucket_name: str = "test-bucket") -> None:
        self.objects: dict[str, bytes] = dict(initial_objects or {})
        self.deleted_paths: list[str] = []
        self.deleted_batches: list[list[str]] = []
        self.fail_delete_objects_once_for: set[str] = set()
        self.bucket_name = bucket_name

    def create_media_input(
        self,
        storage_path: str,
        *,
        expires_in_seconds: int,
        expected_generation: str | None = None,
    ) -> MediaInput:
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive")
        if storage_path not in self.objects:
            raise FileNotFoundError(storage_path)
        generation = "1"
        if expected_generation is not None and expected_generation != generation:
            raise RuntimeError("Normalization source generation changed during retry")
        encoded_path = quote(storage_path, safe="/")
        return MediaInput(
            url=f"https://storage.test/{self.bucket_name}/{encoded_path}",
            generation=generation,
        )

    async def download_object(self, storage_path: str, destination: Path) -> None:
        data = self.objects.get(storage_path)
        if data is None:
            raise FileNotFoundError(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    async def upload_object(self, source: Path, storage_path: str) -> None:
        self.objects[storage_path] = source.read_bytes()

    async def upload_object_if_absent(
        self,
        source: Path,
        storage_path: str,
    ) -> bool:
        if storage_path in self.objects:
            return False
        self.objects[storage_path] = source.read_bytes()
        return True

    async def object_exists(self, storage_path: str) -> bool:
        return storage_path in self.objects

    async def delete_object(self, storage_path: str) -> None:
        self.deleted_paths.append(storage_path)
        self.objects.pop(storage_path, None)

    async def delete_objects(self, storage_paths: list[str]) -> None:
        paths = list(dict.fromkeys(storage_paths))
        self.deleted_batches.append(paths)
        failing_paths = self.fail_delete_objects_once_for.intersection(paths)
        if failing_paths:
            self.fail_delete_objects_once_for.difference_update(failing_paths)
            raise RuntimeError(f"Simulated storage delete failure: {sorted(failing_paths)}")

        for storage_path in paths:
            self.deleted_paths.append(storage_path)
            self.objects.pop(storage_path, None)

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self.bucket_name}/{storage_path}"
