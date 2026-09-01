"""Regression tests for Evaluator's bounded Brainfuck interpreter."""

import importlib.util
import pathlib
import sys
import types
import unittest

_REPO = str(pathlib.Path(__file__).parents[1])
sys.path[:] = [entry for entry in sys.path if entry not in ("", _REPO)]


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    main = types.ModuleType("testhost.main")
    utils = types.ModuleType("testhost.utils")
    log = types.ModuleType("testhost.log")

    loader.Module = object
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    log.HikkaException = type("HikkaException", (), {})

    hikkatl = types.ModuleType("hikkatl")
    hikkatl.tl = types.SimpleNamespace(
        types=types.ModuleType("hikkatl.tl.types"),
        functions=types.ModuleType("hikkatl.tl.functions"),
    )
    hikkatl.tl.types.Message = object
    rpc_errors = types.ModuleType("hikkatl.errors.rpcerrorlist")
    rpc_errors.MessageIdInvalidError = type("MessageIdInvalidError", (Exception,), {})
    sessions = types.ModuleType("hikkatl.sessions")
    sessions.StringSession = type("StringSession", (), {})
    meval_module = types.ModuleType("meval")
    meval_module.meval = None

    package.loader = loader
    package.main = main
    package.utils = utils
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.main": main,
            "testhost.utils": utils,
            "testhost.log": log,
            "hikkatl": hikkatl,
            "hikkatl.errors": types.ModuleType("hikkatl.errors"),
            "hikkatl.errors.rpcerrorlist": rpc_errors,
            "hikkatl.sessions": sessions,
            "hikkatl.tl": types.ModuleType("hikkatl.tl"),
            "hikkatl.tl.types": hikkatl.tl.types,
            "meval": meval_module,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "eval.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = _load_module()


class BrainfuckTests(unittest.TestCase):
    def test_nested_loops_are_supported(self):
        machine = evaluator.Brainfuck()

        result = machine.run("++[>++[>+<-]<-]>>.")

        self.assertEqual([ord(char) for char in result], [4])
        self.assertIsNone(machine.error)

    def test_unmatched_bracket_reports_the_actual_error(self):
        machine = evaluator.Brainfuck()

        self.assertEqual(machine.run("[++"), "")
        self.assertIn("unmatched", machine.error)

    def test_infinite_program_stops_at_step_limit(self):
        machine = evaluator.Brainfuck(max_steps=50)

        self.assertEqual(machine.run("+[+]"), "")
        self.assertIn("step limit", machine.error)

    def test_zero_sized_memory_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluator.Brainfuck(memory_size=0)


if __name__ == "__main__":
    unittest.main()
