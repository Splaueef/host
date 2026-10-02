"""Tests for MathSolver's symbolic commands."""

import asyncio
import importlib.util
import pathlib
import sys
import time
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
    loader.Module = object
    loader.tds = lambda value: value
    utils.get_args_raw = lambda message: message.args
    utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.answer = mock.AsyncMock()

    async def run_sync(func, *args):
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)

    utils.run_sync = run_sync
    package.loader = loader
    package.utils = utils
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
    })
    # The repository's math.py must not shadow the standard library module.
    path = pathlib.Path(__file__).parents[1] / "math.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.mathsolver", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mathsolver = _load_module()


class MathSolverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mathsolver.utils.answer.reset_mock()
        self.module = mathsolver.MathSolverMod()

    def test_equation_roots_and_simplification(self):
        roots = self.module._solve_text("x^2-4=0")
        self.assertIn("[-2, 2]", roots)

        simplified = self.module._solve_text("x^2+2x+1")
        self.assertIn("(x + 1)**2", simplified)

        number = self.module._solve_text("2^10+1/2")
        self.assertIn("2049/2", number)

    def test_derivative_and_integrals(self):
        self.assertIn("3*x**2", self.module._diff_text("x^3"))
        self.assertIn("6*x", self.module._diff_text("2 x^3"))
        self.assertIn("x**3/3", self.module._integral_text("x^2"))
        self.assertIn(">9<", self.module._integral_text("x^2 0 3"))

    async def test_input_is_escaped_in_reply(self):
        await self.module.mcmd(types.SimpleNamespace(args="x<2"))
        rendered = mathsolver.utils.answer.await_args.args[1]
        self.assertIn("x&lt;2", rendered)
        self.assertNotIn("x<2", rendered)

    async def test_slow_computation_times_out_without_blocking(self):
        self.module.SOLVE_TIMEOUT = 0.1
        self.module._solve_text = lambda raw: time.sleep(0.5) or "late"

        await self.module.mcmd(types.SimpleNamespace(args="x"))

        self.assertIn("забагато часу", mathsolver.utils.answer.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
