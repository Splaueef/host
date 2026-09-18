"""Tests for the SessionGuard Hikka module."""

import asyncio
import datetime  # Load stdlib math before the repository's math.py can shadow it.
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value

    def get_prefix(self):
        return "."


class _Validators:
    Boolean = staticmethod(lambda: object())
    Integer = staticmethod(lambda **kwargs: object())


class _RPCError(Exception):
    pass


class _FloodWaitError(_RPCError):
    def __init__(self, seconds):
        super().__init__(seconds)
        self.seconds = seconds


class _GetAuthorizationsRequest:
    pass


class _ResetAuthorizationRequest:
    def __init__(self, hash):
        self.hash = hash


class _UpdateNewAuthorization:
    def __init__(self, hash, device="Phone", location="Berlin"):
        self.hash = hash
        self.device = device
        self.location = location


class _Authorization:
    def __init__(self, hash, current=False, device="Phone"):
        self.hash = hash
        self.current = current
        self.device_model = device
        self.platform = "Android"
        self.system_version = "15"
        self.app_name = "Telegram"
        self.app_version = "12.0"
        self.ip = "192.0.2.1"
        self.country = "Germany"
        self.region = "Berlin"


class _Authorizations:
    def __init__(self, authorizations):
        self.authorizations = authorizations


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = _Module
    loader.validators = _Validators

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.loop = lambda **kwargs: lambda function: function
    loader.raw_handler = lambda *args, **kwargs: lambda function: function
    loader.command = lambda *args, **kwargs: lambda function: function
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    utils.escape_html = lambda value: str(value)
    utils.answer = mock.AsyncMock()
    utils.get_args_raw = lambda message: getattr(message, "args", "")
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    account = types.ModuleType("telethon.tl.functions.account")
    tl_types = types.ModuleType("telethon.tl.types")
    errors.FloodWaitError = _FloodWaitError
    errors.RPCError = _RPCError
    account.GetAuthorizationsRequest = _GetAuthorizationsRequest
    account.ResetAuthorizationRequest = _ResetAuthorizationRequest
    tl_types.UpdateNewAuthorization = _UpdateNewAuthorization

    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.errors": errors,
            "telethon.tl": tl,
            "telethon.tl.functions": functions,
            "telethon.tl.functions.account": account,
            "telethon.tl.types": tl_types,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "sessionguard.py"
    spec = importlib.util.spec_from_file_location("testhost.modules.sessionguard", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sessionguard = _load_module()


class _Client:
    def __init__(self, authorizations, reset_error=None):
        self.authorizations = list(authorizations)
        self.reset_error = reset_error
        self.reset_calls = []
        self.send_message = mock.AsyncMock()

    async def __call__(self, request):
        if isinstance(request, _GetAuthorizationsRequest):
            return _Authorizations(self.authorizations)
        if isinstance(request, _ResetAuthorizationRequest):
            self.reset_calls.append(request.hash)
            if self.reset_error:
                raise self.reset_error
            self.authorizations = [
                item for item in self.authorizations if item.hash != request.hash
            ]
            return True
        raise AssertionError(type(request))


class SessionGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sessionguard.utils.answer.reset_mock()
        self.module = sessionguard.SessionGuardMod()

    async def test_first_load_trusts_all_existing_sessions(self):
        client = _Client(
            [_Authorization(10, current=True), _Authorization(20, device="Laptop")]
        )

        await self.module.client_ready(client, None)

        self.assertEqual(self.module._trusted_hashes(), {"10", "20"})
        self.assertEqual(client.reset_calls, [])
        self.assertTrue(self.module.get("baseline_initialized"))

    async def test_audit_revokes_only_unknown_non_current_session(self):
        self.module.set("baseline_initialized", True)
        self.module.set("trusted_hashes", ["10", "20"])
        client = _Client(
            [
                _Authorization(10, current=True),
                _Authorization(20, device="Laptop"),
                _Authorization(30, device="Intruder"),
            ]
        )

        await self.module.client_ready(client, None)

        self.assertEqual(client.reset_calls, [30])
        self.assertEqual(self.module.get("revoked_count"), 1)
        self.assertIn("завершив невідому", client.send_message.await_args.args[1])

    async def test_current_hikka_session_is_always_added_to_trusted(self):
        self.module.set("baseline_initialized", True)
        self.module.set("trusted_hashes", ["20"])
        client = _Client([_Authorization(99, current=True)])

        await self.module.client_ready(client, None)

        self.assertEqual(self.module._trusted_hashes(), {"20", "99"})
        self.assertEqual(client.reset_calls, [])

    async def test_raw_update_is_revoked_immediately(self):
        self.module.set("baseline_initialized", True)
        self.module.set("trusted_hashes", ["10"])
        client = _Client([_Authorization(10, current=True)])
        self.module._client = client

        await self.module.new_authorization_handler(
            _UpdateNewAuthorization(42, device="Unknown phone")
        )

        self.assertEqual(client.reset_calls, [42])
        self.assertEqual(self.module.get("revoked_count"), 1)

    async def test_failed_revoke_is_retried_without_counting_success(self):
        self.module.set("baseline_initialized", True)
        self.module.set("trusted_hashes", ["10"])
        client = _Client(
            [_Authorization(10, current=True), _Authorization(30)],
            reset_error=_RPCError("FRESH_RESET_AUTHORISATION_FORBIDDEN"),
        )
        self.module._client = client

        success = await self.module._audit_sessions()

        self.assertFalse(success)
        self.assertEqual(client.reset_calls, [30])
        self.assertEqual(self.module.get("revoked_count", 0), 0)
        self.assertIn("30", self.module._retry_after)
        self.assertIn("не дозволив", client.send_message.await_args.args[1])

    async def test_trustall_requires_exact_confirmation(self):
        client = _Client([_Authorization(10, current=True), _Authorization(20)])
        self.module._client = client

        await self.module.sessionguard(types.SimpleNamespace(args="trustall"))
        self.assertFalse(self.module.get("baseline_initialized", False))

        await self.module.sessionguard(
            types.SimpleNamespace(args="trustall CONFIRM")
        )
        self.assertEqual(self.module._trusted_hashes(), {"10", "20"})


if __name__ == "__main__":
    unittest.main()
