from services.text_normalizer import normalize_enriched_text


def test_normalize_enriched_text_collapses_whitespace() -> None:
    assert normalize_enriched_text(" Alpha\tBeta\nGamma  ") == "Alpha Beta Gamma"


def test_normalize_enriched_text_drops_control_characters() -> None:
    assert normalize_enriched_text("Al\x01pha") == "Alpha"
