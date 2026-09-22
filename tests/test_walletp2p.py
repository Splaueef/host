"""Wallet P2P read-only client and presentation tests."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal

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
    package = types.ModuleType("p2phost")
    package.__path__ = []
    modules = types.ModuleType("p2phost.modules")
    modules.__path__ = []
    loader = types.ModuleType("p2phost.loader")
    utils = types.ModuleType("p2phost.utils")
    loader.Module = type("Module", (), {})
    loader.ModuleConfig, loader.ConfigValue = _Config, _Value
    loader.tds = lambda klass: klass
    loader.validators = types.SimpleNamespace(
        String=_Validator, Hidden=_Validator,
    )
    utils.get_args_raw = lambda message: message.args

    async def answer(message, value):
        message.answers.append(value)

    utils.answer = answer
    sys.modules.update({"p2phost": package, "p2phost.modules": modules,
                        "p2phost.loader": loader, "p2phost.utils": utils})
    spec = importlib.util.spec_from_file_location("p2phost.modules.walletp2p", ROOT / "walletp2p.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


walletp2p = _load()


class _Response:
    def __init__(self, status=200, data=None):
        self.status = status
        self.data = data or {"status": "SUCCESS", "data": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        return self.data


class _Session:
    closed = False

    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response

    async def close(self):
        self.closed = True


class WalletP2PTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = walletp2p.WalletP2PMod()
        self.module.config["api_key"] = "private-test-key"

    def test_side_is_from_user_viewpoint(self):
        self.assertEqual(self.module._params("buy USDT EUR")[0], "SELL")
        self.assertEqual(self.module._params("sell USDT UAH 100")[0], "BUY")
        self.assertEqual(self.module._params("купити BTC USD")[0], "SELL")
        with self.assertRaises(ValueError):
            self.module._params("buy USDT EUR -5")

    async def test_authenticated_request_cache_and_key_rotation(self):
        session = _Session(_Response(data={"status": "SUCCESS", "data": [{"price": "1.00"}]}))
        self.module._session = session
        first = await self.module._request("SELL", "USDT", "EUR", 1)
        self.assertEqual(first[0]["price"], "1.00")
        self.assertEqual(session.posts[0][0], walletp2p.API_URL)
        self.assertEqual(session.posts[0][1]["json"], {
            "cryptoCurrency": "USDT", "fiatCurrency": "EUR",
            "side": "SELL", "page": 1, "pageSize": 10,
        })
        self.assertEqual(session.posts[0][1]["headers"]["X-API-Key"], "private-test-key")
        await self.module._request("SELL", "USDT", "EUR", 1)
        self.assertEqual(len(session.posts), 1)
        self.module.config["api_key"] = "new-key"
        await self.module._request("SELL", "USDT", "EUR", 1)
        self.assertEqual(len(session.posts), 2)
        self.assertEqual(session.posts[-1][1]["headers"]["X-API-Key"], "new-key")
        await self.module.on_unload()
        self.assertTrue(session.closed)

    async def test_invalid_responses_do_not_expose_secret(self):
        for response in (_Response(status=401), _Response(status=429),
                         _Response(data={"status": "ERROR", "data": []})):
            self.module._session = _Session(response)
            with self.assertRaises(walletp2p.WalletAPIError) as exc:
                await self.module._request("BUY", "USDT", "EUR", 1)
            self.assertNotIn("private-test-key", str(exc.exception))

    def test_amount_filter_and_untrusted_fields(self):
        items = [
            {"price": "0.98", "minAmount": "50", "maxAmount": "200", "id": "<id>",
             "nickname": "<script>", "payments": ["<bank>"], "executeRate": "0.98"},
            {"price": "0.90", "minAmount": "500", "maxAmount": "1000"},
        ]
        selected = self.module._eligible(items, Decimal("100"))
        self.assertEqual(len(selected), 1)
        output = self.module._render(items, "SELL", "USDT", "EUR", Decimal("100"), 1)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("&lt;bank&gt;", output)
        self.assertNotIn("<script>", output)
        self.assertNotIn("0.90", output)
        self.assertIn("98%", output)

    async def test_command_does_not_trade(self):
        class Message:
            args = "sell USDT EUR 100"
            answers = []

        message = Message()
        session = _Session(_Response())
        self.module._session = session
        self.module.inline = types.SimpleNamespace(form=lambda *args, **kwargs: _opened())

        async def _opened():
            return True

        await self.module.p2pcmd(message)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.posts[0][1]["json"]["side"], "BUY")
        self.assertEqual(session.posts[0][0], walletp2p.API_URL)


if __name__ == "__main__":
    unittest.main()
