"""Regression tests for GiftMonitor's official NFT resale scanner."""

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


def _decorator(*args, **kwargs):
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
    loader.command = _decorator
    loader.loop = _decorator
    loader.validators = types.SimpleNamespace(
        Integer=lambda **kwargs: object(),
        Boolean=lambda **kwargs: object(),
    )

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    utils.get_args_raw = lambda message: message.args
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_functions = types.ModuleType("telethon.tl.functions")
    telethon_payments = types.ModuleType("telethon.tl.functions.payments")

    class GetStarGiftsRequest:
        def __init__(self, hash):
            self.hash = hash

    class GetResaleStarGiftsRequest:
        def __init__(
            self,
            gift_id,
            offset,
            limit,
            sort_by_price=None,
            **kwargs,
        ):
            self.gift_id = gift_id
            self.offset = offset
            self.limit = limit
            self.sort_by_price = sort_by_price
            self.extra = kwargs

    telethon_payments.GetStarGiftsRequest = GetStarGiftsRequest
    telethon_payments.GetResaleStarGiftsRequest = GetResaleStarGiftsRequest
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


class _StarsAmount:
    def __init__(self, amount, nanos=0):
        self.amount = amount
        self.nanos = nanos


class _StarsTonAmount:
    def __init__(self, amount):
        self.amount = amount


class _StarGiftAttributeModel:
    def __init__(self, name="Plush", document=None):
        self.name = name
        self.document = document or object()


class _StarGiftAttributePattern:
    def __init__(self, name="Stars", document=None):
        self.name = name
        self.document = document or object()


class _StarGiftAttributeBackdrop:
    def __init__(self, name="Midnight"):
        self.name = name


def _base(gift_id, *, resale=10, minimum=1, title="Collection"):
    return types.SimpleNamespace(
        id=gift_id,
        availability_resale=resale,
        resell_min_stars=minimum,
        title=title,
    )


def _nft(
    nft_id,
    *,
    collection_id=1,
    slug=None,
    title="Plush Pepe",
    number=7,
    stars=None,
    nanos=0,
    ton=None,
    model="Plush",
):
    amounts = []
    if stars is not None:
        amounts.append(_StarsAmount(stars, nanos))
    if ton is not None:
        amounts.append(_StarsTonAmount(ton))
    return types.SimpleNamespace(
        id=nft_id,
        gift_id=collection_id,
        slug=slug or f"PlushPepe-{nft_id}",
        title=title,
        num=number,
        resell_amount=amounts,
        attributes=[
            _StarGiftAttributeModel(model),
            _StarGiftAttributePattern(),
            _StarGiftAttributeBackdrop(),
        ],
    )


class _Catalog:
    def __init__(self, gifts):
        self.gifts = gifts


