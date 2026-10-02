"""Tests for QuietSchedule peer handling and argument validation."""

import datetime
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


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = _Module

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.loop = lambda *args, **kwargs: (lambda value: value)
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    loader.validators = types.SimpleNamespace(
        Integer=lambda **kwargs: None, Boolean=lambda: None
    )
    utils.get_args = lambda message: message.args
    utils.get_args_raw = lambda message: " ".join(message.args)
    utils.escape_html = lambda value: value
    utils.answer = mock.AsyncMock()
    telethon = types.ModuleType("telethon")
    telethon_utils = types.ModuleType("telethon.utils")
    telethon_utils.get_peer_id = lambda entity: entity.id
    errors = types.ModuleType("telethon.errors")
    errors.RPCError = type("RPCError", (Exception,), {})
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    account = types.ModuleType("telethon.tl.functions.account")
    account.UpdateNotifySettingsRequest = lambda *args, **kwargs: (args, kwargs)
    tl_types = types.ModuleType("telethon.tl.types")
    tl_types.InputNotifyPeer = lambda peer: peer
    tl_types.InputPeerNotifySettings = lambda **kwargs: kwargs
    telethon.utils = telethon_utils
    telethon.errors = errors
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "telethon": telethon,
        "telethon.utils": telethon_utils,
        "telethon.errors": errors,
        "telethon.tl": tl,
        "telethon.tl.functions": functions,
        "telethon.tl.functions.account": account,
        "telethon.tl.types": tl_types,
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
    })
    path = pathlib.Path(__file__).parents[1] / "quietschedule.py"
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.quietschedule", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


quiet = _load_module()


class QuietScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet.utils.answer.reset_mock()
        self.module = quiet.QuietScheduleMod()

    def test_legacy_link_peers_are_resolvable(self):
        ref = self.module._peer_ref
        self.assertEqual(ref("tg://user?id=123"), 123)
        self.assertEqual(ref("tg://resolve?domain=some_chat"), "some_chat")
        self.assertEqual(ref("-100500"), -100500)
        self.assertEqual(ref(42), 42)

    def test_weekdays_reject_unknown_names(self):
        self.assertEqual(self.module._weekdays("mon,wed,mon"), [0, 2])
        with self.assertRaises(ValueError):
            self.module._weekdays("mon,funday")
        with self.assertRaises(ValueError):
            self.module._weekdays(",")

    def test_invalid_timezone_falls_back_to_utc(self):
        for value in ("", "Not/AZone", "/etc/passwd"):
            self.module.config["timezone"] = value
            self.assertEqual(str(self.module._tz()), "UTC")

    async def test_add_rejects_invalid_daily_time_and_stores_numeric_peer(self):
        user = types.SimpleNamespace(id=77, first_name="Alice", last_name=None)
        client = types.SimpleNamespace(get_entity=mock.AsyncMock(return_value=user))
        self.module._client = None  # keep _process_jobs from touching Telegram

        bad = types.SimpleNamespace(client=client, args=["@a", "daily", "25:00", "08:00"])
        await self.module.qadd(bad)
        self.assertEqual(self.module.get("jobs", []), [])

        self.module._process_jobs = mock.AsyncMock()
        good = types.SimpleNamespace(client=client, args=["@a", "daily", "22:00", "08:00"])
        await self.module.qadd(good)
        (job,) = self.module.get("jobs")
        self.assertEqual(job["peer"], 77)
        self.assertEqual(job["title"], "Alice")

    def test_overnight_daily_window(self):
        job = {"type": "daily", "start_time": "22:00", "end_time": "08:00"}
        tz = datetime.timezone.utc
        late = datetime.datetime(2026, 10, 1, 23, 0, tzinfo=tz)
        noon = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=tz)
        self.assertEqual(self.module._active_now(job, late), (True, False))
        self.assertEqual(self.module._active_now(job, noon), (False, False))

    def test_duration_parser(self):
        parse = self.module._parse_duration
        self.assertEqual(parse("1h30m"), datetime.timedelta(minutes=90))
        self.assertEqual(parse("2d"), datetime.timedelta(days=2))
        for bad in ("abc", "10", "0m", "5x", "400d"):
            with self.assertRaises(ValueError):
                parse(bad)

    async def test_for_mode_creates_active_once_job(self):
        user = types.SimpleNamespace(id=5, first_name="Bob", last_name=None)
        client = types.SimpleNamespace(get_entity=mock.AsyncMock(return_value=user))
        self.module._client = None
        self.module.config.update({"default_mute": True, "default_archive": False})
        self.module._apply = mock.AsyncMock()

        await self.module.qadd(types.SimpleNamespace(client=client, args=["@b", "for", "2h"]))

        (job,) = self.module.get("jobs")
        self.assertEqual(job["type"], "once")
        start = datetime.datetime.fromisoformat(job["start"])
        end = datetime.datetime.fromisoformat(job["end"])
        self.assertEqual(end - start, datetime.timedelta(hours=2))
        self.module._apply.assert_awaited_once_with(job, True)

    async def test_paused_job_is_deactivated_and_resumed(self):
        job = {"id": "abc", "peer": 1, "type": "daily", "start_time": "00:00",
               "end_time": "23:59", "mute": True, "active": True}
        self.module.set("jobs", [job])

        async def apply(target, active):
            target["active"] = active

        self.module._apply = mock.AsyncMock(side_effect=apply)
        self.module._now = lambda: datetime.datetime(2026, 10, 1, 12, 0)

        await self.module.qpause(types.SimpleNamespace(args=["abc"]))
        self.assertTrue(job["paused"])
        self.assertFalse(job["active"])
        self.assertIn("paused", self.module._describe(job))

        await self.module.qresume(types.SimpleNamespace(args=["abc"]))
        self.assertFalse(job["paused"])
        self.assertTrue(job["active"])


if __name__ == "__main__":
    unittest.main()
