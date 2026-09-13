# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 💬 Кімнати спілкування між авторизованими HikkaNet-вузлами.
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
__version__ = (1, 0, 0)
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
            if self.config["notify"] and self._network() is not None:
                try:
                    await self._poll_once()
                    self._last_error = ""
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error = str(error).replace("\n", " ")[:240]
                    logger.warning("HikkaNetChat polling failed: %s", self._last_error)
            await self._wait(int(self.config["poll_interval"]))

    async def _poll_once(self):
        network = self._network()
        if network is None:
            return
        cursors = self._cursors()
        instance_id = str(network.config["instance_id"])
        for room in self._rooms():
            if room not in cursors:
                latest = await network.api_events(
                    topic=self._topic(room), limit=1, latest=True
                )
                events = latest.get("events", [])
                cursors[room] = max(
                    [int(item.get("id", 0)) for item in events] or [0]
                )
                continue
            after_id = int(cursors.get(room, 0) or 0)
            data = await network.api_events(
                after_id=after_id, topic=self._topic(room), limit=50
            )
            events = data.get("events", [])
            for event in events:
                cursors[room] = max(
                    int(cursors.get(room, 0) or 0), int(event.get("id", 0))
                )
                if event.get("sender_instance_id") == instance_id:
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

    @loader.command(ru_doc="Стан і довідка HikkaNetChat")
    async def hkchat(self, message):
        """💬 Відкрити HikkaNetChat"""
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
        await utils.answer(message, f"✅ Новий псевдонім: <b>{_esc(nickname)}</b>")

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
        network = self._network()
        if network is None:
            await utils.answer(message, "❌ HikkaNet не встановлено або не налаштовано.")
            return
        raw = utils.get_args_raw(message).strip() or self.config["active_room"]
        try:
            room = self._room(raw)
            data = await network.api_events(
                topic=self._topic(room),
                limit=int(self.config["history_limit"]),
                latest=True,
            )
        except Exception as error:
            self._last_error = str(error).replace("\n", " ")[:240]
            await utils.answer(message, f"❌ <code>{_esc(self._last_error)}</code>")
            return
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
        text = header + (
            "\n\n".join(selected)
            if selected
            else "<i>Повідомлень ще немає.</i>"
        )
        await utils.answer(message, text)
