from __future__ import annotations

import re

from infrastructure import LoadTestError


def duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m)", value)
    if not match:
        raise LoadTestError(f"Duration must use ms, s, or m units: {value}")
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0}[match.group(2)]
    seconds = float(match.group(1)) * multiplier
    if seconds <= 0:
        raise LoadTestError(f"Duration must be greater than zero: {value}")
    return seconds
