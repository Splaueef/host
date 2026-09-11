# meta developer: @Codex
# meta version: 1.0.0
# meta description: Надсилання різних повідомлень у вибрані чати за одноразовим, щоденним або щотижневим розкладом.

import asyncio
import copy
import datetime
import logging
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon import utils as telethon_utils

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class MessageSchedulerMod(loader.Module):
    """Надсилає повідомлення від вашого акаунта у вибрані чати за розкладом"""

    strings = {
        "name": "MessageScheduler",
        "cfg_timezone": "IANA-таймзона розкладів, наприклад Europe/Kyiv або UTC",
        "cfg_check_interval": "Як часто перевіряти розклади, у секундах",
        "cfg_retry_delay": "Затримка повторної спроби після помилки, у секундах",
        "cfg_max_failures": "Після скількох помилок поспіль вимкнути завдання",
        "cfg_link_preview": "Показувати прев'ю посилань у запланованих повідомленнях",
        "bad_args": "❌ <b>{}</b>\n\nНадішли <code>{}ms help</code>, щоб побачити приклади.",
        "not_found": "❌ <b>Завдання не знайдено:</b> <code>{}</code>",
        "empty": "📭 <b>Запланованих повідомлень ще немає.</b>\n\n<code>{}ms help</code> — приклади створення.",
        "added": "✅ <b>Повідомлення заплановано</b>\n\n{}",
        "updated": "✅ <b>Завдання оновлено</b>\n\n{}",
        "removed": "🗑 <b>Завдання видалено:</b> <code>{}</code>",
        "enabled": "▶️ <b>Завдання увімкнено</b>\nНаступне надсилання: <code>{}</code>",
        "disabled": "⏸ <b>Завдання вимкнено:</b> <code>{}</code>",
        "sent": "📨 <b>Повідомлення надіслано.</b>",
        "send_failed": "❌ <b>Не вдалося надіслати повідомлення:</b> <code>{}</code>",
        "clear_confirm": "⚠️ Для видалення всіх завдань введи <code>{}ms clear confirm</code>.",
        "cleared": "🧹 <b>Усі заплановані повідомлення видалено.</b>",
    }

    PAGE_SIZE = 6
    MAX_TEXT_LENGTH = 4096
    MODES = {
        "once": "once",
        "разово": "once",
        "daily": "daily",
        "щодня": "daily",
        "weekly": "weekly",
        "щотижня": "weekly",
    }
    WEEKDAYS = {
        "mon": 0,
        "monday": 0,
        "пн": 0,
        "tue": 1,
        "tuesday": 1,
        "вт": 1,
        "wed": 2,
        "wednesday": 2,
        "ср": 2,
        "thu": 3,
        "thursday": 3,
        "чт": 3,
        "fri": 4,
        "friday": 4,
        "пт": 4,
        "sat": 5,
        "saturday": 5,
        "сб": 5,
        "sun": 6,
        "sunday": 6,
        "нд": 6,
    }
    WEEKDAY_LABELS = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "timezone",
                "Europe/Kyiv",
                lambda: self.strings("cfg_timezone"),
            ),
            loader.ConfigValue(
                "check_interval",
                15,
                lambda: self.strings("cfg_check_interval"),
                validator=loader.validators.Integer(minimum=5, maximum=3600),
            ),
            loader.ConfigValue(
                "retry_delay",
                300,
                lambda: self.strings("cfg_retry_delay"),
                validator=loader.validators.Integer(minimum=30, maximum=86400),
            ),
            loader.ConfigValue(
                "max_failures",
                5,
                lambda: self.strings("cfg_max_failures"),
                validator=loader.validators.Integer(minimum=1, maximum=100),
            ),
            loader.ConfigValue(
                "link_preview",
                True,
                lambda: self.strings("cfg_link_preview"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._lock = asyncio.Lock()
        self._next_tick = 0.0

    async def client_ready(self, client, db):
        self._client = client
        jobs = self.get("jobs", [])
        if not isinstance(jobs, list):
            jobs = []
        self.set("jobs", [job for job in jobs if self._valid_stored_job(job)])

    @loader.loop(interval=1, autostart=True)
    async def scheduler(self):
        if not self._client or time.monotonic() < self._next_tick:
            return
        self._next_tick = time.monotonic() + int(self.config["check_interval"])
        try:
            await self._process_due_jobs()
        except Exception:
            logger.exception("MessageScheduler tick failed")

    def _tz(self):
        timezone = str(self.config["timezone"])
        try:
            return ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"невідома таймзона: {timezone}") from error

    def _now(self):
        return datetime.datetime.now(self._tz())

    @staticmethod
    def _parse_iso(value):
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("datetime must include timezone")
        return parsed

    @staticmethod
    def _parse_time(value):
        try:
            return datetime.datetime.strptime(value, "%H:%M").time()
        except ValueError as error:
            raise ValueError("час має бути у форматі HH:MM") from error

    @classmethod
    def _parse_weekdays(cls, value):
        days = []
        for token in value.replace(";", ",").split(","):
            key = token.strip().casefold()
            if not key or key not in cls.WEEKDAYS:
                raise ValueError(
                    "дні: mon,tue,wed,thu,fri,sat,sun "
                    "або пн,вт,ср,чт,пт,сб,нд"
                )
            day = cls.WEEKDAYS[key]
            if day not in days:
                days.append(day)
        if not days:
            raise ValueError("вкажи хоча б один день тижня")
        return sorted(days)

    def _parse_once(self, date_value, time_value):
        try:
            naive = datetime.datetime.strptime(
                f"{date_value} {time_value}", "%Y-%m-%d %H:%M"
            )
        except ValueError as error:
            raise ValueError(
                "дата й час мають бути у форматі YYYY-MM-DD HH:MM"
            ) from error
        return naive.replace(tzinfo=self._tz())

    def _next_after(self, job, after):
        if job["mode"] == "once":
            return self._parse_iso(job["run_at"])

        schedule_time = self._parse_time(job["time"])
        local_after = after.astimezone(self._tz())
        allowed_days = (
            set(range(7))
            if job["mode"] == "daily"
            else set(job.get("weekdays", []))
        )
        for offset in range(8):
            day = local_after.date() + datetime.timedelta(days=offset)
            if day.weekday() not in allowed_days:
                continue
            candidate = datetime.datetime.combine(
                day, schedule_time, tzinfo=self._tz()
            )
            if candidate > local_after:
                return candidate
        raise ValueError("не вдалося визначити наступне надсилання")

    def _valid_stored_job(self, job):
        if not isinstance(job, dict):
            return False
        if not (
            isinstance(job.get("id"), str)
            and isinstance(job.get("peer"), int)
            and isinstance(job.get("text"), str)
            and 0 < len(job["text"]) <= self.MAX_TEXT_LENGTH
            and job.get("mode") in {"once", "daily", "weekly"}
        ):
            return False
        try:
            self._parse_iso(job["next_run"])
            if job["mode"] == "once":
                self._parse_iso(job["run_at"])
            else:
                self._parse_time(job["time"])
            if job["mode"] == "weekly":
                weekdays = job.get("weekdays")
                if (
                    not isinstance(weekdays, list)
                    or not weekdays
                    or any(
                        not isinstance(day, int) or day < 0 or day > 6
                        for day in weekdays
                    )
                ):
                    return False
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def _jobs(self):
        jobs = self.get("jobs", [])
        return copy.deepcopy(jobs) if isinstance(jobs, list) else []

    def _save(self, jobs):
        self.set("jobs", jobs)

    def _find_job(self, jobs, job_id):
        job_id = job_id.strip().casefold()
        exact = next(
            (
                job
                for job in jobs
                if job.get("id", "").casefold() == job_id
            ),
            None,
        )
        if exact:
            return exact
        matches = [
            job
            for job in jobs
            if job.get("id", "").casefold().startswith(job_id)
        ]
        return matches[0] if len(matches) == 1 else None

    async def _resolve_target(self, message, value):
        if value.casefold() in {"here", "тут"}:
            value = utils.get_chat_id(message)
        elif value.lstrip("-").isdigit():
            value = int(value)
        client = self._client or message.client
        entity = await client.get_entity(value)
        peer = int(await client.get_peer_id(entity))
        title = (
            telethon_utils.get_display_name(entity)
            or getattr(entity, "username", None)
        )
        username = getattr(entity, "username", None)
        return peer, str(title or peer), username

    def _schedule_label(self, job):
        if job["mode"] == "once":
            return (
                "разово · "
                + self._parse_iso(job["run_at"]).strftime("%Y-%m-%d %H:%M")
            )
        if job["mode"] == "daily":
            return f"щодня · {job['time']}"
        days = ",".join(
            self.WEEKDAY_LABELS[day] for day in job.get("weekdays", [])
        )
        return f"щотижня · {days} · {job['time']}"

    def _next_label(self, job):
        if not job.get("enabled", True):
            return "вимкнено"
        try:
            return self._parse_iso(job["next_run"]).strftime("%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            return "не визначено"

    @staticmethod
    def _preview(text, limit=90):
        compact = " ".join(text.split())
        return (
            compact
            if len(compact) <= limit
            else compact[: limit - 1].rstrip() + "…"
        )

    def _summary(self, job, detailed=False):
        state = "🟢" if job.get("enabled", True) else "⚪️"
        target = utils.escape_html(
            job.get("chat_name") or str(job["peer"])
        )
        schedule = utils.escape_html(self._schedule_label(job))
        next_run = utils.escape_html(self._next_label(job))
        preview = utils.escape_html(
            self._preview(job["text"], 180 if detailed else 78)
        )
        result = (
            f"{state} <code>{utils.escape_html(job['id'])}</code> · "
            f"<b>{target}</b>\n"
            f"🗓 {schedule}\n"
            f"⏭ <code>{next_run}</code>\n"
            f"💬 {preview}"
        )
        if detailed:
            last_run = job.get("last_run") or "ще не було"
            result += (
                f"\n🕘 Останнє: "
                f"<code>{utils.escape_html(last_run)}</code>"
            )
            if job.get("last_error"):
                result += (
                    f"\n⚠️ Помилок поспіль: "
                    f"<b>{int(job.get('failures', 0))}</b>"
                    f"\n<code>{utils.escape_html(job['last_error'])}</code>"
                )
        return result

    def _prefix(self):
        try:
            return self.get_prefix()
        except Exception:
            return "."

    def _help(self):
        prefix = utils.escape_html(self._prefix())
        timezone = utils.escape_html(str(self.config["timezone"]))
        return (
            "📨 <b>MessageScheduler</b>\n"
            f"Часова зона: <code>{timezone}</code>\n\n"
            "<b>Створення</b>\n"
            f"<code>{prefix}ms add @chat once "
            "2026-09-12 18:30 | Текст</code>\n"
            f"<code>{prefix}ms add тут daily "
            "09:00 | Доброго ранку!</code>\n"
            f"<code>{prefix}ms add @chat weekly "
            "пн,ср,пт 20:00 | Текст</code>\n\n"
            "<b>Керування</b>\n"
            f"<code>{prefix}ms list [сторінка]</code> · "
            f"<code>{prefix}ms info ID</code>\n"
            f"<code>{prefix}ms on ID</code> · "
            f"<code>{prefix}ms off ID</code>\n"
            f"<code>{prefix}ms run ID</code> · "
            f"<code>{prefix}ms del ID</code>\n\n"
            "<b>Редагування</b>\n"
            f"<code>{prefix}ms edit ID text | Новий текст</code>\n"
            f"<code>{prefix}ms edit ID time 14:30</code>\n"
            f"<code>{prefix}ms edit ID date "
            "2026-09-15 14:30</code>\n"
            f"<code>{prefix}ms edit ID days вт,чт</code>\n"
            f"<code>{prefix}ms edit ID chat @other_chat</code>\n\n"
            f"<code>{prefix}ms now</code> — поточний час; "
            f"<code>{prefix}ms clear confirm</code> — видалити все."
        )

    async def _answer_error(self, message, text):
        await utils.answer(
            message,
            self.strings("bad_args", message).format(
                utils.escape_html(text), utils.escape_html(self._prefix())
            ),
        )

    async def _add(self, message, raw):
        if "|" not in raw:
            raise ValueError("відокрем текст повідомлення символом |")
        schedule_part, text = raw.split("|", 1)
        text = text.strip()
        if not text:
            raise ValueError("текст повідомлення порожній")
        if len(text) > self.MAX_TEXT_LENGTH:
            raise ValueError("повідомлення довше за 4096 символів")

        tokens = schedule_part.split()
        if len(tokens) < 3:
            raise ValueError("не вистачає параметрів розкладу")
        target, mode_token = tokens[0], tokens[1].casefold()
        mode = self.MODES.get(mode_token)
        if not mode:
            raise ValueError("режим: once, daily або weekly")

        now = self._now()
        job = {
            "id": uuid.uuid4().hex[:8],
            "mode": mode,
            "text": text,
            "enabled": True,
            "created_at": now.isoformat(),
            "last_run": None,
            "last_error": None,
            "failures": 0,
            "retry_at": None,
        }
        if mode == "once":
            if len(tokens) != 4:
                raise ValueError("once: вкажи YYYY-MM-DD HH:MM")
            run_at = self._parse_once(tokens[2], tokens[3])
            if run_at <= now:
                raise ValueError(
                    "час одноразового надсилання вже минув"
                )
            job["run_at"] = run_at.isoformat()
            next_run = run_at
        elif mode == "daily":
            if len(tokens) != 3:
                raise ValueError("daily: вкажи час HH:MM")
            self._parse_time(tokens[2])
            job["time"] = tokens[2]
            next_run = self._next_after(job, now)
        else:
            if len(tokens) != 4:
                raise ValueError("weekly: вкажи дні та час HH:MM")
            job["weekdays"] = self._parse_weekdays(tokens[2])
            self._parse_time(tokens[3])
            job["time"] = tokens[3]
            next_run = self._next_after(job, now)

        peer, title, username = await self._resolve_target(message, target)
        job.update(
            {
                "peer": peer,
                "chat_name": title,
                "username": username,
                "next_run": next_run.isoformat(),
            }
        )
        async with self._lock:
            jobs = self._jobs()
            jobs.append(job)
            self._save(jobs)
        await utils.answer(
            message,
            self.strings("added", message).format(
                self._summary(job, True)
            ),
        )

    async def _list(self, message, raw):
        jobs = self._jobs()
        if not jobs:
            return await utils.answer(
                message,
                self.strings("empty", message).format(
                    utils.escape_html(self._prefix())
                ),
            )
        try:
            page = max(1, int(raw.strip() or "1"))
        except ValueError as error:
            raise ValueError(
                "номер сторінки має бути числом"
            ) from error
        pages = max(
            1, (len(jobs) + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        )
        page = min(page, pages)
        start = (page - 1) * self.PAGE_SIZE
        content = "\n\n".join(
            self._summary(job)
            for job in jobs[start : start + self.PAGE_SIZE]
        )
        timezone = utils.escape_html(str(self.config["timezone"]))
        await utils.answer(
            message,
            f"📋 <b>Заплановані повідомлення</b> · "
            f"{page}/{pages}\n"
            f"🌍 <code>{timezone}</code>\n\n{content}",
        )

    async def _info(self, message, raw):
        job = self._find_job(self._jobs(), raw)
        if not job:
            return await utils.answer(
                message,
                self.strings("not_found", message).format(
                    utils.escape_html(raw.strip())
                ),
            )
        await utils.answer(
            message,
            "📌 <b>Заплановане повідомлення</b>\n\n"
            + self._summary(job, True),
        )

    async def _toggle(self, message, raw, enabled):
        async with self._lock:
            jobs = self._jobs()
            job = self._find_job(jobs, raw)
            if not job:
                return await utils.answer(
                    message,
                    self.strings("not_found", message).format(
                        utils.escape_html(raw.strip())
                    ),
                )
            job["enabled"] = enabled
            job["failures"] = 0
            job["last_error"] = None
            job["retry_at"] = None
            if enabled:
                now = self._now()
                if job["mode"] == "once":
                    next_run = self._parse_iso(job["run_at"])
                    if next_run <= now:
                        raise ValueError(
                            "час цього одноразового завдання вже минув"
                        )
                else:
                    next_run = self._next_after(job, now)
                job["next_run"] = next_run.isoformat()
            self._save(jobs)
        if enabled:
            return await utils.answer(
                message,
                self.strings("enabled", message).format(
                    utils.escape_html(self._next_label(job))
                ),
            )
        await utils.answer(
            message,
            self.strings("disabled", message).format(
                utils.escape_html(job["id"])
            ),
        )

    async def _delete(self, message, raw):
        async with self._lock:
            jobs = self._jobs()
            job = self._find_job(jobs, raw)
            if not job:
                return await utils.answer(
                    message,
                    self.strings("not_found", message).format(
                        utils.escape_html(raw.strip())
                    ),
                )
            jobs.remove(job)
            self._save(jobs)
        await utils.answer(
            message,
            self.strings("removed", message).format(
                utils.escape_html(job["id"])
            ),
        )

    async def _edit(self, message, raw):
        head = raw.split(maxsplit=2)
        if len(head) < 3:
            raise ValueError(
                "формат: ms edit ID text|time|date|days|chat значення"
            )
        job_id, field, value = (
            head[0],
            head[1].casefold(),
            head[2].strip(),
        )
        if field == "text" and value.startswith("|"):
            value = value[1:].strip()
        if not value:
            raise ValueError("нове значення порожнє")

        async with self._lock:
            jobs = self._jobs()
            job = self._find_job(jobs, job_id)
            if not job:
                return await utils.answer(
                    message,
                    self.strings("not_found", message).format(
                        utils.escape_html(job_id)
                    ),
                )
            now = self._now()
            if field == "text":
                if len(value) > self.MAX_TEXT_LENGTH:
                    raise ValueError(
                        "повідомлення довше за 4096 символів"
                    )
                job["text"] = value
            elif field == "time":
                if job["mode"] == "once":
                    raise ValueError(
                        "для разового завдання використовуй поле date"
                    )
                self._parse_time(value)
                job["time"] = value
                job["next_run"] = self._next_after(
                    job, now
                ).isoformat()
            elif field == "date":
                if job["mode"] != "once":
                    raise ValueError(
                        "поле date доступне лише для разового завдання"
                    )
                parts = value.split()
                if len(parts) != 2:
                    raise ValueError(
                        "date: вкажи YYYY-MM-DD HH:MM"
                    )
                run_at = self._parse_once(parts[0], parts[1])
                if run_at <= now:
                    raise ValueError("новий час уже минув")
                job["run_at"] = run_at.isoformat()
                job["next_run"] = run_at.isoformat()
                job["enabled"] = True
            elif field == "days":
                if job["mode"] != "weekly":
                    raise ValueError(
                        "поле days доступне лише "
                        "для щотижневого завдання"
                    )
                job["weekdays"] = self._parse_weekdays(value)
                job["next_run"] = self._next_after(
                    job, now
                ).isoformat()
            elif field == "chat":
                peer, title, username = await self._resolve_target(
                    message, value
                )
                job.update(
                    {
                        "peer": peer,
                        "chat_name": title,
                        "username": username,
                    }
                )
            else:
                raise ValueError(
                    "поле редагування: text, time, date, days або chat"
                )
            job["failures"] = 0
            job["last_error"] = None
            job["retry_at"] = None
            self._save(jobs)
        await utils.answer(
            message,
            self.strings("updated", message).format(
                self._summary(job, True)
            ),
        )

    async def _send_job(self, job):
        await self._client.send_message(
            job["peer"],
            job["text"],
            parse_mode=None,
            link_preview=bool(self.config["link_preview"]),
        )

    async def _run_now(self, message, raw):
        job = self._find_job(self._jobs(), raw)
        if not job:
            return await utils.answer(
                message,
                self.strings("not_found", message).format(
                    utils.escape_html(raw.strip())
                ),
            )
        try:
            await self._send_job(job)
        except Exception as error:
            logger.warning(
                "MessageScheduler manual send failed: %s", error
            )
            return await utils.answer(
                message,
                self.strings("send_failed", message).format(
                    utils.escape_html(str(error))
                ),
            )
        await utils.answer(message, self.strings("sent", message))

    async def _clear(self, message, raw):
        if raw.strip().casefold() != "confirm":
            return await utils.answer(
                message,
                self.strings("clear_confirm", message).format(
                    utils.escape_html(self._prefix())
                ),
            )
        async with self._lock:
            self._save([])
        await utils.answer(message, self.strings("cleared", message))

    async def _process_due_jobs(self, now=None):
        now = now or self._now()
        async with self._lock:
            jobs = self._jobs()
            changed = False
            for job in jobs:
                if not job.get("enabled", True):
                    continue
                try:
                    next_run = self._parse_iso(job["next_run"])
                    retry_at = (
                        self._parse_iso(job["retry_at"])
                        if job.get("retry_at")
                        else None
                    )
                except (KeyError, TypeError, ValueError):
                    job["enabled"] = False
                    job["last_error"] = "пошкоджений розклад"
                    changed = True
                    continue
                if next_run > now or (retry_at and retry_at > now):
                    continue
                try:
                    following_run = (
                        None
                        if job["mode"] == "once"
                        else self._next_after(job, now)
                    )
                except (KeyError, TypeError, ValueError) as error:
                    job["enabled"] = False
                    job["last_error"] = (
                        f"пошкоджений розклад: {error}"[:500]
                    )
                    changed = True
                    continue
                try:
                    await self._send_job(job)
                except Exception as error:
                    job["failures"] = (
                        int(job.get("failures", 0)) + 1
                    )
                    job["last_error"] = (
                        str(error)[:500]
                        or error.__class__.__name__
                    )
                    if job["failures"] >= int(
                        self.config["max_failures"]
                    ):
                        job["enabled"] = False
                        job["retry_at"] = None
                    else:
                        job["retry_at"] = (
                            now
                            + datetime.timedelta(
                                seconds=int(
                                    self.config["retry_delay"]
                                )
                            )
                        ).isoformat()
                    logger.warning(
                        "MessageScheduler job %s failed: %s",
                        job.get("id"),
                        error,
                    )
                    changed = True
                    continue

                job["last_run"] = now.isoformat()
                job["last_error"] = None
                job["failures"] = 0
                job["retry_at"] = None
                if job["mode"] == "once":
                    job["enabled"] = False
                else:
                    job["next_run"] = following_run.isoformat()
                changed = True
            if changed:
                self._save(jobs)

    async def mscmd(self, message):
        """Керування запланованими повідомленнями: .ms help"""
        raw = utils.get_args_raw(message).strip()
        if not raw:
            return await utils.answer(message, self._help())
        action, _, rest = raw.partition(" ")
        action = action.casefold()
        aliases = {
            "додати": "add",
            "список": "list",
            "інфо": "info",
            "видалити": "del",
            "увімкнути": "on",
            "вимкнути": "off",
            "запустити": "run",
            "змінити": "edit",
            "очистити": "clear",
            "зараз": "now",
            "довідка": "help",
        }
        action = aliases.get(action, action)
        try:
            if action == "add":
                return await self._add(message, rest)
            if action == "list":
                return await self._list(message, rest)
            if action == "info":
                return await self._info(message, rest)
            if action in {"on", "off"}:
                return await self._toggle(
                    message, rest, action == "on"
                )
            if action in {"del", "delete", "remove"}:
                return await self._delete(message, rest)
            if action == "edit":
                return await self._edit(message, rest)
            if action == "run":
                return await self._run_now(message, rest)
            if action == "clear":
                return await self._clear(message, rest)
            if action == "now":
                now = self._now().strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )
                return await utils.answer(
                    message,
                    f"🕒 <b>Зараз:</b> "
                    f"<code>{utils.escape_html(now)}</code>",
                )
            if action in {"help", "?"}:
                return await utils.answer(message, self._help())
            raise ValueError("невідома дія")
        except Exception as error:
            logger.warning(
                "MessageScheduler command failed: %s", error
            )
            await self._answer_error(message, str(error))
