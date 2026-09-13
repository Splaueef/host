"""SQLite storage. All writes are serialized and SQLite runs outside the event loop."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS instances (
    key_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    derived_key_hex TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_seen INTEGER,
    remote_ip TEXT,
    hikka_version TEXT,
    module_version TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS request_nonces (
    key_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (key_id, nonce),
    FOREIGN KEY (key_id) REFERENCES instances(key_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nonces_expiry ON request_nonces(expires_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    sender_instance_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic_id ON events(topic, id);
CREATE INDEX IF NOT EXISTS idx_events_expiry ON events(expires_at);

CREATE TABLE IF NOT EXISTS kv_items (
    namespace TEXT NOT NULL,
    item_key TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    value_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER,
    PRIMARY KEY (namespace, item_key)
);
CREATE INDEX IF NOT EXISTS idx_kv_expiry ON kv_items(expires_at);

CREATE TABLE IF NOT EXISTS metrics (
    instance_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (instance_id, metric)
);

CREATE TABLE IF NOT EXISTS daily_usage (
    day TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    heartbeats INTEGER NOT NULL DEFAULT 0,
    events INTEGER NOT NULL DEFAULT 0,
    kv_writes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, instance_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    action TEXT NOT NULL,
    instance_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        with contextlib.suppress(PermissionError):
            os.chmod(self.path, 0o600)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._db.close)

    async def _run(self, operation: Callable[[], Any]) -> Any:
        async with self._lock:
            return await asyncio.to_thread(operation)

    async def get_credential(self, key_id: str) -> Optional[dict[str, Any]]:
        def operation():
            row = self._db.execute(
                "SELECT * FROM instances WHERE key_id = ?", (key_id,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(operation)

    async def reserve_nonce(self, key_id: str, nonce: str, expires_at: int) -> bool:
        now = int(time.time())

        def operation():
            self._db.execute("DELETE FROM request_nonces WHERE expires_at < ?", (now,))
            try:
                self._db.execute(
                    "INSERT INTO request_nonces(key_id, nonce, expires_at) VALUES (?, ?, ?)",
                    (key_id, nonce, expires_at),
                )
            except sqlite3.IntegrityError:
                return False
            return True

        return await self._run(operation)

    async def touch_instance(
        self,
        instance_id: str,
        remote_ip: str,
        hikka_version: str = "",
        module_version: str = "",
        capabilities: Optional[list[str]] = None,
        display_name: str = "",
    ) -> None:
        now = int(time.time())
        capabilities_json = json.dumps(capabilities or [], separators=(",", ":"))

        def operation():
            if display_name:
                self._db.execute(
                    """
                    UPDATE instances SET last_seen=?, remote_ip=?, hikka_version=?,
                        module_version=?, capabilities_json=?, display_name=?, updated_at=?
                    WHERE instance_id=?
                    """,
                    (
                        now,
                        remote_ip,
                        hikka_version,
                        module_version,
                        capabilities_json,
                        display_name,
                        now,
                        instance_id,
                    ),
                )
            else:
                self._db.execute(
                    """
                    UPDATE instances SET last_seen=?, remote_ip=?, hikka_version=?,
                        module_version=?, capabilities_json=?, updated_at=?
                    WHERE instance_id=?
                    """,
                    (
                        now,
                        remote_ip,
                        hikka_version,
                        module_version,
                        capabilities_json,
                        now,
                        instance_id,
                    ),
                )

        await self._run(operation)

    async def list_instances(self, active_within: int) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            rows = self._db.execute(
                """
                SELECT instance_id, display_name, last_seen, hikka_version,
                       module_version, capabilities_json
                FROM instances WHERE enabled=1 ORDER BY COALESCE(last_seen, 0) DESC
                """
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["capabilities"] = json.loads(item.pop("capabilities_json") or "[]")
                item["online"] = bool(
                    item["last_seen"] and now - int(item["last_seen"]) <= active_within
                )
                result.append(item)
            return result

        return await self._run(operation)

    async def add_event(
        self,
        topic: str,
        sender_instance_id: str,
        payload: Any,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        now = int(time.time())
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        def operation():
            cursor = self._db.execute(
                """
                INSERT INTO events(topic, sender_instance_id, payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (topic, sender_instance_id, payload_json, now, now + ttl_seconds),
            )
            return {"id": cursor.lastrowid, "created_at": now, "expires_at": now + ttl_seconds}

        return await self._run(operation)

    async def list_events(
        self, after_id: int, topic: Optional[str], limit: int
    ) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            self._db.execute("DELETE FROM events WHERE expires_at < ?", (now,))
            if topic:
                rows = self._db.execute(
                    """
                    SELECT id, topic, sender_instance_id, payload_json, created_at, expires_at
                    FROM events WHERE id > ? AND topic=? AND expires_at >= ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (after_id, topic, now, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    """
                    SELECT id, topic, sender_instance_id, payload_json, created_at, expires_at
                    FROM events WHERE id > ? AND expires_at >= ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (after_id, now, limit),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

        return await self._run(operation)

    async def put_item(
        self,
        namespace: str,
        item_key: str,
        owner_instance_id: str,
        value: Any,
        ttl_seconds: Optional[int],
        expected_revision: Optional[int],
    ) -> dict[str, Any]:
        now = int(time.time())
        expires_at = now + ttl_seconds if ttl_seconds else None
        value_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        def operation():
            self._db.execute(
                "DELETE FROM kv_items WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
            )
            current = self._db.execute(
                "SELECT owner_instance_id, revision FROM kv_items WHERE namespace=? AND item_key=?",
                (namespace, item_key),
            ).fetchone()
            if current and current["owner_instance_id"] != owner_instance_id:
                return {"error": "not_owner"}
            if expected_revision is not None:
                actual = int(current["revision"]) if current else 0
                if actual != expected_revision:
                    return {"error": "revision_conflict", "revision": actual}
            revision = int(current["revision"]) + 1 if current else 1
            self._db.execute(
                """
                INSERT INTO kv_items(namespace, item_key, owner_instance_id, value_json,
                                     revision, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, item_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (
                    namespace,
                    item_key,
                    owner_instance_id,
                    value_json,
                    revision,
                    now,
                    expires_at,
                ),
            )
            return {"revision": revision, "updated_at": now, "expires_at": expires_at}

        return await self._run(operation)

    async def get_item(self, namespace: str, item_key: str) -> Optional[dict[str, Any]]:
        now = int(time.time())

        def operation():
            row = self._db.execute(
                """
                SELECT namespace, item_key, owner_instance_id, value_json, revision,
                       updated_at, expires_at
                FROM kv_items WHERE namespace=? AND item_key=?
                  AND (expires_at IS NULL OR expires_at >= ?)
                """,
                (namespace, item_key, now),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["value"] = json.loads(item.pop("value_json"))
            return item

        return await self._run(operation)

    async def list_items(
        self, namespace: str, prefix: str, limit: int
    ) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            rows = self._db.execute(
                """
                SELECT namespace, item_key, owner_instance_id, value_json, revision,
                       updated_at, expires_at
                FROM kv_items WHERE namespace=? AND item_key LIKE ? ESCAPE '\\'
                  AND (expires_at IS NULL OR expires_at >= ?)
                ORDER BY item_key LIMIT ?
                """,
                (namespace, _like_prefix(prefix), now, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["value"] = json.loads(item.pop("value_json"))
                result.append(item)
            return result

        return await self._run(operation)

    async def delete_item(
        self, namespace: str, item_key: str, owner_instance_id: str
    ) -> str:
        def operation():
            row = self._db.execute(
                "SELECT owner_instance_id FROM kv_items WHERE namespace=? AND item_key=?",
                (namespace, item_key),
            ).fetchone()
            if not row:
                return "missing"
            if row["owner_instance_id"] != owner_instance_id:
                return "not_owner"
            self._db.execute(
                "DELETE FROM kv_items WHERE namespace=? AND item_key=?",
                (namespace, item_key),
            )
            return "deleted"

        return await self._run(operation)

    async def increment_metric(
        self, instance_id: str, metric: str, delta: float
    ) -> dict[str, Any]:
        now = int(time.time())

        def operation():
            self._db.execute(
                """
                INSERT INTO metrics(instance_id, metric, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, metric) DO UPDATE SET
                    value=value + excluded.value,
                    updated_at=excluded.updated_at
                """,
                (instance_id, metric, delta, now),
            )
            row = self._db.execute(
                "SELECT value, updated_at FROM metrics WHERE instance_id=? AND metric=?",
                (instance_id, metric),
            ).fetchone()
            return dict(row)

        return await self._run(operation)

    async def metric_ranking(self, metric: str, limit: int) -> dict[str, Any]:
        def operation():
            rows = self._db.execute(
                """
                SELECT m.instance_id, i.display_name, m.value, m.updated_at
                FROM metrics m JOIN instances i ON i.instance_id=m.instance_id
                WHERE m.metric=? AND i.enabled=1
                ORDER BY m.value DESC LIMIT ?
                """,
                (metric, limit),
            ).fetchall()
            total = self._db.execute(
                "SELECT COALESCE(SUM(value), 0) FROM metrics WHERE metric=?", (metric,)
            ).fetchone()[0]
            return {"metric": metric, "total": total, "ranking": [dict(row) for row in rows]}

        return await self._run(operation)

    async def record_usage(self, instance_id: str, field: str) -> None:
        if field not in {"requests", "heartbeats", "events", "kv_writes"}:
            raise ValueError("unsupported usage field")
        day = time.strftime("%Y-%m-%d", time.gmtime())

        def operation():
            self._db.execute(
                f"""
                INSERT INTO daily_usage(day, instance_id, {field}) VALUES (?, ?, 1)
                ON CONFLICT(day, instance_id) DO UPDATE SET {field}={field}+1
                """,
                (day, instance_id),
            )

        await self._run(operation)

    async def overview(self, offline_after: int) -> dict[str, Any]:
        now = int(time.time())
        since = now - 86400
        day = time.strftime("%Y-%m-%d", time.gmtime(now))

        def operation():
            enabled = self._db.execute(
                "SELECT COUNT(*) FROM instances WHERE enabled=1"
            ).fetchone()[0]
            online = self._db.execute(
                "SELECT COUNT(*) FROM instances WHERE enabled=1 AND last_seen>=?",
                (now - offline_after,),
            ).fetchone()[0]
            events_24h = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE created_at>=?", (since,)
            ).fetchone()[0]
            usage = self._db.execute(
                """
                SELECT COALESCE(SUM(requests),0), COALESCE(SUM(heartbeats),0),
                       COALESCE(SUM(events),0), COALESCE(SUM(kv_writes),0)
                FROM daily_usage WHERE day=?
                """,
                (day,),
            ).fetchone()
            return {
                "instances": {"total": enabled, "online": online},
                "events_24h": events_24h,
                "today": {
                    "requests": usage[0],
                    "heartbeats": usage[1],
                    "events": usage[2],
                    "kv_writes": usage[3],
                },
                "server_time": now,
            }

        return await self._run(operation)

    async def purge_expired(self) -> dict[str, int]:
        now = int(time.time())

        def operation():
            nonce_cursor = self._db.execute(
                "DELETE FROM request_nonces WHERE expires_at < ?", (now,)
            )
            event_cursor = self._db.execute("DELETE FROM events WHERE expires_at < ?", (now,))
            kv_cursor = self._db.execute(
                "DELETE FROM kv_items WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
            )
            return {
                "nonces": nonce_cursor.rowcount,
                "events": event_cursor.rowcount,
                "kv_items": kv_cursor.rowcount,
            }

        return await self._run(operation)


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
