"""Tests for the Systemd unit manager."""

import asyncio
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
    inline = types.ModuleType("testhost.inline")
    inline.__path__ = []
    inline_types = types.ModuleType("testhost.inline.types")
    inline_types.InlineCall = object

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.Module = _Module
    loader.tds = tds
    utils.get_args_raw = lambda message: message.args
    utils.get_chat_id = lambda message: 1
    utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.chunks = lambda items, size: [items[i:i + size] for i in range(0, len(items), size)]
    utils.answer = mock.AsyncMock()
    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    tl_types = types.ModuleType("telethon.tl.types")
    tl_types.Message = object
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "testhost.inline": inline,
        "testhost.inline.types": inline_types,
        "telethon": telethon,
        "telethon.tl": tl,
        "telethon.tl.types": tl_types,
    })
    path = pathlib.Path(__file__).parents[1] / "systemd.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.systemd", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


systemd = _load_module()


class SystemdTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        systemd.utils.answer.reset_mock()
        self.module = systemd.SystemdMod()

    def test_unit_names_are_validated(self):
        valid = self.module._valid_unit
        self.assertTrue(valid("nginx.service"))
        self.assertTrue(valid("getty@tty1.service"))
        self.assertFalse(valid("--help"))
        self.assertFalse(valid("a b"))
        self.assertFalse(valid(""))

    async def test_run_never_waits_for_a_password_and_times_out(self):
        self.module.COMMAND_TIMEOUT = 0.2
        with mock.patch.object(
            systemd.asyncio, "create_subprocess_exec", wraps=asyncio.create_subprocess_exec
        ) as spawn:
            code, _, _ = await self.module._run("sleep", "5", sudo=False)
            self.assertEqual(code, 124)
            await self.module._run("true")
        self.assertEqual(spawn.call_args.args[:2], ("sudo", "-n"))
        self.assertIs(spawn.call_args.kwargs["stdin"], asyncio.subprocess.DEVNULL)

    async def test_panel_escapes_names_and_shows_resources(self):
        self.module.set("services", [{"name": "<web>", "formal": "nginx.service"}])

        async def fake_run(*command, sudo=True):
            if command[:2] == ("systemctl", "is-active"):
                return 0, "active", ""
            if command[:2] == ("systemctl", "show"):
                return 0, "42", ""
            return 0, "2048 1.5", ""

        self.module._run = fake_run
        panel = await self.module._get_panel()

        self.assertIn("&lt;web&gt;", panel)
        self.assertIn("2.00 M", panel)
        self.assertIn("1.5%", panel)

    async def test_failed_action_is_reported(self):
        self.module._run = mock.AsyncMock(
            side_effect=[(0, "", ""), (1, "", "sudo: a password is required")]
        )
        await self.module.unitcmd(types.SimpleNamespace(args="nginx restart"))
        self.assertIn("password is required", systemd.utils.answer.await_args.args[1])

    async def test_rename_keeps_unit_position(self):
        self.module.set(
            "services",
            [{"name": "A", "formal": "a.service"}, {"name": "B", "formal": "b.service"}],
        )
        await self.module.nameunitcmd(types.SimpleNamespace(args="a.service Alpha"))
        self.assertEqual(
            [unit["name"] for unit in self.module.get("services")], ["Alpha", "B"]
        )


if __name__ == "__main__":
    unittest.main()
