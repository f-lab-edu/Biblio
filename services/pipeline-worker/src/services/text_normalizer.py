import re
import unicodedata

_NON_BOUNDARY_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WHITESPACE = re.compile(r"[\t\r\n]+")
_MULTI_SPACE = re.compile(r" {2,}")


def normalize_enriched_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = _NON_BOUNDARY_CONTROL.sub("", normalized)
    normalized = _WHITESPACE.sub(" ", normalized)
    normalized = _MULTI_SPACE.sub(" ", normalized)
    return normalized.strip()
