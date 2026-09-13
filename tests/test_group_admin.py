"""Focused tests for the GroupAdmin moderation module."""

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


class _ConfigValue:
    def __init__(self, key, default, doc, validator=None):
        self.key = key
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.key, value.default) for value in values)


def _decorator(*args, **kwargs):
    def decorate(function):
        function.is_command = True
        return function

    return decorate


class _Request:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _ChatBannedRights:
    _fields = (
        "view_messages",
        "send_messages",
        "send_media",
        "send_stickers",
        "send_gifs",
        "send_games",
        "send_inline",
        "embed_links",
        "send_polls",
        "send_plain",
        "send_photos",
        "send_videos",
        "send_roundvideos",
        "send_audios",
        "send_voices",
        "send_docs",
    )

    def __init__(self, until_date=None, **kwargs):
        self.until_date = until_date
        for field in self._fields:
            setattr(self, field, kwargs.get(field))


def _load_module():
    package = types.ModuleType("adminhost")
    package.__path__ = []
    modules = types.ModuleType("adminhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("adminhost.loader")
    utils = types.ModuleType("adminhost.utils")
    loader.Module = _Module
    loader.ConfigValue = _ConfigValue
    loader.ModuleConfig = _ModuleConfig
    loader.tds = lambda value: value
    loader.command = _decorator
    loader.validators = types.SimpleNamespace(
        Integer=lambda **kwargs: object(),
        Choice=lambda *args, **kwargs: object(),
    )
    utils.get_chat_id = lambda message: message.chat_id
    utils.get_args_raw = lambda message: getattr(message, "args", "")

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    channels = types.ModuleType("telethon.tl.functions.channels")
    messages_api = types.ModuleType("telethon.tl.functions.messages")
    tl_types = types.ModuleType("telethon.tl.types")
    errors.RPCError = type("RPCError", (Exception,), {})
    channels.EditBannedRequest = type("EditBannedRequest", (_Request,), {})
    channels.ToggleSlowModeRequest = type("ToggleSlowModeRequest", (_Request,), {})
    messages_api.EditChatDefaultBannedRightsRequest = type(
        "EditChatDefaultBannedRightsRequest", (_Request,), {}
    )
    tl_types.ChatBannedRights = _ChatBannedRights
    sys.modules.update(
        {
            "adminhost": package,
            "adminhost.modules": modules,
            "adminhost.loader": loader,
            "adminhost.utils": utils,
            "telethon": telethon,
            "telethon.errors": errors,
            "telethon.tl": tl,
            "telethon.tl.functions": functions,
            "telethon.tl.functions.channels": channels,
            "telethon.tl.functions.messages": messages_api,
            "telethon.tl.types": tl_types,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "group_admin.py"
    spec = importlib.util.spec_from_file_location("adminhost.modules.group_admin", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


group_admin = _load_module()


class _Client:
    def __init__(self):
        self.requests = []
        self.chat = types.SimpleNamespace(id=-100, title="Test chat", slowmode_seconds=0)
        self.user = types.SimpleNamespace(id=42, first_name="Yana", last_name=None, username="yana")

    async def __call__(self, request):
        self.requests.append(request)

    async def get_entity(self, value):
        return self.chat if int(value) == -100 else self.user

    async def get_permissions(self, chat, user):
        return types.SimpleNamespace(is_creator=False, is_admin=False, ban_users=True)


class GroupAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = group_admin.GroupAdminMod()
        self.module._client = _Client()
        self.module._me_id = 1

    def test_duration_parser_accepts_supported_units_and_rejects_extremes(self):
        self.assertEqual(self.module._parse_duration("10m"), 600)
        self.assertEqual(self.module._parse_duration("2h"), 7200)
        self.assertEqual(self.module._parse_duration("1w"), 604800)
        self.assertEqual(self.module._parse_duration("forever"), 0)
        self.assertIsNone(self.module._parse_duration("5s"))
        self.assertIsNone(self.module._parse_duration("400d"))

    def test_commands_use_g_prefix_to_avoid_core_moderation_collisions(self):
        commands = {
            name
            for name in dir(self.module)
            if callable(getattr(self.module, name, None))
            and getattr(getattr(self.module, name), "is_command", False)
        }
        self.assertIn("gadmin", commands)
        self.assertIn("gban", commands)
        self.assertIn("gmute", commands)
        self.assertNotIn("ban", commands)
        self.assertNotIn("mute", commands)

    async def test_warn_limit_applies_configured_action_and_resets_counter(self):
        self.module.config["warn_limit"] = 2
        user = self.module._client.user

        first = await self.module._apply_action(-100, user, "warn", reason="one")
        second = await self.module._apply_action(-100, user, "warn", reason="two")

        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 0)
        self.assertEqual(second["auto_action"], "mute")
        self.assertEqual(self.module._warn_count(-100, user.id), 0)
        rights = self.module._client.requests[-1].args[2]
        self.assertTrue(rights.send_messages)
        self.assertIsNotNone(rights.until_date)

    async def test_default_permission_update_preserves_one_rights_object(self):
        rights = _ChatBannedRights(until_date=None)
        self.module._client.chat.default_banned_rights = rights
        token = self.module._new_session(-100)

        class _Call:
            def __init__(self):
                self.answers = []
                self.edits = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

            async def edit(self, text, reply_markup=None):
                self.edits.append((text, reply_markup))

        call = _Call()
        await self.module._set_default_permission(call, token, "media", True)

        self.assertTrue(rights.send_media)
        self.assertTrue(rights.send_photos)
        self.assertIs(self.module._client.requests[-1].args[1], rights)


if __name__ == "__main__":
    unittest.main()
