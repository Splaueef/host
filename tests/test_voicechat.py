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
        self.refetched = False
        self.download_media = mock.AsyncMock()

    async def iter_download(self, message, offset=0, request_size=0, file_size=None):
        assert offset % request_size == 0, offset
        self.downloads += 1
        if self.expire_first and not self.refetched:
            raise RuntimeError("FILE_REFERENCE_EXPIRED")
        while offset < len(self.data):
            yield self.data[offset:offset + request_size]
            offset += request_size

    async def get_messages(self, chat_id, ids=None):
        self.refetched = True
        return types.SimpleNamespace(chat_id=chat_id, id=ids)


class TelegramStreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = bytes(range(256)) * 9000  # ~2.2 MB, several chunks
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


if __name__ == "__main__":
    unittest.main()
