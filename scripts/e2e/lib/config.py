from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


class E2EConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    @classmethod
    def load(cls, path: Path | str) -> "E2EConfig":
        config_path = Path(path)
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"Config file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file is not valid JSON: {config_path}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Config root must be a JSON object.")
        return cls(payload)

    @property
    def values(self) -> dict[str, Any]:
        return dict(self._values)

    @property
    def gcp_project_id(self) -> str:
        return self.required_str("gcp.project_id")

    @property
    def region(self) -> str:
        return self.required_str("gcp.region")

    @property
    def user_id(self) -> str:
        return self.required_str("test_identity.user_id")

    @property
    def project_id(self) -> str:
        return self.required_str("test_identity.project_id")

    @property
    def jwt_secret_key(self) -> str:
        return self.required_str("auth.jwt_secret_key")

    def service_url(self, service_key: str) -> str:
        return self.required_str(f"services.{service_key}_url").rstrip("/")

    def service_name(self, service_key: str) -> str:
        return self.optional_str(f"services.{service_key}_name", service_key.replace("_", "-"))

    def queue_name(self, queue_key: str) -> str:
        defaults = {
            "dataset": "feedback.dataset",
            "training": "feedback.training",
            "rollback": "feedback.rollback.high",
            "reembedding": "feedback.reembedding",
        }
        return self.optional_str(f"queues.{queue_key}", defaults[queue_key])

    def timeout_seconds(self, timeout_key: str, default: float) -> float:
        raw_value = self.get(f"timeouts.{timeout_key}", default)
        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"timeouts.{timeout_key} must be a number.") from exc

    def required_str(self, dotted_path: str) -> str:
        value = self.get(dotted_path)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"Missing required config value: {dotted_path}")
        return value

    def optional_str(self, dotted_path: str, default: str) -> str:
        value = self.get(dotted_path, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ConfigError(f"{dotted_path} must be a string.")
        return value

    def get(self, dotted_path: str, default: Any = None) -> Any:
        current: Any = self._values
        for part in dotted_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def require_live_value(self, dotted_path: str) -> str:
        value = self.required_str(dotted_path)
        if value.startswith("REPLACE_ME"):
            raise ConfigError(f"Config value must be filled before live run: {dotted_path}")
        return value


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "gcp-perf.json"
