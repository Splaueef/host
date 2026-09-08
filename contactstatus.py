# meta developer: @Huai_Baike
# meta version: 3.3.0
# meta description: 🟢 Керування списком спостереження та розширена статистика online-активності.
# requires: matplotlib

import asyncio
import datetime
import io
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import UpdateUserStatus, UserStatusOnline

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class ContactStatusMod(loader.Module):
    """🟢 Спостереження за online-статусом контактів і вибраних користувачів"""

    strings = {
        "name": "ContactStatus",
        "empty": (
            "📭 <b>За вибраний період online-активності не зафіксовано.</b>\n"
            "<i>Telegram показує точний online не для всіх користувачів через "
            "налаштування приватності.</i>"
        ),
        "load_failed": "ContactStatus: не вдалося синхронізувати контакти",
        "bad_period": (
            "⚠️ <b>Невірний період.</b>\n"
            "Використання: <code>.contactstats [today|yesterday|1–31] "
            "[@username]</code>"
        ),
        "user_required": (
            "👤 <b>Вкажіть користувача</b> через <code>@username</code>, "
            "Telegram ID або дайте відповідь на його повідомлення."
        ),
        "user_not_found": "❌ <b>Не вдалося знайти користувача:</b> <code>{}</code>",
        "not_watched": "⚪️ <b>Цей користувач не перебуває у списку спостереження.</b>",
        "already_watched": "ℹ️ <b>{} уже є у списку спостереження.</b>",
        "watch_added": (
            "✅ <b>{} додано до списку спостереження.</b>\n"
            "<i>Статистика почне накопичуватися з моменту додавання.</i>"
        ),
        "watch_removed": (
            "⏸ <b>{} прибрано зі списку спостереження.</b>\n"
            "<i>Зібрана раніше історія збережена.</i>"
        ),
        "sync_done": (
            "🔄 <b>Контакти синхронізовано.</b>\n"
            "👥 У Telegram: <b>{contacts}</b> · нових: <b>{added}</b> · "
            "видалених: <b>{removed}</b>\n"
            "👁 Автоматично додано до спостереження: <b>{watched}</b>"
        ),
        "sync_failed": "❌ <b>Не вдалося синхронізувати контакти.</b>",
        "clear_usage": (
            "⚠️ Для очищення всієї історії використайте "
            "<code>.contactclear all</code>.\n"
            "Історію однієї людини можна очистити через "
            "<code>.contactclear @username</code> або відповідь."
        ),
        "cleared_all": "🗑 <b>Усю історію online-активності очищено.</b>",
        "cleared_user": "🗑 <b>Історію {} очищено.</b>",
        "autowatch_state": "⚙️ Автододавання нових контактів: <b>{}</b>.",
        "bad_autowatch": (
            "⚠️ Використання: <code>.contactautowatch on</code> або "
            "<code>.contactautowatch off</code>"
        ),
        "bad_list": (
            "⚠️ Фільтри: <code>watched</code>, <code>contacts</code>, "
            "<code>manual</code>, <code>online</code>, <code>paused</code>."
        ),
        "timezone_state": "🌍 Часовий пояс статистики: <code>{}</code>.",
        "timezone_invalid": (
            "❌ <b>Невідомий часовий пояс.</b>\n"
            "Приклад: <code>.contacttimezone Europe/Berlin</code> або "
            "<code>.contacttimezone local</code>."
        ),
        "timezone_changed": (
            "✅ Часовий пояс змінено на <code>{}</code>.\n"
            "<i>Накопичену історію перебудовано без втрати сеансів.</i>"
        ),
        "compare_usage": (
            "⚠️ Використання: <code>.contactcompare @user1 @user2 "
            "[today|yesterday|1–31]</code>"
        ),
        "same_user": "⚠️ <b>Для порівняння потрібні два різні користувачі.</b>",
        "chart_building": "📊 <b>Створюю графічний звіт…</b>",
        "chart_failed": (
            "❌ <b>Не вдалося створити графік.</b>\n"
            "<i>Подробиці записано до журналу Hikka.</i>"
        ),
    }

    RETENTION_DAYS = 31
    SYNC_INTERVAL_SECONDS = 5 * 60
    MANUAL_REFRESH_SECONDS = 60 * 60
    HEARTBEAT_GRACE_SECONDS = 90
    MAX_CONTACTS = 12
    LIST_PAGE_SIZE = 15
    MESSAGE_LIMIT = 3800
    CHART_MAX_USERS = 10

    async def client_ready(self, client, db):
        self._client = client
        self._syncing = False
        self._chart_lock = asyncio.Lock()
        self._ensure_storage()

        now = self._now()
        self._recover_open_sessions(now)
        self.set("heartbeat", now.timestamp())

        try:
            await self._sync_contacts(now)
            await self._refresh_manual_profiles(now)
        except Exception:
            logger.exception(self.strings["load_failed"])

    def _ensure_storage(self):
        contacts = self.get("contacts", {})
        if not isinstance(contacts, dict):
            contacts = {}
        self.set("contacts", contacts)

        for key in ("days", "active"):
            value = self.get(key, {})
            self.set(key, value if isinstance(value, dict) else {})

        # Migration from v2: all previously known contacts remain watched.
        watchlist = self.get("watchlist", None)
        if watchlist is None:
            watchlist = list(contacts)
        self._save_ids("watchlist", watchlist)
        self._save_ids("excluded_contacts", self.get("excluded_contacts", []))

        if self.get("auto_watch_contacts", None) is None:
            self.set("auto_watch_contacts", True)
        if self.get("timezone", None) is None:
            self.set("timezone", "local")

    def _tzinfo(self):
        timezone_name = self.get("timezone", "local")
        if not timezone_name or timezone_name == "local":
            return datetime.datetime.now().astimezone().tzinfo
        try:
            return ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            return datetime.datetime.now().astimezone().tzinfo

    def _now(self):
        return datetime.datetime.now(self._tzinfo())

    @staticmethod
    def _day_start(moment):
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

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
            or str(getattr(user, "id", "Unknown"))
        )

    @staticmethod
    def _save_sort_key(value):
        value = str(value)
        return (0, int(value)) if value.lstrip("-").isdigit() else (1, value)

    def _save_ids(self, key, values):
        self.set(
            key,
            sorted({str(value) for value in values}, key=self._save_sort_key),
        )

    def _watched(self):
        return {str(value) for value in self.get("watchlist", [])}

    def _excluded(self):
        return {str(value) for value in self.get("excluded_contacts", [])}

    @staticmethod
    def _status_data(status):
        name = type(status).__name__ if status is not None else "UserStatusEmpty"
        labels = {
            "UserStatusOnline": "online",
            "UserStatusOffline": "offline",
            "UserStatusRecently": "recently",
            "UserStatusLastWeek": "last_week",
            "UserStatusLastMonth": "last_month",
            "UserStatusEmpty": "hidden",
        }
        payload = {"status": labels.get(name, "hidden")}
        was_online = getattr(status, "was_online", None)
        if was_online is not None:
            try:
                payload["last_seen"] = was_online.timestamp()
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
        return payload

    def _profile_from_entity(self, user, previous=None, **overrides):
        previous = dict(previous or {})
        previous.update(
            {
                "name": self._display_name(user),
                "username": getattr(user, "username", None),
                "updated_at": self._now().timestamp(),
                **self._status_data(getattr(user, "status", None)),
            }
        )
        previous.update(overrides)
        previous.setdefault("is_contact", bool(getattr(user, "contact", False)))
        previous.setdefault("manual", False)
        previous.setdefault("added_at", self._now().timestamp())
        return previous

    def _recover_open_sessions(self, now):
        """Close persisted sessions without counting userbot downtime as online."""
        active = self.get("active", {})
        if not active:
            return
        try:
            heartbeat = float(self.get("heartbeat", 0) or 0)
        except (TypeError, ValueError):
            heartbeat = 0

        now_stamp = now.timestamp()
        for user_id, raw_start in list(active.items()):
            try:
                start = float(raw_start)
            except (TypeError, ValueError):
                continue
            # v2 data has no heartbeat. It is safer not to invent a long session.
            end = (
                min(now_stamp, heartbeat + self.HEARTBEAT_GRACE_SECONDS)
                if heartbeat
                else start
            )
            if end > start:
                self._store_interval(
                    user_id,
                    datetime.datetime.fromtimestamp(start, now.tzinfo),
                    datetime.datetime.fromtimestamp(end, now.tzinfo),
                )
        self.set("active", {})

    async def _sync_contacts(self, moment=None):
        if self._syncing:
            return None
        self._syncing = True
        try:
            result = await self._client(GetContactsRequest(hash=0))
            now = moment or self._now()
            profiles = dict(self.get("contacts", {}))
            watched = self._watched()
            excluded = self._excluded()
            previous_ids = {
                key for key, value in profiles.items() if value.get("is_contact")
            }
            current_ids = set()
            online_ids = set()
            new_profile_count = 0
            auto_added = 0

            for user in getattr(result, "users", []):
                if getattr(user, "bot", False) or getattr(user, "self", False):
                    continue
                user_id = getattr(user, "id", None)
                if user_id is None:
                    continue
                key = str(user_id)
                current_ids.add(key)
                if key not in profiles:
                    new_profile_count += 1
                profiles[key] = self._profile_from_entity(
                    user,
                    profiles.get(key),
                    is_contact=True,
                )
                if (
                    self.get("auto_watch_contacts", True)
                    and key not in watched
                    and key not in excluded
                ):
                    watched.add(key)
                    auto_added += 1
                if isinstance(getattr(user, "status", None), UserStatusOnline):
                    online_ids.add(key)

            removed_ids = previous_ids - current_ids
            for key in removed_ids:
                profile = dict(profiles[key])
                profile["is_contact"] = False
                profile["updated_at"] = now.timestamp()
                profiles[key] = profile
                if key in watched and not profile.get("manual"):
                    self._close_user_session(key, now)
                    watched.discard(key)

            self.set("contacts", profiles)
            self._save_ids("watchlist", watched)

            # GetContacts gives a current exact state for visible contact statuses.
            for key in current_ids & watched:
                self._set_online(key, key in online_ids, now)

            self.set("last_sync", now.timestamp())
            self.set("last_sync_error", None)
            self.set("heartbeat", now.timestamp())
            self._prune(now.date())
            return {
                "contacts": len(current_ids),
                "added": len(current_ids - previous_ids),
                "removed": len(removed_ids),
                "watched": auto_added,
                "profiles": new_profile_count,
            }
        except Exception as error:
            self.set("last_sync_error", type(error).__name__)
            raise
        finally:
            self._syncing = False

    async def _refresh_manual_profiles(self, moment=None):
        """Refresh names and usernames of non-contact watch-list entries."""
        now = moment or self._now()
        profiles = dict(self.get("contacts", {}))
        watched = self._watched()
        changed = 0
        for key, stored in list(profiles.items()):
            if key not in watched or not stored.get("manual"):
                continue
            try:
                entity = await self._client.get_entity(int(key))
            except Exception:
                continue
            name = self._display_name(entity)
            username = getattr(entity, "username", None)
            if name == stored.get("name") and username == stored.get("username"):
                continue
            profile = dict(stored)
            profile["name"] = name
            profile["username"] = username
            profile["updated_at"] = now.timestamp()
            profiles[key] = profile
            changed += 1
        if changed:
            self.set("contacts", profiles)
        self.set("last_manual_refresh", now.timestamp())
        return changed

    @loader.loop(interval=60, autostart=True)
    async def contact_maintenance_loop(self):
        if not getattr(self, "_client", None):
            return
        now = self._now()
        self.set("heartbeat", now.timestamp())
        try:
            last_sync = float(self.get("last_sync", 0) or 0)
        except (TypeError, ValueError):
            last_sync = 0
        if now.timestamp() - last_sync >= self.SYNC_INTERVAL_SECONDS:
            try:
                await self._sync_contacts(now)
            except Exception:
                logger.exception(self.strings["load_failed"])
        try:
            last_manual_refresh = float(
                self.get("last_manual_refresh", 0) or 0
            )
        except (TypeError, ValueError):
            last_manual_refresh = 0
        if (
            now.timestamp() - last_manual_refresh
            >= self.MANUAL_REFRESH_SECONDS
        ):
            await self._refresh_manual_profiles(now)
        self._prune(now.date())

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
        """Append a span, coalescing duplicate and adjacent status updates."""
        spans = days.setdefault(day, {}).setdefault(str(user_id), [])
        current = [start.timestamp(), end.timestamp()]
        if not spans:
            spans.append(current)
        elif current[0] >= spans[-1][0] and current[0] <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], current[1])
        elif current[0] > spans[-1][1] + 1:
            spans.append(current)
        else:
            # Telegram normally sends ordered updates, but reconnects can
            # occasionally deliver an older status after a newer one.
            merged = []
            for begin, finish in sorted([*spans, current]):
                if merged and begin <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], finish)
                else:
                    merged.append([begin, finish])
            spans[:] = merged

    def _prune(self, today):
        cutoff = today - datetime.timedelta(days=self.RETENTION_DAYS - 1)
        days = self.get("days", {})
        fresh = {
            key: value
            for key, value in days.items()
            if isinstance(key, str) and key >= cutoff.isoformat()
        }
        if fresh != days:
            self.set("days", fresh)

    def _rebucket_history(self, tzinfo):
        """Rebuild local-day buckets after a timezone change."""
        collected = {}
        for users in self.get("days", {}).values():
            for user_id, spans in users.items():
                collected.setdefault(str(user_id), []).extend(spans)

        rebuilt = {}
        for user_id, spans in collected.items():
            for raw_start, raw_end in self._merge_spans(spans):
                start = datetime.datetime.fromtimestamp(raw_start, tzinfo)
                end = datetime.datetime.fromtimestamp(raw_end, tzinfo)
                cursor = start
                while cursor.date() < end.date():
                    boundary = self._day_start(cursor) + datetime.timedelta(
                        days=1
                    )
                    self._append_interval(
                        rebuilt,
                        cursor.date().isoformat(),
                        user_id,
                        cursor,
                        boundary,
                    )
                    cursor = boundary
                self._append_interval(
                    rebuilt,
                    cursor.date().isoformat(),
                    user_id,
                    cursor,
                    end,
                )
        self.set("days", rebuilt)

    def _set_online(self, user_id, online, moment=None):
        moment = moment or self._now()
        key = str(user_id)
        if key not in self._watched():
            return
        active = dict(self.get("active", {}))
        if online:
            active.setdefault(key, moment.timestamp())
        elif key in active:
            try:
                start = datetime.datetime.fromtimestamp(
                    float(active.pop(key)), moment.tzinfo
                )
            except (TypeError, ValueError, OverflowError):
                active.pop(key, None)
            else:
                self._store_interval(key, start, moment)
        self.set("active", active)

    def _close_user_session(self, user_id, moment=None):
        key = str(user_id)
        active = dict(self.get("active", {}))
        if key not in active:
            return
        moment = moment or self._now()
        try:
            start = datetime.datetime.fromtimestamp(
                float(active.pop(key)), moment.tzinfo
            )
        except (TypeError, ValueError, OverflowError):
            active.pop(key, None)
        else:
            self._store_interval(key, start, moment)
        self.set("active", active)

    @loader.raw_handler(UpdateUserStatus)
    async def status_watcher(self, update):
        key = str(update.user_id)
        if key not in self._watched():
            return
        now = self._now()
        profiles = dict(self.get("contacts", {}))
        profile = dict(profiles.get(key, {"name": key, "username": None}))
        profile.update(self._status_data(update.status))
        profile["updated_at"] = now.timestamp()
        profiles[key] = profile
        self.set("contacts", profiles)
        self._set_online(key, isinstance(update.status, UserStatusOnline), now)

    @staticmethod
    def _merge_spans(spans):
        merged = []
        for start, end in sorted(spans):
            start, end = float(start), float(end)
            if end <= start:
                continue
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged

    def _today_intervals(self, now):
        intervals, _, _ = self._period_intervals(now)
        return intervals

    def _period_intervals(self, now, days_count=1, offset=0):
        """Return stored and live spans clipped to the selected local-day range."""
        end_day = now.date() - datetime.timedelta(days=offset)
        start_day = end_day - datetime.timedelta(days=days_count - 1)
        range_start = self._day_start(now).replace(
            year=start_day.year,
            month=start_day.month,
            day=start_day.day,
        )
        end_midnight = (
            self._day_start(now).replace(
                year=end_day.year,
                month=end_day.month,
                day=end_day.day,
            )
            + datetime.timedelta(days=1)
        )
        range_end = min(now, end_midnight) if not offset else end_midnight
        start_stamp, end_stamp = range_start.timestamp(), range_end.timestamp()

        result = {}
        cursor = start_day
        stored = self.get("days", {})
        while cursor <= end_day:
            for user_id, spans in stored.get(cursor.isoformat(), {}).items():
                for start, end in spans:
                    clipped = [
                        max(float(start), start_stamp),
                        min(float(end), end_stamp),
                    ]
                    if clipped[1] > clipped[0]:
                        result.setdefault(str(user_id), []).append(clipped)
            cursor += datetime.timedelta(days=1)

        for user_id, raw_stamp in self.get("active", {}).items():
            if str(user_id) not in self._watched():
                continue
            try:
                begin = max(float(raw_stamp), start_stamp)
            except (TypeError, ValueError):
                continue
            if begin < end_stamp:
                result.setdefault(str(user_id), []).append([begin, end_stamp])

        return (
            {
                key: self._merge_spans(spans)
                for key, spans in result.items()
                if spans
            },
            range_start,
            range_end,
        )

    @staticmethod
    def _precise_duration(seconds):
        seconds = max(0, int(seconds))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days} дн")
        if hours:
            parts.append(f"{hours} год")
        if minutes:
            parts.append(f"{minutes} хв")
        if seconds or not parts:
            parts.append(f"{seconds} с")
        return " ".join(parts)

    @staticmethod
    def _session_word(count):
        if count % 10 == 1 and count % 100 != 11:
            return "сеанс"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            return "сеанси"
        return "сеансів"

    @staticmethod
    def _clock(stamp, tzinfo, include_date=False, seconds=False):
        if include_date:
            pattern = "%d.%m %H:%M:%S" if seconds else "%d.%m %H:%M"
        else:
            pattern = "%H:%M:%S" if seconds else "%H:%M"
        return datetime.datetime.fromtimestamp(stamp, tzinfo).strftime(pattern)

    @staticmethod
    def _overlaps(intervals):
        events = []
        for user_id, spans in intervals.items():
            for start, end in spans:
                events.extend(((start, 1, user_id), (end, -1, user_id)))
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
    def _intersect_spans(first, second):
        """Return exact intersections of two ordered interval collections."""
        first = ContactStatusMod._merge_spans(first)
        second = ContactStatusMod._merge_spans(second)
        result = []
        left = right = 0
        while left < len(first) and right < len(second):
            start = max(first[left][0], second[right][0])
            end = min(first[left][1], second[right][1])
            if end > start:
                result.append([start, end])
            if first[left][1] <= second[right][1]:
                left += 1
            else:
                right += 1
        return result

    @classmethod
    def _top_pair(cls, intervals, limit=50):
        """Find the pair with the longest shared online time."""
        candidates = sorted(
            intervals,
            key=lambda key: sum(
                end - start for start, end in intervals[key]
            ),
            reverse=True,
        )[:limit]
        best_pair = None
        best_spans = []
        best_total = 0
        for left, first_id in enumerate(candidates):
            for second_id in candidates[left + 1 :]:
                spans = cls._intersect_spans(
                    intervals[first_id],
                    intervals[second_id],
                )
                total = sum(end - start for start, end in spans)
                if total > best_total:
                    best_pair = (first_id, second_id)
                    best_spans = spans
                    best_total = total
        return best_pair, best_spans, best_total

    @staticmethod
    def _peak_online(intervals):
        return ContactStatusMod._peak_details(intervals)[0]

    @staticmethod
    def _peak_details(intervals):
        events = []
        for spans in intervals.values():
            for start, end in spans:
                events.extend(((start, 1), (end, -1)))
        current = peak = 0
        peak_at = None
        for stamp, delta in sorted(events, key=lambda item: (item[0], item[1])):
            current += delta
            if current > peak:
                peak = current
                peak_at = stamp
        return peak, peak_at

    @classmethod
    def _union_duration(cls, intervals):
        spans = [
            span
            for user_spans in intervals.values()
            for span in user_spans
        ]
        return sum(end - start for start, end in cls._merge_spans(spans))

    @staticmethod
    def _hourly_totals(intervals, tzinfo):
        buckets = [0.0] * 24
        for spans in intervals.values():
            for start, end in spans:
                cursor = float(start)
                while cursor < end:
                    local = datetime.datetime.fromtimestamp(cursor, tzinfo)
                    boundary = (
                        local.replace(minute=0, second=0, microsecond=0)
                        + datetime.timedelta(hours=1)
                    ).timestamp()
                    if boundary <= cursor:
                        boundary = cursor + 3600
                    portion_end = min(float(end), boundary)
                    buckets[local.hour] += max(0, portion_end - cursor)
                    cursor = portion_end
        return buckets

    @classmethod
    def _busiest_hour(cls, intervals, tzinfo):
        buckets = cls._hourly_totals(intervals, tzinfo)
        maximum = max(buckets, default=0)
        return (buckets.index(maximum), maximum) if maximum else (None, 0)

    @classmethod
    def _daily_totals(cls, intervals, range_start, range_end, tzinfo):
        totals = {}
        cursor = range_start.date()
        while cursor <= (range_end - datetime.timedelta(microseconds=1)).date():
            totals[cursor] = 0.0
            cursor += datetime.timedelta(days=1)

        for spans in intervals.values():
            for raw_start, raw_end in spans:
                cursor_dt = datetime.datetime.fromtimestamp(
                    raw_start,
                    tzinfo,
                )
                end_dt = datetime.datetime.fromtimestamp(raw_end, tzinfo)
                while cursor_dt.date() < end_dt.date():
                    boundary = cls._day_start(cursor_dt) + datetime.timedelta(
                        days=1
                    )
                    totals[cursor_dt.date()] = (
                        totals.get(cursor_dt.date(), 0)
                        + boundary.timestamp()
                        - cursor_dt.timestamp()
                    )
                    cursor_dt = boundary
                if end_dt > cursor_dt:
                    totals[cursor_dt.date()] = (
                        totals.get(cursor_dt.date(), 0)
                        + end_dt.timestamp()
                        - cursor_dt.timestamp()
                    )
        return totals

    @staticmethod
    def _bar(seconds, maximum, width=10):
        filled = round(width * seconds / maximum) if maximum else 0
        if seconds > 0 and maximum > 0:
            filled = max(1, filled)
        filled = max(0, min(width, filled))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _profile_link(user_id, profile):
        name = utils.escape_html(str(profile.get("name") or user_id))
        return f'<a href="tg://user?id={user_id}">{name}</a>'

    @staticmethod
    def _chart_name(user_id, profile, limit=22):
        name = str(profile.get("name") or user_id).replace("\n", " ").strip()
        return name if len(name) <= limit else name[: limit - 1] + "…"

    @staticmethod
    def _chart_scale(values):
        maximum = max(values, default=0)
        if maximum >= 3600:
            return 3600, "години"
        if maximum >= 60:
            return 60, "хвилини"
        return 1, "секунди"

    @staticmethod
    def _style_chart_axis(ax, grid_axis="y"):
        ax.set_facecolor("#111827")
        ax.tick_params(colors="#cbd5e1", labelsize=9)
        ax.title.set_color("#f8fafc")
        ax.xaxis.label.set_color("#cbd5e1")
        ax.yaxis.label.set_color("#cbd5e1")
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.grid(
            True,
            axis=grid_axis,
            color="#334155",
            alpha=0.45,
            linewidth=0.7,
        )
        ax.set_axisbelow(True)

    def _draw_timeline(
        self,
        ax,
        ordered,
        contacts,
        range_start,
        range_end,
    ):
        palette = (
            "#38bdf8",
            "#a78bfa",
            "#34d399",
            "#fbbf24",
            "#fb7185",
            "#22d3ee",
            "#c084fc",
            "#4ade80",
            "#f97316",
            "#60a5fa",
        )
        shown = ordered[: self.CHART_MAX_USERS]
        shown = list(reversed(shown))
        for row, (user_id, spans) in enumerate(shown):
            color = palette[(len(shown) - row - 1) % len(palette)]
            bars = [
                (
                    mdates.date2num(
                        datetime.datetime.fromtimestamp(
                            start,
                            range_start.tzinfo,
                        )
                    ),
                    (end - start) / 86400,
                )
                for start, end in spans
            ]
            ax.broken_barh(
                bars,
                (row - 0.32, 0.64),
                facecolors=color,
                edgecolors=color,
                linewidth=0.5,
                alpha=0.92,
            )
        ax.set_yticks(range(len(shown)))
        ax.set_yticklabels(
            [
                self._chart_name(
                    user_id,
                    contacts.get(user_id, {}),
                )
                for user_id, _ in shown
            ]
        )
        ax.set_xlim(
            mdates.date2num(range_start),
            mdates.date2num(range_end),
        )
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
                tz=range_start.tzinfo,
            )
        )
        ax.set_title(
            "Хронологія online-сеансів",
            loc="left",
            fontsize=13,
            color="#f8fafc",
        )
        ax.set_xlabel("Час")
        subtitle = f"Показано {len(shown)}"
        if len(ordered) > len(shown):
            subtitle += f" із {len(ordered)} найактивніших"
        ax.text(
            1,
            1.02,
            subtitle,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            color="#94a3b8",
            fontsize=8,
        )
        self._style_chart_axis(ax, "x")

    def _draw_daily(
        self,
        ax,
        intervals,
        range_start,
        range_end,
    ):
        totals = self._daily_totals(
            intervals,
            range_start,
            range_end,
            range_start.tzinfo,
        )
        labels = [day.strftime("%d.%m") for day in totals]
        raw_values = list(totals.values())
        divisor, unit = self._chart_scale(raw_values)
        values = [value / divisor for value in raw_values]
        colors = [
            "#fbbf24" if value == max(values, default=0) and value else "#38bdf8"
            for value in values
        ]
        ax.bar(labels, values, color=colors, alpha=0.9, width=0.72)
        ax.plot(labels, values, color="#a78bfa", marker="o", linewidth=2)
        ax.set_title(
            "Активність за днями",
            loc="left",
            fontsize=13,
            color="#f8fafc",
        )
        ax.set_ylabel(unit.capitalize())
        if len(labels) > 10:
            ax.tick_params(axis="x", rotation=45)
        self._style_chart_axis(ax)

    def _draw_hourly(self, ax, intervals, tzinfo):
        raw_values = self._hourly_totals(intervals, tzinfo)
        divisor, unit = self._chart_scale(raw_values)
        values = [value / divisor for value in raw_values]
        maximum = max(values, default=0)
        colors = [
            "#fbbf24" if value == maximum and value else "#38bdf8"
            for value in values
        ]
        ax.bar(range(24), values, color=colors, alpha=0.9, width=0.78)
        ax.set_xticks(range(0, 24, 2))
        ax.set_xticklabels([f"{hour:02d}" for hour in range(0, 24, 2)])
        ax.set_xlim(-0.7, 23.7)
        ax.set_title(
            "Розподіл за годинами",
            loc="left",
            fontsize=12,
            color="#f8fafc",
        )
        ax.set_xlabel("Година доби")
        ax.set_ylabel(unit.capitalize())
        self._style_chart_axis(ax)

    def _draw_ranking(self, ax, ordered, contacts):
        shown = ordered[: self.CHART_MAX_USERS]
        raw_values = [
            sum(end - start for start, end in spans)
            for _, spans in shown
        ]
        divisor, unit = self._chart_scale(raw_values)
        values = [value / divisor for value in reversed(raw_values)]
        labels = [
            self._chart_name(user_id, contacts.get(user_id, {}), 18)
            for user_id, _ in reversed(shown)
        ]
        colors = ["#38bdf8"] * len(values)
        if colors:
            colors[-1] = "#fbbf24"
        ax.barh(labels, values, color=colors, alpha=0.9, height=0.68)
        for row, (value, raw_value) in enumerate(
            zip(values, reversed(raw_values))
        ):
            ax.text(
                value,
                row,
                f"  {self._precise_duration(raw_value)}",
                va="center",
                color="#e2e8f0",
                fontsize=8,
            )
        ax.set_title(
            "Рейтинг активності",
            loc="left",
            fontsize=12,
            color="#f8fafc",
        )
        ax.set_xlabel(unit.capitalize())
        self._style_chart_axis(ax, "x")

    def _draw_session_distribution(self, ax, spans):
        raw_values = [end - start for start, end in spans]
        divisor, unit = self._chart_scale(raw_values)
        values = [value / divisor for value in raw_values]
        bins = max(1, min(10, len(values)))
        ax.hist(
            values,
            bins=bins,
            color="#a78bfa",
            edgecolor="#c4b5fd",
            alpha=0.9,
        )
        ax.set_title(
            "Тривалість сеансів",
            loc="left",
            fontsize=12,
            color="#f8fafc",
        )
        ax.set_xlabel(unit.capitalize())
        ax.set_ylabel("Кількість")
        self._style_chart_axis(ax)

    def _render_chart(
        self,
        intervals,
        contacts,
        range_start,
        range_end,
        days_count,
        period,
        user_id=None,
    ):
        ordered = sorted(
            intervals.items(),
            key=lambda item: sum(
                end - start for start, end in item[1]
            ),
            reverse=True,
        )
        figure = plt.figure(figsize=(14, 10), dpi=140)
        figure.patch.set_facecolor("#0b1120")
        grid = figure.add_gridspec(
            2,
            2,
            height_ratios=(1.35, 1),
            hspace=0.38,
            wspace=0.28,
        )
        main_ax = figure.add_subplot(grid[0, :])
        hourly_ax = figure.add_subplot(grid[1, 0])
        detail_ax = figure.add_subplot(grid[1, 1])

        if days_count == 1:
            self._draw_timeline(
                main_ax,
                ordered,
                contacts,
                range_start,
                range_end,
            )
        else:
            self._draw_daily(
                main_ax,
                intervals,
                range_start,
                range_end,
            )
        self._draw_hourly(hourly_ax, intervals, range_start.tzinfo)
        if user_id is None:
            self._draw_ranking(detail_ax, ordered, contacts)
            subject = "усі користувачі"
        else:
            self._draw_session_distribution(
                detail_ax,
                intervals[user_id],
            )
            subject = self._chart_name(
                user_id,
                contacts.get(user_id, {}),
                40,
            )

        session_count = sum(len(spans) for spans in intervals.values())
        total = sum(
            end - start
            for spans in intervals.values()
            for start, end in spans
        )
        figure.suptitle(
            "ContactStatus — графічний звіт",
            x=0.06,
            y=0.975,
            ha="left",
            color="#f8fafc",
            fontsize=19,
            fontweight="bold",
        )
        figure.text(
            0.06,
            0.94,
            f"{period}  •  {subject}",
            color="#94a3b8",
            fontsize=10,
        )
        figure.text(
            0.5,
            0.018,
            (
                f"{session_count} {self._session_word(session_count)}  •  "
                f"{self._precise_duration(total)} сумарної активності"
            ),
            ha="center",
            color="#94a3b8",
            fontsize=9,
        )
        figure.subplots_adjust(
            left=0.09,
            right=0.96,
            top=0.89,
            bottom=0.08,
        )
        buffer = io.BytesIO()
        try:
            figure.savefig(
                buffer,
                format="png",
                facecolor=figure.get_facecolor(),
                bbox_inches="tight",
            )
        finally:
            plt.close(figure)
        buffer.seek(0)
        buffer.name = "contactstatus-chart.png"
        return buffer

    def _presence(self, user_id, profile, now=None):
        if str(user_id) in self.get("active", {}):
            return "🟢 зараз online"
        state = profile.get("status")
        if state == "recently":
            return "🕓 був(ла) нещодавно"
        if state == "last_week":
            return "🗓 був(ла) цього тижня"
        if state == "last_month":
            return "🗓 був(ла) цього місяця"
        last_seen = profile.get("last_seen")
        if last_seen:
            try:
                moment = datetime.datetime.fromtimestamp(
                    float(last_seen),
                    (now or self._now()).tzinfo,
                )
                return f"⚪️ останній раз {moment:%d.%m о %H:%M}"
            except (TypeError, ValueError, OverflowError):
                pass
        return "🔒 точний статус приховано"

    @staticmethod
    def _period_title(now, range_start, range_end, days_count, offset):
        if days_count == 1 and not offset:
            return f"Сьогодні · 00:00–{range_end:%H:%M}"
        if days_count == 1 and offset == 1:
            return f"Вчора · {range_start:%d.%m.%Y}"
        return (
            f"Останні {days_count} дн. · "
            f"{range_start:%d.%m}–{range_end:%d.%m.%Y}"
        )

    def _timeline_blocks(
        self,
        ordered,
        now,
        range_end,
        include_date,
        offset,
    ):
        active = set(self.get("active", {})) if not offset else set()
        blocks = []
        medals = ("🥇", "🥈", "🥉")
        for rank, (user_id, spans) in enumerate(ordered, 1):
            profile = self.get("contacts", {}).get(user_id, {})
            icon = medals[rank - 1] if rank <= len(medals) else f"{rank}."
            title = (
                f"{icon} <b>{self._profile_link(user_id, profile)}</b> · "
                f"{len(spans)} {self._session_word(len(spans))}\n"
            )
            continuation = (
                f"↳ <b>{self._profile_link(user_id, profile)} · "
                "продовження</b>\n"
            )
            block = title
            for number, (begin, end) in enumerate(spans, 1):
                is_live = (
                    user_id in active
                    and abs(end - range_end.timestamp()) < 1
                    and number == len(spans)
                )
                begin_text = self._clock(
                    begin,
                    now.tzinfo,
                    include_date,
                    seconds=True,
                )
                end_text = (
                    "зараз"
                    if is_live
                    else self._clock(
                        end,
                        now.tzinfo,
                        include_date,
                        seconds=True,
                    )
                )
                line = (
                    f"  {number:02d}. <code>{begin_text}–{end_text}</code> "
                    f"· {self._precise_duration(end - begin)}\n"
                )
                if len(block) + len(line) > 2600 and block != title:
                    blocks.append(block.rstrip())
                    block = continuation
                block += line
            blocks.append(block.rstrip())
        return blocks

    def _paginate_report(self, summary, blocks):
        pages = []
        current = summary.rstrip()
        continuation = "🕓 <b>Повна хронологія · продовження</b>"
        for block in blocks:
            addition = f"\n\n{block}"
            if len(current) + len(addition) > self.MESSAGE_LIMIT:
                pages.append(current)
                current = f"{continuation}\n\n{block}"
            else:
                current += addition
        if current:
            pages.append(current)
        if len(pages) > 1:
            total = len(pages)
            pages = [
                f"{page}\n\n<i>Сторінка {index}/{total}</i>"
                for index, page in enumerate(pages, 1)
            ]
        return pages

    def _report_pages(self, now, days_count=1, offset=0, user_id=None):
        intervals, range_start, range_end = self._period_intervals(
            now,
            days_count,
            offset,
        )
        if user_id is not None:
            user_id = str(user_id)
            intervals = (
                {user_id: intervals[user_id]}
                if user_id in intervals
                else {}
            )
        if not intervals:
            if user_id is None:
                return [self.strings["empty"]]
            profile = self.get("contacts", {}).get(user_id, {})
            return [(
                f"📭 <b>Для {self._profile_link(user_id, profile)} немає даних "
                "за вибраний період.</b>\n"
                f"{self._presence(user_id, profile, now)}"
            )]

        contacts = self.get("contacts", {})
        ordered = sorted(
            intervals.items(),
            key=lambda item: sum(end - begin for begin, end in item[1]),
            reverse=True,
        )
        period = self._period_title(
            now,
            range_start,
            range_end,
            days_count,
            offset,
        )
        range_seconds = max(
            1,
            range_end.timestamp() - range_start.timestamp(),
        )
        include_date = days_count > 1

        if user_id is not None:
            spans = ordered[0][1]
            profile = contacts.get(user_id, {})
            total = sum(end - begin for begin, end in spans)
            longest = max(end - begin for begin, end in spans)
            average = total / len(spans)
            text = (
                "👤 <b>ContactStatus</b>\n"
                f"╰ {self._profile_link(user_id, profile)}\n"
                f"📅 <b>{period}</b>\n"
                f"{self._presence(user_id, profile, now)}\n"
            )
            username = profile.get("username")
            if username:
                text += f"🔗 <code>@{utils.escape_html(username)}</code>\n"
            text += (
                "\n📊 <b>Підсумок</b>\n"
                f"⏱ У мережі: <b>{self._precise_duration(total)}</b>\n"
                f"📐 Частка періоду: <b>{total / range_seconds * 100:.1f}%</b>\n"
                f"🔁 Входів: <b>{len(spans)}</b> · у середньому "
                f"<b>{self._precise_duration(average)}</b>\n"
                f"🏅 Найдовший сеанс: <b>{self._precise_duration(longest)}</b>\n"
                f"⏮ Перший вхід: <b>"
                f"{self._clock(spans[0][0], now.tzinfo, include_date, True)}"
                "</b>\n"
                f"⏭ Останній вихід: <b>"
                f"{self._clock(spans[-1][1], now.tzinfo, include_date, True)}"
                "</b>\n"
                "\n🕓 <b>Повна хронологія</b>"
            )
            blocks = self._timeline_blocks(
                ordered,
                now,
                range_end,
                include_date,
                offset,
            )
            return self._paginate_report(text, blocks)

        all_time = sum(
            sum(end - begin for begin, end in spans)
            for _, spans in ordered
        )
        sessions = sum(len(spans) for _, spans in ordered)
        longest = max(
            (
                end - begin
                for _, spans in ordered
                for begin, end in spans
            ),
            default=0,
        )
        active_now = (
            len(set(intervals) & set(self.get("active", {})))
            if not offset
            else 0
        )
        coverage = self._union_duration(intervals)
        peak, peak_at = self._peak_details(intervals)
        busiest_hour, _ = self._busiest_hour(intervals, now.tzinfo)
        overlaps = self._overlaps(intervals)
        shared = sum(end - begin for begin, end in overlaps)
        top_pair, _, top_pair_total = self._top_pair(intervals)
        current_names = (
            [
                self._profile_link(key, contacts.get(key, {}))
                for key in sorted(
                    set(intervals) & set(self.get("active", {})),
                    key=lambda key: str(
                        contacts.get(key, {}).get("name") or key
                    ).casefold(),
                )
            ]
            if not offset
            else []
        )

        text = (
            "🟢 <b>ContactStatus</b>\n"
            f"📅 <b>{period}</b>\n"
            f"🟢 Зараз online: <b>{active_now}</b>"
        )
        if current_names:
            text += " · " + ", ".join(current_names[:5])
            if len(current_names) > 5:
                text += f" <i>(+{len(current_names) - 5})</i>"
        text += (
            "\n\n📊 <b>Підсумок</b>\n"
            f"👥 З активністю: <b>{len(intervals)}</b> · входів: "
            f"<b>{sessions}</b>\n"
            f"⏱ Сумарна активність: <b>{self._precise_duration(all_time)}</b>\n"
            f"🌐 Хоча б хтось online: <b>{self._precise_duration(coverage)}</b>\n"
            f"🤝 Щонайменше двоє online: "
            f"<b>{self._precise_duration(shared) if shared else '—'}</b>\n"
        )
        if top_pair:
            first_id, second_id = top_pair
            text += (
                "💞 Найчастіше разом: "
                f"{self._profile_link(first_id, contacts.get(first_id, {}))} + "
                f"{self._profile_link(second_id, contacts.get(second_id, {}))}"
                f" · <b>{self._precise_duration(top_pair_total)}</b>\n"
            )
        text += (
            f"🏅 Найдовший сеанс: <b>{self._precise_duration(longest)}</b>\n"
            f"📈 Одночасний пік: <b>{peak}</b>"
        )
        if peak_at is not None:
            text += (
                f" · о <b>{self._clock(peak_at, now.tzinfo, include_date, True)}</b>"
            )
        if busiest_hour is not None:
            text += (
                f"\n🕘 Найактивніша година: <b>{busiest_hour:02d}:00–"
                f"{(busiest_hour + 1) % 24:02d}:00</b>"
            )
        text += "\n\n🏆 <b>Рейтинг активності</b>\n"

        maximum = sum(end - begin for begin, end in ordered[0][1])
        for index, (current_id, spans) in enumerate(
            ordered[: self.MAX_CONTACTS],
            1,
        ):
            profile = contacts.get(current_id, {})
            total = sum(end - begin for begin, end in spans)
            status = (
                " 🟢"
                if current_id in self.get("active", {}) and not offset
                else ""
            )
            medal = (
                ("🥇", "🥈", "🥉")[index - 1]
                if index <= 3
                else f"{index}."
            )
            share = total / all_time * 100 if all_time else 0
            text += (
                f"\n{medal} <b>{self._profile_link(current_id, profile)}</b>{status}\n"
                f"   <b>{self._precise_duration(total)}</b> · "
                f"{share:.1f}% активності · {len(spans)} "
                f"{self._session_word(len(spans))}\n"
                f"   <code>{self._bar(total, maximum)}</code>\n"
            )

        if len(ordered) > self.MAX_CONTACTS:
            text += (
                f"\n<i>…і ще {len(ordered) - self.MAX_CONTACTS} "
                "користувачів</i>\n"
            )
        text += "\n🕓 <b>Повна хронологія всіх входів</b>"
        blocks = self._timeline_blocks(
            ordered,
            now,
            range_end,
            include_date,
            offset,
        )
        return self._paginate_report(text, blocks)

    def _report(self, now, days_count=1, offset=0, user_id=None):
        """Compatibility helper used by tests and integrations."""
        return "\n\n".join(
            self._report_pages(now, days_count, offset, user_id)
        )

    def _compare_report_pages(
        self,
        now,
        first_id,
        second_id,
        days_count=1,
        offset=0,
    ):
        intervals, range_start, range_end = self._period_intervals(
            now,
            days_count,
            offset,
        )
        first_id, second_id = str(first_id), str(second_id)
        first = intervals.get(first_id, [])
        second = intervals.get(second_id, [])
        first_total = sum(end - start for start, end in first)
        second_total = sum(end - start for start, end in second)
        shared = self._intersect_spans(first, second)
        shared_total = sum(end - start for start, end in shared)
        contacts = self.get("contacts", {})
        first_name = self._profile_link(
            first_id,
            contacts.get(first_id, {}),
        )
        second_name = self._profile_link(
            second_id,
            contacts.get(second_id, {}),
        )
        period = self._period_title(
            now,
            range_start,
            range_end,
            days_count,
            offset,
        )
        smaller_total = min(first_total, second_total)
        overlap_share = (
            shared_total / smaller_total * 100
            if smaller_total
            else 0
        )
        difference = abs(first_total - second_total)
        if first_total > second_total:
            leader = first_name
        elif second_total > first_total:
            leader = second_name
        else:
            leader = "порівну"

        text = (
            "⚖️ <b>ContactStatus · порівняння</b>\n"
            f"📅 <b>{period}</b>\n\n"
            f"👤 {first_name}\n"
            f"   ⏱ <b>{self._precise_duration(first_total)}</b> · "
            f"{len(first)} {self._session_word(len(first))}\n"
            f"👤 {second_name}\n"
            f"   ⏱ <b>{self._precise_duration(second_total)}</b> · "
            f"{len(second)} {self._session_word(len(second))}\n\n"
            "📊 <b>Результат</b>\n"
            f"🏆 Більше часу online: <b>{leader}</b>"
        )
        if difference:
            text += f" · різниця <b>{self._precise_duration(difference)}</b>"
        text += (
            f"\n🤝 Разом online: "
            f"<b>{self._precise_duration(shared_total) if shared else '—'}</b>\n"
            f"🎯 Збіг меншої активності: <b>{overlap_share:.1f}%</b>"
        )
        if not shared:
            return [text + "\n\n<i>Спільних online-моментів не зафіксовано.</i>"]

        text += "\n\n🕓 <b>Усі спільні моменти</b>"
        include_date = days_count > 1
        blocks = []
        block = ""
        for number, (begin, end) in enumerate(shared, 1):
            line = (
                f"{number:02d}. <code>"
                f"{self._clock(begin, now.tzinfo, include_date, True)}–"
                f"{self._clock(end, now.tzinfo, include_date, True)}"
                f"</code> · {self._precise_duration(end - begin)}\n"
            )
            if len(block) + len(line) > 2600 and block:
                blocks.append(block.rstrip())
                block = ""
            block += line
        if block:
            blocks.append(block.rstrip())
        return self._paginate_report(text, blocks)

    async def _send_report_pages(self, message, pages):
        await utils.answer(message, pages[0])
        for page in pages[1:]:
            if hasattr(message, "respond"):
                await message.respond(
                    page,
                    parse_mode="html",
                    link_preview=False,
                )
            else:
                await self._client.send_message(
                    message.peer_id,
                    page,
                    parse_mode="html",
                    link_preview=False,
                )

    async def _resolve_user(self, message, raw=None):
        raw = (raw or "").strip()
        try:
            if raw:
                identifier = raw.split()[0]
                identifier = (
                    identifier[1:]
                    if identifier.startswith("@")
                    else identifier
                )
                if identifier.lstrip("-").isdigit():
                    identifier = int(identifier)
                user = await self._client.get_entity(identifier)
            else:
                reply = await message.get_reply_message()
                if not reply:
                    return None
                user = await reply.get_sender()
        except Exception:
            return False
        if (
            user is None
            or getattr(user, "id", None) is None
            or getattr(user, "bot", False)
            or getattr(user, "self", False)
        ):
            return False
        return user

    def _find_profile(self, raw):
        raw = (raw or "").strip()
        raw = raw.split()[0] if raw else ""
        needle = raw.lstrip("@").lower()
        if not needle:
            return None
        profiles = self.get("contacts", {})
        if needle.lstrip("-").isdigit() and needle in profiles:
            return needle
        for user_id, profile in profiles.items():
            if str(profile.get("username") or "").lower() == needle:
                return str(user_id)
        return None

    async def _resolve_target_id(self, message, raw=None):
        found = self._find_profile(raw)
        if found:
            return found
        user = await self._resolve_user(message, raw)
        return str(user.id) if user not in (None, False) else user

    @loader.command(ru_doc="Панель стану ContactStatus")
    async def contactstatus(self, message):
        """ℹ️ Стан модуля, синхронізації та коротка довідка"""
        profiles = self.get("contacts", {})
        watched = self._watched()
        contacts = {
            key
            for key, value in profiles.items()
            if value.get("is_contact")
        }
        manual = {
            key
            for key, value in profiles.items()
            if value.get("manual") and key in watched
        }
        paused = contacts - watched
        active = watched & set(self.get("active", {}))
        last_sync = self.get("last_sync")
        if last_sync:
            try:
                synced = datetime.datetime.fromtimestamp(
                    float(last_sync),
                    self._now().tzinfo,
                ).strftime("%d.%m.%Y о %H:%M")
            except (TypeError, ValueError, OverflowError):
                synced = "невідомо"
        else:
            synced = "ще не виконувалась"
        error = self.get("last_sync_error")
        sync_state = (
            f"⚠️ {utils.escape_html(error)}"
            if error
            else "✅ без помилок"
        )
        auto = (
            "увімкнено"
            if self.get("auto_watch_contacts", True)
            else "вимкнено"
        )
        timezone_name = self.get("timezone", "local")
        timezone_label = (
            f"local ({self._now().tzname()})"
            if timezone_name == "local"
            else timezone_name
        )
        days = self.get("days", {})
        session_count = sum(
            len(spans)
            for users in days.values()
            for spans in users.values()
        )
        text = (
            "🛰 <b>ContactStatus · панель</b>\n\n"
            f"👁 Відстежується: <b>{len(watched)}</b> · online: <b>{len(active)}</b>\n"
            f"👥 Контактів Telegram: <b>{len(contacts)}</b> · на паузі: "
            f"<b>{len(paused)}</b>\n"
            f"➕ Додано вручну: <b>{len(manual)}</b>\n"
            f"💾 Збережено сеансів: <b>{session_count}</b> · термін: "
            f"<b>{self.RETENTION_DAYS} дн.</b>\n\n"
            f"🔄 Остання синхронізація: <b>{synced}</b>\n"
            f"Стан: {sync_state} · автододавання: <b>{auto}</b>\n"
            f"🌍 Часовий пояс: <code>{timezone_label}</code>\n\n"
            "<b>Команди</b>\n"
            "<code>.contactlist</code> — список спостереження\n"
            "<code>.contactadd @user</code> — додати користувача\n"
            "<code>.contactremove @user</code> — призупинити\n"
            "<code>.contactstats 7 @user</code> — статистика\n"
            "<code>.contactchart 7 @user</code> — PNG-графік\n"
            "<code>.contactcompare @a @b 7</code> — порівняти\n"
            "<code>.contactsync</code> — синхронізувати зараз"
        )
        await utils.answer(message, text)

    @loader.command(ru_doc="Показати список контактів і стан спостереження")
    async def contactlist(self, message):
        """👥 .contactlist [watched|contacts|manual|online|paused] [page]"""
        args = utils.get_args_raw(message).lower().split()
        category = "watched"
        page = 1
        allowed = {"watched", "contacts", "manual", "online", "paused"}
        for item in args:
            if item in allowed:
                category = item
            elif item.isdigit():
                page = max(1, int(item))
            else:
                await utils.answer(message, self.strings["bad_list"])
                return

        profiles = self.get("contacts", {})
        watched = self._watched()
        active = set(self.get("active", {}))
        filters = {
            "watched": lambda key, value: key in watched,
            "contacts": lambda key, value: value.get("is_contact"),
            "manual": lambda key, value: value.get("manual") and key in watched,
            "online": lambda key, value: key in watched and key in active,
            "paused": lambda key, value: value.get("is_contact") and key not in watched,
        }
        selected = [
            (key, value)
            for key, value in profiles.items()
            if filters[category](key, value)
        ]
        selected.sort(
            key=lambda item: (
                item[0] not in active,
                item[0] not in watched,
                str(item[1].get("name") or item[0]).casefold(),
            )
        )
        pages = max(
            1,
            (len(selected) + self.LIST_PAGE_SIZE - 1)
            // self.LIST_PAGE_SIZE,
        )
        page = min(page, pages)
        start = (page - 1) * self.LIST_PAGE_SIZE
        chunk = selected[start : start + self.LIST_PAGE_SIZE]
        labels = {
            "watched": "спостереження",
            "contacts": "контакти Telegram",
            "manual": "додані вручну",
            "online": "зараз online",
            "paused": "на паузі",
        }
        text = (
            f"👥 <b>ContactStatus · {labels[category]}</b>\n"
            f"Знайдено: <b>{len(selected)}</b> · сторінка <b>{page}/{pages}</b>\n\n"
        )
        if not chunk:
            text += "<i>Список порожній.</i>"
        for index, (user_id, profile) in enumerate(chunk, start + 1):
            icon = (
                "🟢"
                if user_id in active
                else ("👁" if user_id in watched else "⏸")
            )
            badges = []
            if profile.get("is_contact"):
                badges.append("контакт")
            if profile.get("manual"):
                badges.append("вручну")
            if user_id not in watched:
                badges.append("пауза")
            username = profile.get("username")
            suffix = (
                f" · <code>@{utils.escape_html(username)}</code>"
                if username
                else ""
            )
            text += (
                f"{index}. {icon} {self._profile_link(user_id, profile)}{suffix}\n"
                f"   <i>{' · '.join(badges) or 'збережена історія'}</i>\n"
            )
        if pages > 1:
            text += (
                f"\n<i>Інша сторінка: <code>.contactlist {category} "
                "N</code></i>"
            )
        await utils.answer(message, text)

    @loader.command(ru_doc="Додати користувача до списку спостереження")
    async def contactadd(self, message):
        """➕ .contactadd @username / ID / відповідь"""
        raw = utils.get_args_raw(message).strip()
        user = await self._resolve_user(message, raw)
        if user is None:
            await utils.answer(message, self.strings["user_required"])
            return
        if user is False:
            await utils.answer(
                message,
                self.strings["user_not_found"].format(
                    utils.escape_html(raw or "reply")
                ),
            )
            return

        key = str(user.id)
        profiles = dict(self.get("contacts", {}))
        previous = profiles.get(key, {})
        was_watched = key in self._watched()
        is_contact = bool(
            previous.get("is_contact")
            or getattr(user, "contact", False)
        )
        profiles[key] = self._profile_from_entity(
            user,
            previous,
            is_contact=is_contact,
            manual=bool(previous.get("manual") or not is_contact),
        )
        self.set("contacts", profiles)
        watched = self._watched()
        watched.add(key)
        self._save_ids("watchlist", watched)
        excluded = self._excluded()
        excluded.discard(key)
        self._save_ids("excluded_contacts", excluded)
        if isinstance(getattr(user, "status", None), UserStatusOnline):
            self._set_online(key, True)

        name = self._profile_link(key, profiles[key])
        template = (
            self.strings["already_watched"]
            if was_watched
            else self.strings["watch_added"]
        )
        await utils.answer(message, template.format(name))

    @loader.command(ru_doc="Прибрати користувача зі списку спостереження")
    async def contactremove(self, message):
        """➖ .contactremove @username / ID / відповідь"""
        raw = utils.get_args_raw(message).strip()
        key = await self._resolve_target_id(message, raw)
        if key is None:
            await utils.answer(message, self.strings["user_required"])
            return
        if key is False:
            await utils.answer(
                message,
                self.strings["user_not_found"].format(
                    utils.escape_html(raw or "reply")
                ),
            )
            return
        key = str(key)
        if key not in self._watched():
            await utils.answer(message, self.strings["not_watched"])
            return

        self._close_user_session(key)
        watched = self._watched()
        watched.discard(key)
        self._save_ids("watchlist", watched)
        profiles = dict(self.get("contacts", {}))
        profile = dict(
            profiles.get(key, {"name": key, "username": None})
        )
        if profile.get("is_contact"):
            excluded = self._excluded()
            excluded.add(key)
            self._save_ids("excluded_contacts", excluded)
        profile["manual"] = False
        profiles[key] = profile
        self.set("contacts", profiles)
        await utils.answer(
            message,
            self.strings["watch_removed"].format(
                self._profile_link(key, profile)
            ),
        )

    @loader.command(ru_doc="Синхронізувати контакти Telegram зараз")
    async def contactsync(self, message):
        """🔄 Запустити синхронізацію контактів вручну"""
        try:
            result = await self._sync_contacts()
        except Exception:
            logger.exception(self.strings["load_failed"])
            await utils.answer(message, self.strings["sync_failed"])
            return
        if result is None:
            await utils.answer(
                message,
                "⏳ <b>Синхронізація вже виконується.</b>",
            )
            return
        await utils.answer(
            message,
            self.strings["sync_done"].format(**result),
        )

    @loader.command(ru_doc="Автододавання нових Telegram-контактів")
    async def contactautowatch(self, message):
        """⚙️ .contactautowatch [on|off]"""
        raw = utils.get_args_raw(message).strip().lower()
        if not raw:
            state = (
                "увімкнено"
                if self.get("auto_watch_contacts", True)
                else "вимкнено"
            )
            await utils.answer(
                message,
                self.strings["autowatch_state"].format(state),
            )
            return
        if raw not in {"on", "off", "1", "0", "так", "ні"}:
            await utils.answer(message, self.strings["bad_autowatch"])
            return
        enabled = raw in {"on", "1", "так"}
        self.set("auto_watch_contacts", enabled)
        if enabled:
            try:
                await self._sync_contacts()
            except Exception:
                logger.exception(self.strings["load_failed"])
        await utils.answer(
            message,
            self.strings["autowatch_state"].format(
                "увімкнено" if enabled else "вимкнено"
            ),
        )

    @loader.command(ru_doc="Часовий пояс для online-статистики")
    async def contacttimezone(self, message):
        """🌍 .contacttimezone [Europe/Berlin|Europe/Kyiv|local]"""
        raw = utils.get_args_raw(message).strip()
        if not raw:
            timezone_name = self.get("timezone", "local")
            label = (
                f"local ({self._now().tzname()})"
                if timezone_name == "local"
                else timezone_name
            )
            await utils.answer(
                message,
                self.strings["timezone_state"].format(
                    utils.escape_html(label)
                ),
            )
            return

        normalized = "local" if raw.lower() == "local" else raw
        if normalized == "local":
            new_tz = datetime.datetime.now().astimezone().tzinfo
        else:
            try:
                new_tz = ZoneInfo(normalized)
            except (ZoneInfoNotFoundError, ValueError):
                await utils.answer(
                    message,
                    self.strings["timezone_invalid"],
                )
                return

        instant = datetime.datetime.now(datetime.timezone.utc)
        old_now = instant.astimezone(self._tzinfo())
        active_ids = set(self.get("active", {})) & self._watched()
        for user_id in active_ids:
            self._close_user_session(user_id, old_now)

        self.set("timezone", normalized)
        self._rebucket_history(new_tz)
        self.set(
            "active",
            {user_id: instant.timestamp() for user_id in active_ids},
        )
        self._prune(instant.astimezone(new_tz).date())
        await utils.answer(
            message,
            self.strings["timezone_changed"].format(
                utils.escape_html(normalized)
            ),
        )

    @loader.command(ru_doc="Online-статистика за період")
    async def contactstats(self, message):
        """📊 .contactstats [today|yesterday|1–31] [@username / reply]"""
        tokens = utils.get_args_raw(message).strip().split()
        period_token = None
        target_token = ""
        for token in tokens:
            lowered = token.lower()
            is_period = (
                lowered
                in {"today", "сьогодні", "yesterday", "вчора"}
                or (
                    lowered.isdigit()
                    and 1 <= int(lowered) <= self.RETENTION_DAYS
                )
            )
            if is_period and period_token is None:
                period_token = lowered
            elif not is_period and not target_token:
                target_token = token
            else:
                await utils.answer(message, self.strings["bad_period"])
                return

        period_token = period_token or "today"
        if period_token in {"today", "сьогодні"}:
            days_count, offset = 1, 0
        elif period_token in {"yesterday", "вчора"}:
            days_count, offset = 1, 1
        elif (
            period_token.isdigit()
            and 1 <= int(period_token) <= self.RETENTION_DAYS
        ):
            days_count, offset = int(period_token), 0
        else:
            await utils.answer(message, self.strings["bad_period"])
            return

        user_id = None
        if target_token:
            user_id = await self._resolve_target_id(
                message,
                target_token,
            )
        elif tokens:
            try:
                reply = await message.get_reply_message()
            except Exception:
                reply = None
            if reply:
                user = await reply.get_sender()
                user_id = (
                    str(user.id)
                    if user and getattr(user, "id", None)
                    else None
                )

        if user_id is False:
            await utils.answer(
                message,
                self.strings["user_not_found"].format(
                    utils.escape_html(target_token)
                ),
            )
            return
        if user_id is not None and str(user_id) not in self._watched():
            await utils.answer(message, self.strings["not_watched"])
            return
        await self._send_report_pages(
            message,
            self._report_pages(
                self._now(),
                days_count,
                offset,
                user_id,
            ),
        )

    @loader.command(ru_doc="Створити PNG-графік online-активності")
    async def contactchart(self, message):
        """📊 .contactchart [today|yesterday|1–31] [@username / reply]"""
        tokens = utils.get_args_raw(message).strip().split()
        period_token = None
        target_token = ""
        for token in tokens:
            lowered = token.lower()
            is_period = (
                lowered
                in {"today", "сьогодні", "yesterday", "вчора"}
                or (
                    lowered.isdigit()
                    and 1 <= int(lowered) <= self.RETENTION_DAYS
                )
            )
            if is_period and period_token is None:
                period_token = lowered
            elif not is_period and not target_token:
                target_token = token
            else:
                await utils.answer(message, self.strings["bad_period"])
                return

        period_token = period_token or "today"
        if period_token in {"today", "сьогодні"}:
            days_count, offset = 1, 0
        elif period_token in {"yesterday", "вчора"}:
            days_count, offset = 1, 1
        else:
            days_count, offset = int(period_token), 0

        user_id = None
        if target_token:
            user_id = await self._resolve_target_id(
                message,
                target_token,
            )
        elif tokens:
            try:
                reply = await message.get_reply_message()
            except Exception:
                reply = None
            if reply:
                user = await reply.get_sender()
                user_id = (
                    str(user.id)
                    if user and getattr(user, "id", None)
                    else None
                )

        if user_id is False:
            await utils.answer(
                message,
                self.strings["user_not_found"].format(
                    utils.escape_html(target_token)
                ),
            )
            return
        if user_id is not None:
            user_id = str(user_id)
            if user_id not in self._watched():
                await utils.answer(message, self.strings["not_watched"])
                return

        now = self._now()
        intervals, range_start, range_end = self._period_intervals(
            now,
            days_count,
            offset,
        )
        if user_id is not None:
            intervals = (
                {user_id: intervals[user_id]}
                if user_id in intervals
                else {}
            )
        if not intervals:
            await utils.answer(message, self.strings["empty"])
            return

        await utils.answer(message, self.strings["chart_building"])
        contacts = {
            key: dict(value)
            for key, value in self.get("contacts", {}).items()
        }
        period = self._period_title(
            now,
            range_start,
            range_end,
            days_count,
            offset,
        )
        if not hasattr(self, "_chart_lock"):
            self._chart_lock = asyncio.Lock()
        chart = None
        try:
            async with self._chart_lock:
                chart = await asyncio.to_thread(
                    self._render_chart,
                    intervals,
                    contacts,
                    range_start,
                    range_end,
                    days_count,
                    period,
                    user_id,
                )
            caption = (
                "📊 <b>ContactStatus · графічний звіт</b>\n"
                f"📅 <b>{period}</b>"
            )
            if user_id is not None:
                caption += (
                    "\n👤 "
                    + self._profile_link(
                        user_id,
                        contacts.get(user_id, {}),
                    )
                )
            await self._client.send_file(
                message.peer_id,
                chart,
                caption=caption,
                parse_mode="html",
                reply_to=getattr(message, "reply_to_msg_id", None),
            )
            try:
                await message.delete()
            except Exception:
                pass
        except Exception:
            logger.exception("ContactStatus: chart rendering failed")
            await utils.answer(message, self.strings["chart_failed"])
        finally:
            if chart is not None:
                chart.close()

    @loader.command(ru_doc="Порівняти online-активність двох користувачів")
    async def contactcompare(self, message):
        """⚖️ .contactcompare @user1 @user2 [today|yesterday|1–31]"""
        tokens = utils.get_args_raw(message).strip().split()
        period_token = None
        targets = []
        for token in tokens:
            lowered = token.lower()
            is_period = (
                lowered
                in {"today", "сьогодні", "yesterday", "вчора"}
                or (
                    lowered.isdigit()
                    and 1 <= int(lowered) <= self.RETENTION_DAYS
                )
            )
            if is_period and period_token is None:
                period_token = lowered
            elif not is_period:
                targets.append(token)
            else:
                await utils.answer(message, self.strings["compare_usage"])
                return

        period_token = period_token or "today"
        if period_token in {"today", "сьогодні"}:
            days_count, offset = 1, 0
        elif period_token in {"yesterday", "вчора"}:
            days_count, offset = 1, 1
        elif (
            period_token.isdigit()
            and 1 <= int(period_token) <= self.RETENTION_DAYS
        ):
            days_count, offset = int(period_token), 0
        else:
            await utils.answer(message, self.strings["compare_usage"])
            return

        if len(targets) == 2:
            first_id = await self._resolve_target_id(
                message,
                targets[0],
            )
            second_id = await self._resolve_target_id(
                message,
                targets[1],
            )
        elif len(targets) == 1:
            first_id = await self._resolve_target_id(message, "")
            second_id = await self._resolve_target_id(
                message,
                targets[0],
            )
        else:
            await utils.answer(message, self.strings["compare_usage"])
            return

        if first_id in (None, False) or second_id in (None, False):
            await utils.answer(message, self.strings["compare_usage"])
            return
        first_id, second_id = str(first_id), str(second_id)
        if first_id == second_id:
            await utils.answer(message, self.strings["same_user"])
            return
        watched = self._watched()
        if first_id not in watched or second_id not in watched:
            await utils.answer(message, self.strings["not_watched"])
            return
        await self._send_report_pages(
            message,
            self._compare_report_pages(
                self._now(),
                first_id,
                second_id,
                days_count,
                offset,
            ),
        )

    @loader.command(ru_doc="Очистити всю або вибрану online-історію")
    async def contactclear(self, message):
        """🗑 .contactclear all / @username / відповідь"""
        raw = utils.get_args_raw(message).strip()
        if raw.lower() == "all":
            self.set("days", {})
            now = self._now().timestamp()
            self.set(
                "active",
                {
                    key: now
                    for key in self.get("active", {})
                    if key in self._watched()
                },
            )
            await utils.answer(message, self.strings["cleared_all"])
            return

        if not raw:
            try:
                reply = await message.get_reply_message()
            except Exception:
                reply = None
            if not reply:
                await utils.answer(message, self.strings["clear_usage"])
                return
        key = await self._resolve_target_id(message, raw)
        if key in (None, False):
            await utils.answer(
                message,
                self.strings["user_not_found"].format(
                    utils.escape_html(raw or "reply")
                ),
            )
            return
        key = str(key)
        days = self.get("days", {})
        cleaned = {}
        for day, users in days.items():
            remaining = {
                user_id: spans
                for user_id, spans in users.items()
                if user_id != key
            }
            if remaining:
                cleaned[day] = remaining
        self.set("days", cleaned)
        if key in self.get("active", {}):
            active = dict(self.get("active", {}))
            active[key] = self._now().timestamp()
            self.set("active", active)
        profile = self.get("contacts", {}).get(key, {"name": key})
        await utils.answer(
            message,
            self.strings["cleared_user"].format(
                self._profile_link(key, profile)
            ),
        )
