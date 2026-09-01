"""Regression tests for Teledocs search and empty-query handling."""

import importlib.util
import pathlib
import sys
import types
import unittest

_REPO = str(pathlib.Path(__file__).parents[1])
sys.path[:] = [entry for entry in sys.path if entry not in ("", _REPO)]

import asyncio


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
    loader.inline_everyone = lambda value: value
    utils.escape_html = lambda value: str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
    path = pathlib.Path(__file__).parents[1] / "teledocs.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.teledocs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


teledocs = _load_module()


class TeledocsTests(unittest.TestCase):
    def setUp(self):
        self.module = object.__new__(teledocs.TeledocsMod)
        self.module._tl = {
            "requests": ["GetHistoryRequest"],
            "requests_urls": ["methods/messages/get_history.html"],
            "requests_desc": [["Read history", ""]],
            "requests_ex": ["await client(GetHistoryRequest(...))"],
            "types": ["User"],
            "types_urls": ["types/user.html"],
            "constructors": ["InputPeerUser"],
            "constructors_urls": ["constructors/input_peer_user.html"],
            "constructors_desc": [["Input user", ""]],
        }

    def test_empty_or_punctuation_only_query_has_no_matches(self):
        self.assertEqual(self.module.search(""), [])
        self.assertEqual(self.module.search("---"), [])

    def test_search_is_case_insensitive(self):
        results = self.module.search("GETHISTORY")
        self.assertEqual(results[0]["result"], "GetHistoryRequest")

    def test_subsequence_search_remains_bounded(self):
        self.assertGreaterEqual(self.module._find("GetHistoryRequest", "ghr"), 0)
        self.assertEqual(self.module._find("GetHistoryRequest", "xyz"), -1)

    def test_command_reports_no_result_instead_of_index_error(self):
        answers = []
        teledocs.utils.get_args_raw = lambda message: "NoSuchType"

        async def answer(message, text):
            answers.append(text)

        teledocs.utils.answer = answer
        self.module.strings = lambda key, *args: teledocs.TeledocsMod.strings[key]

        asyncio.run(self.module.tlcmd(object()))

        self.assertEqual(answers, [teledocs.TeledocsMod.strings["not_found"]])


if __name__ == "__main__":
    unittest.main()
