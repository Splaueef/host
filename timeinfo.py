# meta developer: @Codex
# meta version: 1.0.0
# meta description: Точний час VPS, час Hikka, UTC, Unix timestamp і аптайм модуля.

import datetime
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import loader, utils


def _format_offset(value):
    """Return a human-readable UTC offset for an aware datetime."""
    offset = value.utcoffset() or datetime.timedelta()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _format_duration(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours or days:
        parts.append(f"{hours} год.")
    if minutes or hours or days:
        parts.append(f"{minutes} хв.")
    parts.append(f"{seconds} с.")
    return " ".join(parts)


@loader.tds
class TimeInfoMod(loader.Module):
    """Показує час VPS і Hikka та конвертує часові позначки"""

    strings = {
        "name": "TimeInfo",
        "cfg_timezone": (
            "IANA-таймзона, яку має використовувати модуль для часу Hikka "
            "(наприклад Europe/Kyiv)"
        ),
        "time": (
            "🕰 <b>Точний час</b>\n\n"
            "🖥 <b>VPS:</b> <code>{vps}</code>\n"
            "🤖 <b>Hikka:</b> <code>{hikka}</code>\n"
            "🌐 <b>UTC:</b> <code>{utc}</code>\n"
            "🔢 <b>Unix:</b> <code>{timestamp}</code>\n"
            "⏱ <b>Аптайм модуля:</b> <code>{uptime}</code>\n\n"
            "<i>Усі значення отримані з одного знімка системного годинника.</i>"
        ),
        "timezone": (
            "🌍 <b>Час Hikka:</b> <code>{time}</code>\n"
            "📍 <b>Таймзона:</b> <code>{zone}</code> (<code>{offset}</code>)"
        ),
        "timezone_set": "✅ <b>Таймзону Hikka змінено:</b> <code>{}</code>",
        "bad_timezone": (
            "❌ <b>Невідома таймзона:</b> <code>{}</code>\n"
            "Приклад: <code>.timezone Europe/Kyiv</code>"
        ),
        "timestamp": (
            "🔄 <b>Конвертація часу</b>\n\n"
            "📅 <b>Hikka:</b> <code>{local}</code>\n"
            "🌐 <b>UTC:</b> <code>{utc}</code>\n"
            "🔢 <b>Unix:</b> <code>{timestamp}</code>"
        ),
        "bad_timestamp": (
            "❌ <b>Не вдалося розпізнати час.</b>\n"
            "Використання: <code>.timestamp 1788451200</code> або "
            "<code>.timestamp 2026-09-03 12:00:00</code>"
        ),
        "uptime": "⏱ <b>Аптайм модуля TimeInfo:</b> <code>{}</code>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "timezone",
                "Europe/Kyiv",
                lambda: self.strings("cfg_timezone"),
            )
        )
        self._started_at = time.monotonic()

    def _hikka_timezone(self):
        return ZoneInfo(self.config["timezone"])

    @staticmethod
    def _render_time(value):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + f" {value.tzname()}"

    async def timecmd(self, message):
        """Показати точний час VPS, Hikka, UTC, Unix timestamp та аптайм"""
        try:
            hikka_tz = self._hikka_timezone()
        except ZoneInfoNotFoundError:
            return await utils.answer(
                message,
                self.strings("bad_timezone", message).format(
                    utils.escape_html(str(self.config["timezone"]))
                ),
            )

        # Capture once so milliseconds and the Unix timestamp cannot disagree.
        now = datetime.datetime.now(datetime.timezone.utc)
        vps_now = now.astimezone()
        hikka_now = now.astimezone(hikka_tz)
        await utils.answer(
            message,
            self.strings("time", message).format(
                vps=utils.escape_html(self._render_time(vps_now)),
                hikka=utils.escape_html(self._render_time(hikka_now)),
                utc=utils.escape_html(self._render_time(now)),
                timestamp=f"{now.timestamp():.3f}",
                uptime=_format_duration(time.monotonic() - self._started_at),
            ),
        )

    async def timezonecmd(self, message):
        """Показати або змінити таймзону Hikka: .timezone Europe/Kyiv"""
        requested = utils.get_args_raw(message).strip()
        zone_name = requested or str(self.config["timezone"])
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            return await utils.answer(
                message,
                self.strings("bad_timezone", message).format(
                    utils.escape_html(zone_name)
                ),
            )

        if requested:
            self.config["timezone"] = requested
            return await utils.answer(
                message,
                self.strings("timezone_set", message).format(
                    utils.escape_html(requested)
                ),
            )

        now = datetime.datetime.now(zone)
        await utils.answer(
            message,
            self.strings("timezone", message).format(
                time=utils.escape_html(self._render_time(now)),
                zone=utils.escape_html(zone_name),
                offset=_format_offset(now),
            ),
        )

    async def timestampcmd(self, message):
        """Конвертувати Unix timestamp або дату в часовій зоні Hikka"""
        raw = utils.get_args_raw(message).strip()
        try:
            zone = self._hikka_timezone()
            if not raw:
                value = datetime.datetime.now(datetime.timezone.utc)
            else:
                try:
                    value = datetime.datetime.fromtimestamp(float(raw), datetime.timezone.utc)
                except ValueError:
                    value = datetime.datetime.fromisoformat(raw)
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=zone)
                    value = value.astimezone(datetime.timezone.utc)
        except (OverflowError, OSError, ValueError, ZoneInfoNotFoundError):
            return await utils.answer(message, self.strings("bad_timestamp", message))

        local = value.astimezone(zone)
        await utils.answer(
            message,
            self.strings("timestamp", message).format(
                local=utils.escape_html(self._render_time(local)),
                utc=utils.escape_html(self._render_time(value)),
                timestamp=f"{value.timestamp():.3f}",
            ),
        )

    async def uptimecmd(self, message):
        """Показати, скільки часу завантажений модуль TimeInfo"""
        await utils.answer(
            message,
            self.strings("uptime", message).format(
                _format_duration(time.monotonic() - self._started_at)
            ),
        )
