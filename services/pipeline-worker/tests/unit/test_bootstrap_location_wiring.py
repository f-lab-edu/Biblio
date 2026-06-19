from pathlib import Path


def test_bootstrap_wires_independent_stt_and_vision_locations() -> None:
    bootstrap_source = (
        Path(__file__).resolve().parents[2] / "src" / "bootstrap.py"
    ).read_text(encoding="utf-8")

    assert "location=settings.stt_location" in bootstrap_source
    assert "location=settings.vision_location" in bootstrap_source
    assert "location=settings.gcp_location" not in bootstrap_source
