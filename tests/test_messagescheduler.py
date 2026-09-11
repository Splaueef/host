"""Tests for the MessageScheduler Hikka module."""

import datetime
import html
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value

    def get_prefix(self):
        return "."


class _Validators:
    Boolean = staticmethod(lambda: object())
    Integer = staticmethod(lambda **kwargs: object())


class _RPCError(Exception):
    pass


def _load_module():
    package = types.ModuleType("schedulerhost")
    package.__path__ = []
    modules = types.ModuleType("schedulerhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("schedulerhost.loader")
    utils = types.ModuleType("schedulerhost.utils")
    loader.Module = _Module
    loader.validators = _Validators

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.loop = lambda **kwargs: lambda function: function
    loader.ConfigValue = (
        lambda name, default, *args, **kwargs: (name, default)
    )
    loader.ModuleConfig = _Config
    utils.escape_html = lambda value: html.escape(
        str(value), quote=False
    )
    utils.get_args_raw = lambda message: message.raw
    utils.get_chat_id = lambda message: message.chat_id

    async def answer(message, text):
        message.answers.append(text)
        return text

    utils.answer = answer
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    telethon_utils = types.ModuleType("telethon.utils")
    errors = types.ModuleType("telethon.errors")
    telethon_utils.get_display_name = lambda entity: entity.title
    errors.RPCError = _RPCError
    telethon.utils = telethon_utils
    sys.modules.update(
        {
            "schedulerhost": package,
            "schedulerhost.modules": modules,
            "schedulerhost.loader": loader,
            "schedulerhost.utils": utils,
            "telethon": telethon,
            "telethon.utils": telethon_utils,
            "telethon.errors": errors,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "messagescheduler.py"
    spec = importlib.util.spec_from_file_location(
        "schedulerhost.modules.messagescheduler", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


messagescheduler = _load_module()


class _Client:
    def __init__(self):
        self.entities = {
            "@team": types.SimpleNamespace(
                peer=-10042, title="Team <Main>", username="team"
            ),
            "@other": types.SimpleNamespace(
                peer=-10099, title="Other", username="other"
            ),
            -100777: types.SimpleNamespace(
                peer=-100777, title="Current chat", username=None
            ),
        }
        self.sent = []
        self.error = None

    async def get_entity(self, value):
        if value not in self.entities:
            raise ValueError("unknown chat")
        return self.entities[value]

    async def get_peer_id(self, entity):
        return entity.peer

    async def send_message(self, peer, text, **kwargs):
        if self.error:
            raise self.error
        self.sent.append((peer, text, kwargs))
        return types.SimpleNamespace(id=len(self.sent))


class _Message:
    def __init__(self, raw="", client=None, chat_id=-100777):
        self.raw = raw
        self.client = client
        self.chat_id = chat_id
        self.answers = []


class MessageSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = _Client()
        self.module = messagescheduler.MessageSchedulerMod()
        self.module._client = self.client
        self.now = datetime.datetime(
            2026,
            9,
            11,
            10,
            0,
            tzinfo=messagescheduler.ZoneInfo("Europe/Kyiv"),
        )
        self.now_patch = mock.patch.object(
            self.module, "_now", return_value=self.now
        )
        self.now_patch.start()

    def tearDown(self):
        self.now_patch.stop()

    async def test_adds_multiple_daily_jobs_for_different_chats_and_times(self):
        first = _Message(client=self.client)
        second = _Message(client=self.client)

        await self.module._add(
            first,
            "@team daily 12:30 | Перше повідомлення",
        )
        await self.module._add(
            second,
            "тут щодня 18:45 | Друге повідомлення",
        )

        jobs = self.module.get("jobs")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["peer"], -10042)
        self.assertEqual(jobs[0]["time"], "12:30")
        self.assertEqual(jobs[1]["peer"], -100777)
        self.assertEqual(jobs[1]["time"], "18:45")
        self.assertNotEqual(jobs[0]["id"], jobs[1]["id"])
        self.assertIn("Team &lt;Main&gt;", first.answers[-1])

    async def test_adds_weekly_job_with_ukrainian_days(self):
        await self.module._add(
            _Message(client=self.client),
            "@team weekly пн,ср,пт 09:15 | Нагадування",
        )

        job = self.module.get("jobs")[0]
        self.assertEqual(job["weekdays"], [0, 2, 4])
        self.assertEqual(
            job["next_run"], "2026-09-14T09:15:00+03:00"
        )

    async def test_rejects_once_job_in_the_past(self):
        with self.assertRaisesRegex(ValueError, "вже минув"):
            await self.module._add(
                _Message(client=self.client),
                (
                    "@team once 2026-09-10 18:00 "
                    "| Запізніле повідомлення"
                ),
            )

    async def test_due_daily_job_is_sent_as_plain_text_and_rescheduled(self):
        job = self._job(
            mode="daily",
            time="09:00",
            next_run="2026-09-11T09:00:00+03:00",
            text="<b>Не форматувати</b>",
        )
        self.module.set("jobs", [job])

        await self.module._process_due_jobs(self.now)

        self.assertEqual(
            self.client.sent[0][0:2],
            (-10042, "<b>Не форматувати</b>"),
        )
        self.assertEqual(
            self.client.sent[0][2],
            {"parse_mode": None, "link_preview": True},
        )
        stored = self.module.get("jobs")[0]
        self.assertEqual(
            stored["next_run"], "2026-09-12T09:00:00+03:00"
        )
        self.assertEqual(stored["failures"], 0)
        self.assertTrue(stored["enabled"])

    async def test_due_once_job_is_disabled_after_send(self):
        job = self._job(
            mode="once",
            run_at="2026-09-11T09:30:00+03:00",
            next_run="2026-09-11T09:30:00+03:00",
        )
        self.module.set("jobs", [job])

        await self.module._process_due_jobs(self.now)

        self.assertEqual(len(self.client.sent), 1)
        self.assertFalse(self.module.get("jobs")[0]["enabled"])

    async def test_failed_job_waits_before_retry_and_disables_at_limit(self):
        self.module.config["max_failures"] = 2
        self.module.config["retry_delay"] = 300
        self.client.error = RuntimeError("network down")
        job = self._job(
            mode="daily",
            time="09:00",
            next_run="2026-09-11T09:00:00+03:00",
        )
        self.module.set("jobs", [job])

        await self.module._process_due_jobs(self.now)
        stored = self.module.get("jobs")[0]
        self.assertEqual(stored["failures"], 1)
        self.assertEqual(
            stored["retry_at"], "2026-09-11T10:05:00+03:00"
        )

        await self.module._process_due_jobs(
            self.now + datetime.timedelta(minutes=1)
        )
        self.assertEqual(
            self.module.get("jobs")[0]["failures"], 1
        )

        await self.module._process_due_jobs(
            self.now + datetime.timedelta(minutes=5)
        )
        stored = self.module.get("jobs")[0]
        self.assertEqual(stored["failures"], 2)
        self.assertFalse(stored["enabled"])
        self.assertIsNone(stored["retry_at"])

    async def test_edit_updates_text_time_and_chat(self):
        job = self._job(
            mode="daily",
            time="09:00",
            next_run="2026-09-12T09:00:00+03:00",
        )
        self.module.set("jobs", [job])
        message = _Message(client=self.client)

        await self.module._edit(
            message, f"{job['id']} text | Новий | текст"
        )
        await self.module._edit(
            message, f"{job['id']} time 14:30"
        )
        await self.module._edit(
            message, f"{job['id']} chat @other"
        )

        stored = self.module.get("jobs")[0]
        self.assertEqual(stored["text"], "Новий | текст")
        self.assertEqual(stored["time"], "14:30")
        self.assertEqual(stored["peer"], -10099)
        self.assertEqual(stored["chat_name"], "Other")

    async def test_list_is_paginated_and_escapes_message_text(self):
        jobs = []
        for index in range(7):
            jobs.append(
                self._job(
                    job_id=f"job{index}",
                    mode="daily",
                    time="12:00",
                    next_run="2026-09-11T12:00:00+03:00",
                    text="<script>alert(1)</script>",
                )
            )
        self.module.set("jobs", jobs)
        message = _Message(client=self.client)

        await self.module._list(message, "2")

        self.assertIn("2/2", message.answers[-1])
        self.assertIn("job6", message.answers[-1])
        self.assertNotIn("job0", message.answers[-1])
        self.assertIn("&lt;script&gt;", message.answers[-1])

    async def test_manual_run_does_not_change_schedule(self):
        job = self._job(
            mode="daily",
            time="18:00",
            next_run="2026-09-11T18:00:00+03:00",
        )
        self.module.set("jobs", [job])
        message = _Message(client=self.client)

        await self.module._run_now(message, job["id"][:4])

        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(
            self.module.get("jobs")[0]["next_run"],
            "2026-09-11T18:00:00+03:00",
        )
        self.assertIn("надіслано", message.answers[-1])

    async def test_clear_requires_explicit_confirmation(self):
        self.module.set("jobs", [self._job()])
        message = _Message(client=self.client)

        await self.module._clear(message, "")
        self.assertEqual(len(self.module.get("jobs")), 1)
        await self.module._clear(message, "confirm")
        self.assertEqual(self.module.get("jobs"), [])

    async def test_client_ready_drops_invalid_storage_entries(self):
        valid = self._job()
        self.module.set(
            "jobs", [valid, "broken", {"id": "bad"}]
        )

        await self.module.client_ready(self.client, None)

        self.assertEqual(self.module.get("jobs"), [valid])

    async def test_command_without_arguments_shows_help(self):
        message = _Message(raw="", client=self.client)

        await self.module.mscmd(message)

        self.assertIn("MessageScheduler", message.answers[-1])
        self.assertIn(".ms add", message.answers[-1])

    async def test_invalid_timezone_is_reported_to_user(self):
        self.module.config["timezone"] = "Not/A_Timezone"
        message = _Message(
            raw="add @team daily 12:00 | Тест",
            client=self.client,
        )

        await self.module.mscmd(message)

        self.assertIn("невідома таймзона", message.answers[-1])
        self.assertIn("Not/A_Timezone", message.answers[-1])

    @staticmethod
    def _job(**overrides):
        job = {
            "id": "abcd1234",
            "peer": -10042,
            "chat_name": "Team",
            "username": "team",
            "mode": "daily",
            "time": "12:00",
            "text": "Тест",
            "enabled": True,
            "created_at": "2026-09-10T10:00:00+03:00",
            "last_run": None,
            "last_error": None,
            "failures": 0,
            "retry_at": None,
            "next_run": "2026-09-11T12:00:00+03:00",
        }
        if "job_id" in overrides:
            overrides["id"] = overrides.pop("job_id")
        job.update(overrides)
        return job


if __name__ == "__main__":
    unittest.main()
