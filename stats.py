# meta developer: @Huai_Baike
# meta version: 1.5.0
# meta description: 📊 Статистика вашої активності в Telegram — повідомлення, чати, піки по годинах.

import datetime
import logging
import re

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class DailyStatMod(loader.Module):
    """📊 Відстежує вашу щоденну активність у Telegram"""

    strings = {
        "name": "DailyStat",
        "no_data": (
            "📭 <b>За цей період ще немає даних.</b>\n"
            "<i>Нові повідомлення з’являться тут автоматично.</i>"
        ),
        "reset_done": "🗑 <b>Статистику скинуто.</b>",
        "reset_warning": (
            "⚠️ <b>Це назавжди видалить усю статистику.</b>\n"
            "Для підтвердження введіть: "
            "<code>{prefix}ds reset confirm</code>"
        ),
        "stat_header": "📊 <b>DailyStat</b>\n🗓 <i>{period}</i>\n\n",
        "stat_body": (
            "<b>💬 Повідомлення</b>\n"
            "├ 📤 Надіслано: <b>{sent}</b>\n"
            "├ 📥 Отримано в особистих: <b>{received}</b>\n"
            "└ 📊 Разом: <b>{total}</b>\n\n"
            "<b>⚡ Активність</b>\n"
            "├ 🗂 Активних чатів: <b>{chats}</b>\n"
            "├ 📎 Медіа: <b>{media}</b> <i>({media_percent}%)</i>\n"
            "{average_line}"
            "└ ⏰ Пік надсилання: <b>{peak}</b>\n"
        ),
        "top_header": "\n🏆 <b>Топ чатів за надісланими</b>\n",
        "inbox_header": "\n👤 <b>Найактивніші співрозмовники</b>\n",
        "peak_header": "\n🕓 <b>Надсилання по годинах</b>\n",
        "scan_done": (
            "✅ <b>Статистику за сьогодні відновлено.</b>\n"
            "Перевірено активних чатів: <b>{chats}</b>"
        ),
        "scan_progress": "⏳ <b>Аналізую всі чати за сьогодні…</b>",
        "scan_failed": "❌ <b>Не вдалося відновити статистику:</b> <code>{}</code>",
        "user_not_found": "🔎 <b>Користувача не знайдено у статистиці за сьогодні.</b>",
        "users_peak_header": (
            "📊 <b>DailyStat</b>\n🗓 <i>сьогодні</i>\n\n"
            "👥 <b>Піки співрозмовників</b>\n"
        ),
        "help": (
            "📊 <b>DailyStat — команди</b>\n\n"
            "<code>{prefix}ds</code> — статистика за сьогодні\n"
            "<code>{prefix}ds week</code> — останні 7 днів\n"
            "<code>{prefix}ds month</code> — останні 30 днів\n"
            "<code>{prefix}ds top</code> — топ чатів сьогодні\n"
            "<code>{prefix}ds peak</code> — активність по годинах\n"
            "<code>{prefix}ds peak @username</code> — піки співрозмовника\n"
            "<code>{prefix}ds scan</code> — відновити статистику за сьогодні\n"
            "<code>{prefix}ds reset</code> — скинути всю статистику"
        ),
        "unknown_arg": (
            "❓ <b>Невідома підкоманда:</b> <code>{}</code>\n\n{}"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "top_count",
                5,
                "Кількість чатів у топі",
                validator=loader.validators.Integer(minimum=1, maximum=20),
            ),
        )

    async def client_ready(self, client, db):
        self._client = client
        self._init_storage()

    # ── Internal storage helpers ──────────────────────────────────────────

    def _init_storage(self):
        if not isinstance(self.get("stats"), dict):
            self.set("stats", {})

    @staticmethod
    def _empty_day() -> dict:
        return {
            "sent": 0,
            "received": 0,
            "media": 0,
            "chats": {},
            "senders": {},
            "hours": [0] * 24,
            "users": {},
        }

    @staticmethod
    def _safe_count(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _normalize_hours(cls, value) -> list:
        if not isinstance(value, (list, tuple)):
            value = []
        hours = [cls._safe_count(item) for item in value[:24]]
        return hours + [0] * (24 - len(hours))

    def _today_key(self) -> str:
        return datetime.date.today().isoformat()

    def _week_keys(self) -> list:
        today = datetime.date.today()
        return [(today - datetime.timedelta(days=i)).isoformat() for i in range(7)]

    def _month_keys(self) -> list:
        today = datetime.date.today()
        return [(today - datetime.timedelta(days=i)).isoformat() for i in range(30)]

    def _get_day(self, key: str) -> dict:
        stats = self.get("stats", {})
        if not isinstance(stats, dict):
            stats = {}
        stored = stats.get(key, {})
        day = dict(stored) if isinstance(stored, dict) else {}

        day["sent"] = self._safe_count(day.get("sent"))
        day["received"] = self._safe_count(day.get("received"))
        day["media"] = self._safe_count(day.get("media"))
        day["hours"] = self._normalize_hours(day.get("hours"))
        day["chats"] = self._normalize_records(
            day.get("chats"), include_kind=True
        )
        day["senders"] = self._normalize_records(day.get("senders"))

        users = day.get("users")
        normalized_users = {}
        if isinstance(users, dict):
            for user_id, raw_user in users.items():
                if not isinstance(raw_user, dict):
                    continue
                user = dict(raw_user)
                stored_id = user.get("id")
                user["id"] = self._coerce_user_id(
                    user_id if stored_id is None else stored_id
                )
                user["username"] = self._clean_username(user.get("username"))
                user["name"] = str(user.get("name") or "Unknown")
                user["sent"] = self._safe_count(user.get("sent"))
                user["received"] = self._safe_count(user.get("received"))
                user["sent_hours"] = self._normalize_hours(
                    user.get("sent_hours")
                )
                user["received_hours"] = self._normalize_hours(
                    user.get("received_hours")
                )
                normalized_users[str(user_id)] = user
        day["users"] = normalized_users
        return day

    def _normalize_records(self, records, include_kind=False) -> dict:
        normalized = {}
        if not isinstance(records, dict):
            return normalized
        for record_id, raw_record in records.items():
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            stored_id = record.get("id")
            record["id"] = self._coerce_user_id(
                record_id if stored_id is None else stored_id
            )
            record["username"] = self._clean_username(
                record.get("username")
            )
            record["name"] = str(record.get("name") or "Unknown")
            record["count"] = self._safe_count(record.get("count"))
            if include_kind:
                kind = record.get("kind")
                record["kind"] = (
                    kind if kind in {"private", "group", "channel"} else "chat"
                )
            normalized[str(record_id)] = record
        return normalized

    def _save_day(self, key: str, data: dict):
        stats = self.get("stats", {})
        if not isinstance(stats, dict):
            stats = {}
        stats[key] = data
        self.set("stats", stats)

    @staticmethod
    def _coerce_user_id(user_id):
        try:
            return int(user_id)
        except (TypeError, ValueError, OverflowError):
            return user_id

    @staticmethod
    def _clean_username(username):
        if not username:
            return None
        return str(username).lstrip("@") or None

    @staticmethod
    def _entity_name(entity) -> str:
        if entity is None:
            return "Unknown"
        title = getattr(entity, "title", None)
        if title:
            return str(title)
        full_name = " ".join(
            part for part in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            ) if part
        )
        return full_name or "Unknown"

    @staticmethod
    def _chat_kind(source) -> str:
        if (
            getattr(source, "is_private", False)
            or getattr(source, "is_user", False)
        ):
            return "private"
        if getattr(source, "is_group", False):
            return "group"
        if getattr(source, "is_channel", False):
            return "channel"
        return "chat"

    def _ensure_user(self, day: dict, user_id: int, name: str,
                     username: str = None) -> dict:
        key = str(user_id)
        if key not in day["users"]:
            day["users"][key] = {
                "id": self._coerce_user_id(user_id),
                "username": self._clean_username(username),
                "name": name, "sent": 0, "received": 0,
                "sent_hours": [0] * 24, "received_hours": [0] * 24,
            }
        user = day["users"][key]
        user["id"] = self._coerce_user_id(user_id)
        user["name"] = name
        if username:
            user["username"] = self._clean_username(username)
        else:
            user.setdefault("username", None)
        return user

    def _record_message(self, chat_id: int, chat_name: str, has_media: bool,
                        hour: int = None, username: str = None,
                        is_private: bool = True, chat_kind: str = None):
        key = self._today_key()
        day = self._get_day(key)
        hour = datetime.datetime.now().hour if hour is None else hour
        chat_kind = chat_kind or ("private" if is_private else "chat")

        day["sent"] += 1

        if has_media:
            day["media"] += 1

        day["hours"][hour] += 1

        chats = day["chats"]
        if str(chat_id) not in chats:
            chats[str(chat_id)] = {
                "id": self._coerce_user_id(chat_id),
                "username": self._clean_username(username),
                "name": chat_name,
                "kind": chat_kind,
                "count": 0,
            }
        chat = chats[str(chat_id)]
        if chat_name != "Unknown" or chat.get("name") == "Unknown":
            chat["name"] = chat_name
        chat["username"] = self._clean_username(username) or chat.get("username")
        chat["kind"] = chat_kind
        chat["count"] += 1
        # Per-user peaks only make sense for private dialogs. Groups and
        # channels still contribute to totals, media, hours and top chats.
        if is_private:
            user = self._ensure_user(day, chat_id, chat_name, username)
            user["sent"] += 1
            user["sent_hours"][hour] += 1

        self._save_day(key, day)

    def _record_received(self, sender_id: int, sender_name: str, hour: int = None,
                         username: str = None):
        """Record an incoming private message and its human/bot sender."""
        key = self._today_key()
        day = self._get_day(key)
        sender_key = str(sender_id)
        hour = datetime.datetime.now().hour if hour is None else hour

        day["received"] += 1
        if sender_key not in day["senders"]:
            day["senders"][sender_key] = {
                "id": self._coerce_user_id(sender_id),
                "username": self._clean_username(username),
                "name": sender_name, "count": 0,
            }
        day["senders"][sender_key]["name"] = sender_name
        day["senders"][sender_key]["username"] = (
            self._clean_username(username)
            or day["senders"][sender_key].get("username")
        )
        day["senders"][sender_key]["count"] += 1
        user = self._ensure_user(day, sender_id, sender_name, username)
        user["received"] += 1
        user["received_hours"][hour] += 1

        self._save_day(key, day)

    # ── Event listeners ───────────────────────────────────────────────────

    async def watcher(self, message):
        """Перехоплює всі повідомлення для підрахунку статистики."""
        if not hasattr(message, "out") or not hasattr(message, "chat_id"):
            return
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            return

        # Вхідні рахуємо лише в особистих діалогах. Таким чином повідомлення
        # каналів і груп не потрапляють до статистики, а користувачі й боти — так.
        if not message.out:
            if not getattr(message, "is_private", False):
                return

            try:
                sender = await message.get_sender()
                sender_id = getattr(sender, "id", None)
                if sender_id is None:
                    return
                sender_name = self._entity_name(sender)
            except Exception:
                return

            self._record_received(
                sender_id, sender_name, username=getattr(sender, "username", None)
            )
            return

        # Ігноруємо команди юзербота
        message_text = getattr(message, "text", None)
        if (
            isinstance(message_text, str)
            and message_text.startswith(self.get_prefix())
        ):
            return

        try:
            chat = await message.get_chat()
            chat_name = self._entity_name(chat)
        except Exception:
            chat_name = "Unknown"
            chat = None

        self._record_message(
            chat_id,
            chat_name,
            has_media=bool(getattr(message, "media", None)),
            username=getattr(chat, "username", None),
            is_private=bool(getattr(message, "is_private", False)),
            chat_kind=self._chat_kind(message),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _merge_days(self, keys: list) -> dict:
        merged = self._empty_day()
        for key in keys:
            day = self._get_day(key)
            merged["sent"] += day["sent"]
            merged["received"] += day["received"]
            merged["media"] += day["media"]
            for h in range(24):
                merged["hours"][h] += day["hours"][h]
            for cid, info in day["chats"].items():
                if cid not in merged["chats"]:
                    merged["chats"][cid] = {
                        "id": info["id"],
                        "username": info["username"],
                        "name": info["name"],
                        "kind": info["kind"],
                        "count": 0,
                    }
                merged["chats"][cid]["count"] += info["count"]
            for sender_id, info in day["senders"].items():
                if sender_id not in merged["senders"]:
                    merged["senders"][sender_id] = {
                        "id": info["id"],
                        "username": info["username"],
                        "name": info["name"],
                        "count": 0,
                    }
                merged["senders"][sender_id]["count"] += info["count"]
            for user_id, info in day["users"].items():
                if user_id not in merged["users"]:
                    merged["users"][user_id] = {
                        "id": info["id"], "username": info["username"],
                        "name": info["name"], "sent": 0, "received": 0,
                        "sent_hours": [0] * 24, "received_hours": [0] * 24,
                    }
                target = merged["users"][user_id]
                target["name"] = info["name"]
                target["username"] = info["username"] or target["username"]
                target["sent"] += info["sent"]
                target["received"] += info["received"]
                for h in range(24):
                    target["sent_hours"][h] += info["sent_hours"][h]
                    target["received_hours"][h] += info["received_hours"][h]
        return merged

    def _peak_hour(self, hours: list) -> str:
        mx = max(hours, default=0)
        if mx == 0:
            return "—"
        idx = hours.index(mx)
        return f"{idx:02d}:00–{(idx+1)%24:02d}:00"

    def _bar(self, value: int, max_val: int, width: int = 10) -> str:
        if max_val <= 0 or value <= 0:
            return "░" * width
        filled = max(1, min(width, round(value / max_val * width)))
        return "█" * filled + "░" * (width - filled)

    def _format_stat(self, data: dict, period: str, days: int = 1) -> str:
        active_chat_ids = {
            chat_id for chat_id, chat in data["chats"].items()
            if chat["count"] > 0
        }
        active_chat_ids.update(
            sender_id for sender_id, sender in data["senders"].items()
            if sender["count"] > 0
        )
        total_chats = len(active_chat_ids)
        peak = self._peak_hour(data["hours"])
        total = data["sent"] + data["received"]
        media_percent = (
            min(100, round(data["media"] / data["sent"] * 100))
            if data["sent"] else 0
        )
        average_line = (
            f"├ 📅 У середньому: <b>{total / days:.1f}</b>/день\n"
            if days > 1 else ""
        )

        text = self.strings["stat_header"].format(period=period)
        text += self.strings["stat_body"].format(
            sent=data["sent"],
            received=data["received"],
            total=total,
            media=data["media"],
            media_percent=media_percent,
            chats=total_chats,
            peak=peak,
            average_line=average_line,
        )
        return text

    @staticmethod
    def _format_name(info: dict, limit: int = 36) -> str:
        raw_name = str(info.get("name") or "Unknown")
        if len(raw_name) > limit:
            raw_name = raw_name[:limit - 1].rstrip() + "…"
        name = utils.escape_html(raw_name)
        username = str(info.get("username") or "")
        if re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            return f'<a href="https://t.me/{username}">{name}</a>'
        return name

    def _format_senders(self, data: dict, n: int) -> str:
        top = sorted(
            (
                item for item in data["senders"].values()
                if item["count"] > 0
            ),
            key=lambda item: item["count"],
            reverse=True,
        )[:n]
        if not top:
            return ""

        max_count = top[0]["count"]
        text = self.strings["inbox_header"]
        for index, sender in enumerate(top, 1):
            name = self._format_name(sender)
            bar = self._bar(sender["count"], max_count)
            text += (
                f"{index}. {name} — <b>{sender['count']}</b>\n"
                f"   <code>{bar}</code>\n"
            )
        return text

    def _format_top(self, data: dict, n: int) -> str:
        top = sorted(
            (chat for chat in data["chats"].values() if chat["count"] > 0),
            key=lambda chat: chat["count"],
            reverse=True,
        )[:n]
        if not top:
            return ""
        max_c = top[0]["count"]
        text = self.strings["top_header"]
        icons = {"private": "👤", "group": "👥", "channel": "📢", "chat": "💬"}
        for i, chat in enumerate(top, 1):
            bar = self._bar(chat["count"], max_c)
            name = self._format_name(chat)
            icon = icons.get(chat.get("kind"), "💬")
            text += (
                f"{i}. {icon} {name} — <b>{chat['count']}</b>\n"
                f"   <code>{bar}</code>\n"
            )
        return text

    def _format_peak(self, data: dict) -> str:
        hours = data["hours"]
        max_h = max(hours) or 1
        text = self.strings["peak_header"]
        # Показуємо тільки години з активністю
        active = [(h, v) for h, v in enumerate(hours) if v > 0]
        if not active:
            return ""
        for h, v in active:
            bar = self._bar(v, max_h)
            text += f"<code>{h:02d}:00  {bar}</code>  <b>{v}</b>\n"
        return text

    def _format_user_peak(self, user: dict) -> str:
        name = self._format_name(user)
        text = (
            f"📊 <b>DailyStat</b>\n🗓 <i>сьогодні</i>\n\n"
            f"👤 <b>{name}</b>\n"
        )
        text += "\n📤 <b>Я писав</b>\n"
        text += self._format_hours(user["sent_hours"])
        text += "\n📥 <b>Писав мені</b>\n"
        text += self._format_hours(user["received_hours"])
        return text

    def _format_hours(self, hours: list) -> str:
        maximum = max(hours) if hours else 0
        if not maximum:
            return "—\n"
        return "".join(
            f"<code>{h:02d}:00  {self._bar(value, maximum)}</code>  "
            f"<b>{value}</b>\n"
            for h, value in enumerate(hours) if value
        )

    def _find_user(self, data: dict, query: str):
        query = query.strip().lstrip("@").casefold()
        if query in data["users"]:
            return data["users"][query]
        matches = [
            user for user in data["users"].values()
            if query == str(user.get("username") or "").casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        matches = [
            user for user in data["users"].values()
            if query in str(user["name"]).casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    def _help_text(self) -> str:
        prefix = utils.escape_html(str(self.get_prefix()))
        return self.strings["help"].format(prefix=prefix)

    async def _scan_today(self) -> tuple:
        """Rebuild today's counters from all Telegram dialog history."""
        now = datetime.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rebuilt = self._empty_day()
        scanned = 0

        async for dialog in self._client.iter_dialogs():
            is_private = bool(getattr(dialog, "is_user", False))
            entity = getattr(dialog, "entity", None)
            chat_id = getattr(dialog, "id", None)
            if chat_id is None:
                chat_id = getattr(entity, "id", None)
            if chat_id is None:
                continue
            # ``entity`` can be a partially populated ``User`` (for example for
            # deleted accounts) whose username/access hash is ``None``.  Asking
            # Telethon to resolve such an entity again can eventually call
            # ``.encode()`` on that missing value.  Dialogs already expose the
            # ready-to-use input peer, so prefer it for history requests.
            history_peer = getattr(dialog, "input_entity", None) or entity
            if history_peer is None:
                continue
            messages = []
            has_outgoing = False
            async for item in self._client.iter_messages(
                history_peer, offset_date=now
            ):
                stamp = item.date
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=datetime.timezone.utc)
                local_stamp = stamp.astimezone(now.tzinfo)
                if local_stamp < start:
                    break
                is_command = bool(
                    item.out
                    and isinstance(getattr(item, "text", None), str)
                    and item.text.startswith(self.get_prefix())
                )
                if is_command:
                    continue
                messages.append((item, local_stamp.hour))
                has_outgoing = has_outgoing or bool(item.out)
            if not has_outgoing:
                continue

            scanned += 1
            name = str(
                getattr(dialog, "name", None)
                or getattr(dialog, "title", None)
                or self._entity_name(entity)
            )
            username = getattr(entity, "username", None)
            chat_kind = self._chat_kind(dialog)
            for item, hour in messages:
                if item.out:
                    self._add_sent(
                        rebuilt, chat_id, name, bool(item.media), hour, username,
                        is_private=is_private,
                        chat_kind=chat_kind,
                    )
                elif is_private:
                    self._add_received(rebuilt, chat_id, name, hour, username)

        stats = self.get("stats", {})
        if not isinstance(stats, dict):
            stats = {}
        stats[self._today_key()] = rebuilt
        self.set("stats", stats)
        return rebuilt, scanned

    def _add_sent(self, day, chat_id, name, has_media, hour, username=None,
                  is_private=True, chat_kind=None):
        chat_kind = chat_kind or ("private" if is_private else "chat")
        day["sent"] += 1
        day["media"] += int(has_media)
        day["hours"][hour] += 1
        chat = day["chats"].setdefault(
            str(chat_id),
            {
                "id": self._coerce_user_id(chat_id),
                "username": self._clean_username(username),
                "name": name,
                "kind": chat_kind,
                "count": 0,
            },
        )
        chat["username"] = self._clean_username(username) or chat.get("username")
        chat["kind"] = chat_kind
        chat["count"] += 1
        if is_private:
            user = self._ensure_user(day, chat_id, name, username)
            user["sent"] += 1
            user["sent_hours"][hour] += 1

    def _add_received(self, day, user_id, name, hour, username=None):
        day["received"] += 1
        sender = day["senders"].setdefault(
            str(user_id),
            {
                "id": self._coerce_user_id(user_id),
                "username": self._clean_username(username),
                "name": name,
                "count": 0,
            },
        )
        sender["username"] = self._clean_username(username) or sender.get("username")
        sender["count"] += 1
        user = self._ensure_user(day, user_id, name, username)
        user["received"] += 1
        user["received_hours"][hour] += 1

    # ── Commands ──────────────────────────────────────────────────────────

    @loader.command(ru_doc="Статистика за сьогодні")
    async def ds(self, message):
        """📊 Статистика | .ds [week|month|top|peak|scan|reset|help]"""
        args = utils.get_args_raw(message).strip().lower()

        if not args or args in {"today", "сьогодні"}:
            await self._ds_today(message)
        elif args in {"reset", "reset confirm"}:
            await self._ds_reset(message, confirmed=args == "reset confirm")
        elif args in {"week", "тиждень"}:
            await self._ds_week(message)
        elif args in {"month", "місяць"}:
            await self._ds_month(message)
        elif args == "top":
            await self._ds_top(message)
        elif args == "scan":
            await self._ds_scan(message)
        elif args == "peak" or args.startswith("peak "):
            await self._ds_peak(message, args[4:].strip())
        elif args in {"help", "?"}:
            await utils.answer(message, self._help_text())
        else:
            argument = utils.escape_html(args[:64])
            await utils.answer(
                message,
                self.strings["unknown_arg"].format(argument, self._help_text()),
            )

    async def _ds_today(self, message):
        data = self._get_day(self._today_key())
        if data["sent"] == 0 and data["received"] == 0:
            return await utils.answer(message, self.strings["no_data"])

        text = self._format_stat(data, "сьогодні")
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_week(self, message):
        data = self._merge_days(self._week_keys())
        if data["sent"] == 0 and data["received"] == 0:
            return await utils.answer(message, self.strings["no_data"])

        text = self._format_stat(data, "останні 7 днів", days=7)
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_month(self, message):
        data = self._merge_days(self._month_keys())
        if data["sent"] == 0 and data["received"] == 0:
            return await utils.answer(message, self.strings["no_data"])

        text = self._format_stat(data, "останні 30 днів", days=30)
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_top(self, message):
        data = self._get_day(self._today_key())
        if not any(chat["count"] > 0 for chat in data["chats"].values()):
            return await utils.answer(message, self.strings["no_data"])

        text = "📊 <b>DailyStat</b>\n🗓 <i>топ чатів сьогодні</i>\n"
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_peak(self, message, user_query=""):
        data = self._get_day(self._today_key())
        if user_query == "users":
            users = sorted(
                (
                    user for user in data["users"].values()
                    if user["sent"] + user["received"] > 0
                ),
                key=lambda user: user["sent"] + user["received"],
                reverse=True,
            )[:self.config["top_count"]]
            if not users:
                return await utils.answer(message, self.strings["no_data"])
            text = self.strings["users_peak_header"]
            for user in users:
                name = self._format_name(user)
                text += (
                    f"\n👤 <b>{name}</b>\n"
                    f"├ 📤 Я: <b>{self._peak_hour(user['sent_hours'])}</b>\n"
                    f"└ 📥 Мені: <b>{self._peak_hour(user['received_hours'])}</b>\n"
                )
            return await utils.answer(message, text)
        if user_query:
            user = self._find_user(data, user_query)
            if not user:
                return await utils.answer(message, self.strings["user_not_found"])
            return await utils.answer(message, self._format_user_peak(user))
        peak_text = self._format_peak(data)
        if not peak_text:
            return await utils.answer(message, self.strings["no_data"])

        text = "📊 <b>DailyStat</b>\n🗓 <i>сьогодні</i>\n"
        text += peak_text
        await utils.answer(message, text)

    async def _ds_scan(self, message):
        # ``utils.answer`` may replace the original command message (rather than
        # edit it in place).  Keep the returned message so the final update does
        # not target a deleted/stale message and surface Hikka's generic
        # "Call .ds scan failed" notification.
        status = await utils.answer(message, self.strings["scan_progress"])
        try:
            data, chats = await self._scan_today()
        except Exception as error:
            logger.exception("DailyStat history scan failed")
            error_text = utils.escape_html(str(error) or type(error).__name__)
            return await utils.answer(
                status or message,
                self.strings["scan_failed"].format(error_text[:500]),
            )
        text = self.strings["scan_done"].format(chats=chats) + "\n\n"
        text += self._format_stat(data, "сьогодні")
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(status or message, text)

    async def _ds_reset(self, message, confirmed=False):
        if not confirmed:
            prefix = utils.escape_html(str(self.get_prefix()))
            return await utils.answer(
                message,
                self.strings["reset_warning"].format(prefix=prefix),
            )
        self.set("stats", {})
        await utils.answer(message, self.strings["reset_done"])
