from src.release.reembed_text import embedding_input_text


def test_prefers_enriched_text():
    assert embedding_input_text("enriched", "raw") == "enriched"


def test_falls_back_to_raw_when_enriched_blank():
    assert embedding_input_text("   ", "raw") == "raw"
    assert embedding_input_text(None, "raw") == "raw"


def test_returns_none_when_both_blank():
    assert embedding_input_text(None, None) is None
    assert embedding_input_text("", "  ") is None
