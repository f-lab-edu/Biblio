from pathlib import Path
from typing import Protocol


class StorageClient(Protocol):
    async def download_object(self, storage_path: str, destination: Path) -> None: ...

    async def upload_object(self, source: Path, storage_path: str) -> None: ...

    async def delete_object(self, storage_path: str) -> None: ...
