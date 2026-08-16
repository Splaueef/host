"""Regression tests for VideoDownloader's cookie/session helpers."""

import importlib.util
import os
import pathlib
import sqlite3
import sys

_REPO = pathlib.Path(__file__).parents[1]
sys.path[:] = [entry for entry in sys.path if entry not in ("", str(_REPO))]

import tempfile
import types
import unittest


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.watcher = lambda *args, **kwargs: (lambda value: value)
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_types = types.ModuleType("telethon.tl.types")
    telethon_messages = types.ModuleType("telethon.tl.functions.messages")
    telethon_types.InputMessagesFilterMusic = object
    telethon_messages.CheckChatInviteRequest = object
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "telethon": telethon,
        "telethon.tl": telethon_tl,
        "telethon.tl.types": telethon_types,
        "telethon.tl.functions": types.ModuleType("telethon.tl.functions"),
        "telethon.tl.functions.messages": telethon_messages,
    })
    spec = importlib.util.spec_from_file_location("testhost.modules.vdlt", _REPO / "vdlt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vdlt = _load_module()


class CookieManagerTests(unittest.TestCase):
    def test_file_for_requires_matching_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cookies.txt")
            pathlib.Path(path).write_text(
                "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tvalue\n",
                encoding="utf-8",
            )
            manager = vdlt.CookieManager(path, directory)
            self.assertEqual(manager.file_for("https://instagram.com/reel/1"), path)
            self.assertIsNone(manager.file_for("https://youtube.com/watch?v=1"))

    def test_firefox_profile_checks_cookie_database_and_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "cookies.sqlite")
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE moz_cookies (host TEXT)")
            connection.execute("INSERT INTO moz_cookies VALUES (?)", (".youtube.com",))
            connection.commit()
            connection.close()

            manager = vdlt.CookieManager("", directory)
            self.assertTrue(manager.firefox_profile_valid())
            self.assertTrue(manager.firefox_has_url("https://www.youtube.com/watch?v=1"))
            self.assertFalse(manager.firefox_has_url("https://instagram.com/reel/1"))

    def test_help_version_has_single_source_of_truth(self):
        self.assertEqual(vdlt.VERSION, ".".join(map(str, vdlt.__version__)))
        self.assertIn(f"VideoDownloader v{vdlt.VERSION}", vdlt.VideoDownloaderMod.strings["help_text"])


if __name__ == "__main__":
    unittest.main()
