import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class _Validator:
    def __init__(self, *args, **kwargs):
        pass


class _ConfigValue:
    def __init__(self, name, default, doc, validator=None):
        self.name = name
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.name, value.default) for value in values)


def _decorator(*args, **kwargs):
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]
    return lambda value: value


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.ModuleConfig = _ModuleConfig
    loader.ConfigValue = _ConfigValue
    loader.tds = _decorator
    loader.command = _decorator
    loader.validators = types.SimpleNamespace(
        String=_Validator,
        Hidden=_Validator,
        Integer=_Validator,
        Boolean=_Validator,
        Series=_Validator,
    )
    utils.get_args_raw = lambda message: ""
    utils.answer = None
    sys.modules["testhost"] = package
    sys.modules["testhost.modules"] = modules
    sys.modules["testhost.loader"] = loader
    sys.modules["testhost.utils"] = utils
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.hikkanet", ROOT / "hikkanet.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hikkanet = _load_module()


class HikkaNetHelperTests(unittest.TestCase):
    def test_signature_matches_server_implementation(self):
        from hikka_hub.security import sign_with_secret

        arguments = (
            "s" * 48,
            "POST",
            "/v1/events?limit=10",
            "1700000000",
            "nonce0123456789012345",
            "hikka-main",
            "123456789",
            "0" * 64,
        )
        self.assertEqual(hikkanet._sign(*arguments), sign_with_secret(*arguments))

    def test_server_url_validation(self):
        self.assertEqual(
            hikkanet._normalise_server_url("http://127.0.0.1:8765/"),
            "http://127.0.0.1:8765",
        )
        self.assertEqual(
            hikkanet._normalise_server_url("https://hub.example.com"),
            "https://hub.example.com",
        )
        for invalid in (
            "ftp://127.0.0.1",
            "http://user:pass@127.0.0.1",
            "http://127.0.0.1/api",
            "http://127.0.0.1?secret=x",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                hikkanet._normalise_server_url(invalid)

    def test_sensitive_values_are_rejected_before_publish(self):
        self.assertEqual(
            hikkanet._find_sensitive_field({"profile": {"api_key": "x"}}),
            "profile.api_key",
        )
        self.assertEqual(
            hikkanet._find_sensitive_field([{"telegram_token": "x"}]),
            "[0].telegram_token",
        )
        self.assertEqual(hikkanet._find_sensitive_field({"version": 1}), "")

    def test_module_has_hidden_secret_and_expected_defaults(self):
        module = hikkanet.HikkaNetMod()
        self.assertEqual(module.config["server_url"], "http://127.0.0.1:8765")
        self.assertEqual(module.config["heartbeat_interval"], 30)
        self.assertEqual(module.config["key_secret"], "")
        for method in (
            "api_publish",
            "api_events",
            "api_instances",
            "api_module_versions",
            "api_get",
            "api_put",
            "api_delete",
            "api_increment",
            "api_increment_many",
            "api_stats",
        ):
            self.assertTrue(callable(getattr(module, method)))


if __name__ == "__main__":
    unittest.main()
