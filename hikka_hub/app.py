"""aiohttp application for the standalone Hikka Hub service."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import math
import os
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Optional
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from . import __version__
from .config import Settings
from .security import (
    IDENTIFIER_RE,
    INSTANCE_ID_RE,
    body_sha256,
    parse_authorization,
    sign_with_key,
)
from .storage import Store


logger = logging.getLogger("hikka_hub")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
PUBLIC_PATHS = {"/", "/health"}
MODULE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/Splaueef/host/main/module_versions.json"
)
MODULE_REPOSITORY = "Splaueef/host"
MODULE_STATE_KEY = "module_versions"
MODULE_UPDATE_TOPIC = "system.module_updates"
MODULE_MANIFEST_LIMIT = 131072
MODULE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_]+\.py$")
MODULE_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window_seconds - now))
                return False, retry_after
            hits.append(now)
            return True, 0


SETTINGS_KEY = web.AppKey("settings", Settings)
STORE_KEY = web.AppKey("store", Store)
LIMITER_KEY = web.AppKey("limiter", SlidingWindowLimiter)
PREAUTH_LIMITER_KEY = web.AppKey("preauth_limiter", SlidingWindowLimiter)


def _response(data: Any, status: int = 200, **headers: str) -> web.Response:
    return web.json_response(
        {"ok": status < 400, "data": data} if status < 400 else data,
        status=status,
        headers=headers,
        dumps=lambda value: json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ),
    )


@web.middleware
async def error_middleware(request: web.Request, handler):
    request_id = uuid.uuid4().hex[:16]
    request["request_id"] = request_id
    try:
        response = await handler(request)
    except ApiError as exc:
        response = _response(
            {"ok": False, "error": {"code": exc.code, "message": exc.message}},
            status=exc.status,
        )
    except web.HTTPException as exc:
        response = _response(
            {
                "ok": False,
                "error": {"code": "http_error", "message": exc.reason or "HTTP error"},
            },
            status=exc.status,
        )
    except Exception:
        logger.exception("Unhandled request error id=%s path=%s", request_id, request.path)
        response = _response(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": f"Internal error. Request ID: {request_id}",
                },
            },
            status=500,
        )
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _remote_ip(request: web.Request) -> str:
    # Forwarded headers are deliberately ignored. Put the service behind a trusted
    # reverse proxy/firewall if the original client address is required.
    return (request.remote or "unknown")[:64]


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in PUBLIC_PATHS:
        return await handler(request)
    if not request.path.startswith("/v1/"):
        raise ApiError(404, "not_found", "Route not found")

    settings = request.app[SETTINGS_KEY]
    preauth_allowed, preauth_retry = await request.app[PREAUTH_LIMITER_KEY].allow(
        _remote_ip(request)
    )
    if not preauth_allowed:
        raise ApiError(429, "rate_limited", f"Retry after {preauth_retry} seconds")
    if (
        request.content_length is not None
        and request.content_length > settings.request_body_limit
    ):
        raise ApiError(413, "body_too_large", "Request body is too large")
    body = await request.read()
    if len(body) > settings.request_body_limit:
        raise ApiError(413, "body_too_large", "Request body is too large")

    try:
        authorization = parse_authorization(request.headers.get("Authorization", ""))
        timestamp_text = request.headers["X-Hikka-Timestamp"]
        timestamp = int(timestamp_text)
        nonce = request.headers["X-Hikka-Nonce"]
        instance_id = request.headers["X-Hikka-Instance"]
        owner_id_text = request.headers["X-Hikka-Owner"]
        owner_id = int(owner_id_text)
        supplied_hash = request.headers["X-Hikka-Content-SHA256"].lower()
    except (KeyError, TypeError, ValueError):
        raise ApiError(401, "authentication_failed", "Authentication failed") from None

    now = int(time.time())
    if abs(now - timestamp) > settings.max_clock_skew:
        raise ApiError(401, "clock_skew", "Client clock is outside the allowed window")
    if not NONCE_RE.fullmatch(nonce) or not INSTANCE_ID_RE.fullmatch(instance_id):
        raise ApiError(401, "authentication_failed", "Authentication failed")
    calculated_hash = body_sha256(body)
    if not hmac.compare_digest(calculated_hash, supplied_hash):
        raise ApiError(401, "body_hash_mismatch", "Request body hash does not match")

    store = request.app[STORE_KEY]
    credential = await store.get_credential(authorization.key_id)
    if (
        not credential
        or not credential["enabled"]
        or credential["instance_id"] != instance_id
        or int(credential["owner_id"]) != owner_id
    ):
        raise ApiError(401, "authentication_failed", "Authentication failed")

    try:
        expected = sign_with_key(
            bytes.fromhex(credential["derived_key_hex"]),
            request.method,
            request.raw_path,
            timestamp_text,
            nonce,
            instance_id,
            owner_id_text,
            supplied_hash,
        )
    except (ValueError, TypeError):
        raise ApiError(401, "authentication_failed", "Authentication failed") from None
    if not hmac.compare_digest(expected, authorization.signature):
        raise ApiError(401, "authentication_failed", "Authentication failed")

    nonce_ok = await store.reserve_nonce(
        authorization.key_id, nonce, now + settings.max_clock_skew + 5
    )
    if not nonce_ok:
        raise ApiError(409, "replayed_request", "This signed request was already used")

    allowed, retry_after = await request.app[LIMITER_KEY].allow(authorization.key_id)
    if not allowed:
        raise ApiError(429, "rate_limited", f"Retry after {retry_after} seconds")

    request["credential"] = credential
    await store.record_usage(instance_id, "requests")
    return await handler(request)


async def _json_object(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json(loads=json.loads)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ApiError(400, "invalid_json", "Body must contain valid JSON") from None
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_json", "JSON body must be an object")
    _reject_non_finite(payload)
    return payload


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ApiError(400, "invalid_json", "NaN and Infinity are not supported")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite(item)


def _bounded_text(value: Any, field: str, maximum: int, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_field", f"{field} must be a string")
    value = value.strip()
    if (not allow_empty and not value) or len(value) > maximum:
        raise ApiError(400, "invalid_field", f"Invalid {field}")
    return value


def _identifier(value: Any, field: str) -> str:
    value = _bounded_text(value, field, 64, allow_empty=False).lower()
    if not IDENTIFIER_RE.fullmatch(value):
        raise ApiError(
            400,
            "invalid_identifier",
            f"{field} must match {IDENTIFIER_RE.pattern}",
        )
    return value


def _query_int(
    request: web.Request, name: str, default: int, minimum: int, maximum: int
) -> int:
    try:
        value = int(request.query.get(name, str(default)))
    except ValueError:
        raise ApiError(400, "invalid_query", f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ApiError(400, "invalid_query", f"Invalid {name}")
    return value


def _query_bool(request: web.Request, name: str, default: bool = False) -> bool:
    raw = request.query.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ApiError(400, "invalid_query", f"{name} must be a boolean")


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = tuple(int(item) for item in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _validate_module_manifest(payload: Any) -> dict[str, str]:
    modules = payload.get("modules") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or not isinstance(modules, dict)
        or not 1 <= len(modules) <= 512
    ):
        raise ValueError("unsupported module manifest")
    result = {}
    for filename, version in modules.items():
        if (
            not isinstance(filename, str)
            or not MODULE_FILENAME_RE.fullmatch(filename)
            or not isinstance(version, str)
            or not MODULE_VERSION_RE.fullmatch(version)
        ):
            raise ValueError("invalid module manifest entry")
        result[filename] = version
    return result


async def _fetch_module_manifest(
    session: aiohttp.ClientSession,
) -> dict[str, str]:
    url = f"{MODULE_MANIFEST_URL}?t={int(time.time()) // 300}"
    async with session.get(url, allow_redirects=True) as response:
        final = urlsplit(str(response.url))
        if (
            response.status != 200
            or final.scheme != "https"
            or (final.hostname or "").lower() != "raw.githubusercontent.com"
        ):
            raise RuntimeError(f"module manifest returned HTTP {response.status}")
        raw = await response.content.read(MODULE_MANIFEST_LIMIT + 1)
    if len(raw) > MODULE_MANIFEST_LIMIT:
        raise RuntimeError("module manifest is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("module manifest is not valid JSON") from exc
    try:
        return _validate_module_manifest(payload)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


async def _process_module_manifest(
    app: web.Application,
    modules: dict[str, str],
    detected_at: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Cache one manifest and emit an event only for newer module versions."""
    modules = _validate_module_manifest({"schema": 1, "modules": modules})
    detected_at = int(time.time()) if detected_at is None else int(detected_at)
    state_value = {
        "schema": 1,
        "repository": MODULE_REPOSITORY,
        "modules": modules,
    }
    store = app[STORE_KEY]
    previous_state = await store.get_service_state(MODULE_STATE_KEY)
    if previous_state is None:
        await store.set_service_state(MODULE_STATE_KEY, state_value)
        logger.info("Stored initial module version manifest (%s modules)", len(modules))
        return []

    previous_value = previous_state.get("value", {})
    previous_modules = (
        previous_value.get("modules", {})
        if isinstance(previous_value, dict)
        else {}
    )
    changed = []
    for filename, version in sorted(modules.items()):
        previous_version = previous_modules.get(filename)
        if previous_version is None or (
            isinstance(previous_version, str)
            and MODULE_VERSION_RE.fullmatch(previous_version)
            and _version_tuple(version) > _version_tuple(previous_version)
        ):
            changed.append(
                {
                    "filename": filename,
                    "previous_version": previous_version,
                    "version": version,
                }
            )

    if changed:
        ttl_seconds = app[SETTINGS_KEY].event_retention_hours * 3600
        await store.add_event(
            MODULE_UPDATE_TOPIC,
            "hikka-hub",
            {
                "schema": 1,
                "repository": MODULE_REPOSITORY,
                "changed": changed,
                "detected_at": detected_at,
            },
            ttl_seconds,
        )
        logger.info(
            "Published module update event: %s",
            ", ".join(item["filename"] for item in changed),
        )

    # Persist only after the event is durable. A crash can therefore duplicate an
    # idempotent update notification, but cannot silently lose one.
    await store.set_service_state(MODULE_STATE_KEY, state_value)
    return changed


