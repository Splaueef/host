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


def _u(name, text, uid=1, **kw):
    return {"role": "user", "name": name, "uid": uid, "text": text, **kw}


def _a(text):
    return {"role": "assistant", "text": text}


class HistoryFormattingTests(unittest.TestCase):
    def test_group_messages_alternate_and_mark_trigger(self):
        entries = [
            _u("Олег", "хто йде на пиво?", 1),
            _u("Іра", "я пас", 2),
            _a("Я теж пас"),
            _u("Олег", "а ти чого?", 1),
            _u("Іра", "@bot скільки буде 2+2?", 2),
        ]
        msgs = mistral._build_chat_messages(entries, is_group=True)
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertIn("[Олег]: хто йде на пиво?", msgs[0]["content"])
        self.assertIn("[Іра]: я пас", msgs[0]["content"])
        last = msgs[-1]["content"]
        self.assertIn("[Олег]: а ти чого?", last)
        self.assertIn(">>> Зараз до тебе звертається [Іра]", last)
        self.assertTrue(last.rstrip().endswith("[Іра]: @bot скільки буде 2+2?"))

    def test_history_never_starts_with_assistant(self):
        msgs = mistral._build_chat_messages([_a("старе"), _u("A", "привіт")], is_group=True)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(len(msgs), 1)

    def test_trigger_is_not_duplicated(self):
        msgs = mistral._build_chat_messages([_u("A", "одне питання")], is_group=False)
        self.assertEqual(msgs, [{"role": "user", "content": "одне питання"}])

    def test_reply_context_is_shown(self):
        entry = _u("Іра", "згодна", 2, reply_name="Олег", reply_text="погода класна")
        msgs = mistral._build_chat_messages([entry], is_group=True)
        self.assertIn("відповідь на повідомлення Олег: «погода класна»", msgs[0]["content"])
        entry = _u("Іра", "чому?", 2, reply_to_us=True, reply_text="ні")
        msgs = mistral._build_chat_messages([entry], is_group=True)
        self.assertIn("відповідь на твоє повідомлення: «ні»", msgs[0]["content"])

    def test_trim_keeps_newest_and_trigger(self):
        entries = [_u("A", "x" * 500, i) for i in range(10)] + [_u("B", "останнє")]
        trimmed = mistral._trim_history(entries, 1200)
        self.assertEqual(trimmed[-1]["text"], "останнє")
        self.assertLess(len(trimmed), len(entries))
        self.assertEqual(mistral._trim_history(entries[-1:], 1)[0]["text"], "останнє")

    def test_transcript_for_agents(self):
        text = mistral._build_transcript(
            [_u("Олег", "привіт"), _a("Привіт!"), _u("Іра", "як справи?", 2)], is_group=True
        )
        self.assertIn("[Олег]: привіт", text)
        self.assertIn("[Ти]: Привіт!", text)
        self.assertIn(">>> Зараз до тебе звертається [Іра]", text)


class TextCleanupTests(unittest.TestCase):
    def test_strip_speaker_prefix(self):
        self.assertEqual(mistral._strip_speaker_prefix("[Бот]: привіт"), "привіт")
        self.assertEqual(mistral._strip_speaker_prefix("Assistant: ok"), "ok")
        self.assertEqual(
            mistral._strip_speaker_prefix("[WERWOLF:user:SENDER]: x"), "[WERWOLF:user:SENDER]: x"
        )
        self.assertEqual(mistral._strip_speaker_prefix("звичайна відповідь"), "звичайна відповідь")

    def test_strip_bot_mention(self):
        self.assertEqual(mistral._strip_bot_mention("@MyBot, привіт", "mybot"), "привіт")
        self.assertEqual(mistral._strip_bot_mention("@mybot", "mybot"), "@mybot")
        self.assertEqual(mistral._strip_bot_mention("@mybot_x hi", "mybot"), "@mybot_x hi")


if __name__ == "__main__":
    unittest.main()
