"""Regression tests for GemmaSelf output escaping and lock cleanup."""

import asyncio
import importlib.util
import pathlib
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
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    utils.escape_html = (
        lambda value: str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    package.loader = loader
    package.utils = utils
    sys.modules.update(
        {
            "aiohttp": types.ModuleType("aiohttp"),
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
        }
    )
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.gemmaself", _REPO / "gemmaself.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gemma = _load_module()


class GemmaSelfTests(unittest.TestCase):
    def test_generated_html_is_escaped_before_edit(self):
        module = object.__new__(gemma.GemmaSelf)
        message = types.SimpleNamespace(edit=AsyncMock())

        asyncio.run(module._safe_edit(message, "<b>untrusted</b>"))

        message.edit.assert_awaited_once_with("&lt;b&gt;untrusted&lt;/b&gt;")

    def test_rate_limiter_only_discards_unlocked_locks(self):
        limiter = gemma._RateLimiter()

        async def exercise():
            lock = limiter.get(1)
            await lock.acquire()
            limiter.discard(1)
            self.assertIn(1, limiter._locks)
            lock.release()
            limiter.discard(1)
            self.assertNotIn(1, limiter._locks)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
