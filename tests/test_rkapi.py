"""Regression tests for rkapi's pure formatting and validation helpers."""

import sys

# This repository intentionally has a userbot module named ``math.py``.  Keep
# it from shadowing Python's standard-library ``math`` while the test runtime
# imports asyncio/aiohttp.
_REPO = __file__.rsplit("/tests/", 1)[0]
sys.path[:] = [entry for entry in sys.path if entry not in ("", _REPO)]

import importlib.util
import pathlib
import types
import unittest

try:
    import aiohttp  # noqa: F401
except ImportError:
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = OSError
    aiohttp.ClientSession = object
    aiohttp.ClientTimeout = object
    sys.modules["aiohttp"] = aiohttp


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
    loader.ModuleConfig = lambda *args: dict()
    loader.ConfigValue = lambda *args, **kwargs: None
    loader.validators = types.SimpleNamespace(
        String=lambda: None,
        Integer=lambda **kwargs: None,
        Boolean=lambda: None,
    )
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
    })

    path = pathlib.Path(__file__).parents[1] / "rkapi.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.rkapi", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rkapi = _load_module()


class YaAliasTests(unittest.IsolatedAsyncioTestCase):
    def test_alias_matches_only_bare_ya(self):
        for text in ("йа", "Йа", "ЙА", " йа ", "йа!", "Йа)"):
            self.assertTrue(rkapi._is_ya_alias(text), text)
        for text in ("я", ".я", "йа тут", "майа", "ya", "", None):
            self.assertFalse(rkapi._is_ya_alias(text), text)

    def _mod(self, **cfg):
        mod = rkapi.WerwolfStatsMod.__new__(rkapi.WerwolfStatsMod)
        mod.config = {"ya_alias": True, **cfg}
        mod.calls = []

        async def fake_ya(message):
            mod.calls.append(message)

        mod.я = fake_ya
        return mod

    async def test_watcher_runs_ya_for_own_message(self):
        mod = self._mod()
        msg = types.SimpleNamespace(out=True, raw_text="Йа", fwd_from=None, via_bot_id=None)
        await mod.ya_watcher(msg)
        self.assertEqual(mod.calls, [msg])

    async def test_watcher_ignores_others_forwards_and_disabled(self):
        incoming = types.SimpleNamespace(out=False, raw_text="йа", fwd_from=None, via_bot_id=None)
        forwarded = types.SimpleNamespace(out=True, raw_text="йа", fwd_from=object(), via_bot_id=None)
        for mod, msg in (
            (self._mod(), incoming),
            (self._mod(), forwarded),
            (self._mod(ya_alias=False), types.SimpleNamespace(out=True, raw_text="йа")),
        ):
            await mod.ya_watcher(msg)
            self.assertEqual(mod.calls, [])


class HelperTests(unittest.IsolatedAsyncioTestCase):
    def test_escape_does_not_preserve_api_html(self):
        self.assertEqual(
            rkapi._esc('<a href="tg://user?id=1">admin</a>'),
            "&lt;a href=&quot;tg://user?id=1&quot;&gt;admin&lt;/a&gt;",
        )

    def test_bar_is_clamped(self):
        self.assertEqual(rkapi._bar(-10, 100, 4), "░░░░")
        self.assertEqual(rkapi._bar(200, 100, 4), "████")
        self.assertEqual(len(rkapi._bar(1, 0, 0)), 1)

    def test_number_helpers_reject_non_finite_values(self):
        self.assertEqual(rkapi._n("nan", 7), 7)
        self.assertEqual(rkapi._f("inf", 2.5), 2.5)

    def test_json_object_rejects_arrays_and_invalid_json(self):
        self.assertEqual(rkapi._json_object('{"name": "Луна"}'), {"name": "Луна"})
        with self.assertRaises(ValueError):
            rkapi._json_object("[]")
        with self.assertRaises(ValueError):
            rkapi._json_object("not json")

    def test_redact_recurses_without_mutating_payload(self):
        payload = {"token": "secret", "user": {"password": "pw", "id": 1}, "items": [{"api_key": "key"}]}
        redacted = rkapi._redact(payload)
        self.assertEqual(redacted["token"], "***")
        self.assertEqual(redacted["user"]["password"], "***")
        self.assertEqual(redacted["items"][0]["api_key"], "***")
        self.assertEqual(payload["token"], "secret")

    def test_user_formatter_uses_telegram_fallback(self):
        rendered = rkapi._fmt_user({"_tg_id": 42, "_tg_name": "Test <User>"})
        self.assertIn("Test &lt;User&gt;", rendered)
        self.assertIn("id: 42", rendered)

    def test_group_formatter_rejects_unsafe_link(self):
        rendered = rkapi._fmt_group({"title": "Group", "link": "javascript:alert(1)"})
        self.assertNotIn("href=", rendered)

    async def test_chart_rejects_empty_or_malformed_data(self):
        self.assertIsNone(await rkapi._make_chart([], "empty"))
        self.assertIsNone(await rkapi._make_chart([{"day": "not-a-date"}], "bad"))

    async def test_request_rejects_non_api_and_traversal_paths(self):
        module = rkapi.WerwolfStatsMod()
        with self.assertRaisesRegex(RuntimeError, "лише маршрути"):
            await module._request("GET", "admin/login")
        with self.assertRaisesRegex(RuntimeError, "лише маршрути"):
            await module._request("GET", "api/../admin/login")


if __name__ == "__main__":
    unittest.main()
