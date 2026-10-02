"""Regression tests for VideoDownloader's cookie/session helpers."""

import importlib.util
import os
import pathlib
import sqlite3
import sys

_REPO = pathlib.Path(__file__).parents[1]
sys.path[:] = [entry for entry in sys.path if entry not in ("", str(_REPO))]

import asyncio
import tempfile
import types
import unittest
import unittest.mock


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = object
    loader.tds = lambda value: value
    loader.command = lambda *args, **kwargs: (lambda value: value)
    loader.watcher = lambda *args, **kwargs: (lambda value: value)
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    telethon_tl = types.ModuleType("telethon.tl")
    telethon_types = types.ModuleType("telethon.tl.types")
    telethon_messages = types.ModuleType("telethon.tl.functions.messages")
    telethon_types.InputMessagesFilterMusic = object
    class DocumentAttributeVideo:
        def __init__(self, duration, w, h, supports_streaming=False):
            self.duration = duration
            self.w = w
            self.h = h
            self.supports_streaming = supports_streaming
    telethon_types.DocumentAttributeVideo = DocumentAttributeVideo
    telethon_messages.CheckChatInviteRequest = object
    sys.modules.update({
        "testhost": package,
        "testhost.modules": modules,
        "testhost.loader": loader,
        "testhost.utils": utils,
        "telethon": telethon,
        "telethon.tl": telethon_tl,
        "telethon.tl.types": telethon_types,
        "telethon.tl.functions": types.ModuleType("telethon.tl.functions"),
        "telethon.tl.functions.messages": telethon_messages,
    })
    spec = importlib.util.spec_from_file_location("testhost.modules.vdlt", _REPO / "vdlt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vdlt = _load_module()


class CookieManagerTests(unittest.TestCase):
    def test_deployment_path_falls_back_to_data_mount(self):
        original_exists = vdlt.os.path.exists
        try:
            vdlt.os.path.exists = lambda path: path == "/data/home/rkbot/URKbot/cookies.txt"
            self.assertEqual(
                vdlt._deployment_path("/home/rkbot/URKbot/cookies.txt"),
                "/data/home/rkbot/URKbot/cookies.txt",
            )
            self.assertEqual(
                vdlt._deployment_path("/custom/cookies.txt"),
                "/custom/cookies.txt",
            )
        finally:
            vdlt.os.path.exists = original_exists

    def test_file_for_requires_matching_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cookies.txt")
            pathlib.Path(path).write_text(
                "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tvalue\n",
                encoding="utf-8",
            )
            manager = vdlt.CookieManager(path, directory)
            self.assertEqual(manager.file_for("https://instagram.com/reel/1"), path)
            self.assertIsNone(manager.file_for("https://youtube.com/watch?v=1"))

    def test_firefox_profile_checks_cookie_database_and_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "cookies.sqlite")
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE moz_cookies (host TEXT)")
            connection.execute("INSERT INTO moz_cookies VALUES (?)", (".youtube.com",))
            connection.commit()
            connection.close()

            manager = vdlt.CookieManager("", directory)
            self.assertTrue(manager.firefox_profile_valid())
            self.assertTrue(manager.firefox_has_url("https://www.youtube.com/watch?v=1"))
            self.assertFalse(manager.firefox_has_url("https://instagram.com/reel/1"))

    def test_help_version_has_single_source_of_truth(self):
        self.assertEqual(vdlt.VERSION, ".".join(map(str, vdlt.__version__)))
        self.assertIn(f"VideoDownloader v{vdlt.VERSION}", vdlt.VideoDownloaderMod.strings["help_text"])


