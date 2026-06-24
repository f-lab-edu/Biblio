from __future__ import annotations


def embedding_input_text(enriched_text: str | None, text: str | None) -> str | None:
    if enriched_text is not None and enriched_text.strip():
        return enriched_text
    if text is not None and text.strip():
        return text
    return None
