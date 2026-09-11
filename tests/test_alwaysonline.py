"""Tests for the AlwaysOnline Hikka module."""

import datetime  # Load stdlib math before the repository's math.py can shadow it.
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


class _Validators:
    Boolean = staticmethod(lambda: object())
    Integer = staticmethod(lambda **kwargs: object())


class _RPCError(Exception):
    pass


class _FloodWaitError(_RPCError):
    def __init__(self, seconds):
        super().__init__(seconds)
        self.seconds = seconds


class _UpdateStatusRequest:
    def __init__(self, offline):
        self.offline = offline


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.validators = _Validators

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.loop = lambda **kwargs: lambda function: function
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    utils.escape_html = lambda value: value
    utils.answer = mock.AsyncMock()
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    account = types.ModuleType("telethon.tl.functions.account")
    errors.FloodWaitError = _FloodWaitError
    errors.RPCError = _RPCError
    account.UpdateStatusRequest = _UpdateStatusRequest

    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.errors": errors,
            "telethon.tl": tl,
            "telethon.tl.functions": functions,
            "telethon.tl.functions.account": account,
        }
    )
    path = pathlib.Path(__file__).parent / "alwaysonline.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.alwaysonline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


alwaysonline = _load_module()


class _Client:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def __call__(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error


class AlwaysOnlineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        alwaysonline.utils.answer.reset_mock()
        self.module = alwaysonline.AlwaysOnlineMod()

    async def test_client_ready_publishes_online_immediately(self):
        client = _Client()
        await self.module.client_ready(client, None)
        self.assertFalse(client.calls[-1].offline)
        self.assertIsNotNone(self.module._last_success)

    async def test_onlineoff_disables_loop_and_publishes_offline(self):
        client = _Client()
        await self.module.client_ready(client, None)
        client.calls.clear()

        await self.module.onlineoffcmd(types.SimpleNamespace())

        self.assertFalse(self.module.config["enabled"])
        self.assertTrue(client.calls[-1].offline)
        self.assertIn("вимкнено", alwaysonline.utils.answer.await_args.args[1])

    async def test_presence_loop_refreshes_when_due(self):
        client = _Client()
        await self.module.client_ready(client, None)
        client.calls.clear()
        self.module._next_refresh = 0

        await self.module.presence_loop()

        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.calls[0].offline)

    async def test_flood_wait_is_recorded_and_retried_later(self):
        client = _Client(_FloodWaitError(90))
        before = alwaysonline.time.monotonic()

        await self.module.client_ready(client, None)

        self.assertIn("90", self.module._last_error)
        self.assertGreaterEqual(self.module._next_refresh, before + 90)


if __name__ == "__main__":
    unittest.main()
