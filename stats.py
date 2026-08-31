# meta developer: @Huai_Baike
# meta version: 1.0.0
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
        return day

    def _save_day(self, key: str, data: dict):
        stats = self.get("stats", {})
        stats[key] = data
        self.set("stats", stats)

    def _record_message(self, chat_id: int, chat_name: str, has_media: bool):
        key = self._today_key()
        day = self._get_day(key)
        hour = datetime.datetime.now().hour

        day["sent"] += 1

        if has_media:
            day["media"] += 1

        day["hours"][hour] += 1

        chats = day["chats"]
        if str(chat_id) not in chats:
            chats[str(chat_id)] = {"name": chat_name, "count": 0}
        chats[str(chat_id)]["count"] += 1

        self._save_day(key, day)

    def _record_received(self, sender_id: int, sender_name: str):
        """Record an incoming private message and its human/bot sender."""
        key = self._today_key()
        day = self._get_day(key)
        sender_key = str(sender_id)

        day["received"] += 1
        if sender_key not in day["senders"]:
            day["senders"][sender_key] = {"name": sender_name, "count": 0}
        day["senders"][sender_key]["name"] = sender_name
        day["senders"][sender_key]["count"] += 1

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
            "chats": {}, "senders": {}, "hours": [0] * 24,
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
            text += f"{i}. {chat['name']}  {bar}  <b>{chat['count']}</b>\n"
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

    # ── Commands ──────────────────────────────────────────────────────────

    @loader.command(ru_doc="Статистика за сьогодні")
    async def ds(self, message):
        """📊 Статистика | .ds [week|top|peak|reset]"""
        args = utils.get_args_raw(message).strip().lower()

        if args == "reset":
            await self._ds_reset(message)
        elif args == "week":
            await self._ds_week(message)
        elif args == "top":
            await self._ds_top(message)
        elif args == "peak":
            await self._ds_peak(message)
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

    async def _ds_peak(self, message):
        data = self._get_day(self._today_key())
        peak_text = self._format_peak(data)
        if not peak_text:
            return await utils.answer(message, self.strings["no_data"])

        text = "📊 <b>DailyStat</b> — активність по годинах\n"
        text += peak_text
        await utils.answer(message, text)

    async def _ds_reset(self, message):
        self.set("stats", {})
        await utils.answer(message, self.strings["reset_done"])
