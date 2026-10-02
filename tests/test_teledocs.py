"""Tests for TeleDocs search and failure handling."""

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
    inline = types.ModuleType("testhost.inline")
    inline.__path__ = []
    inline_types = types.ModuleType("testhost.inline.types")
    inline_types.InlineCall = object

    class Module:
        def get_prefix(self):
            return "."

    loader.Module = Module
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.inline_everyone = lambda value: value
    utils.get_args_raw = lambda message: message.args
    utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.answer = mock.AsyncMock()
    utils.run_sync = mock.AsyncMock()

    requests = types.ModuleType("requests")
    requests.get = mock.Mock()
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
        "requests": requests,
        "telethon": telethon,
        "telethon.tl": tl,
        "telethon.tl.types": tl_types,
    })
    path = pathlib.Path(__file__).parents[1] / "teledocs.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.teledocs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


teledocs = _load_module()

DOCS = {
    "requests": ["SendMessageRequest"],
    "requests_urls": ["methods/messages/send_message.html"],
    "requests_desc": [["Sends a message", "<b>peer</b>"]],
    "requests_ex": ["await client(SendMessageRequest(...))"],
    "types": [],
    "types_urls": [],
    "constructors": [],
    "constructors_urls": [],
    "constructors_desc": [],
}


class TeledocsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        teledocs.utils.answer.reset_mock()
        teledocs.utils.run_sync.reset_mock()
        teledocs.utils.run_sync.side_effect = None
        self.module = teledocs.TeledocsMod()

    def test_search_is_case_insensitive_and_handles_empty_input(self):
        self.module._tl = DOCS
        self.assertEqual(self.module.search("SendMessage")[0]["result"], "SendMessageRequest")
        self.assertEqual(self.module.search("   "), [])
        self.assertEqual(self.module._find("", "abc"), -1)

    async def test_failed_download_does_not_break_loading(self):
        teledocs.utils.run_sync.side_effect = OSError("offline")

        await self.module.client_ready(None, None)
        await self.module.tl(types.SimpleNamespace(args="send"))

        self.assertIn("не вдалося", teledocs.utils.answer.await_args.args[1])

    async def test_command_reports_usage_and_missing_results(self):
        self.module._tl = DOCS
        await self.module.tl(types.SimpleNamespace(args=""))
        self.assertIn(".tl", teledocs.utils.answer.await_args.args[1])

        await self.module.tl(types.SimpleNamespace(args="zzzz<x>"))
        self.assertIn("zzzz&lt;x&gt;", teledocs.utils.answer.await_args.args[1])

        await self.module.tl(types.SimpleNamespace(args="sendmessage"))
        self.assertIn("SendMessageRequest", teledocs.utils.answer.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
