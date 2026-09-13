"""Environment-backed configuration for Hikka Hub."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


@dataclass(frozen=True)
class Settings:
    bind: str
    port: int
    database: Path
    max_clock_skew: int
    rate_limit_per_minute: int
    request_body_limit: int
    event_retention_hours: int
    offline_after_seconds: int
    module_updates_enabled: bool = True
    module_update_interval: int = 300

    @classmethod
    def from_env(cls) -> "Settings":
        database = Path(os.getenv("HIKKA_HUB_DATABASE", "./data/hikka-hub.sqlite3"))
        return cls(
            bind=os.getenv("HIKKA_HUB_BIND", "0.0.0.0"),
            port=_integer("HIKKA_HUB_PORT", 8765, 1, 65535),
            database=database,
            max_clock_skew=_integer("HIKKA_HUB_MAX_CLOCK_SKEW", 90, 15, 600),
            rate_limit_per_minute=_integer("HIKKA_HUB_RATE_LIMIT", 120, 10, 10000),
            request_body_limit=_integer(
                "HIKKA_HUB_BODY_LIMIT", 131072, 4096, 2 * 1024 * 1024
            ),
            event_retention_hours=_integer(
                "HIKKA_HUB_EVENT_RETENTION_HOURS", 168, 1, 24 * 365
            ),
            offline_after_seconds=_integer(
                "HIKKA_HUB_OFFLINE_AFTER", 120, 30, 3600
            ),
            module_updates_enabled=_boolean(
                "HIKKA_HUB_MODULE_UPDATES", True
            ),
            module_update_interval=_integer(
                "HIKKA_HUB_MODULE_UPDATE_INTERVAL", 300, 60, 86400
            ),
        )
