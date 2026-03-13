from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class CursorDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KeysetCursor:
    created_at: datetime
    id: UUID


def encode_cursor(cursor: KeysetCursor) -> str:
    payload = json.dumps(
        {
            "created_at": cursor.created_at.isoformat(),
            "id": str(cursor.id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def decode_cursor(token: str) -> KeysetCursor:
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(f"{token}{padding}".encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        created_at = datetime.fromisoformat(payload["created_at"])
        cursor_id = UUID(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CursorDecodeError("Invalid cursor token.") from exc

    if created_at.tzinfo is None:
        raise CursorDecodeError("Cursor created_at must include timezone information.")

    return KeysetCursor(created_at=created_at, id=cursor_id)
