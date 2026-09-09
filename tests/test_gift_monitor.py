"""Regression tests for GiftMonitor catalog state and previews."""

import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value


class _ConfigValue:
    def __init__(self, key, default, doc, validator=None):
        self.key = key
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.key, value.default) for value in values)


def _command(*args, **kwargs):
    return lambda value: value


def _load_module():
    package = types.ModuleType("gifthost")
    package.__path__ = []
    modules = types.ModuleType("gifthost.modules")
    modules.__path__ = []
    loader = types.ModuleType("gifthost.loader")
    utils = types.ModuleType("gifthost.utils")

    loader.Module = _Module
    loader.ModuleConfig = _ModuleConfig
    loader.ConfigValue = _ConfigValue
    loader.tds = lambda value: value
    loader.command = _command
    loader.validators = types.SimpleNamespace(
        Integer=lambda **kwargs: object(),
        Boolean=lambda **kwargs: object(),
    )

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_functions = types.ModuleType("telethon.tl.functions")
    telethon_payments = types.ModuleType("telethon.tl.functions.payments")

    class GetStarGiftsRequest:
        def __init__(self, hash):
            self.hash = hash

    telethon_payments.GetStarGiftsRequest = GetStarGiftsRequest
    sys.modules.update(
        {
            "gifthost": package,
            "gifthost.modules": modules,
            "gifthost.loader": loader,
            "gifthost.utils": utils,
            "telethon": telethon,
            "telethon.tl": telethon_tl,
            "telethon.tl.functions": telethon_functions,
            "telethon.tl.functions.payments": telethon_payments,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "gift_monitor.py"
    spec = importlib.util.spec_from_file_location(
        "gifthost.modules.gift_monitor", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gift_monitor = _load_module()


def _gift(gift_id, stars, title=None, **kwargs):
    sticker = types.SimpleNamespace(
        attributes=[types.SimpleNamespace(alt="🎁")]
    )
    values = {
        "id": gift_id,
        "stars": stars,
        "title": title,
        "sticker": sticker,
        "limited": True,
        "sold_out": False,
        "availability_total": 10_000,
        "availability_remains": 9_999,
        "availability_resale": None,
        "upgrade_stars": None,
        "auction": False,
        "auction_slug": None,
        "require_premium": False,
        "locked_until_date": None,
    }
    values.update(kwargs)
    return types.SimpleNamespace(**values)


class _Catalog:
    def __init__(self, gifts, hash=1):
        self.gifts = gifts
        self.hash = hash


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent_files = []
        self.sent_messages = []
        self.fail_preview = False

    async def __call__(self, request):
        return self.responses.pop(0)

    async def send_file(self, target, file, **kwargs):
        if self.fail_preview:
            raise RuntimeError("stale file reference")
        self.sent_files.append((target, file, kwargs))

    async def send_message(self, target, text, **kwargs):
        self.sent_messages.append((target, text, kwargs))


class GiftMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = gift_monitor.GiftMonitorMod()

    async def test_first_check_creates_silent_baseline(self):
        old_in_range = _gift(1, 50, "Old gift")
        old_out_of_range = _gift(2, 500, "Expensive old gift")
        client = _Client([_Catalog([old_in_range, old_out_of_range])])
        await self.module.client_ready(client, {})

        sent = await self.module._check_gifts()

        self.assertEqual(sent, 0)
        self.assertEqual(self.module._known_gifts, {1, 2})
        self.assertEqual(self.module.get("known_gift_ids"), [1, 2])
        self.assertTrue(self.module.get("catalog_initialized"))
        self.assertEqual(client.sent_files, [])
        self.assertEqual(client.sent_messages, [])

    async def test_only_new_gift_is_sent_with_sticker_preview(self):
        old = _gift(1, 50, "Old gift")
        new = _gift(2, 25, "Scared Cat")
        client = _Client([_Catalog([old]), _Catalog([old, new], hash=2)])
        await self.module.client_ready(client, {})
        await self.module._check_gifts()

        sent = await self.module._check_gifts()

        self.assertEqual(sent, 1)
        self.assertEqual(len(client.sent_files), 1)
        target, sticker, kwargs = client.sent_files[0]
        self.assertEqual(target, "me")
        self.assertIs(sticker, new.sticker)
        self.assertIn("Scared Cat", kwargs["caption"])
        self.assertNotIn("t.me/nft/2", kwargs["caption"])
        self.assertEqual(self.module.get("known_gift_ids"), [1, 2])

    async def test_preview_error_falls_back_to_text_notification(self):
        old = _gift(1, 50)
        new = _gift(2, 50)
        client = _Client([_Catalog([old]), _Catalog([old, new], hash=2)])
        client.fail_preview = True
        await self.module.client_ready(client, {})
        await self.module._check_gifts()

        sent = await self.module._check_gifts()

        self.assertEqual(sent, 1)
        self.assertEqual(len(client.sent_messages), 1)
        self.assertIn("Подарунок 🎁", client.sent_messages[0][1])

    async def test_price_change_does_not_reannounce_known_gift(self):
        gift = _gift(7, 500, "Existing")
        client = _Client([_Catalog([gift]), _Catalog([gift], hash=2)])
        await self.module.client_ready(client, {})
        await self.module._check_gifts()
        self.module.config["max_stars"] = 1_000

        sent = await self.module._check_gifts()

        self.assertEqual(sent, 0)
        self.assertEqual(client.sent_files, [])

    def test_only_auction_slug_gets_a_valid_deep_link(self):
        regular = self.module._format_gift(_gift(10, 15, "Regular"), 15)
        auction = self.module._format_gift(
            _gift(
                11,
                100,
                "Auction",
                auction=True,
                auction_slug="special drop",
            ),
            100,
        )

        self.assertNotIn("t.me/nft", regular)
        self.assertNotIn("href=", regular)
        self.assertIn("https://t.me/auction/special%20drop", auction)


if __name__ == "__main__":
    unittest.main()
