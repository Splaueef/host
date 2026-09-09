"""Regression tests for SystemInfo command registration and formatting."""

import html
import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    pass


def _command(*args, **kwargs):
    def decorator(value):
        value.is_command = True
        return value

    return decorator


def _load_module():
    package = types.ModuleType("infohost")
    package.__path__ = []
    modules = types.ModuleType("infohost.modules")
    modules.__path__ = []
    loader = types.ModuleType("infohost.loader")
    utils = types.ModuleType("infohost.utils")
    loader.Module = _Module
    loader.tds = lambda value: value
    loader.command = _command
    utils.escape_html = lambda value: html.escape(str(value), quote=False)
    utils.get_base_dir = lambda: str(pathlib.Path(__file__).parents[1])

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils
    telethon = types.ModuleType("telethon")
    telethon.__version__ = "test"
    sys.modules.update(
        {
            "infohost": package,
            "infohost.modules": modules,
            "infohost.loader": loader,
            "infohost.utils": utils,
            "telethon": telethon,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "sysinfo.py"
    spec = importlib.util.spec_from_file_location("infohost.modules.sysinfo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sysinfo = _load_module()


def _registered_commands(instance):
    return {
        (name.rsplit("cmd", 1)[0] if name.endswith("cmd") else name).lower()
        for name in dir(instance)
        if not isinstance(getattr(type(instance), name, None), property)
        and callable(getattr(instance, name))
        and (
            name.endswith("cmd")
            or getattr(getattr(instance, name), "is_command", False)
        )
    }


class SystemInfoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = sysinfo.InfoMod()

    def test_unique_command_does_not_override_builtin_info(self):
        commands = _registered_commands(self.module)

        self.assertEqual(commands, {"sysinfo"})
        self.assertNotIn("info", commands)
        self.assertNotIn("", commands)

    def test_human_formatters(self):
        self.assertEqual(self.module._human_bytes(1024**3), "1.0 ГіБ")
        self.assertEqual(self.module._duration(90060), "1 д 1 год 1 хв")

    async def test_report_contains_runtime_metrics(self):
        text = await self.module._report()

        self.assertIn("SystemInfo · Hikka", text)
        self.assertIn("<b>RAM:</b>", text)
        self.assertIn("<b>Диск:</b>", text)
        self.assertIn("<b>Telethon:</b> <code>test</code>", text)


if __name__ == "__main__":
    unittest.main()
