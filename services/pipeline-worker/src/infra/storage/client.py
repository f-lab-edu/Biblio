from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MediaInput:
    url: str
    generation: str


class StorageClient(Protocol):
    def create_media_input(
        self,
        storage_path: str,
        *,
        expires_in_seconds: int,
        expected_generation: str | None = None,
    ) -> MediaInput: ...

    async def download_object(self, storage_path: str, destination: Path) -> None: ...

    async def upload_object(self, source: Path, storage_path: str) -> None: ...

    async def upload_object_if_absent(
        self,
        source: Path,
        storage_path: str,
    ) -> bool: ...

    async def object_exists(self, storage_path: str) -> bool: ...

    async def delete_object(self, storage_path: str) -> None: ...

    async def delete_objects(self, storage_paths: list[str]) -> None: ...

    def object_uri(self, storage_path: str) -> str: ...