class VideoFormatTests(unittest.TestCase):
    def test_diagnostics_use_the_supported_ytdlp_cli_prefix(self):
        module = object.__new__(vdlt.VideoDownloaderMod)
        module.config = {"cobalt_api_url": "", "ffmpeg_path": ""}
        module._ytdlp_cli_prefix = lambda: ["python", "-m", "yt_dlp"]
        module._find_executable = lambda configured, names: None
        manager = types.SimpleNamespace(
            cookies_file="/missing/cookies.txt",
            firefox_profile="",
            browser_user="",
            firefox_profile_valid=lambda: False,
        )
        module._cookie_manager = lambda: manager
        answers = []
        original_answer = getattr(vdlt.utils, "answer", None)
        original_escape_html = getattr(vdlt.utils, "escape_html", None)

        async def answer(message, text):
            answers.append(text)

        try:
            vdlt.utils.answer = answer
            vdlt.utils.escape_html = lambda value: value
            asyncio.run(module.vdldiag(types.SimpleNamespace()))
        finally:
            if original_answer is None:
                del vdlt.utils.answer
            else:
                vdlt.utils.answer = original_answer
            if original_escape_html is None:
                del vdlt.utils.escape_html
            else:
                vdlt.utils.escape_html = original_escape_html

        self.assertIn("CLI: ✅ <code>python -m yt_dlp</code>", answers[0])

    def test_fast_options_use_bounded_configured_fragment_concurrency(self):
        module = object.__new__(vdlt.VideoDownloaderMod)
        module.config = {"fragment_workers": 12}
        self.assertEqual(module._fast_ytdlp_opts()["concurrent_fragment_downloads"], 12)
        module.config["fragment_workers"] = 100
        self.assertEqual(module._fast_ytdlp_opts()["concurrent_fragment_downloads"], 16)
        module.config["fragment_workers"] = "invalid"
        self.assertEqual(module._fast_ytdlp_opts()["concurrent_fragment_downloads"], 8)

    def test_cli_parallelizes_fragments_without_forced_transcode(self):
        module = object.__new__(vdlt.VideoDownloaderMod)
        module.config = {
            "fragment_workers": 6, "quality": "720", "max_size": 500,
            "task_timeout": 60, "audio_format": "mp3", "force_ipv4": False,
            "yt_browser_cookies": "", "cookies_file": "",
            "ffmpeg_path": "", "yt_dlp_path": "",
        }
        module._js_runtime = None
        module._ytdlp_cli_prefix = lambda: ["yt-dlp"]
        module._youtube_cookie_candidates = lambda url: [(None, False, "none")]
        module._ffmpeg_location = lambda: None
        module._yt_browser_cookies_value = lambda: ""
        info = {
            "id": "clip", "width": 1280, "height": 720,
            "formats": [{
                "format_id": "v1", "width": 1280, "height": 720,
                "vcodec": "avc1", "acodec": "aac", "ext": "mp4",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "clip.mp4")
            pathlib.Path(output).write_bytes(b"video")
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                if "--dump-json" in command:
                    return types.SimpleNamespace(returncode=0, stdout=__import__("json").dumps(info), stderr="")
                return types.SimpleNamespace(returncode=0, stdout=output + "\n", stderr="")

            original_run = vdlt.subprocess.run
            try:
                vdlt.subprocess.run = fake_run
                result = module._run_ytdlp_cli_sync("https://youtube.com/watch?v=clip", os.path.join(directory, "base"), False)
            finally:
                vdlt.subprocess.run = original_run

        self.assertEqual(result, [output])
        self.assertEqual(len(calls), 1)
        download_command = calls[0]
        self.assertNotIn("--dump-json", download_command)
        self.assertIn("--concurrent-fragments", download_command)
        self.assertEqual(download_command[download_command.index("--concurrent-fragments") + 1], "6")
        self.assertIn("--merge-output-format", download_command)
        self.assertNotIn("--recode-video", download_command)

    def test_media_shape_keeps_square_separate_from_landscape(self):
        self.assertEqual(vdlt._media_shape(1080, 1080), "square")
        self.assertEqual(vdlt._media_shape(1080, 1920), "portrait")
        self.assertEqual(vdlt._media_shape(1920, 1080), "landscape")
        self.assertEqual(vdlt._media_shape(1088, 1080), "square")

    def test_cli_selector_preserves_square_source_aspect(self):
        module = object.__new__(vdlt.VideoDownloaderMod)
        module.config = {"quality": "best"}
        info = {
            "width": 1080,
            "height": 1080,
            "formats": [
                {"format_id": "landscape", "width": 1920, "height": 1080,
                 "vcodec": "avc1", "acodec": "aac", "ext": "mp4", "tbr": 5000},
                {"format_id": "square", "width": 1080, "height": 1080,
                 "vcodec": "vp9", "acodec": "opus", "ext": "webm", "tbr": 2500},
            ],
        }
        selected, _ = module._tuitube_format_value(info, False)
        self.assertEqual(selected, "square")

    def test_actual_requested_dimensions_take_priority(self):
        info = {
            "width": 1080,
            "height": 1080,
            "requested_formats": [
                {"width": 1920, "height": 1080, "vcodec": "avc1"},
                {"vcodec": "none", "acodec": "aac"},
            ],
        }
        self.assertEqual(vdlt._media_dimensions_from_info(info), (1920, 1080))

    def test_final_file_dimensions_are_used_for_telegram(self):
        stream = {
            "codec_type": "video", "width": 1920, "height": 1080,
            "duration": "4.6",
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": -90},
            ],
        }
        completed = types.SimpleNamespace(
            returncode=0,
            stdout=__import__("json").dumps({"streams": [stream], "format": {}}),
        )
        original_which = vdlt.shutil.which
        original_run = vdlt.subprocess.run
        original_file_type = vdlt._file_type
        try:
            vdlt.shutil.which = lambda executable: "/usr/bin/ffprobe"
            vdlt.subprocess.run = lambda *args, **kwargs: completed
            vdlt._file_type = lambda path: "video"
            attribute = vdlt._telegram_video_attribute("downloaded.mp4")
        finally:
            vdlt.shutil.which = original_which
            vdlt.subprocess.run = original_run
            vdlt._file_type = original_file_type
        self.assertEqual((attribute.w, attribute.h), (1080, 1920))
        self.assertEqual(attribute.duration, 5)
        self.assertTrue(attribute.supports_streaming)


class _Storage:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _module(**config):
    """A VideoDownloaderMod without Hikka: dict config, in-memory storage."""
    module = object.__new__(vdlt.VideoDownloaderMod)
    storage = _Storage()
    module.get = storage.get
    module.set = storage.set
    module.storage = storage
    module.strings = lambda key, *args: vdlt.VideoDownloaderMod.strings[key]
    module.get_prefix = lambda: "."
    module.config = {
        "cooldown": 0, "daily_limit": 0, "queue_max": 5, "queue_workers": 2,
        "auto_delete": 0, "cache_hours": 72, "quality": "720", "max_links": 3,
        "enabled": True, "group_whitelist": [-1], "private_whitelist": [],
        "user_blacklist": [], "allow_any_url": False, "audio_mode": False,
        "playlist_enabled": False, "notify_dm": False, "retries": 1,
        "task_timeout": 60,
    }
    module.config.update(config)
    module._stats = vdlt._empty_stats()
    module._last_dl = 0.0
    module._last_dl_by = {}
    module._queue = None
    module._worker_task = None
    module._worker_tasks = []
    module._busy_workers = set()
    module._retire_workers = 0
    module._client = None
    return module


def _install_utils():
    async def run_sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    vdlt.utils.escape_html = lambda value: (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    vdlt.utils.answer = unittest.mock.AsyncMock()
    vdlt.utils.run_sync = run_sync
    vdlt.utils.get_args_raw = lambda message: message.args


class UrlRoutingTests(unittest.TestCase):
    def test_hosts_are_matched_by_domain_not_substring(self):
        self.assertEqual(vdlt._platform_name("https://www.dropbox.com/s/x.mp4"), "Other")
        self.assertEqual(vdlt._platform_name("https://m.youtube.com/watch?v=1"), "YouTube")
        self.assertEqual(vdlt._platform_name("https://vm.tiktok.com/abc"), "TikTok")
        self.assertEqual(vdlt._platform_name("https://example.com/?u=tiktok.com"), "Other")
        self.assertFalse(vdlt._is_supported_url("https://notyoutube.com/watch"))
        self.assertTrue(vdlt._is_supported_url("https://www.instagram.com/reel/abc"))
        self.assertTrue(vdlt._is_youtube_url("https://youtu.be/xyz"))

    def test_www_prefix_is_removed_as_a_label(self):
        self.assertEqual(vdlt._strip_www("www.youtube.com"), "youtube.com")
        # str.lstrip("www.") used to turn this into "eb.whatsapp.com".
        self.assertEqual(vdlt._strip_www("web.whatsapp.com"), "web.whatsapp.com")

    def test_multiple_links_are_extracted_once_with_limit(self):
        module = _module()
        text = "see https://youtu.be/a, https://youtu.be/a and www.tiktok.com/@x/video/1 https://x.com/a"
        self.assertEqual(
            module._extract_urls(text, 2),
            ["https://youtu.be/a", "https://www.tiktok.com/@x/video/1"],
        )
        self.assertEqual(module._extract_url(text), "https://youtu.be/a")


class QueueAndSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_utils()

    async def test_shrinking_workers_never_cancels_a_running_download(self):
        module = _module(queue_workers=2)
        module._queue = asyncio.Queue()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = []

        async def process(url, message, status, audio_override=False):
            started.set()
            await release.wait()
            finished.append(url)

        module._process = process
        module._start_queue_workers()
        self.assertTrue(module._enqueue("https://youtu.be/1", None, None))
        await started.wait()

        module.config["queue_workers"] = 1
        module._start_queue_workers()
        await asyncio.sleep(0)
        release.set()
        await module._queue.join()

        self.assertEqual(finished, ["https://youtu.be/1"])
        await asyncio.sleep(0)
        self.assertEqual(len([t for t in module._worker_tasks if not t.done()]), 1)
        for task in module._worker_tasks:
            task.cancel()

    async def test_worker_runs_the_real_process_signature(self):
        module = _module(queue_workers=1)
        module._queue = asyncio.Queue()
        calls = []

        async def download(url, base, status, audio):
            calls.append((url, status, audio))
            return None

        module._download = download
        module._try_direct = unittest.mock.AsyncMock(return_value=None)
        status = types.SimpleNamespace(
            edit=unittest.mock.AsyncMock(), delete=unittest.mock.AsyncMock()
        )
        message = types.SimpleNamespace(chat_id=-1, id=1, client=None)
        module._start_queue_workers()
        with tempfile.TemporaryDirectory() as directory:
            original_cwd = os.getcwd()
            os.chdir(directory)
            try:
                with unittest.mock.patch.object(vdlt.asyncio, "sleep", unittest.mock.AsyncMock()):
                    module._enqueue("https://soundcloud.com/a/b", message, status)
                    await module._queue.join()
            finally:
                os.chdir(original_cwd)
        for task in module._worker_tasks:
            task.cancel()

        # Music links are always audio; the status message reaches _download.
        self.assertEqual(calls, [("https://soundcloud.com/a/b", status, True)])
        self.assertEqual(module._stats["err"], 1)

    async def test_queue_limit_and_cancel(self):
        module = _module(queue_max=2)
        module._queue = asyncio.Queue()
        statuses = [types.SimpleNamespace(edit=unittest.mock.AsyncMock()) for _ in range(3)]
        self.assertTrue(module._enqueue("u1", None, statuses[0]))
        self.assertTrue(module._enqueue("u2", None, statuses[1]))
        self.assertFalse(module._enqueue("u3", None, statuses[2]))

        dropped = await module._drain_queue("cancelled")

        self.assertEqual(dropped, 2)
        statuses[0].edit.assert_awaited_once_with("cancelled")
        self.assertTrue(module._queue.empty())

    async def test_invalid_setting_is_rejected_before_it_is_stored(self):
        module = _module()
        await module.vdlset(types.SimpleNamespace(args="queue_max 0"))
        self.assertEqual(module.config["queue_max"], 5)
        self.assertIn("допустимо", vdlt.utils.answer.await_args.args[1])

        await module.vdlset(types.SimpleNamespace(args="playlist on"))
        self.assertTrue(module.config["playlist_enabled"])
        await module.vdlset(types.SimpleNamespace(args="links 5"))
        self.assertEqual(module.config["max_links"], 5)

    def test_cooldown_is_tracked_per_sender(self):
        module = _module(cooldown=60)
        module._mark_download(1)
        self.assertGreater(module._cooldown_left(1), 0)
        self.assertEqual(module._cooldown_left(2), 0)

    def test_stats_survive_restart(self):
        module = _module()
        module._stats["ok"] = 4
        module._stats["platforms"]["YouTube"] = 3
        module._save_stats()

        restarted = _module()
        restarted.storage.data = module.storage.data
        restarted._load_stats()

        self.assertEqual(restarted._stats["ok"], 4)
        self.assertEqual(restarted._stats["platforms"]["YouTube"], 3)
        restarted._stats["platforms"]["New"] += 1  # still a defaultdict

    async def test_forced_ytdlp_update_is_rate_limited(self):
        module = _module()
        calls = []
        module._pip_install_sync = lambda packages, upgrade: calls.append(packages) or (True, "ok")

        self.assertTrue((await module._auto_update_ytdlp(force=True))[0])
        self.assertFalse((await module._auto_update_ytdlp(force=True))[0])
        self.assertTrue((await module._auto_update_ytdlp(force=True, manual=True))[0])
        self.assertEqual(len(calls), 2)
        self.assertEqual((await module._auto_update_ytdlp())[1], "recent")


class MediaCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_utils()

    def _message(self, client):
        return types.SimpleNamespace(client=client, chat_id=-1, id=10)

    async def test_repeated_link_is_resent_from_telegram_without_download(self):
        module = _module()
        stored = [types.SimpleNamespace(id=5, media="video-media")]
        client = types.SimpleNamespace(
            get_messages=unittest.mock.AsyncMock(return_value=stored),
            send_file=unittest.mock.AsyncMock(return_value=types.SimpleNamespace(id=6)),
        )
        module._cache_put("https://youtu.be/a", False, -1, stored, "cap")

        self.assertTrue(await module._send_cached(self._message(client), "https://youtu.be/a", False))
        client.send_file.assert_awaited_once()
        self.assertEqual(client.send_file.await_args.args[1], "video-media")
        # Audio mode and a different quality are separate cache entries.
        self.assertFalse(await module._send_cached(self._message(client), "https://youtu.be/a", True))

    async def test_deleted_cached_media_falls_back_and_is_forgotten(self):
        module = _module()
        module._cache_put("u", False, -1, [types.SimpleNamespace(id=5)], "cap")
        client = types.SimpleNamespace(
            get_messages=unittest.mock.AsyncMock(return_value=[None]),
            send_file=unittest.mock.AsyncMock(),
        )

        self.assertFalse(await module._send_cached(self._message(client), "u", False))
        self.assertEqual(module.get("media_cache"), {})

    def test_cache_is_disabled_with_auto_delete_and_expires(self):
        module = _module(auto_delete=30)
        module._cache_put("u", False, -1, [types.SimpleNamespace(id=5)], "cap")
        self.assertIsNone(module.get("media_cache"))

        module = _module(cache_hours=1)
        module._cache_put("u", False, -1, [types.SimpleNamespace(id=5)], "cap")
        entry = module.get("media_cache")[module._cache_key("u", False)]
        entry["ts"] -= 7200
        self.assertIsNone(module._cache_get("u", False))


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_utils()

    async def test_process_downloads_into_a_private_workdir_and_removes_it(self):
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            original_cwd = os.getcwd()
            os.chdir(directory)
            try:
                seen = {}

                async def download(url, base, status, audio):
                    seen["base"] = base
                    path = base + ".jpg"
                    pathlib.Path(path).write_bytes(b"x")
                    return [path]

                async def send_album(message, paths, caption):
                    return [types.SimpleNamespace(id=7)]

                module._download = download
                module._send_album = send_album
                module._notify = unittest.mock.AsyncMock()
                status = types.SimpleNamespace(
                    edit=unittest.mock.AsyncMock(), delete=unittest.mock.AsyncMock()
                )
                message = types.SimpleNamespace(chat_id=-1, id=1, client=None)

                await module._process("https://x.com/a/status/1", message, status)

                workdir = os.path.dirname(seen["base"])
                self.assertEqual(
                    os.path.dirname(workdir), os.path.join(directory, vdlt.WORK_DIR_NAME)
                )
                self.assertFalse(os.path.exists(workdir))
                self.assertEqual(module._stats["ok"], 1)
                self.assertEqual(module._stats["photos"], 1)
                self.assertEqual(module.get("stats")["ok"], 1)
                self.assertIn(module._cache_key("https://x.com/a/status/1", False),
                              module.get("media_cache"))
            finally:
                os.chdir(original_cwd)

    async def test_transcript_text_is_escaped(self):
        module = _module()
        module._get_transcript = unittest.mock.AsyncMock(return_value=("A & B", "1 <b>2</b>"))
        status = types.SimpleNamespace(edit=unittest.mock.AsyncMock())
        vdlt.utils.answer = unittest.mock.AsyncMock(return_value=status)
        module._normalize = lambda url: url

        await module.vdlt(types.SimpleNamespace(args="https://youtu.be/a"))

        rendered = status.edit.await_args.args[0]
        self.assertIn("A &amp; B", rendered)
        self.assertIn("1 &lt;b&gt;2&lt;/b&gt;", rendered)


class MediaInfoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_utils()

    def test_info_text_is_escaped_and_lists_qualities(self):
        module = _module()
        text = module._media_info_text("https://youtu.be/a", {
            "title": "<Live> & more", "uploader": "Me", "duration": 3725,
            "view_count": 1234567,
            "formats": [
                {"height": 360, "vcodec": "avc1", "filesize": 10 * 1024 * 1024},
                {"height": 1080, "vcodec": "avc1", "filesize_approx": 200 * 1024 * 1024},
                {"height": None, "vcodec": "none"},
            ],
        })
        self.assertIn("&lt;Live&gt; &amp; more", text)
        self.assertIn("1:02:05", text)
        self.assertIn("360p, 1080p", text)
        self.assertIn("200 МБ", text)
        self.assertIn("1 234 567", text)

    def test_source_link_is_optional_and_escaped(self):
        module = _module()
        self.assertEqual(module._source_suffix("https://x.com/a?b=1&c=2", "X/Twitter"), "")
        module.config["source_link"] = True
        suffix = module._source_suffix("https://x.com/a?b=1&c=2", "X/Twitter")
        self.assertIn('href="https://x.com/a?b=1&amp;c=2"', suffix)
        self.assertIn("X/Twitter", suffix)


if __name__ == "__main__":
    unittest.main()
