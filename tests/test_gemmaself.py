"""Tests for GemmaSelf output escaping, quotas and status."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


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
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = lambda *values: dict(values)
    utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.answer = mock.AsyncMock()
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
    })
    path = pathlib.Path(__file__).parents[1] / "gemmaself.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.gemmaself", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gemma = _load_module()


class GemmaSelfTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        gemma.utils.answer.reset_mock()
        self.module = gemma.GemmaSelf()

    def test_model_output_is_escaped_but_quota_notice_is_not(self):
        rendered = self.module._render('<a href="https://evil">click</a>')
        self.assertNotIn("<a", rendered)
        self.assertIn("&lt;a", rendered)

        notice = self.module._quota_message("2026-10-03 00:00 UTC")
        self.assertEqual(self.module._render(notice), notice)

    def test_quota_storage_keeps_only_today(self):
        self.module.config["daily_user_limit"] = 5
        self.module.set("quota", {"1": {"day": "2020-01-01", "count": 3}})

        self.module._quota_inc(2)
        self.module._quota_inc(2)

        self.assertEqual(set(self.module.get("quota")), {"2"})
        self.assertEqual(self.module._quota_used(2), 2)

    async def test_status_reports_current_chat(self):
        self.module.config["allowed_chats"] = [10]
        self.module.config["daily_user_limit"] = 3
        self.module._quota_inc(7)

        await self.module.gmstatus(types.SimpleNamespace(chat_id=10))

        rendered = gemma.utils.answer.await_args.args[1]
        self.assertIn("Поточний чат: <b>увімкнено</b>", rendered)
        self.assertIn("Запитів сьогодні (усі користувачі): <b>1</b>", rendered)


if __name__ == "__main__":
    unittest.main()
