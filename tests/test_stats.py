"""Regression tests for DailyStat incoming-message accounting."""

import datetime
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
    utils.get_args_raw = lambda message: ""
    utils.answer = mock.AsyncMock()
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
    def __init__(self, *, outgoing=False, private=False, sender=None, date=None,
                 text="hello", media=None):
        self.out = outgoing
        self.is_private = private
        self.chat_id = 100
        self.text = text
        self.media = media
        self.date = date
        self._sender = sender

    async def get_sender(self):
        return self._sender

    async def get_chat(self):
        return self._sender


async def _aiter(values):
    for value in values:
        yield value


class _HistoryClient:
    def __init__(self, dialogs, histories):
        self.dialogs = dialogs
        self.histories = histories

    def iter_dialogs(self):
        return _aiter(self.dialogs)

    def iter_messages(self, entity, **kwargs):
        return _aiter(self.histories.get(entity.id, []))


class DailyStatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = stats.DailyStatMod()
        self.module._init_storage()

    async def test_private_user_and_bot_messages_are_counted_by_sender(self):
        alice = types.SimpleNamespace(id=1, first_name="Alice", username="alice")
        bot = types.SimpleNamespace(id=2, first_name="Helper Bot", bot=True)

        await self.module.watcher(_Message(private=True, sender=alice))
        await self.module.watcher(_Message(private=True, sender=alice))
        await self.module.watcher(_Message(private=True, sender=bot))

        day = self.module._get_day(self.module._today_key())
        self.assertEqual(day["received"], 3)
        self.assertEqual(day["senders"]["1"]["count"], 2)
        self.assertEqual(day["senders"]["2"]["count"], 1)
        self.assertEqual(day["users"]["1"]["id"], 1)
        self.assertEqual(day["users"]["1"]["username"], "alice")

    async def test_group_and_channel_messages_are_ignored(self):
        sender = types.SimpleNamespace(id=1, first_name="Alice")
        await self.module.watcher(_Message(private=False, sender=sender))

        day = self.module._get_day(self.module._today_key())
        self.assertEqual(day["received"], 0)
        self.assertEqual(day["senders"], {})
        self.assertEqual(day["users"], {})

        await self.module.watcher(
            _Message(outgoing=True, private=False, sender=sender)
        )
        self.assertEqual(day["sent"], 0)

    async def test_private_outgoing_tracks_per_user_hour(self):
        alice = types.SimpleNamespace(id=1, first_name="Alice")
        with mock.patch.object(stats.datetime, "datetime", wraps=datetime.datetime) as dt:
            dt.now.return_value = datetime.datetime(2026, 8, 31, 14, 30)
            await self.module.watcher(
                _Message(outgoing=True, private=True, sender=alice, media=object())
            )

        day = self.module._get_day(self.module._today_key())
        self.assertEqual(day["sent"], 1)
        self.assertEqual(day["media"], 1)
        self.assertEqual(day["hours"][14], 1)
        self.assertEqual(day["users"]["100"]["sent_hours"][14], 1)

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

        day["users"]["7"] = {"name": "Alice"}
        migrated = self.module._get_day(key)["users"]["7"]
        self.assertEqual(migrated["id"], 7)
        self.assertIsNone(migrated["username"])
        self.assertEqual(len(migrated["sent_hours"]), 24)

    async def test_scan_rebuilds_today_from_private_dialogs_with_outgoing(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        alice = types.SimpleNamespace(id=1, first_name="Alice", username="alice_user")
        bob = types.SimpleNamespace(id=2, first_name="Bob")
        group = types.SimpleNamespace(id=3, title="Group")
        dialogs = [
            types.SimpleNamespace(is_user=True, entity=alice),
            types.SimpleNamespace(is_user=True, entity=bob),
            types.SimpleNamespace(is_user=False, entity=group),
        ]
        histories = {
            1: [
                _Message(outgoing=True, date=now.replace(hour=9), media=object()),
                _Message(outgoing=False, date=now.replace(hour=10)),
                _Message(outgoing=True, date=now.replace(hour=11), text=".help"),
            ],
            # Bob wrote to us, but we did not write to Bob today: skip the dialog.
            2: [_Message(outgoing=False, date=now.replace(hour=12))],
        }
        self.module._client = _HistoryClient(dialogs, histories)
        self.module.set("stats", {self.module._today_key(): {"sent": 99}})

        day, scanned = await self.module._scan_today()

        self.assertEqual(scanned, 1)
        self.assertEqual((day["sent"], day["received"], day["media"]), (1, 1, 1))
        self.assertEqual(day["users"]["1"]["sent_hours"][9], 1)
        self.assertEqual(day["users"]["1"]["received_hours"][10], 1)
        self.assertEqual(day["users"]["1"]["id"], 1)
        self.assertEqual(day["users"]["1"]["username"], "alice_user")
        self.assertIs(self.module._find_user(day, "@alice_user"), day["users"]["1"])
        self.assertNotIn("2", day["users"])

    async def test_scan_uses_dialog_input_entity_for_history(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        partial_user = types.SimpleNamespace(id=1, first_name=None, username=None)
        input_peer = types.SimpleNamespace(id=1, access_hash=123)
        dialog = types.SimpleNamespace(
            is_user=True, entity=partial_user, input_entity=input_peer
        )
        outgoing = _Message(outgoing=True, date=now.replace(hour=9))

        class InputPeerOnlyClient(_HistoryClient):
            def iter_messages(client_self, entity, **kwargs):
                if entity is not input_peer:
                    raise AttributeError("'NoneType' object has no attribute 'encode'")
                return _aiter([outgoing])

        self.module._client = InputPeerOnlyClient([dialog], {})

        day, scanned = await self.module._scan_today()

        self.assertEqual(scanned, 1)
        self.assertEqual(day["sent"], 1)
        self.assertEqual(day["chats"]["1"]["name"], "Unknown")

    def test_individual_and_all_user_peak_formats(self):
        day = self.module._get_day(self.module._today_key())
        self.module._add_sent(day, 7, "<Alice>", False, 8)
        self.module._add_received(day, 7, "<Alice>", 21)

        user = self.module._find_user(day, "alice")
        rendered = self.module._format_user_peak(user)
        self.assertIn("&lt;Alice&gt;", rendered)
        self.assertIn("08:00", rendered)
        self.assertIn("21:00", rendered)

    async def test_scan_updates_message_returned_by_progress_answer(self):
        original = object()
        status = object()
        stats.utils.answer.reset_mock()
        stats.utils.answer.side_effect = [status, None]
        self.module._scan_today = mock.AsyncMock(
            return_value=(self.module._get_day("__empty__"), 0)
        )

        await self.module._ds_scan(original)

        self.assertEqual(stats.utils.answer.await_args_list[0].args[0], original)
        self.assertEqual(stats.utils.answer.await_args_list[1].args[0], status)

    async def test_scan_displays_escaped_error_on_returned_message(self):
        original = object()
        status = object()
        stats.utils.answer.reset_mock()
        stats.utils.answer.side_effect = [status, None]
        self.module._scan_today = mock.AsyncMock(
            side_effect=RuntimeError("bad <dialog>")
        )

        await self.module._ds_scan(original)

        target, text = stats.utils.answer.await_args_list[1].args
        self.assertIs(target, status)
        self.assertIn("bad &lt;dialog&gt;", text)


if __name__ == "__main__":
    unittest.main()
