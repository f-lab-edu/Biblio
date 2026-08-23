import pytest

from src.infra.ai.vision_adapter import MockVisionAdapter, extract_with_fallback


@pytest.mark.asyncio
async def test_vision_adapter_returns_fields() -> None:
    adapter = MockVisionAdapter(caption="caption", ocr_text="ocr", scene_tags="scene")

    result = await extract_with_fallback(adapter, keyframe_path="frame.jpg", trace_id="trace-1")

    assert result.visual_caption == "caption"
    assert result.ocr_text == "ocr"
    assert result.scene_tags == "scene"


@pytest.mark.asyncio
async def test_vision_adapter_falls_back_after_retries() -> None:
    adapter = MockVisionAdapter(fail_times=10)

    result = await extract_with_fallback(adapter, keyframe_path="frame.jpg", trace_id="trace-2", max_retries=1)

    assert result.visual_caption == ""
    assert result.ocr_text == ""
    assert result.scene_tags == ""


@pytest.mark.asyncio
async def test_vision_adapter_can_raise_after_retries() -> None:
    adapter = MockVisionAdapter(fail_times=10)

    with pytest.raises(RuntimeError, match="vision failure"):
        await extract_with_fallback(
            adapter,
            keyframe_path="frame.jpg",
            trace_id="trace-3",
            max_retries=1,
            raise_on_exhaustion=True,
        )

