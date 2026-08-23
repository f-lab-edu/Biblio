import json
import sys
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from uuid import UUID

from loguru import logger

def _patch_record(record: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    extras = record.setdefault("extra", {})
    extras.setdefault("log_schema_version", 1)
    extras.setdefault("trace_id", "-")
    extras.setdefault("video_id", "-")
    extras.setdefault("user_id", "-")
    return record


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, UUID)):
        return str(value)
    return str(value)


def _write_json(message: Any) -> None:
    record = message.record
    extras = dict(record["extra"])
    payload = {
        "log_schema_version": extras.pop("log_schema_version", 1),
        "timestamp_utc": extras.pop(
            "timestamp_utc",
            record["time"].isoformat(),
        ),
        "severity": record["level"].name,
        "message": record["message"],
        **extras,
    }
    if record["exception"] is not None:
        payload["exception"] = str(record["exception"])
    sys.stderr.write(
        json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n"
    )


def configure_logging() -> None:
    logger.remove()
    logger.configure(patcher=_patch_record)
    logger.add(_write_json, enqueue=False, backtrace=False, diagnose=False)


def get_logger():
    return logger
