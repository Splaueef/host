"""Regression tests for QuietSchedule validation and mute deadlines."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REPO = str(pathlib.Path(__file__).parents[1])
sys.path[:] = [entry for entry in sys.path if entry not in ("", _REPO)]


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    loader.loop = lambda *args, **kwargs: (lambda value: value)
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")
    errors.RPCError = type("RPCError", (Exception,), {})
    account = types.ModuleType("telethon.tl.functions.account")
    account.UpdateNotifySettingsRequest = object
    tl_types = types.ModuleType("telethon.tl.types")
    tl_types.InputNotifyPeer = object
    tl_types.InputPeerNotifySettings = object
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.errors": errors,
            "telethon.tl": types.ModuleType("telethon.tl"),
            "telethon.tl.functions": types.ModuleType("telethon.tl.functions"),
            "telethon.tl.functions.account": account,
            "telethon.tl.types": tl_types,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "quietschedule.py"
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.quietschedule", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


quiet = _load_module()


class QuietScheduleTests(unittest.TestCase):
    def setUp(self):
        self.module = object.__new__(quiet.QuietScheduleMod)
        self.module.config = {
            "timezone": "Europe/Kyiv",
            "default_mute": True,
            "default_archive": False,
        }

    def test_unknown_timezone_is_not_silently_treated_as_utc(self):
        self.module.config["timezone"] = "Mars/Olympus"
        with self.assertRaises(ZoneInfoNotFoundError):
            self.module._tz()

    def test_invalid_weekday_and_action_are_rejected(self):
        with self.assertRaises(ValueError):
            self.module._weekdays("mon,funday")
        with self.assertRaises(ValueError):
            self.module._actions(["mute", "explode"])

    def test_equal_daily_times_are_rejected(self):
        with self.assertRaises(ValueError):
            self.module._validate_times("22:00", "22:00")

    def test_overnight_mute_uses_the_real_end_of_window(self):
        tz = ZoneInfo("Europe/Kyiv")
        job = {"type": "daily", "start_time": "22:00", "end_time": "08:00"}

        before_midnight = datetime.datetime(2026, 9, 1, 23, 0, tzinfo=tz)
        after_midnight = datetime.datetime(2026, 9, 2, 2, 0, tzinfo=tz)

        self.assertEqual(
            self.module._active_end(job, before_midnight),
            datetime.datetime(2026, 9, 2, 8, 0, tzinfo=tz),
        )
        self.assertEqual(
            self.module._active_end(job, after_midnight),
            datetime.datetime(2026, 9, 2, 8, 0, tzinfo=tz),
        )

    def test_recurring_mute_is_renewed_for_a_new_window(self):
        tz = ZoneInfo("Europe/Kyiv")
        now = datetime.datetime(2026, 9, 2, 23, 0, tzinfo=tz)
        job = {
            "type": "daily",
            "peer": "peer",
            "start_time": "22:00",
            "end_time": "08:00",
            "mute": True,
            "archive": False,
            "active": True,
            "active_until": "2026-09-02T08:00:00+03:00",
        }
        self.module.get = lambda key, default=None: [job]
        self.module.set = lambda key, value: None
        self.module._now = lambda: now
        self.module._apply = AsyncMock()

        import asyncio

        asyncio.run(self.module._process_jobs())

        self.module._apply.assert_awaited_once_with(job, True, now)


if __name__ == "__main__":
    unittest.main()
