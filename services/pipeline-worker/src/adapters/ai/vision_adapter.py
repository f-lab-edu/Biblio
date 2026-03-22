import asyncio
from dataclasses import dataclass
from typing import Protocol

from loguru import logger


@dataclass(slots=True)
class VisionResult:
    visual_caption: str = ""
    ocr_text: str = ""
    scene_tags: str = ""


class VisionAdapter(Protocol):
    async def extract_caption(self, keyframe_path: str, *, trace_id: str) -> str: ...

    async def extract_ocr(self, keyframe_path: str, *, trace_id: str) -> str: ...

    async def extract_scene_tags(self, keyframe_path: str, *, trace_id: str) -> str: ...


class MockVisionAdapter:
    def __init__(
        self,
        *,
        caption: str = "",
        ocr_text: str = "",
        scene_tags: str = "",
        fail_times: int = 0,
    ) -> None:
        self.caption = caption
        self.ocr_text = ocr_text
        self.scene_tags = scene_tags
        self.fail_times = fail_times
        self.calls = 0

    async def extract_caption(self, keyframe_path: str, *, trace_id: str) -> str:
        self.calls += 1
        self._maybe_fail()
        return self.caption

    async def extract_ocr(self, keyframe_path: str, *, trace_id: str) -> str:
        self._maybe_fail()
        return self.ocr_text

    async def extract_scene_tags(self, keyframe_path: str, *, trace_id: str) -> str:
        self._maybe_fail()
        return self.scene_tags

    def _maybe_fail(self) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("vision failure")


async def extract_with_fallback(
    adapter: VisionAdapter,
    *,
    keyframe_path: str,
    trace_id: str,
    max_retries: int = 2,
) -> VisionResult:
    for attempt in range(max_retries + 1):
        try:
            visual_caption, ocr_text, scene_tags = await asyncio.gather(
                adapter.extract_caption(keyframe_path, trace_id=trace_id),
                adapter.extract_ocr(keyframe_path, trace_id=trace_id),
                adapter.extract_scene_tags(keyframe_path, trace_id=trace_id),
            )
            return VisionResult(
                visual_caption=visual_caption,
                ocr_text=ocr_text,
                scene_tags=scene_tags,
            )
        except Exception:
            logger.bind(trace_id=trace_id, keyframe_path=keyframe_path).warning(
                "Vision extraction failed on attempt {}/{} for {}",
                attempt + 1,
                max_retries + 1,
                keyframe_path,
            )
            if attempt >= max_retries:
                return VisionResult()
            await asyncio.sleep(0)
    return VisionResult()
