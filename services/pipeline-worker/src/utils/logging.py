import sys
from collections.abc import MutableMapping
from typing import Any

from loguru import logger

_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | trace_id={extra[trace_id]} "
    "video_id={extra[video_id]} user_id={extra[user_id]} | {message}"
)


def _patch_record(record: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    extras = record.setdefault("extra", {})
    extras.setdefault("trace_id", "-")
    extras.setdefault("video_id", "-")
    extras.setdefault("user_id", "-")
    return record


def configure_logging() -> None:
    logger.remove()
    logger.configure(patcher=_patch_record)
    logger.add(sys.stderr, format=_LOG_FORMAT, enqueue=False, backtrace=False, diagnose=False)


def get_logger():
    return logger
