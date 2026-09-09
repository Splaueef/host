"""Regression tests for non-interactive and resilient systemd helpers."""

import asyncio
import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from unittest.mock import AsyncMock

_REPO = pathlib.Path(__file__).parents[1]
sys.path[:] = [entry for entry in sys.path if entry not in ("", str(_REPO))]


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    inline = types.ModuleType("testhost.inline")
    inline.__path__ = []
    inline_types = types.ModuleType("testhost.inline.types")
    inline_types.InlineCall = object
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    utils.escape_html = (
        lambda value: str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    utils.chunks = lambda values, size: [
        values[index:index + size] for index in range(0, len(values), size)
    ]
    package.loader = loader
    package.utils = utils

    telethon_types = types.ModuleType("telethon.tl.types")
    telethon_types.Message = object
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "testhost.inline": inline,
            "testhost.inline.types": inline_types,
            "telethon": types.ModuleType("telethon"),
            "telethon.tl": types.ModuleType("telethon.tl"),
            "telethon.tl.types": telethon_types,
        }
    )
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.systemd", _REPO / "systemd.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


systemd = _load_module()


class SystemdTests(unittest.TestCase):
    def setUp(self):
        self.module = object.__new__(systemd.SystemdMod)

    def test_sudo_is_non_interactive(self):
        self.assertEqual(
            self.module._sudo("systemctl", "start", "demo.service"),
            ["sudo", "-n", "systemctl", "start", "demo.service"],
        )

    def test_status_emoji_handles_known_and_unknown_states(self):
        self.assertEqual(self.module._status_emoji("active"), "🍏")
        self.assertEqual(self.module._status_emoji("failed"), "🚫")
        self.assertEqual(self.module._status_emoji("mystery"), "❓")

    def test_resource_parser_handles_headerless_ps_output(self):
        self.module._is_running = lambda unit: True
        self.module._get_unit_pid = lambda unit: "42"
        outputs = iter(("2048\n", "12.5\n"))
        self.module._run_command = lambda *args, **kwargs: types.SimpleNamespace(
            stdout=next(outputs)
        )

        result = self.module._get_unit_resources_consumption("demo.service")

        self.assertIn("2.00 M", result)
        self.assertIn("12.5%", result)

    def test_failed_command_is_reported_without_hanging(self):
        self.module._manage_unit_impl = AsyncMock(
            side_effect=subprocess.CalledProcessError(
                1, ["sudo", "-n", "systemctl"], stderr="permission denied"
            )
        )

        result = asyncio.run(
            self.module._manage_unit(
                123,
                {"formal": "demo.service", "name": "Demo"},
                "start",
            )
        )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
