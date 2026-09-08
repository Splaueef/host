"""Tests for contact online interval tracking and overlap reporting."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        self._storage = getattr(self, "_storage", {})
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
    loader.raw_handler = lambda *args: lambda value: value
    loader.command = lambda *args, **kwargs: lambda value: value
    utils.escape_html = lambda value: value.replace("<", "&lt;").replace(">", "&gt;")

    async def answer(*args, **kwargs):
        return None

    utils.answer = answer
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    contacts = types.ModuleType("telethon.tl.functions.contacts")
    types_mod = types.ModuleType("telethon.tl.types")
    contacts.GetContactsRequest = lambda hash: types.SimpleNamespace(hash=hash)
    types_mod.UpdateUserStatus = type("UpdateUserStatus", (), {})
    types_mod.UserStatusOnline = type("UserStatusOnline", (), {})
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.tl": tl,
            "telethon.tl.functions": functions,
            "telethon.tl.functions.contacts": contacts,
            "telethon.tl.types": types_mod,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "contactstatus.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.contactstatus", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


contactstatus = _load_module()
UTC = datetime.timezone.utc


class ContactStatusTests(unittest.TestCase):
    def setUp(self):
        self.module = contactstatus.ContactStatusMod()
        self.module._ensure_storage()
        self.module.set(
            "contacts",
            {
                "1": {"name": "<Alice>", "username": "alice"},
                "2": {"name": "Bob", "username": "bob"},
            },
        )

    def test_sessions_are_closed_and_split_at_midnight(self):
        start = datetime.datetime(2026, 9, 7, 23, 59, tzinfo=UTC)
        end = datetime.datetime(2026, 9, 8, 0, 1, tzinfo=UTC)
        self.module._set_online(1, True, start)
        self.module._set_online(1, False, end)

        days = self.module.get("days")
        self.assertEqual(len(days["2026-09-07"]["1"]), 1)
        self.assertEqual(len(days["2026-09-08"]["1"]), 1)
        self.assertEqual(
            days["2026-09-07"]["1"][0][1],
            end.replace(minute=0).timestamp(),
        )

    def test_report_includes_each_user_period_and_shared_online(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(1, day.replace(hour=8), day.replace(hour=10))
        self.module._store_interval(2, day.replace(hour=9), day.replace(hour=11))

        report = self.module._report(day.replace(hour=12))

        self.assertIn("&lt;Alice&gt;", report)
        self.assertIn("08:00:00–10:00:00", report)
        self.assertIn("Спільний online:</b> 01:00:00", report)
        self.assertIn("09:00:00–10:00:00", report)

    def test_open_session_is_counted_without_being_closed(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._set_online(1, True, day.replace(hour=7))
        intervals = self.module._today_intervals(day.replace(hour=8))

        self.assertEqual(
            intervals["1"],
            [[day.replace(hour=7).timestamp(), day.replace(hour=8).timestamp()]],
        )
        self.assertIn("1", self.module.get("active"))


if __name__ == "__main__":
    unittest.main()
