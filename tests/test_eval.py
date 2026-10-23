"""Tests for the Evaluator module."""

import asyncio
import functools
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
    main = types.ModuleType("testhost.main")
    log = types.ModuleType("testhost.log")

    class Module:
        pass

    loader.Module = Module
    loader.tds = lambda cls: cls
    loader.command = lambda **_: (lambda func: func)

    def run_sync(func, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(func, *args, **kwargs),
        )

    utils.run_sync = run_sync
    log.HikkaException = object
    package.loader = loader
    package.utils = utils
    package.main = main

    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    tl_types = types.ModuleType("telethon.tl.types")
    tl_types.__package__ = "telethon.tl"
    tl_types.Message = type("Message", (), {})
    tl_functions = types.ModuleType("telethon.tl.functions")
    tl_functions.__package__ = "telethon.tl"
    tl_functions.PingRequest = type("PingRequest", (), {})
    tl.types = tl_types
    tl.functions = tl_functions
    telethon.tl = tl
    errors = types.ModuleType("telethon.errors.rpcerrorlist")
    errors.MessageIdInvalidError = type("MessageIdInvalidError", (Exception,), {})
    sessions = types.ModuleType("telethon.sessions")
    sessions.StringSession = object
    meval = types.ModuleType("meval")
    meval.meval = mock.AsyncMock()

    path = pathlib.Path(__file__).parents[1] / "eval.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.eval", path)
    module = importlib.util.module_from_spec(spec)
    # Only for the import, so the stubs don't leak into other test modules
    with mock.patch.dict(
        sys.modules,
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "testhost.main": main,
            "testhost.log": log,
            "telethon": telethon,
            "telethon.tl": tl,
            "telethon.tl.types": tl_types,
            "telethon.tl.functions": tl_functions,
            "telethon.errors": types.ModuleType("telethon.errors"),
            "telethon.errors.rpcerrorlist": errors,
            "telethon.sessions": sessions,
            "meval": meval,
            spec.name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


evaluator = _load_module()


class EvaluatorTests(unittest.IsolatedAsyncioTestCase):
    def test_uses_telethon_instead_of_retired_fork(self):
        source = (pathlib.Path(__file__).parents[1] / "eval.py").read_text()

        self.assertNotIn("import hikkatl", source)
        self.assertNotIn("from hikkatl", source)

    async def test_programs_run_without_blocking_event_loop(self):
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.ensure_future(ticker())
        try:
            output = await evaluator._run(
                [sys.executable, "-c", "import time; time.sleep(0.2); print('ok')"]
            )
        finally:
            task.cancel()

        self.assertEqual(output.strip(), "ok")
        self.assertGreater(ticks, 5)

    def test_tl_namespace_is_built_once(self):
        module = evaluator.Evaluator()
        with mock.patch.object(
            evaluator.Evaluator,
            "get_sub",
            wraps=module.get_sub,
        ) as get_sub:
            first = module._tl_namespace()
            second = module._tl_namespace()

        self.assertIs(first, second)
        self.assertEqual(get_sub.call_count, 2)
        self.assertIn("Message", first)
        self.assertIn("PingRequest", first)


if __name__ == "__main__":
    unittest.main()
