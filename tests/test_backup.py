"""Tests for the portable Hikka backup format."""

import importlib.util
import io
import pathlib
import shutil
import sys
import types
import unittest
import zipfile


class _Module:
    def lookup(self, name):
        return self._loader


class _Database(dict):
    def process_db_autofix(self, value):
        return isinstance(value, dict)


class _LoaderModule:
    def __init__(self, modules):
        self.modules = modules

    def get(self, key, default=None):
        return self.modules if key == "loaded_modules" else default


def _load_module(module_directory):
    package = types.ModuleType("backuphost")
    package.__path__ = []
    modules = types.ModuleType("backuphost.modules")
    modules.__path__ = []
    loader = types.ModuleType("backuphost.loader")
    utils = types.ModuleType("backuphost.utils")

    loader.Module = _Module
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: lambda value: value
    loader.LOADED_MODULES_PATH = module_directory
    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_types = types.ModuleType("telethon.tl.types")
    telethon_types.Message = type("Message", (), {})
    package.loader = loader
    package.utils = utils
    sys.modules.update(
        {
            "backuphost": package,
            "backuphost.modules": modules,
            "backuphost.loader": loader,
            "backuphost.utils": utils,
            "telethon": telethon,
            "telethon.tl": telethon_tl,
            "telethon.tl.types": telethon_types,
        }
    )

    path = pathlib.Path(__file__).parents[1] / "backup.py"
    spec = importlib.util.spec_from_file_location("backuphost.modules.backup", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FullBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(f"/tmp/hikka-backup-test-{id(self)}")
        self.directory.mkdir()
        self.backup = _load_module(self.directory)
        self.instance = self.backup.FullBackupMod()
        self.instance.tg_id = 42
        self.instance._db = _Database({"Example": {"setting": True}})
        self.instance._loader = _LoaderModule(
            {"ExampleMod": "https://example.com/example.py"}
        )

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_archive_contains_database_links_and_account_modules(self):
        (self.directory / "example42.py").write_text("value = 42\n")
        (self.directory / "another-account7.py").write_text("value = 7\n")

        archive, count = self.instance._make_archive()
        database, links, modules = self.instance._validate_archive(archive.getvalue())

        self.assertEqual(count, 2)
        self.assertEqual(database, {"Example": {"setting": True}})
        self.assertEqual(
            links, {"ExampleMod": "https://example.com/example.py"}
        )
        self.assertEqual(modules, {"example42.py": b"value = 42\n"})

    def test_rejects_wrong_format(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("manifest.json", '{"format": "foreign", "version": 1}')
            archive.writestr("database.json", "{}")
            archive.writestr("module_links.json", "{}")

        with self.assertRaisesRegex(ValueError, "невідомий формат"):
            self.instance._validate_archive(payload.getvalue())

    def test_rejects_oversized_uncompressed_archive(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"x" * (101 * 1024 * 1024))

        with self.assertRaisesRegex(ValueError, "розпаковані дані завеликі"):
            self.instance._validate_archive(payload.getvalue())


if __name__ == "__main__":
    unittest.main()
