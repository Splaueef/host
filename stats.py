# meta developer: @Huai_Baike
# meta version: 1.1.1
# meta description: 📊 Статистика вашої активності в Telegram — повідомлення, чати, піки по годинах.

import datetime

from .. import loader, utils


@loader.tds
class DailyStatMod(loader.Module):
    """📊 Відстежує вашу щоденну активність у Telegram"""

    strings = {
        "name": "DailyStat",
        "no_data": "📭 <b>Немає даних за цей період.</b>\nПочніть спілкуватися — статистика з'явиться автоматично.",
        "reset_done": "🗑 <b>Статистику скинуто.</b>",
        "stat_header": "📊 <b>DailyStat</b> — {period}\n\n",
        "stat_body": (
            "✉️ Надіслано: <b>{sent}</b>\n"
            "📥 Отримано в особистих: <b>{received}</b>\n"
            "📎 Медіа: <b>{media}</b>\n"
            "💬 Активних чатів: <b>{chats}</b>\n"
            "⏰ Пік активності: <b>{peak}</b>\n"
        ),
        "top_header": "\n🏆 <b>Топ чати:</b>\n",
        "inbox_header": "\n👤 <b>Хто писав мені:</b>\n",
        "peak_header": "\n📈 <b>Активність по годинах:</b>\n",
        "scan_done": (
            "✅ <b>Статистику за сьогодні відновлено.</b>\n"
            "Перевірено особистих діалогів: <b>{chats}</b>"
        ),
        "scan_progress": "⏳ <b>Аналізую особисті діалоги за сьогодні…</b>",
        "user_not_found": "🔎 <b>Користувача не знайдено у статистиці за сьогодні.</b>",
        "users_peak_header": "📊 <b>DailyStat</b> — піки співрозмовників сьогодні\n\n",
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
        if not self.get("stats"):
            self.set("stats", {})

    def _today_key(self) -> str:
        return datetime.date.today().isoformat()

    def _week_keys(self) -> list:
        today = datetime.date.today()
        return [(today - datetime.timedelta(days=i)).isoformat() for i in range(7)]

    def _get_day(self, key: str) -> dict:
        stats = self.get("stats", {})
        day = stats.get(key, {})

        # setdefault keeps data written by older DailyStat versions compatible.
        day.setdefault("sent", 0)
        day.setdefault("received", 0)
        day.setdefault("media", 0)
        day.setdefault("chats", {})
        day.setdefault("senders", {})
        day.setdefault("hours", [0] * 24)
        day.setdefault("users", {})
        return day

    def _save_day(self, key: str, data: dict):
        stats = self.get("stats", {})
        stats[key] = data
        self.set("stats", stats)

    def _ensure_user(self, day: dict, user_id: int, name: str) -> dict:
        key = str(user_id)
        if key not in day["users"]:
            day["users"][key] = {
                "name": name, "sent": 0, "received": 0,
                "sent_hours": [0] * 24, "received_hours": [0] * 24,
            }
        day["users"][key]["name"] = name
        return day["users"][key]

    def _record_message(self, chat_id: int, chat_name: str, has_media: bool,
                        hour: int = None):
        key = self._today_key()
        day = self._get_day(key)
        hour = datetime.datetime.now().hour if hour is None else hour

        day["sent"] += 1

        if has_media:
            day["media"] += 1

        day["hours"][hour] += 1

        chats = day["chats"]
        if str(chat_id) not in chats:
            chats[str(chat_id)] = {"name": chat_name, "count": 0}
        chats[str(chat_id)]["count"] += 1
        user = self._ensure_user(day, chat_id, chat_name)
        user["sent"] += 1
        user["sent_hours"][hour] += 1

        self._save_day(key, day)

    def _record_received(self, sender_id: int, sender_name: str, hour: int = None):
        """Record an incoming private message and its human/bot sender."""
        key = self._today_key()
        day = self._get_day(key)
        sender_key = str(sender_id)
        hour = datetime.datetime.now().hour if hour is None else hour

        day["received"] += 1
        if sender_key not in day["senders"]:
            day["senders"][sender_key] = {"name": sender_name, "count": 0}
        day["senders"][sender_key]["name"] = sender_name
        day["senders"][sender_key]["count"] += 1
        user = self._ensure_user(day, sender_id, sender_name)
        user["received"] += 1
        user["received_hours"][hour] += 1

        self._save_day(key, day)

    # ── Event listeners ───────────────────────────────────────────────────

    async def watcher(self, message):
        """Перехоплює всі повідомлення для підрахунку статистики."""
        if not hasattr(message, "out") or not hasattr(message, "chat_id"):
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
                sender_name = (
                    getattr(sender, "first_name", None)
                    or getattr(sender, "title", None)
                    or "Unknown"
                )
            except Exception:
                return

            self._record_received(sender_id, sender_name)
            return

        # Ігноруємо команди юзербота
        if message.text and message.text.startswith(self.get_prefix()):
            return

        # DailyStat призначений для особистого спілкування: вихідні
        # повідомлення у групах і каналах також не враховуємо.
        if not getattr(message, "is_private", False):
            return

        try:
            chat = await message.get_chat()
            chat_name = (
                getattr(chat, "title", None)
                or getattr(chat, "first_name", None)
                or "Unknown"
            )
        except Exception:
            chat_name = "Unknown"

        self._record_message(
            message.chat_id,
            chat_name,
            has_media=bool(message.media),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _merge_days(self, keys: list) -> dict:
        merged = {
            "sent": 0, "received": 0, "media": 0,
            "chats": {}, "senders": {}, "hours": [0] * 24, "users": {},
        }
        for key in keys:
            day = self._get_day(key)
            merged["sent"] += day["sent"]
            merged["received"] += day["received"]
            merged["media"] += day["media"]
            for h in range(24):
                merged["hours"][h] += day["hours"][h]
            for cid, info in day["chats"].items():
                if cid not in merged["chats"]:
                    merged["chats"][cid] = {"name": info["name"], "count": 0}
                merged["chats"][cid]["count"] += info["count"]
            for sender_id, info in day["senders"].items():
                if sender_id not in merged["senders"]:
                    merged["senders"][sender_id] = {"name": info["name"], "count": 0}
                merged["senders"][sender_id]["name"] = info["name"]
                merged["senders"][sender_id]["count"] += info["count"]
            for user_id, info in day["users"].items():
                if user_id not in merged["users"]:
                    merged["users"][user_id] = {
                        "name": info["name"], "sent": 0, "received": 0,
                        "sent_hours": [0] * 24, "received_hours": [0] * 24,
                    }
                target = merged["users"][user_id]
                target["name"] = info["name"]
                target["sent"] += info["sent"]
                target["received"] += info["received"]
                for h in range(24):
                    target["sent_hours"][h] += info["sent_hours"][h]
                    target["received_hours"][h] += info["received_hours"][h]
        return merged

    def _peak_hour(self, hours: list) -> str:
        mx = max(hours)
        if mx == 0:
            return "—"
        idx = hours.index(mx)
        return f"{idx:02d}:00–{(idx+1)%24:02d}:00"

    def _bar(self, value: int, max_val: int, width: int = 10) -> str:
        if max_val == 0:
            return "░" * width
        filled = round(value / max_val * width)
        return "█" * filled + "░" * (width - filled)

    def _format_stat(self, data: dict, period: str) -> str:
        total_chats = len([c for c in data["chats"].values() if c["count"] > 0])
        peak = self._peak_hour(data["hours"])

        text = self.strings["stat_header"].format(period=period)
        text += self.strings["stat_body"].format(
            sent=data["sent"],
            received=data["received"],
            media=data["media"],
            chats=total_chats,
            peak=peak,
        )
        return text

    def _format_senders(self, data: dict, n: int) -> str:
        top = sorted(
            data["senders"].values(), key=lambda item: item["count"], reverse=True
        )[:n]
        if not top:
            return ""

        max_count = top[0]["count"]
        text = self.strings["inbox_header"]
        for index, sender in enumerate(top, 1):
            name = utils.escape_html(str(sender["name"]))
            bar = self._bar(sender["count"], max_count)
            text += f"{index}. {name}  {bar}  <b>{sender['count']}</b>\n"
        return text

    def _format_top(self, data: dict, n: int) -> str:
        top = sorted(data["chats"].values(), key=lambda x: x["count"], reverse=True)[:n]
        if not top:
            return ""
        max_c = top[0]["count"]
        text = self.strings["top_header"]
        for i, chat in enumerate(top, 1):
            bar = self._bar(chat["count"], max_c)
            name = utils.escape_html(str(chat["name"]))
            text += f"{i}. {name}  {bar}  <b>{chat['count']}</b>\n"
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
            text += f"<code>{h:02d}:00</code> {bar} <b>{v}</b>\n"
        return text

    def _format_user_peak(self, user: dict) -> str:
        name = utils.escape_html(str(user["name"]))
        text = f"📊 <b>DailyStat</b> — активність: <b>{name}</b>\n"
        text += "\n📤 <b>Я писав:</b>\n"
        text += self._format_hours(user["sent_hours"])
        text += "\n📥 <b>Писав мені:</b>\n"
        text += self._format_hours(user["received_hours"])
        return text

    def _format_hours(self, hours: list) -> str:
        maximum = max(hours) if hours else 0
        if not maximum:
            return "—\n"
        return "".join(
            f"<code>{h:02d}:00</code> {self._bar(value, maximum)} <b>{value}</b>\n"
            for h, value in enumerate(hours) if value
        )

    def _find_user(self, data: dict, query: str):
        query = query.strip().lstrip("@").casefold()
        if query in data["users"]:
            return data["users"][query]
        matches = [
            user for user in data["users"].values()
            if query in str(user["name"]).casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    async def _scan_today(self) -> tuple:
        """Rebuild today's counters from Telegram private-dialog history."""
        now = datetime.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rebuilt = self._get_day("__empty__")
        scanned = 0

        async for dialog in self._client.iter_dialogs():
            if not getattr(dialog, "is_user", False):
                continue
            entity = getattr(dialog, "entity", None)
            user_id = getattr(entity, "id", None)
            if user_id is None:
                continue
            messages = []
            has_outgoing = False
            async for item in self._client.iter_messages(entity, offset_date=now):
                stamp = item.date
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=datetime.timezone.utc)
                local_stamp = stamp.astimezone(now.tzinfo)
                if local_stamp < start:
                    break
                is_command = bool(
                    item.out and item.text and item.text.startswith(self.get_prefix())
                )
                if is_command:
                    continue
                messages.append((item, local_stamp.hour))
                has_outgoing = has_outgoing or bool(item.out)
            if not has_outgoing:
                continue

            scanned += 1
            name = (
                getattr(entity, "first_name", None)
                or getattr(entity, "title", None)
                or "Unknown"
            )
            for item, hour in messages:
                if item.out:
                    self._add_sent(rebuilt, user_id, name, bool(item.media), hour)
                else:
                    self._add_received(rebuilt, user_id, name, hour)

        stats = self.get("stats", {})
        stats[self._today_key()] = rebuilt
        stats.pop("__empty__", None)
        self.set("stats", stats)
        return rebuilt, scanned

    def _add_sent(self, day, user_id, name, has_media, hour):
        day["sent"] += 1
        day["media"] += int(has_media)
        day["hours"][hour] += 1
        chat = day["chats"].setdefault(str(user_id), {"name": name, "count": 0})
        chat["count"] += 1
        user = self._ensure_user(day, user_id, name)
        user["sent"] += 1
        user["sent_hours"][hour] += 1

    def _add_received(self, day, user_id, name, hour):
        day["received"] += 1
        sender = day["senders"].setdefault(str(user_id), {"name": name, "count": 0})
        sender["count"] += 1
        user = self._ensure_user(day, user_id, name)
        user["received"] += 1
        user["received_hours"][hour] += 1

    # ── Commands ──────────────────────────────────────────────────────────

    @loader.command(ru_doc="Статистика за сьогодні")
    async def ds(self, message):
        """📊 Статистика | .ds [scan|week|top|peak [користувач]|reset]"""
        args = utils.get_args_raw(message).strip().lower()

        if args == "reset":
            await self._ds_reset(message)
        elif args == "week":
            await self._ds_week(message)
        elif args == "top":
            await self._ds_top(message)
        elif args == "scan":
            await self._ds_scan(message)
        elif args == "peak" or args.startswith("peak "):
            await self._ds_peak(message, args[4:].strip())
        else:
            await self._ds_today(message)

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

        text = self._format_stat(data, "останні 7 днів")
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_top(self, message):
        data = self._get_day(self._today_key())
        if not data["chats"]:
            return await utils.answer(message, self.strings["no_data"])

        text = "📊 <b>DailyStat</b> — топ чати сьогодні\n"
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_peak(self, message, user_query=""):
        data = self._get_day(self._today_key())
        if user_query == "users":
            if not data["users"]:
                return await utils.answer(message, self.strings["no_data"])
            text = self.strings["users_peak_header"]
            users = sorted(
                data["users"].values(),
                key=lambda user: user["sent"] + user["received"],
                reverse=True,
            )
            for user in users:
                name = utils.escape_html(str(user["name"]))
                text += (
                    f"👤 <b>{name}</b>: я — "
                    f"{self._peak_hour(user['sent_hours'])}; "
                    f"мені — {self._peak_hour(user['received_hours'])}\n"
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

        text = "📊 <b>DailyStat</b> — активність по годинах\n"
        text += peak_text
        await utils.answer(message, text)

    async def _ds_scan(self, message):
        await utils.answer(message, self.strings["scan_progress"])
        data, chats = await self._scan_today()
        text = self.strings["scan_done"].format(chats=chats) + "\n\n"
        text += self._format_stat(data, "сьогодні")
        text += self._format_senders(data, self.config["top_count"])
        text += self._format_top(data, self.config["top_count"])
        await utils.answer(message, text)

    async def _ds_reset(self, message):
        self.set("stats", {})
        await utils.answer(message, self.strings["reset_done"])
