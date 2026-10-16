"""Tests for the VoiceChat Hikka module."""

import asyncio
import importlib.util
import pathlib
import sys
import time
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


class _Module:
    def get_prefix(self):
        return "."


class _Validators:
    def __getattr__(self, name):
        return lambda *args, **kwargs: object()


def _load_module():
    package = types.ModuleType("vchost")
    package.__path__ = []
    modules = types.ModuleType("vchost.modules")
    modules.__path__ = []
    loader = types.ModuleType("vchost.loader")
    utils = types.ModuleType("vchost.utils")
    loader.Module = _Module
    loader.validators = _Validators()

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key: strings[key]
        return cls

    loader.tds = tds
    loader.command = lambda *args, **kwargs: lambda function: function
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    utils.escape_html = lambda value: str(value)
    utils.answer = mock.AsyncMock(side_effect=lambda message, *a, **k: message)
    utils.get_args_raw = lambda message: getattr(message, "args", "")
    package.loader = loader
    package.utils = utils

    sys.modules.update(
        {
            "vchost": package,
            "vchost.modules": modules,
            "vchost.loader": loader,
            "vchost.utils": utils,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "voicechat.py"
    spec = importlib.util.spec_from_file_location("vchost.modules.voicechat", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, utils


voicechat, vc_utils = _load_module()

CHAT = -1001234


class _GroupCallConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _pytgcalls_stub():
    pytgcalls = types.ModuleType("pytgcalls")
    pytgcalls.__path__ = []
    pytgcalls_types = types.ModuleType("pytgcalls.types")
    pytgcalls_types.GroupCallConfig = _GroupCallConfig
    pytgcalls.types = pytgcalls_types
    return {"pytgcalls": pytgcalls, "pytgcalls.types": pytgcalls_types}


class _Calls:
    def __init__(self, fail_sources=()):
        self.fail_sources = set(fail_sources)
        self.play = mock.AsyncMock(side_effect=self._play)
        self.pause = mock.AsyncMock()
        self.resume = mock.AsyncMock()
        self.leave_call = mock.AsyncMock()
        self.change_volume_call = mock.AsyncMock()
        self.played = []

    async def _play(self, chat_id, stream, config=None):
        if stream["source"] in self.fail_sources:
            raise RuntimeError("broken stream")
        self.played.append((chat_id, stream["source"], stream["offset"]))


class _Message:
    def __init__(self, args="", chat_id=CHAT, reply=None):
        self.args = args
        self.chat_id = chat_id
        self.sender_id = 1
        self.is_reply = reply is not None
        self._reply = reply

    async def get_reply_message(self):
        return self._reply


class _End:
    def __init__(self, kind, chat_id=CHAT):
        self.chat_id = chat_id
        self.stream_type = types.SimpleNamespace(name=kind)


class HelpersTest(unittest.TestCase):
    def test_fit_frame_keeps_orientation_and_never_upscales(self):
        self.assertEqual(voicechat.fit_frame(3840, 2160, "4k"), (3840, 2160))
        self.assertEqual(voicechat.fit_frame(3840, 2160, "1080p"), (1920, 1080))
        self.assertEqual(voicechat.fit_frame(1080, 1920, "1080p"), (1080, 1920))
        self.assertEqual(voicechat.fit_frame(1280, 720, "4k"), (1280, 720))
        self.assertEqual(voicechat.fit_frame(None, None, "720p"), (1280, 720))
        width, height = voicechat.fit_frame(1001, 563, "4k")
        self.assertEqual((width % 2, height % 2), (0, 0))

    def test_parse_seek_absolute_relative_and_bounds(self):
        self.assertEqual(voicechat.parse_seek("1:30", 0), 90)
        self.assertEqual(voicechat.parse_seek("1:00:05", 0), 3605)
        self.assertEqual(voicechat.parse_seek("+15", 100), 115)
        self.assertEqual(voicechat.parse_seek("-200", 100), 0)
        self.assertEqual(voicechat.parse_seek("10:00", 0, 300), 299)
        self.assertIsNone(voicechat.parse_seek("abc", 0))
        self.assertIsNone(voicechat.parse_seek("", 0))

    def test_fmt_time_and_fps(self):
        self.assertEqual(voicechat.fmt_time(65), "1:05")
        self.assertEqual(voicechat.fmt_time(3725), "1:02:05")
        self.assertEqual(voicechat.clamp_fps(None), 30)
        self.assertEqual(voicechat.clamp_fps(120), 60)
        self.assertEqual(voicechat.clamp_fps(29.97), 30)

    def test_playlist_detection(self):
        is_playlist = voicechat.VoiceChatMod._is_playlist
        self.assertTrue(is_playlist("https://youtube.com/playlist?list=PL1"))
        self.assertFalse(is_playlist("https://youtube.com/watch?v=x&list=PL1"))
        self.assertFalse(is_playlist("https://youtu.be/x?list=PL1"))

    def test_apply_info_splits_video_and_audio(self):
        mod = voicechat.VoiceChatMod()
        track = voicechat.Track("https://youtu.be/x")
        mod._apply_info(
            track,
            {
                "title": "Clip",
                "duration": 200,
                "webpage_url": "https://youtube.com/watch?v=x",
                "requested_formats": [
                    {
                        "url": "v",
                        "vcodec": "vp09",
                        "acodec": "none",
                        "width": 3840,
                        "height": 2160,
                        "fps": 60,
                        "http_headers": {"User-Agent": "UA"},
                    },
                    {"url": "a", "vcodec": "none", "acodec": "opus"},
                ],
            },
        )
        self.assertEqual((track.video_url, track.audio_url), ("v", "a"))
        self.assertEqual((track.width, track.height, track.fps), (3840, 2160, 60))
        self.assertEqual(track.headers, {"User-Agent": "UA"})
        self.assertEqual(track.title, "Clip")
        self.assertTrue(track.has_video and track.resolved)


class SetupTest(unittest.TestCase):
    def _plan(self, have_telethon, have_hikkatl, have_pytgcalls=False):
        specs = {"hikkatl": have_hikkatl, "pytgcalls": have_pytgcalls}
        with mock.patch.object(
            voicechat.importlib.util,
            "find_spec",
            side_effect=lambda name: object() if specs.get(name) else None,
        ), mock.patch.object(
            voicechat.VoiceChatMod,
            "_telethon_version",
            staticmethod(lambda: have_telethon),
        ):
            return voicechat.VoiceChatMod()._setup_plan()

    def test_fork_on_new_telethon_installs_only_missing(self):
        packages, restart, warning = self._plan((1, 44, 0), have_hikkatl=False)
        self.assertEqual(packages, ["py-tgcalls", "yt-dlp"])
        self.assertFalse(restart)
        self.assertIsNone(warning)

    def test_hikka_telethon_too_old_is_never_replaced(self):
        packages, _, warning = self._plan((1, 30, 0), have_hikkatl=False)
        self.assertNotIn(voicechat.TELETHON_REQUIREMENT, packages)
        self.assertIn("py-tgcalls", packages)
        self.assertIn("оновіть Hikka", warning)

    def test_hikkatl_build_gets_its_own_telethon(self):
        packages, _, warning = self._plan(None, have_hikkatl=True)
        self.assertIn(voicechat.TELETHON_REQUIREMENT, packages)
        self.assertIsNone(warning)

    def test_pip_command_installs_listed_packages(self):
        command = voicechat.VoiceChatMod._pip_command(["yt-dlp"])
        self.assertEqual(command[1:5], ["-m", "pip", "install", "--upgrade"])
        self.assertEqual(command[-1], "yt-dlp")


class PlaybackTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        vc_utils.answer.reset_mock()
        patcher = mock.patch.dict(sys.modules, _pytgcalls_stub())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mod = voicechat.VoiceChatMod()
        self.mod._client = mock.Mock(send_message=mock.AsyncMock())
        self.calls = _Calls()
        self.mod._calls = self.calls
        self.mod._check_dependencies = lambda: None
        self.mod._ensure_calls = mock.AsyncMock(return_value=self.calls)
        self.mod._prime_chat = mock.AsyncMock()

        async def resolve(track):
            track.audio_url = track.source
            track.video_url = None if track.audio_only else track.source
            track.duration = 100
            track.resolved_at = time.monotonic()

        self.mod._resolve = resolve
        self.mod._stream = lambda track, offset=0: {
            "source": track.source,
            "offset": offset,
        }

    def _last_answer(self):
        return vc_utils.answer.await_args.args[1]

    def _player(self):
        return self.mod._players[CHAT]

    async def _end(self, *kinds):
        self._player().switched_at = 0
        for kind in kinds:
            await self.mod._on_stream_end(None, _End(kind))

    async def test_private_chat_is_rejected(self):
        await self.mod.vcplay(_Message("https://a", chat_id=42))
        self.assertIn("лише в групах", self._last_answer())
        self.calls.play.assert_not_awaited()

    async def test_without_source_asks_for_link(self):
        await self.mod.vcplay(_Message(""))
        self.assertIn("посилання", self._last_answer())

    async def test_play_queue_advance_and_auto_leave(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        self.assertEqual(self.calls.played, [(CHAT, "https://a", 0)])
        self.assertEqual(len(self._player().queue), 1)
        self.assertIn("Додано в чергу", self._last_answer())

        await self._end("AUDIO", "VIDEO")
        self.assertEqual(self.calls.played[-1], (CHAT, "https://b", 0))
        self.mod._client.send_message.assert_awaited()

        await self._end("AUDIO", "VIDEO")
        self.calls.leave_call.assert_awaited_once_with(CHAT)
        self.assertNotIn(CHAT, self.mod._players)

    async def test_vcplay_replaces_current_track(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcplay(_Message("https://b"))
        self.assertEqual(
            [source for _, source, _ in self.calls.played],
            ["https://a", "https://b"],
        )
        self.assertEqual(self._player().current.source, "https://b")
        self.assertFalse(self._player().queue)

    async def test_audio_only_waits_for_audio_end_only(self):
        await self.mod.vcaudio(_Message("https://a"))
        self.assertFalse(self._player().current.has_video)
        await self.mod.vcadd(_Message("https://b"))
        await self._end("AUDIO")
        self.assertEqual(self.calls.played[-1][1], "https://b")

    async def test_single_stream_end_falls_back_after_grace(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        with mock.patch.object(voicechat, "END_GRACE", 0.01):
            await self._end("VIDEO")
            self.assertEqual(len(self.calls.played), 1)
            await asyncio.sleep(0.05)
        self.assertEqual(self.calls.played[-1][1], "https://b")

    async def test_stale_end_right_after_switch_is_ignored(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        await self.mod._on_stream_end(None, _End("AUDIO"))
        await self.mod._on_stream_end(None, _End("VIDEO"))
        self.assertEqual(len(self.calls.played), 1)

    async def test_pause_resume_volume_loop(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcpause(_Message())
        self.calls.pause.assert_awaited_once_with(CHAT)
        self.assertTrue(self._player().paused)
        await self.mod.vcpause(_Message())
        self.assertIn("Вже на паузі", self._last_answer())
        await self.mod.vcresume(_Message())
        self.calls.resume.assert_awaited_once_with(CHAT)
        self.assertFalse(self._player().paused)

        await self.mod.vcvol(_Message("150"))
        self.calls.change_volume_call.assert_awaited_once_with(CHAT, 150)
        await self.mod.vcvol(_Message("500"))
        self.assertIn("від 1 до 200", self._last_answer())

        await self.mod.vcloop(_Message())
        await self._end("AUDIO", "VIDEO")
        self.assertEqual(
            [source for _, source, _ in self.calls.played],
            ["https://a", "https://a"],
        )

    async def test_seek_restarts_with_offset(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcseek(_Message("1:10"))
        self.assertEqual(self.calls.played[-1], (CHAT, "https://a", 70))
        self.assertGreaterEqual(self._player().position, 70)
        await self.mod.vcseek(_Message("soon"))
        self.assertIn("Вкажіть час", self._last_answer())

    async def test_skip_and_stop(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        await self.mod.vcskip(_Message())
        self.assertEqual(self.calls.played[-1][1], "https://b")
        await self.mod.vcstop(_Message())
        self.calls.leave_call.assert_awaited_with(CHAT)
        self.assertNotIn(CHAT, self.mod._players)
        await self.mod.vcpause(_Message())
        self.assertIn("нічого не грає", self._last_answer())

    async def test_broken_queue_item_is_reported_and_skipped(self):
        self.calls.fail_sources = {"https://bad"}
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://bad"))
        await self.mod.vcadd(_Message("https://c"))
        await self._end("AUDIO", "VIDEO")
        self.assertEqual(self.calls.played[-1][1], "https://c")
        texts = [c.args[1] for c in self.mod._client.send_message.await_args_list]
        self.assertTrue(any("Не вдалося відтворити" in t for t in texts))

    async def test_failed_first_play_reports_error(self):
        self.calls.fail_sources = {"https://bad"}
        await self.mod.vcplay(_Message("https://bad"))
        self.assertIn("broken stream", self._last_answer())
        self.assertNotIn(CHAT, self.mod._players)

    async def test_reply_with_link_is_used(self):
        reply = types.SimpleNamespace(file=None, raw_text="look https://r/x ok")
        await self.mod.vcplay(_Message("", reply=reply))
        self.assertEqual(self.calls.played[-1][1], "https://r/x")

    async def test_queue_listing_and_clear(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        await self.mod.vcqueue(_Message())
        self.assertIn("https://b", self._last_answer())
        await self.mod.vcclear(_Message())
        self.assertIn("(1)", self._last_answer())
        await self.mod.vcnow(_Message())
        self.assertIn("https://a", self._last_answer())


if __name__ == "__main__":
    unittest.main()
