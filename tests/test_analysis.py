"""Tests for the ChatAnalysis report builder."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = lambda *values: dict(values)
    loader.validators = types.SimpleNamespace(Integer=lambda **kwargs: None)
    utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.answer = mock.AsyncMock()

    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    tl_types = types.ModuleType("telethon.tl.types")
    tl_types.PeerUser = type("PeerUser", (), {})
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "telethon": telethon,
        "telethon.tl": tl,
        "telethon.tl.types": tl_types,
    })
    path = pathlib.Path(__file__).parents[1] / "analysis.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.analysis", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analysis = _load_module()


def _msg(sender_id, name, day, hour=12, out=False, media=None):
    return types.SimpleNamespace(
        sender_id=sender_id,
        sender=types.SimpleNamespace(first_name=name, last_name=None, username="secret"),
        date=datetime.datetime(2026, 9, day, hour, tzinfo=datetime.timezone.utc),
        message="text",
        media=media,
        action=None,
        out=out,
    )


class ChatAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.module = analysis.ChatAnalysisMod()
        self.module._me = types.SimpleNamespace(id=1)

    def _report(self, messages):
        stats = self.module._new_stats()
        for msg in messages:
            side = "me" if msg.out else "others"
            self.module._add_message(stats[side], msg)
            self.module._add_message(stats["all"], msg)
            self.module._add_sender(stats, msg)
        return self.module._format_report(stats, "Chat", "група")

    def test_group_report_lists_participants_without_links(self):
        messages = [
            _msg(1, "Me", 1, out=True),
            _msg(2, "<Alice>", 1),
            _msg(2, "<Alice>", 3),
            _msg(3, "Bob", 3),
        ]
        report = self._report(messages)

        self.assertIn("Найактивніші учасники", report)
        self.assertIn("1. &lt;Alice&gt; — <b>2</b>", report)
        self.assertIn("Я — <b>1</b>", report)
        self.assertNotIn("secret", report)
        self.assertNotIn("<a ", report)
        self.assertIn("активних днів: 2 з 3", report)
        self.assertIn("Найактивніший день", report)

    def test_private_chat_skips_participants_and_uses_ukrainian_weekdays(self):
        report = self._report([_msg(1, "Me", 7, out=True), _msg(2, "Alice", 7)])

        self.assertNotIn("Найактивніші учасники", report)
        self.assertIn("понеділок", report)


if __name__ == "__main__":
    unittest.main()
