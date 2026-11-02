# meta developer: @Huang_Baike
# meta version: 1.2.0
# meta description: Автоматично завершує всі нові невідомі сесії Telegram.

"""Protect a Telegram account from newly-created, untrusted sessions."""

import asyncio
import contextlib
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

BOT_API = "https://api.telegram.org/bot{token}/{method}"
EPHEMERAL_RETRY = 10 * 60
HISTORY_LIMIT = 20
SHORT_ID_LENGTH = 6
FRESH_SESSION_RETRY = 15 * 60
ALLOW_DEFAULT_MINUTES = 5
ALLOW_MAX_MINUTES = 30


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
        "cfg_ephemeral": (
            "У групах показувати відповіді команд лише вам — ефемерним "
            "повідомленням inline-бота (бот має бути в групі)"
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
        "fresh_session_hint": (
            "Telegram не дозволяє завершувати інші сесії з авторизації, якій "
            "менше 24 годин. Завершіть сесію вручну з основного пристрою — "
            "SessionGuard повторить спробу за 15 хвилин."
        ),
        "allowed_new": (
            "✅ <b>SessionGuard додав нову сесію до довірених</b> "
            "(було відкрито вікно <code>allow</code>).\n\n"
            "Пристрій: <code>{device}</code>\n"
            "Застосунок: <code>{app}</code>\n"
            "Місце: <code>{location}</code>\n"
            "IP: <code>{ip}</code>"
        ),
        "status": (
            "🛡 <b>SessionGuard</b>\n\n"
            "Захист: {state}\n"
            "Довірених сесій: <b>{trusted}</b>\n"
            "Активних під час останньої перевірки: <b>{active}</b>\n"
            "Завершено невідомих сесій: <b>{revoked}</b>\n"
            "Створено базовий список: <code>{baseline}</code>\n"
            "Остання перевірка: <code>{last_check}</code>\n"
            "Остання помилка: <code>{last_error}</code>\n"
            "Вікно для нового входу: {allow}\n\n"
            "Команди: <code>{prefix}sessionguard list</code>, "
            "<code>check</code>, <code>on</code>, <code>off</code>, "
            "<code>allow [хв]</code>, <code>trust ID</code>, "
            "<code>kill ID</code>, <code>history</code>."
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
            "{icon} <b>{device}</b>{current} <code>#{short_id}</code>\n"
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
        "allow_opened": (
            "🔓 <b>Вікно для нового входу відкрито на {} хв.</b>\n"
            "Перша нова сесія за цей час стане довіреною, решта — як завжди "
            "завершуватимуться. Скасувати: "
            "<code>{}sessionguard allow off</code>"
        ),
        "allow_closed": "🔒 <b>Вікно для нового входу закрито.</b>",
        "allow_bad": (
            "Вкажіть кількість хвилин від 1 до {}, наприклад "
            "<code>{}sessionguard allow 5</code>."
        ),
        "allow_state_open": "🔓 <b>відкрито</b> ще <code>{}</code> с",
        "allow_state_closed": "закрито",
        "id_required": (
            "Вкажіть ID сесії з <code>{}sessionguard list</code> "
            "(наприклад <code>#123456</code>)."
        ),
        "id_not_found": "❌ Сесію <code>{}</code> не знайдено серед активних.",
        "id_ambiguous": (
            "❌ ID <code>{}</code> підходить кільком сесіям — вкажіть довший."
        ),
        "trusted_one": "🛡 Сесію <b>{}</b> додано до довірених.",
        "current_protected": "🟢 Це поточна сесія Hikka, її не можна завершити.",
        "killed": "✅ Сесію <b>{}</b> завершено.",
        "kill_failed": "❌ Не вдалося завершити сесію: <code>{}</code>",
        "history_title": "📜 <b>Останні завершені SessionGuard сесії</b>\n\n{}",
        "history_item": (
            "• <code>{time}</code> — <b>{device}</b> · <code>{app}</code>\n"
            "   <code>{location}</code> · <code>{ip}</code> · {source}\n"
        ),
        "history_empty": "Ще жодної сесії не було завершено.",
        "help": (
            "Невідомий параметр. Доступно: <code>list</code>, "
            "<code>check</code>, <code>on</code>, <code>off</code>, "
            "<code>allow [хв|off]</code>, <code>trust ID</code>, "
            "<code>kill ID</code>, <code>history</code>, "
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
            loader.ConfigValue(
                "ephemeral",
                True,
                lambda: self.strings("cfg_ephemeral"),
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
        self._ephemeral_blocked = {}
        self._allow_until = 0.0

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

    @staticmethod
    def _error_text(error):
        return " ".join(
            str(part)
            for part in (
                type(error).__name__,
                getattr(error, "message", "") or "",
                error,
            )
        ).upper()

    @classmethod
    def _is_hash_invalid(cls, error):
        text = cls._error_text(error)
        return "HASH_INVALID" in text or "HASHINVALID" in text

    @classmethod
    def _is_fresh_forbidden(cls, error):
        text = cls._error_text(error)
        return "FRESH_RESET_AUTHORISATION_FORBIDDEN" in text or (
            "FRESHRESETAUTHORISATIONFORBIDDEN" in text
        )

    @staticmethod
    def _short_id(key):
        return str(key).lstrip("-")[-SHORT_ID_LENGTH:]

    def _allow_remaining(self):
        return max(0, int(self._allow_until - time.monotonic()))

    def _escaped_details(self, session):
        return {
            name: utils.escape_html(value)
            for name, value in self._details(session).items()
        }

    def _add_history(self, session, source):
        history = self.get("revoked_history", [])
        history = list(history) if isinstance(history, list) else []
        record = self._details(session)
        record["time"] = self._now_iso()
        record["source"] = str(source)
        history.append(record)
        self.set("revoked_history", history[-HISTORY_LIMIT:])

    def _record_revoked(self, key, session, source):
        self._completed_hashes.add(key)
        self._retry_after.pop(key, None)
        self._reported_failures.discard(key)
        self._last_error = None
        self.set("revoked_count", int(self.get("revoked_count", 0)) + 1)
        self._add_history(session, source)

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

    def _forget_metadata(self, trusted):
        saved = self.get("trusted_devices", {})
        if isinstance(saved, dict) and any(key not in trusted for key in saved):
            self.set(
                "trusted_devices",
                {key: value for key, value in saved.items() if key in trusted},
            )

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
        details = self._escaped_details(session)
        details["error"] = utils.escape_html(str(error))
        await self._notify(self.strings("revoke_failed").format(**details))

    async def _revoke(self, key, session, source):
        trusted = self._trusted_hashes()
        if key in trusted or key in self._completed_hashes:
            return False

        now = time.monotonic()
        if now < self._allow_until:
            # The user opened a one-time window for a planned login: trust the
            # first new session instead of revoking it, then close the window.
            self._allow_until = 0.0
            trusted.add(key)
            self._save_trusted_hashes(trusted)
            self._remember_metadata([session], trusted)
            self._retry_after.pop(key, None)
            self._reported_failures.discard(key)
            await self._notify(
                self.strings("allowed_new").format(**self._escaped_details(session))
            )
            return True

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
            if self._is_hash_invalid(error):
                # The session is already gone (ended by the user or by an
                # earlier request): nothing left to protect against.
                self._completed_hashes.add(key)
                self._retry_after.pop(key, None)
                self._reported_failures.discard(key)
                return True
            self._last_error = f"{type(error).__name__}: {error}"
            if self._is_fresh_forbidden(error):
                self._retry_after[key] = now + FRESH_SESSION_RETRY
                await self._notify_revoke_failure(
                    key,
                    session,
                    f"{self._last_error}\n{self.strings('fresh_session_hint')}",
                )
                logger.warning("SessionGuard: Hikka session is too fresh to revoke")
                return False
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

        self._record_revoked(key, session, source)

        details = self._escaped_details(session)
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

            # Authorization hashes are never reused, so trusted entries of
            # sessions that no longer exist only inflate the list: drop them.
            active = {
                key
                for authorization in authorizations
                if (key := self._hash_key(self._field(authorization, "hash")))
                is not None
            }
            if active and not trusted <= active:
                trusted &= active
                changed = True
                self._forget_metadata(trusted)
            self._completed_hashes &= active
            for key in list(self._retry_after):
                if key not in active:
                    self._retry_after.pop(key, None)
                    self._reported_failures.discard(key)

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

    def _bot_token(self):
        inline = getattr(self, "inline", None)
        return getattr(inline, "_token", None) or getattr(
            getattr(inline, "bot", None), "token", None
        )

    async def _answer(self, message, text):
        """Answer a command; in a group only its sender sees the reply."""
        if not await self._send_ephemeral(message, text):
            await utils.answer(message, text)

    async def _send_ephemeral(self, message, text):
        """Send ``text`` as an ephemeral message of the inline bot.

        Telegram shows such a message only to ``receiver_user_id``; it works
        in groups where the bot is a member.  False when it cannot be sent
        here, so the caller answers as usual.
        """
        chat_id = getattr(message, "chat_id", None)
        receiver = getattr(message, "sender_id", None)
        token = self._bot_token()
        if (
            not self.config["ephemeral"]
            or not getattr(message, "is_group", False)
            or not chat_id
            or not receiver
            or not token
            or self._ephemeral_blocked.get(chat_id, 0) > time.monotonic()
        ):
            return False
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "ephemeral_message_parameters": {"receiver_user_id": receiver},
        }
        try:
            import aiohttp

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.post(
                    BOT_API.format(token=token, method="sendMessage"), json=payload
                ) as response:
                    data = await response.json(content_type=None)
        except Exception as error:
            logger.debug("SessionGuard ephemeral answer failed: %s", error)
            return False
        if not isinstance(data, dict) or not data.get("ok"):
            data = data if isinstance(data, dict) else {"description": str(data)}
            description = str(data.get("description", ""))
            if data.get("error_code") == 403 or "chat not found" in description.lower():
                # The bot is not in this group: do not ask again for a while.
                self._ephemeral_blocked[chat_id] = time.monotonic() + EPHEMERAL_RETRY
            logger.info("SessionGuard: no ephemeral answer here (%s)", description)
            return False
        if getattr(message, "out", False):
            with contextlib.suppress(Exception):
                await message.delete()
        return True

    async def _fetch_for_command(self, message):
        """Fetch authorizations for a command; ``None`` after answering an error."""
        try:
            return await self._fetch_authorizations()
        except (RPCError, ConnectionError, OSError) as error:
            text = str(error)
        except Exception as error:
            logger.exception("Unable to fetch Telegram sessions")
            text = str(error)
        await self._answer(
            message, self.strings("check_failed").format(utils.escape_html(text))
        )
        return None

    async def _resolve_session(self, message, raw_id):
        """Find one active session by its full hash or the ``#ID`` from list."""
        wanted = (raw_id or "").strip().lstrip("#")
        if not wanted.lstrip("-").isdigit():
            await self._answer(
                message,
                self.strings("id_required").format(
                    utils.escape_html(self.get_prefix())
                ),
            )
            return None
        authorizations = await self._fetch_for_command(message)
        if authorizations is None:
            return None
        exact, partial = [], []
        for authorization in authorizations:
            key = self._hash_key(self._field(authorization, "hash"))
            if key is None:
                continue
            if key == wanted:
                exact.append((key, authorization))
            elif key.lstrip("-").endswith(wanted.lstrip("-")):
                partial.append((key, authorization))
        matches = exact or partial
        if len(matches) == 1:
            return matches[0]
        name = "id_ambiguous" if matches else "id_not_found"
        await self._answer(
            message, self.strings(name).format(utils.escape_html("#" + wanted))
        )
        return None

    async def _trust_one(self, message, raw_id):
        found = await self._resolve_session(message, raw_id)
        if not found:
            return
        key, authorization = found
        async with self._guard_lock:
            trusted = self._trusted_hashes()
            trusted.add(key)
            self._save_trusted_hashes(trusted)
            self._remember_metadata([authorization], trusted)
            self._retry_after.pop(key, None)
            self._reported_failures.discard(key)
        await self._answer(
            message,
            self.strings("trusted_one").format(
                self._escaped_details(authorization)["device"]
            ),
        )

    async def _kill_one(self, message, raw_id):
        found = await self._resolve_session(message, raw_id)
        if not found:
            return
        key, authorization = found
        if self._field(authorization, "current", False):
            await self._answer(message, self.strings("current_protected"))
            return
        async with self._guard_lock:
            try:
                result = await self._client(ResetAuthorizationRequest(hash=int(key)))
                if not result:
                    raise RuntimeError("Telegram повернув негативну відповідь")
            except Exception as error:
                if not self._is_hash_invalid(error):
                    text = f"{type(error).__name__}: {error}"
                    if self._is_fresh_forbidden(error):
                        text += "\n" + self.strings("fresh_session_hint")
                    if not isinstance(
                        error,
                        (RPCError, ConnectionError, OSError, RuntimeError),
                    ):
                        logger.exception("Unexpected SessionGuard kill failure")
                    await self._answer(
                        message,
                        self.strings("kill_failed").format(utils.escape_html(text)),
                    )
                    return
            else:
                self._record_revoked(key, authorization, "команда kill")
            trusted = self._trusted_hashes()
            if key in trusted:
                trusted.discard(key)
                self._save_trusted_hashes(trusted)
                self._forget_metadata(trusted)
        await self._answer(
            message,
            self.strings("killed").format(
                self._escaped_details(authorization)["device"]
            ),
        )

    async def _allow(self, message, raw_value):
        value = (raw_value or "").strip().lower()
        prefix = utils.escape_html(self.get_prefix())
        if value in {"off", "close", "0"}:
            self._allow_until = 0.0
            await self._answer(message, self.strings("allow_closed"))
            return
        try:
            minutes = int(value) if value else ALLOW_DEFAULT_MINUTES
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= ALLOW_MAX_MINUTES:
            await self._answer(
                message,
                self.strings("allow_bad").format(ALLOW_MAX_MINUTES, prefix),
            )
            return
        self._allow_until = time.monotonic() + minutes * 60
        await self._answer(
            message, self.strings("allow_opened").format(minutes, prefix)
        )

    async def _history(self, message):
        history = self.get("revoked_history", [])
        history = history if isinstance(history, list) else []
        lines = []
        for record in reversed(history):
            if not isinstance(record, dict):
                continue
            lines.append(
                self.strings("history_item").format(
                    **{
                        name: utils.escape_html(record.get(name, "невідомо"))
                        for name in (
                            "time",
                            "device",
                            "app",
                            "location",
                            "ip",
                            "source",
                        )
                    }
                )
            )
        body = "\n".join(lines) if lines else self.strings("history_empty")
        await self._answer(message, self.strings("history_title").format(body))

    async def _list_sessions(self, message):
        authorizations = await self._fetch_for_command(message)
        if authorizations is None:
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
            details = self._escaped_details(authorization)
            lines.append(
                self.strings("session_item").format(
                    icon=icon,
                    current=" <i>(ця Hikka)</i>" if current else "",
                    short_id=utils.escape_html(self._short_id(key or "")),
                    **details,
                )
            )

        body = "\n".join(lines) if lines else self.strings("no_sessions")
        if len(authorizations) > 30:
            body += f"\n…ще {len(authorizations) - 30} сесій"
        await self._answer(message, self.strings("sessions_title").format(body))

    async def _trust_all(self, message):
        authorizations = await self._fetch_for_command(message)
        if authorizations is None:
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
        await self._answer(message, self.strings("trusted_all").format(len(hashes)))

    @loader.command()
    async def sessionguard(self, message):
        """[list|check|on|off|allow [хв]|trust ID|kill ID|history|trustall CONFIRM] — керувати захистом сесій"""
        args = (utils.get_args_raw(message) or "").strip()
        command = args.split(maxsplit=1)[0].lower() if args else ""
        rest = args.split(maxsplit=1)[1].strip() if " " in args else ""

        if not command or command == "status":
            await self._answer(
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
                    allow=(
                        self.strings("allow_state_open").format(remaining)
                        if (remaining := self._allow_remaining())
                        else self.strings("allow_state_closed")
                    ),
                    prefix=utils.escape_html(self.get_prefix()),
                ),
            )
            return

        if command == "allow":
            await self._allow(message, rest)
            return

        if command == "trust":
            await self._trust_one(message, rest)
            return

        if command in {"kill", "revoke"}:
            await self._kill_one(message, rest)
            return

        if command == "history":
            await self._history(message)
            return

        if command == "list":
            await self._list_sessions(message)
            return

        if command == "check":
            success = await self._audit_sessions(force=True)
            await self._answer(
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
            await self._answer(
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
            await self._answer(message, self.strings("disabled"))
            return

        if command == "trustall":
            if rest != "CONFIRM":
                await self._answer(
                    message,
                    self.strings("trust_warning").format(
                        utils.escape_html(self.get_prefix())
                    ),
                )
                return
            await self._trust_all(message)
            return

        await self._answer(message, self.strings("help"))
