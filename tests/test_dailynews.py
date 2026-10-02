"""Tests for DailyNews scheduling, source management and failure backoff."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


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
        Integer=lambda **kwargs: None,
        String=lambda **kwargs: None,
        Hidden=lambda value: value,
    )
    utils.get_args_raw = lambda message: message.args
    utils.answer = mock.AsyncMock()

    telethon = types.ModuleType("telethon")
    telethon_utils = types.ModuleType("telethon.utils")
    errors = types.ModuleType("telethon.errors")
    errors.RPCError = type("RPCError", (Exception,), {})
    extensions = types.ModuleType("telethon.extensions")
    extensions.markdown = types.SimpleNamespace(parse=lambda text: (text, []))
    telethon.utils = telethon_utils
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "telethon": telethon,
        "telethon.utils": telethon_utils,
        "telethon.errors": errors,
        "telethon.extensions": extensions,
    })
    path = pathlib.Path(__file__).parents[1] / "dailynews.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.dailynews", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dailynews = _load_module()


def _configured(module):
    module.config.update({
        "sources": "@one,@two",
        "target": "@target",
        "api_key": "key",
        "agent_id": "agent",
        "bot_token": "token",
        "publish_time": "08:00",
        "publish_time_2": "20:00",
        "timezone": "Europe/Kyiv",
    })


class DailyNewsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dailynews.utils.answer.reset_mock()
        self.module = dailynews.DailyNewsMod()

    def test_parse_sources_accepts_mixed_separators_and_post_links(self):
        parsed = self.module._parse_sources(
            "@one, https://t.me/second_channel/123\n@one;  @three."
        )
        self.assertEqual(parsed, ["@one", "@second_channel", "@three"])

    def test_invalid_timezone_is_reported_as_missing_config(self):
        _configured(self.module)
        for value in ("", "/etc/passwd", "Mars/Base"):
            self.module.config["timezone"] = value
            self.assertIn("timezone", self.module._missing_config())

    def test_second_slot_window_starts_at_first_slot(self):
        _configured(self.module)
        zone = ZoneInfo("Europe/Kyiv")
        now = datetime.datetime(2026, 10, 1, 20, 5, tzinfo=zone)
        since, until = self.module._slot_window(now, datetime.time(20, 0))
        self.assertEqual(since.astimezone(zone).hour, 8)
        self.assertEqual(until, now.astimezone(datetime.timezone.utc))

    async def test_failed_scheduled_run_backs_off(self):
        _configured(self.module)
        self.module._client = object()
        self.module.config["publish_time"] = "00:00"
        self.module.config["publish_time_2"] = "00:01"
        self.module._run_digest = mock.AsyncMock(side_effect=RuntimeError("down"))

        await self.module.news_scheduler()
        await self.module.news_scheduler()

        self.module._run_digest.assert_awaited_once()
        self.assertGreater(self.module._retry_after, 0)

    async def test_status_survives_invalid_publish_time(self):
        self.module.config["publish_time"] = "25:99"
        await self.module.newsstatus(types.SimpleNamespace(args=""))
        self.assertIn("некоректний", dailynews.utils.answer.await_args.args[1])

    async def test_list_and_delete_sources_by_name_and_number(self):
        self.module.config["sources"] = "@one,@Two,@three"

        await self.module.newslist(types.SimpleNamespace(args=""))
        listed = dailynews.utils.answer.await_args.args[1]
        self.assertIn("2. <code>@Two</code>", listed)

        await self.module.newsdel(types.SimpleNamespace(args="two 3"))
        self.assertEqual(self.module.config["sources"], "@one")

    async def test_scheduled_edition_skips_already_published_posts(self):
        _configured(self.module)
        item = {"source_channel_id": 1, "message_id": 5, "published_at": "x"}
        self.module._remember_edition("2026-10-01", "08:00", [item])
        self.module._collect_news = mock.AsyncMock(return_value=[item])
        self.module._ask_agent = mock.AsyncMock()

        count = await self.module._run_digest("2026-10-01", slot_name="20:00")

        self.assertEqual(count, 0)
        self.module._ask_agent.assert_not_awaited()
        self.assertEqual(
            self.module.get("completed_slots"), {"2026-10-01": ["08:00", "20:00"]}
        )


if __name__ == "__main__":
    unittest.main()
