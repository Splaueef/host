"""TonWallet import, signing and confirmation tests (no network)."""

import importlib.util
import pathlib
import sys
import time
import types
import unittest

import nacl.signing
from ton_core import (
    Address,
    SignatureDomain,
    begin_cell,
    multichain_mnemonic_new,
    ton_mnemonic_new,
)
from tonutils.clients import TonapiClient
from tonutils.types import ContractInfo

ROOT = pathlib.Path(__file__).parents[1]


class _Validator:
    def __init__(self, *args, **kwargs):
        pass


class _Value:
    def __init__(self, name, default, description, validator=None):
        self.name, self.default = name, default


class _Config(dict):
    def __init__(self, *values):
        super().__init__((v.name, v.default) for v in values)


def _load():
    package = types.ModuleType("tonhost")
    package.__path__ = []
    modules = types.ModuleType("tonhost.modules")
    modules.__path__ = []
    inline = types.ModuleType("tonhost.inline")
    inline.__path__ = []
    inline_types = types.ModuleType("tonhost.inline.types")
    inline_types.InlineCall = object
    loader = types.ModuleType("tonhost.loader")
    utils = types.ModuleType("tonhost.utils")

    loader.Module = type("Module", (), {})
    loader.ModuleConfig, loader.ConfigValue = _Config, _Value
    loader.tds = lambda klass: klass
    loader.command = lambda *args, **kwargs: (lambda func: func)
    loader.validators = types.SimpleNamespace(
        Hidden=_Validator, Choice=_Validator, Float=_Validator,
    )
    utils.get_args_raw = lambda message: message.args
    utils.escape_html = lambda text: (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.rand = lambda size: "x" * size + str(time.monotonic_ns())

    async def answer(message, value):
        message.answers.append(value)

    utils.answer = answer
    sys.modules.update({
        "tonhost": package, "tonhost.modules": modules, "tonhost.inline": inline,
        "tonhost.inline.types": inline_types, "tonhost.loader": loader, "tonhost.utils": utils,
    })
    spec = importlib.util.spec_from_file_location("tonhost.modules.tonwallet", ROOT / "tonwallet.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tonwallet = _load()


class _Message:
    def __init__(self, args=""):
        self.args, self.answers, self.peer_id, self.deleted = args, [], 1, False

    async def delete(self):
        self.deleted = True


class _Telegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, peer, text, parse_mode=None):
        self.sent.append(text)


class _Inline:
    def __init__(self):
        self.forms = []

    async def form(self, message, text, reply_markup):
        self.forms.append((text, reply_markup))


class _Call:
    def __init__(self):
        self.edits = []

    async def edit(self, text):
        self.edits.append(text)


class TonWalletTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = tonwallet.TonWalletMod()
        self.mod.strings = tonwallet.TonWalletMod.strings.__getitem__
        self.mod._client = _Telegram()
        self.mod.inline = _Inline()
        self.sent = []
        self.api = {}

        async def fake_api(path, **params):
            return self.api[path]

        self.mod._api = fake_api
        patches = {
            "connect": self._noop, "close": self._noop,
            "_send_message": self._send, "_get_info": self._info,
        }
        self._saved = {name: getattr(TonapiClient, name) for name in patches}
        for name, func in patches.items():
            setattr(TonapiClient, name, func)

    def tearDown(self):
        for name, func in self._saved.items():
            setattr(TonapiClient, name, func)

    @staticmethod
    async def _noop(client):
        pass

    @staticmethod
    async def _info(client, address):
        return ContractInfo(balance=5_000_000_000)

    async def _send(self_client, boc):
        _SENT.append(boc)

    def _use_tg_wallet(self, balance=5_000_000_000):
        self.mod.config["mnemonic"] = " ".join(multichain_mnemonic_new(12))
        self.mod.config["wallet_type"] = "tg"
        address = self.mod._address()
        self.api[f"/accounts/{address}"] = {"balance": balance, "status": "uninit"}
        return address

    async def test_import_detects_wallet_type_and_deletes_phrase(self):
        cases = [
            (multichain_mnemonic_new(12), "tg"),
            (multichain_mnemonic_new(12) + multichain_mnemonic_new(12), "tg"),
            (ton_mnemonic_new(24), "w5"),
        ]
        for words, kind in cases:
            message = _Message(" ".join(words).upper())
            await self.mod.tonimport(message)
            self.assertTrue(message.deleted)
            self.assertEqual(self.mod.config["wallet_type"], kind)
            self.assertEqual(self.mod.config["mnemonic"], " ".join(words))
            self.assertIn("✅", self.mod._client.sent[-1])

    async def test_import_rejects_invalid_phrase(self):
        message = _Message("foo bar baz")
        await self.mod.tonimport(message)
        self.assertTrue(message.deleted)
        self.assertEqual(self.mod.config["mnemonic"], "")
        self.assertIn("❌", self.mod._client.sent[-1])

    def test_telegram_wallet_address_matches_trampoline_state_init(self):
        self.mod.config["mnemonic"] = " ".join(multichain_mnemonic_new(12))
        wallet = self.mod._wallet(self.mod._ton())
        trampoline = wallet.state_init.code
        data = (
            begin_cell().store_uint(0, 8).store_uint(0, 32).store_uint(0x7FFF7F11, 32)
            .store_bytes(wallet.public_key.as_bytes).end_cell()
        )
        self.assertEqual(wallet.state_init.data.hash, data.hash)
        self.assertEqual(wallet.address.hash_part, type(wallet.state_init)(code=trampoline, data=data).serialize().hash)

    def test_address_mismatch_is_rejected(self):
        self.mod.config["mnemonic"] = " ".join(multichain_mnemonic_new(12))
        self.mod.config["address"] = "0:" + "11" * 32
        with self.assertRaises(tonwallet.TonError):
            self.mod._wallet(self.mod._ton())

    def test_amount_helpers(self):
        self.assertEqual(tonwallet.parse_amount("1,5", 9), 1_500_000_000)
        self.assertEqual(tonwallet.parse_amount("2.25", 6), 2_250_000)
        self.assertEqual(tonwallet.fmt_amount(1_500_000_000, 9), "1.5")
        self.assertEqual(tonwallet.fmt_amount(5_000_000_000, 9), "5")
        for bad in ("0", "-1", "abc", "0.0000000001"):
            with self.assertRaises(tonwallet.TonError):
                tonwallet.parse_amount(bad, 9)

    async def test_usage_is_html_escaped(self):
        message = _Message("")
        await self.mod.tonsend(message)
        self.assertIn("&lt;адреса&gt;", message.answers[-1])

    async def test_send_over_limit_is_refused(self):
        address = self._use_tg_wallet()
        message = _Message(f"{address} 1000")
        await self.mod.tonsend(message)
        self.assertIn("max_amount", message.answers[-1])
        self.assertEqual(self.mod.inline.forms, [])

    async def test_send_needs_balance(self):
        address = self._use_tg_wallet(balance=1_000_000_000)
        message = _Message(f"{address} 1")
        await self.mod.tonsend(message)
        self.assertIn("Недостатньо", message.answers[-1])

    async def test_confirmed_telegram_wallet_transfer_is_signed_once(self):
        _SENT.clear()
        address = self._use_tg_wallet()
        await self.mod.tonsend(_Message(f"{address} 1.5 привіт"))
        text, markup = self.mod.inline.forms[-1]
        self.assertIn("1.5 GRAM", text)
        args = markup[0][0]["args"]

        call = _Call()
        await self.mod._do_send(call, *args)
        await self.mod._do_send(call, *args)
        self.assertEqual(len(_SENT), 1)
        self.assertIn("✅", call.edits[-1])

        wallet = self.mod._wallet(self.mod._ton())
        ext = await wallet.build_external_message([args[0]])
        body = ext.body.begin_parse()
        signature = body.load_bytes(64)
        request = body.to_cell()
        header = request.begin_parse()
        self.assertEqual(header.load_uint(32), 0x63896E75)
        self.assertEqual(header.load_uint(32), 0x7FFF7F11)
        domain = SignatureDomain(wallet.client.network)
        nacl.signing.VerifyKey(wallet.public_key.as_bytes).verify(
            domain.data_to_sign(request.hash), signature
        )

    async def test_expired_confirmation_is_not_sent(self):
        _SENT.clear()
        address = self._use_tg_wallet()
        await self.mod.tonsend(_Message(f"{address} 1"))
        builder, form_id, _ = self.mod.inline.forms[-1][1][0][0]["args"]
        call = _Call()
        await self.mod._do_send(call, builder, form_id, time.time() - 1)
        self.assertEqual(_SENT, [])
        self.assertIn("⌛", call.edits[-1])

    async def test_jetton_transfer_uses_known_wallet_and_checks_balance(self):
        address = self._use_tg_wallet()
        self.api[f"/accounts/{address}/jettons"] = {"balances": [{
            "balance": "12500000",
            "jetton": {"address": "0:" + "ab" * 32, "symbol": "USDT", "decimals": 6},
            "wallet_address": {"address": "0:" + "cd" * 32},
        }]}
        message = _Message(f"{address} 99 usdt")
        await self.mod.tonjsend(message)
        self.assertIn("12.5 USDT", message.answers[-1])

        await self.mod.tonjsend(_Message(f"{address} 2.5 USDT дякую"))
        text, markup = self.mod.inline.forms[-1]
        self.assertIn("2.5 USDT", text)
        builder = markup[0][0]["args"][0]
        self.assertEqual(builder.jetton_amount, 2_500_000)
        self.assertEqual(Address(builder.jetton_wallet_address), Address("0:" + "cd" * 32))

    async def test_nft_of_another_owner_is_refused(self):
        address = self._use_tg_wallet()
        nft = "0:" + "ef" * 32
        self.api[f"/accounts/{address}/nfts"] = {"nft_items": [
            {"address": nft, "metadata": {"name": "Plush Pepe"}, "collection": {"name": "Gifts"}},
        ]}
        listing = _Message()
        await self.mod.tonnfts(listing)
        self.assertIn("Plush Pepe", listing.answers[-1])

        self.api[f"/nfts/{tonwallet.friendly(nft)}"] = {"owner": {"address": "0:" + "22" * 32}}
        message = _Message(f"1 {address}")
        await self.mod.tonnft(message)
        self.assertIn("не знайдено", message.answers[-1])

        self.api[f"/nfts/{tonwallet.friendly(nft)}"] = {"owner": {"address": address}, "metadata": {"name": "Plush Pepe"}}
        await self.mod.tonnft(_Message(f"1 {address}"))
        self.assertIn("Plush Pepe", self.mod.inline.forms[-1][0])


_SENT = []


if __name__ == "__main__":
    unittest.main()
