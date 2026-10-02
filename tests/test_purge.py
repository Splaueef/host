"""Tests for the Purge module."""

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

    class Module:
        def get_prefix(self):
            return "."

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.Module = Module
    loader.tds = tds
    loader.group_admin_delete_messages = lambda value: value
    loader.ratelimit = lambda value: value
    utils.get_args = lambda message: message.args
    utils.escape_html = lambda value: value
    utils.answer = mock.AsyncMock()
    telethon = types.ModuleType("telethon")
    telethon.tl = types.SimpleNamespace(types=types.SimpleNamespace(User=object))
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "telethon": telethon,
    })
    path = pathlib.Path(__file__).parents[1] / "purge.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.purge", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


purge = _load_module()


async def _aiter(values):
    for value in values:
        yield value


class _Client:
    def __init__(self, history):
        self.history = history
        self.delete_messages = mock.AsyncMock()
        self.calls = []

    async def is_bot(self):
        return False

    def iter_messages(self, *args, **kwargs):
        self.calls.append(kwargs)
        return _aiter(self.history)


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        purge.utils.answer.reset_mock()
        self.module = purge.PurgeMod()

    async def test_del_without_previous_message_does_not_crash(self):
        client = _Client([])
        message = types.SimpleNamespace(id=10, is_reply=False, client=client, to_id=1)

        await self.module.delcmd(message)

        client.delete_messages.assert_not_awaited()
        self.assertIn("What message", purge.utils.answer.await_args.args[1])

    async def test_delme_deletes_own_messages_in_batches(self):
        history = [types.SimpleNamespace(id=i) for i in range(150, 0, -1)]
        client = _Client(history)
        message = types.SimpleNamespace(id=200, client=client, to_id=1, args=["150"])

        await self.module.delmecmd(message)

        self.assertEqual(client.calls[0]["from_user"], "me")
        self.assertEqual(client.calls[0]["limit"], 150)
        batches = [call.args[1] for call in client.delete_messages.await_args_list]
        self.assertEqual([len(batch) for batch in batches], [100, 51])
        self.assertEqual(batches[0][0], 200)

    async def test_delme_validates_count(self):
        client = _Client([])
        for args in ([], ["0"], ["abc"], ["501"]):
            await self.module.delmecmd(
                types.SimpleNamespace(id=1, client=client, to_id=1, args=args)
            )
        client.delete_messages.assert_not_awaited()
        self.assertEqual(purge.utils.answer.await_count, 4)


if __name__ == "__main__":
    unittest.main()
