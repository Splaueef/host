"""Regression tests for DailyStat incoming-message accounting."""

import importlib.util
import pathlib
import sys
import types
import unittest


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
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.ModuleConfig = lambda *args: {"top_count": 5}
    loader.ConfigValue = lambda *args, **kwargs: None
    loader.validators = types.SimpleNamespace(Integer=lambda **kwargs: None)
    utils.escape_html = lambda value: (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    package.loader = loader
    package.utils = utils
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "stats.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.stats", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stats = _load_module()


class _Message:
    def __init__(self, *, outgoing=False, private=False, sender=None):
        self.out = outgoing
        self.is_private = private
        self.chat_id = 100
        self.text = "hello"
        self.media = None
        self._sender = sender

    async def get_sender(self):
        return self._sender


class DailyStatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = stats.DailyStatMod()
        self.module._init_storage()

    async def test_private_user_and_bot_messages_are_counted_by_sender(self):
        alice = types.SimpleNamespace(id=1, first_name="Alice")
        bot = types.SimpleNamespace(id=2, first_name="Helper Bot", bot=True)

        await self.module.watcher(_Message(private=True, sender=alice))
        await self.module.watcher(_Message(private=True, sender=alice))
        await self.module.watcher(_Message(private=True, sender=bot))

        day = self.module._get_day(self.module._today_key())
        self.assertEqual(day["received"], 3)
        self.assertEqual(day["senders"]["1"]["count"], 2)
        self.assertEqual(day["senders"]["2"]["count"], 1)

    async def test_group_and_channel_messages_are_ignored(self):
        sender = types.SimpleNamespace(id=1, first_name="Alice")
        await self.module.watcher(_Message(private=False, sender=sender))

        day = self.module._get_day(self.module._today_key())
        self.assertEqual(day["received"], 0)
        self.assertEqual(day["senders"], {})

    def test_old_storage_is_migrated_and_sender_names_are_escaped(self):
        key = self.module._today_key()
        self.module.set(
            "stats",
            {key: {"sent": 4, "media": 0, "chats": {}, "hours": [0] * 24}},
        )
        day = self.module._get_day(key)
        self.assertEqual(day["received"], 0)
        self.assertEqual(day["senders"], {})

        day["senders"]["7"] = {"name": "<Alice>", "count": 2}
        rendered = self.module._format_senders(day, 5)
        self.assertIn("&lt;Alice&gt;", rendered)
        self.assertNotIn("<Alice>", rendered)


if __name__ == "__main__":
    unittest.main()
