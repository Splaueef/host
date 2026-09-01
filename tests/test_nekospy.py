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
from unittest import mock


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

    aiogram = types.ModuleType("aiogram")
    aiogram_types = types.ModuleType("aiogram.types")

    class BufferedInputFile:
        def __init__(self, file, filename, chunk_size=65536):
            self.data = file
            self.filename = filename
            self.chunk_size = chunk_size

    aiogram_types.BufferedInputFile = BufferedInputFile

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
            "aiogram": aiogram,
            "aiogram.types": aiogram_types,
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


class LocalBackupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.module = object.__new__(nekospy.NekoSpy)
        self.module._backup_root = (
            pathlib.Path(self.temporary_directory.name) / "NekoSpyBSP"
        )
        self.module._prepare_backup_directories()
        self.module._queue = []
        self.module._cache = {}
        self.module._media_cache = {}
        self.module._media_cache_bytes = 0

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

    async def test_round_video_falls_back_to_document_when_rejected(self):
        media = io.BytesIO(b"round video")
        media.name = "round_video.mp4"
        bot = types.SimpleNamespace(
            send_video_note=mock.AsyncMock(side_effect=RuntimeError("rejected")),
            send_document=mock.AsyncMock(),
        )
        self.module.inline = types.SimpleNamespace(bot=bot)
        self.module._channel = -10042

        await self.module._send_round_video(media)

        video_note = bot.send_video_note.await_args.kwargs
        self.assertEqual(video_note["chat_id"], -10042)
        self.assertEqual(video_note["video_note"].data, b"round video")
        self.assertEqual(video_note["video_note"].filename, "round_video.mp4")

        document = bot.send_document.await_args.kwargs
        self.assertEqual(document["chat_id"], -10042)
        self.assertEqual(document["document"].data, b"round video")
        self.assertEqual(document["document"].filename, "round_video.mp4")
        self.assertEqual(media.tell(), 0)

    async def test_round_video_does_not_duplicate_successful_upload(self):
        media = io.BytesIO(b"round video")
        bot = types.SimpleNamespace(
            send_video_note=mock.AsyncMock(),
            send_document=mock.AsyncMock(),
        )
        self.module.inline = types.SimpleNamespace(bot=bot)
        self.module._channel = -10042

        await self.module._send_round_video(media)

        video_note = bot.send_video_note.await_args.kwargs
        self.assertEqual(video_note["video_note"].data, b"round video")
        bot.send_document.assert_not_awaited()

    async def test_photo_falls_back_to_document_when_rejected(self):
        media = io.BytesIO(b"ephemeral photo")
        media.name = "photo.jpg"
        bot = types.SimpleNamespace(
            send_photo=mock.AsyncMock(side_effect=RuntimeError("rejected")),
            send_document=mock.AsyncMock(),
        )
        self.module.inline = types.SimpleNamespace(bot=bot)
        self.module._channel = -10042

        await self.module._send_photo(media, "sender")

        photo = bot.send_photo.await_args.kwargs
        self.assertEqual(photo["chat_id"], -10042)
        self.assertEqual(photo["caption"], "sender")
        self.assertEqual(photo["photo"].data, b"ephemeral photo")
        self.assertEqual(photo["photo"].filename, "photo.jpg")

        document = bot.send_document.await_args.kwargs
        self.assertEqual(document["chat_id"], -10042)
        self.assertEqual(document["caption"], "sender")
        self.assertEqual(document["document"].data, b"ephemeral photo")
        self.assertEqual(document["document"].filename, "photo.jpg")
        self.assertEqual(media.tell(), 0)

    async def test_enqueued_photo_is_lazy_and_uses_buffered_input_file(self):
        media = io.BytesIO(b"ephemeral photo")
        media.name = "photo.jpg"
        message = types.SimpleNamespace(photo=object())
        bot = types.SimpleNamespace(
            send_photo=mock.AsyncMock(),
            send_document=mock.AsyncMock(),
        )
        self.module.inline = types.SimpleNamespace(bot=bot)
        self.module._channel = -10042
        self.module._next = 0
        self.module.config = {"fw_protect": 0}

        self.module._enqueue_media(message, "sender", media)

        self.assertEqual(len(self.module._queue), 1)
        self.assertTrue(callable(self.module._queue[0]))
        bot.send_photo.assert_not_awaited()

        await self.module.sender()

        upload = bot.send_photo.await_args.kwargs["photo"]
        self.assertEqual(upload.data, b"ephemeral photo")
        self.assertEqual(upload.filename, "photo.jpg")

    async def test_incoming_view_once_photo_is_logged_while_spy_mode_is_off(self):
        sender = types.SimpleNamespace(id=99, first_name="Sender")
        message = types.SimpleNamespace(
            id=7,
            chat_id=99,
            sender_id=99,
            sender=sender,
            out=False,
            photo=object(),
            media=types.SimpleNamespace(ttl_seconds=0),
        )
        bot = types.SimpleNamespace(
            send_photo=mock.AsyncMock(),
            send_document=mock.AsyncMock(),
        )
        self.module._client = types.SimpleNamespace(
            download_media=mock.AsyncMock(return_value=b"view once"),
        )
        self.module.inline = types.SimpleNamespace(bot=bot)
        self.module._channel = -10042
        self.module._next = 0
        self.module.config = {"save_sd": True, "fw_protect": 0}
        self.module.get = lambda key, default=None: False
        self.module.strings = lambda key: {
            "sd_media": "from {} {}",
            "sd_media_out": "outgoing",
            "sd_media_download_failed": " failed",
        }[key]

        await self.module.watcher(message)

        self.module._client.download_media.assert_awaited_once_with(message, bytes)
        backups = list(self.module._backup_dirs["received"].iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"view once")
        self.assertEqual(len(self.module._queue), 1)

        await self.module.sender()

        upload = bot.send_photo.await_args.kwargs["photo"]
        self.assertEqual(upload.data, b"view once")
        self.assertEqual(upload.filename, "photo.jpg")

    async def test_download_retries_with_refetched_message(self):
        original = types.SimpleNamespace(
            id=7,
            peer_id=object(),
            photo=object(),
        )
        refreshed = types.SimpleNamespace(
            id=7,
            peer_id=original.peer_id,
            photo=object(),
            media=object(),
        )
        self.module._client = types.SimpleNamespace(
            download_media=mock.AsyncMock(
                side_effect=[RuntimeError("not ready"), b"photo bytes"],
            ),
            get_messages=mock.AsyncMock(return_value=refreshed),
        )

        with mock.patch.object(nekospy.asyncio, "sleep", new=mock.AsyncMock()):
            media = await self.module._download_media_file(original, attempts=2)

        self.assertEqual(media.getvalue(), b"photo bytes")
        self.assertEqual(media.name, "photo.jpg")
        self.assertEqual(self.module._client.download_media.await_count, 2)
        self.module._client.get_messages.assert_awaited_once_with(
            original.peer_id,
            ids=7,
        )

    def test_media_cache_evicts_oldest_files_by_byte_limit(self):
        self.module.media_cache_limit = 5
        first = types.SimpleNamespace(
            id=1,
            is_private=True,
            peer_id=None,
            chat_id=11,
        )
        second = types.SimpleNamespace(
            id=2,
            is_private=True,
            peer_id=None,
            chat_id=11,
        )

        self.module._store_media_snapshot(first, io.BytesIO(b"one"))
        self.module._store_media_snapshot(second, io.BytesIO(b"two"))

        self.assertNotIn(1, self.module._media_cache)
        self.assertEqual(self.module._media_cache[2].getvalue(), b"two")
        self.assertEqual(self.module._media_cache_bytes, 3)

    async def test_sender_continues_after_failed_delivery(self):
        failed_item = mock.AsyncMock(side_effect=RuntimeError("rejected"))()
        next_item = mock.AsyncMock()()
        self.module._queue = [failed_item, next_item]
        self.module._next = 0
        self.module.config = {"fw_protect": 0}

        await self.module.sender()
        await self.module.sender()

        self.assertEqual(self.module._queue, [])


if __name__ == "__main__":
    unittest.main()
