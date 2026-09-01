"""Regression tests for NekoSpy's local ephemeral-media backups."""

import importlib.util
import io
import pathlib
import sys

_REPO = pathlib.Path(__file__).parents[1]
sys.path[:] = [entry for entry in sys.path if entry not in ("", str(_REPO))]

import tempfile
import types
import unittest


def _decorator(*args, **kwargs):
    return lambda value: value


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    loader.Module = object
    loader.tds = lambda value: value
    loader.command = _decorator
    loader.loop = _decorator
    loader.raw_handler = _decorator
    loader.watcher = _decorator
    utils = types.ModuleType("testhost.utils")
    utils.get_chat_id = lambda message: message.chat_id

    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_types = types.ModuleType("telethon.tl.types")
    for name in (
        "DocumentAttributeFilename",
        "Message",
        "PeerChat",
        "UpdateDeleteChannelMessages",
        "UpdateDeleteMessages",
        "UpdateEditChannelMessage",
        "UpdateEditMessage",
    ):
        setattr(telethon_types, name, type(name, (), {}))
    telethon_utils = types.ModuleType("telethon.utils")
    telethon_utils.get_display_name = str

    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.tl": telethon_tl,
            "telethon.tl.types": telethon_types,
            "telethon.utils": telethon_utils,
        }
    )
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.nekospy", _REPO / "nekospy.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nekospy = _load_module()


class LocalBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.module = object.__new__(nekospy.NekoSpy)
        self.module._backup_root = (
            pathlib.Path(self.temporary_directory.name) / "NekoSpyBSP"
        )
        self.module._prepare_backup_directories()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_prepare_creates_sent_and_received_directories(self):
        self.assertEqual(
            {path.name for path in self.module._backup_root.iterdir()},
            {"sent", "received"},
        )

    def test_backup_separates_directions_and_preserves_stream_position(self):
        for outgoing, directory_name in ((True, "sent"), (False, "received")):
            media = io.BytesIO(b"ephemeral media")
            media.name = "../../unsafe:name.jpg"
            media.seek(4)
            message = types.SimpleNamespace(out=outgoing, id=42, chat_id=-1007)

            self.module._backup_ephemeral_media(message, media)

            backups = list(self.module._backup_dirs[directory_name].iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"ephemeral media")
            self.assertNotIn(":", backups[0].name)
            self.assertEqual(media.tell(), 4)

    def test_ephemeral_detection_accepts_zero_ttl_for_view_once_media(self):
        message = types.SimpleNamespace(
            media=types.SimpleNamespace(ttl_seconds=0),
        )

        self.assertTrue(self.module._is_ephemeral_media(message))

    def test_ephemeral_detection_rejects_regular_media(self):
        message = types.SimpleNamespace(media=types.SimpleNamespace())

        self.assertFalse(self.module._is_ephemeral_media(message))

    def test_round_video_detection_uses_current_media_flag(self):
        message = types.SimpleNamespace(
            video_note=None,
            media=types.SimpleNamespace(round=True, ttl_seconds=0),
            document=None,
        )

        self.assertTrue(self.module._is_round_video(message))
        self.assertEqual(self.module._media_name(message), "round_video.mp4")

    def test_round_video_detection_supports_document_attribute(self):
        message = types.SimpleNamespace(
            video_note=None,
            media=types.SimpleNamespace(),
            document=types.SimpleNamespace(
                attributes=[types.SimpleNamespace(round_message=True)],
            ),
        )

        self.assertTrue(self.module._is_round_video(message))

    def test_regular_video_is_not_detected_as_round(self):
        message = types.SimpleNamespace(
            video_note=None,
            media=types.SimpleNamespace(round=False),
            document=types.SimpleNamespace(
                attributes=[types.SimpleNamespace(round_message=False)],
            ),
        )

        self.assertFalse(self.module._is_round_video(message))


if __name__ == "__main__":
    unittest.main()
