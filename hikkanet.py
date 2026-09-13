# meta developer: @Huai_Baike
# meta version: 1.1.0
# meta description: 🔐 Захищена мережа обміну даними між окремими Hikka.
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
__version__ = (1, 1, 0)
_PROTOCOL = "HIKKA-HUB-V1"
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
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
                headers={"User-Agent": "HikkaNet/1.1 (Hikka module)"}
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
                if self.config["notify_events"]:
                    await self._poll_notifications()
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

    @staticmethod
    def _safe_error(exc):
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

    async def _request(self, method, path, params=None, payload=None):
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
                raw = await response.content.read(524289)
                if len(raw) > 524288:
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
                    "presence",
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

    async def _poll_notifications(self):
        topics = [
            _identifier(item, "тема")
            for item in list(self.config["event_topics"] or [])
            if str(item).strip()
        ]
        after_id = int(self.get("last_event_id", 0) or 0)
        # The API accepts one topic at a time. Empty config intentionally means all.
        topic_queries = topics or [None]
        newest = after_id
        notifications = []
        for topic in topic_queries:
            data = await self._request(
                "GET",
                "/v1/events",
                params={"after_id": after_id, "limit": 30, "topic": topic},
            )
            for event in data.get("events", []):
                newest = max(newest, int(event.get("id", 0)))
                if event.get("sender_instance_id") != self.config["instance_id"]:
                    notifications.append(event)
        if newest > after_id:
            self.set("last_event_id", newest)
        for event in sorted(notifications, key=lambda item: int(item.get("id", 0)))[:10]:
            text = (
                "🌐 <b>HikkaNet · нова подія</b>\n"
                f"Тема: <code>{_esc(event.get('topic'))}</code>\n"
                f"Від: <code>{_esc(event.get('sender_instance_id'))}</code>\n"
                f"<code>{_esc(_preview(event.get('payload')))}</code>"
            )
            await self._client.send_message("me", text, parse_mode="html")

    def _panel_markup(self):
        return [
            [
                {"text": "🔄 Оновити", "callback": self._status_callback},
                {"text": "🖥 Вузли", "callback": self._nodes_callback},
            ],
            [
                {"text": "📊 Статистика", "callback": self._stats_callback},
                {"text": "📨 Події", "callback": self._events_callback},
            ],
            [{"text": "✖️ Закрити", "action": "close"}],
        ]

    def _status_text(self, remote=None):
        configured = self._configured()
        connected = self._connected and configured
        state = "🟢 Підключено" if connected else "🔴 Не підключено"
        lines = ["<b>🔐 HikkaNet</b>", "", f"Стан: <b>{state}</b>"]
        if configured:
            lines.extend(
                [
                    f"Сервер: <code>{_esc(self.config['server_url'])}</code>",
                    f"Вузол: <code>{_esc(self.config['instance_id'])}</code>",
                    f"Затримка: <b>{self._last_latency_ms} мс</b>",
                ]
            )
            if self._last_success:
                lines.append(
                    "Останній зв'язок: <code>"
                    + time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(self._last_success))
                    + "</code>"
                )
            if remote:
                lines.append(
                    f"Авторизація: <b>{_esc(remote.get('display_name') or remote.get('instance_id'))}</b>"
                )
        else:
            lines.extend(["", self.strings["not_configured"]])
        if self._last_error:
            lines.extend(["", f"Помилка: <code>{_esc(self._last_error)}</code>"])
        if configured and str(self.config["server_url"]).startswith("http://"):
            lines.extend(
                [
                    "",
                    "⚠️ <i>HMAC захищає цілісність запиту, але HTTP не шифрує дані. "
                    "Для зовнішньої мережі використовуй HTTPS або VPN.</i>",
                ]
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
        return (
            "<b>📊 HikkaNet · статистика</b>\n\n"
            f"🟢 Онлайн: <b>{int(instances.get('online', 0))}</b> / "
            f"{int(instances.get('total', 0))}\n"
            f"📨 Подій за 24 год: <b>{int(data.get('events_24h', 0))}</b>\n\n"
            "<b>Сьогодні</b>\n"
            f"Запитів: <b>{int(today.get('requests', 0))}</b>\n"
            f"Heartbeat: <b>{int(today.get('heartbeats', 0))}</b>\n"
            f"Опубліковано подій: <b>{int(today.get('events', 0))}</b>\n"
            f"Записів даних: <b>{int(today.get('kv_writes', 0))}</b>"
        )

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

    async def _edit_callback(self, call, producer):
        try:
            text = await producer()
            await call.edit(text, reply_markup=self._panel_markup())
        except Exception as exc:
            await call.answer(self._safe_error(exc), show_alert=True)

    async def _status_callback(self, call):
        remote = await self._status_remote()
        await call.edit(self._status_text(remote), reply_markup=self._panel_markup())

    async def _nodes_callback(self, call):
        await self._edit_callback(call, self._nodes_text)

    async def _stats_callback(self, call):
        await self._edit_callback(call, self._overview_text)

    async def _events_callback(self, call):
        await self._edit_callback(call, lambda: self._events_text(advance=True))

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
