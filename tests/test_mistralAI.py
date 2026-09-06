"""Regression tests for MistralAI group response modes."""

import importlib.util
import pathlib
import sys
import types
import unittest
from collections import deque

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


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_message_is_not_duplicated_in_chat_payload(self):
        module = object.__new__(mistral.MistralModule)
        module.config = {
            "agent_system_prompt": "system",
            "agent_model": "model",
            "max_tokens": 100,
            "werwolf_api_key": "",
            "werwolf_enabled": False,
        }
        captured = {}

        async def post(path, payload):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "ok"}}]}

        module._post = post
        history = deque([{"role": mistral.ROLE_USER, "content": "hello"}])

        result = await module._chat_history(history=history, new_msg="hello")

        self.assertEqual(result, "ok")
        user_messages = [
            item for item in captured["payload"]["messages"]
            if item.get("role") == mistral.ROLE_USER
        ]
        self.assertEqual(user_messages, [{"role": mistral.ROLE_USER, "content": "hello"}])

    def test_history_limit_change_resizes_existing_deque(self):
        module = object.__new__(mistral.MistralModule)
        module.config = {"agent_history_limit": 2}
        module._memory = {
            7: deque(
                [
                    {"role": "user", "content": "one"},
                    {"role": "user", "content": "two"},
                    {"role": "user", "content": "three"},
                ],
                maxlen=5,
            )
        }

        history = module._get_plain_mem(7)

        self.assertEqual(history.maxlen, 2)
        self.assertEqual([item["content"] for item in history], ["two", "three"])

    def test_repository_has_no_default_werwolf_credential(self):
        source = (pathlib.Path(__file__).parents[1] / "mistralAI.py").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(
            source,
            r'"werwolf_api_key"\s*,\s*"[A-Za-z0-9_-]{24,}"',
        )


if __name__ == "__main__":
    unittest.main()
