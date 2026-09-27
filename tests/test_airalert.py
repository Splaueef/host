"""Checks for AirAlert configuration migration and delivery."""

import asyncio
import datetime  # Load stdlib math before the repository's math.py can shadow it.
import html
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


def load_module():
    package = types.ModuleType("test_airalert_host")
    package.__path__ = []
    modules = types.ModuleType("test_airalert_host.modules")
    modules.__path__ = []
    loader = types.ModuleType("test_airalert_host.loader")
    utils = types.ModuleType("test_airalert_host.utils")
    inline = types.ModuleType("test_airalert_host.inline")
    loader.Module = type("Module", (), {
        "get": lambda self, key, default=None: getattr(self, "_storage", {}).get(key, default),
        "set": lambda self, key, value: self._storage.__setitem__(key, value),
    })
    loader.tds = lambda cls: cls
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = lambda *values: dict(values)
    loader.validators = types.SimpleNamespace(
        Boolean=lambda: None, String=lambda: None,
        Integer=lambda **kwargs: None, Series=lambda value: None,
    )
    utils.escape_html = html.escape
    utils.get_args_raw = lambda message: message.args
    utils.answer = mock.AsyncMock()
    inline.GeekInlineQuery = type("GeekInlineQuery", (), {})
    inline.rand = lambda size: "selection"

    aiogram = types.ModuleType("aiogram")
    aiogram_types = types.ModuleType("aiogram.types")
    aiogram_types.InlineQueryResultArticle = lambda **kwargs: types.SimpleNamespace(**kwargs)
    aiogram_types.InputTextMessageContent = lambda *args, **kwargs: None
    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    channels = types.ModuleType("telethon.tl.functions.channels")
    tl_types = types.ModuleType("telethon.tl.types")
    tl_utils = types.ModuleType("telethon.utils")
    channels.JoinChannelRequest = lambda channel: channel
    tl_types.Message = type("Message", (), {})
    tl_utils.get_display_name = lambda entity: entity.name
    tl_utils.get_peer_id = lambda entity: -1000000000000 - entity.id
    sys.modules.update({
        "test_airalert_host": package,
        "test_airalert_host.modules": modules,
        "test_airalert_host.loader": loader,
        "test_airalert_host.utils": utils,
        "test_airalert_host.inline": inline,
        "aiogram": aiogram,
        "aiogram.types": aiogram_types,
        "telethon": telethon,
        "telethon.tl": tl,
        "telethon.tl.functions": functions,
        "telethon.tl.functions.channels": channels,
        "telethon.tl.types": tl_types,
        "telethon.utils": tl_utils,
    })
    package.loader, package.utils = loader, utils
    spec = importlib.util.spec_from_file_location(
        "test_airalert_host.modules.airalert", pathlib.Path(__file__).parents[1] / "airalert.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, utils


airalert, utils = load_module()


class AirAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = airalert.AirAlertMod()
        self.module._storage = {}
        self.bot = types.SimpleNamespace(send_message=mock.AsyncMock(), get_me=mock.AsyncMock(
            return_value=types.SimpleNamespace(id=777)
        ))
        self.module.inline = types.SimpleNamespace(bot=self.bot, form=mock.AsyncMock())
        self.client = mock.AsyncMock()
        self.client.get_me.return_value = types.SimpleNamespace(id=123)
        self.client.get_entity.return_value = types.SimpleNamespace(id=321, name="Чат")
        self.db = mock.Mock()
        old_values = {
            "regions": ["Полтавська_область"], "forwards": [321], "nametag": "Підпис"
        }
        self.db.get.side_effect = lambda section, key, default: old_values.get(key, default)
        self.db.set.side_effect = lambda section, key, value: old_values.__setitem__(key, value)

    async def _ready(self):
        await self.module.client_ready(self.client, self.db)

    def _alert(self, ident=1, text="Полтавська_область <небезпека>"):
        return types.SimpleNamespace(
            peer_id=types.SimpleNamespace(channel_id=airalert.SOURCE_CHANNEL_ID),
            id=ident, raw_text=text, out=False, via_bot_id=None,
        )

    async def test_old_settings_migrate_once_and_existing_config_wins(self):
        self.module.config["regions"] = ["м_Київ"]
        await self._ready()
        self.assertEqual(self.module.config["regions"], ["м_Київ"])
        self.assertEqual(self.module.config["forward_chats"], ["321"])
        self.assertEqual(self.module.config["nametag"], "Підпис")
        self.db.get.reset_mock()
        await self._ready()
        self.db.get.assert_called_once_with("AirAlert", "config_migrated", False)

    async def test_single_escaped_pm_and_independent_forward_destinations(self):
        await self._ready()
        self.module.config["forward_chats"] = ["-1001", "-1002"]
        async def send(chat, text, **kwargs):
            if chat == -1001:
                raise RuntimeError("No permission")
        self.client.send_message.side_effect = send
        message = self._alert()
        with self.assertLogs(airalert.logger, level="WARNING"):
            await self.module.watcher(message)
        await self.module.watcher(message)
        self.bot.send_message.assert_awaited_once_with(
            123, "Полтавська_область &lt;небезпека&gt;", parse_mode="HTML"
        )
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.send_message.await_args_list[1].args[0], -1002)

    async def test_config_edits_take_effect_immediately(self):
        await self._ready()
        self.module.config["notify_pm"] = False
        self.module.config["regions"] = ["м_Київ"]
        await self.module.watcher(self._alert())
        self.client.send_message.assert_not_awaited()
        self.module.config["regions"] = ["all"]
        await self.module.watcher(self._alert(2))
        self.bot.send_message.assert_not_awaited()
        self.client.send_message.assert_awaited_once()

    async def test_inline_selection_updates_config(self):
        await self._ready()
        message = types.SimpleNamespace(
            out=True, via_bot_id=777,
            raw_text=airalert.REGION_EDIT_PREFIX + "м_Київ",
            peer_id=None,
        )
        await self.module.watcher(message)
        self.assertIn("м_Київ", self.module.config["regions"])
        await self.module.watcher(message)
        self.assertNotIn("м_Київ", self.module.config["regions"])

    async def test_region_name_matches_readable_source_text(self):
        await self._ready()
        await self.module.watcher(self._alert(text="Повітряна тривога: Полтавська область"))
        self.bot.send_message.assert_awaited_once()

    async def test_forward_command_removes_migrated_chat(self):
        await self._ready()
        await self.module.alertforwardcmd(types.SimpleNamespace(args="321"))
        self.assertEqual(self.module.config["forward_chats"], [])


if __name__ == "__main__":
    unittest.main()
