"""SQLite storage. All writes are serialized and SQLite runs outside the event loop."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .game_engine import apply_action, new_state, resign_state


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

CREATE TABLE IF NOT EXISTS service_state (
    state_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS game_sessions (
    game_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    creator_instance_id TEXT NOT NULL,
    opponent_instance_id TEXT,
    player0_owner_id INTEGER NOT NULL,
    player1_owner_id INTEGER,
    player0_name TEXT NOT NULL,
    player1_name TEXT,
    state_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_status_updated
    ON game_sessions(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_creator_updated
    ON game_sessions(creator_instance_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_opponent_updated
    ON game_sessions(opponent_instance_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_expiry ON game_sessions(expires_at);

CREATE TABLE IF NOT EXISTS game_players (
    owner_id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS game_stats (
    owner_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    played INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    draws INTEGER NOT NULL DEFAULT 0,
    rating REAL NOT NULL DEFAULT 1000,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (owner_id, kind),
    FOREIGN KEY (owner_id) REFERENCES game_players(owner_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_game_stats_kind_rating
    ON game_stats(kind, rating DESC);

CREATE TABLE IF NOT EXISTS chat_presence (
    room TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    nickname TEXT NOT NULL,
    last_seen INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (room, instance_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_presence_expiry ON chat_presence(expires_at);
CREATE INDEX IF NOT EXISTS idx_chat_presence_room_seen
    ON chat_presence(room, last_seen DESC);
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

    async def get_service_state(self, state_key: str) -> Optional[dict[str, Any]]:
        def operation():
            row = self._db.execute(
                "SELECT value_json, updated_at FROM service_state WHERE state_key=?",
                (state_key,),
            ).fetchone()
            if not row:
                return None
            return {
                "value": json.loads(row["value_json"]),
                "updated_at": int(row["updated_at"]),
            }

        return await self._run(operation)

    async def set_service_state(self, state_key: str, value: Any) -> dict[str, int]:
        now = int(time.time())
        value_json = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )

        def operation():
            self._db.execute(
                """
                INSERT INTO service_state(state_key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (state_key, value_json, now),
            )
            return {"updated_at": now}

        return await self._run(operation)

    async def list_events(
        self,
        after_id: int,
        topic: Optional[str],
        limit: int,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            self._db.execute("DELETE FROM events WHERE expires_at < ?", (now,))
            if latest:
                if topic:
                    rows = self._db.execute(
                        """
                        SELECT id, topic, sender_instance_id, payload_json,
                               created_at, expires_at
                        FROM events WHERE topic=? AND expires_at >= ?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (topic, now, limit),
                    ).fetchall()
                else:
                    rows = self._db.execute(
                        """
                        SELECT id, topic, sender_instance_id, payload_json,
                               created_at, expires_at
                        FROM events WHERE expires_at >= ?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (now, limit),
                    ).fetchall()
                rows = list(reversed(rows))
            elif topic:
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

    async def increment_metrics(
        self, instance_id: str, metrics: dict[str, float]
    ) -> dict[str, dict[str, Any]]:
        """Increment several metrics atomically in one SQLite transaction."""
        now = int(time.time())

        def operation():
            result = {}
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for metric, delta in metrics.items():
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
                placeholders = ",".join("?" for _ in metrics)
                rows = self._db.execute(
                    f"""
                    SELECT metric, value, updated_at FROM metrics
                    WHERE instance_id=? AND metric IN ({placeholders})
                    """,
                    (instance_id, *metrics),
                ).fetchall()
                result = {
                    row["metric"]: {
                        "value": row["value"],
                        "updated_at": row["updated_at"],
                    }
                    for row in rows
                }
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return result

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

    @staticmethod
    def _game_payload(row: sqlite3.Row, viewer_instance_id: str = "") -> dict[str, Any]:
        state = json.loads(row["state_json"])
        players = [
            {
                "slot": 0,
                "instance_id": row["creator_instance_id"],
                "display_name": row["player0_name"],
            }
        ]
        if row["opponent_instance_id"]:
            players.append(
                {
                    "slot": 1,
                    "instance_id": row["opponent_instance_id"],
                    "display_name": row["player1_name"],
                }
            )
        my_slot = None
        if viewer_instance_id == row["creator_instance_id"]:
            my_slot = 0
        elif viewer_instance_id and viewer_instance_id == row["opponent_instance_id"]:
            my_slot = 1
        return {
            "game_id": row["game_id"],
            "kind": row["kind"],
            "status": row["status"],
            "players": players,
            "my_slot": my_slot,
            "state": state,
            "revision": int(row["revision"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "expires_at": int(row["expires_at"]),
        }

    async def create_game(
        self,
        kind: str,
        credential: dict[str, Any],
        opponent_instance_id: Optional[str],
        wait_ttl_seconds: int,
    ) -> dict[str, Any]:
        now = int(time.time())
        game_id = "ng_" + secrets.token_hex(8)

        def operation():
            opponent = None
            if opponent_instance_id:
                opponent = self._db.execute(
                    """
                    SELECT instance_id, owner_id, display_name FROM instances
                    WHERE instance_id=? AND enabled=1
                    """,
                    (opponent_instance_id,),
                ).fetchone()
                if not opponent:
                    return {"error": "opponent_not_found"}
                if (
                    opponent["instance_id"] == credential["instance_id"]
                    or int(opponent["owner_id"]) == int(credential["owner_id"])
                ):
                    return {"error": "self_game"}
            status = "invited" if opponent else "waiting"
            state = new_state(kind)
            self._db.execute(
                """
                INSERT INTO game_sessions(
                    game_id, kind, status, creator_instance_id,
                    opponent_instance_id, player0_owner_id, player1_owner_id,
                    player0_name, player1_name, state_json, revision,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    game_id,
                    kind,
                    status,
                    credential["instance_id"],
                    opponent["instance_id"] if opponent else None,
                    int(credential["owner_id"]),
                    int(opponent["owner_id"]) if opponent else None,
                    str(credential["display_name"]),
                    str(opponent["display_name"]) if opponent else None,
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                    now + int(wait_ttl_seconds),
                ),
            )
            row = self._db.execute(
                "SELECT * FROM game_sessions WHERE game_id=?", (game_id,)
            ).fetchone()
            return self._game_payload(row, credential["instance_id"])

        return await self._run(operation)

    async def list_games(
        self,
        viewer_instance_id: str,
        scope: str,
        kind: Optional[str],
        status: Optional[str],
        updated_after: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            self._db.execute("DELETE FROM game_sessions WHERE expires_at < ?", (now,))
            clauses = ["updated_at >= ?"]
            parameters: list[Any] = [int(updated_after)]
            if scope == "mine":
                clauses.append("(creator_instance_id=? OR opponent_instance_id=?)")
                parameters.extend([viewer_instance_id, viewer_instance_id])
            elif scope == "open":
                clauses.extend(
                    [
                        "status='waiting'",
                        "opponent_instance_id IS NULL",
                        "creator_instance_id<>?",
                    ]
                )
                parameters.append(viewer_instance_id)
            else:
                clauses.append(
                    "((creator_instance_id=? OR opponent_instance_id=?) OR "
                    "(status='waiting' AND opponent_instance_id IS NULL "
                    "AND creator_instance_id<>?))"
                )
                parameters.extend(
                    [viewer_instance_id, viewer_instance_id, viewer_instance_id]
                )
            if kind:
                clauses.append("kind=?")
                parameters.append(kind)
            if status:
                clauses.append("status=?")
                parameters.append(status)
            parameters.append(int(limit))
            rows = self._db.execute(
                "SELECT * FROM game_sessions WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
            return [self._game_payload(row, viewer_instance_id) for row in rows]

        return await self._run(operation)

    async def get_game(
        self, game_id: str, viewer_instance_id: str
    ) -> Optional[dict[str, Any]]:
        now = int(time.time())

        def operation():
            row = self._db.execute(
                "SELECT * FROM game_sessions WHERE game_id=? AND expires_at>=?",
                (game_id, now),
            ).fetchone()
            if not row:
                return None
            participant = viewer_instance_id in {
                row["creator_instance_id"],
                row["opponent_instance_id"],
            }
            public_waiting = row["status"] == "waiting" and not row["opponent_instance_id"]
            if not participant and not public_waiting:
                return {"error": "game_forbidden"}
            return self._game_payload(row, viewer_instance_id)

        return await self._run(operation)

    async def join_game(
        self,
        game_id: str,
        credential: dict[str, Any],
        active_ttl_seconds: int,
    ) -> dict[str, Any]:
        now = int(time.time())

        def operation():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM game_sessions WHERE game_id=? AND expires_at>=?",
                    (game_id, now),
                ).fetchone()
                if not row:
                    result = {"error": "game_not_found"}
                elif credential["instance_id"] == row["creator_instance_id"]:
                    result = {"error": "self_game"}
                elif int(credential["owner_id"]) == int(row["player0_owner_id"]):
                    result = {"error": "self_game"}
                elif row["status"] == "active" and (
                    credential["instance_id"] == row["opponent_instance_id"]
                ):
                    result = self._game_payload(row, credential["instance_id"])
                elif row["status"] not in {"waiting", "invited"}:
                    result = {"error": "game_unavailable"}
                elif row["status"] == "invited" and (
                    credential["instance_id"] != row["opponent_instance_id"]
                ):
                    result = {"error": "game_forbidden"}
                else:
                    self._db.execute(
                        """
                        UPDATE game_sessions SET status='active',
                            opponent_instance_id=?, player1_owner_id=?, player1_name=?,
                            revision=revision+1, updated_at=?, expires_at=?
                        WHERE game_id=?
                        """,
                        (
                            credential["instance_id"],
                            int(credential["owner_id"]),
                            str(credential["display_name"]),
                            now,
                            now + int(active_ttl_seconds),
                            game_id,
                        ),
                    )
                    joined = self._db.execute(
                        "SELECT * FROM game_sessions WHERE game_id=?", (game_id,)
                    ).fetchone()
                    result = self._game_payload(joined, credential["instance_id"])
                self._db.execute("COMMIT")
                return result
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        return await self._run(operation)

    def _ensure_game_player_sync(self, owner_id: int, display_name: str, now: int) -> None:
        public_id = "p_" + secrets.token_hex(6)
        self._db.execute(
            """
            INSERT INTO game_players(owner_id, public_id, display_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at
            """,
            (int(owner_id), public_id, str(display_name)[:64], now),
        )

    def _record_game_result_sync(
        self, row: sqlite3.Row, state: dict[str, Any], now: int
    ) -> None:
        owner_ids = [int(row["player0_owner_id"]), int(row["player1_owner_id"])]
        names = [str(row["player0_name"]), str(row["player1_name"])]
        kind = str(row["kind"])
        for owner_id, name in zip(owner_ids, names):
            self._ensure_game_player_sync(owner_id, name, now)
        rating_rows = self._db.execute(
            "SELECT owner_id, rating FROM game_stats WHERE kind=? AND owner_id IN (?, ?)",
            (kind, owner_ids[0], owner_ids[1]),
        ).fetchall()
        ratings = {int(item["owner_id"]): float(item["rating"]) for item in rating_rows}
        before = [ratings.get(owner_id, 1000.0) for owner_id in owner_ids]
        winner = state.get("winner")
        draw = bool(state.get("draw"))
        scores = [0.5, 0.5] if draw else [1.0 if winner == slot else 0.0 for slot in (0, 1)]
        expected = [
            1.0 / (1.0 + math.pow(10.0, (before[1] - before[0]) / 400.0)),
            1.0 / (1.0 + math.pow(10.0, (before[0] - before[1]) / 400.0)),
        ]
        after = [round(before[slot] + 32.0 * (scores[slot] - expected[slot]), 1) for slot in (0, 1)]
        for slot, owner_id in enumerate(owner_ids):
            win = int(not draw and winner == slot)
            loss = int(not draw and winner == 1 - slot)
            self._db.execute(
                """
                INSERT INTO game_stats(
                    owner_id, kind, played, wins, losses, draws, rating, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, kind) DO UPDATE SET
                    played=played+1,
                    wins=wins+excluded.wins,
                    losses=losses+excluded.losses,
                    draws=draws+excluded.draws,
                    rating=excluded.rating,
                    updated_at=excluded.updated_at
                """,
                (owner_id, kind, win, loss, int(draw), after[slot], now),
            )

    async def move_game(
        self,
        game_id: str,
        actor_instance_id: str,
        expected_revision: int,
        action: dict[str, Any],
        active_ttl_seconds: int,
    ) -> dict[str, Any]:
        now = int(time.time())

        def operation():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM game_sessions WHERE game_id=? AND expires_at>=?",
                    (game_id, now),
                ).fetchone()
                if not row:
                    result = {"error": "game_not_found"}
                elif row["status"] != "active":
                    result = {"error": "game_unavailable"}
                elif int(row["revision"]) != int(expected_revision):
                    result = {
                        "error": "revision_conflict",
                        "revision": int(row["revision"]),
                    }
                else:
                    state = json.loads(row["state_json"])
                    side = int(state.get("turn", 0))
                    expected_actor = (
                        row["creator_instance_id"]
                        if side == 0
                        else row["opponent_instance_id"]
                    )
                    if actor_instance_id not in {
                        row["creator_instance_id"],
                        row["opponent_instance_id"],
                    }:
                        result = {"error": "game_forbidden"}
                    elif actor_instance_id != expected_actor:
                        result = {"error": "not_your_turn"}
                    else:
                        next_state = apply_action(row["kind"], state, action)
                        status = "finished" if next_state.get("finished") else "active"
                        expires_at = now + (
                            30 * 86400 if status == "finished" else int(active_ttl_seconds)
                        )
                        self._db.execute(
                            """
                            UPDATE game_sessions SET state_json=?, status=?,
                                revision=revision+1, updated_at=?, expires_at=?
                            WHERE game_id=?
                            """,
                            (
                                json.dumps(
                                    next_state,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                status,
                                now,
                                expires_at,
                                game_id,
                            ),
                        )
                        if status == "finished":
                            self._record_game_result_sync(row, next_state, now)
                        updated = self._db.execute(
                            "SELECT * FROM game_sessions WHERE game_id=?", (game_id,)
                        ).fetchone()
                        result = self._game_payload(updated, actor_instance_id)
                self._db.execute("COMMIT")
                return result
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        return await self._run(operation)

    async def resign_game(
        self, game_id: str, actor_instance_id: str
    ) -> dict[str, Any]:
        now = int(time.time())

        def operation():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM game_sessions WHERE game_id=? AND expires_at>=?",
                    (game_id, now),
                ).fetchone()
                if not row:
                    result = {"error": "game_not_found"}
                elif row["status"] != "active":
                    result = {"error": "game_unavailable"}
                elif actor_instance_id == row["creator_instance_id"]:
                    loser_slot = 0
                    result = None
                elif actor_instance_id == row["opponent_instance_id"]:
                    loser_slot = 1
                    result = None
                else:
                    result = {"error": "game_forbidden"}
                if result is None:
                    state = resign_state(json.loads(row["state_json"]), loser_slot)
                    self._db.execute(
                        """
                        UPDATE game_sessions SET state_json=?, status='finished',
                            revision=revision+1, updated_at=?, expires_at=?
                        WHERE game_id=?
                        """,
                        (
                            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                            now,
                            now + 30 * 86400,
                            game_id,
                        ),
                    )
                    self._record_game_result_sync(row, state, now)
                    updated = self._db.execute(
                        "SELECT * FROM game_sessions WHERE game_id=?", (game_id,)
                    ).fetchone()
                    result = self._game_payload(updated, actor_instance_id)
                self._db.execute("COMMIT")
                return result
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        return await self._run(operation)

    async def cancel_game(
        self, game_id: str, actor_instance_id: str
    ) -> dict[str, Any]:
        now = int(time.time())

        def operation():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM game_sessions WHERE game_id=? AND expires_at>=?",
                    (game_id, now),
                ).fetchone()
                if not row:
                    result = {"error": "game_not_found"}
                elif row["status"] not in {"waiting", "invited"}:
                    result = {"error": "game_unavailable"}
                elif actor_instance_id not in {
                    row["creator_instance_id"],
                    row["opponent_instance_id"],
                }:
                    result = {"error": "game_forbidden"}
                else:
                    self._db.execute(
                        """
                        UPDATE game_sessions SET status='cancelled', revision=revision+1,
                            updated_at=?, expires_at=? WHERE game_id=?
                        """,
                        (now, now + 86400, game_id),
                    )
                    updated = self._db.execute(
                        "SELECT * FROM game_sessions WHERE game_id=?", (game_id,)
                    ).fetchone()
                    result = self._game_payload(updated, actor_instance_id)
                self._db.execute("COMMIT")
                return result
            except Exception:
                self._db.execute("ROLLBACK")
                raise

        return await self._run(operation)

    async def game_leaderboard(
        self, kind: Optional[str], order_by: str, limit: int
    ) -> dict[str, Any]:
        def operation():
            order_column = {
                "rating": "rating",
                "played": "played",
                "wins": "wins",
            }.get(order_by, "wins")
            if kind:
                rows = self._db.execute(
                    f"""
                    SELECT p.public_id, p.display_name, s.kind, s.played, s.wins,
                           s.losses, s.draws, ROUND(s.rating, 1) AS rating,
                           s.updated_at
                    FROM game_stats s JOIN game_players p ON p.owner_id=s.owner_id
                    WHERE s.kind=?
                    ORDER BY {order_column} DESC, s.played DESC, p.display_name
                    LIMIT ?
                    """,
                    (kind, int(limit)),
                ).fetchall()
            else:
                aggregate_order = (
                    "rating" if order_column == "rating" else order_column
                )
                rows = self._db.execute(
                    f"""
                    SELECT p.public_id, p.display_name, SUM(s.played) AS played,
                           SUM(s.wins) AS wins, SUM(s.losses) AS losses,
                           SUM(s.draws) AS draws, ROUND(AVG(s.rating), 1) AS rating,
                           MAX(s.updated_at) AS updated_at
                    FROM game_stats s JOIN game_players p ON p.owner_id=s.owner_id
                    GROUP BY s.owner_id
                    ORDER BY {aggregate_order} DESC, played DESC, p.display_name
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return {
                "kind": kind,
                "sort": order_by,
                "ranking": [dict(row) for row in rows],
            }

        return await self._run(operation)

    async def game_profile(self, owner_id: int) -> dict[str, Any]:
        def operation():
            player = self._db.execute(
                "SELECT public_id, display_name FROM game_players WHERE owner_id=?",
                (int(owner_id),),
            ).fetchone()
            rows = self._db.execute(
                """
                SELECT kind, played, wins, losses, draws,
                       ROUND(rating, 1) AS rating, updated_at
                FROM game_stats WHERE owner_id=? ORDER BY kind
                """,
                (int(owner_id),),
            ).fetchall()
            games = [dict(row) for row in rows]
            return {
                "public_id": player["public_id"] if player else None,
                "display_name": player["display_name"] if player else None,
                "totals": {
                    "played": sum(int(row["played"]) for row in rows),
                    "wins": sum(int(row["wins"]) for row in rows),
                    "losses": sum(int(row["losses"]) for row in rows),
                    "draws": sum(int(row["draws"]) for row in rows),
                },
                "by_game": games,
            }

        return await self._run(operation)

    async def touch_chat_presence(
        self,
        room: str,
        instance_id: str,
        nickname: str,
        ttl_seconds: int,
    ) -> dict[str, int]:
        now = int(time.time())

        def operation():
            self._db.execute(
                """
                INSERT INTO chat_presence(room, instance_id, nickname, last_seen, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room, instance_id) DO UPDATE SET
                    nickname=excluded.nickname,
                    last_seen=excluded.last_seen,
                    expires_at=excluded.expires_at
                """,
                (room, instance_id, nickname, now, now + int(ttl_seconds)),
            )
            return {"last_seen": now, "expires_at": now + int(ttl_seconds)}

        return await self._run(operation)

    async def leave_chat_presence(self, room: str, instance_id: str) -> bool:
        def operation():
            cursor = self._db.execute(
                "DELETE FROM chat_presence WHERE room=? AND instance_id=?",
                (room, instance_id),
            )
            return cursor.rowcount > 0

        return await self._run(operation)

    async def chat_room_members(
        self, room: str, active_within: int, limit: int
    ) -> list[dict[str, Any]]:
        now = int(time.time())

        def operation():
            rows = self._db.execute(
                """
                SELECT p.instance_id, p.nickname, p.last_seen, i.display_name
                FROM chat_presence p
                LEFT JOIN instances i ON i.instance_id=p.instance_id
                WHERE p.room=? AND p.expires_at>=? AND p.last_seen>=?
                ORDER BY p.last_seen DESC LIMIT ?
                """,
                (room, now, now - int(active_within), int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)

    async def chat_rooms(
        self, active_within: int, limit: int
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        since = now - 86400

        def operation():
            self._db.execute("DELETE FROM chat_presence WHERE expires_at < ?", (now,))
            presence = self._db.execute(
                """
                SELECT room, COUNT(*) AS online, MAX(last_seen) AS last_activity
                FROM chat_presence WHERE last_seen>=?
                GROUP BY room
                """,
                (now - int(active_within),),
            ).fetchall()
            messages = self._db.execute(
                """
                SELECT SUBSTR(topic, 6) AS room, COUNT(*) AS messages_24h,
                       MAX(created_at) AS last_message_at
                FROM events WHERE topic LIKE 'chat.%' AND created_at>=?
                GROUP BY topic
                """,
                (since,),
            ).fetchall()
            result: dict[str, dict[str, Any]] = {}
            for row in presence:
                result[row["room"]] = {
                    "room": row["room"],
                    "online": int(row["online"]),
                    "messages_24h": 0,
                    "last_activity": int(row["last_activity"] or 0),
                }
            for row in messages:
                item = result.setdefault(
                    row["room"],
                    {
                        "room": row["room"],
                        "online": 0,
                        "messages_24h": 0,
                        "last_activity": 0,
                    },
                )
                item["messages_24h"] = int(row["messages_24h"])
                item["last_activity"] = max(
                    item["last_activity"], int(row["last_message_at"] or 0)
                )
            return sorted(
                result.values(),
                key=lambda item: (
                    -int(item["online"]),
                    -int(item["messages_24h"]),
                    str(item["room"]),
                ),
            )[: int(limit)]

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
            games = self._db.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END)
                FROM game_sessions WHERE expires_at>=?
                """,
                (now,),
            ).fetchone()
            game_players = self._db.execute(
                "SELECT COUNT(*) FROM game_players"
            ).fetchone()[0]
            chat_rooms = self._db.execute(
                """
                SELECT COUNT(DISTINCT room) FROM chat_presence
                WHERE expires_at>=? AND last_seen>=?
                """,
                (now, now - offline_after),
            ).fetchone()[0]
            return {
                "instances": {"total": enabled, "online": online},
                "events_24h": events_24h,
                "today": {
                    "requests": usage[0],
                    "heartbeats": usage[1],
                    "events": usage[2],
                    "kv_writes": usage[3],
                },
                "games": {
                    "total": int(games[0] or 0),
                    "active": int(games[1] or 0),
                    "finished": int(games[2] or 0),
                    "players": int(game_players or 0),
                },
                "chat": {"active_rooms": int(chat_rooms or 0)},
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
            game_cursor = self._db.execute(
                "DELETE FROM game_sessions WHERE expires_at < ?", (now,)
            )
            presence_cursor = self._db.execute(
                "DELETE FROM chat_presence WHERE expires_at < ?", (now,)
            )
            return {
                "nonces": nonce_cursor.rowcount,
                "events": event_cursor.rowcount,
                "kv_items": kv_cursor.rowcount,
                "games": game_cursor.rowcount,
                "chat_presence": presence_cursor.rowcount,
            }

        return await self._run(operation)


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
