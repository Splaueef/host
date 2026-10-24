"""Tests for the VoiceChat Hikka module."""

import asyncio
import importlib.util
import os
import pathlib
import shlex
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

    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value


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
    loader.group_member = lambda function: function
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
        self.active = {CHAT}
        self.played_time = 0
        self.time = mock.AsyncMock(side_effect=lambda chat_id: self.played_time)

    @property
    async def calls(self):
        return {chat_id: object() for chat_id in self.active}

    async def _play(self, chat_id, stream, config=None):
        if stream["source"] in self.fail_sources:
            raise RuntimeError("broken stream")
        self.played.append((chat_id, stream["source"], stream["offset"]))


class _Message:
    def __init__(self, args="", chat_id=CHAT, reply=None, sender_id=1, out=True):
        self.args = args
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.out = out
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


class _DownloadError(Exception):
    pass


class YouTubeTest(unittest.TestCase):
    def setUp(self):
        self.mod = voicechat.VoiceChatMod()
        self.mod.config["video_quality"] = "1080p"
        self.mod.config["cookies_file"] = ""
        self.mod.lookup = lambda name: None
        self.seen = []

    def _yt_dlp(self, works_with=None, fatal=False):
        seen = self.seen

        class YoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                seen.append(self.options)
                if fatal:
                    raise _DownloadError("ERROR: Private video")
                clients = (
                    self.options.get("extractor_args", {})
                    .get("youtube", {})
                    .get("player_client")
                )
                if works_with is not None and clients == works_with:
                    return {"title": "ok", "url": "u", "acodec": "opus"}
                raise _DownloadError(
                    "ERROR: [youtube+GetPOT] x: Sign in to confirm you're not a bot"
                )

        return mock.patch.dict(
            sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YoutubeDL)}
        )

    def test_youtube_detection(self):
        self.assertTrue(voicechat.is_youtube("https://youtube.com/watch?v=x&si=y"))
        self.assertTrue(voicechat.is_youtube("https://m.youtube.com/watch?v=x"))
        self.assertTrue(voicechat.is_youtube("https://youtu.be/x"))
        self.assertTrue(voicechat.is_youtube("despacito"))
        self.assertFalse(voicechat.is_youtube("https://soundcloud.com/a/b"))
        self.assertFalse(voicechat.is_youtube("https://notyoutube.com/x"))

    def test_falls_through_player_clients_until_one_works(self):
        track = voicechat.Track("https://youtube.com/watch?v=x")
        context = {"cookies": None, "runtime": "deno:/usr/local/bin/deno"}
        with self._yt_dlp(works_with=["android_vr"]):
            info = self.mod._extract_sync(track, context)
        self.assertEqual(info["title"], "ok")
        self.assertGreater(len(self.seen), 1)
        self.assertEqual(
            self.seen[0]["js_runtimes"], {"deno": {"path": "/usr/local/bin/deno"}}
        )
        self.assertNotIn("extractor_args", self.seen[0])

    def test_fatal_error_stops_retrying(self):
        track = voicechat.Track("https://youtube.com/watch?v=x")
        with self._yt_dlp(fatal=True), self.assertRaises(_DownloadError):
            self.mod._extract_sync(track, {})
        self.assertEqual(len(self.seen), 1)

    def test_cookie_file_is_found_and_used(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            empty = pathlib.Path(folder, "empty.txt")
            empty.write_text("# Netscape HTTP Cookie File\n")
            good = pathlib.Path(folder, "cookies.txt")
            good.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv\n")
            with mock.patch.object(
                voicechat, "YT_COOKIE_FILES", (str(empty), str(good))
            ):
                context = self.mod._yt_context()
            self.assertEqual(context["cookies"], str(good))
            track = voicechat.Track("https://youtu.be/x")
            with self._yt_dlp(works_with=["web_safari"]):
                self.mod._extract_sync(track, context)
            self.assertTrue(all(o["cookiefile"] == str(good) for o in self.seen))

    def test_videodownloader_settings_are_reused(self):
        vdl = types.SimpleNamespace(
            _js_runtime="node:/opt/node",
            _cookies_for=lambda url: None,
            _yt_browser_cookies_value=lambda: "firefox",
        )
        self.mod.lookup = lambda name: vdl if name == "VideoDownloader" else None
        with mock.patch.object(voicechat, "YT_COOKIE_FILES", ()):
            context = self.mod._yt_context()
        self.assertEqual(context["runtime"], "node:/opt/node")
        self.assertEqual(context["browser"], ("firefox", None, None, None))
        attempts = self.mod._youtube_attempts({}, context)
        self.assertEqual(
            len(attempts), 2 * len(voicechat.YT_CLIENT_PROFILES)
        )
        self.assertEqual(attempts[-1]["cookiesfrombrowser"][0], "firefox")

    def test_auth_error_has_readable_message(self):
        text = self.mod._error_text(
            voicechat.Track("https://youtu.be/x"),
            voicechat.YouTubeAuthError({"cookies": None}),
        )
        self.assertIn("vcbrowser", text)
        self.assertNotIn("GetPOT", text)
        text = self.mod._error_text(
            voicechat.Track("https://youtu.be/x"),
            voicechat.YouTubeAuthError({"cookies": "/c.txt"}),
        )
        self.assertIn("/c.txt", text)


PAGE = """<html><head><title>Film &amp; serial</title>
<meta property="og:image" content="https://cdn.example/poster.jpg"></head>
<body><iframe src="https://ads.example/banner"></iframe>
<iframe data-src="/player/embed?id=42"></iframe></body></html>"""

PLAYER = """<html><head><title>player</title></head><body><script>
new Playerjs({file:"https:\\/\\/cdn.example\\/hls\\/720\\/index.m3u8",
 poster:"/p.jpg", alt:"https://cdn.example/low/360.mp4"});</script></body></html>"""


class AnySiteTest(unittest.TestCase):
    def test_find_media_prefers_hls_and_unescapes_json(self):
        media = voicechat.find_media(PLAYER, "https://player.example/embed")
        self.assertEqual(media[0], "https://cdn.example/hls/720/index.m3u8")
        self.assertIn("https://cdn.example/low/360.mp4", media)
        self.assertNotIn("https://player.example/p.jpg", media)

    def test_find_media_reads_tags_and_meta(self):
        page = (
            '<meta content="/v/clip.mp4" property="og:video">'
            '<video><source src="a.webm"></video>'
        )
        media = voicechat.find_media(page, "https://site.example/watch/1")
        self.assertIn("https://site.example/v/clip.mp4", media)
        self.assertIn("https://site.example/watch/a.webm", media)

    def test_find_frames_and_title(self):
        frames = voicechat.find_frames(PAGE, "https://site.example/film/1")
        self.assertEqual(
            frames,
            ["https://ads.example/banner", "https://site.example/player/embed?id=42"],
        )
        self.assertEqual(voicechat.page_title(PAGE), "Film & serial")

    def test_scrape_follows_iframe_with_referer(self):
        mod = voicechat.VoiceChatMod()
        pages = {
            "https://site.example/film/1": PAGE,
            "https://site.example/player/embed?id=42": PLAYER,
        }
        calls = []

        def fetch(url, referer=None):
            calls.append((url, referer))
            if url not in pages:
                raise OSError("blocked")
            return url, pages[url]

        mod._fetch_page = fetch
        mod._ydl_embedded = lambda url, referer, base: None
        info = mod._scrape_sync("https://site.example/film/1", {})
        self.assertEqual(info["url"], "https://cdn.example/hls/720/index.m3u8")
        self.assertEqual(info["title"], "Film & serial")
        self.assertEqual(
            info["http_headers"]["Referer"], "https://site.example/player/embed?id=42"
        )
        self.assertIn(
            ("https://site.example/player/embed?id=42", "https://site.example/film/1"),
            calls,
        )

    def test_apply_info_unknown_codecs_keeps_url_for_probe(self):
        mod = voicechat.VoiceChatMod()
        track = voicechat.Track("https://site.example/film/1")
        mod._apply_info(
            track,
            {"url": "https://cdn.example/a.m3u8", "title": "F", "_scraped": True},
        )
        self.assertEqual(track.video_url, "https://cdn.example/a.m3u8")
        self.assertEqual(track.audio_url, "https://cdn.example/a.m3u8")
        self.assertEqual(track.protocol, "HLS")

    def test_http_args_survive_ffmpeg_single_headers_option(self):
        args = voicechat.http_args(
            {"User-Agent": "UA", "Referer": "https://r/", "Origin": "https://r",
             "Accept": "*/*"}
        )
        self.assertEqual(
            args,
            ["-user_agent", "UA", "-referer", "https://r/", "-headers",
             "Origin: https://r"],
        )
        self.assertEqual(args.count("-headers"), 1)

    def test_hls_args_only_for_playlists_and_known_options(self):
        with mock.patch.object(
            voicechat, "_hls_demuxer_options", lambda: {"allowed_extensions"}
        ):
            self.assertEqual(
                voicechat.hls_args("https://a/x.m3u8?t=1"),
                ["-allowed_extensions", "ALL"],
            )
            self.assertEqual(voicechat.hls_args("https://a/x.mp4"), [])

    def test_reconnect_at_eof_is_stripped(self):
        command = ["ffmpeg", "-reconnect", "1", "-reconnect_at_eof", "1",
                   "-reconnect_streamed", "1", "-i", "u", "pipe:1"]
        self.assertEqual(
            voicechat._strip_reconnect_at_eof(command),
            ["ffmpeg", "-reconnect", "1", "-reconnect_streamed", "1", "-i", "u",
             "pipe:1"],
        )

    def test_codec_names_and_fps_cap(self):
        self.assertEqual(voicechat.codec_name("avc1.64002a"), "h264")
        self.assertEqual(voicechat.codec_name("hvc1"), "h265")
        self.assertIsNone(voicechat.codec_name("none"))
        self.assertEqual(voicechat.clamp_fps(60, 30), 30)
        self.assertEqual(voicechat.clamp_fps(None, 24), 24)


COOKIES_TXT = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t1999999999\tSAPISID\tabc
#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1999999999\t__Secure-1PSIDTS\tts1
www.youtube.com\tFALSE\t/\tTRUE\t0\t__Host-GAPS\tg
.example.com\tTRUE\t/\tFALSE\t0\tother\tx
"""


class BrowserTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mod = voicechat.VoiceChatMod()
        self.mod.lookup = lambda name: None
        self.mod._browser = voicechat.BrowserSession(self.tmp.name)
        patcher = mock.patch.object(
            voicechat.BrowserSession, "available", staticmethod(lambda: True)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        work = mock.patch.object(
            voicechat, "WORK_DIR", str(pathlib.Path(self.tmp.name, "work"))
        )
        work.start()
        self.addCleanup(work.stop)

    def _fake_refresh(self, logged_in=True):
        browser = self.mod._browser
        calls = []

        async def refresh(import_cookies=None):
            calls.append(import_cookies)
            cookies = import_cookies or voicechat.parse_netscape(COOKIES_TXT)
            pathlib.Path(browser.profile).mkdir(parents=True, exist_ok=True)
            pathlib.Path(browser.cookie_file).write_text(
                voicechat.to_netscape(cookies)
            )
            return browser._save_state(
                refreshed_at=voicechat.time.time(),
                logged_in=logged_in,
                count=len(cookies),
            )

        browser.refresh = refresh
        return calls

    def test_netscape_round_trip_keeps_http_only_and_session(self):
        cookies = voicechat.parse_netscape(COOKIES_TXT)
        self.assertEqual(len(cookies), 4)
        self.assertTrue(cookies[1]["httpOnly"])
        self.assertEqual(cookies[2]["expires"], -1)
        again = voicechat.parse_netscape(voicechat.to_netscape(cookies))
        self.assertEqual(again, cookies)
        self.assertTrue(voicechat.is_logged_in(cookies))
        self.assertEqual(len(voicechat.session_cookies(cookies)), 3)

    def test_host_prefixed_cookie_uses_url(self):
        host = voicechat.to_playwright(voicechat.parse_netscape(COOKIES_TXT)[2])
        self.assertEqual(host["url"], "https://www.youtube.com/")
        self.assertNotIn("domain", host)
        plain = voicechat.to_playwright(voicechat.parse_netscape(COOKIES_TXT)[0])
        self.assertEqual((plain["domain"], plain["path"]), (".youtube.com", "/"))

    async def test_import_command_hands_cookies_to_browser(self):
        calls = self._fake_refresh()
        path = pathlib.Path(self.tmp.name, "in.txt")
        path.write_text(COOKIES_TXT)
        vc_utils.answer.reset_mock()
        await self.mod.vcbrowser(_Message(f"import {path}"))
        self.assertEqual(len(calls[0]), 3)  # only YouTube/Google cookies
        self.assertIn("Сесію оновлено", vc_utils.answer.await_args.args[1])
        self.assertTrue(self.mod._browser.has_session())

    async def test_snapshot_is_preferred_and_never_handed_out_itself(self):
        self._fake_refresh()
        await self.mod._browser.refresh()
        context = self.mod._yt_context("https://youtu.be/x")
        self.assertEqual(context["cookies"], self.mod._browser.cookie_file)
        self.assertTrue(context["snapshot"])
        attempts = self.mod._youtube_attempts({}, context)
        used = {a.get("cookiefile") for a in attempts if a.get("cookiefile")}
        self.assertEqual(len(used), 1)
        self.assertNotIn(self.mod._browser.cookie_file, used)
        # Anonymous fallback follows the cookie attempts.
        self.assertTrue(any("cookiefile" not in a for a in attempts))

    async def test_logged_out_browser_is_not_used(self):
        self._fake_refresh(logged_in=False)
        await self.mod._browser.refresh()
        with mock.patch.object(voicechat, "YT_COOKIE_FILES", ()):
            context = self.mod._yt_context("https://youtu.be/x")
        self.assertIsNone(context["cookies"])

    async def test_bot_check_refreshes_browser_once_and_retries(self):
        calls = self._fake_refresh()
        await self.mod._browser.refresh()
        self.mod._browser._save_state(refreshed_at=1)
        results = iter([RuntimeError("Sign in to confirm you're not a bot"), {"ok": 1}])

        def extract(track, context):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return {"title": "T", "url": "u", "acodec": "opus"}

        self.mod._extract_sync = extract
        track = voicechat.Track("https://youtu.be/x")
        await self.mod._resolve(track)
        self.assertEqual(len(calls), 2)
        self.assertEqual(track.title, "T")

    async def test_status_explains_setup(self):
        vc_utils.answer.reset_mock()
        await self.mod.vcbrowser(_Message(""))
        text = vc_utils.answer.await_args.args[1]
        self.assertIn("vcbrowser setup", text)
        self.assertIn("приватне вікно", text)


class _TgClient:
    """Serves bytes like Telegram: offsets must be request-size aligned."""

    def __init__(self, data, expire_first=False):
        self.data = data
        self.expire_first = expire_first
        self.downloads = 0
        self.requested = []
        self.refetched = False
        self.download_media = mock.AsyncMock()

    async def iter_download(
        self, message, offset=0, request_size=0, file_size=None, limit=None
    ):
        assert offset % request_size == 0, offset
        self.downloads += 1
        if self.expire_first and not self.refetched:
            raise RuntimeError("FILE_REFERENCE_EXPIRED")
        sent = 0
        while offset < len(self.data) and (limit is None or sent < limit):
            await asyncio.sleep(0)
            self.requested.append(offset)
            yield self.data[offset:offset + request_size]
            offset += request_size
            sent += 1

    async def get_messages(self, chat_id, ids=None):
        self.refetched = True
        return types.SimpleNamespace(chat_id=chat_id, id=ids)


_real_sleep = asyncio.sleep


async def _no_sleep(delay, *args, **kwargs):
    await _real_sleep(0)


class TelegramStreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile

        self.data = bytes(range(256)) * 9000  # ~2.2 MB, several chunks
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(voicechat, "WORK_DIR", folder.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.streamer = voicechat.TelegramStreamer()
        self.addAsyncCleanup(self.streamer.close)

    async def _get(self, url, range_header=None):
        import urllib.error
        import urllib.request

        def fetch():
            request = urllib.request.Request(url)
            if range_header:
                request.add_header("Range", range_header)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as error:
                return error.code, dict(error.headers), b""

        return await asyncio.to_thread(fetch)

    def test_parse_range(self):
        self.assertEqual(voicechat.parse_range(None, 100), (0, 99))
        self.assertEqual(voicechat.parse_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(voicechat.parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(voicechat.parse_range("bytes=-5", 100), (95, 99))
        self.assertEqual(voicechat.parse_range("bytes=50-500", 100), (50, 99))
        self.assertIsNone(voicechat.parse_range("bytes=100-", 100))
        self.assertIsNone(voicechat.parse_range("items=1-2", 100))

    async def test_serves_whole_file_and_unaligned_ranges(self):
        client = _TgClient(self.data)
        message = types.SimpleNamespace(chat_id=-1, id=5)
        url = await self.streamer.register(
            client, message, len(self.data), "video/mp4", ".mp4"
        )
        self.assertTrue(url.endswith(".mp4"))
        status, headers, body = await self._get(url)
        self.assertEqual((status, body), (200, self.data))
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        start = voicechat.TG_CHUNK + 12345
        status, headers, body = await self._get(url, f"bytes={start}-{start + 99}")
        self.assertEqual(status, 206)
        self.assertEqual(body, self.data[start:start + 100])
        self.assertEqual(
            headers["Content-Range"], f"bytes {start}-{start + 99}/{len(self.data)}"
        )
        status, _, body = await self._get(url, "bytes=-10")
        self.assertEqual(body, self.data[-10:])
        status, _, _ = await self._get(url, f"bytes={len(self.data)}-")
        self.assertEqual(status, 416)

    async def test_each_chunk_is_fetched_once_for_all_readers(self):
        client = _TgClient(self.data)
        message = types.SimpleNamespace(chat_id=-1, id=5)
        url = await self.streamer.register(client, message, len(self.data))
        # Two "ffmpeg" readers (sound and picture) plus a seek back.
        results = await asyncio.gather(self._get(url), self._get(url))
        self.assertTrue(all(body == self.data for _, _, body in results))
        _, _, body = await self._get(url, "bytes=100-199")
        self.assertEqual(body, self.data[100:200])
        self.assertEqual(sorted(client.requested), sorted(set(client.requested)))
        cache = self.streamer.cache(url)
        self.assertTrue(cache.complete)
        self.assertEqual(cache.progress, 1.0)

    async def test_prebuffer_waits_for_the_start_and_cleans_up(self):
        client = _TgClient(self.data)
        message = types.SimpleNamespace(chat_id=-1, id=5)
        url = await self.streamer.register(client, message, len(self.data))
        cache = self.streamer.cache(url)
        reports = []

        async def report(fraction, cache):
            reports.append(fraction)

        await self.streamer.prebuffer(url, 0, 3 * voicechat.TG_CHUNK, report)
        self.assertGreaterEqual(cache.buffered_from(0), 3)
        path = cache.path
        self.assertTrue(os.path.exists(path))
        self.streamer.unregister(url)
        await asyncio.sleep(0.05)
        self.assertFalse(os.path.exists(path))

    async def test_prefetch_window_fetches_only_the_opening(self):
        client = _TgClient(self.data)
        message = types.SimpleNamespace(chat_id=-1, id=5)
        url = await self.streamer.register(client, message, len(self.data))
        cache = self.streamer.cache(url)
        await cache.set_window(2)
        await self.streamer.prebuffer(url, 0, voicechat.TG_CHUNK)
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual(cache.done, 2)  # the next track waits its turn
        await cache.set_window(None)  # it starts playing: fetch the rest
        await cache.wait_for(cache.chunks - 1)
        for _ in range(50):
            if cache.complete:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(cache.complete)

    async def test_flaky_network_is_retried_not_fatal(self):
        client = _TgClient(self.data)
        original = client.iter_download
        failures = {"left": 5}

        def flaky(*args, **kwargs):
            if failures["left"]:
                failures["left"] -= 1
                raise ConnectionError("network down")
            return original(*args, **kwargs)

        client.iter_download = flaky
        message = types.SimpleNamespace(chat_id=-1, id=5)
        url = await self.streamer.register(client, message, len(self.data))
        with mock.patch.object(voicechat.asyncio, "sleep", side_effect=_no_sleep):
            status, _, body = await self._get(url, "bytes=0-99")
        self.assertEqual((status, body), (206, self.data[:100]))

    async def test_unregistered_token_is_gone(self):
        client = _TgClient(self.data)
        url = await self.streamer.register(
            client, types.SimpleNamespace(chat_id=-1, id=5), len(self.data)
        )
        self.streamer.unregister(url)
        status, _, _ = await self._get(url)
        self.assertEqual(status, 404)

    async def test_expired_file_reference_is_refreshed(self):
        client = _TgClient(self.data, expire_first=True)
        url = await self.streamer.register(
            client, types.SimpleNamespace(chat_id=-1, id=5), len(self.data)
        )
        status, _, body = await self._get(url, "bytes=0-9")
        self.assertEqual((status, body), (206, self.data[:10]))
        self.assertTrue(client.refetched)

    async def test_replied_media_streams_without_download(self):
        mod = voicechat.VoiceChatMod()
        mod._client = _TgClient(self.data)
        mod._streamer = self.streamer
        reply = types.SimpleNamespace(
            id=9,
            chat_id=-1,
            file=types.SimpleNamespace(
                size=len(self.data), mime_type="video/mp4", ext=".mp4",
                title=None, name="film.mp4", performer=None, duration=20,
            ),
            video=True,
        )
        message = _Message("", reply=reply)
        tracks = await mod._tracks_from_message(message, message, "", False)
        self.assertEqual(len(tracks), 1)
        self.assertTrue(tracks[0].stream_url.startswith("http://127.0.0.1:"))
        self.assertEqual(tracks[0].title, "film.mp4")
        mod._client.download_media.assert_not_awaited()

    async def test_download_mode_reports_progress(self):
        mod = voicechat.VoiceChatMod()
        status = object()
        vc_utils.answer.reset_mock()
        report = mod._progress_reporter(status, every=0)
        await report(50 * 1048576, 200 * 1048576)
        text = vc_utils.answer.await_args.args[1]
        self.assertIn("25%", text)
        self.assertIn("50.0 / 200.0", text)


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
        async def stream(track, offset=0):
            return {"source": track.source, "offset": offset}

        self.mod._stream = stream

    def _last_answer(self):
        return vc_utils.answer.await_args.args[1]

    def _player(self):
        return self.mod._players[CHAT]

    async def _end(self, *kinds, at_end=True):
        player = self._player()
        player.switched_at = 0
        if at_end and player.current is not None and player.current.duration:
            # A normal end: playback really reached the track's last second.
            player.started_at -= float(player.current.duration)
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

    async def test_watchdog_advances_when_end_event_never_comes(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        player = self._player()
        player.current.duration = 1
        with mock.patch.object(voicechat, "WATCHDOG_SLACK", 0):
            player.started_at -= 5
            self.mod._arm_watchdog(player, player.current)
            await asyncio.sleep(0.05)
        self.assertEqual(self.calls.played[-1][1], "https://b")

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


def video_threads(params):
    parts = shlex.split(params)
    section = parts[parts.index("--video"):]
    return section[section.index("-threads") + 1]


class _Panel:
    def __init__(self, text, markup):
        self.text, self.markup = text, markup
        self.edits = []
        self.deleted = False

    async def edit(self, text, reply_markup=None):
        self.edits.append(text)
        self.text = text
        if reply_markup is not None:
            self.markup = reply_markup

    async def delete(self):
        self.deleted = True


class _Inline:
    def __init__(self):
        self.forms = []

    async def form(self, text, message=None, reply_markup=None, **kwargs):
        panel = _Panel(text, reply_markup)
        self.forms.append(panel)
        return panel


class _Call:
    def __init__(self, user_id):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.answers = []
        self.answer = mock.AsyncMock(side_effect=lambda text, **k: self.answers.append(text))
        self.edit = mock.AsyncMock()
        self.delete = mock.AsyncMock()


class FeaturesTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player
    _last_answer = PlaybackTest._last_answer
    _end = PlaybackTest._end

    def _button(self, panel, action):
        for row in panel.markup:
            for button in row:
                if button.get("args", (None, None))[1] == action:
                    return button
        raise AssertionError(action)

    # -- access
    def test_access_modes(self):
        self.mod._client.tg_id = 1
        self.assertTrue(self.mod._user_allowed(1, "control"))
        self.assertFalse(self.mod._user_allowed(7, "view"))
        self.mod.config["access"] = "queue"
        self.assertTrue(self.mod._user_allowed(7, "queue"))
        self.assertFalse(self.mod._user_allowed(7, "control"))
        self.mod.config["trusted_users"] = [7]
        self.assertTrue(self.mod._user_allowed(7, "control"))
        self.mod.config["trusted_users"] = []
        self.mod.config["access"] = "all"
        self.assertTrue(self.mod._user_allowed(7, "control"))

    async def test_strangers_are_ignored_silently_in_owner_mode(self):
        self.mod._client.tg_id = 1
        vc_utils.answer.reset_mock()
        await self.mod.vcplay(_Message("https://a", sender_id=7, out=False))
        self.calls.play.assert_not_awaited()
        vc_utils.answer.assert_not_awaited()

    async def test_queue_mode_lets_members_add_but_not_skip(self):
        self.mod._client.tg_id = 1
        self.mod.config["access"] = "queue"
        await self.mod.vcadd(_Message("https://a", sender_id=7, out=False))
        self.assertEqual(self.calls.played[-1][1], "https://a")
        await self.mod.vcskip(_Message(sender_id=7, out=False))
        self.assertIn("не дозволено", self._last_answer())
        self.assertEqual(self._player().current.source, "https://a")

    # -- recovery
    async def test_early_end_restarts_track_where_it_stopped(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        player = self._player()
        player.started_at -= 40  # died at 0:40 of 1:40
        await self._end("AUDIO", "VIDEO", at_end=False)
        source, offset = self.calls.played[-1][1:]
        self.assertEqual(source, "https://a")
        self.assertAlmostEqual(offset, 38, delta=1)
        self.assertEqual(player.current.recoveries, 1)

    async def test_recovery_gives_up_after_limit(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        self._player().current.recoveries = voicechat.MAX_RECOVERIES
        await self._end("AUDIO", "VIDEO", at_end=False)
        self.assertEqual(self.calls.played[-1][1], "https://b")

    # -- prefetch
    async def test_next_track_is_prepared_while_current_plays(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        upcoming = self._player().queue[0]
        self.assertIsNotNone(upcoming.prefetch_task)
        await upcoming.prefetch_task
        self.assertTrue(upcoming.resolved)

    # -- audio tracks, decoder threads, no audio filters
    def test_audio_track_and_threads_go_to_the_right_ffmpeg(self):
        track = voicechat.Track("https://x/film.mkv")
        track.video_url = track.audio_url = "https://x/film.mkv"
        track.audio_streams = [
            {"lang": "jpn", "title": "", "codec": "aac", "channels": 2},
            {"lang": "ukr", "title": "Дубляж", "codec": "aac", "channels": 6},
        ]
        track.audio_index = 1
        self.mod.config["decode_threads"] = 3
        params = shlex.split(self.mod._ffmpeg_parameters(track))
        audio = params[params.index("--audio"):]
        video = params[params.index("--video"):params.index("--audio")]
        self.assertIn("0:a:1", audio)
        self.assertEqual(video, ["--video", "-threads", "3"])
        self.assertNotIn("0:a:1", params[: params.index("--audio")])
        # No loudness filter any more: the sound goes through untouched.
        self.assertNotIn("-af", params)
        self.mod.config["decode_threads"] = 0
        threads = int(video_threads(self.mod._ffmpeg_parameters(track)))
        self.assertTrue(1 <= threads <= 4)
        track.audio_streams, track.audio_index = [], None
        self.assertNotIn("--audio", self.mod._ffmpeg_parameters(track))

    def test_audio_only_track_gets_no_video_options(self):
        track = voicechat.Track("https://x/song.mp3")
        track.audio_url = "https://x/song.mp3"
        self.assertIsNone(self.mod._ffmpeg_parameters(track))

    def test_preferred_language_is_picked(self):
        streams = [
            {"lang": "eng", "title": "", "codec": "", "channels": 2},
            {"lang": "", "title": "Українська озвучка (ukr)", "codec": "", "channels": 2},
            {"lang": "ukr", "title": "", "codec": "", "channels": 2},
        ]
        self.assertEqual(self.mod._preferred_audio(streams), 2)
        self.mod.config["audio_languages"] = ["en"]
        self.assertEqual(self.mod._preferred_audio(streams), 0)
        self.mod.config["audio_languages"] = []
        self.assertIsNone(self.mod._preferred_audio(streams))

    async def test_audio_track_command_switches_and_keeps_position(self):
        await self.mod.vcplay(_Message("https://a"))
        player = self._player()
        player.current.audio_streams = [
            {"lang": "jpn", "title": "", "codec": "aac", "channels": 2},
            {"lang": "ukr", "title": "", "codec": "aac", "channels": 2},
        ]
        player.started_at -= 30
        await self.mod.vcaudiotrack(_Message(""))
        self.assertIn("UKR", self._last_answer())
        await self.mod.vcaudiotrack(_Message("2"))
        self.assertEqual(player.current.audio_index, 1)
        self.assertAlmostEqual(self.calls.played[-1][2], 30, delta=1)
        await self.mod.vcaudiotrack(_Message("9"))
        self.assertIn("Немає доріжки", self._last_answer())

    # -- queue management
    async def test_remove_and_move(self):
        await self.mod.vcplay(_Message("https://a"))
        for link in ("https://b", "https://c", "https://d"):
            await self.mod.vcadd(_Message(link))
        await self.mod.vcmove(_Message("3 1"))
        self.assertEqual(
            [t.source for t in self._player().queue],
            ["https://d", "https://b", "https://c"],
        )
        await self.mod.vcremove(_Message("2"))
        self.assertEqual(
            [t.source for t in self._player().queue], ["https://d", "https://c"]
        )
        await self.mod.vcremove(_Message("5"))
        self.assertIn("номер", self._last_answer())

    # -- persistence
    async def test_queue_survives_a_restart(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        player = self._player()
        player.started_at -= 50
        await self.mod.vcvol(_Message("140"))
        self.mod._save_state()
        saved = self.mod.get("state")[str(CHAT)]
        self.assertEqual(saved["current"]["source"], "https://a")
        self.assertEqual([q["source"] for q in saved["queue"]], ["https://b"])
        self.assertEqual(saved["volume"], 140)

        fresh = voicechat.VoiceChatMod()
        fresh._storage = {"state": self.mod.get("state")}
        fresh._client = mock.Mock(send_message=mock.AsyncMock())
        fresh._calls = self.calls
        fresh._prepare_chat = mock.AsyncMock(return_value=None)
        fresh._resolve = self.mod._resolve
        fresh._stream = self.mod._stream
        with mock.patch.object(voicechat, "RESTORE_DELAY", 0):
            await fresh._restore_all()
        restored = fresh._players[CHAT]
        self.assertEqual(restored.current.source, "https://a")
        self.assertAlmostEqual(self.calls.played[-1][2], 47, delta=2)
        self.assertEqual([t.source for t in restored.queue], ["https://b"])
        self.assertEqual(restored.volume, 140)

    async def test_stopping_clears_saved_state(self):
        await self.mod.vcplay(_Message("https://a"))
        self.assertIn(str(CHAT), self.mod.get("state"))
        await self.mod.vcstop(_Message())
        self.assertEqual(self.mod.get("state"), {})

    # -- panel
    async def test_panel_buttons_control_playback(self):
        self.mod.inline = _Inline()
        self.mod._client.tg_id = 1
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        panel = self._player().panel
        self.assertIsNotNone(panel)
        self.assertIn("https://b", panel.text)  # "next up" line after refresh

        stranger = _Call(7)
        button = self._button(panel, "toggle")
        await button["callback"](stranger, *button["args"])
        self.assertFalse(self._player().paused)
        self.assertIn("не дозволено", stranger.answers[-1])

        owner = _Call(1)
        await button["callback"](owner, *button["args"])
        self.assertTrue(self._player().paused)
        self.calls.pause.assert_awaited()

        volup = self._button(panel, "volup")
        await volup["callback"](owner, *volup["args"])
        self.calls.change_volume_call.assert_awaited_with(CHAT, 110)

        skip = self._button(self._player().panel, "skip")
        await skip["callback"](owner, *skip["args"])
        self.assertEqual(self._player().current.source, "https://b")

        stop = self._button(self._player().panel, "stop")
        await stop["callback"](owner, *stop["args"])
        self.assertNotIn(CHAT, self.mod._players)
        self.assertIn("зупинено", panel.text)

    async def test_vcnow_reposts_panel_and_removes_old(self):
        self.mod.inline = _Inline()
        await self.mod.vcplay(_Message("https://a"))
        first = self._player().panel
        await self.mod.vcnow(_Message())
        self.assertIsNot(self._player().panel, first)
        self.assertTrue(first.deleted)

    # -- search
    async def test_search_offers_results_and_queues_the_pick(self):
        self.mod.inline = _Inline()
        self.mod._client.tg_id = 1
        self.mod._search_sync = lambda query, count: [
            {"url": "https://youtu.be/1", "title": "One", "duration": 61, "channel": None},
            {"url": "https://youtu.be/2", "title": "Two", "duration": None, "channel": None},
        ]
        await self.mod.vcsearch(_Message("songs"))
        form = self.mod.inline.forms[-1]
        self.assertIn("1:01", form.markup[0][0]["text"])
        pick = form.markup[1][0]
        await pick["callback"](_Call(1), *pick["args"])
        self.assertEqual(self.calls.played[-1][1], "https://youtu.be/2")


class OptimizationTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player

    def test_clean_command_matches_pytgcalls_rules(self):
        with mock.patch.dict(
            voicechat._SUPPORTED_OPTIONS,
            {"ffmpeg": {"-i", "-ss", "-v", "-f", "-headers"}},
        ):
            command = ["ffmpeg", "-ss", "5", "-bogus", "x", "-i", "u", "-v",
                       "-1", "-f", "s16le", "", "pipe:1"]
            self.assertEqual(
                voicechat.clean_command(command),
                ["ffmpeg", "-ss", "5", "-i", "u", "-v", "-1", "-f", "s16le",
                 "pipe:1"],
            )
            self.assertEqual(
                voicechat.clean_command(["ffmpeg", "-ss", "1", "-i", "u"],
                                        blacklist=["-ss"]),
                ["ffmpeg", "-i", "u"],
            )

    def test_supported_options_are_read_once(self):
        voicechat._SUPPORTED_OPTIONS.pop("fakeprog", None)
        result = types.SimpleNamespace(stdout="  -foo  <int>\n  -bar, -baz x\n")
        with mock.patch.object(voicechat.subprocess, "run", return_value=result) as run:
            self.assertIn("-baz", voicechat.supported_options("fakeprog"))
            voicechat.supported_options("fakeprog")
        run.assert_called_once()
        voicechat._SUPPORTED_OPTIONS.pop("fakeprog", None)

    async def test_repeated_link_is_resolved_from_cache(self):
        mod = voicechat.VoiceChatMod()
        mod.lookup = lambda name: None
        calls = []

        def extract(track, context):
            calls.append(track.source)
            return {"title": "T", "url": "u", "acodec": "opus", "vcodec": "none"}

        mod._extract_sync = extract
        mod._yt_context = lambda url: {}
        await mod._resolve(voicechat.Track("https://youtu.be/x"))
        await mod._resolve(voicechat.Track("https://youtu.be/x"))
        self.assertEqual(len(calls), 1)
        again = voicechat.Track("https://youtu.be/x")
        again.fresh = True  # e.g. recovering from an expired stream URL
        await mod._resolve(again)
        self.assertEqual(len(calls), 2)

    def test_cached_info_keeps_only_what_playback_uses(self):
        info = {
            "title": "T", "duration": 9, "formats": [{"url": "f"}] * 500,
            "thumbnails": [{}] * 50, "description": "x" * 5000,
            "requested_formats": [
                {"url": "v", "vcodec": "vp9", "acodec": "none", "width": 1,
                 "height": 1, "fragments": [{}] * 100},
                {"url": "a", "vcodec": "none", "acodec": "opus"},
            ],
        }
        slim = voicechat.slim_info(info)
        self.assertNotIn("formats", slim)
        self.assertNotIn("fragments", slim["requested_formats"][0])
        track = voicechat.Track("https://youtu.be/x")
        self.mod._apply_info(track, slim)
        self.assertEqual((track.video_url, track.audio_url), ("v", "a"))

    def test_working_youtube_client_is_tried_first_next_time(self):
        mod = voicechat.VoiceChatMod()
        mod.config["cookies_file"] = ""
        seen = []

        class YoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                clients = (self.options.get("extractor_args", {})
                           .get("youtube", {}).get("player_client"))
                seen.append(clients)
                if clients == ["android_vr"]:
                    return {"title": "ok", "url": "u"}
                raise RuntimeError("Sign in to confirm you're not a bot")

        with mock.patch.dict(
            sys.modules, {"yt_dlp": types.SimpleNamespace(YoutubeDL=YoutubeDL)}
        ):
            mod._extract_sync(voicechat.Track("https://youtu.be/a"), {})
            first_round = len(seen)
            seen.clear()
            mod._extract_sync(voicechat.Track("https://youtu.be/b"), {})
        self.assertGreater(first_round, 1)
        self.assertEqual(seen, [["android_vr"]])

    def test_frames_are_dropped_before_cheap_scaling(self):
        track = voicechat.Track("https://x/v.mp4")
        track.video_url = track.audio_url = "https://x/v.mp4"
        track.width, track.height = 3840, 2160
        params = self.mod._ffmpeg_parameters(track, frame=(1280, 720, 30))
        self.assertNotIn("lanczos", params)
        video = shlex.split(params)[shlex.split(params).index("--video"):]
        self.assertEqual(
            video[video.index("-atend"):],
            ["-atend", "-an", "-sn", "-dn", "-vf",
             "fps=30,scale=1280:720:flags=bilinear"],
        )
        # Without exact frame data py-tgcalls' own filter stays.
        self.assertNotIn("-vf", self.mod._ffmpeg_parameters(track))

    def test_format_choice_caps_fps_and_prefers_cheap_codec(self):
        self.mod.config["video_quality"] = "1080p"
        self.mod.config["max_fps"] = 30
        sort = self.mod._ydl_options(False)["format_sort"]
        self.assertEqual(sort, ["res:1080", "fps:30", "hdr:sdr", "vcodec:h264"])
        self.mod.config["video_quality"] = "4k"
        self.assertIn("vcodec:vp9", self.mod._ydl_options(False)["format_sort"])

    def test_periodic_saves_are_throttled(self):
        writes = []
        self.mod.set = lambda key, value: writes.append(value)
        self.mod._save_state()
        self.mod._save_state(periodic=True)
        self.mod._save_state(periodic=True)
        self.assertEqual(len(writes), 1)
        self.mod._saved_at -= voicechat.SAVE_EVERY
        self.mod._save_state(periodic=True)
        self.assertEqual(len(writes), 2)


class RawStreamTest(unittest.TestCase):
    """The stream is built without py-tgcalls' probing, and stays correct."""

    def _stubs(self):
        def build_command(name, params, path, parameters, before, headers, live):
            return ["ffmpeg", *(shlex.split(params) if params else []),
                    "-reconnect", "1", "-reconnect_at_eof", "1", "-i", path,
                    "-f", "x", "pipe:1"]

        class Raw:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        raw = types.SimpleNamespace(
            AudioParameters=Raw, VideoParameters=Raw, AudioStream=Raw,
            VideoStream=Raw, Stream=Raw,
        )
        modules = {
            "ntgcalls": types.SimpleNamespace(MediaSource=types.SimpleNamespace(SHELL="shell")),
            "pytgcalls": types.ModuleType("pytgcalls"),
            "pytgcalls.ffmpeg": types.SimpleNamespace(build_command=build_command),
            "pytgcalls.list_to_cmd": types.SimpleNamespace(list_to_cmd=shlex.join),
            "pytgcalls.types": types.ModuleType("pytgcalls.types"),
            "pytgcalls.types.raw": raw,
        }
        return mock.patch.dict(sys.modules, modules)

    def test_raw_stream_has_no_reconnect_at_eof_and_uses_screen(self):
        mod = voicechat.VoiceChatMod()
        track = voicechat.Track("https://x/a.m3u8")
        track.video_url = track.audio_url = "https://x/a.m3u8"
        track.width, track.height, track.fps = 1280, 720, 25
        with self._stubs(), mock.patch.object(voicechat, "patch_pytgcalls"), \
                mock.patch.object(voicechat, "clean_command", lambda c: c), \
                mock.patch.object(voicechat, "_hls_demuxer_options", lambda: set()):
            stream = mod._raw_stream(track, offset=5)
        screen = stream.kwargs["screen"]
        microphone = stream.kwargs["microphone"]
        for device in (screen, microphone):
            command = device.args[1]
            self.assertNotIn("-reconnect_at_eof", command)
            self.assertIn("-ss 5", command)
        self.assertEqual(screen.args[2].args[:2], (1280, 720))
        self.assertIn("fps=25,scale=1280:720:flags=bilinear", screen.args[1])

    def test_unknown_frame_size_falls_back_to_full_check(self):
        mod = voicechat.VoiceChatMod()
        track = voicechat.Track("https://x/a.mp4")
        track.video_url = track.audio_url = "https://x/a.mp4"
        self.assertIsNone(mod._raw_stream(track))


class FastPrebufferTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = TelegramStreamTest.asyncSetUp

    async def test_fast_download_starts_before_the_full_buffer(self):
        data = bytes(range(256)) * 40000  # ~10 MB, 20 chunks
        client = _TgClient(data)
        url = await self.streamer.register(
            client, types.SimpleNamespace(chat_id=-1, id=5), len(data)
        )
        cache = self.streamer.cache(url)
        await cache.set_window(4)  # stop the workers after four chunks
        with mock.patch.object(
            type(cache), "speed", new_callable=mock.PropertyMock, return_value=10**9
        ):
            await self.streamer.prebuffer(
                url, 0, len(data), rate=1000, enough=2 * voicechat.TG_CHUNK,
                timeout=5,
            )
        self.assertLess(cache.buffered_from(0), cache.chunks)


class _Proc:
    def __init__(self, pid, name, cpu):
        self.pid, self._name, self._cpu = pid, name, cpu

    def name(self):
        return self._name

    def cpu_percent(self, interval=None):
        return self._cpu

    def children(self, recursive=False):
        return self.kids


def _psutil(system, hikka=50.0, ffmpeg=(20.0, 10.0)):
    me = _Proc(1, "python", hikka)
    me.kids = [_Proc(10 + i, "ffmpeg", c) for i, c in enumerate(ffmpeg)]
    me.kids.append(_Proc(99, "sh", 5.0))
    return types.SimpleNamespace(
        Process=lambda: me,
        cpu_count=lambda: 2,
        cpu_percent=lambda interval=None: system,
        virtual_memory=lambda: types.SimpleNamespace(used=2 * 2**30, total=4 * 2**30),
    )


class AutoQualityTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player
    _last_answer = PlaybackTest._last_answer

    async def _play_video(self):
        await self.mod.vcplay(_Message("https://a"))
        track = self._player().current
        track.width, track.height = 1920, 1080
        return track

    async def test_overload_steps_resolution_down_and_keeps_position(self):
        track = await self._play_video()
        self.mod.config["video_quality"] = "1080p"
        player = self._player()
        player.started_at -= 40
        with mock.patch.dict(sys.modules, {"psutil": _psutil(97)}):
            await self.mod._watch_load(player)
            self.assertEqual(track.quality_drop, 0)  # one busy sample is not enough
            await self.mod._watch_load(player)
        self.assertEqual(track.quality_drop, 1)
        self.assertEqual(self.mod._video_cap(track), "720p")
        self.assertIn("1280×720", self.mod._quality_label(track))
        self.assertAlmostEqual(self.calls.played[-1][2], 40, delta=1)
        texts = [c.args[1] for c in self.mod._client.send_message.await_args_list]
        self.assertTrue(any("720p" in t for t in texts))

    async def test_calm_cpu_changes_nothing_and_next_track_steps_back_up(self):
        track = await self._play_video()
        player = self._player()
        with mock.patch.dict(sys.modules, {"psutil": _psutil(40)}):
            await self.mod._watch_load(player)
        self.assertEqual(track.quality_drop, 0)
        player.quality_drop = 2
        await self.mod.vcadd(_Message("https://b"))
        await self.mod.vcskip(_Message())
        self.assertEqual(self._player().current.quality_drop, 1)

    async def test_module_cpu_over_limit_steps_down_on_a_big_server(self):
        # 4 % of the whole server, but Hikka + ffmpeg take two cores.
        track = await self._play_video()
        self.mod.config["video_quality"] = "1080p"
        player = self._player()
        with mock.patch.dict(sys.modules, {"psutil": _psutil(40, hikka=170.0)}):
            await self.mod._watch_load(player)
            await self.mod._watch_load(player)
        self.assertEqual(track.quality_drop, 1)
        # The next track does not step back up while the module stays busy.
        await self.mod.vcadd(_Message("https://b"))
        await self.mod.vcskip(_Message())
        self.assertEqual(self._player().current.quality_drop, 1)

    async def test_cpu_limit_zero_watches_only_the_server(self):
        track = await self._play_video()
        self.mod.config["cpu_limit"] = 0
        with mock.patch.dict(sys.modules, {"psutil": _psutil(40, hikka=400.0)}):
            for _ in range(3):
                await self.mod._watch_load(self._player())
        self.assertEqual(track.quality_drop, 0)

    async def test_auto_quality_can_be_switched_off(self):
        track = await self._play_video()
        self.mod.config["auto_quality"] = False
        with mock.patch.dict(sys.modules, {"psutil": _psutil(100)}):
            for _ in range(3):
                await self.mod._watch_load(self._player())
        self.assertEqual(track.quality_drop, 0)

    async def test_stats_report(self):
        await self._play_video()
        with mock.patch.dict(sys.modules, {"psutil": _psutil(95)}), \
                mock.patch.object(voicechat.asyncio, "sleep", side_effect=_no_sleep):
            await self.mod.vcstats(_Message())
        text = self._last_answer()
        self.assertIn("95%", text)
        self.assertIn("ffmpeg (декодування): <b>15%</b> · процесів: 2", text)
        self.assertIn("перевантажений", text)

    def test_stats_without_psutil(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertIsNone(self.mod._load())


class ReconnectTest(unittest.IsolatedAsyncioTestCase):
    """Self-healing when the call connection silently dies (from #109)."""

    _player = PlaybackTest._player
    _last_answer = PlaybackTest._last_answer

    def setUp(self):
        PlaybackTest.setUp(self)
        patcher = mock.patch.object(voicechat, "RECONNECT_DELAYS", (0, 0, 0))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _settle(self):
        player = self._player() if CHAT in self.mod._players else None
        while player is not None and player.rejoin_task is not None and not player.rejoin_task.done():
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    def _age(self, seconds):
        player = self._player()
        player.switched_at -= seconds
        player.started_at -= seconds
        player.progress_at -= seconds

    async def test_lost_connection_rejoins_at_same_position(self):
        await self.mod.vcplay(_Message("https://a"))
        self._age(42)
        state = types.SimpleNamespace(state=types.SimpleNamespace(name="FAILED"))
        self.mod._on_connection_changed(CHAT, state)
        await self._settle()
        self.calls.leave_call.assert_awaited_with(CHAT)
        self.assertEqual(self.calls.played[-1], (CHAT, "https://a", 42))
        self.assertEqual(self._player().reconnects, 1)
        texts = [c.args[1] for c in self.mod._client.send_message.await_args_list]
        self.assertTrue(any("відновлено" in t for t in texts))

    async def test_connection_events_while_joining_are_ignored(self):
        await self.mod.vcplay(_Message("https://a"))
        state = types.SimpleNamespace(state=types.SimpleNamespace(name="CLOSED"))
        self.mod._on_connection_changed(CHAT, state)
        connected = types.SimpleNamespace(state=types.SimpleNamespace(name="CONNECTED"))
        self._age(30)
        self.mod._on_connection_changed(CHAT, connected)
        self.assertIsNone(self._player().rejoin_task)

    async def test_hook_keeps_original_handler(self):
        calls = types.SimpleNamespace(_handle_connection_changed=mock.AsyncMock())
        self.mod._hook_connection(calls)
        self.mod._on_connection_changed = mock.Mock()
        await calls._handle_connection_changed(CHAT, "state")
        self.mod._on_connection_changed.assert_called_once_with(CHAT, "state")

    async def test_health_check_rejoins_dropped_call(self):
        await self.mod.vcplay(_Message("https://a"))
        self._age(30)
        self.calls.active = set()
        await self.mod._check_player(self._player())
        await self._settle()
        self.assertEqual(len(self.calls.played), 2)

    async def test_frozen_stream_rejoins_only_after_progress_was_seen(self):
        await self.mod.vcplay(_Message("https://a"))
        player = self._player()
        self._age(30)
        await self.mod._check_player(player)
        self._age(30)
        await self.mod._check_player(player)
        self.assertIsNone(player.rejoin_task)
        self.calls.played_time = 5
        await self.mod._check_player(player)
        self._age(30)
        await self.mod._check_player(player)
        await self._settle()
        self.assertEqual(len(self.calls.played), 2)

    async def test_paused_track_is_not_stalled_and_stays_paused(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcpause(_Message())
        player = self._player()
        player.progress_seen = True
        self._age(60)
        await self.mod._check_player(player)
        self.assertIsNone(player.rejoin_task)
        self.calls.active = set()
        await self.mod._check_player(player)
        await self._settle()
        self.assertEqual(len(self.calls.played), 2)
        self.assertTrue(player.paused)
        self.assertEqual(self.calls.pause.await_count, 2)

    async def test_gives_up_after_repeated_failures(self):
        await self.mod.vcplay(_Message("https://a"))
        self._age(30)
        self.calls.fail_sources = {"https://a"}
        self.calls.active = set()
        player = self._player()
        await self.mod._check_player(player)
        while player.rejoin_task is not None and not player.rejoin_task.done():
            await asyncio.sleep(0)
        self.assertNotIn(CHAT, self.mod._players)
        texts = [c.args[1] for c in self.mod._client.send_message.await_args_list]
        self.assertTrue(any("не вдалося" in t for t in texts))

    async def test_stop_cancels_pending_rejoin(self):
        await self.mod.vcplay(_Message("https://a"))
        self._age(30)
        with mock.patch.object(voicechat, "RECONNECT_DELAYS", (10,)):
            self.mod._schedule_rejoin(self._player(), "test")
            task = self._player().rejoin_task
            await self.mod.vcstop(_Message())
            await asyncio.sleep(0)
        self.assertTrue(task.cancelled())
        self.assertEqual(len(self.calls.played), 1)


if __name__ == "__main__":
    unittest.main()


def _song(message_id, chat_id=-1002562411082):
    file = types.SimpleNamespace(
        size=1000,
        mime_type="audio/mpeg",
        ext=".mp3",
        title=f"Song {message_id}",
        performer="Artist",
        name=None,
        duration=100,
    )
    return types.SimpleNamespace(
        id=message_id, chat_id=chat_id, audio=True, voice=None, file=file
    )


class _RadioClient:
    def __init__(self, songs):
        self.songs = {song.id: song for song in songs}
        self.send_message = mock.AsyncMock(
            side_effect=lambda chat, text, **k: types.SimpleNamespace(id=900)
        )
        self.edit_message = mock.AsyncMock()
        self.reads = 0

    async def get_input_entity(self, peer):
        return peer

    async def iter_messages(self, channel, limit=None, filter=None):
        self.reads += 1
        for song in sorted(self.songs.values(), key=lambda s: -s.id)[:limit]:
            yield song
        yield types.SimpleNamespace(id=1, file=None, voice=None)  # a text post

    async def get_messages(self, channel, ids=None):
        return self.songs.get(ids)


class MemoryAndMessagesTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player
    _last_answer = PlaybackTest._last_answer
    _end = PlaybackTest._end

    def test_channel_formats(self):
        self.assertEqual(voicechat.parse_channel("-1002562411082"), -1002562411082)
        self.assertEqual(voicechat.parse_channel("@music_ua"), "music_ua")
        self.assertEqual(voicechat.parse_channel("https://t.me/music_ua"), "music_ua")
        self.assertEqual(
            voicechat.parse_channel("t.me/c/2562411082/15"), -1002562411082
        )
        self.assertIsNone(voicechat.parse_channel(" "))

    async def test_volume_is_remembered_for_the_next_session(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcvol(_Message("150"))
        await self.mod.vcstop(_Message())
        self.assertNotIn(CHAT, self.mod._players)
        self.calls.change_volume_call.reset_mock()

        await self.mod.vcplay(_Message("https://b"))
        self.assertEqual(self._player().volume, 150)
        self.calls.change_volume_call.assert_awaited_with(CHAT, 150)

    async def test_next_track_edits_the_now_playing_text(self):
        self.mod._client.edit_message = mock.AsyncMock()
        status = types.SimpleNamespace(id=55)
        vc_utils.answer.side_effect = lambda message, *a, **k: status
        self.addCleanup(
            setattr, vc_utils.answer, "side_effect", lambda message, *a, **k: message
        )
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        self.assertEqual(self._player().now_message, 55)

        await self._end("AUDIO", "VIDEO")
        self.assertEqual(self.calls.played[-1][1], "https://b")
        self.mod._client.send_message.assert_not_awaited()
        chat, message_id, text = self.mod._client.edit_message.await_args.args
        self.assertEqual((chat, message_id), (CHAT, 55))
        self.assertIn("https://b", text)

    async def test_text_falls_back_to_a_new_message_when_edit_fails(self):
        self.mod._client.edit_message = mock.AsyncMock(side_effect=RuntimeError("gone"))
        player = self.mod._player(CHAT)
        player.now_message = 55
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        await self._end("AUDIO", "VIDEO")
        self.mod._client.send_message.assert_awaited()

    async def test_next_track_edits_the_same_panel(self):
        self.mod.inline = _Inline()
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        panel = self._player().panel
        await self._end("AUDIO", "VIDEO")
        self.assertIs(self._player().panel, panel)
        self.assertEqual(len(self.mod.inline.forms), 1)
        self.assertIn("https://b", panel.text)
        self.assertFalse(panel.deleted)

        await self.mod.vcplay(_Message("https://c"))
        await self.mod.vcadd(_Message("https://d"))
        await self.mod.vcskip(_Message())
        self.assertEqual(len(self.mod.inline.forms), 1)
        self.assertIn("https://d", panel.text)
        self.assertIn("Далі", self._last_answer())


class RadioTest(unittest.IsolatedAsyncioTestCase):
    _player = PlaybackTest._player
    _last_answer = PlaybackTest._last_answer
    _end = PlaybackTest._end

    def setUp(self):
        PlaybackTest.setUp(self)
        self.client = _RadioClient([_song(10), _song(11), _song(12)])
        self.mod._client = self.client
        self.mod.config["radio_channel"] = "-1002562411082"
        self.mod.config["radio_shuffle"] = False

        async def register(client, message, size, mime=None, ext=""):
            return f"http://127.0.0.1/tg/{message.id}"

        self.mod._streamer.register = register
        self.mod._streamer.unregister = mock.Mock()

    def _sources(self):
        return [source for _, source, _ in self.calls.played]

    async def _settle(self):
        player = self._player()
        if player.radio_task is not None:
            await asyncio.wait([player.radio_task])

    async def test_radio_plays_the_channel_round_and_round(self):
        await self.mod.vcradio(_Message("on"))
        self.assertTrue(self._player().radio)
        for _ in range(4):
            await self._settle()
            await self._end("AUDIO")
        self.assertEqual(
            self._sources(),
            [f"http://127.0.0.1/tg/{n}" for n in (10, 11, 12, 10, 11)],
        )
        self.calls.leave_call.assert_not_awaited()
        self.assertTrue(self._player().current.radio)
        self.assertIn("📻", self.mod._panel_text(self._player()))
        settings = self.mod.get("chat_settings")[str(CHAT)]
        self.assertTrue(settings["radio"])
        self.assertEqual(settings["radio_last"], 11)

    async def test_queued_tracks_go_before_the_radio(self):
        await self.mod.vcradio(_Message("on"))
        await self.mod.vcadd(_Message("https://mine"))
        await self._settle()
        await self._end("AUDIO")
        self.assertEqual(self._sources()[-1], "https://mine")
        await self._end("AUDIO", "VIDEO")
        self.assertEqual(self._sources()[-1], "http://127.0.0.1/tg/11")

    async def test_radio_continues_after_the_last_played_song(self):
        self.mod._store_settings(CHAT, radio_last=11)
        await self.mod.vcradio(_Message("on"))
        self.assertEqual(self._sources(), ["http://127.0.0.1/tg/12"])

    async def test_shuffle_keeps_every_song_and_moves_the_last_one_back(self):
        self.mod.config["radio_shuffle"] = True
        player = self.mod._player(CHAT)
        player.radio_last = 11
        order = await self.mod._radio_reload(player)
        self.assertEqual(sorted(order), [10, 11, 12])
        self.assertEqual(order[-1], 11)

    async def test_off_lets_the_song_finish_and_stop_forgets_the_radio(self):
        await self.mod.vcradio(_Message("on"))
        await self.mod.vcradio(_Message("off"))
        self.assertIn("вимкнено", self._last_answer())
        await self._end("AUDIO")
        self.calls.leave_call.assert_awaited_with(CHAT)

        await self.mod.vcradio(_Message("on"))
        await self.mod.vcstop(_Message())
        self.assertFalse(self.mod.get("chat_settings")[str(CHAT)]["radio"])

    async def test_radio_needs_a_channel(self):
        self.mod.config["radio_channel"] = ""
        await self.mod.vcradio(_Message("on"))
        self.assertIn("не вказано", self._last_answer())
        self.calls.play.assert_not_awaited()
        self.assertNotIn(CHAT, self.mod._players)

    async def test_empty_channel_is_reported(self):
        self.client.songs = {}
        await self.mod.vcradio(_Message("on"))
        self.assertIn("немає музики", self._last_answer())
        self.assertNotIn(CHAT, self.mod._players)

    async def test_channel_from_the_command_is_remembered(self):
        self.mod.config["radio_channel"] = ""
        await self.mod.vcradio(_Message("on @music_ua"))
        self.assertEqual(self._sources(), ["http://127.0.0.1/tg/10"])
        settings = self.mod.get("chat_settings")[str(CHAT)]
        self.assertEqual(settings["radio_channel"], "@music_ua")
        await self.mod.vcradio(_Message(""))
        self.assertIn("@music_ua", self._last_answer())

    async def test_radio_starts_again_after_a_restart(self):
        self.mod._store_settings(CHAT, radio=True)
        self.mod._prepare_chat = mock.AsyncMock(return_value=None)
        with mock.patch.object(voicechat, "RESTORE_DELAY", 0):
            await self.mod._restore_all()
        self.assertEqual(self._sources(), ["http://127.0.0.1/tg/10"])
        self.assertTrue(self._player().radio)


class _SlowInline(_Inline):
    """Hikka's form() that waits for the bot's confirmation."""

    def __init__(self):
        super().__init__()
        self.confirmed = asyncio.Event()

    async def form(self, text, message=None, reply_markup=None, **kwargs):
        await self.confirmed.wait()
        return await super().form(text, message, reply_markup, **kwargs)


class SlowPanelTest(unittest.IsolatedAsyncioTestCase):
    _player = PlaybackTest._player
    _end = PlaybackTest._end

    def setUp(self):
        PlaybackTest.setUp(self)
        self.mod.inline = _SlowInline()
        self.mod.inline.bot_username = "hikka_test_bot"
        self.mod._client.edit_message = mock.AsyncMock()
        self.mod._client.delete_messages = mock.AsyncMock()
        status = types.SimpleNamespace(id=77)
        vc_utils.answer.side_effect = lambda message, *a, **k: status
        self.addCleanup(
            setattr, vc_utils.answer, "side_effect", lambda message, *a, **k: message
        )
        patcher = mock.patch.object(voicechat, "PANEL_TIMEOUT", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hints(self):
        return [
            c.args[1]
            for c in self.mod._client.send_message.await_args_list
            if "setinlinefeedback" in c.args[1]
        ]

    async def test_unconfirmed_panel_turns_into_a_text_panel(self):
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        player = self._player()
        self.assertIsNone(player.panel)
        self.assertEqual(player.now_message, 77)
        self.assertFalse(player.lock.locked())
        self.assertEqual(len(self._hints()), 1)
        self.assertIn("@hikka_test_bot", self._hints()[0])

        # The text panel keeps its progress current …
        player.started_at -= 30
        await self.mod._update_panel(player)
        chat, message_id, text = self.mod._client.edit_message.await_args.args
        self.assertEqual((chat, message_id), (CHAT, 77))
        self.assertIn("0:30", text)

        # … and shows the next track without new panels or hints.
        await self._end("AUDIO", "VIDEO")
        self.assertEqual(self.calls.played[-1][1], "https://b")
        self.assertEqual(len(self.mod.inline.forms), 0)
        self.assertIn("https://b", self.mod._client.edit_message.await_args.args[2])
        self.assertEqual(len(self._hints()), 1)

    async def test_vcnow_tries_the_inline_panel_again(self):
        await self.mod.vcplay(_Message("https://a"))
        self.mod.inline.confirmed.set()  # e.g. inline feedback switched on
        await self.mod.vcnow(_Message())
        self.assertIsNotNone(self._player().panel)
        self.assertIsNone(self._player().now_message)
        self.mod._client.delete_messages.assert_awaited_with(CHAT, 77)


class _DeadPanel(_Panel):
    async def edit(self, text, reply_markup=None):
        return False  # what Hikka returns without an inline_message_id


class _DeadInline(_Inline):
    async def form(self, text, message=None, reply_markup=None, **kwargs):
        panel = _DeadPanel(text, reply_markup)
        self.forms.append(panel)
        return panel


class DeadPanelTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player

    async def test_panel_that_cannot_be_edited_is_replaced_by_text(self):
        self.mod.inline = _DeadInline()
        self.mod._client.send_message = mock.AsyncMock(
            side_effect=lambda chat, text, **k: types.SimpleNamespace(id=5)
        )
        self.mod._client.edit_message = mock.AsyncMock()
        await self.mod.vcplay(_Message("https://a"))
        player = self._player()
        panel = player.panel
        for seconds in range(1, voicechat.PANEL_FAILURES + 1):
            player.started_at -= 15
            await self.mod._update_panel(player)
        self.assertIsNone(player.panel)
        self.assertTrue(panel.deleted)
        self.assertEqual(player.now_message, 5)
        texts = [c.args[1] for c in self.mod._client.send_message.await_args_list]
        self.assertTrue(any("setinlinefeedback" in t for t in texts))
        self.assertIn("0:45", texts[-1])  # the text panel, progress included

        player.started_at -= 15
        await self.mod._update_panel(player)
        self.assertIn("1:00", self.mod._client.edit_message.await_args.args[2])
        await self.mod._show_now(player)
        self.assertEqual(len(self.mod.inline.forms), 1)  # no new inline panels


class _RichPanel(_Panel):
    inline_message_id = "AAAAinline"


class _RichInline(_Inline):
    """Hikka's inline manager with a bot token and generate_markup()."""

    _token = "123:abc"

    def __init__(self):
        super().__init__()
        self.custom_map = {}

    async def form(self, text, message=None, reply_markup=None, **kwargs):
        panel = _RichPanel(text, reply_markup)
        self.forms.append(panel)
        return panel

    def generate_markup(self, markup):
        for row in markup:
            for button in row:
                if "_callback_data" not in button:
                    button["_callback_data"] = f"cb{len(self.custom_map)}"
                    self.custom_map[button["_callback_data"]] = button
        return markup


class RichPanelTest(unittest.IsolatedAsyncioTestCase):
    setUp = PlaybackTest.setUp
    _player = PlaybackTest._player

    def _api(self, answer=None):
        self.sent = []

        async def call(method, payload):
            self.sent.append((method, payload))
            return answer or {"ok": True, "result": True}

        self.mod._bot_api = call

    @staticmethod
    def _buttons(payload):
        return [
            button
            for block in payload["rich_message"]["blocks"]
            if block["type"] == "buttons"
            for button in block["buttons"]
        ]

    async def test_panel_is_shown_and_kept_as_rich_text(self):
        self.mod.inline = _RichInline()
        self._api()
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        method, payload = self.sent[0]
        self.assertEqual(method, "editMessageText")
        self.assertEqual(payload["inline_message_id"], "AAAAinline")
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": []})
        blocks = payload["rich_message"]["blocks"]
        self.assertEqual(blocks[0]["type"], "heading")
        self.assertEqual(blocks[0]["text"][1]["url"], "https://a")
        self.assertEqual(blocks[2]["type"], "table")
        self.assertEqual(blocks[-1]["type"], "footer")
        styles = {b["text"]: b.get("style") for b in self._buttons(payload)}
        self.assertEqual(styles["⏸"], "primary")
        self.assertEqual(styles["⏹"], "danger")

        # The "next" line and the progress come with later edits; the
        # buttons keep their callback data, nothing is registered twice.
        _, payload = self.sent[-1]
        quote = [b for b in payload["rich_message"]["blocks"] if b["type"] == "blockquote"]
        self.assertEqual(quote[0]["blocks"][0]["text"][1]["text"], "https://b")
        registered = len(self.mod.inline.custom_map)
        player = self._player()
        player.started_at -= 30
        await self.mod._update_panel(player)
        _, payload = self.sent[-1]
        self.assertIn("0:30", payload["rich_message"]["blocks"][1]["text"][1]["text"])
        self.assertEqual(len(self.mod.inline.custom_map), registered)
        self.assertEqual(self.mod.inline.forms[0].edits, [])  # never the HTML edit

        # A rich button runs the same panel action.
        self.mod._client.tg_id = 1
        pause = next(b for b in self._buttons(payload) if b["text"] == "⏸")
        button = self.mod.inline.custom_map[pause["callback_data"]]
        await button["callback"](_Call(1), *button["args"])
        self.assertTrue(player.paused)
        _, payload = self.sent[-1]
        self.assertIn("▶️", [b["text"] for b in self._buttons(payload)])

    async def test_refused_rich_text_falls_back_to_the_html_panel(self):
        self.mod.inline = _RichInline()
        self._api({"ok": False, "description": "Bad Request: rich messages are not supported"})
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        panel = self._player().panel
        self.assertTrue(self.mod._rich_broken)
        self.assertIs(panel, self.mod.inline.forms[0])
        self.assertIn("https://b", panel.text)  # edited the usual way
        self.assertEqual(len(self.sent), 1)

    async def test_html_style_keeps_the_classic_panel(self):
        self.mod.inline = _RichInline()
        self.mod.config["panel_style"] = "html"
        self._api()
        await self.mod.vcplay(_Message("https://a"))
        await self.mod.vcadd(_Message("https://b"))
        self.assertEqual(self.sent, [])
        self.assertIn("https://b", self._player().panel.text)


class _FailingInline(_Inline):
    """Hikka's form() when the inline query brings no results: it posts the
    traceback to the chat itself and returns False."""

    def __init__(self, chat):
        super().__init__()
        self.calls = 0
        self.chat = chat

    async def form(self, text, message=None, reply_markup=None, **kwargs):
        self.calls += 1
        self.chat.append(
            types.SimpleNamespace(
                id=900 + self.calls,
                out=True,
                date=None,
                raw_text=(
                    "🚫 Виклик юніта завершився невдачею!\n\n📖 Логи:\n"
                    '  File "/hikka/inline/core.py", line 262, in _invoke_unit\n'
                    "Exception: No query results"
                ),
                delete=mock.AsyncMock(),
            )
        )
        return False


class FailedPanelTest(unittest.IsolatedAsyncioTestCase):
    _player = PlaybackTest._player

    def setUp(self):
        PlaybackTest.setUp(self)
        self.chat = []
        self.mod.inline = _FailingInline(self.chat)
        self.mod._client.edit_message = mock.AsyncMock()
        self.mod._client.delete_messages = mock.AsyncMock()

        async def iter_messages(chat_id, limit=None):
            for message in reversed(self.chat):
                yield message

        self.mod._client.iter_messages = iter_messages

    async def test_vcnow_tries_once_and_removes_hikkas_traceback(self):
        await self.mod.vcplay(_Message("https://a"))
        self.mod.inline.calls = 0
        self.chat.clear()
        self._player().now_message = 77
        await self.mod.vcnow(_Message())
        self.assertEqual(self.mod.inline.calls, 1)
        self.assertEqual(len(self.chat), 1)
        self.chat[0].delete.assert_awaited()
        self.assertIsNone(self._player().panel)
        self.assertIn("https://a", vc_utils.answer.call_args.args[1])

    async def test_failed_panel_is_not_retried_on_every_track(self):
        await self.mod.vcplay(_Message("https://a"))
        calls = self.mod.inline.calls
        await self.mod._show_panel(self._player())
        self.assertEqual(self.mod.inline.calls, calls)
