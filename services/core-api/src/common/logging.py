import json
import sys
from datetime import datetime, timezone
from typing import Any

from loguru import logger

_CONFIGURED = False


def _ensure_configured() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    logger.remove()
    logger.add(
        sys.stderr,
        format="{message}",
        backtrace=False,
        diagnose=False,
    )
    _CONFIGURED = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_level(level: str | int) -> str:
    if isinstance(level, str):
        return level.upper()

    if level >= 40:
        return "ERROR"
    if level >= 30:
        return "WARNING"
    return "INFO"


def log(level: str | int, message: str, **fields: Any) -> None:
    """Emit a structured JSON line.

    Kept minimal. Common fields like trace_id/user_id/video_id
    can be passed via kwargs.
    """
    _ensure_configured()
    record = {"ts": _now_iso(), "level": _normalize_level(level), "msg": message}
    if fields:
        record.update({k: v for k, v in fields.items() if v is not None})
    logger.bind(**{k: v for k, v in fields.items() if v is not None}).log(
        record["level"],
        json.dumps(record, separators=(",", ":")),
    )


def info(message: str, **fields: Any) -> None:  # pragma: no cover - thin wrapper
    log("INFO", message, **fields)


def warning(message: str, **fields: Any) -> None:  # pragma: no cover
    log("WARNING", message, **fields)


def error(message: str, **fields: Any) -> None:  # pragma: no cover
    log("ERROR", message, **fields)

