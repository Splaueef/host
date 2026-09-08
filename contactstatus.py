# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 🟢 Щоденна статистика online-статусу контактів.

import datetime
import logging

from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import UpdateUserStatus, UserStatusOnline

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class ContactStatusMod(loader.Module):
    """🟢 Спостерігає за online-статусом ваших Telegram-контактів"""

    strings = {
        "name": "ContactStatus",
        "empty": (
            "📭 <b>Сьогодні online-активність контактів ще не "
            "зафіксована.</b>\n<i>Модуль бачить лише статуси, які Telegram "
            "дозволяє бачити.</i>"
        ),
        "load_failed": "ContactStatus: не вдалося завантажити контакти",
    }

    async def client_ready(self, client, db):
        self._client = client
        self._ensure_storage()
        try:
            result = await client(GetContactsRequest(hash=0))
            now = self._now()
            contacts = self.get("contacts", {})
            online = set()
            for user in getattr(result, "users", []):
                key = str(user.id)
                contacts[key] = {
                    "name": self._display_name(user),
                    "username": getattr(user, "username", None),
                }
                if isinstance(getattr(user, "status", None), UserStatusOnline):
                    online.add(key)
            self.set("contacts", contacts)
            # Reconcile persisted open sessions after a module/userbot restart.
            # Otherwise a contact that went offline while Hikka was stopped
            # would incorrectly look online for the entire downtime.
            for key in set(self.get("active", {})) | online:
                self._set_online(key, key in online, now)
        except Exception:
            logger.exception(self.strings["load_failed"])

    def _ensure_storage(self):
        for key in ("contacts", "days", "active"):
            if not self.get(key):
                self.set(key, {})

    @staticmethod
    def _now():
        return datetime.datetime.now().astimezone()

    @staticmethod
    def _display_name(user):
        return (
            " ".join(
                part
                for part in (
                    getattr(user, "first_name", None),
                    getattr(user, "last_name", None),
                )
                if part
            )
            or getattr(user, "username", None)
            or "Unknown"
        )

    @staticmethod
    def _day_start(moment):
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    def _store_interval(self, user_id, start, end):
        """Split a session at local midnight so daily reports stay exact."""
        if end <= start:
            return
        days = self.get("days", {})
        cursor = start
        while cursor.date() < end.date():
            boundary = self._day_start(cursor) + datetime.timedelta(days=1)
            days.setdefault(cursor.date().isoformat(), {}).setdefault(
                str(user_id), []
            ).append([cursor.timestamp(), boundary.timestamp()])
            cursor = boundary
        days.setdefault(cursor.date().isoformat(), {}).setdefault(
            str(user_id), []
        ).append([cursor.timestamp(), end.timestamp()])
        self.set("days", days)

    def _set_online(self, user_id, online, moment=None):
        moment = moment or self._now()
        key = str(user_id)
        if key not in self.get("contacts", {}):
            return
        active = self.get("active", {})
        if online:
            active.setdefault(key, moment.timestamp())
        elif key in active:
            start = datetime.datetime.fromtimestamp(active.pop(key), moment.tzinfo)
            self._store_interval(key, start, moment)
        self.set("active", active)

    @loader.raw_handler(UpdateUserStatus)
    async def status_watcher(self, update):
        self._set_online(
            update.user_id,
            isinstance(update.status, UserStatusOnline),
        )

    def _today_intervals(self, now):
        start = self._day_start(now)
        result = {
            key: [list(interval) for interval in intervals]
            for key, intervals in self.get("days", {}).get(
                now.date().isoformat(), {}
            ).items()
        }
        for key, stamp in self.get("active", {}).items():
            session_start = max(float(stamp), start.timestamp())
            if session_start < now.timestamp():
                result.setdefault(key, []).append([session_start, now.timestamp()])
        return result

    @staticmethod
    def _duration(seconds):
        seconds = max(0, int(seconds))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _clock(stamp, tzinfo):
        return datetime.datetime.fromtimestamp(stamp, tzinfo).strftime("%H:%M:%S")

    @staticmethod
    def _overlaps(intervals):
        events = []
        for user_id, spans in intervals.items():
            for start, end in spans:
                events.extend(((start, 1, user_id), (end, -1, user_id)))
        # Ends precede starts at the same instant; zero-length overlap is ignored.
        events.sort(key=lambda item: (item[0], item[1]))
        active, overlaps, began = set(), [], None
        for stamp, delta, user_id in events:
            was_shared = len(active) >= 2
            if delta < 0:
                active.discard(user_id)
            else:
                active.add(user_id)
            is_shared = len(active) >= 2
            if not was_shared and is_shared:
                began = stamp
            elif was_shared and not is_shared and began is not None:
                overlaps.append((began, stamp))
                began = None
        return overlaps

    def _report(self, now):
        intervals = self._today_intervals(now)
        if not intervals:
            return self.strings["empty"]
        contacts = self.get("contacts", {})
        start = self._day_start(now).strftime("%H:%M")
        text = (
            f"🟢 <b>Активність контактів</b>\n"
            f"📅 Сьогодні, <b>{start}–{now.strftime('%H:%M:%S')}</b>\n\n"
        )
        ordered = sorted(
            intervals.items(),
            key=lambda item: sum(end - begin for begin, end in item[1]),
            reverse=True,
        )
        for user_id, spans in ordered:
            contact = contacts.get(user_id, {})
            name = utils.escape_html(str(contact.get("name", user_id)))
            total = sum(end - begin for begin, end in spans)
            periods = ", ".join(
                f"{self._clock(begin, now.tzinfo)}–{self._clock(end, now.tzinfo)}"
                for begin, end in spans
            )
            text += (
                f"👤 <b>{name}</b> — {self._duration(total)}\n"
                f"<code>{periods}</code>\n\n"
            )
        overlaps = self._overlaps(intervals)
        text += "🤝 <b>Спільний online:</b> "
        if not overlaps:
            return text + "—"
        total = sum(end - begin for begin, end in overlaps)
        text += self._duration(total) + "\n" + "\n".join(
            f"<code>{self._clock(begin, now.tzinfo)}–{self._clock(end, now.tzinfo)}</code>"
            for begin, end in overlaps
        )
        return text

    @loader.command(ru_doc="Online-статистика контактів з 00:00 до поточного часу")
    async def contactstats(self, message):
        """📊 Показати online-статистику контактів з 00:00 до зараз"""
        await utils.answer(message, self._report(self._now()))