async def root(request: web.Request) -> web.Response:
    return _response(
        {
            "service": "Hikka Hub",
            "version": __version__,
            "message": "Authenticated Hikka clients only",
        }
    )


async def health(request: web.Request) -> web.Response:
    return _response({"status": "ok", "version": __version__, "time": int(time.time())})


async def me(request: web.Request) -> web.Response:
    credential = request["credential"]
    return _response(
        {
            "instance_id": credential["instance_id"],
            "display_name": credential["display_name"],
            "owner_id": credential["owner_id"],
            "created_at": credential["created_at"],
            "last_seen": credential["last_seen"],
        }
    )


async def heartbeat(request: web.Request) -> web.Response:
    payload = await _json_object(request)
    capabilities_raw = payload.get("capabilities", [])
    if not isinstance(capabilities_raw, list) or len(capabilities_raw) > 32:
        raise ApiError(400, "invalid_field", "capabilities must be a short list")
    capabilities = [_identifier(item, "capability") for item in capabilities_raw]
    hikka_version = _bounded_text(payload.get("hikka_version", ""), "hikka_version", 64)
    module_version = _bounded_text(payload.get("module_version", ""), "module_version", 64)
    display_name = _bounded_text(payload.get("display_name", ""), "display_name", 64)
    credential = request["credential"]
    store = request.app[STORE_KEY]
    await store.touch_instance(
        credential["instance_id"],
        _remote_ip(request),
        hikka_version=hikka_version,
        module_version=module_version,
        capabilities=capabilities,
        display_name=display_name,
    )
    await store.record_usage(credential["instance_id"], "heartbeats")
    return _response({"accepted": True, "server_time": int(time.time())})


