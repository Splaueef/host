# meta developer: @Huai_Baike
# meta version: 2.0.0
# meta description: 💬 Глобальні кімнати HikkaNet з online-учасниками та історією.
# scope: hikka_only

"""Small room-based chat built on top of the authenticated HikkaNet API."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time

from .. import loader, utils


logger = logging.getLogger(__name__)
__version__ = (2, 0, 0)
ROOM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _esc_limit(value, maximum):
    """Escape text without truncating in the middle of an HTML entity."""
    result = []
    size = 0
    for character in str(value if value is not None else ""):
        escaped = html.escape(character, quote=True)
        if size + len(escaped) > maximum:
            result.append("…")
            break
        result.append(escaped)
        size += len(escaped)
    return "".join(result)


@loader.tds
class HikkaNetChatMod(loader.Module):
    """💬 Чат між Hikka через окремий захищений Hikka Hub сервіс."""

    strings = {"name": "HikkaNetChat"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "rooms",
                ["lobby"],
                "Кімнати, з яких отримувати повідомлення",
                validator=loader.validators.Series(loader.validators.String()),
            ),
            loader.ConfigValue(
                "active_room",
                "lobby",
                "Кімната для .hksay",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "nickname",
                "",
                "Псевдонім у HikkaNetChat; порожньо — ім'я HikkaNet-вузла",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "notify",
                True,
                "Надсилати нові повідомлення у Збережені повідомлення",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "poll_interval",
                8,
                "Інтервал перевірки кімнат у секундах",
                validator=loader.validators.Integer(minimum=5, maximum=300),
            ),
            loader.ConfigValue(
                "history_limit",
                20,
                "Кількість повідомлень у .hkhistory",
                validator=loader.validators.Integer(minimum=5, maximum=50),
            ),
        )
        self._client = None
        self._me = None
        self._poll_task = None
        self._stop_event = asyncio.Event()
        self._last_error = ""
        self._last_presence = 0

    async def client_ready(self, client, db):
        self._client = client
        self._me = await client.get_me()
        self._stop_event.clear()
        if not self._poll_task or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._poll_worker(), name="hikkanet-chat-poll"
            )

    async def on_unload(self):
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        self._poll_task = None
        network = self._network()
        leave = getattr(network, "api_chat_leave", None) if network else None
        if callable(leave):
            for room in self._rooms()[:32]:
                with contextlib.suppress(Exception):
                    await leave(room)

    @staticmethod
    def _room(value):
        value = str(value or "").strip().lower()
        if not ROOM_RE.fullmatch(value):
            raise ValueError("Кімната: a-z, 0-9, _ або -, до 48 символів")
        return value

    def _rooms(self):
        result = []
        for raw in list(self.config["rooms"] or []):
            with contextlib.suppress(ValueError):
                room = self._room(raw)
                if room not in result:
                    result.append(room)
        return result

    def _network(self, require_configured=True):
        network = None
        with contextlib.suppress(Exception):
            network = self.lookup("HikkaNet")
        if network is None:
            network = next(
                (
                    module
                    for module in getattr(
                        getattr(self, "allmodules", None), "modules", []
                    )
                    if module.__class__.__name__ == "HikkaNetMod"
                ),
                None,
            )
        configured = getattr(network, "_configured", None) if network else None
        if network is None or (
            require_configured and callable(configured) and not configured()
        ):
            return None
        return network

    def _nickname(self):
        configured = str(self.config["nickname"] or "").strip()
        if configured:
            return configured[:40]
        network = self._network(require_configured=False)
        if network is not None:
            display_name = str(network.config.get("display_name", "") or "").strip()
            if display_name:
                return display_name[:40]
        if self._me is not None:
            return str(
                getattr(self._me, "first_name", "")
                or getattr(self._me, "username", "")
                or "Hikka"
            )[:40]
        return "Hikka"

    @staticmethod
    def _topic(room):
        return f"chat.{room}"

    def _cursors(self):
        value = self.get("chat_cursors", {})
        return dict(value) if isinstance(value, dict) else {}

    def _save_cursors(self, value):
        self.set("chat_cursors", value)

    def _count(self, key, delta=1):
        counters = self.get("chat_stats", {})
        counters = dict(counters) if isinstance(counters, dict) else {}
        counters[key] = int(counters.get(key, 0) or 0) + int(delta)
        self.set("chat_stats", counters)
        with contextlib.suppress(Exception):
            hub = self.lookup("ModuleHub")
            if hub is not None:
                hub.report_stat(self, key, delta)

    def modulehub_stats(self):
        counters = self.get("chat_stats", {})
        counters = counters if isinstance(counters, dict) else {}
        return {
            "joined_rooms": len(self._rooms()),
            "messages_sent": int(counters.get("messages_sent", 0) or 0),
            "messages_received": int(counters.get("messages_received", 0) or 0),
        }

    async def _wait(self, seconds):
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=max(1, seconds))
        except asyncio.TimeoutError:
            pass

    async def _poll_worker(self):
        await self._wait(5)
        while not self._stop_event.is_set():
            if self._network() is not None:
                try:
                    await self._sync_presence()
                    if self.config["notify"]:
                        await self._poll_once()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error = str(error).replace("\n", " ")[:240]
                    logger.warning("HikkaNetChat polling failed: %s", self._last_error)
            await self._wait(int(self.config["poll_interval"]))

    async def _sync_presence(self, force=False):
        network = self._network()
        publish = getattr(network, "api_chat_presence", None) if network else None
        if not callable(publish):
            return
        now = int(time.time())
        if not force and now - int(self._last_presence or 0) < 45:
            return
        nickname = self._nickname()
        for room in self._rooms()[:32]:
            await publish(room, nickname)
        self._last_presence = now

    async def _poll_once(self):
        network = self._network()
        if network is None:
            return
        await self._sync_presence()
        cursors = self._cursors()
        instance_id = str(network.config["instance_id"])
        rooms = set(self._rooms())
        global_cursor = int(cursors.get("__global__", 0) or 0)
        if "__global__" not in cursors:
            latest = await network.api_events(limit=1, latest=True)
            events = latest.get("events", [])
            cursors["__global__"] = max(
                [int(item.get("id", 0)) for item in events] or [0]
            )
            self._save_cursors(cursors)
            return
        for _ in range(3):
            data = await network.api_events(after_id=global_cursor, limit=200)
            events = data.get("events", [])
            for event in events:
                global_cursor = max(global_cursor, int(event.get("id", 0)))
                topic = str(event.get("topic", ""))
                room = topic[5:] if topic.startswith("chat.") else ""
                if room not in rooms or event.get("sender_instance_id") == instance_id:
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("kind") != "chat.message":
                    continue
                text = str(payload.get("body", ""))[:2000]
                nickname = str(payload.get("nickname", "Hikka"))[:40]
                await self._client.send_message(
                    "me",
                    f"💬 <b>HikkaNet · #{_esc(room)}</b>\n"
                    f"<b>{_esc(nickname)}</b> "
                    f"<code>{_esc(event.get('sender_instance_id'))}</code>\n\n"
                    f"{_esc_limit(text, 3500)}",
                    parse_mode="html",
                )
                self._count("messages_received")
            if len(events) < 200:
                break
        cursors["__global__"] = global_cursor
        self._save_cursors(cursors)

    @staticmethod
    def _event_line(event):
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "chat.message":
            return None
        stamp = int(event.get("created_at", 0) or 0)
        shown_time = time.strftime("%d.%m %H:%M", time.localtime(stamp))
        return (
            f"<b>{_esc(payload.get('nickname') or 'Hikka')}</b> "
            f"<code>{_esc(event.get('sender_instance_id'))}</code> · {shown_time}\n"
            f"{_esc_limit(str(payload.get('body', ''))[:2000], 3000)}"
        )

    def _status_text(self):
        network = self._network()
        rooms = self._rooms()
        active = str(self.config["active_room"] or "—")
        lines = [
            "💬 <b>HikkaNetChat</b>",
            "",
            f"Мережа: <b>{'🟢 підключена' if network else '🔴 недоступна'}</b>",
            f"Псевдонім: <b>{_esc(self._nickname())}</b>",
            f"Активна кімната: <code>#{_esc(active)}</code>",
            "Кімнати: " + (", ".join(f"<code>#{_esc(x)}</code>" for x in rooms) or "—"),
            "",
            "<code>.hksay текст</code> — написати",
            "<code>.hkhistory [room]</code> — історія",
            "<code>.hkrooms</code> — глобальні кімнати",
            "<code>.hkmembers [room]</code> — хто online",
            "<code>.hkjoin room</code> — приєднатися",
            "<code>.hkroom room</code> — вибрати активну",
            "<code>.hkleave [room]</code> — вийти",
            "<code>.hknick [ім'я]</code> — псевдонім",
        ]
        if self._last_error:
            lines.extend(["", f"Остання помилка: <code>{_esc(self._last_error)}</code>"])
        if network is not None and str(network.config.get("server_url", "")).startswith(
            "http://"
        ):
            lines.extend(
                [
                    "",
                    "⚠️ <i>HTTP не шифрує чат. Для зовнішньої мережі потрібен "
                    "HTTPS або VPN.</i>",
                ]
            )
        return "\n".join(lines)

    def _panel_markup(self):
        return [
            [
                {"text": "🔄 Оновити", "callback": self._status_callback},
                {"text": "🌐 Кімнати", "callback": self._rooms_callback},
            ],
            [
                {"text": "👥 Учасники", "callback": self._members_callback},
                {"text": "🕘 Історія", "callback": self._history_callback},
            ],
            [{"text": "✖️ Закрити", "action": "close"}],
        ]

    async def _rooms_text(self):
        network = self._network()
        method = getattr(network, "api_chat_rooms", None) if network else None
        if not callable(method):
            raise RuntimeError("Онови модуль HikkaNet до версії 2.0.0")
        data = await method(limit=50)
        lines = ["🌐 <b>HikkaNetChat · кімнати</b>", ""]
        for item in data.get("rooms", []):
            lines.append(
                f"• <code>#{_esc(item.get('room'))}</code> — "
                f"🟢 <b>{int(item.get('online', 0))}</b> · "
                f"💬 {int(item.get('messages_24h', 0))} за 24 год"
            )
        if not data.get("rooms"):
            lines.append("<i>Активних кімнат ще немає.</i>")
        return "\n".join(lines)

    async def _members_text(self, room=None):
        network = self._network()
        method = getattr(network, "api_chat_members", None) if network else None
        if not callable(method):
            raise RuntimeError("Онови модуль HikkaNet до версії 2.0.0")
        room = self._room(room or self.config["active_room"])
        data = await method(room, limit=100)
        lines = [f"👥 <b>HikkaNetChat · #{_esc(room)}</b>", ""]
        for item in data.get("members", []):
            nickname = item.get("nickname") or item.get("display_name") or "Hikka"
            lines.append(
                f"🟢 <b>{_esc(nickname)}</b> "
                f"<code>{_esc(item.get('instance_id'))}</code>"
            )
        if not data.get("members"):
            lines.append("<i>Зараз нікого немає online.</i>")
        return "\n".join(lines)

    async def _history_text(self, room):
        network = self._network()
        if network is None:
            raise RuntimeError("HikkaNet не встановлено або не налаштовано")
        room = self._room(room)
        data = await network.api_events(
            topic=self._topic(room),
            limit=int(self.config["history_limit"]),
            latest=True,
        )
        events = data.get("events", [])
        lines = [line for line in (self._event_line(event) for event in events) if line]
        if events:
            cursors = self._cursors()
            cursors[room] = max(int(item.get("id", 0)) for item in events)
            self._save_cursors(cursors)
        header = f"💬 <b>HikkaNet · #{_esc(room)}</b>\n\n"
        selected = []
        used = len(header)
        for line in reversed(lines):
            if used + len(line) + 2 > 4000:
                break
            selected.insert(0, line)
            used += len(line) + 2
        return header + (
            "\n\n".join(selected)
            if selected
            else "<i>Повідомлень ще немає.</i>"
        )

    async def _edit_panel(self, call, producer):
        try:
            text = await producer()
            await call.edit(text, reply_markup=self._panel_markup())
        except Exception as error:
            self._last_error = str(error).replace("\n", " ")[:240]
            await call.answer(self._last_error, show_alert=True)

    async def _status_callback(self, call):
        with contextlib.suppress(Exception):
            await self._sync_presence(force=True)
        await call.edit(self._status_text(), reply_markup=self._panel_markup())

    async def _rooms_callback(self, call):
        await self._edit_panel(call, self._rooms_text)

    async def _members_callback(self, call):
        await self._edit_panel(call, self._members_text)

    async def _history_callback(self, call):
        await self._edit_panel(
            call, lambda: self._history_text(self.config["active_room"])
        )

    @loader.command(ru_doc="Стан і довідка HikkaNetChat")
    async def hkchat(self, message):
        """💬 Відкрити HikkaNetChat"""
        with contextlib.suppress(Exception):
            await self._sync_presence(force=True)
        try:
            opened = await self.inline.form(
                self._status_text(), message, reply_markup=self._panel_markup()
            )
            if opened:
                return
        except Exception:
            logger.exception("HikkaNetChat inline panel failed")
        await utils.answer(message, self._status_text())

    @loader.command(ru_doc="Приєднатися до HikkaNet-кімнати")
    async def hkjoin(self, message):
        """➕ .hkjoin <room>"""
        try:
            room = self._room(utils.get_args_raw(message))
        except ValueError as error:
            await utils.answer(message, f"❌ <code>{_esc(error)}</code>")
            return
        rooms = self._rooms()
        if room not in rooms:
            rooms.append(room)
        self.config["rooms"] = rooms
        self.config["active_room"] = room
        cursors = self._cursors()
        cursors.pop(room, None)
        self._save_cursors(cursors)
        with contextlib.suppress(Exception):
            await self._sync_presence(force=True)
        await utils.answer(message, f"✅ Активна кімната: <code>#{_esc(room)}</code>")

    @loader.command(ru_doc="Змінити активну HikkaNet-кімнату")
    async def hkroom(self, message):
        """➡️ .hkroom <room>"""
        try:
            room = self._room(utils.get_args_raw(message))
        except ValueError as error:
            await utils.answer(message, f"❌ <code>{_esc(error)}</code>")
            return
        if room not in self._rooms():
            await utils.answer(message, "❌ Спочатку приєднайся через <code>.hkjoin</code>.")
            return
        self.config["active_room"] = room
        await utils.answer(message, f"➡️ Активна кімната: <code>#{_esc(room)}</code>")

    @loader.command(ru_doc="Вийти з HikkaNet-кімнати")
    async def hkleave(self, message):
        """➖ .hkleave [room]"""
        raw = utils.get_args_raw(message).strip() or self.config["active_room"]
        try:
            room = self._room(raw)
        except ValueError as error:
            await utils.answer(message, f"❌ <code>{_esc(error)}</code>")
            return
        rooms = [item for item in self._rooms() if item != room]
        network = self._network()
        leave = getattr(network, "api_chat_leave", None) if network else None
        if callable(leave):
            with contextlib.suppress(Exception):
                await leave(room)
        self.config["rooms"] = rooms
        if self.config["active_room"] == room:
            self.config["active_room"] = rooms[0] if rooms else ""
        await utils.answer(message, f"➖ Вихід із <code>#{_esc(room)}</code>.")

    @loader.command(ru_doc="Змінити псевдонім у HikkaNetChat")
    async def hknick(self, message):
        """🪪 .hknick [ім'я]; без аргументу — показати поточне"""
        nickname = utils.get_args_raw(message).strip()
        if not nickname:
            await utils.answer(message, f"🪪 Псевдонім: <b>{_esc(self._nickname())}</b>")
            return
        if len(nickname) > 40 or any(ch in nickname for ch in "\r\n"):
            await utils.answer(message, "❌ Псевдонім має містити до 40 символів.")
            return
        self.config["nickname"] = nickname
        with contextlib.suppress(Exception):
            await self._sync_presence(force=True)
        await utils.answer(message, f"✅ Новий псевдонім: <b>{_esc(nickname)}</b>")

    @loader.command(ru_doc="Показати активні кімнати HikkaNetChat")
    async def hkrooms(self, message):
        """🌐 Кімнати, online і повідомлення за 24 години"""
        try:
            await utils.answer(message, await self._rooms_text())
        except Exception as error:
            await utils.answer(message, f"❌ <code>{_esc(error)}</code>")

    @loader.command(ru_doc="Показати online-учасників чат-кімнати")
    async def hkmembers(self, message):
        """👥 .hkmembers [room]"""
        raw = utils.get_args_raw(message).strip() or self.config["active_room"]
        try:
            await utils.answer(message, await self._members_text(raw))
        except Exception as error:
            await utils.answer(message, f"❌ <code>{_esc(error)}</code>")

    @loader.command(ru_doc="Надіслати повідомлення в активну HikkaNet-кімнату")
    async def hksay(self, message):
        """📨 .hksay <текст>"""
        body = utils.get_args_raw(message).strip()
        if not body:
            await utils.answer(message, "Використання: <code>.hksay текст</code>")
            return
        if len(body) > 2000:
            await utils.answer(message, "❌ Повідомлення задовге (максимум 2000 символів).")
            return
        network = self._network()
        if network is None:
            await utils.answer(message, "❌ HikkaNet не встановлено або не налаштовано.")
            return
        try:
            room = self._room(self.config["active_room"])
            await network.api_publish(
                self._topic(room),
                {
                    "kind": "chat.message",
                    "nickname": self._nickname(),
                    "body": body,
                    "sent_at": int(time.time()),
                },
                ttl_seconds=7 * 86400,
            )
        except Exception as error:
            self._last_error = str(error).replace("\n", " ")[:240]
            await utils.answer(message, f"❌ <code>{_esc(self._last_error)}</code>")
            return
        self._count("messages_sent")
        await utils.answer(message, f"✅ Надіслано в <code>#{_esc(room)}</code>.")

    @loader.command(ru_doc="Показати останні повідомлення HikkaNet-кімнати")
    async def hkhistory(self, message):
        """🕘 .hkhistory [room]"""
        raw = utils.get_args_raw(message).strip() or self.config["active_room"]
        try:
            text = await self._history_text(raw)
        except Exception as error:
            self._last_error = str(error).replace("\n", " ")[:240]
            await utils.answer(message, f"❌ <code>{_esc(self._last_error)}</code>")
            return
        await utils.answer(message, text)
