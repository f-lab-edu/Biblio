from __future__ import annotations

import re


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]
