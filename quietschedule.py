# meta developer: @Huang_Baike
# meta version: 1.1.0
# meta description: Планувальник тиші: вимикає сповіщення та/або архівує чати за розкладом.

import datetime
import logging
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon.errors import RPCError
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class QuietScheduleMod(loader.Module):
    """Вимикає звук і/або архівує користувачів/чати за точним розкладом"""

    strings = {
        "name": "QuietSchedule",
        "cfg_timezone": "IANA-таймзона для розкладів, наприклад Europe/Kyiv або UTC",
        "cfg_check_interval": "Як часто перевіряти розклади, у секундах",
        "cfg_default_mute": "Типова дія: вимикати сповіщення",
        "cfg_default_archive": "Типова дія: кидати чат в архів",
        "bad_tz": "❌ <b>Невідома таймзона:</b> <code>{}</code>",
        "now": "🕒 <b>Зараз:</b> <code>{}</code>\n🌍 <b>Таймзона:</b> <code>{}</code>",
        "help": (
            "<b>QuietSchedule</b>\n\n"
            "<code>.qnow</code> — точна дата й час.\n"
            "<code>.qadd @user 2026-07-11 22:00 2026-07-12 08:00 mute archive</code> — разово.\n"
            "<code>.qadd @user daily 22:00 08:00 mute archive</code> — щодня.\n"
            "<code>.qadd @user weekly mon,wed,fri 22:00 08:00 mute</code> — щотижня.\n"
            "<code>.qlist</code> — список.\n"
            "<code>.qdel id</code> — видалити.\n\n"
            "Дії можна не вказувати — будуть використані значення з конфігу."
        ),
        "added": "✅ <b>Розклад додано.</b>\n<code>{}</code>",
        "removed": "✅ <b>Розклад видалено:</b> <code>{}</code>",
        "not_found": "❌ <b>Розклад не знайдено:</b> <code>{}</code>",
        "empty": "📭 <b>Розкладів немає.</b>",
        "list_header": "📋 <b>QuietSchedule:</b>\n\n{}",
        "bad_args": "❌ <b>Не можу розібрати аргументи.</b>\n\n{}",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("timezone", "Europe/Kyiv", lambda: self.strings("cfg_timezone")),
            loader.ConfigValue("check_interval", 30, lambda: self.strings("cfg_check_interval"), validator=loader.validators.Integer(minimum=5, maximum=3600)),
            loader.ConfigValue("default_mute", True, lambda: self.strings("cfg_default_mute"), validator=loader.validators.Boolean()),
            loader.ConfigValue("default_archive", True, lambda: self.strings("cfg_default_archive"), validator=loader.validators.Boolean()),
        )
        self._client = None
        self._next_tick = 0

    async def client_ready(self, client, db):
        self._client = client
        if self.get("jobs") is None:
            self.set("jobs", [])

    @loader.loop(interval=1, autostart=True)
    async def scheduler(self):
        if not self._client or time.time() < self._next_tick:
            return
        self._next_tick = time.time() + int(self.config["check_interval"])
        try:
            await self._process_jobs()
        except ZoneInfoNotFoundError:
            # Executing a Kyiv schedule as UTC is much worse than waiting for
            # the owner to fix a typo in the configured timezone.
            logger.error("QuietSchedule: unknown timezone %s", self.config["timezone"])

    def _tz(self):
        return ZoneInfo(str(self.config["timezone"]).strip())

    def _now(self):
        return datetime.datetime.now(self._tz()).replace(second=0, microsecond=0)

    @staticmethod
    def _parse_time(value):
        return datetime.datetime.strptime(value, "%H:%M").time()

    @staticmethod
    def _parse_dt(date_value, time_value, tz):
        naive = datetime.datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=tz)

    @staticmethod
    def _weekdays(value):
        names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        values = [item.strip().lower() for item in value.split(",") if item.strip()]
        if not values or any(item not in names for item in values):
            raise ValueError("weekdays: mon,tue,wed,thu,fri,sat,sun")
        return sorted({names[item] for item in values})

    def _actions(self, tokens):
        acts = {"mute": self.config["default_mute"], "archive": self.config["default_archive"]}
        if tokens:
            normalized = {str(token).strip().lower() for token in tokens}
            unknown = normalized - {"mute", "archive"}
            if unknown:
                raise ValueError("unknown action: " + ", ".join(sorted(unknown)))
            acts = {"mute": "mute" in normalized, "archive": "archive" in normalized}
        if not any(acts.values()):
            raise ValueError("select mute and/or archive")
        return acts

    def _validate_times(self, start_value, end_value):
        start = self._parse_time(start_value)
        end = self._parse_time(end_value)
        if start == end:
            raise ValueError("start and end times must differ")

    async def _entity_id(self, message, token):
        entity = await message.client.get_entity(token)
        return utils.get_entity_url(entity) or str(getattr(entity, "id", token)), entity

    def _describe(self, job):
        actions = ", ".join(k for k in ("mute", "archive") if job.get(k)) or "нічого"
        if job["type"] == "once":
            period = f"{job['start']} → {job['end']}"
        elif job["type"] == "daily":
            period = f"daily {job['start_time']} → {job['end_time']}"
        else:
            days = ",".join(["mon", "tue", "wed", "thu", "fri", "sat", "sun"][d] for d in job["weekdays"])
            period = f"weekly {days} {job['start_time']} → {job['end_time']}"
        return f"{job['id']} | {job['peer']} | {period} | {actions} | active={job.get('active', False)}"

    def _active_now(self, job, now):
        if job["type"] == "once":
            start = datetime.datetime.fromisoformat(job["start"])
            end = datetime.datetime.fromisoformat(job["end"])
            return start <= now < end, now >= end
        start_t = self._parse_time(job["start_time"])
        end_t = self._parse_time(job["end_time"])
        if job["type"] == "daily":
            days = set(range(7))
        else:
            days = set(job["weekdays"])
        current = now.weekday() in days and now.time() >= start_t
        if end_t <= start_t:
            previous_day = (now.weekday() - 1) % 7
            current = current or (previous_day in days and now.time() < end_t)
        else:
            current = current and now.time() < end_t
        return current, False

    def _active_end(self, job, now):
        if job["type"] == "once":
            return datetime.datetime.fromisoformat(job["end"])

        start_t = self._parse_time(job["start_time"])
        end_t = self._parse_time(job["end_time"])
        end_date = now.date()
        if end_t <= start_t and now.time() >= start_t:
            end_date += datetime.timedelta(days=1)
        return datetime.datetime.combine(end_date, end_t, tzinfo=now.tzinfo)

    async def _set_mute(self, peer, mute, until=None):
        entity = await self._client.get_entity(peer)
        until = until.astimezone(datetime.timezone.utc) if mute and until else None
        await self._client(UpdateNotifySettingsRequest(InputNotifyPeer(entity), InputPeerNotifySettings(mute_until=until)))

    async def _set_archive(self, peer, archive):
        entity = await self._client.get_entity(peer)
        await self._client.edit_folder(entity, folder=1 if archive else 0)

    async def _apply(self, job, active, now=None):
        if job.get("mute"):
            until = self._active_end(job, now or self._now()) if active else None
            await self._set_mute(job["peer"], active, until)
            if until:
                job["active_until"] = until.isoformat()
            else:
                job.pop("active_until", None)
        if job.get("archive"):
            await self._set_archive(job["peer"], active)
        job["active"] = active

    async def _process_jobs(self):
        jobs = self.get("jobs", [])
        now = self._now()
        changed = False
        for job in list(jobs):
            try:
                active, expired = self._active_now(job, now)
                expected_until = (
                    self._active_end(job, now).isoformat()
                    if active and job.get("mute")
                    else None
                )
                if (
                    active != job.get("active", False)
                    or (
                        active
                        and job.get("mute")
                        and job.get("active_until") != expected_until
                    )
                ):
                    await self._apply(job, active, now)
                    changed = True
                if expired and not active:
                    jobs.remove(job)
                    changed = True
            except (KeyError, ValueError, ZoneInfoNotFoundError, RPCError, TypeError) as e:
                logger.warning("QuietSchedule job failed: %s", e)
        if changed:
            self.set("jobs", jobs)

    async def qnowcmd(self, message):
        """Показати точну дату та час у налаштованій таймзоні"""
        try:
            now = datetime.datetime.now(ZoneInfo(self.config["timezone"])).strftime("%Y-%m-%d %H:%M:%S %Z")
        except ZoneInfoNotFoundError:
            return await utils.answer(message, self.strings("bad_tz", message).format(utils.escape_html(self.config["timezone"])))
        await utils.answer(message, self.strings("now", message).format(now, utils.escape_html(self.config["timezone"])))

    async def qhelpcmd(self, message):
        """Довідка QuietSchedule"""
        await utils.answer(message, self.strings("help", message))

    async def qaddcmd(self, message):
        """Додати розклад: .qadd @user ..."""
        args = utils.get_args(message)
        if len(args) < 2:
            return await utils.answer(message, self.strings("bad_args", message).format(self.strings("help", message)))
        try:
            peer, entity = await self._entity_id(message, args[0])
            tz = self._tz()
            mode = args[1].lower()
            job = {"id": uuid.uuid4().hex[:8], "peer": peer, "active": False}
            if mode == "daily":
                if len(args) < 4:
                    raise ValueError("daily requires start and end time")
                self._validate_times(args[2], args[3])
                job.update({"type": "daily", "start_time": args[2], "end_time": args[3], **self._actions(args[4:])})
            elif mode == "weekly":
                if len(args) < 5:
                    raise ValueError("weekly requires weekdays, start and end time")
                self._validate_times(args[3], args[4])
                job.update({"type": "weekly", "weekdays": self._weekdays(args[2]), "start_time": args[3], "end_time": args[4], **self._actions(args[5:])})
            else:
                if len(args) < 5:
                    raise ValueError("one-time schedule requires start and end date/time")
                start = self._parse_dt(args[1], args[2], tz)
                end = self._parse_dt(args[3], args[4], tz)
                if end <= start:
                    raise ValueError("end <= start")
                job.update({"type": "once", "start": start.isoformat(), "end": end.isoformat(), **self._actions(args[5:])})
            # Validate the target can be resolved now; this also warms Telethon cache.
            await message.client.get_entity(entity)
            jobs = self.get("jobs", [])
            jobs.append(job)
            self.set("jobs", jobs)
            await self._process_jobs()
            await utils.answer(message, self.strings("added", message).format(utils.escape_html(self._describe(job))))
        except (IndexError, ValueError, ZoneInfoNotFoundError, RPCError) as e:
            await utils.answer(message, self.strings("bad_args", message).format(utils.escape_html(str(e)) + "\n\n" + self.strings("help", message)))

    async def qlistcmd(self, message):
        """Показати всі розклади"""
        jobs = self.get("jobs", [])
        if not jobs:
            return await utils.answer(message, self.strings("empty", message))
        text = "\n".join(f"<code>{utils.escape_html(self._describe(job))}</code>" for job in jobs)
        await utils.answer(message, self.strings("list_header", message).format(text))

    async def qdelcmd(self, message):
        """Видалити розклад за id"""
        job_id = utils.get_args_raw(message).strip()
        jobs = self.get("jobs", [])
        for job in list(jobs):
            if job["id"] == job_id:
                if job.get("active"):
                    await self._apply(job, False)
                jobs.remove(job)
                self.set("jobs", jobs)
                return await utils.answer(message, self.strings("removed", message).format(utils.escape_html(job_id)))
        await utils.answer(message, self.strings("not_found", message).format(utils.escape_html(job_id)))
