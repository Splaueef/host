"""Local credential administration for Hikka Hub."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from .config import Settings
from .security import INSTANCE_ID_RE, derive_hmac_key
from .storage import SCHEMA


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    with contextlib.suppress(PermissionError):
        os.chmod(path, 0o600)
    return connection


def _audit(db: sqlite3.Connection, action: str, instance_id: str, details: dict) -> None:
    db.execute(
        "INSERT INTO audit_log(created_at, action, instance_id, details_json) VALUES (?, ?, ?, ?)",
        (int(time.time()), action, instance_id, json.dumps(details, separators=(",", ":"))),
    )


def issue(args: argparse.Namespace) -> int:
    instance_id = args.instance_id or f"hikka-{uuid.uuid4().hex[:12]}"
    if not INSTANCE_ID_RE.fullmatch(instance_id):
        raise SystemExit("Invalid --instance-id (3..64 ASCII letters/digits/._-)")
    if args.owner_id <= 0:
        raise SystemExit("--owner-id must be a positive Telegram user ID")
    display_name = (args.name or instance_id).strip()
    if not display_name or len(display_name) > 64:
        raise SystemExit("--name must contain 1..64 characters")

    key_id = "hk_" + secrets.token_urlsafe(12)
    secret = secrets.token_urlsafe(36)
    now = int(time.time())
    db = _connect(args.database)
    try:
        db.execute(
            """
            INSERT INTO instances(key_id, instance_id, owner_id, display_name,
                                  derived_key_hex, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                key_id,
                instance_id,
                args.owner_id,
                display_name,
                derive_hmac_key(secret).hex(),
                now,
                now,
            ),
        )
        _audit(db, "credential_issued", instance_id, {"key_id": key_id, "owner_id": args.owner_id})
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise SystemExit(f"Instance already exists: {instance_id}") from exc
    finally:
        db.close()

    print("Credential created. The secret is shown only once.\n")
    print(f"server_url={args.server_url.rstrip('/')}")
    print(f"instance_id={instance_id}")
    print(f"key_id={key_id}")
    print(f"key_secret={secret}")
    print("\nStore key_secret only in Hikka's protected module config.")
    return 0


def list_credentials(args: argparse.Namespace) -> int:
    db = _connect(args.database)
    try:
        rows = db.execute(
            """
            SELECT key_id, instance_id, owner_id, display_name, enabled, created_at, last_seen
            FROM instances ORDER BY created_at
            """
        ).fetchall()
    finally:
        db.close()
    if not rows:
        print("No credentials.")
        return 0
    for row in rows:
        state = "enabled" if row["enabled"] else "revoked"
        print(
            f"{row['instance_id']}  {row['key_id']}  owner={row['owner_id']}  "
            f"{state}  last_seen={row['last_seen'] or '-'}  {row['display_name']}"
        )
    return 0


def set_enabled(args: argparse.Namespace, enabled: bool) -> int:
    db = _connect(args.database)
    try:
        row = db.execute(
            "SELECT instance_id FROM instances WHERE key_id=? OR instance_id=?",
            (args.credential, args.credential),
        ).fetchone()
        if not row:
            raise SystemExit("Credential not found")
        db.execute(
            "UPDATE instances SET enabled=?, updated_at=? WHERE instance_id=?",
            (1 if enabled else 0, int(time.time()), row["instance_id"]),
        )
        _audit(
            db,
            "credential_enabled" if enabled else "credential_revoked",
            row["instance_id"],
            {},
        )
        db.commit()
    finally:
        db.close()
    print(f"{row['instance_id']}: {'enabled' if enabled else 'revoked'}")
    return 0


def rotate(args: argparse.Namespace) -> int:
    secret = secrets.token_urlsafe(36)
    db = _connect(args.database)
    try:
        row = db.execute(
            "SELECT key_id, instance_id FROM instances WHERE key_id=? OR instance_id=?",
            (args.credential, args.credential),
        ).fetchone()
        if not row:
            raise SystemExit("Credential not found")
        db.execute(
            "UPDATE instances SET derived_key_hex=?, enabled=1, updated_at=? WHERE instance_id=?",
            (derive_hmac_key(secret).hex(), int(time.time()), row["instance_id"]),
        )
        db.execute("DELETE FROM request_nonces WHERE key_id=?", (row["key_id"],))
        _audit(db, "credential_rotated", row["instance_id"], {"key_id": row["key_id"]})
        db.commit()
    finally:
        db.close()
    print("Credential rotated. The new secret is shown only once.\n")
    print(f"instance_id={row['instance_id']}")
    print(f"key_id={row['key_id']}")
    print(f"key_secret={secret}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Hikka Hub credentials")
    parser.add_argument(
        "--database",
        type=Path,
        default=Settings.from_env().database,
        help="SQLite database path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue_parser = subparsers.add_parser("issue", help="Issue a key for one Hikka instance")
    issue_parser.add_argument("--owner-id", required=True, type=int)
    issue_parser.add_argument("--instance-id")
    issue_parser.add_argument("--name")
    issue_parser.add_argument("--server-url", default="http://127.0.0.1:8765")
    issue_parser.set_defaults(func=issue)

    list_parser = subparsers.add_parser("list", help="List issued credentials")
    list_parser.set_defaults(func=list_credentials)

    for command, enabled in (("revoke", False), ("enable", True)):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("credential", help="instance_id or key_id")
        command_parser.set_defaults(func=lambda args, state=enabled: set_enabled(args, state))

    rotate_parser = subparsers.add_parser("rotate", help="Replace a credential secret")
    rotate_parser.add_argument("credential", help="instance_id or key_id")
    rotate_parser.set_defaults(func=rotate)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
