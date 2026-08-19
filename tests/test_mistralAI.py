"""Regression tests for MistralAI group response modes."""

import importlib.util
import pathlib
import sys
import types
import unittest

# Avoid the repository's userbot ``math.py`` shadowing the standard library
# while asyncio/aiohttp are imported.
_REPO = str(pathlib.Path(__file__).parents[1])
sys.path[:] = [entry for entry in sys.path if entry not in ("", _REPO)]

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
    loader.ModuleConfig = lambda *args: dict()
    loader.ConfigValue = lambda *args, **kwargs: None
    loader.validators = types.SimpleNamespace(
        String=lambda: None,
        Integer=lambda **kwargs: None,
        Boolean=lambda: None,
        Choice=lambda choices: None,
        Hidden=lambda validator: None,
    )
    package.loader = loader
    package.utils = utils
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "mistralAI.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.mistralAI", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mistral = _load_module()


class GroupModeTests(unittest.TestCase):
    def test_all_mode_accepts_every_message(self):
        self.assertTrue(mistral._group_mode_matches("all", "звичайне повідомлення"))

    def test_addressed_mode_accepts_mention_or_reply(self):
        self.assertTrue(
            mistral._group_mode_matches(
                "addressed", "привіт, @MyAssistant!", username="myassistant"
            )
        )
        self.assertTrue(
            mistral._group_mode_matches(
                "addressed", "продовжуй", username="myassistant", is_reply_to_us=True
            )
        )
        self.assertFalse(
            mistral._group_mode_matches(
                "addressed", "розмова між людьми", username="myassistant"
            )
        )

    def test_mention_mode_requires_exact_username_mention(self):
        self.assertTrue(
            mistral._group_mode_matches(
                "mention", "@MyAssistant допоможи", username="myassistant"
            )
        )
        self.assertFalse(
            mistral._group_mode_matches(
                "mention", "відповідаю без згадки", username="myassistant", is_reply_to_us=True
            )
        )
        self.assertFalse(
            mistral._group_mode_matches(
                "mention", "@myassistant_fake ні", username="myassistant"
            )
        )


if __name__ == "__main__":
    unittest.main()
