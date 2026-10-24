# meta developer: @Huai_Baike
# meta version: 2.3.0
# meta description: 🔐 HikkaNet: вузли, каталог модулів, пошта, глобальні ігри, чат, події та статистика.
# scope: inline
# scope: hikka_only

"""Hikka client for the standalone Hikka Hub service.

Each installation gets its own instance_id, key_id and secret. Every request is
HMAC-SHA256 signed and bound to the actual Telegram owner ID of this Hikka.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import html
import json
import logging
import math
import re
import secrets
import shlex
import time
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import aiohttp

from .. import loader, utils


logger = logging.getLogger(__name__)
__version__ = (2, 3, 0)
_PROTOCOL = "HIKKA-HUB-V1"
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
_GAME_ID_RE = re.compile(r"^ng_[0-9a-f]{16}$")
_ROOM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_KEY_RE = re.compile(r"^hk_[A-Za-z0-9_-]{8,48}$")
_SECRET_FIELDS = {
    "api_key",
    "authorization",
    "cookie",
    "key_secret",
    "password",
    "phone",
    "secret",
    "session",
    "session_string",
    "token",
}
_GAME_KINDS = {"ttt", "checkers", "chess", "go9", "go13"}
_GAME_STATUSES = {"waiting", "invited", "active", "finished", "cancelled"}
_CATALOG_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
_CATALOG_SORTS = {
    "popular": "🔥 Популярні",
    "rating": "⭐ Рейтинг",
    "new": "🆕 Нові",
    "updated": "🔄 Оновлені",
}
_CATALOG_PAGE = 6
_MODULE_FILE_LIMIT = 1024 * 1024
_WARNING_LABELS = {
    "dynamic_code": "⚠️ виконує динамічний код (eval / exec)",
    "shell": "🖥 запускає системні команди",
    "session_access": "🔑 звертається до сесії Telegram",
    "account_danger": "🚨 викликає небезпечні методи акаунта",
    "low_level": "🧬 використовує ctypes / pickle / marshal",
    "filesystem": "🗂 видаляє або переміщує файли",
    "network": "🌐 надсилає запити в мережу",
}
# Network access alone is normal for many modules and does not need a prompt.
_CONFIRM_WARNINGS = set(_WARNING_LABELS) - {"network"}
_DIRECT_TEXT_LIMIT = 2000


class HubClientError(RuntimeError):
    def __init__(self, message, status=0, code="connection_error"):
        super().__init__(message)
        self.status = int(status or 0)
        self.code = str(code or "connection_error")


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical(
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    instance_id: str,
    owner_id: str,
    content_hash: str,
) -> bytes:
    values = (
        _PROTOCOL,
        method.upper(),
        target,
        timestamp,
        nonce,
        instance_id,
        owner_id,
        content_hash.lower(),
    )
    if any("\n" in value or "\r" in value for value in values):
        raise ValueError("Некоректне поле підпису")
    return "\n".join(values).encode("utf-8")


def _sign(
    secret: str,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    instance_id: str,
    owner_id: str,
    content_hash: str,
) -> str:
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return hmac.new(
        key,
        _canonical(
            method,
            target,
            timestamp,
            nonce,
            instance_id,
            owner_id,
            content_hash,
        ),
        hashlib.sha256,
    ).hexdigest()


def _normalise_server_url(value: str) -> str:
    value = str(value or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("URL повинен починатися з http:// або https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("URL не повинен містити логін, пароль, query або fragment")
    if parts.path not in {"", "/"}:
        raise ValueError("Вкажи лише адресу сервера без шляху")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Некоректний порт") from exc
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def _identifier(value: str, label="ідентифікатор") -> str:
    value = str(value or "").strip().lower()
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label}: лише a-z, 0-9, крапка, _ або -, до 64 символів")
    return value


def _game_id(value: str) -> str:
    value = _identifier(value, "ID гри")
    if not _GAME_ID_RE.fullmatch(value):
        raise ValueError("Некоректний ID мережевої гри")
    return value


def _instance_identifier(value: str) -> str:
    value = str(value or "").strip()
    if not _INSTANCE_RE.fullmatch(value):
        raise ValueError("Некоректний instance_id суперника")
    return value


def _room_identifier(value: str) -> str:
    value = str(value or "").strip().lower()
    if not _ROOM_RE.fullmatch(value):
        raise ValueError("Кімната: a-z, 0-9, _ або -, до 48 символів")
    return value


def _json_or_text(raw: str):
    raw = str(raw).strip()
    if not raw:
        raise ValueError("Порожнє значення")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    _reject_non_finite(value)
    return value


def _reject_non_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN та Infinity не підтримуються")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite(item)


def _find_sensitive_field(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            current = f"{path}.{key}" if path else str(key)
            if key_text in _SECRET_FIELDS or any(
                marker in key_text for marker in ("password", "secret", "token")
            ):
                return current
            found = _find_sensitive_field(item, current)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_sensitive_field(item, f"{path}[{index}]")
            if found:
                return found
    return ""


def _preview(value, maximum=350) -> str:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return rendered if len(rendered) <= maximum else rendered[: maximum - 1] + "…"


def _catalog_id(value) -> str:
    value = str(value or "").strip().lower()
    if not _CATALOG_ID_RE.fullmatch(value):
        raise ValueError("ID модуля: латинські літери, цифри або _, 2–48 символів")
    return value


def _version(value) -> str:
    value = str(value or "").strip().lstrip("vV")
    if not _VERSION_RE.fullmatch(value):
        raise ValueError("Версія має виглядати як 1.2.3")
    return value


def _version_tuple(value) -> tuple:
    try:
        parts = tuple(int(item) for item in str(value).split("."))
    except ValueError:
        return (0, 0, 0, 0)
    return parts + (0,) * (4 - len(parts))


def _format_size(size) -> str:
    size = int(size or 0)
    if size < 1024:
        return f"{size} Б"
    return f"{size / 1024:.1f} КБ"


def _stars(rating, count) -> str:
    if not count:
        return "☆ без оцінок"
    return f"⭐ {float(rating):.1f} ({int(count)})"


def _warning_lines(codes) -> list:
    return [_WARNING_LABELS.get(code, f"❔ {code}") for code in codes or []]


def _parse_publish_args(raw: str):
    """'#fun text | Що нового' -> (['fun', 'text'], 'Що нового')."""
    head, _, changelog = str(raw or "").partition("|")
    tags = []
    for word in head.replace(",", " ").split():
        tag = word.strip().lower().lstrip("#")
        if tag and tag not in tags:
            tags.append(tag)
    return tags, " ".join(changelog.split())


@loader.tds
class HikkaNetMod(loader.Module):
    """🔐 Мережа обміну даними між авторизованими Hikka"""

    strings = {
        "name": "HikkaNet",
        "not_configured": (
            "🔐 <b>HikkaNet ще не налаштовано.</b>\n\n"
            "Внеси <code>server_url</code>, <code>instance_id</code>, "
            "<code>key_id</code> і <code>key_secret</code> через "
            "<code>.config HikkaNet</code> або використай <code>.hknetsetup</code>."
        ),
        "inline_failed": "❌ Inline-меню недоступне. Використай <code>.hknetstatus</code>.",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "server_url",
                "http://127.0.0.1:8765",
                "Адреса окремого Hikka Hub сервісу (IP:порт або HTTPS-домен)",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "instance_id",
                "",
                "Унікальний ID саме цієї Hikka, виданий адміністратором сервісу",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "key_id",
                "",
                "Публічний ID ключа саме цієї Hikka",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "key_secret",
                "",
                "Секрет саме цієї Hikka; нікому його не надсилай",
                validator=loader.validators.Hidden(loader.validators.String()),
            ),
            loader.ConfigValue(
                "display_name",
                "",
                "Ім'я цієї Hikka у списку вузлів",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "heartbeat_interval",
                30,
                "Інтервал heartbeat у секундах",
                validator=loader.validators.Integer(minimum=20, maximum=600),
            ),
            loader.ConfigValue(
                "request_timeout",
                15,
                "Таймаут API-запиту у секундах",
                validator=loader.validators.Integer(minimum=3, maximum=120),
            ),
            loader.ConfigValue(
                "verify_ssl",
                True,
                "Перевіряти TLS-сертифікат HTTPS-сервера",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "notify_events",
                False,
                "Надсилати нові мережеві події у Збережені повідомлення",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "event_topics",
                [],
                "Теми сповіщень; порожній список означає всі теми",
                validator=loader.validators.Series(loader.validators.String()),
            ),
            loader.ConfigValue(
                "notify_direct",
                True,
                "Надсилати особисті повідомлення інших вузлів у Збережені",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "catalog_check_hours",
                6,
                "Як часто перевіряти оновлення модулів із каталогу (годин, 0 — ніколи)",
                validator=loader.validators.Integer(minimum=0, maximum=168),
            ),
            loader.ConfigValue(
                "catalog_auto_update",
                False,
                "Самостійно оновлювати встановлені з каталогу модулі, "
                "якщо нова версія не має попереджень безпеки",
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._me = None
        self._session = None
        self._worker_task = None
        self._stop_event = asyncio.Event()
        self._connected = False
        self._last_success = 0
        self._last_error = ""
        self._last_latency_ms = 0
        self._last_catalog_check = time.monotonic()

    async def client_ready(self, client, db):
        self._client = client
        self._me = await client.get_me()
        self._stop_event.clear()
        await self._ensure_session()
        self._start_worker()

    async def on_unload(self):
        self._stop_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        self._worker_task = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _configured(self):
        return bool(
            str(self.config["server_url"]).strip()
            and _INSTANCE_RE.fullmatch(str(self.config["instance_id"]).strip())
            and _KEY_RE.fullmatch(str(self.config["key_id"]).strip())
            and len(str(self.config["key_secret"]).strip()) >= 32
            and self._me
        )

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "HikkaNet/{} (Hikka module)".format(
                        ".".join(map(str, __version__))
                    )
                }
            )

    def _start_worker(self):
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(
            self._worker(), name="hikka-net-heartbeat"
        )

    async def _wait(self, seconds):
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=max(1, seconds))
        except asyncio.TimeoutError:
            pass

    async def _worker(self):
        backoff = 5
        while not self._stop_event.is_set():
            if not self._configured():
                self._connected = False
                await self._wait(10)
                continue
            try:
                await self._heartbeat()
                await self._background_extras()
                self._connected = True
                self._last_success = int(time.time())
                self._last_error = ""
                backoff = 5
                await self._wait(int(self.config["heartbeat_interval"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._last_error = self._safe_error(exc)
                logger.warning("HikkaNet background request failed: %s", self._last_error)
                await self._wait(backoff)
                backoff = min(backoff * 2, 300)

    async def _background_extras(self):
        """Optional polling; a failure here must not mark the node offline."""
        jobs = []
        if self.config["notify_events"]:
            jobs.append(("events", self._poll_notifications))
        if self.config["notify_direct"]:
            jobs.append(("inbox", self._poll_direct))
        hours = int(self.config["catalog_check_hours"] or 0)
        if hours and time.monotonic() - self._last_catalog_check >= hours * 3600:
            self._last_catalog_check = time.monotonic()
            jobs.append(("catalog", self._check_catalog_updates))
        for name, job in jobs:
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except HubClientError as exc:
                # An older Hikka Hub simply has no such route yet.
                if exc.status != 404:
                    logger.warning("HikkaNet %s poll failed: %s", name, exc)
            except Exception:
                logger.exception("HikkaNet %s poll failed", name)

    @staticmethod
    def _safe_error(exc):
        if (
            isinstance(exc, HubClientError)
            and exc.status == 404
            and exc.code == "http_error"
        ):
            return "Цей Hikka Hub ще не підтримує функцію — онови сервер до 2.1"
        text = str(exc).replace("\n", " ").strip()
        return text[:240] or exc.__class__.__name__

    def _target(self, path, params=None):
        path = "/" + str(path).lstrip("/")
        if not path.startswith("/v1/") or ".." in path.split("/"):
            raise HubClientError("Дозволені лише маршрути /v1/*", code="bad_route")
        if not params:
            return path
        pairs = []
        for key, value in sorted(params.items()):
            if value is None or value == "":
                continue
            if isinstance(value, (list, tuple)):
                pairs.extend((key, item) for item in value)
            else:
                pairs.append((key, value))
        query = urlencode(pairs, doseq=True)
        return f"{path}?{query}" if query else path

    async def _request(self, method, path, params=None, payload=None, max_bytes=524288):
        if not self._configured():
            raise HubClientError("HikkaNet не налаштовано", code="not_configured")
        try:
            server_url = _normalise_server_url(self.config["server_url"])
        except ValueError as exc:
            raise HubClientError(str(exc), code="invalid_server_url") from exc
        target = self._target(path, params)
        body = b""
        if payload is not None:
            try:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise HubClientError(f"Дані не можна перетворити на JSON: {exc}") from exc

        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        instance_id = str(self.config["instance_id"]).strip()
        key_id = str(self.config["key_id"]).strip()
        secret = str(self.config["key_secret"]).strip()
        owner_id = str(int(self._me.id))
        content_hash = _body_hash(body)
        signature = _sign(
            secret,
            method,
            target,
            timestamp,
            nonce,
            instance_id,
            owner_id,
            content_hash,
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Hikka-HMAC {key_id}:{signature}",
            "X-Hikka-Timestamp": timestamp,
            "X-Hikka-Nonce": nonce,
            "X-Hikka-Instance": instance_id,
            "X-Hikka-Owner": owner_id,
            "X-Hikka-Content-SHA256": content_hash,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        await self._ensure_session()
        started = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=int(self.config["request_timeout"]))
        ssl = None if self.config["verify_ssl"] else False
        try:
            async with self._session.request(
                method.upper(),
                server_url + target,
                data=body if payload is not None else None,
                headers=headers,
                timeout=timeout,
                ssl=ssl,
            ) as response:
                raw = await response.content.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise HubClientError("Відповідь сервера завелика")
                try:
                    envelope = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise HubClientError(
                        f"Сервер повернув не JSON (HTTP {response.status})",
                        status=response.status,
                        code="invalid_response",
                    ) from None
                if response.status >= 400 or not envelope.get("ok", False):
                    error = envelope.get("error") if isinstance(envelope, dict) else {}
                    if not isinstance(error, dict):
                        error = {}
                    raise HubClientError(
                        str(error.get("message") or f"HTTP {response.status}"),
                        status=response.status,
                        code=str(error.get("code") or "api_error"),
                    )
                self._last_latency_ms = round((time.monotonic() - started) * 1000)
                self._connected = True
                self._last_success = int(time.time())
                self._last_error = ""
                return envelope.get("data")
        except HubClientError:
            raise
        except asyncio.TimeoutError:
            raise HubClientError("Сервер не відповів вчасно", code="timeout") from None
        except aiohttp.ClientError as exc:
            raise HubClientError(
                f"Немає з'єднання із сервісом: {exc.__class__.__name__}",
                code="connection_error",
            ) from None

    async def _heartbeat(self):
        display_name = str(self.config["display_name"]).strip()
        if not display_name:
            display_name = (
                getattr(self._me, "first_name", "")
                or getattr(self._me, "username", "")
                or f"Hikka {self._me.id}"
            )
        return await self._request(
            "POST",
            "/v1/heartbeat",
            payload={
                "display_name": str(display_name)[:64],
                "hikka_version": self._hikka_version(),
                "module_version": ".".join(map(str, __version__)),
                "capabilities": [
                    "events",
                    "event_history",
                    "kv",
                    "metrics",
                    "metrics_batch",
                    "module_updates",
                    "module_update_summaries",
                    "network_games",
                    "game_leaderboard",
                    "chat_presence",
                    "presence",
                    "module_catalog",
                    "direct_messages",
                ],
            },
        )

    def _hikka_version(self):
        version = getattr(getattr(self, "allmodules", None), "version", "")
        if isinstance(version, (tuple, list)):
            return ".".join(map(str, version))[:64]
        return str(version or "unknown")[:64]

    # Public integration API for other locally installed Hikka modules. A module
    # can use self.lookup("HikkaNet") and call these methods without learning the
    # credential secret or reimplementing request signing.
    async def api_publish(self, topic, payload, ttl_seconds=86400):
        topic = _identifier(topic, "тема")
        _reject_non_finite(payload)
        sensitive = _find_sensitive_field(payload)
        if sensitive:
            raise ValueError(f"Схоже на секретне поле: {sensitive}")
        ttl_seconds = int(ttl_seconds)
        return await self._request(
            "POST",
            "/v1/events",
            payload={
                "topic": topic,
                "payload": payload,
                "ttl_seconds": ttl_seconds,
            },
        )

    async def api_events(self, after_id=0, topic=None, limit=50, latest=False):
        topic = _identifier(topic, "тема") if topic else None
        return await self._request(
            "GET",
            "/v1/events",
            params={
                "after_id": max(0, int(after_id)),
                "limit": max(1, min(int(limit), 200)),
                "topic": topic,
                "latest": 1 if latest else None,
            },
        )

    async def api_instances(self, active_within=120):
        return await self._request(
            "GET",
            "/v1/instances",
            params={"active_within": max(30, min(int(active_within), 3600))},
        )

    async def api_module_versions(self):
        """Return the server-cached trusted module version manifest."""
        return await self._request("GET", "/v1/modules/versions")

    async def api_game_create(self, kind, opponent_instance_id=None):
        kind = _identifier(kind, "гра")
        if kind not in _GAME_KINDS:
            raise ValueError("Підтримуються ttt, checkers, chess, go9 та go13")
        payload = {"kind": kind}
        if opponent_instance_id:
            payload["opponent_instance_id"] = _instance_identifier(
                opponent_instance_id
            )
        return await self._request("POST", "/v1/games", payload=payload)

    async def api_games(
        self,
        scope="mine",
        kind=None,
        status=None,
        updated_after=0,
        limit=30,
    ):
        scope = str(scope).strip().lower()
        if scope not in {"mine", "open", "all"}:
            raise ValueError("scope повинен бути mine, open або all")
        if kind:
            kind = _identifier(kind, "гра")
            if kind not in _GAME_KINDS:
                raise ValueError("Непідтримувана гра")
        if status:
            status = _identifier(status, "статус")
            if status not in _GAME_STATUSES:
                raise ValueError("Непідтримуваний статус гри")
        return await self._request(
            "GET",
            "/v1/games",
            params={
                "scope": scope,
                "kind": kind,
                "status": status,
                "updated_after": max(0, int(updated_after)),
                "limit": max(1, min(int(limit), 100)),
            },
        )

    async def api_game_get(self, game_id):
        game_id = _game_id(game_id)
        return await self._request(
            "GET", f"/v1/games/{quote(game_id, safe='')}"
        )

    async def api_game_join(self, game_id):
        game_id = _game_id(game_id)
        return await self._request(
            "POST", f"/v1/games/{quote(game_id, safe='')}/join", payload={}
        )

    async def api_game_move(self, game_id, revision, action):
        game_id = _game_id(game_id)
        if not isinstance(action, dict) or not action:
            raise ValueError("action повинен бути непорожнім словником")
        _reject_non_finite(action)
        sensitive = _find_sensitive_field(action)
        if sensitive:
            raise ValueError(f"Схоже на секретне поле: {sensitive}")
        revision = int(revision)
        if revision < 1:
            raise ValueError("revision повинен бути додатним")
        return await self._request(
            "POST",
            f"/v1/games/{quote(game_id, safe='')}/move",
            payload={"if_revision": revision, "action": action},
        )

    async def api_game_resign(self, game_id):
        game_id = _game_id(game_id)
        return await self._request(
            "POST", f"/v1/games/{quote(game_id, safe='')}/resign", payload={}
        )

    async def api_game_cancel(self, game_id):
        game_id = _game_id(game_id)
        return await self._request(
            "DELETE", f"/v1/games/{quote(game_id, safe='')}"
        )

    async def api_game_leaderboard(self, kind=None, sort="wins", limit=20):
        if kind:
            kind = _identifier(kind, "гра")
            if kind not in _GAME_KINDS:
                raise ValueError("Непідтримувана гра")
        sort = str(sort).strip().lower()
        if sort not in {"wins", "played", "rating"}:
            raise ValueError("sort повинен бути wins, played або rating")
        return await self._request(
            "GET",
            "/v1/games/leaderboard",
            params={
                "kind": kind,
                "sort": sort,
                "limit": max(1, min(int(limit), 100)),
            },
        )

    async def api_game_profile(self):
        return await self._request("GET", "/v1/games/profile")

    async def api_chat_presence(self, room, nickname=""):
        room = _room_identifier(room)
        nickname = str(nickname or "").strip()
        if len(nickname) > 40 or any(character in nickname for character in "\r\n"):
            raise ValueError("Псевдонім має містити до 40 символів")
        return await self._request(
            "POST",
            "/v1/chat/presence",
            payload={"room": room, "nickname": nickname},
        )

    async def api_chat_leave(self, room):
        room = _room_identifier(room)
        return await self._request(
            "DELETE", f"/v1/chat/presence/{quote(room, safe='')}"
        )

    async def api_chat_rooms(self, active_within=120, limit=50):
        return await self._request(
            "GET",
            "/v1/chat/rooms",
            params={
                "active_within": max(30, min(int(active_within), 3600)),
                "limit": max(1, min(int(limit), 100)),
            },
        )

    async def api_chat_members(self, room, active_within=120, limit=100):
        room = _room_identifier(room)
        return await self._request(
            "GET",
            f"/v1/chat/rooms/{quote(room, safe='')}/members",
            params={
                "active_within": max(30, min(int(active_within), 3600)),
                "limit": max(1, min(int(limit), 200)),
            },
        )

    async def api_get(self, namespace, item_key=None, prefix="", limit=50):
        namespace = _identifier(namespace, "namespace")
        if item_key is not None:
            item_key = _identifier(item_key, "key")
            return await self._request(
                "GET",
                f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}",
            )
        return await self._request(
            "GET",
            f"/v1/kv/{quote(namespace, safe='')}",
            params={"prefix": str(prefix), "limit": max(1, min(int(limit), 200))},
        )

    async def api_put(
        self,
        namespace,
        item_key,
        value,
        ttl_seconds=None,
        if_revision=None,
    ):
        namespace = _identifier(namespace, "namespace")
        item_key = _identifier(item_key, "key")
        _reject_non_finite(value)
        sensitive = _find_sensitive_field(value)
        if sensitive:
            raise ValueError(f"Схоже на секретне поле: {sensitive}")
        payload = {"value": value}
        if ttl_seconds is not None:
            payload["ttl_seconds"] = int(ttl_seconds)
        if if_revision is not None:
            payload["if_revision"] = int(if_revision)
        return await self._request(
            "PUT",
            f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}",
            payload=payload,
        )

    async def api_delete(self, namespace, item_key):
        namespace = _identifier(namespace, "namespace")
        item_key = _identifier(item_key, "key")
        return await self._request(
            "DELETE",
            f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}",
        )

    async def api_increment(self, metric, delta=1):
        metric = _identifier(metric, "метрика")
        delta = float(delta)
        if not math.isfinite(delta) or not 0 < delta <= 10000:
            raise ValueError("delta повинен бути > 0 та ≤ 10000")
        return await self._request(
            "POST",
            f"/v1/metrics/{quote(metric, safe='')}/increment",
            payload={"delta": delta},
        )

    async def api_increment_many(self, metrics):
        if not isinstance(metrics, dict) or not 1 <= len(metrics) <= 100:
            raise ValueError("metrics повинен містити від 1 до 100 значень")
        clean = {}
        for metric, delta in metrics.items():
            metric = _identifier(metric, "метрика")
            delta = float(delta)
            if not math.isfinite(delta) or not 0 < delta <= 10000:
                raise ValueError("кожен delta повинен бути > 0 та ≤ 10000")
            clean[metric] = delta
        return await self._request(
            "POST", "/v1/metrics/batch", payload={"metrics": clean}
        )

    async def api_stats(self, metric=None, limit=20):
        if metric is None:
            return await self._request("GET", "/v1/stats")
        metric = _identifier(metric, "метрика")
        return await self._request(
            "GET",
            f"/v1/stats/{quote(metric, safe='')}",
            params={"limit": max(1, min(int(limit), 100))},
        )

    async def api_catalog_search(
        self,
        query="",
        tag="",
        sort="popular",
        limit=20,
        offset=0,
        mine=False,
        official=False,
    ):
        sort = str(sort or "popular").strip().lower()
        if sort not in _CATALOG_SORTS:
            raise ValueError("sort: popular, rating, new або updated")
        return await self._request(
            "GET",
            "/v1/catalog/modules",
            params={
                "q": " ".join(str(query or "").split())[:64],
                "tag": str(tag or "").strip().lower().lstrip("#"),
                "sort": sort,
                "limit": max(1, min(int(limit), 50)),
                "offset": max(0, int(offset)),
                "mine": 1 if mine else None,
                "official": 1 if official else None,
            },
        )

    async def api_catalog_tags(self, limit=30):
        return await self._request(
            "GET", "/v1/catalog/tags", params={"limit": max(1, min(int(limit), 100))}
        )

    async def api_catalog_get(self, module_id):
        module_id = _catalog_id(module_id)
        return await self._request(
            "GET", f"/v1/catalog/modules/{quote(module_id, safe='')}"
        )

    async def api_catalog_source(self, module_id, version=None):
        module_id = _catalog_id(module_id)
        return await self._request(
            "GET",
            f"/v1/catalog/modules/{quote(module_id, safe='')}/source",
            params={"version": _version(version) if version else None},
            max_bytes=4 * 1024 * 1024,
        )

    async def api_catalog_publish(self, source, tags=None, changelog="", version=None):
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Порожній файл модуля")
        payload = {"source": source, "changelog": str(changelog or "")[:1000]}
        if tags is not None:
            payload["tags"] = list(tags)
        if version:
            payload["version"] = _version(version)
        return await self._request("POST", "/v1/catalog/modules", payload=payload)

    async def api_catalog_delete(self, module_id, version=None):
        module_id = _catalog_id(module_id)
        return await self._request(
            "DELETE",
            f"/v1/catalog/modules/{quote(module_id, safe='')}",
            params={"version": _version(version) if version else None},
        )

    async def api_catalog_rate(self, module_id, stars):
        module_id = _catalog_id(module_id)
        stars = int(stars)
        if not 1 <= stars <= 5:
            raise ValueError("Оцінка від 1 до 5")
        return await self._request(
            "POST",
            f"/v1/catalog/modules/{quote(module_id, safe='')}/rating",
            payload={"stars": stars},
        )

    async def api_catalog_updates(self, installed):
        clean = {_catalog_id(key): _version(value) for key, value in installed.items()}
        return await self._request(
            "POST", "/v1/catalog/updates", payload={"modules": clean}
        )

    async def api_send_direct(self, instance_id, text):
        instance_id = _instance_identifier(instance_id)
        text = str(text or "").strip()
        if not text or len(text) > _DIRECT_TEXT_LIMIT:
            raise ValueError(f"Текст від 1 до {_DIRECT_TEXT_LIMIT} символів")
        return await self._request(
            "POST", "/v1/inbox", payload={"to": instance_id, "text": text}
        )

    async def api_inbox(self, after_id=0, limit=50):
        return await self._request(
            "GET",
            "/v1/inbox",
            params={
                "after_id": max(0, int(after_id)),
                "limit": max(1, min(int(limit), 200)),
            },
        )

    # Modules installed from the catalog: {module_id: {version, class_name, ...}}
    def _installed_catalog(self):
        value = self.get("catalog_installed", {}) or {}
        return dict(value) if isinstance(value, dict) else {}

    def _installed_version(self, module_id):
        entry = self._installed_catalog().get(module_id)
        if not isinstance(entry, dict):
            return None
        class_name = entry.get("class_name") or ""
        instance = self.lookup(class_name) if class_name else None
        if class_name and not instance:
            return None  # unloaded with .unloadmod since then
        # ModuleHub or .dlmod may have updated the module since; trust the code.
        loaded = getattr(instance, "__version__", None)
        if isinstance(loaded, (tuple, list)) and 2 <= len(loaded) <= 4:
            if all(isinstance(part, int) for part in loaded):
                return ".".join(map(str, loaded))
        return entry.get("version")

    async def install_from_catalog(self, module_id, version=None, target=None):
        """Download a catalog module and load it through Hikka's own loader.

        ``target`` is the message or inline call the loader reports into.
        Returns the source metadata dict on success.
        """
        module_id = _catalog_id(module_id)
        data = await self.api_catalog_source(module_id, version)
        source = data.get("source")
        if not isinstance(source, str) or not source:
            raise HubClientError("Сервер не повернув код модуля", code="invalid_response")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, str(data.get("sha256") or "")):
            raise HubClientError(
                "Контрольна сума модуля не збігається — встановлення скасовано",
                code="checksum_mismatch",
            )
        loader_module = self.lookup("loader")
        load = getattr(loader_module, "load_module", None)
        if load is None:
            raise HubClientError("Завантажувач модулів Hikka недоступний", code="no_loader")

        class_name = str(data.get("class_name") or "")
        previous = self.lookup(class_name) if class_name else None
        await load(source, target, origin="<string>", save_fs=True)
        current = self.lookup(class_name) if class_name else None
        if not current or current is previous:
            raise HubClientError(
                "Hikka не змогла завантажити модуль. Подробиці — у повідомленні "
                "завантажувача або в логах.",
                code="load_failed",
            )

        installed = self._installed_catalog()
        installed[module_id] = {
            "version": data.get("version"),
            "class_name": class_name,
            "sha256": digest,
            "installed_at": int(time.time()),
        }
        self.set("catalog_installed", installed)
        with contextlib.suppress(Exception):
            await self._request(
                "POST",
                f"/v1/catalog/modules/{quote(module_id, safe='')}/installs",
                payload={"version": data.get("version")},
            )
        return data

    async def _poll_notifications(self):
        topics = {
            _identifier(item, "тема")
            for item in list(self.config["event_topics"] or [])
            if str(item).strip()
        }
        after_id = int(self.get("last_event_id", 0) or 0)
        own = str(self.config["instance_id"]).strip()
        notifications = []
        # One cursor for all topics, filtered locally: separate per-topic queries
        # sharing a cursor used to skip events of busier topics.
        for _ in range(5):
            data = await self._request(
                "GET", "/v1/events", params={"after_id": after_id, "limit": 100}
            )
            events = data.get("events", [])
            for event in events:
                after_id = max(after_id, int(event.get("id", 0)))
                topic = str(event.get("topic") or "")
                if event.get("sender_instance_id") == own:
                    continue
                if topics and topic not in topics:
                    continue
                notifications.append(event)
            if len(events) < 100:
                break
        self.set("last_event_id", after_id)
        for event in notifications[-10:]:
            text = (
                "🌐 <b>HikkaNet · нова подія</b>\n"
                f"Тема: <code>{_esc(event.get('topic'))}</code>\n"
                f"Від: <code>{_esc(event.get('sender_instance_id'))}</code>\n"
                f"<code>{_esc(_preview(event.get('payload')))}</code>"
            )
            await self._client.send_message("me", text, parse_mode="html")

    def _direct_text(self, item):
        sender = item.get("sender_instance_id")
        name = item.get("sender_display_name") or sender
        return (
            f"✉️ <b>HikkaNet · повідомлення від {_esc(name)}</b>\n"
            f"<code>{_esc(sender)}</code> · "
            + time.strftime("%d.%m %H:%M", time.localtime(int(item.get("created_at", 0))))
            + f"\n<blockquote>{_esc(item.get('text'))}</blockquote>\n"
            f"Відповісти: <code>.hkdm {_esc(sender)} </code>"
        )

    async def _poll_direct(self):
        after_id = int(self.get("last_direct_id", 0) or 0)
        data = await self.api_inbox(after_id, limit=50)
        messages = data.get("messages", [])
        if not messages:
            return
        self.set("last_direct_id", int(data.get("next_after_id", after_id)))
        if after_id == 0 and len(messages) > 5:
            # First run on this node: do not flood Saved Messages with history.
            messages = messages[-5:]
        for item in messages:
            await self._client.send_message("me", self._direct_text(item), parse_mode="html")

    async def _check_catalog_updates(self, notify=True):
        installed = {
            module_id: entry["version"]
            for module_id, entry in self._installed_catalog().items()
            if isinstance(entry, dict)
            and _VERSION_RE.fullmatch(str(entry.get("version") or ""))
            and self._installed_version(module_id)
        }
        if not installed:
            return []
        updates = (await self.api_catalog_updates(installed)).get("updates", [])
        if not notify:
            return updates
        notified = dict(self.get("catalog_notified", {}) or {})
        for item in updates:
            module_id = item.get("module_id")
            latest = item.get("latest_version")
            if notified.get(module_id) == latest:
                continue
            notified[module_id] = latest
            warnings = [code for code in item.get("warnings", []) if code in _CONFIRM_WARNINGS]
            if self.config["catalog_auto_update"] and (item.get("official") or not warnings):
                status = await self._client.send_message(
                    "me",
                    f"🔄 <b>HikkaNet</b> оновлює <code>{_esc(module_id)}</code> "
                    f"до v{_esc(latest)}…",
                    parse_mode="html",
                )
                try:
                    await self.install_from_catalog(module_id, latest, status)
                except Exception as exc:
                    await self._client.send_message(
                        "me",
                        f"❌ Не вдалося оновити <code>{_esc(module_id)}</code>: "
                        f"<code>{_esc(self._safe_error(exc))}</code>",
                        parse_mode="html",
                    )
                continue
            await self._client.send_message(
                "me", self._update_notice(item), parse_mode="html"
            )
        self.set("catalog_notified", notified)
        return updates

    @staticmethod
    def _update_notice(item):
        lines = [
            "🧩 <b>HikkaNet · оновлення модуля</b>",
            f"<b>{_esc(item.get('title'))}</b> <code>{_esc(item.get('module_id'))}</code>",
            f"v{_esc(item.get('installed_version'))} → <b>v{_esc(item.get('latest_version'))}</b>",
        ]
        if item.get("changelog"):
            lines.append(f"<blockquote>{_esc(item['changelog'])}</blockquote>")
        lines.extend(_warning_lines(item.get("warnings")))
        lines.append(f"Оновити: <code>.hkupdate {_esc(item.get('module_id'))}</code>")
        return "\n".join(lines)

    def _panel_markup(self):
        return [
            [
                {"text": "🧩 Модулі", "callback": self._catalog_callback},
                {"text": "✉️ Пошта", "callback": self._inbox_callback},
            ],
            [
                {"text": "🖥 Вузли", "callback": self._nodes_callback},
                {"text": "📊 Статистика", "callback": self._stats_callback},
            ],
            [
                {"text": "🎮 Ігри", "callback": self._games_callback},
                {"text": "💬 Чат", "callback": self._chat_callback},
            ],
            [
                {"text": "📨 Події", "callback": self._events_callback},
                {"text": "🔄 Оновити", "callback": self._status_callback},
            ],
            [{"text": "✖️ Закрити", "action": "close"}],
        ]

    def _back_markup(self, refresh=None, *args):
        row = [{"text": "◀️ Меню", "callback": self._status_callback}]
        if refresh is not None:
            row.append({"text": "🔄", "callback": refresh, "args": args})
        return [row]

    def _status_text(self, remote=None):
        configured = self._configured()
        connected = self._connected and configured
        if connected:
            state = f"🟢 <b>Підключено</b> · {self._last_latency_ms} мс"
        elif configured:
            state = "🟠 <b>Немає зв'язку з Hikka Hub</b>"
        else:
            state = "⚪️ <b>Не налаштовано</b>"
        lines = ["🔐 <b>HikkaNet</b>", state]
        if configured:
            details = [
                f"🌐 Сервер: <code>{_esc(self.config['server_url'])}</code>",
                f"🆔 Вузол: <code>{_esc(self.config['instance_id'])}</code>",
            ]
            if remote:
                details.append(
                    f"👤 Як: <b>{_esc(remote.get('display_name') or remote.get('instance_id'))}</b>"
                )
                details.append(
                    f"🛰 Hikka Hub <b>v{_esc(remote.get('server_version') or '?')}</b> · "
                    f"API {int(remote.get('api_version', 1) or 1)}"
                )
            if self._last_success:
                details.append(
                    "🕒 Зв'язок: "
                    + time.strftime("%d.%m %H:%M:%S", time.localtime(self._last_success))
                )
            installed = sum(
                1 for module_id in self._installed_catalog() if self._installed_version(module_id)
            )
            details.append(f"🧩 Модулів із каталогу: <b>{installed}</b>")
            lines.append("<blockquote>" + "\n".join(details) + "</blockquote>")
        else:
            lines.extend(["", self.strings["not_configured"]])
        if self._last_error:
            lines.append(f"❗️ <code>{_esc(self._last_error)}</code>")
        if configured and str(self.config["server_url"]).startswith("http://"):
            lines.append(
                "<i>⚠️ HTTP не шифрує дані — для зовнішньої мережі "
                "використовуй HTTPS або VPN.</i>"
            )
        return "\n".join(lines)

    async def _status_remote(self):
        if not self._configured():
            return None
        try:
            await self._heartbeat()
            return await self._request("GET", "/v1/me")
        except Exception as exc:
            self._connected = False
            self._last_error = self._safe_error(exc)
            return None

    async def _nodes_text(self):
        data = await self._request("GET", "/v1/instances")
        nodes = data.get("instances", [])
        lines = ["<b>🖥 HikkaNet · вузли</b>", ""]
        if not nodes:
            return "\n".join(lines + ["Немає зареєстрованих вузлів."])
        for node in nodes[:30]:
            icon = "🟢" if node.get("online") else "⚫️"
            lines.append(
                f"{icon} <b>{_esc(node.get('display_name') or node.get('instance_id'))}</b> "
                f"<code>{_esc(node.get('instance_id'))}</code>"
            )
        online = sum(1 for node in nodes if node.get("online"))
        lines.extend(["", f"Онлайн: <b>{online}</b> / {len(nodes)}"])
        return "\n".join(lines)

    async def _overview_text(self):
        data = await self._request("GET", "/v1/stats")
        instances = data.get("instances", {})
        today = data.get("today", {})
        games = data.get("games", {})
        chat = data.get("chat", {})
        catalog = data.get("catalog") or {}
        return (
            "<b>📊 HikkaNet · статистика</b>\n\n"
            f"🟢 Онлайн: <b>{int(instances.get('online', 0))}</b> / "
            f"{int(instances.get('total', 0))}\n"
            f"📨 Подій за 24 год: <b>{int(data.get('events_24h', 0))}</b>\n\n"
            "<b>Сьогодні</b>\n"
            f"Запитів: <b>{int(today.get('requests', 0))}</b>\n"
            f"Heartbeat: <b>{int(today.get('heartbeats', 0))}</b>\n"
            f"Опубліковано подій: <b>{int(today.get('events', 0))}</b>\n"
            f"Записів даних: <b>{int(today.get('kv_writes', 0))}</b>\n\n"
            "<b>Спільнота</b>\n"
            f"Активних партій: <b>{int(games.get('active', 0))}</b>\n"
            f"Завершених партій: <b>{int(games.get('finished', 0))}</b>\n"
            f"Гравців: <b>{int(games.get('players', 0))}</b>\n"
            f"Активних чат-кімнат: <b>{int(chat.get('active_rooms', 0))}</b>"
            + (
                "\n\n<b>Каталог модулів</b>\n"
                f"Модулів: <b>{int(catalog.get('modules', 0))}</b>\n"
                f"Встановлень: <b>{int(catalog.get('installs', 0))}</b>"
                if catalog
                else ""
            )
        )

    async def _games_text(self):
        overview, leaders = await asyncio.gather(
            self.api_stats(),
            self.api_game_leaderboard(sort="wins", limit=10),
        )
        games = overview.get("games", {})
        lines = [
            "🎮 <b>HikkaNet · глобальні ігри</b>",
            "",
            f"Активні: <b>{int(games.get('active', 0))}</b>",
            f"Завершені: <b>{int(games.get('finished', 0))}</b>",
            f"Гравці: <b>{int(games.get('players', 0))}</b>",
            "",
            "<b>Топ за перемогами</b>",
        ]
        for index, item in enumerate(leaders.get("ranking", [])[:10], 1):
            lines.append(
                f"{index}. <b>{_esc(item.get('display_name') or item.get('public_id'))}</b> — "
                f"{int(item.get('wins', 0))}–{int(item.get('losses', 0))}–"
                f"{int(item.get('draws', 0))} · {int(item.get('played', 0))} ігор"
            )
        if not leaders.get("ranking"):
            lines.append("<i>Завершених мережевих ігор ще немає.</i>")
        lines.extend(
            ["", "Відкрити матчі: <code>.netgames</code> у модулі MiniGames."]
        )
        return "\n".join(lines)

    async def _chat_text(self):
        data = await self.api_chat_rooms(limit=20)
        lines = ["💬 <b>HikkaNet · чат-кімнати</b>", ""]
        for room in data.get("rooms", []):
            lines.append(
                f"• <code>#{_esc(room.get('room'))}</code> — "
                f"🟢 {int(room.get('online', 0))} · "
                f"💬 {int(room.get('messages_24h', 0))}/24г"
            )
        if not data.get("rooms"):
            lines.append("<i>Активних кімнат ще немає.</i>")
        lines.extend(["", "Відкрити чат: <code>.hkchat</code>."])
        return "\n".join(lines)

    async def _events_text(self, topic=None, advance=False):
        after_id = int(self.get("last_event_id", 0) or 0)
        data = await self._request(
            "GET",
            "/v1/events",
            params={"after_id": after_id, "limit": 20, "topic": topic},
        )
        events = data.get("events", [])
        if advance and events:
            self.set("last_event_id", max(int(item.get("id", 0)) for item in events))
        lines = ["<b>📨 HikkaNet · нові події</b>", ""]
        if not events:
            return "\n".join(lines + ["Нових подій немає."])
        for event in events[-10:]:
            lines.append(
                f"<b>#{int(event.get('id', 0))}</b> · "
                f"<code>{_esc(event.get('topic'))}</code> · "
                f"{_esc(event.get('sender_instance_id'))}\n"
                f"<code>{_esc(_preview(event.get('payload'), 180))}</code>"
            )
        return "\n\n".join(lines)

    async def _edit_callback(self, call, producer, refresh=None):
        try:
            text = await producer()
            await call.edit(text, reply_markup=self._back_markup(refresh))
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)

    async def _status_callback(self, call):
        remote = await self._status_remote()
        await call.edit(self._status_text(remote), reply_markup=self._panel_markup())

    async def _nodes_callback(self, call):
        await self._edit_callback(call, self._nodes_text, self._nodes_callback)

    async def _stats_callback(self, call):
        await self._edit_callback(call, self._overview_text, self._stats_callback)

    async def _games_callback(self, call):
        await self._edit_callback(call, self._games_text, self._games_callback)

    async def _chat_callback(self, call):
        await self._edit_callback(call, self._chat_text, self._chat_callback)

    async def _events_callback(self, call):
        await self._edit_callback(
            call, lambda: self._events_text(advance=True), self._events_callback
        )

    # ------------------------------------------------------------------
    # Module catalog UI
    # ------------------------------------------------------------------
    async def _catalog_list_view(
        self, query="", sort="popular", offset=0, mine=False, official=False
    ):
        data = await self.api_catalog_search(
            query,
            sort=sort,
            limit=_CATALOG_PAGE,
            offset=offset,
            mine=mine,
            official=official,
        )
        modules = data.get("modules", [])
        total = int(data.get("total", 0))
        if mine:
            title = "🧩 <b>HikkaNet · мої модулі</b>"
        elif official:
            title = "🏛 <b>HikkaNet · офіційні модулі</b>"
        else:
            title = "🧩 <b>HikkaNet · каталог модулів</b>"
        subtitle = f"{_CATALOG_SORTS[sort]} · знайдено <b>{total}</b>"
        if query:
            subtitle += f" · «{_esc(query)}»"
        lines = [title, subtitle, ""]
        back = (query, sort, offset, mine, official)
        markup = []
        for item in modules:
            module_id = item.get("module_id")
            local = self._installed_version(module_id)
            mark = "✅ " if local else ("🏛 " if item.get("official") else "")
            if mine and item.get("status") != "published":
                mark = "⛔️ "
            lines.append(
                f"{mark}<b>{_esc(item.get('title'))}</b> v{_esc(item.get('latest_version'))}"
                f" · {_stars(item.get('rating'), item.get('rating_count'))}"
                f" · ⬇️ {int(item.get('installs', 0))}"
            )
            description = str(item.get("description") or "")
            if description:
                lines.append(f"<i>{_esc(description[:110])}</i>")
            lines.append("")
            markup.append(
                [
                    {
                        "text": f"{mark}{item.get('title')} · v{item.get('latest_version')}",
                        "callback": self._catalog_card_callback,
                        "args": (module_id, back),
                    }
                ]
            )
        if not modules:
            lines.append(
                "<i>Тут поки порожньо.</i>\nОпублікуй свій модуль: надішли .py файл "
                "із підписом <code>.hkpublish</code> або дай відповідь цією командою на файл."
            )
        pages = []
        if offset > 0:
            pages.append(
                {
                    "text": "◀️",
                    "callback": self._catalog_list_callback,
                    "args": (query, sort, max(0, offset - _CATALOG_PAGE), mine, official),
                }
            )
        if total > _CATALOG_PAGE:
            pages.append(
                {
                    "text": f"{offset // _CATALOG_PAGE + 1}/{-(-total // _CATALOG_PAGE)}",
                    "callback": self._catalog_list_callback,
                    "args": back,
                }
            )
        if offset + _CATALOG_PAGE < total:
            pages.append(
                {
                    "text": "▶️",
                    "callback": self._catalog_list_callback,
                    "args": (query, sort, offset + _CATALOG_PAGE, mine, official),
                }
            )
        if pages:
            markup.append(pages)
        markup.append(
            [
                {
                    "text": label,
                    "callback": self._catalog_list_callback,
                    "args": (query, key, 0, mine, official),
                }
                for key, label in _CATALOG_SORTS.items()
                if key != sort
            ]
        )
        markup.append(
            [
                {"text": "◀️ Меню", "callback": self._status_callback},
                {"text": "⬆️ Оновлення", "callback": self._catalog_updates_callback},
            ]
        )
        markup.append(
            [
                {
                    "text": "🌍 Усі" if official else "🏛 Офіційні",
                    "callback": self._catalog_list_callback,
                    "args": ("", "popular", 0, False, not official),
                },
                {
                    "text": "🌍 Усі" if mine else "👤 Мої",
                    "callback": self._catalog_list_callback,
                    "args": ("", "updated" if not mine else "popular", 0, not mine, False),
                },
            ]
        )
        return "\n".join(lines).rstrip(), markup

    def _catalog_card_text(self, item):
        module_id = item.get("module_id")
        latest = item.get("latest_version")
        local = self._installed_version(module_id)
        lines = [
            f"🧩 <b>{_esc(item.get('title'))}</b> <code>{_esc(module_id)}</code>",
            f"Версія <b>v{_esc(latest)}</b>"
            + (f" · у тебе v{_esc(local)}" if local else "")
            + f" · {_format_size(item.get('size'))}",
        ]
        if item.get("official"):
            lines.append(
                "🏛 <b>Офіційний модуль</b> · автоматично з репозиторію "
                "<code>Splaueef/host</code>"
            )
        if item.get("status") == "blocked":
            lines.append("⛔️ <b>Заблоковано адміністратором Hikka Hub</b>")
        if item.get("description"):
            lines.append(f"<blockquote>{_esc(item['description'])}</blockquote>")
        about = [
            f"{_stars(item.get('rating'), item.get('rating_count'))} · "
            f"⬇️ {int(item.get('installs', 0))} встановлень",
        ]
        if item.get("developer"):
            about.append(f"👨‍💻 Розробник: {_esc(item['developer'])}")
        about.append(
            f"📤 Опублікував: {_esc(item.get('owner_display_name') or item.get('owner_instance_id'))}"
        )
        if item.get("updated_at"):
            about.append(
                "🕒 Оновлено: "
                + time.strftime("%d.%m.%Y", time.localtime(int(item["updated_at"])))
            )
        if item.get("tags"):
            about.append("🏷 " + " ".join(f"#{_esc(tag)}" for tag in item["tags"]))
        lines.append("\n".join(about))
        commands = item.get("commands") or []
        if commands:
            shown = ", ".join(f"<code>.{_esc(name)}</code>" for name in commands[:15])
            more = f" і ще {len(commands) - 15}" if len(commands) > 15 else ""
            lines.append(f"⌨️ Команди: {shown}{more}")
        if item.get("changelog"):
            lines.append(f"📝 Що нового:\n<blockquote>{_esc(item['changelog'][:500])}</blockquote>")
        warnings = item.get("warnings") or []
        if warnings:
            lines.append(
                "🛡 <b>Перевірка коду:</b>\n" + "\n".join(_warning_lines(warnings))
            )
        else:
            lines.append("🛡 Перевірка коду: <b>підозрілих викликів не знайдено</b>")
        if not item.get("official"):
            lines.append(
                "<i>Модулі публікують інші користувачі. "
                "Встановлюй лише те, чому довіряєш.</i>"
            )
        return "\n\n".join(lines)

    def _catalog_card_markup(self, item, back):
        module_id = item.get("module_id")
        latest = item.get("latest_version")
        local = self._installed_version(module_id)
        if not local:
            install_text = f"⬇️ Встановити v{latest}"
        elif _version_tuple(latest) > _version_tuple(local):
            install_text = f"🔄 Оновити v{local} → v{latest}"
        else:
            install_text = "♻️ Перевстановити"
        markup = []
        if item.get("status") == "published":
            markup.append(
                [
                    {
                        "text": install_text,
                        "callback": self._catalog_install_callback,
                        "args": (module_id, latest, back, False),
                    }
                ]
            )
        if not item.get("is_owner") and item.get("status") == "published":
            mine = int(item.get("my_rating") or 0)
            markup.append(
                [
                    {
                        "text": ("★" if stars <= mine else "☆") + str(stars),
                        "callback": self._catalog_rate_callback,
                        "args": (module_id, stars, back),
                    }
                    for stars in range(1, 6)
                ]
            )
        if item.get("is_owner"):
            markup.append(
                [
                    {
                        "text": "🗑 Зняти з каталогу",
                        "callback": self._catalog_delete_callback,
                        "args": (module_id, back, False),
                    }
                ]
            )
        markup.append(
            [
                {"text": "◀️ До списку", "callback": self._catalog_list_callback, "args": back},
                {"text": "🔄", "callback": self._catalog_card_callback, "args": (module_id, back)},
            ]
        )
        return markup

    async def _catalog_list_callback(
        self, call, query="", sort="popular", offset=0, mine=False, official=False
    ):
        try:
            text, markup = await self._catalog_list_view(query, sort, offset, mine, official)
            await call.edit(text, reply_markup=markup)
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)

    async def _catalog_callback(self, call):
        await self._catalog_list_callback(call)

    async def _catalog_card_callback(self, call, module_id, back=("", "popular", 0, False)):
        try:
            item = await self.api_catalog_get(module_id)
            await call.edit(
                self._catalog_card_text(item),
                reply_markup=self._catalog_card_markup(item, back),
            )
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)

    async def _catalog_install_callback(self, call, module_id, version, back, confirmed):
        try:
            if not confirmed:
                item = await self.api_catalog_get(module_id)
                entry = next(
                    (v for v in item.get("versions", []) if v.get("version") == version),
                    item,
                )
                risky = [c for c in entry.get("warnings", []) if c in _CONFIRM_WARNINGS]
                if risky and not item.get("official"):
                    await call.edit(
                        f"⚠️ <b>{_esc(item.get('title'))} v{_esc(version)}</b>\n\n"
                        "Автоматична перевірка знайшла в коді:\n"
                        + "\n".join(_warning_lines(risky))
                        + "\n\nЦе не обов'язково шкідливо, але такий модуль може "
                        "отримати повний доступ до акаунта чи сервера. Встановлювати?",
                        reply_markup=[
                            [
                                {
                                    "text": "✅ Так, встановити",
                                    "callback": self._catalog_install_callback,
                                    "args": (module_id, version, back, True),
                                }
                            ],
                            [
                                {
                                    "text": "◀️ Скасувати",
                                    "callback": self._catalog_card_callback,
                                    "args": (module_id, back),
                                }
                            ],
                        ],
                    )
                    return
            await call.edit(f"⏳ Встановлюю <code>{_esc(module_id)}</code> v{_esc(version)}…")
            await self.install_from_catalog(module_id, version, call)
        except Exception as exc:
            await call.edit(
                f"❌ <b>Не вдалося встановити</b> <code>{_esc(module_id)}</code>\n"
                f"<code>{_esc(self._safe_error(exc))}</code>",
                reply_markup=[
                    [
                        {
                            "text": "◀️ До модуля",
                            "callback": self._catalog_card_callback,
                            "args": (module_id, back),
                        }
                    ]
                ],
            )

    async def _catalog_rate_callback(self, call, module_id, stars, back):
        try:
            result = await self.api_catalog_rate(module_id, stars)
            await call.answer(
                f"Дякуємо! Оцінка {stars}★ · середня {float(result.get('rating', 0)):.1f}"
            )
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)
            return
        await self._catalog_card_callback(call, module_id, back)

    async def _catalog_delete_callback(self, call, module_id, back, confirmed):
        if not confirmed:
            await call.edit(
                f"🗑 Зняти <code>{_esc(module_id)}</code> з каталогу разом з усіма "
                "версіями? Уже встановлені копії в інших користувачів залишаться.",
                reply_markup=[
                    [
                        {
                            "text": "🗑 Так, зняти",
                            "callback": self._catalog_delete_callback,
                            "args": (module_id, back, True),
                        },
                        {
                            "text": "◀️ Ні",
                            "callback": self._catalog_card_callback,
                            "args": (module_id, back),
                        },
                    ]
                ],
            )
            return
        try:
            await self.api_catalog_delete(module_id)
            await call.answer("Модуль знято з каталогу")
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)
            return
        await self._catalog_list_callback(call, *back)

    async def _catalog_updates_view(self):
        updates = await self._check_catalog_updates(notify=False)
        lines = ["⬆️ <b>HikkaNet · оновлення модулів</b>", ""]
        markup = []
        if not self._installed_catalog():
            lines.append("<i>Ти ще не встановлював модулі з каталогу.</i>")
        elif not updates:
            lines.append("✅ Усі модулі з каталогу актуальні.")
        for item in updates:
            lines.append(
                f"• <b>{_esc(item.get('title'))}</b> v{_esc(item.get('installed_version'))}"
                f" → <b>v{_esc(item.get('latest_version'))}</b>"
            )
            if item.get("changelog"):
                lines.append(f"  <i>{_esc(item['changelog'][:160])}</i>")
            markup.append(
                [
                    {
                        "text": f"🔄 {item.get('title')} → v{item.get('latest_version')}",
                        "callback": self._catalog_install_callback,
                        "args": (
                            item.get("module_id"),
                            item.get("latest_version"),
                            ("", "popular", 0, False),
                            False,
                        ),
                    }
                ]
            )
        markup.append(
            [
                {"text": "◀️ Каталог", "callback": self._catalog_callback},
                {"text": "🔄", "callback": self._catalog_updates_callback},
            ]
        )
        return "\n".join(lines), markup

    async def _catalog_updates_callback(self, call):
        try:
            text, markup = await self._catalog_updates_view()
            await call.edit(text, reply_markup=markup)
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)

    async def _inbox_text(self):
        after_id, latest = 0, []
        for _ in range(10):
            data = await self.api_inbox(after_id, limit=200)
            messages = data.get("messages", [])
            latest = (latest + messages)[-8:]
            after_id = int(data.get("next_after_id", after_id))
            if len(messages) < 200:
                break
        lines = ["✉️ <b>HikkaNet · пошта</b>", ""]
        if not latest:
            lines.append("<i>Вхідних повідомлень немає.</i>")
        for item in reversed(latest):
            lines.append(
                f"<b>{_esc(item.get('sender_display_name') or item.get('sender_instance_id'))}</b>"
                f" · <code>{_esc(item.get('sender_instance_id'))}</code> · "
                + time.strftime("%d.%m %H:%M", time.localtime(int(item.get("created_at", 0))))
                + f"\n<blockquote>{_esc(str(item.get('text'))[:400])}</blockquote>"
            )
        lines.append("Написати: <code>.hkdm instance_id текст</code>")
        return "\n".join(lines)

    async def _inbox_callback(self, call):
        await self._edit_callback(call, self._inbox_text, self._inbox_callback)

    async def _open_form(self, message, text, markup):
        try:
            if await self.inline.form(text, message, reply_markup=markup):
                return
        except Exception:
            logger.exception("HikkaNet inline form failed")
        await utils.answer(message, text)

    async def _module_file(self, message):
        target = message if getattr(message, "file", None) else await message.get_reply_message()
        file = getattr(target, "file", None) if target else None
        if not file:
            raise ValueError("Надішли .py файл з підписом .hkpublish або дай відповідь на файл")
        name = str(getattr(file, "name", "") or "")
        if name and not name.lower().endswith(".py"):
            raise ValueError("Потрібен файл модуля з розширенням .py")
        if int(getattr(file, "size", 0) or 0) > _MODULE_FILE_LIMIT:
            raise ValueError("Файл модуля завеликий (максимум 1 МБ)")
        raw = await target.download_media(bytes)
        try:
            return raw.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            raise ValueError("Файл модуля має бути в кодуванні UTF-8") from None

    @loader.command(ru_doc="Каталог модулів HikkaNet: пошук і встановлення")
    async def hkmods(self, message):
        """🧩 .hkmods [пошук | official] — каталог модулів HikkaNet"""
        query = utils.get_args_raw(message).strip()
        try:
            if query.lower() in {"official", "офіційні", "host"}:
                text, markup = await self._catalog_list_view(official=True)
            else:
                text, markup = await self._catalog_list_view(
                    query, "popular" if not query else "rating"
                )
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        await self._open_form(message, text, markup)

    @loader.command(ru_doc="Модулі, які ти опублікував у HikkaNet")
    async def hkmymods(self, message):
        """👤 Мої модулі в каталозі HikkaNet"""
        try:
            text, markup = await self._catalog_list_view("", "updated", 0, True)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        await self._open_form(message, text, markup)

    @loader.command(ru_doc="Картка модуля з каталогу HikkaNet")
    async def hkmod(self, message):
        """🔎 .hkmod <id> — картка модуля"""
        raw = utils.get_args_raw(message).strip()
        try:
            item = await self.api_catalog_get(_catalog_id(raw))
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        await self._open_form(
            message,
            self._catalog_card_text(item),
            self._catalog_card_markup(item, ("", "popular", 0, False)),
        )

    @loader.command(ru_doc="Встановити модуль прямо з каталогу HikkaNet")
    async def hkinstall(self, message):
        """⬇️ .hkinstall <id> [версія] [-y]"""
        parts = utils.get_args_raw(message).split()
        confirmed = "-y" in parts
        parts = [part for part in parts if part != "-y"]
        if not 1 <= len(parts) <= 2:
            await utils.answer(message, "Використання: <code>.hkinstall id [версія] [-y]</code>")
            return
        try:
            module_id = _catalog_id(parts[0])
            version = _version(parts[1]) if len(parts) == 2 else None
            item = await self.api_catalog_get(module_id)
            version = version or item.get("latest_version")
            entry = next(
                (v for v in item.get("versions", []) if v.get("version") == version), None
            )
            if entry is None:
                raise ValueError(f"Версії v{version} немає в каталозі")
            risky = [c for c in entry.get("warnings", []) if c in _CONFIRM_WARNINGS]
            if risky and not confirmed and not item.get("official"):
                await utils.answer(
                    message,
                    f"⚠️ <b>{_esc(item.get('title'))} v{_esc(version)}</b>\n"
                    + "\n".join(_warning_lines(risky))
                    + "\n\nЯкщо довіряєш автору, повтори з <code>-y</code>: "
                    f"<code>.hkinstall {_esc(module_id)} {_esc(version)} -y</code>",
                )
                return
            message = await utils.answer(
                message, f"⏳ Встановлюю <code>{_esc(module_id)}</code> v{_esc(version)}…"
            )
            await self.install_from_catalog(module_id, version, message)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Опублікувати модуль у каталозі HikkaNet")
    async def hkpublish(self, message):
        """📤 .hkpublish [#теги] [| що нового] — у відповідь на .py файл"""
        tags, changelog = _parse_publish_args(utils.get_args_raw(message))
        try:
            source = await self._module_file(message)
            result = await self.api_catalog_publish(source, tags or None, changelog)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        action = "Опубліковано" if result.get("created") else (
            f"Оновлено v{_esc(result.get('previous_version'))} →"
        )
        lines = [
            f"✅ <b>{action} v{_esc(result.get('latest_version'))}</b>",
            f"🧩 <b>{_esc(result.get('title'))}</b> · ID <code>{_esc(result.get('module_id'))}</code>",
            f"Встановлення для інших: <code>.hkinstall {_esc(result.get('module_id'))}</code>",
        ]
        if result.get("warnings"):
            lines.append(
                "\n🛡 Користувачі побачать попередження:\n"
                + "\n".join(_warning_lines(result["warnings"]))
            )
        await utils.answer(message, "\n".join(lines))

    @loader.command(ru_doc="Зняти свій модуль або його версію з каталогу")
    async def hkunpublish(self, message):
        """🗑 .hkunpublish <id> [версія]"""
        parts = utils.get_args_raw(message).split()
        if not 1 <= len(parts) <= 2:
            await utils.answer(message, "Використання: <code>.hkunpublish id [версія]</code>")
            return
        try:
            result = await self.api_catalog_delete(
                _catalog_id(parts[0]), _version(parts[1]) if len(parts) == 2 else None
            )
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        if result.get("deleted") == "version":
            text = (
                f"✅ Версію видалено. Актуальна: <b>v{_esc(result.get('latest_version'))}</b>"
            )
        else:
            text = "✅ Модуль знято з каталогу."
        await utils.answer(message, text)

    @loader.command(ru_doc="Перевірити оновлення модулів із каталогу")
    async def hkupdates(self, message):
        """⬆️ Оновлення встановлених із HikkaNet модулів"""
        try:
            text, markup = await self._catalog_updates_view()
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        await self._open_form(message, text, markup)

    @loader.command(ru_doc="Оновити модуль (або всі) з каталогу HikkaNet")
    async def hkupdate(self, message):
        """🔄 .hkupdate <id|all>"""
        raw = utils.get_args_raw(message).strip().lower()
        if not raw:
            await utils.answer(message, "Використання: <code>.hkupdate id</code> або <code>.hkupdate all</code>")
            return
        try:
            updates = await self._check_catalog_updates(notify=False)
            if raw != "all":
                module_id = _catalog_id(raw)
                updates = [item for item in updates if item.get("module_id") == module_id]
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        if not updates:
            await utils.answer(message, "✅ Оновлень немає.")
            return
        results = []
        for item in updates:
            module_id = item["module_id"]
            risky = [c for c in item.get("warnings", []) if c in _CONFIRM_WARNINGS]
            if risky and not item.get("official"):
                results.append(
                    f"⚠️ <code>{_esc(module_id)}</code>: нова версія має попередження — "
                    f"<code>.hkinstall {_esc(module_id)} -y</code>"
                )
                continue
            status = await self._client.send_message(
                utils.get_chat_id(message),
                f"⏳ Оновлюю <code>{_esc(module_id)}</code>…",
                parse_mode="html",
            )
            try:
                await self.install_from_catalog(module_id, item["latest_version"], status)
                results.append(f"✅ <code>{_esc(module_id)}</code> → v{_esc(item['latest_version'])}")
            except Exception as exc:
                results.append(
                    f"❌ <code>{_esc(module_id)}</code>: {_esc(self._safe_error(exc))}"
                )
        await utils.answer(message, "⬆️ <b>Оновлення модулів</b>\n" + "\n".join(results))

    @loader.command(ru_doc="Надіслати особисте повідомлення іншому вузлу HikkaNet")
    async def hkdm(self, message):
        """✉️ .hkdm <instance_id> <текст>"""
        instance_id, _, text = utils.get_args_raw(message).strip().partition(" ")
        if not instance_id or not text.strip():
            await utils.answer(message, "Використання: <code>.hkdm instance_id текст</code>")
            return
        try:
            await self.api_send_direct(instance_id, text)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
            return
        await utils.answer(
            message,
            f"✉️ Надіслано <code>{_esc(instance_id)}</code>:\n<blockquote>{_esc(text.strip())}</blockquote>",
        )

    @loader.command(ru_doc="Останні особисті повідомлення HikkaNet")
    async def hkinbox(self, message):
        """📬 Вхідні повідомлення HikkaNet"""
        try:
            await utils.answer(message, await self._inbox_text())
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Відкрити захищену панель HikkaNet")
    async def hknet(self, message):
        """🔐 Панель мережі Hikka"""
        remote = await self._status_remote()
        try:
            opened = await self.inline.form(
                self._status_text(remote),
                message,
                reply_markup=self._panel_markup(),
            )
            if opened:
                return
        except Exception:
            logger.exception("HikkaNet inline panel failed")
        await utils.answer(message, self.strings["inline_failed"])

    @loader.command(ru_doc="Перевірити стан підключення до Hikka Hub")
    async def hknetstatus(self, message):
        """🔎 Перевірити підключення"""
        remote = await self._status_remote()
        await utils.answer(message, self._status_text(remote))

    @loader.command(ru_doc="Безпечно зберегти видані сервером реквізити")
    async def hknetsetup(self, message):
        """🔑 .hknetsetup <url> <instance_id> <key_id> <secret>"""
        raw = utils.get_args_raw(message)
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = []
        if len(parts) != 4:
            await utils.answer(
                message,
                "Використання: <code>.hknetsetup http://IP:PORT INSTANCE_ID KEY_ID SECRET</code>\n"
                "⚠️ Повідомлення з секретом буде одразу видалене.",
            )
            return
        server_url, instance_id, key_id, secret = parts
        try:
            server_url = _normalise_server_url(server_url)
            if not _INSTANCE_RE.fullmatch(instance_id):
                raise ValueError("Некоректний instance_id")
            if not _KEY_RE.fullmatch(key_id):
                raise ValueError("Некоректний key_id")
            if len(secret) < 32:
                raise ValueError("Секрет закороткий")
        except ValueError as exc:
            await utils.answer(message, f"❌ <code>{_esc(exc)}</code>")
            return

        peer = getattr(message, "peer_id", None) or getattr(message, "chat_id", None)
        with contextlib.suppress(Exception):
            await message.delete()
        self.config["server_url"] = server_url
        self.config["instance_id"] = instance_id
        self.config["key_id"] = key_id
        self.config["key_secret"] = secret
        self._connected = False
        self._last_error = ""
        self._start_worker()
        text = "✅ <b>Ключ HikkaNet збережено.</b> Перевіряю підключення…"
        if peer is not None:
            await self._client.send_message(peer, text, parse_mode="html")
        else:
            await utils.answer(message, text)

    @loader.command(ru_doc="Показати авторизовані Hikka та їх онлайн-стан")
    async def hknetnodes(self, message):
        """🖥 Список вузлів HikkaNet"""
        try:
            await utils.answer(message, await self._nodes_text())
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Огляд сервісу або рейтинг метрики")
    async def hknetstats(self, message):
        """📊 .hknetstats [metric]"""
        metric = utils.get_args_raw(message).strip()
        try:
            if not metric:
                text = await self._overview_text()
            else:
                metric = _identifier(metric, "метрика")
                data = await self._request(
                    "GET", f"/v1/stats/{quote(metric, safe='')}", params={"limit": 20}
                )
                lines = [
                    f"<b>📈 Метрика <code>{_esc(metric)}</code></b>",
                    f"Загалом: <b>{float(data.get('total', 0)):g}</b>",
                    "",
                ]
                for index, item in enumerate(data.get("ranking", []), 1):
                    lines.append(
                        f"{index}. <b>{_esc(item.get('display_name') or item.get('instance_id'))}</b> — "
                        f"{float(item.get('value', 0)):g}"
                    )
                if not data.get("ranking"):
                    lines.append("Даних немає.")
                text = "\n".join(lines)
            await utils.answer(message, text)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Глобальна статистика ігор HikkaNet")
    async def hknetgames(self, message):
        """🎮 Глобальні партії та рейтинг HikkaNet"""
        try:
            await utils.answer(message, await self._games_text())
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Активні кімнати HikkaNetChat")
    async def hknetrooms(self, message):
        """💬 Активні чат-кімнати мережі"""
        try:
            await utils.answer(message, await self._chat_text())
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Опублікувати мережеву подію")
    async def hknetpublish(self, message):
        """📨 .hknetpublish <topic> <JSON або текст>"""
        raw = utils.get_args_raw(message).strip()
        topic, separator, payload_raw = raw.partition(" ")
        if not separator:
            await utils.answer(message, "Використання: <code>.hknetpublish topic текст/JSON</code>")
            return
        try:
            topic = _identifier(topic, "тема")
            payload = _json_or_text(payload_raw)
            sensitive = _find_sensitive_field(payload)
            if sensitive:
                raise ValueError(f"Схоже на секретне поле: {sensitive}")
            result = await self._request(
                "POST",
                "/v1/events",
                payload={"topic": topic, "payload": payload, "ttl_seconds": 86400},
            )
            await utils.answer(
                message,
                f"✅ Подію <b>#{int(result.get('id', 0))}</b> опубліковано у "
                f"<code>{_esc(topic)}</code>.",
            )
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Прочитати нові мережеві події")
    async def hknetevents(self, message):
        """📨 .hknetevents [topic]"""
        topic = utils.get_args_raw(message).strip()
        try:
            topic = _identifier(topic, "тема") if topic else None
            await utils.answer(message, await self._events_text(topic, advance=True))
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Записати спільне JSON/текстове значення")
    async def hknetput(self, message):
        """🗃 .hknetput <namespace> <key> <JSON або текст>"""
        raw = utils.get_args_raw(message).strip()
        parts = raw.split(maxsplit=2)
        if len(parts) != 3:
            await utils.answer(
                message, "Використання: <code>.hknetput namespace key значення</code>"
            )
            return
        try:
            namespace = _identifier(parts[0], "namespace")
            item_key = _identifier(parts[1], "key")
            value = _json_or_text(parts[2])
            sensitive = _find_sensitive_field(value)
            if sensitive:
                raise ValueError(f"Схоже на секретне поле: {sensitive}")
            result = await self._request(
                "PUT",
                f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}",
                payload={"value": value},
            )
            await utils.answer(
                message,
                f"✅ <code>{_esc(namespace)}/{_esc(item_key)}</code> збережено · "
                f"revision <b>{int(result.get('revision', 0))}</b>.",
            )
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Прочитати спільне значення або namespace")
    async def hknetget(self, message):
        """🗂 .hknetget <namespace> [key]"""
        parts = utils.get_args_raw(message).split()
        if not 1 <= len(parts) <= 2:
            await utils.answer(message, "Використання: <code>.hknetget namespace [key]</code>")
            return
        try:
            namespace = _identifier(parts[0], "namespace")
            if len(parts) == 2:
                item_key = _identifier(parts[1], "key")
                item = await self._request(
                    "GET", f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}"
                )
                text = (
                    f"<b>🗃 {_esc(namespace)}/{_esc(item_key)}</b> · rev "
                    f"{int(item.get('revision', 0))}\n"
                    f"Власник: <code>{_esc(item.get('owner_instance_id'))}</code>\n\n"
                    f"<code>{_esc(_preview(item.get('value'), 2500))}</code>"
                )
            else:
                data = await self._request("GET", f"/v1/kv/{quote(namespace, safe='')}")
                lines = [f"<b>🗂 Namespace <code>{_esc(namespace)}</code></b>", ""]
                for item in data.get("items", [])[:50]:
                    lines.append(
                        f"• <code>{_esc(item.get('item_key'))}</code> · rev "
                        f"{int(item.get('revision', 0))} · {_esc(item.get('owner_instance_id'))}"
                    )
                if not data.get("items"):
                    lines.append("Порожньо.")
                text = "\n".join(lines)
            await utils.answer(message, text)
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Видалити власне спільне значення")
    async def hknetdel(self, message):
        """🗑 .hknetdel <namespace> <key>"""
        parts = utils.get_args_raw(message).split()
        if len(parts) != 2:
            await utils.answer(message, "Використання: <code>.hknetdel namespace key</code>")
            return
        try:
            namespace = _identifier(parts[0], "namespace")
            item_key = _identifier(parts[1], "key")
            await self._request(
                "DELETE", f"/v1/kv/{quote(namespace, safe='')}/{quote(item_key, safe='')}"
            )
            await utils.answer(message, "✅ Значення видалено.")
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")

    @loader.command(ru_doc="Збільшити власну числову метрику")
    async def hknetinc(self, message):
        """➕ .hknetinc <metric> [delta]"""
        parts = utils.get_args_raw(message).split()
        if not 1 <= len(parts) <= 2:
            await utils.answer(message, "Використання: <code>.hknetinc metric [delta]</code>")
            return
        try:
            metric = _identifier(parts[0], "метрика")
            delta = float(parts[1]) if len(parts) == 2 else 1.0
            if not math.isfinite(delta) or not 0 < delta <= 10000:
                raise ValueError("delta повинен бути > 0 та ≤ 10000")
            data = await self._request(
                "POST",
                f"/v1/metrics/{quote(metric, safe='')}/increment",
                payload={"delta": delta},
            )
            await utils.answer(
                message,
                f"✅ <code>{_esc(metric)}</code>: <b>{float(data.get('value', 0)):g}</b>",
            )
        except Exception as exc:
            await utils.answer(message, f"❌ <code>{_esc(self._safe_error(exc))}</code>")
