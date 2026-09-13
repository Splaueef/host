# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: Панель адміністрування груп і каналів з попередженнями та кнопками
# scope: inline
# scope: hikka_only

__version__ = (1, 0, 0)

import asyncio
import contextlib
import datetime
import html
import logging
import re
import secrets
import time

from telethon.errors import RPCError
from telethon.tl.functions.channels import EditBannedRequest, ToggleSlowModeRequest
from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
from telethon.tl.types import ChatBannedRights

from .. import loader, utils


logger = logging.getLogger(__name__)

SESSION_TTL = 15 * 60
SLOWMODE_VALUES = (0, 10, 30, 60, 300, 900, 3600)
DURATION_RE = re.compile(r"^(\d{1,5})([smhdw])$", re.IGNORECASE)


@loader.tds
class GroupAdminMod(loader.Module):
    """Зручне адміністрування груп і каналів із безпечними підтвердженнями."""

    strings = {
        "name": "GroupAdmin",
        "group_only": "❌ <b>Ця команда працює лише в групі або каналі.</b>",
        "need_right": "❌ Потрібне право адміністратора: <b>{}</b>.",
        "need_user": (
            "❌ Не вдалося визначити користувача. Дайте команду у відповідь "
            "або вкажіть <code>@username</code> чи ID."
        ),
        "bad_user": "❌ Не вдалося знайти користувача: <code>{}</code>",
        "protected_user": "❌ Не можна застосувати цю дію до власника або адміністратора чату.",
        "self_target": "❌ Не можна застосувати цю дію до власного акаунта.",
        "bad_duration": (
            "❌ Некоректний час. Приклади: <code>10m</code>, <code>2h</code>, "
            "<code>3d</code>, <code>1w</code> або <code>forever</code>."
        ),
        "bad_slowmode": "❌ Доступні значення: <code>off, 10s, 30s, 1m, 5m, 15m, 1h</code>.",
        "bad_lock": "❌ Розділи: <code>messages, media, stickers, links, polls</code>.",
        "inline_failed": "❌ Не вдалося відкрити панель. Перевірте inline-бота Hikka.",
        "expired": "⌛ Панель застаріла. Відкрийте <code>.gadmin</code> ще раз.",
        "done": "✅ <b>{action}</b> · {user}{details}",
        "failed": "❌ <b>Дію не виконано.</b>\n<code>{}</code>",
    }

    PERMISSION_LABELS = {
        "delete_messages": "видалення повідомлень",
        "ban_users": "блокування користувачів",
        "pin_messages": "закріплення повідомлень",
        "change_info": "зміна налаштувань чату",
    }

    LOCK_GROUPS = {
        "messages": ("send_messages", "send_plain"),
        "media": (
            "send_media",
            "send_photos",
            "send_videos",
            "send_roundvideos",
            "send_audios",
            "send_voices",
            "send_docs",
        ),
        "stickers": ("send_stickers", "send_gifs", "send_games", "send_inline"),
        "links": ("embed_links",),
        "polls": ("send_polls",),
    }

    LOCK_LABELS = {
        "messages": "💬 Повідомлення",
        "media": "🖼 Медіа",
        "stickers": "🎭 Стікери та GIF",
        "links": "🔗 Посилання",
        "polls": "📊 Опитування",
    }

    ACTION_LABELS = {
        "ban": "Заблоковано",
        "unban": "Розблоковано",
        "kick": "Видалено з чату",
        "mute": "Обмежено",
        "unmute": "Обмеження знято",
        "warn": "Додано попередження",
        "unwarn": "Знято попередження",
        "clearwarns": "Попередження очищено",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "warn_limit",
                3,
                lambda: "Кількість попереджень до автоматичного покарання",
                validator=loader.validators.Integer(minimum=1, maximum=10),
            ),
            loader.ConfigValue(
                "warn_action",
                "mute",
                lambda: "Покарання після ліміту: mute, kick або ban",
                validator=loader.validators.Choice(["mute", "kick", "ban"]),
            ),
            loader.ConfigValue(
                "warn_mute_minutes",
                1440,
                lambda: "Тривалість автоматичного mute після ліміту попереджень",
                validator=loader.validators.Integer(minimum=1, maximum=525600),
            ),
            loader.ConfigValue(
                "default_mute_minutes",
                60,
                lambda: "Типова тривалість .gmute без указаного часу",
                validator=loader.validators.Integer(minimum=1, maximum=525600),
            ),
            loader.ConfigValue(
                "log_chat",
                0,
                lambda: "ID чату для журналу модерації; 0 — вимкнено",
                validator=loader.validators.Integer(),
            ),
        )
        self._client = None
        self._me_id = None
        self._sessions = {}
        self._locks = {}

    async def client_ready(self, client, db):
        self._client = client
        self._me_id = getattr(client, "tg_id", None)
        if self._me_id is None:
            self._me_id = (await client.get_me()).id

    @staticmethod
    def _display_name(entity):
        title = getattr(entity, "title", None)
        if title:
            return str(title)
        name = " ".join(
            value
            for value in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            )
            if value
        ).strip()
        username = getattr(entity, "username", None)
        return name or (f"@{username}" if username else str(getattr(entity, "id", "—")))

    @staticmethod
    def _parse_duration(value, default_seconds=None):
        raw = str(value or "").strip().lower()
        if not raw:
            return default_seconds
        if raw in {"forever", "perm", "permanent", "назавжди", "навсегда"}:
            return 0
        match = DURATION_RE.fullmatch(raw)
        if not match:
            return None
        amount = int(match.group(1))
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[
            match.group(2).lower()
        ]
        seconds = amount * multiplier
        return seconds if 30 <= seconds <= 366 * 86400 else None

    @staticmethod
    def _duration_text(seconds):
        if not seconds:
            return "назавжди"
        for unit, size in (("тиж.", 604800), ("дн.", 86400), ("год.", 3600), ("хв.", 60)):
            if seconds % size == 0:
                return f"{seconds // size} {unit}"
        return f"{seconds} с"

    @staticmethod
    def _split_first(raw):
        parts = str(raw or "").strip().split(maxsplit=1)
        return (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")

    def _purge_sessions(self):
        now = time.monotonic()
        for token, session in list(self._sessions.items()):
            if now - session["created_at"] > SESSION_TTL:
                self._sessions.pop(token, None)
                self._locks.pop(token, None)

    def _session(self, token):
        self._purge_sessions()
        return self._sessions.get(token)

    def _new_session(self, chat_id, target_id=None, reply_id=None):
        self._purge_sessions()
        token = secrets.token_urlsafe(7)
        self._sessions[token] = {
            "chat_id": int(chat_id),
            "target_id": int(target_id) if target_id else None,
            "reply_id": int(reply_id) if reply_id else None,
            "created_at": time.monotonic(),
        }
        self._locks[token] = asyncio.Lock()
        return token

    @staticmethod
    def _chat_id(message):
        with contextlib.suppress(Exception):
            return int(utils.get_chat_id(message))
        return int(getattr(message, "chat_id", 0) or 0)

    @staticmethod
    def _is_chat(message):
        return bool(getattr(message, "is_group", False) or getattr(message, "is_channel", False))

    async def _require_permission(self, message, permission):
        if not self._is_chat(message):
            await utils.answer(message, self.strings["group_only"])
            return False
        try:
            rights = await self._client.get_permissions(self._chat_id(message), self._me_id)
            allowed = bool(
                getattr(rights, "is_creator", False)
                or getattr(rights, permission, False)
            )
        except (RPCError, ValueError, TypeError):
            allowed = False
        if not allowed:
            await utils.answer(
                message,
                self.strings["need_right"].format(self.PERMISSION_LABELS.get(permission, permission)),
            )
        return allowed

    async def _resolve_target(self, message, raw=""):
        reply = None
        with contextlib.suppress(Exception):
            reply = await message.get_reply_message()
        if reply is not None and getattr(reply, "sender_id", None):
            try:
                return await self._client.get_entity(reply.sender_id), str(raw or "").strip()
            except (RPCError, ValueError, TypeError):
                pass

        token, remainder = self._split_first(raw)
        if not token:
            await utils.answer(message, self.strings["need_user"])
            return None, ""
        try:
            entity = await self._client.get_entity(int(token) if token.lstrip("-").isdigit() else token)
        except (RPCError, ValueError, TypeError):
            await utils.answer(message, self.strings["bad_user"].format(html.escape(token)))
            return None, ""
        return entity, remainder

    async def _target_is_protected(self, chat_id, user):
        if int(getattr(user, "id", 0)) == int(self._me_id or 0):
            return "self"
        try:
            rights = await self._client.get_permissions(chat_id, user)
        except (RPCError, ValueError, TypeError):
            return None
        if getattr(rights, "is_creator", False) or getattr(rights, "is_admin", False):
            return "admin"
        return None

    @staticmethod
    def _empty_rights():
        return ChatBannedRights(until_date=None)

    @staticmethod
    def _ban_rights():
        return ChatBannedRights(until_date=None, view_messages=True)

    @staticmethod
    def _mute_rights(seconds):
        until = None
        if seconds:
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        rights = ChatBannedRights(
            until_date=until,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
            send_polls=True,
        )
        # Newer Telegram layers split media and plain text into individual flags.
        # Setting them when available keeps a full mute effective while remaining
        # compatible with older Telethon builds used by some Hikka installations.
        for attr in (
            "send_plain",
            "send_photos",
            "send_videos",
            "send_roundvideos",
            "send_audios",
            "send_voices",
            "send_docs",
        ):
            if hasattr(rights, attr):
                setattr(rights, attr, True)
        return rights

    async def _edit_banned(self, chat_id, user_id, rights):
        chat = await self._client.get_entity(chat_id)
        user = await self._client.get_entity(user_id)
        await self._client(EditBannedRequest(chat, user, rights))

    async def _apply_action(self, chat_id, user, action, value=None, reason=""):
        user_id = int(user.id)
        if action in {"ban", "kick", "mute", "warn"}:
            protected = await self._target_is_protected(chat_id, user)
            if protected == "self":
                raise ValueError(self.strings["self_target"])
            if protected == "admin":
                raise ValueError(self.strings["protected_user"])

        if action == "ban":
            await self._edit_banned(chat_id, user_id, self._ban_rights())
        elif action in {"unban", "unmute"}:
            await self._edit_banned(chat_id, user_id, self._empty_rights())
        elif action == "kick":
            await self._edit_banned(chat_id, user_id, self._ban_rights())
            await asyncio.sleep(0.35)
            await self._edit_banned(chat_id, user_id, self._empty_rights())
        elif action == "mute":
            await self._edit_banned(chat_id, user_id, self._mute_rights(int(value or 0)))
        elif action == "warn":
            return await self._add_warning(chat_id, user, reason)
        elif action == "unwarn":
            result = self._remove_warning(chat_id, user_id, clear=False)
            await self._log_action(chat_id, user, action, reason=reason)
            return result
        elif action == "clearwarns":
            result = self._remove_warning(chat_id, user_id, clear=True)
            await self._log_action(chat_id, user, action, reason=reason)
            return result
        else:
            raise ValueError("Невідома дія")

        await self._log_action(chat_id, user, action, value=value, reason=reason)
        return {"count": self._warn_count(chat_id, user_id), "auto_action": None}

    def _warnings(self):
        value = self.get("warnings", {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _warning_key(chat_id, user_id):
        return f"{int(chat_id)}:{int(user_id)}"

    def _warn_count(self, chat_id, user_id):
        return len(self._warnings().get(self._warning_key(chat_id, user_id), []))

    async def _add_warning(self, chat_id, user, reason):
        data = self._warnings()
        key = self._warning_key(chat_id, user.id)
        items = list(data.get(key, []))
        items.append(
            {
                "at": int(time.time()),
                "reason": str(reason or "Без причини")[:300],
                "by": int(self._me_id or 0),
            }
        )
        data[key] = items[-50:]
        self.set("warnings", data)
        count = len(data[key])
        limit = int(self.config["warn_limit"])
        auto_action = None
        if count >= limit:
            auto_action = str(self.config["warn_action"])
            if auto_action == "mute":
                seconds = int(self.config["warn_mute_minutes"]) * 60
                await self._edit_banned(chat_id, user.id, self._mute_rights(seconds))
            elif auto_action == "kick":
                await self._edit_banned(chat_id, user.id, self._ban_rights())
                await asyncio.sleep(0.35)
                await self._edit_banned(chat_id, user.id, self._empty_rights())
            else:
                await self._edit_banned(chat_id, user.id, self._ban_rights())
            data.pop(key, None)
            self.set("warnings", data)
        await self._log_action(chat_id, user, "warn", reason=reason, count=count, auto_action=auto_action)
        return {"count": 0 if auto_action else count, "auto_action": auto_action}

    def _remove_warning(self, chat_id, user_id, clear=False):
        data = self._warnings()
        key = self._warning_key(chat_id, user_id)
        items = list(data.get(key, []))
        if clear:
            items = []
        elif items:
            items.pop()
        if items:
            data[key] = items
        else:
            data.pop(key, None)
        self.set("warnings", data)
        return {"count": len(items), "auto_action": None}

    async def _log_action(self, chat_id, user, action, **details):
        log_chat = int(self.config["log_chat"] or 0)
        if not log_chat:
            return
        reason = html.escape(str(details.get("reason") or "Без причини"))
        text = (
            "🛡 <b>Журнал модерації</b>\n\n"
            f"Дія: <b>{html.escape(self.ACTION_LABELS.get(action, action))}</b>\n"
            f"Чат: <code>{chat_id}</code>\n"
            f"Користувач: {html.escape(self._display_name(user))} "
            f"(<code>{int(user.id)}</code>)\n"
            f"Причина: {reason}"
        )
        with contextlib.suppress(Exception):
            await self._client.send_message(log_chat, text, parse_mode="html")

    async def _panel_data(self, session):
        chat = await self._client.get_entity(session["chat_id"])
        rights = await self._client.get_permissions(chat, self._me_id)
        target = None
        if session.get("target_id"):
            with contextlib.suppress(Exception):
                target = await self._client.get_entity(session["target_id"])
        return chat, rights, target

    async def _panel_text(self, session):
        chat, rights, target = await self._panel_data(session)
        right_lines = []
        for key, label in self.PERMISSION_LABELS.items():
            enabled = getattr(rights, "is_creator", False) or getattr(rights, key, False)
            right_lines.append(f"{'✅' if enabled else '▫️'} {label}")
        slowmode = int(getattr(chat, "slowmode_seconds", 0) or 0)
        text = (
            "🛡 <b>GroupAdmin · панель керування</b>\n\n"
            f"💬 <b>Чат:</b> {html.escape(self._display_name(chat))}\n"
            f"🆔 <code>{session['chat_id']}</code>\n"
            f"⏱ <b>Slow mode:</b> {self._duration_text(slowmode) if slowmode else 'вимкнено'}\n\n"
            "<b>Ваші права</b>\n" + "\n".join(right_lines)
        )
        if target is not None:
            count = self._warn_count(session["chat_id"], target.id)
            text += (
                "\n\n👤 <b>Користувач:</b> "
                f"{html.escape(self._display_name(target))}\n"
                f"🆔 <code>{int(target.id)}</code>\n"
                f"⚠️ Попередження: <b>{count}/{int(self.config['warn_limit'])}</b>"
            )
        else:
            text += "\n\n<i>Дайте .gadmin у відповідь, щоб з'явилися дії над користувачем.</i>"
        return text

    def _panel_markup(self, token):
        session = self._session(token)
        if session is None:
            return []
        rows = []
        if session.get("target_id"):
            rows.extend(
                [
                    [
                        {"text": "🔇 Обмежити", "callback": self._mute_menu, "args": (token,)},
                        {"text": "🔊 Зняти mute", "callback": self._confirm_action, "args": (token, "unmute", None)},
                    ],
                    [
                        {"text": "⚠️ Warn", "callback": self._confirm_action, "args": (token, "warn", None)},
                        {"text": "➖ Unwarn", "callback": self._confirm_action, "args": (token, "unwarn", None)},
                    ],
                    [
                        {"text": "🚪 Kick", "callback": self._confirm_action, "args": (token, "kick", None)},
                        {"text": "⛔ Ban", "callback": self._confirm_action, "args": (token, "ban", None)},
                    ],
                    [
                        {"text": "✅ Unban", "callback": self._confirm_action, "args": (token, "unban", None)},
                        {"text": "🧹 Очистити warns", "callback": self._confirm_action, "args": (token, "clearwarns", None)},
                    ],
                ]
            )
        rows.extend(
            [
                [
                    {"text": "⏱ Slow mode", "callback": self._slowmode_menu, "args": (token,)},
                    {"text": "🔐 Дозволи", "callback": self._permissions_menu, "args": (token,)},
                ],
            ]
        )
        if session.get("reply_id"):
            rows.append([{"text": "📌 Закріпити відповідь", "callback": self._pin_reply, "args": (token,)}])
        rows.append(
            [
                {"text": "🔄 Оновити", "callback": self._refresh_panel, "args": (token,)},
                {"text": "✖️ Закрити", "action": "close"},
            ]
        )
        return rows

    async def _refresh_panel(self, call, token, note=None):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        text = await self._panel_text(session)
        if note:
            text += f"\n\n✅ <i>{html.escape(str(note))}</i>"
        await call.edit(text, reply_markup=self._panel_markup(token))

    async def _mute_menu(self, call, token):
        session = self._session(token)
        if session is None or not session.get("target_id"):
            await call.answer(self.strings["expired"], show_alert=True)
            return
        choices = (("10 хв", 600), ("1 год", 3600), ("8 год", 28800), ("1 день", 86400), ("7 днів", 604800), ("Назавжди", 0))
        buttons = [
            {"text": label, "callback": self._perform_panel_action, "args": (token, "mute", seconds)}
            for label, seconds in choices
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([{"text": "↩️ Назад", "callback": self._refresh_panel, "args": (token,)}])
        await call.edit(
            "🔇 <b>Тривалість обмеження</b>\n\nОберіть строк. Дія буде виконана одразу.",
            reply_markup=rows,
        )

    async def _confirm_action(self, call, token, action, value=None):
        session = self._session(token)
        if session is None or not session.get("target_id"):
            await call.answer(self.strings["expired"], show_alert=True)
            return
        user = await self._client.get_entity(session["target_id"])
        label = self.ACTION_LABELS.get(action, action)
        await call.edit(
            "⚠️ <b>Підтвердження дії</b>\n\n"
            f"{html.escape(label)}: <b>{html.escape(self._display_name(user))}</b>?",
            reply_markup=[
                [
                    {"text": "✅ Підтвердити", "callback": self._perform_panel_action, "args": (token, action, value)},
                    {"text": "❌ Скасувати", "callback": self._refresh_panel, "args": (token,)},
                ]
            ],
        )

    async def _perform_panel_action(self, call, token, action, value=None):
        session = self._session(token)
        if session is None or not session.get("target_id"):
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        if lock.locked():
            await call.answer("⏳ Дія вже виконується")
            return
        async with lock:
            try:
                user = await self._client.get_entity(session["target_id"])
                result = await self._apply_action(session["chat_id"], user, action, value=value)
            except Exception as error:
                logger.exception("GroupAdmin panel action failed: %s", action)
                text = str(error)
                if text.startswith("❌"):
                    await call.answer(text, show_alert=True)
                else:
                    await call.answer(text[:180] or type(error).__name__, show_alert=True)
                await self._refresh_panel(call, token)
                return
        details = ""
        if action == "mute":
            details = f" на {self._duration_text(int(value or 0))}"
        elif action in {"warn", "unwarn", "clearwarns"}:
            details = f"; тепер {result['count']}/{int(self.config['warn_limit'])}"
            if result.get("auto_action"):
                details += f"; автоматично: {result['auto_action']}"
        await call.answer("✅ Готово")
        await self._refresh_panel(call, token, f"{self.ACTION_LABELS.get(action, action)}{details}")

    async def _slowmode_menu(self, call, token):
        if self._session(token) is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        labels = (("Вимкнути", 0), ("10 с", 10), ("30 с", 30), ("1 хв", 60), ("5 хв", 300), ("15 хв", 900), ("1 год", 3600))
        buttons = [
            {"text": label, "callback": self._set_slowmode, "args": (token, seconds)}
            for label, seconds in labels
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([{"text": "↩️ Назад", "callback": self._refresh_panel, "args": (token,)}])
        await call.edit("⏱ <b>Slow mode</b>\n\nОберіть інтервал між повідомленнями.", reply_markup=rows)

    async def _set_slowmode(self, call, token, seconds):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        try:
            chat = await self._client.get_entity(session["chat_id"])
            await self._client(ToggleSlowModeRequest(chat, int(seconds)))
        except Exception as error:
            logger.exception("GroupAdmin slow mode failed")
            await call.answer(str(error)[:180] or type(error).__name__, show_alert=True)
            return
        await self._refresh_panel(call, token, "Slow mode оновлено")

    async def _permissions_menu(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        chat = await self._client.get_entity(session["chat_id"])
        rights = getattr(chat, "default_banned_rights", None)
        rows = []
        for key, attrs in self.LOCK_GROUPS.items():
            locked = any(bool(getattr(rights, attr, False)) for attr in attrs) if rights else False
            rows.append(
                [
                    {
                        "text": f"{'🔒' if locked else '🔓'} {self.LOCK_LABELS[key]}",
                        "callback": self._set_default_permission,
                        "args": (token, key, not locked),
                    }
                ]
            )
        rows.append([{"text": "↩️ Назад", "callback": self._refresh_panel, "args": (token,)}])
        await call.edit(
            "🔐 <b>Дозволи звичайних учасників</b>\n\n"
            "Натисніть розділ, щоб заблокувати або дозволити його.",
            reply_markup=rows,
        )

    async def _set_default_permission(self, call, token, key, locked):
        session = self._session(token)
        if session is None or key not in self.LOCK_GROUPS:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        try:
            chat = await self._client.get_entity(session["chat_id"])
            rights = getattr(chat, "default_banned_rights", None) or ChatBannedRights(until_date=None)
            changed = False
            for attr in self.LOCK_GROUPS[key]:
                if hasattr(rights, attr):
                    setattr(rights, attr, bool(locked))
                    changed = True
            if not changed:
                raise RuntimeError("Ця версія Telethon не підтримує цей тип дозволу")
            await self._client(EditChatDefaultBannedRightsRequest(chat, rights))
        except Exception as error:
            logger.exception("GroupAdmin permission update failed")
            await call.answer(str(error)[:180] or type(error).__name__, show_alert=True)
            return
        await self._permissions_menu(call, token)
        await call.answer("🔒 Заблоковано" if locked else "🔓 Дозволено")

    async def _pin_reply(self, call, token):
        session = self._session(token)
        if session is None or not session.get("reply_id"):
            await call.answer(self.strings["expired"], show_alert=True)
            return
        try:
            await self._client.pin_message(session["chat_id"], session["reply_id"], notify=False)
        except Exception as error:
            logger.exception("GroupAdmin pin failed")
            await call.answer(str(error)[:180] or type(error).__name__, show_alert=True)
            return
        await self._refresh_panel(call, token, "Повідомлення закріплено")

    async def _run_user_command(self, message, action, permission="ban_users", duration=False):
        if not await self._require_permission(message, permission):
            return
        user, remainder = await self._resolve_target(message, utils.get_args_raw(message))
        if user is None:
            return
        value = None
        reason = remainder
        if duration:
            duration_token, possible_reason = self._split_first(remainder)
            default_seconds = int(self.config["default_mute_minutes"]) * 60
            if duration_token and self._parse_duration(duration_token) is not None:
                value = self._parse_duration(duration_token)
                reason = possible_reason
            elif duration_token and DURATION_RE.fullmatch(duration_token.lower()):
                await utils.answer(message, self.strings["bad_duration"])
                return
            else:
                value = default_seconds
        try:
            result = await self._apply_action(self._chat_id(message), user, action, value=value, reason=reason)
        except Exception as error:
            logger.exception("GroupAdmin command action failed: %s", action)
            text = str(error)
            await utils.answer(
                message,
                text if text.startswith("❌") else self.strings["failed"].format(html.escape(text or type(error).__name__)),
            )
            return
        details = ""
        if action == "mute":
            details = f"\n⏱ {self._duration_text(int(value or 0))}"
        if reason:
            details += f"\n📝 {html.escape(reason[:300])}"
        if action in {"warn", "unwarn", "clearwarns"}:
            details += f"\n⚠️ {result['count']}/{int(self.config['warn_limit'])}"
            if result.get("auto_action"):
                details += f" · автоматично: <b>{result['auto_action']}</b>"
        await utils.answer(
            message,
            self.strings["done"].format(
                action=self.ACTION_LABELS.get(action, action),
                user=html.escape(self._display_name(user)),
                details=details,
            ),
        )

    @loader.command(ru_doc="Відкрити панель адміністрування; дайте у відповідь на користувача")
    async def gadmin(self, message):
        """🛡 Панель керування групою або каналом"""
        if not await self._require_permission(message, "delete_messages"):
            return
        reply = None
        target = None
        with contextlib.suppress(Exception):
            reply = await message.get_reply_message()
        if reply is not None and getattr(reply, "sender_id", None):
            with contextlib.suppress(Exception):
                target = await self._client.get_entity(reply.sender_id)
        token = self._new_session(
            self._chat_id(message),
            getattr(target, "id", None),
            getattr(reply, "id", None),
        )
        try:
            opened = await self.inline.form(
                await self._panel_text(self._sessions[token]),
                message,
                reply_markup=self._panel_markup(token),
                force_me=True,
                disable_security=False,
            )
        except Exception:
            logger.exception("GroupAdmin panel failed")
            opened = False
        if not opened:
            self._sessions.pop(token, None)
            self._locks.pop(token, None)
            await utils.answer(message, self.strings["inline_failed"])

    @loader.command(ru_doc="Заблокувати: у відповідь або .gban @user [причина]")
    async def gban(self, message):
        """⛔ Заблокувати користувача"""
        await self._run_user_command(message, "ban")

    @loader.command(ru_doc="Розблокувати: у відповідь або .gunban @user")
    async def gunban(self, message):
        """✅ Розблокувати користувача"""
        await self._run_user_command(message, "unban")

    @loader.command(ru_doc="Видалити з чату: у відповідь або .gkick @user [причина]")
    async def gkick(self, message):
        """🚪 Видалити користувача без постійного бану"""
        await self._run_user_command(message, "kick")

    @loader.command(ru_doc="Обмежити: .gmute @user [10m|2h|3d|1w|forever] [причина]")
    async def gmute(self, message):
        """🔇 Тимчасово або назавжди заборонити надсилати повідомлення"""
        await self._run_user_command(message, "mute", duration=True)

    @loader.command(ru_doc="Зняти обмеження: у відповідь або .gunmute @user")
    async def gunmute(self, message):
        """🔊 Зняти обмеження користувача"""
        await self._run_user_command(message, "unmute")

    @loader.command(ru_doc="Попередити: у відповідь або .gwarn @user [причина]")
    async def gwarn(self, message):
        """⚠️ Додати попередження з автоматичним покаранням за лімітом"""
        await self._run_user_command(message, "warn")

    @loader.command(ru_doc="Зняти останнє попередження: .gunwarn @user")
    async def gunwarn(self, message):
        """➖ Зняти останнє попередження"""
        await self._run_user_command(message, "unwarn")

    @loader.command(ru_doc="Очистити всі попередження: .gclearwarns @user")
    async def gclearwarns(self, message):
        """🧹 Очистити попередження користувача"""
        await self._run_user_command(message, "clearwarns")

    @loader.command(ru_doc="Показати попередження: у відповідь або .gwarns @user")
    async def gwarns(self, message):
        """📋 Список попереджень користувача"""
        if not await self._require_permission(message, "ban_users"):
            return
        user, _ = await self._resolve_target(message, utils.get_args_raw(message))
        if user is None:
            return
        items = self._warnings().get(self._warning_key(self._chat_id(message), user.id), [])
        if not items:
            body = "<i>Попереджень немає.</i>"
        else:
            body = "\n".join(
                f"{index}. {html.escape(str(item.get('reason') or 'Без причини'))}"
                for index, item in enumerate(items, 1)
            )
        await utils.answer(
            message,
            "⚠️ <b>Попередження</b> · "
            f"{html.escape(self._display_name(user))}\n\n{body}\n\n"
            f"Ліміт: <b>{len(items)}/{int(self.config['warn_limit'])}</b>",
        )

    @loader.command(ru_doc="Закріпити повідомлення, на яке дана відповідь")
    async def gpin(self, message):
        """📌 Закріпити повідомлення"""
        if not await self._require_permission(message, "pin_messages"):
            return
        reply = await message.get_reply_message()
        if reply is None:
            await utils.answer(message, "❌ Дайте команду у відповідь на повідомлення.")
            return
        try:
            await self._client.pin_message(self._chat_id(message), reply.id, notify=False)
        except Exception as error:
            await utils.answer(message, self.strings["failed"].format(html.escape(str(error))))
            return
        await utils.answer(message, "✅ Повідомлення закріплено.")

    @loader.command(ru_doc="Відкріпити повідомлення у відповіді або всі: .gunpin all")
    async def gunpin(self, message):
        """📍 Відкріпити повідомлення"""
        if not await self._require_permission(message, "pin_messages"):
            return
        reply = await message.get_reply_message()
        raw = str(utils.get_args_raw(message) or "").strip().lower()
        try:
            if reply is not None:
                await self._client.unpin_message(self._chat_id(message), reply.id)
            elif raw == "all":
                await self._client.unpin_message(self._chat_id(message), None)
            else:
                await utils.answer(message, "❌ Дайте у відповідь або використайте <code>.gunpin all</code>.")
                return
        except Exception as error:
            await utils.answer(message, self.strings["failed"].format(html.escape(str(error))))
            return
        await utils.answer(message, "✅ Закріплення прибрано.")

    @loader.command(ru_doc="Змінити slow mode: .gslowmode off|10s|30s|1m|5m|15m|1h")
    async def gslowmode(self, message):
        """⏱ Змінити інтервал між повідомленнями"""
        if not await self._require_permission(message, "change_info"):
            return
        raw = str(utils.get_args_raw(message) or "").strip().lower()
        seconds = 0 if raw in {"0", "off", "вимк", "выкл"} else self._parse_duration(raw)
        if seconds not in SLOWMODE_VALUES:
            await utils.answer(message, self.strings["bad_slowmode"])
            return
        try:
            chat = await self._client.get_entity(self._chat_id(message))
            await self._client(ToggleSlowModeRequest(chat, int(seconds)))
        except Exception as error:
            await utils.answer(message, self.strings["failed"].format(html.escape(str(error))))
            return
        await utils.answer(message, f"✅ Slow mode: <b>{self._duration_text(seconds) if seconds else 'вимкнено'}</b>.")

    async def _lock_command(self, message, locked):
        if not await self._require_permission(message, "change_info"):
            return
        key = str(utils.get_args_raw(message) or "").strip().lower()
        if key not in self.LOCK_GROUPS:
            await utils.answer(message, self.strings["bad_lock"])
            return
        try:
            chat = await self._client.get_entity(self._chat_id(message))
            rights = getattr(chat, "default_banned_rights", None) or ChatBannedRights(until_date=None)
            changed = False
            for attr in self.LOCK_GROUPS[key]:
                if hasattr(rights, attr):
                    setattr(rights, attr, bool(locked))
                    changed = True
            if not changed:
                raise RuntimeError("Цей тип дозволу не підтримується")
            await self._client(EditChatDefaultBannedRightsRequest(chat, rights))
        except Exception as error:
            await utils.answer(message, self.strings["failed"].format(html.escape(str(error))))
            return
        await utils.answer(
            message,
            f"{'🔒 Заблоковано' if locked else '🔓 Дозволено'}: <b>{self.LOCK_LABELS[key]}</b>.",
        )

    @loader.command(ru_doc="Заблокувати розділ: .glock messages|media|stickers|links|polls")
    async def glock(self, message):
        """🔒 Заборонити тип повідомлень звичайним учасникам"""
        await self._lock_command(message, True)

    @loader.command(ru_doc="Дозволити розділ: .gunlock messages|media|stickers|links|polls")
    async def gunlock(self, message):
        """🔓 Дозволити тип повідомлень звичайним учасникам"""
        await self._lock_command(message, False)
