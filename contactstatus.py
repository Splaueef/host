# meta developer: @Huai_Baike
# meta version: 2.0.0
# meta description: 🟢 Детальна статистика online-активності контактів.

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
            "📭 <b>Online-активність контактів за цей період не "
            "зафіксована.</b>\n<i>Модуль бачить лише статуси, які Telegram "
            "дозволяє бачити.</i>"
        ),
        "load_failed": "ContactStatus: не вдалося завантажити контакти",
        "bad_period": (
            "⚠️ <b>Невірний період.</b> Використайте: "
            "<code>.contactstats [today|yesterday|7]</code>"
        ),
        "cleared": "🗑 <b>Історію online-активності очищено.</b>",
    }

    RETENTION_DAYS = 31
    MAX_CONTACTS = 15
    MAX_PERIODS = 4

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
            self._prune(now.date())
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
            self._append_interval(
                days, cursor.date().isoformat(), user_id, cursor, boundary
            )
            cursor = boundary
        self._append_interval(days, cursor.date().isoformat(), user_id, cursor, end)
        self.set("days", days)

    @staticmethod
    def _append_interval(days, day, user_id, start, end):
        """Append a span, coalescing duplicate/adjacent Telegram updates."""
        spans = days.setdefault(day, {}).setdefault(str(user_id), [])
        current = [start.timestamp(), end.timestamp()]
        if spans and current[0] <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], current[1])
        else:
            spans.append(current)

    def _prune(self, today):
        cutoff = today - datetime.timedelta(days=self.RETENTION_DAYS - 1)
        days = self.get("days", {})
        fresh = {key: value for key, value in days.items() if key >= cutoff.isoformat()}
        if fresh != days:
            self.set("days", fresh)

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
            for key, intervals in self.get("days", {})
            .get(now.date().isoformat(), {})
            .items()
        }
        for key, stamp in self.get("active", {}).items():
            session_start = max(float(stamp), start.timestamp())
            if session_start < now.timestamp():
                result.setdefault(key, []).append([session_start, now.timestamp()])
        return result

    def _period_intervals(self, now, days_count=1, offset=0):
        """Return persisted and live spans clipped to a local-day range."""
        end_day = now.date() - datetime.timedelta(days=offset)
        start_day = end_day - datetime.timedelta(days=days_count - 1)
        range_start = self._day_start(now).replace(
            year=start_day.year, month=start_day.month, day=start_day.day
        )
        range_end = min(
            now,
            self._day_start(now).replace(
                year=end_day.year, month=end_day.month, day=end_day.day
            )
            + datetime.timedelta(days=1),
        )
        result = {}
        cursor = start_day
        stored = self.get("days", {})
        while cursor <= end_day:
            for user_id, spans in stored.get(cursor.isoformat(), {}).items():
                result.setdefault(user_id, []).extend([list(span) for span in spans])
            cursor += datetime.timedelta(days=1)
        for user_id, stamp in self.get("active", {}).items():
            begin = max(float(stamp), range_start.timestamp())
            if begin < range_end.timestamp():
                result.setdefault(user_id, []).append([begin, range_end.timestamp()])
        return result, range_start, range_end

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

    @staticmethod
    def _peak_online(intervals):
        events = []
        for spans in intervals.values():
            for start, end in spans:
                events.extend(((start, 1), (end, -1)))
        current = peak = 0
        for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
            current += delta
            peak = max(peak, current)
        return peak

    @staticmethod
    def _bar(seconds, maximum, width=10):
        filled = round(width * seconds / maximum) if maximum else 0
        return "█" * filled + "░" * (width - filled)

    def _report(self, now, days_count=1, offset=0):
        intervals, range_start, range_end = self._period_intervals(
            now, days_count, offset
        )
        if not intervals:
            return self.strings["empty"]
        contacts = self.get("contacts", {})
        ordered = sorted(
            intervals.items(),
            key=lambda item: sum(end - begin for begin, end in item[1]),
            reverse=True,
        )
        all_time = sum(sum(end - begin for begin, end in spans) for _, spans in ordered)
        active_now = (
            len(set(intervals) & set(self.get("active", {}))) if not offset else 0
        )
        period = (
            f"сьогодні, {range_start:%H:%M}–{range_end:%H:%M}"
            if days_count == 1 and not offset
            else f"{range_start:%d.%m.%Y}–{range_end:%d.%m.%Y}"
        )
        text = (
            f"🟢 <b>ContactStatus · огляд</b>\n"
            f"📅 <b>{period}</b>\n\n"
            f"👥 Активних: <b>{len(intervals)}</b>"
            f"  ·  🟢 Зараз: <b>{active_now}</b>\n"
            f"⏱ Сумарно: <b>{self._duration(all_time)}</b>"
            f"  ·  📈 Пік: <b>{self._peak_online(intervals)}</b>\n\n"
            "🏆 <b>Рейтинг активності</b>\n"
        )
        maximum = sum(end - begin for begin, end in ordered[0][1])
        for index, (user_id, spans) in enumerate(ordered[: self.MAX_CONTACTS], 1):
            contact = contacts.get(user_id, {})
            name = utils.escape_html(str(contact.get("name", user_id)))
            total = sum(end - begin for begin, end in spans)
            periods = " · ".join(
                f"{self._clock(begin, now.tzinfo)}–{self._clock(end, now.tzinfo)}"
                for begin, end in spans[-self.MAX_PERIODS :]
            )
            status = (
                " 🟢" if str(user_id) in self.get("active", {}) and not offset else ""
            )
            text += (
                f"\n<b>{index}. {name}</b>{status}  ·  <b>{self._duration(total)}</b>\n"
                f"<code>{self._bar(total, maximum)}  {len(spans)} сеанс.</code>\n"
                f"└ <code>{periods}</code>"
            )
            if len(spans) > self.MAX_PERIODS:
                text += f" <i>(+ще {len(spans) - self.MAX_PERIODS})</i>"
            text += "\n"
        if len(ordered) > self.MAX_CONTACTS:
            text += f"\n<i>…і ще {len(ordered) - self.MAX_CONTACTS} контактів</i>\n"
        overlaps = self._overlaps(intervals)
        text += "\n🤝 <b>Спільний online:</b> "
        if not overlaps:
            return text + "—"
        total = sum(end - begin for begin, end in overlaps)
        text += (
            self._duration(total)
            + "\n"
            + "\n".join(
                f"<code>{self._clock(begin, now.tzinfo)}–{self._clock(end, now.tzinfo)}</code>"
                for begin, end in overlaps[-self.MAX_PERIODS :]
            )
        )
        return text

    @loader.command(ru_doc="Online-статистика контактів з 00:00 до поточного часу")
    async def contactstats(self, message):
        """📊 Статистика: .contactstats [today|yesterday|7]"""
        argument = utils.get_args_raw(message).strip().lower()
        if argument in ("", "today", "сьогодні"):
            report = self._report(self._now())
        elif argument in ("yesterday", "вчора"):
            report = self._report(self._now(), offset=1)
        elif argument.isdigit() and 1 <= int(argument) <= self.RETENTION_DAYS:
            report = self._report(self._now(), days_count=int(argument))
        else:
            report = self.strings["bad_period"]
        await utils.answer(message, report)

    @loader.command(ru_doc="Очистити історію online-активності")
    async def contactclear(self, message):
        """🗑 Очистити накопичену статистику"""
        self.set("days", {})
        # Keep currently open sessions, but restart their measurement now.
        now = self._now().timestamp()
        self.set("active", {key: now for key in self.get("active", {})})
        await utils.answer(message, self.strings["cleared"])
