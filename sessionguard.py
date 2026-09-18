# meta developer: @Huang_Baike
# meta version: 1.0.0
# meta description: Автоматично завершує всі нові невідомі сесії Telegram.

"""Protect a Telegram account from newly-created, untrusted sessions."""

import asyncio
import datetime
import logging
import time

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
)
from telethon.tl.types import UpdateNewAuthorization

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class SessionGuardMod(loader.Module):
    """Завершує сесії, яких не було під час першого запуску модуля"""

    strings = {
        "name": "SessionGuard",
        "cfg_enabled": "Автоматично завершувати всі нові невідомі сесії",
        "cfg_interval": (
            "Інтервал резервної перевірки активних сесій у секундах "
            "(від 10 до 300)"
        ),
        "cfg_notifications": (
            "Надсилати у «Збережені повідомлення» звіти про нові сесії"
        ),
        "baseline_ready": (
            "🛡 <b>SessionGuard активовано.</b>\n"
            "Запам’ятано довірених сесій: <b>{}</b>.\n\n"
            "Усі нові авторизації будуть автоматично завершені."
        ),
        "revoked": (
            "🚨 <b>SessionGuard завершив невідому сесію</b>\n\n"
            "Пристрій: <code>{device}</code>\n"
            "Застосунок: <code>{app}</code>\n"
            "Система: <code>{system}</code>\n"
            "Місце: <code>{location}</code>\n"
            "IP: <code>{ip}</code>\n"
            "Джерело виявлення: <code>{source}</code>"
        ),
        "revoke_failed": (
            "⚠️ <b>SessionGuard виявив невідому сесію, але Telegram не дозволив "
            "її завершити.</b>\n\n"
            "Пристрій: <code>{device}</code>\n"
            "Місце: <code>{location}</code>\n"
            "Помилка: <code>{error}</code>\n\n"
            "Модуль автоматично повторить спробу."
        ),
        "status": (
            "🛡 <b>SessionGuard</b>\n\n"
            "Захист: {state}\n"
            "Довірених сесій: <b>{trusted}</b>\n"
            "Активних під час останньої перевірки: <b>{active}</b>\n"
            "Завершено невідомих сесій: <b>{revoked}</b>\n"
            "Створено базовий список: <code>{baseline}</code>\n"
            "Остання перевірка: <code>{last_check}</code>\n"
            "Остання помилка: <code>{last_error}</code>\n\n"
            "Команди: <code>{prefix}sessionguard list</code>, "
            "<code>{prefix}sessionguard check</code>, "
            "<code>{prefix}sessionguard on</code>, "
            "<code>{prefix}sessionguard off</code>."
        ),
        "enabled": "🟢 <b>SessionGuard увімкнено.</b>",
        "disabled": (
            "⚫ <b>SessionGuard вимкнено.</b> Нові сесії тимчасово не "
            "завершуватимуться."
        ),
        "checked": "✅ <b>Перевірку сесій завершено.</b>",
        "check_failed": "❌ <b>Не вдалося перевірити сесії:</b> <code>{}</code>",
        "sessions_title": "📱 <b>Активні сесії Telegram</b>\n\n{}",
        "session_item": (
            "{icon} <b>{device}</b>{current}\n"
            "   <code>{app}</code> · <code>{system}</code>\n"
            "   <code>{location}</code> · <code>{ip}</code>\n"
        ),
        "no_sessions": "Telegram не повернув жодної активної сесії.",
        "trust_warning": (
            "⚠️ <b>Ця дія зробить усі активні зараз сесії довіреними.</b>\n"
            "Спочатку переконайтеся, що серед них немає чужих пристроїв.\n\n"
            "Для підтвердження: <code>{}sessionguard trustall CONFIRM</code>"
        ),
        "trusted_all": (
            "✅ <b>Базовий список оновлено.</b> Тепер довірених сесій: "
            "<b>{}</b>."
        ),
        "help": (
            "Невідомий параметр. Доступно: <code>list</code>, "
            "<code>check</code>, <code>on</code>, <code>off</code>, "
            "<code>trustall CONFIRM</code>."
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "enabled",
                True,
                lambda: self.strings("cfg_enabled"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "check_interval",
                30,
                lambda: self.strings("cfg_interval"),
                validator=loader.validators.Integer(minimum=10, maximum=300),
            ),
            loader.ConfigValue(
                "notifications",
                True,
                lambda: self.strings("cfg_notifications"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._guard_lock = asyncio.Lock()
        self._next_check = 0.0
        self._last_check = None
        self._last_error = None
        self._last_active_count = 0
        self._retry_after = {}
        self._reported_failures = set()
        self._completed_hashes = set()

    async def client_ready(self, client, db):
        self._client = client
        if not self.get("baseline_initialized", False):
            await self._create_baseline(notify=True)
        elif self.config["enabled"]:
            await self._audit_sessions()

    @staticmethod
    def _now_iso():
        return datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    @staticmethod
    def _hash_key(value):
        if value is None:
            return None
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _field(session, name, default=None):
        if isinstance(session, dict):
            return session.get(name, default)
        return getattr(session, name, default)

    def _trusted_hashes(self):
        raw = self.get("trusted_hashes", [])
        if not isinstance(raw, list):
            return set()
        return {
            key
            for value in raw
            if (key := self._hash_key(value)) is not None
        }

    def _save_trusted_hashes(self, hashes):
        self.set("trusted_hashes", sorted(hashes, key=lambda value: int(value)))

    def _session_metadata(self, session):
        return {
            "device_model": str(self._field(session, "device_model", "") or ""),
            "platform": str(self._field(session, "platform", "") or ""),
            "system_version": str(
                self._field(session, "system_version", "") or ""
            ),
            "app_name": str(self._field(session, "app_name", "") or ""),
            "app_version": str(self._field(session, "app_version", "") or ""),
        }

    def _remember_metadata(self, authorizations, trusted):
        saved = self.get("trusted_devices", {})
        if not isinstance(saved, dict):
            saved = {}
        else:
            saved = dict(saved)

        changed = False
        for authorization in authorizations:
            key = self._hash_key(self._field(authorization, "hash"))
            if key not in trusted:
                continue
            metadata = self._session_metadata(authorization)
            if saved.get(key) != metadata:
                saved[key] = metadata
                changed = True

        if changed:
            self.set("trusted_devices", saved)

    async def _fetch_authorizations(self):
        result = await self._client(GetAuthorizationsRequest())
        return list(getattr(result, "authorizations", []) or [])

    async def _create_baseline(self, notify=False):
        try:
            authorizations = await self._fetch_authorizations()
        except FloodWaitError as error:
            seconds = max(1, int(getattr(error, "seconds", 0)))
            self._last_error = f"FloodWaitError: зачекайте {seconds} с."
            self._next_check = time.monotonic() + seconds + 1
            logger.warning("SessionGuard baseline flood wait: %s seconds", seconds)
            return False
        except (RPCError, ConnectionError, OSError) as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._next_check = time.monotonic() + 30
            logger.warning("Unable to create SessionGuard baseline: %s", error)
            return False
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._next_check = time.monotonic() + 30
            logger.exception("Unexpected SessionGuard baseline failure")
            return False

        hashes = {
            key
            for authorization in authorizations
            if (key := self._hash_key(self._field(authorization, "hash")))
            is not None
        }
        self._save_trusted_hashes(hashes)
        self._remember_metadata(authorizations, hashes)
        self.set("baseline_initialized", True)
        self.set("baseline_created_at", self._now_iso())
        self._last_active_count = len(authorizations)
        self._last_check = self._now_iso()
        self._last_error = None
        self._next_check = time.monotonic() + int(self.config["check_interval"])

        if notify and self.config["notifications"]:
            await self._notify(self.strings("baseline_ready").format(len(hashes)))
        return True

    def _details(self, session):
        device = (
            self._field(session, "device_model")
            or self._field(session, "device")
            or "невідомий"
        )
        app_name = self._field(session, "app_name") or "невідомий застосунок"
        app_version = self._field(session, "app_version") or ""
        app = f"{app_name} {app_version}".strip()

        platform = self._field(session, "platform") or ""
        system_version = self._field(session, "system_version") or ""
        system = f"{platform} {system_version}".strip() or "невідомо"

        location_parts = [
            self._field(session, "country") or "",
            self._field(session, "region") or "",
        ]
        location = ", ".join(part for part in location_parts if part)
        location = location or self._field(session, "location") or "невідомо"

        return {
            "device": str(device),
            "app": str(app),
            "system": str(system),
            "location": str(location),
            "ip": str(self._field(session, "ip") or "невідомо"),
        }

    async def _notify(self, text):
        if not self._client or not self.config["notifications"]:
            return
        try:
            await self._client.send_message("me", text)
        except (RPCError, ConnectionError, OSError):
            logger.warning("Unable to send SessionGuard notification", exc_info=True)
        except Exception:
            logger.exception("Unexpected SessionGuard notification failure")

    async def _notify_revoke_failure(self, key, session, error):
        if key in self._reported_failures:
            return
        self._reported_failures.add(key)
        details = {
            name: utils.escape_html(value)
            for name, value in self._details(session).items()
        }
        details["error"] = utils.escape_html(str(error))
        await self._notify(self.strings("revoke_failed").format(**details))

    async def _revoke(self, key, session, source):
        trusted = self._trusted_hashes()
        if key in trusted or key in self._completed_hashes:
            return False

        now = time.monotonic()
        if now < self._retry_after.get(key, 0.0):
            return False

        try:
            result = await self._client(ResetAuthorizationRequest(hash=int(key)))
            if not result:
                raise RuntimeError("Telegram повернув негативну відповідь")
        except FloodWaitError as error:
            seconds = max(1, int(getattr(error, "seconds", 0)))
            self._last_error = f"FloodWaitError: зачекайте {seconds} с."
            self._retry_after[key] = now + seconds + 1
            self._next_check = max(self._next_check, now + seconds + 1)
            await self._notify_revoke_failure(key, session, self._last_error)
            return False
        except (RPCError, ConnectionError, OSError, RuntimeError) as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._retry_after[key] = now + 60
            await self._notify_revoke_failure(key, session, self._last_error)
            logger.warning("Unable to revoke unknown Telegram session: %s", error)
            return False
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._retry_after[key] = now + 60
            await self._notify_revoke_failure(key, session, self._last_error)
            logger.exception("Unexpected SessionGuard revoke failure")
            return False

        self._completed_hashes.add(key)
        self._retry_after.pop(key, None)
        self._reported_failures.discard(key)
        self._last_error = None
        self.set("revoked_count", int(self.get("revoked_count", 0)) + 1)

        details = {
            name: utils.escape_html(value)
            for name, value in self._details(session).items()
        }
        details["source"] = utils.escape_html(source)
        await self._notify(self.strings("revoked").format(**details))
        return True

    async def _audit_sessions(self, force=False):
        if not self._client or (not force and not self.config["enabled"]):
            return False
        if not self.get("baseline_initialized", False):
            return await self._create_baseline(notify=True)

        async with self._guard_lock:
            try:
                authorizations = await self._fetch_authorizations()
            except FloodWaitError as error:
                seconds = max(1, int(getattr(error, "seconds", 0)))
                self._last_error = f"FloodWaitError: зачекайте {seconds} с."
                self._next_check = time.monotonic() + seconds + 1
                logger.warning("SessionGuard audit flood wait: %s seconds", seconds)
                return False
            except (RPCError, ConnectionError, OSError) as error:
                self._last_error = f"{type(error).__name__}: {error}"
                self._next_check = time.monotonic() + 30
                logger.warning("SessionGuard audit failed: %s", error)
                return False
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                self._next_check = time.monotonic() + 30
                logger.exception("Unexpected SessionGuard audit failure")
                return False

            trusted = self._trusted_hashes()
            changed = False

            # The session running Hikka can never be treated as hostile. This
            # also keeps restored databases safe when Hikka gets a new session.
            for authorization in authorizations:
                key = self._hash_key(self._field(authorization, "hash"))
                if key is not None and self._field(authorization, "current", False):
                    if key not in trusted:
                        trusted.add(key)
                        changed = True

            if changed:
                self._save_trusted_hashes(trusted)
            self._remember_metadata(authorizations, trusted)

            all_secure = True
            for authorization in authorizations:
                key = self._hash_key(self._field(authorization, "hash"))
                if (
                    key is not None
                    and key not in trusted
                    and not self._field(authorization, "current", False)
                ):
                    if not await self._revoke(
                        key,
                        authorization,
                        "резервна перевірка",
                    ):
                        all_secure = False

            self._last_active_count = len(authorizations)
            self._last_check = self._now_iso()
            self._next_check = time.monotonic() + int(self.config["check_interval"])
            return all_secure

    @loader.raw_handler(UpdateNewAuthorization)
    async def new_authorization_handler(self, update):
        """Immediately revoke an untrusted authorization reported by Telegram."""
        if not self._client or not self.config["enabled"]:
            return

        key = self._hash_key(getattr(update, "hash", None))
        if key is None or key in self._trusted_hashes():
            return

        async with self._guard_lock:
            await self._revoke(key, update, "UpdateNewAuthorization")

    @loader.loop(interval=5, autostart=True)
    async def guard_loop(self):
        if not self._client or not self.config["enabled"]:
            return
        if time.monotonic() < self._next_check:
            return
        await self._audit_sessions()

    async def _list_sessions(self, message):
        try:
            authorizations = await self._fetch_authorizations()
        except (RPCError, ConnectionError, OSError) as error:
            await utils.answer(
                message,
                self.strings("check_failed").format(utils.escape_html(str(error))),
            )
            return
        except Exception as error:
            logger.exception("Unable to list Telegram sessions")
            await utils.answer(
                message,
                self.strings("check_failed").format(utils.escape_html(str(error))),
            )
            return

        trusted = self._trusted_hashes()
        lines = []
        for authorization in authorizations[:30]:
            key = self._hash_key(self._field(authorization, "hash"))
            current = bool(self._field(authorization, "current", False))
            if current:
                icon = "🟢"
            elif key in trusted:
                icon = "🛡"
            else:
                icon = "⚠️"
            details = {
                name: utils.escape_html(value)
                for name, value in self._details(authorization).items()
            }
            lines.append(
                self.strings("session_item").format(
                    icon=icon,
                    current=" <i>(ця Hikka)</i>" if current else "",
                    **details,
                )
            )

        body = "\n".join(lines) if lines else self.strings("no_sessions")
        if len(authorizations) > 30:
            body += f"\n…ще {len(authorizations) - 30} сесій"
        await utils.answer(message, self.strings("sessions_title").format(body))

    async def _trust_all(self, message):
        try:
            authorizations = await self._fetch_authorizations()
        except (RPCError, ConnectionError, OSError) as error:
            await utils.answer(
                message,
                self.strings("check_failed").format(utils.escape_html(str(error))),
            )
            return
        except Exception as error:
            logger.exception("Unable to replace SessionGuard baseline")
            await utils.answer(
                message,
                self.strings("check_failed").format(utils.escape_html(str(error))),
            )
            return

        hashes = {
            key
            for authorization in authorizations
            if (key := self._hash_key(self._field(authorization, "hash")))
            is not None
        }
        self._save_trusted_hashes(hashes)
        self._remember_metadata(authorizations, hashes)
        self.set("baseline_initialized", True)
        self.set("baseline_created_at", self._now_iso())
        self._retry_after.clear()
        self._reported_failures.clear()
        self._completed_hashes.clear()
        self._last_active_count = len(authorizations)
        self._last_check = self._now_iso()
        self._last_error = None
        self._next_check = time.monotonic() + int(self.config["check_interval"])
        await utils.answer(message, self.strings("trusted_all").format(len(hashes)))

    @loader.command()
    async def sessionguard(self, message):
        """[list|check|on|off|trustall CONFIRM] — керувати захистом сесій"""
        args = (utils.get_args_raw(message) or "").strip()
        command = args.split(maxsplit=1)[0].lower() if args else ""

        if not command or command == "status":
            await utils.answer(
                message,
                self.strings("status").format(
                    state=(
                        "🟢 <b>увімкнено</b>"
                        if self.config["enabled"]
                        else "⚫ <b>вимкнено</b>"
                    ),
                    trusted=len(self._trusted_hashes()),
                    active=self._last_active_count,
                    revoked=int(self.get("revoked_count", 0)),
                    baseline=utils.escape_html(
                        self.get("baseline_created_at", "ще не створено")
                    ),
                    last_check=utils.escape_html(self._last_check or "ще не було"),
                    last_error=utils.escape_html(self._last_error or "немає"),
                    prefix=utils.escape_html(self.get_prefix()),
                ),
            )
            return

        if command == "list":
            await self._list_sessions(message)
            return

        if command == "check":
            success = await self._audit_sessions(force=True)
            await utils.answer(
                message,
                self.strings("checked")
                if success
                else self.strings("check_failed").format(
                    utils.escape_html(self._last_error or "невідома помилка")
                ),
            )
            return

        if command == "on":
            self.config["enabled"] = True
            success = await self._audit_sessions(force=True)
            await utils.answer(
                message,
                self.strings("enabled")
                if success
                else self.strings("check_failed").format(
                    utils.escape_html(self._last_error or "невідома помилка")
                ),
            )
            return

        if command == "off":
            self.config["enabled"] = False
            await utils.answer(message, self.strings("disabled"))
            return

        if command == "trustall":
            confirmation = args.split(maxsplit=1)[1] if " " in args else ""
            if confirmation.strip() != "CONFIRM":
                await utils.answer(
                    message,
                    self.strings("trust_warning").format(
                        utils.escape_html(self.get_prefix())
                    ),
                )
                return
            await self._trust_all(message)
            return

        await utils.answer(message, self.strings("help"))