class _Resale:
    def __init__(self, gifts, count=None):
        self.gifts = gifts
        self.count = len(gifts) if count is None else count
        self.next_offset = None


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.sent_files = []
        self.sent_messages = []
        self.fail_preview = False
        self.fail_message = False

    async def __call__(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def send_file(self, target, file, **kwargs):
        if self.fail_preview:
            raise RuntimeError("stale file reference")
        self.sent_files.append((target, file, kwargs))

    async def send_message(self, target, text, **kwargs):
        if self.fail_message:
            raise RuntimeError("message delivery failed")
        self.sent_messages.append((target, text, kwargs))


class _Message:
    def __init__(self, args=""):
        self.args = args
        self.answers = []


class GiftMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = gift_monitor.GiftMonitorMod()
        self.module.config["request_delay_ms"] = 0

    async def _ready(self, responses):
        client = _Client(responses)
        await self.module.client_ready(client, {})
        return client

    async def test_first_collection_scan_creates_silent_nft_baseline(self):
        old = _nft(101, stars=50)
        client = await self._ready([_Catalog([_base(1)]), _Resale([old])])

        sent = await self.module._check_market(
            force_catalog=True,
            scan_all=True,
        )

        self.assertEqual(sent, 0)
        self.assertIn(old.slug, self.module._known_listings)
        self.assertEqual(self.module.get("initialized_nft_collections"), [1])
        self.assertEqual(client.sent_files, [])
        self.assertEqual(client.sent_messages, [])

    async def test_new_in_range_listing_sends_model_preview(self):
        old = _nft(101, stars=50)
        new = _nft(102, stars=25, title="Scared Cat")
        client = await self._ready(
            [
                _Catalog([_base(1)]),
                _Resale([old]),
                _Resale([new, old]),
            ]
        )
        await self.module._check_market(force_catalog=True, scan_all=True)

        sent = await self.module._check_market(scan_all=True)

        self.assertEqual(sent, 1)
        self.assertEqual(len(client.sent_files), 1)
        target, preview, kwargs = client.sent_files[0]
        self.assertEqual(target, "me")
        self.assertIs(preview, new.attributes[0].document)
        self.assertIn("Scared Cat #7", kwargs["caption"])
        self.assertIn(f"https://t.me/nft/{new.slug}", kwargs["caption"])
        self.assertIn(new.slug, self.module._known_listings)

    async def test_price_change_into_range_is_announced(self):
        listing = _nft(201, stars=500)
        cheaper = _nft(201, slug=listing.slug, stars=100)
        client = await self._ready(
            [
                _Catalog([_base(1, minimum=500)]),
                _Resale([listing]),
                _Resale([cheaper]),
            ]
        )
        await self.module._check_market(force_catalog=True, scan_all=True)

        sent = await self.module._check_market(scan_all=True)

        self.assertEqual(sent, 1)
        self.assertIn(
            "Ціна NFT увійшла",
            client.sent_files[0][2]["caption"],
        )

    async def test_failed_notification_is_not_remembered_and_is_retried(self):
        old = _nft(301, stars=50)
        new = _nft(302, stars=40)
        client = await self._ready(
            [
                _Catalog([_base(1)]),
                _Resale([old]),
                _Resale([new, old]),
                _Resale([new, old]),
            ]
        )
        await self.module._check_market(force_catalog=True, scan_all=True)

        client.fail_preview = True
        client.fail_message = True
        sent = await self.module._check_market(scan_all=True)
        self.assertEqual(sent, 0)
        self.assertNotIn(new.slug, self.module._known_listings)

        client.fail_preview = False
        client.fail_message = False
        sent = await self.module._check_market(scan_all=True)
        self.assertEqual(sent, 1)
        self.assertIn(new.slug, self.module._known_listings)

    async def test_all_resale_collections_are_scanned_for_future_price_drops(self):
        catalog = [
            _base(1, resale=0, minimum=1),
            _base(2, resale=5, minimum=500),
            _base(3, resale=7, minimum=10),
        ]
        client = await self._ready(
            [_Catalog(catalog), _Resale([]), _Resale([])]
        )

        await self.module._check_market(force_catalog=True, scan_all=True)

        resale_requests = [
            request
            for request in client.requests
            if hasattr(request, "gift_id")
        ]
        self.assertEqual([request.gift_id for request in resale_requests], [2, 3])
        self.assertEqual(self.module._catalog_count, 3)
        self.assertEqual(self.module._resale_collections_count, 2)
        self.assertEqual(self.module._eligible_collections_count, 1)

    def test_collection_batches_rotate_without_skipping(self):
        collections = [_base(value) for value in range(1, 6)]
        self.module.config["collections_per_check"] = 2

        first = self.module._select_collection_batch(collections)
        second = self.module._select_collection_batch(collections)
        third = self.module._select_collection_batch(collections)

        self.assertEqual([item.id for item in first], [1, 2])
        self.assertEqual([item.id for item in second], [3, 4])
        self.assertEqual([item.id for item in third], [5, 1])

    def test_stars_and_ton_prices_are_parsed_and_formatted(self):
        listing = _nft(
            401,
            stars=12,
            nanos=500_000_000,
            ton=1_250_000_000,
        )

        stars, ton = self.module._extract_prices(listing)

        self.assertEqual(stars, 12_500_000_000)
        self.assertEqual(ton, 1_250_000_000)
        self.assertEqual(self.module._format_price(listing), "12.5 ⭐ / 1.25 TON")

    def test_market_item_escapes_text_and_uses_official_nft_link(self):
        listing = _nft(
            501,
            slug="Gift Name/5",
            title="<Rare & Gift>",
            stars=10,
            model="<Gold>",
        )

        text = self.module._format_market_item(listing, 1)

        self.assertIn("https://t.me/nft/Gift%20Name%2F5", text)
        self.assertIn("&lt;Rare &amp; Gift&gt;", text)
        self.assertIn("&lt;Gold&gt;", text)
        self.assertNotIn("<Rare & Gift>", text)

    async def test_market_command_supports_range_and_all_modes(self):
        stars_listing = _nft(601, stars=50)
        ton_listing = _nft(602, stars=None, ton=2_000_000_000)
        client = await self._ready(
            [
                _Catalog([_base(1)]),
                _Resale([stars_listing, ton_listing], count=2),
                _Catalog([_base(1)]),
                _Resale([stars_listing, ton_listing], count=2),
            ]
        )

        range_message = _Message()
        await self.module.gmarket(range_message)
        all_message = _Message("all")
        await self.module.gmarket(all_message)

        self.assertIn("ціна 1–150 ⭐", range_message.answers[-1])
        self.assertIn(stars_listing.slug, range_message.answers[-1])
        self.assertNotIn(ton_listing.slug, range_message.answers[-1])
        self.assertIn("усі ціни та валюти", all_message.answers[-1])
        self.assertIn(ton_listing.slug, all_message.answers[-1])
        resale_requests = [
            request
            for request in client.requests
            if hasattr(request, "sort_by_price")
        ]
        self.assertTrue(all(request.sort_by_price for request in resale_requests))

    async def test_hikka_loop_runs_due_round_robin_scan(self):
        listing = _nft(701, stars=50)
        client = await self._ready(
            [_Catalog([_base(1)]), _Resale([listing])]
        )
        self.module.set("enabled", True)
        self.module._next_check_at = 0.0

        await self.module.gift_monitor_loop()

        self.assertEqual(self.module._checks_count, 1)
        self.assertEqual(self.module._last_scanned_collections, 1)
        self.assertGreater(self.module._next_check_at, 0.0)
        self.assertEqual(len(client.requests), 2)

    async def test_one_failed_collection_does_not_hide_successful_results(self):
        catalog = [_base(1), _base(2)]
        listing = _nft(801, collection_id=2, stars=50)
        client = await self._ready(
            [
                _Catalog(catalog),
                RuntimeError("temporary flood wait"),
                _Resale([listing]),
            ]
        )

        sent = await self.module._check_market(
            force_catalog=True,
            scan_all=True,
        )

        self.assertEqual(sent, 0)
        self.assertEqual(self.module._last_scanned_collections, 1)
        self.assertEqual(self.module._request_errors, 1)
        self.assertIn("1 колекцій не перевірено", self.module._last_error)


if __name__ == "__main__":
    unittest.main()
