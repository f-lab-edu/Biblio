import threading


class AdmissionController:
    """Fail-fast concurrency gate checked before offloading inference work."""

    def __init__(self, max_concurrency: int) -> None:
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()
