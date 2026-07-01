# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 📈 Повний аналіз повідомлень у поточному чаті за командою !аналіз.

import calendar
import datetime
import time
from collections import defaultdict

from telethon.tl.types import PeerUser

from .. import loader, utils


@loader.tds
class ChatAnalysisMod(loader.Module):
    """📈 Аналізує всю історію поточного чату командою !аналіз"""

    strings = {
        "name": "ChatAnalysis",
        "started": "⏳ <b>Аналізую всю історію цього чату...</b>\nЦе може зайняти деякий час у великих чатах.",
        "empty": "📭 <b>У цьому чаті не знайдено повідомлень для аналізу.</b>",
        "done": "📈 <b>Повний аналіз чату</b>\n",
        "error": "🚫 <b>Не вдалося проаналізувати чат:</b> <code>{}</code>",
        "progress": "⏳ <b>Аналізую чат...</b>\n🔎 Проскановано повідомлень: <b>{}</b>\n⚡️ Швидкість: <b>{:.1f} повідомлень/с</b>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "top_limit",
                8,
                "Скільки найактивніших годин/тижнів/місяців показувати",
                validator=loader.validators.Integer(minimum=3, maximum=20),
            ),
            loader.ConfigValue(
                "answer_limit",
                3900,
                "Максимальна довжина відповіді, щоб Telegram її прийняв",
                validator=loader.validators.Integer(minimum=2500, maximum=4096),
            ),
        )

    async def client_ready(self, client, db):
        self._me = await client.get_me()

    async def watcher(self, message):
        """Запускає аналіз, коли в чаті написано !аналіз."""
        if not getattr(message, "text", None):
            return

        if message.text.strip().lower() != "!аналіз":
            return

        if not getattr(message, "out", False):
            return

        status = await utils.answer(message, self.strings["started"])

        try:
            text = await self._build_analysis(message, status)
        except Exception as e:
            return await utils.answer(status, self.strings["error"].format(utils.escape_html(str(e))))

        await utils.answer(status, text)

    async def _build_analysis(self, message, status):
        stats = self._new_stats()
        chat = await message.get_chat()
        chat_title = self._chat_title(chat)
        chat_type = self._chat_type(message)

        scanned = 0
        started_at = time.monotonic()
        last_progress_at = started_at

        async for msg in message.client.iter_messages(message.to_id, reverse=True):
            scanned += 1
            now = time.monotonic()
            if now - last_progress_at >= 10:
                speed = scanned / max(now - started_at, 1)
                await utils.answer(status, self.strings["progress"].format(scanned, speed))
                last_progress_at = now

            if not self._is_countable(msg):
                continue

            side = "me" if msg.sender_id == self._me.id else "others"
            self._add_message(stats[side], msg)
            self._add_message(stats["all"], msg)

        if stats["all"]["total"] == 0:
            return self.strings["empty"]

        return self._format_report(stats, chat_title, chat_type)

    def _new_stats(self):
        return {
            "me": self._new_bucket(),
            "others": self._new_bucket(),
            "all": self._new_bucket(),
        }

    @staticmethod
    def _new_bucket():
        return {
            "total": 0,
            "text": 0,
            "media": 0,
            "service": 0,
            "first": None,
            "last": None,
            "hours": [0] * 24,
            "weekdays": [0] * 7,
            "weeks": defaultdict(int),
            "months": defaultdict(int),
        }

    @staticmethod
    def _is_countable(msg):
        return bool(getattr(msg, "message", None) or getattr(msg, "media", None) or getattr(msg, "action", None))

    def _add_message(self, bucket, msg):
        dt = msg.date.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        iso_year, iso_week, _ = dt.isocalendar()
        month_key = dt.strftime("%Y-%m")
        week_key = f"{iso_year}-W{iso_week:02d}"

        bucket["total"] += 1
        bucket["first"] = dt if bucket["first"] is None or dt < bucket["first"] else bucket["first"]
        bucket["last"] = dt if bucket["last"] is None or dt > bucket["last"] else bucket["last"]
        bucket["hours"][dt.hour] += 1
        bucket["weekdays"][dt.weekday()] += 1
        bucket["weeks"][week_key] += 1
        bucket["months"][month_key] += 1

        if getattr(msg, "action", None):
            bucket["service"] += 1
        elif getattr(msg, "media", None):
            bucket["media"] += 1
        else:
            bucket["text"] += 1

    @staticmethod
    def _chat_title(chat):
        return utils.escape_html(
            getattr(chat, "title", None)
            or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)]))
            or "цей чат"
        )

    @staticmethod
    def _chat_type(message):
        if isinstance(getattr(message, "to_id", None), PeerUser):
            return "особистий чат"
        if getattr(message, "is_group", False):
            return "група"
        if getattr(message, "is_channel", False):
            return "канал/супергрупа"
        return "чат"

    def _format_report(self, stats, chat_title, chat_type):
        total = stats["all"]["total"]
        lines = [
            self.strings["done"],
            f"💬 <b>Чат:</b> {chat_title}",
            f"📌 <b>Тип:</b> {chat_type}",
            f"🧮 <b>Усього повідомлень:</b> {total}",
            f"🗓 <b>Період:</b> {self._range(stats['all'])}",
            "",
            self._format_side("🙋 Мої повідомлення", stats["me"], total),
            self._format_side("👥 Повідомлення інших", stats["others"], total),
            self._format_activity("⏰ Активність по годинах", stats),
            self._format_top_map("📅 Найактивніші тижні", stats),
            self._format_top_map("🗓 Найактивніші місяці", stats, key="months"),
        ]
        text = "\n".join(filter(None, lines))
        limit = self.config["answer_limit"]
        if len(text) > limit:
            text = text[: limit - 80].rsplit("\n", 1)[0] + "\n\n… <i>Звіт обрізано через ліміт Telegram.</i>"
        return text

    def _format_side(self, title, bucket, total):
        percent = bucket["total"] / total * 100 if total else 0
        return (
            f"<b>{title}</b>\n"
            f"• Усього: <b>{bucket['total']}</b> ({percent:.1f}%)\n"
            f"• Текст: <b>{bucket['text']}</b> | Медіа: <b>{bucket['media']}</b> | Сервісні: <b>{bucket['service']}</b>\n"
            f"• Перший/останній запис: {self._range(bucket)}\n"
            f"• Пікова година: <b>{self._peak_hour(bucket['hours'])}</b>\n"
            f"• Найактивніший день: <b>{self._peak_weekday(bucket['weekdays'])}</b>"
        )

    def _format_activity(self, title, stats):
        active_hours = [(h, stats["all"]["hours"][h], stats["me"]["hours"][h], stats["others"]["hours"][h]) for h in range(24) if stats["all"]["hours"][h]]
        active_hours.sort(key=lambda item: item[1], reverse=True)
        rows = [f"<b>{title}</b>"]
        for hour, total, mine, others in active_hours[: self.config["top_limit"]]:
            rows.append(f"• <code>{hour:02d}:00</code> — всі: <b>{total}</b>, я: <b>{mine}</b>, інші: <b>{others}</b>")
        return "\n".join(rows)

    def _format_top_map(self, title, stats, key="weeks"):
        rows = [f"<b>{title}</b>"]
        top = sorted(stats["all"][key].items(), key=lambda item: item[1], reverse=True)[: self.config["top_limit"]]
        for period, total in top:
            rows.append(f"• <code>{period}</code> — всі: <b>{total}</b>, я: <b>{stats['me'][key].get(period, 0)}</b>, інші: <b>{stats['others'][key].get(period, 0)}</b>")
        return "\n".join(rows)

    @staticmethod
    def _range(bucket):
        if not bucket["first"]:
            return "—"
        return f"{bucket['first']:%Y-%m-%d %H:%M} → {bucket['last']:%Y-%m-%d %H:%M} UTC"

    @staticmethod
    def _peak_hour(hours):
        if not any(hours):
            return "—"
        hour = max(range(24), key=lambda h: hours[h])
        return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00 ({hours[hour]})"

    @staticmethod
    def _peak_weekday(weekdays):
        if not any(weekdays):
            return "—"
        day = max(range(7), key=lambda d: weekdays[d])
        return f"{calendar.day_name[day]} ({weekdays[day]})"
