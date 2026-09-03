"""Tests for the TimeInfo Hikka module."""

import datetime  # Load stdlib math before the repository's math.py can shadow it.
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    utils.get_args_raw = lambda message: message.args
    utils.escape_html = lambda value: value
    utils.answer = mock.AsyncMock()
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
    })
    path = pathlib.Path(__file__).parents[1] / "timeinfo.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.timeinfo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


timeinfo = _load_module()


class TimeInfoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        timeinfo.utils.answer.reset_mock()
        self.module = timeinfo.TimeInfoMod()

    def test_duration_and_offset_formatting(self):
        self.assertEqual(timeinfo._format_duration(90061), "1 дн. 1 год. 1 хв. 1 с.")
        kyiv = timeinfo.ZoneInfo("Europe/Kyiv")
        winter = timeinfo.datetime.datetime(2026, 1, 1, tzinfo=kyiv)
        self.assertEqual(timeinfo._format_offset(winter), "UTC+02:00")

    async def test_timezone_command_changes_configuration(self):
        message = types.SimpleNamespace(args="America/New_York")
        await self.module.timezonecmd(message)
        self.assertEqual(self.module.config["timezone"], "America/New_York")
        self.assertIn("змінено", timeinfo.utils.answer.await_args.args[1])

    async def test_timestamp_converts_naive_iso_in_hikka_timezone(self):
        message = types.SimpleNamespace(args="2026-09-03 12:00:00")
        await self.module.timestampcmd(message)
        rendered = timeinfo.utils.answer.await_args.args[1]
        self.assertIn("2026-09-03 12:00:00.000 EEST", rendered)
        self.assertIn("2026-09-03 09:00:00.000 UTC", rendered)
        self.assertIn("1788426000.000", rendered)

    async def test_invalid_timezone_is_reported_without_changing_config(self):
        message = types.SimpleNamespace(args="Mars/Olympus")
        await self.module.timezonecmd(message)
        self.assertEqual(self.module.config["timezone"], "Europe/Kyiv")
        self.assertIn("Невідома таймзона", timeinfo.utils.answer.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
