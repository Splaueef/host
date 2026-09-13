"""Regression tests for the HiddenGifts payment confirmation flow."""

import html
import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    pass


class _ConfigValue:
    def __init__(self, key, default, doc, validator=None):
        self.key = key
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.key, value.default) for value in values)


def _decorator(*args, **kwargs):
    return lambda value: value


def _load_module():
    package = types.ModuleType("hiddenhost")
    package.__path__ = []
    modules = types.ModuleType("hiddenhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("hiddenhost.loader")
    utils = types.ModuleType("hiddenhost.utils")

    loader.Module = _Module
    loader.ModuleConfig = _ModuleConfig
    loader.ConfigValue = _ConfigValue
    loader.tds = lambda value: value
    loader.command = _decorator
    loader.validators = types.SimpleNamespace(Boolean=lambda **kwargs: object())

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    utils.get_args_raw = lambda message: message.args
    utils.escape_html = lambda value: html.escape(str(value), quote=False)
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    telethon_errors = types.ModuleType("telethon.errors")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_functions = types.ModuleType("telethon.tl.functions")
    telethon_payments = types.ModuleType("telethon.tl.functions.payments")
    telethon_types = types.ModuleType("telethon.tl.types")

    class RPCError(Exception):
        pass

    class GetPaymentFormRequest:
        def __init__(self, invoice):
            self.invoice = invoice

    class GetStarGiftsRequest:
        def __init__(self, hash):
            self.hash = hash

    class SendStarsFormRequest:
        def __init__(self, form_id, invoice):
            self.form_id = form_id
            self.invoice = invoice

    class InputInvoiceStarGift:
        def __init__(self, peer, gift_id, hide_name=None, message=None):
            self.peer = peer
            self.gift_id = gift_id
            self.hide_name = hide_name
            self.message = message

    class TextWithEntities:
        def __init__(self, text, entities):
            self.text = text
            self.entities = entities

    telethon_errors.RPCError = RPCError
    telethon_payments.GetPaymentFormRequest = GetPaymentFormRequest
    telethon_payments.GetStarGiftsRequest = GetStarGiftsRequest
    telethon_payments.SendStarsFormRequest = SendStarsFormRequest
    telethon_types.InputInvoiceStarGift = InputInvoiceStarGift
    telethon_types.TextWithEntities = TextWithEntities
    sys.modules.update(
        {
            "hiddenhost": package,
            "hiddenhost.modules": modules,
            "hiddenhost.loader": loader,
            "hiddenhost.utils": utils,
            "telethon": telethon,
            "telethon.errors": telethon_errors,
            "telethon.tl": telethon_tl,
            "telethon.tl.functions": telethon_functions,
            "telethon.tl.functions.payments": telethon_payments,
            "telethon.tl.types": telethon_types,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "hidden_gifts.py"
    spec = importlib.util.spec_from_file_location(
        "hiddenhost.modules.hidden_gifts", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hidden_gifts = _load_module()


class _Price:
    def __init__(self, amount):
        self.amount = amount


def _form(amount=50, currency="XTR", form_id=100):
    return types.SimpleNamespace(
        form_id=form_id,
        invoice=types.SimpleNamespace(currency=currency, prices=[_Price(amount)]),
    )


def _gift(
    gift_id,
    title="Кекс",
    stars=25,
    emoji="🧁",
    sold_out=False,
    auction=False,
    remains=None,
    per_user_remains=None,
    require_premium=False,
):
    sticker = types.SimpleNamespace(
        attributes=[types.SimpleNamespace(alt=emoji)]
    )
    return types.SimpleNamespace(
        id=gift_id,
        title=title,
        stars=stars,
        sticker=sticker,
        sold_out=sold_out,
        auction=auction,
        availability_remains=remains,
        availability_total=100 if remains is not None else None,
        per_user_remains=per_user_remains,
        locked_until_date=None,
        require_premium=require_premium,
        birthday=False,
    )


class _Entity:
    def __init__(self, user_id=42, first_name="Yana", bot=False):
        self.id = user_id
        self.first_name = first_name
        self.last_name = None
        self.bot = bot


class _Client:
    def __init__(self, forms=None, catalog=None):
        self.forms = list(forms or [])
        self.catalog = list(catalog or [])
        self.requests = []
        self.entities = {"@yana": _Entity()}

    async def get_entity(self, target):
        if isinstance(target, int):
            return _Entity(target)
        return self.entities[target]

    async def get_input_entity(self, entity):
        return f"input:{entity.id}"

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, hidden_gifts.GetStarGiftsRequest):
            return types.SimpleNamespace(gifts=self.catalog)
        if isinstance(request, hidden_gifts.SendStarsFormRequest):
            return types.SimpleNamespace(updates=[])
        response = self.forms.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Inline:
    def __init__(self):
        self.forms = []

    async def form(self, text, message, **kwargs):
        self.forms.append({"text": text, "message": message, **kwargs})
        return True


class _Message:
    def __init__(self, args="", is_private=False, chat=None):
        self.args = args
        self.is_private = is_private
        self._chat = chat
        self.reply_to_msg_id = None
        self.answers = []

    async def get_chat(self):
        return self._chat


class _Call:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit(self, text, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})


def _buttons(markup):
    return [button for row in markup for button in row]


class HiddenGiftsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = hidden_gifts.HiddenGiftsMod()
        self.module.inline = _Inline()

    async def _ready(self, forms=None, catalog=None):
        client = _Client(forms, catalog)
        await self.module.client_ready(client, object())
        return client

    def test_catalog_has_all_historical_gifts_and_heart_bear_id(self):
        self.assertEqual(len(self.module.GIFTS), 11)
        bear = self.module.GIFT_BY_ID[5800655655995968830]
        self.assertEqual(bear["name"], "Білий ведмедик із серцем")

    def test_camel_case_rpc_error_is_translated(self):
        error_type = type("BalanceTooLowError", (hidden_gifts.RPCError,), {})
        error = error_type("payment failed")

        self.assertEqual(
            self.module._friendly_error(error),
            "недостатньо Stars на балансі",
        )

    async def test_command_opens_owner_only_catalog_with_message(self):
        client = await self._ready(catalog=[_gift(7000000000000000001)])
        message = _Message("@yana | Люблю тебе")

        await self.module.hgift(message)

        self.assertEqual(len(self.module.inline.forms), 1)
        form = self.module.inline.forms[0]
        self.assertTrue(form["force_me"])
        self.assertIn("Yana", form["text"])
        self.assertIn("Люблю тебе", form["text"])
        labels = [button["text"] for button in _buttons(form["reply_markup"])]
        self.assertTrue(any("Актуальні · 1" in label for label in labels))
        self.assertTrue(any("Історичні · 11" in label for label in labels))
        self.assertIsInstance(client.requests[0], hidden_gifts.GetStarGiftsRequest)

    async def test_current_catalog_filters_unsendable_and_historical_duplicates(self):
        current_id = 7000000000000000001
        await self._ready(
            catalog=[
                _gift(current_id, title="Кекс", stars=25),
                _gift(7000000000000000002, sold_out=True),
                _gift(7000000000000000003, auction=True),
                _gift(7000000000000000004, remains=0),
                _gift(5800655655995968830),
            ]
        )

        gifts = await self.module._get_current_gifts()

        self.assertEqual([gift["id"] for gift in gifts], [current_id])
        self.assertEqual(gifts[0]["emoji"], "🧁")
        self.assertEqual(gifts[0]["stars"], 25)

    async def test_current_gift_can_be_selected_and_paid(self):
        current_id = 7000000000000000001
        client = await self._ready([_form(25), _form(25)])
        gift = self.module._normalise_current_gifts([_gift(current_id)])[0]
        recipient = {"peer": "input:42", "name": "Yana", "id": 42}
        token = self.module._create_session(recipient, "", current_gifts=[gift])
        call = _Call()

        await self.module._select_gift(call, token, current_id, "current", 0)
        await self.module._pay(call, token)

        self.assertEqual(len(client.requests), 3)
        self.assertIsInstance(client.requests[-1], hidden_gifts.SendStarsFormRequest)
        self.assertEqual(client.requests[-1].invoice.gift_id, current_id)
        self.assertIn("Подарунок надіслано", call.edits[-1]["text"])

    async def test_current_catalog_is_paginated(self):
        await self._ready()
        gifts = self.module._normalise_current_gifts(
            [_gift(7000000000000000000 + index, title=f"Gift {index}") for index in range(1, 11)]
        )
        recipient = {"peer": "input:42", "name": "Yana", "id": 42}
        token = self.module._create_session(recipient, "", current_gifts=gifts)
        call = _Call()

        await self.module._open_catalog(call, token, "current", 0)

        labels = [button["text"] for button in _buttons(call.edits[-1]["reply_markup"])]
        self.assertIn("➡️", labels)
        self.assertIn("Сторінка <b>1/2</b>", call.edits[-1]["text"])

    async def test_selection_preflights_before_showing_payment_button(self):
        client = await self._ready([_form(50)])
        recipient = {
            "peer": "input:42",
            "name": "Yana",
            "id": 42,
        }
        token = self.module._create_session(recipient, "Привіт")
        call = _Call()

        await self.module._select_gift(call, token, 5800655655995968830)

        self.assertEqual(len(client.requests), 1)
        self.assertIsInstance(client.requests[0], hidden_gifts.GetPaymentFormRequest)
        self.assertIn("Telegram підтвердив ціну: <b>50 ⭐</b>", call.edits[-1]["text"])
        labels = [button["text"] for button in _buttons(call.edits[-1]["reply_markup"])]
        self.assertIn("💫 Надіслати за 50 ⭐", labels)

    async def test_payment_refetches_form_and_submits_only_after_confirmation(self):
        client = await self._ready([_form(50, form_id=1), _form(50, form_id=2)])
        recipient = {"peer": "input:42", "name": "Yana", "id": 42}
        token = self.module._create_session(recipient, "")
        call = _Call()
        await self.module._select_gift(call, token, 5800655655995968830)

        await self.module._pay(call, token)

        self.assertEqual(len(client.requests), 3)
        self.assertIsInstance(client.requests[-1], hidden_gifts.SendStarsFormRequest)
        self.assertEqual(client.requests[-1].form_id, 2)
        self.assertNotIn(token, self.module._sessions)
        self.assertIn("Подарунок надіслано", call.edits[-1]["text"])

    async def test_price_change_requires_a_new_confirmation(self):
        client = await self._ready([_form(50), _form(75)])
        recipient = {"peer": "input:42", "name": "Yana", "id": 42}
        token = self.module._create_session(recipient, "")
        call = _Call()
        await self.module._select_gift(call, token, 5800655655995968830)

        await self.module._pay(call, token)

        self.assertEqual(len(client.requests), 2)
        self.assertFalse(
            any(
                isinstance(request, hidden_gifts.SendStarsFormRequest)
                for request in client.requests
            )
        )
        self.assertIn("Ціна змінилася з 50 до 75 ⭐", call.edits[-1]["text"])
        self.assertEqual(self.module._sessions[token]["confirmed_price"], 75)

    async def test_non_star_payment_form_is_rejected(self):
        client = await self._ready([_form(50, currency="USD")])
        recipient = {"peer": "input:42", "name": "Yana", "id": 42}
        token = self.module._create_session(recipient, "")
        call = _Call()

        await self.module._select_gift(call, token, 5800655655995968830)

        self.assertEqual(len(client.requests), 1)
        self.assertIn("не Telegram Stars", call.edits[-1]["text"])
        self.assertNotIn("Надіслати за", str(call.edits[-1]["reply_markup"]))


if __name__ == "__main__":
    unittest.main()
