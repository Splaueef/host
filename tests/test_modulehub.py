"""Tests for the ModuleHub inline command launcher."""

import html
import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    def get_prefix(self):
        return "."


def _decorator(*args, **kwargs):
    return lambda value: value


def _load_module():
    package = types.ModuleType("hubhost")
    package.__path__ = []
    modules = types.ModuleType("hubhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("hubhost.loader")
    utils = types.ModuleType("hubhost.utils")
    loader.Module = _Module
    loader.tds = lambda value: value
    loader.command = _decorator
    utils.escape_html = lambda value: html.escape(str(value), quote=False)

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils
    sys.modules.update(
        {
            "hubhost": package,
            "hubhost.modules": modules,
            "hubhost.loader": loader,
            "hubhost.utils": utils,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "modulehub.py"
    spec = importlib.util.spec_from_file_location("hubhost.modules.modulehub", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


modulehub = _load_module()


def _loaded(class_name, commands):
    instance = type(class_name, (), {})()
    instance.commands = commands
    return instance


def _handler(doc, sink=None, label=None):
    async def command(message):
        if sink is not None:
            sink.append((label, message))

    command.__doc__ = doc
    return command


class _Call:
    def __init__(self, chat=100):
        self.form = {"chat": chat}
        self.edits = []
        self.answers = []

    async def edit(self, text, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})
        return True

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})


class _Inline:
    def __init__(self, result=True):
        self.result = result
        self.forms = []

    async def form(self, text, message, **kwargs):
        self.forms.append({"text": text, "message": message, **kwargs})
        return self.result


class _Client:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        message = types.SimpleNamespace(chat_id=chat_id, text=text, kwargs=kwargs)
        self.sent.append(message)
        return message


class _Message:
    def __init__(self, reply_to_msg_id=None):
        self.reply_to_msg_id = reply_to_msg_id
        self.answers = []


def _buttons(markup):
    return [button for row in markup for button in row]


class ModuleHubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = modulehub.ModuleHubMod()
        self.module.allmodules = types.SimpleNamespace(modules=[], commands={})

    async def test_menu_is_owner_only_and_contains_search(self):
        self.module.inline = _Inline()
        message = _Message(reply_to_msg_id=77)

        await self.module.modmenu(message)

        form = self.module.inline.forms[0]
        self.assertTrue(form["force_me"])
        self.assertFalse(form["disable_security"])
        buttons = _buttons(form["reply_markup"])
        search = next(button for button in buttons if button["text"].startswith("🔎"))
        self.assertEqual(search["args"], (77,))
        self.assertTrue(any(button.get("url") == "https://t.me/RotKranzUK" for button in buttons))

    async def test_module_commands_are_discovered_and_paginated(self):
        commands = {
            f"cmd{index}": _handler(f"Опис {index}")
            for index in range(13)
        }
        self.module.allmodules.modules = [_loaded("ContactStatusMod", commands)]
        call = _Call()

        await self.module._module_page(call, "contactstatus", 1)

        view = call.edits[-1]
        self.assertIn("сторінка <b>2/3</b>", view["text"])
        command_buttons = [
            button for button in _buttons(view["reply_markup"])
            if button["text"].startswith(".cmd")
        ]
        self.assertEqual(len(command_buttons), 6)
        self.assertEqual(command_buttons[0]["text"], ".cmd6")

    async def test_search_uses_command_docs_and_skips_unloaded_modules(self):
        commands = {
            "contactstats": _handler("Показати детальну активність користувача"),
            "contactlist": _handler("Список контактів"),
        }
        self.module.allmodules.modules = [_loaded("ContactStatusMod", commands)]
        call = _Call()

        await self.module._search_page(call, "активність")

        view = call.edits[-1]
        self.assertIn("Знайдено: <b>1</b>", view["text"])
        self.assertIn(".contactstats", view["text"])
        self.assertNotIn(".contactlist", view["text"])

    async def test_mutating_command_requires_confirmation(self):
        self.module.allmodules.modules = [
            _loaded("PurgeMod", {"purge": _handler("Видалити повідомлення")})
        ]
        call = _Call()

        await self.module._command_page(call, "purge", "purge")

        view = call.edits[-1]
        self.assertIn("команда може змінити дані", view["text"])
        run = _buttons(view["reply_markup"])[0]
        self.assertEqual(run["callback"], self.module._confirm_command)

    async def test_read_only_empty_variant_skips_confirmation(self):
        self.module.allmodules.modules = [
            _loaded("WerwolfStatsMod", {"wwkey": _handler("API key")})
        ]
        call = _Call()

        await self.module._command_page(call, "werwolf", "wwkey")

        run = _buttons(call.edits[-1]["reply_markup"])[0]
        self.assertEqual(run["callback"], self.module._execute_command)
        self.assertTrue(self.module._is_dangerous("wwkey", "new-secret"))

    async def test_secret_is_masked_on_confirmation(self):
        call = _Call()

        await self.module._confirm_command(
            call, "werwolf", "wwkey", "super-secret-token"
        )

        text = call.edits[-1]["text"]
        self.assertNotIn("super-secret-token", text)
        self.assertIn("••••••", text)

    async def test_selected_module_handler_wins_on_command_collision(self):
        invoked = []
        werwolf = _loaded(
            "WerwolfStatsMod",
            {"wwkey": _handler("RotKranz key", invoked, "werwolf")},
        )
        mistral = _loaded(
            "MistralModule",
            {"wwkey": _handler("Mistral key", invoked, "mistral")},
        )
        self.module.allmodules.modules = [mistral, werwolf]
        self.module._client = _Client()
        call = _Call(chat=321)

        await self.module._execute_command(
            call, "werwolf", "wwkey", "token", reply_id=55
        )

        self.assertEqual([item[0] for item in invoked], ["werwolf"])
        sent = self.module._client.sent[0]
        self.assertEqual(sent.chat_id, 321)
        self.assertEqual(sent.text, ".wwkey token")
        self.assertEqual(sent.kwargs, {"reply_to": 55})

    async def test_missing_module_is_reported_without_editing(self):
        call = _Call()

        await self.module._module_page(call, "math", 0)

        self.assertFalse(call.edits)
        self.assertTrue(call.answers[-1]["show_alert"])
        self.assertIn("не завантажено", call.answers[-1]["text"])


if __name__ == "__main__":
    unittest.main()
