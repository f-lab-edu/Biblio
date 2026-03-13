from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


_LOGGER = logging.getLogger("coreapi")


def _ensure_configured() -> None:
    # Configure root once if not already configured (safe in tests)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(level: int, message: str, **fields: Any) -> None:
    """Emit a structured JSON line.

    Kept minimal and stdlib-only. Common fields like trace_id/user_id/video_id
    can be passed via kwargs.
    """
    _ensure_configured()
    record = {"ts": _now_iso(), "level": logging.getLevelName(level), "msg": message}
    if fields:
        record.update({k: v for k, v in fields.items() if v is not None})
    _LOGGER.log(level, json.dumps(record, separators=(",", ":")))


def info(message: str, **fields: Any) -> None:  # pragma: no cover - thin wrapper
    log(logging.INFO, message, **fields)


def warning(message: str, **fields: Any) -> None:  # pragma: no cover
    log(logging.WARNING, message, **fields)


def error(message: str, **fields: Any) -> None:  # pragma: no cover
    log(logging.ERROR, message, **fields)