async def instances(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    active_within = _query_int(
        request,
        "active_within",
        settings.offline_after_seconds,
        30,
        3600,
    )
    items = await request.app[STORE_KEY].list_instances(active_within)
    return _response({"instances": items, "active_within": active_within})


async def module_versions(request: web.Request) -> web.Response:
    state = await request.app[STORE_KEY].get_service_state(MODULE_STATE_KEY)
    if state is None:
        raise ApiError(
            503,
            "module_manifest_unavailable",
            "Module version manifest has not been loaded yet",
        )
    value = state.get("value")
    try:
        modules = _validate_module_manifest(value)
    except ValueError:
        logger.error("Cached module version manifest is invalid")
        raise ApiError(
            503,
            "module_manifest_unavailable",
            "Module version manifest is unavailable",
        ) from None
    if value.get("repository") != MODULE_REPOSITORY:
        raise ApiError(
            503,
            "module_manifest_unavailable",
            "Module version manifest is unavailable",
        )
    return _response(
        {
            "schema": 1,
            "repository": MODULE_REPOSITORY,
            "modules": modules,
            "updated_at": int(state["updated_at"]),
        }
    )


async def publish_event(request: web.Request) -> web.Response:
    payload = await _json_object(request)
    topic = _identifier(payload.get("topic"), "topic")
    if topic == "system" or topic.startswith("system."):
        raise ApiError(403, "reserved_topic", "system.* topics are reserved")
    ttl_max = request.app[SETTINGS_KEY].event_retention_hours * 3600
    try:
        ttl_seconds = int(payload.get("ttl_seconds", min(86400, ttl_max)))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_field", "ttl_seconds must be an integer") from None
    if not 60 <= ttl_seconds <= ttl_max:
        raise ApiError(400, "invalid_field", f"ttl_seconds must be between 60 and {ttl_max}")
    event = await request.app[STORE_KEY].add_event(
        topic,
        request["credential"]["instance_id"],
        payload.get("payload"),
        ttl_seconds,
    )
    await request.app[STORE_KEY].record_usage(
        request["credential"]["instance_id"], "events"
    )
    return _response(event, status=201)


async def get_events(request: web.Request) -> web.Response:
    after_id = _query_int(request, "after_id", 0, 0, 2**63 - 1)
    limit = _query_int(request, "limit", 50, 1, 200)
    latest = _query_bool(request, "latest")
    topic = request.query.get("topic")
    topic = _identifier(topic, "topic") if topic else None
    events = await request.app[STORE_KEY].list_events(
        after_id, topic, limit, latest=latest
    )
    return _response(
        {
            "events": events,
            "next_after_id": events[-1]["id"] if events else after_id,
        }
    )


async def put_item(request: web.Request) -> web.Response:
    namespace = _identifier(request.match_info["namespace"], "namespace")
    item_key = _identifier(request.match_info["item_key"], "item_key")
    payload = await _json_object(request)
    ttl_raw = payload.get("ttl_seconds")
    ttl_seconds: Optional[int]
    if ttl_raw in (None, 0):
        ttl_seconds = None
    else:
        try:
            ttl_seconds = int(ttl_raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_field", "ttl_seconds must be an integer") from None
        if not 60 <= ttl_seconds <= 31_536_000:
            raise ApiError(400, "invalid_field", "ttl_seconds must be 60..31536000")
    expected_raw = payload.get("if_revision")
    try:
        expected_revision = None if expected_raw is None else int(expected_raw)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_field", "if_revision must be an integer") from None
    if expected_revision is not None and expected_revision < 0:
        raise ApiError(400, "invalid_field", "if_revision cannot be negative")

    result = await request.app[STORE_KEY].put_item(
        namespace,
        item_key,
        request["credential"]["instance_id"],
        payload.get("value"),
        ttl_seconds,
        expected_revision,
    )
    if result.get("error") == "not_owner":
        raise ApiError(403, "not_item_owner", "Only the creating Hikka may update this key")
    if result.get("error") == "revision_conflict":
        raise ApiError(409, "revision_conflict", f"Current revision is {result['revision']}")
    await request.app[STORE_KEY].record_usage(
        request["credential"]["instance_id"], "kv_writes"
    )
    return _response(result)


async def get_item(request: web.Request) -> web.Response:
    namespace = _identifier(request.match_info["namespace"], "namespace")
    item_key = _identifier(request.match_info["item_key"], "item_key")
    item = await request.app[STORE_KEY].get_item(namespace, item_key)
    if item is None:
        raise ApiError(404, "item_not_found", "Shared item not found")
    return _response(item)


async def list_items(request: web.Request) -> web.Response:
    namespace = _identifier(request.match_info["namespace"], "namespace")
    prefix = _bounded_text(request.query.get("prefix", ""), "prefix", 64)
    if prefix and not re.fullmatch(r"[a-z0-9_.-]+", prefix.lower()):
        raise ApiError(400, "invalid_query", "Invalid prefix")
    limit = _query_int(request, "limit", 50, 1, 200)
    items = await request.app[STORE_KEY].list_items(namespace, prefix.lower(), limit)
    return _response({"items": items})


async def delete_item(request: web.Request) -> web.Response:
    namespace = _identifier(request.match_info["namespace"], "namespace")
    item_key = _identifier(request.match_info["item_key"], "item_key")
    result = await request.app[STORE_KEY].delete_item(
        namespace, item_key, request["credential"]["instance_id"]
    )
    if result == "not_owner":
        raise ApiError(403, "not_item_owner", "Only the creating Hikka may delete this key")
    if result == "missing":
        raise ApiError(404, "item_not_found", "Shared item not found")
    return _response({"deleted": True})


async def increment_metric(request: web.Request) -> web.Response:
    metric = _identifier(request.match_info["metric"], "metric")
    payload = await _json_object(request)
    try:
        delta = float(payload.get("delta", 1))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_field", "delta must be numeric") from None
    if not math.isfinite(delta) or not 0 < delta <= 10000:
        raise ApiError(400, "invalid_field", "delta must be greater than 0 and at most 10000")
    result = await request.app[STORE_KEY].increment_metric(
        request["credential"]["instance_id"], metric, delta
    )
    return _response({"metric": metric, **result})


async def increment_metrics(request: web.Request) -> web.Response:
    payload = await _json_object(request)
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, dict) or not 1 <= len(raw_metrics) <= 100:
        raise ApiError(400, "invalid_field", "metrics must contain 1..100 items")
    metrics = {}
    for raw_metric, raw_delta in raw_metrics.items():
        metric = _identifier(raw_metric, "metric")
        try:
            delta = float(raw_delta)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_field", "metric delta must be numeric") from None
        if not math.isfinite(delta) or not 0 < delta <= 10000:
            raise ApiError(
                400,
                "invalid_field",
                "metric delta must be greater than 0 and at most 10000",
            )
        metrics[metric] = delta
    values = await request.app[STORE_KEY].increment_metrics(
        request["credential"]["instance_id"], metrics
    )
    return _response({"metrics": values})


async def metric_stats(request: web.Request) -> web.Response:
    metric = _identifier(request.match_info["metric"], "metric")
    limit = _query_int(request, "limit", 20, 1, 100)
    return _response(await request.app[STORE_KEY].metric_ranking(metric, limit))


async def overview(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    return _response(await request.app[STORE_KEY].overview(settings.offline_after_seconds))


async def _cleanup_context(app: web.Application):
    async def worker():
        while True:
            await asyncio.sleep(300)
            try:
                removed = await app[STORE_KEY].purge_expired()
                if any(removed.values()):
                    logger.info("Purged expired rows: %s", removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic cleanup failed")

    async def module_update_worker():
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": f"Hikka-Hub/{__version__} module watcher"},
        ) as session:
            while True:
                try:
                    modules = await _fetch_module_manifest(session)
                    await _process_module_manifest(app, modules)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Module manifest check failed")
                await asyncio.sleep(app[SETTINGS_KEY].module_update_interval)

    tasks = [asyncio.create_task(worker(), name="hikka-hub-cleanup")]
    if app[SETTINGS_KEY].module_updates_enabled:
        tasks.append(
            asyncio.create_task(
                module_update_worker(), name="hikka-hub-module-updates"
            )
        )
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await app[STORE_KEY].close()


def create_app(
    settings: Optional[Settings] = None, store: Optional[Store] = None
) -> web.Application:
    settings = settings or Settings.from_env()
    app = web.Application(
        middlewares=[error_middleware, auth_middleware],
        client_max_size=settings.request_body_limit,
    )
    app[SETTINGS_KEY] = settings
    app[STORE_KEY] = store or Store(settings.database)
    app[LIMITER_KEY] = SlidingWindowLimiter(settings.rate_limit_per_minute)
    app[PREAUTH_LIMITER_KEY] = SlidingWindowLimiter(
        max(60, settings.rate_limit_per_minute * 5)
    )
    app.cleanup_ctx.append(_cleanup_context)
    app.add_routes(
        [
            web.get("/", root),
            web.get("/health", health),
            web.get("/v1/me", me),
            web.post("/v1/heartbeat", heartbeat),
            web.get("/v1/instances", instances),
            web.get("/v1/modules/versions", module_versions),
            web.post("/v1/events", publish_event),
            web.get("/v1/events", get_events),
            web.put("/v1/kv/{namespace}/{item_key}", put_item),
            web.get("/v1/kv/{namespace}/{item_key}", get_item),
            web.delete("/v1/kv/{namespace}/{item_key}", delete_item),
            web.get("/v1/kv/{namespace}", list_items),
            web.post("/v1/metrics/batch", increment_metrics),
            web.post("/v1/metrics/{metric}/increment", increment_metric),
            web.get("/v1/stats/{metric}", metric_stats),
            web.get("/v1/stats", overview),
        ]
    )
    return app


def run() -> None:
    os.umask(0o077)
    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "Starting Hikka Hub %s on %s:%s using %s",
        __version__,
        settings.bind,
        settings.port,
        settings.database,
    )
    web.run_app(
        create_app(settings),
        host=settings.bind,
        port=settings.port,
        access_log=logger,
    )
