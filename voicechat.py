# meta developer: @Huang_Baike
# meta version: 1.1.0
# meta description: 🎙 Відтворення відео й музики з YouTube та інших сайтів у груповому дзвінку.
# requires: py-tgcalls yt-dlp

"""Stream YouTube (and anything yt-dlp understands) into Telegram group calls.

Hikka talks to Telegram through ``hikkatl``, a Telethon fork frozen at an old
API layer that knows nothing about current group-call requests.  py-tgcalls
therefore gets its own lightweight upstream Telethon connection that reuses
the account's authorisation key: no second login, no extra session file, and
the call is joined by the same account that runs the userbot.
"""

import asyncio
import collections
import contextlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

from .. import loader, utils


logger = logging.getLogger(__name__)

# Upstream Telethon with a layer that has the group/conference call requests
# py-tgcalls 3.x imports at module level.
MIN_TELETHON = (1, 40, 0)
PIP_PACKAGES = ("py-tgcalls", "yt-dlp", "telethon>=1.40")

WORK_DIR = os.path.abspath(".voicechat_cache")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
TIME_RE = re.compile(r"^([+-])?(?:(\d+):)?(?:(\d+):)?(\d+)$")

# Long and short side of the frame for every quality cap.  The box is turned
# to the orientation of the source, so vertical videos keep their resolution.
VIDEO_CAPS = {
    "4k": (3840, 2160),
    "1440p": (2560, 1440),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "360p": (640, 360),
}
# Opus in Telegram calls works at 48 kHz, so "high" is already lossless with
# respect to what listeners receive; "studio" only resamples twice.
AUDIO_MODES = {
    "high": (48000, 2),
    "studio": (96000, 2),
    "medium": (36000, 1),
}
MAX_FPS = 60
DEFAULT_FPS = 30
RESOLVE_TTL = 4 * 60 * 60
END_GRACE = 4.0
STALE_END = 1.5
MAX_FAILED_IN_ROW = 3
# Self-healing: how often the call is checked, how long the stream may stand
# still before it counts as frozen, and the pauses between rejoin attempts.
WATCHDOG_INTERVAL = 5.0
STALL_TIMEOUT = 20.0
LOST_GRACE = 5.0
HEALTHY_AFTER = 60.0
RECONNECT_DELAYS = (1, 3, 5, 10, 20)
LOST_STATES = {"FAILED", "TIMEOUT", "CLOSED"}


def fmt_time(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_seek(text, position, duration=None):
    """Turn ``1:30``, ``+15`` or ``-10`` into an absolute position in seconds."""
    match = TIME_RE.match((text or "").strip())
    if not match:
        return None
    sign, first, second, last = match.groups()
    parts = [int(part) for part in (first, second, last) if part is not None]
    value = 0
    for part in parts:
        value = value * 60 + part
    if sign == "+":
        value = position + value
    elif sign == "-":
        value = position - value
    value = max(0, value)
    if duration:
        value = min(value, max(0, int(duration) - 1))
    return value


def fit_frame(width, height, cap):
    """Largest even frame with the source proportions inside the quality box."""
    long_side, short_side = VIDEO_CAPS.get(cap, VIDEO_CAPS["1080p"])
    if not width or not height:
        return long_side, short_side
    box_w, box_h = (
        (long_side, short_side) if width >= height else (short_side, long_side)
    )
    scale = min(1.0, box_w / width, box_h / height)
    out_w = max(2, int(width * scale) // 2 * 2)
    out_h = max(2, int(height * scale) // 2 * 2)
    return out_w, out_h


def clamp_fps(fps):
    try:
        fps = float(fps or 0)
    except (TypeError, ValueError):
        fps = 0
    if fps <= 0:
        return DEFAULT_FPS
    return max(1, min(MAX_FPS, int(round(fps))))


def _chat_id(message):
    """Marked chat ID (``-100…`` for channels), as py-tgcalls expects it."""
    return getattr(message, "chat_id", None) or 0


def _version_tuple(version):
    parts = []
    for part in str(version).split("."):
        digits = re.match(r"\d+", part)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts[:3])


class Track:
    """One queue item; the stream URLs are resolved lazily right before play."""

    def __init__(self, source, *, title=None, audio_only=False, path=None,
                 duration=None, requester=None):
        self.source = source
        self.title = title or source
        self.audio_only = audio_only
        self.path = path
        self.duration = duration
        self.requester = requester
        self.webpage = source if URL_RE.match(source or "") else None
        self.video_url = None
        self.audio_url = None
        self.headers = None
        self.width = None
        self.height = None
        self.fps = None
        self.is_live = False
        self.resolved_at = 0.0

    @property
    def resolved(self):
        if self.path:
            return self.resolved_at > 0
        return (
            self.resolved_at > 0
            and time.monotonic() - self.resolved_at < RESOLVE_TTL
        )

    @property
    def has_video(self):
        return not self.audio_only and bool(self.video_url)


class Player:
    """Per-chat playback state."""

    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.queue = collections.deque()
        self.current = None
        self.lock = asyncio.Lock()
        self.generation = 0
        self.pending_ends = set()
        self.end_task = None
        self.loop = False
        self.volume = 100
        self.offset = 0.0
        self.started_at = 0.0
        self.paused_at = None
        self.switched_at = 0.0
        self.failed_in_row = 0
        self.reconnects = 0
        self.recover_task = None
        self.last_played = None
        self.progress_seen = False
        self.progress_at = 0.0

    @property
    def paused(self):
        return self.paused_at is not None

    @property
    def position(self):
        if not self.current or not self.started_at:
            return 0.0
        end = self.paused_at if self.paused else time.monotonic()
        return self.offset + max(0.0, end - self.started_at)

    def mark_started(self, offset=0.0):
        self.offset = float(offset)
        self.started_at = time.monotonic()
        self.paused_at = None

    def mark_paused(self):
        if self.paused_at is None:
            self.paused_at = time.monotonic()

    def mark_resumed(self):
        if self.paused_at is not None:
            self.started_at += time.monotonic() - self.paused_at
            self.paused_at = None


@loader.tds
class VoiceChatMod(loader.Module):
    """Відтворення відео й музики в групових дзвінках"""

    strings = {
        "name": "VoiceChat",
        "cfg_video_quality": (
            "Максимальна якість відео: 4k, 1440p, 1080p, 720p, 480p, 360p. "
            "Береться найкраща доступна якість джерела, але не вище цієї"
        ),
        "cfg_audio_quality": (
            "Якість звуку: high (48 кГц стерео — максимум для дзвінків), "
            "studio (96 кГц), medium"
        ),
        "cfg_auto_leave": "Виходити з дзвінка, коли черга закінчилась",
        "cfg_announce": "Писати в чат, що зараз грає",
        "cfg_playlist_limit": "Скільки відео брати з плейлиста",
        "cfg_max_file_mb": "Макс. розмір файлу з Telegram для відтворення (МБ)",
        "cfg_cookies": "Шлях до cookies.txt для yt-dlp (необов'язково)",
        "only_groups": "❌ <b>Групові дзвінки є лише в групах і каналах.</b>",
        "no_source": (
            "❌ <b>Дайте посилання, пошуковий запит або відповідайте "
            "на аудіо / відео.</b>"
        ),
        "loading": "⏳ <b>Готую відтворення…</b>",
        "resolving": "🔎 <b>Отримую потік…</b>",
        "deps_missing": (
            "❌ <b>Не встановлено залежності:</b> <code>{}</code>\n"
            "Виконайте <code>{}vcsetup</code>."
        ),
        "deps_old_telethon": (
            "❌ <b>Потрібен Telethon {need}+ (зараз {have}).</b>\n"
            "Виконайте <code>{p}vcsetup</code>."
        ),
        "deps_ffmpeg": "❌ <b>Не знайдено ffmpeg / ffprobe у системі.</b>",
        "deps_unsafe": (
            "❌ <b>Ця збірка Hikka сама працює на пакеті telethon, тому "
            "автоматично оновлювати його небезпечно.</b> Встановіть "
            "Telethon {}+ вручну в окреме середовище або оновіть Hikka."
        ),
        "setup_running": "⏳ <b>Встановлюю залежності…</b>",
        "setup_done": (
            "✅ <b>Залежності встановлено.</b> Якщо оновився Telethon, "
            "перезапустіть Hikka: <code>{}restart</code>."
        ),
        "setup_failed": "❌ <b>pip завершився з помилкою:</b>\n<code>{}</code>",
        "unauthorized": (
            "❌ <b>Не вдалося підключити дзвінки:</b> ключ сесії не прийнято."
        ),
        "connect_failed": "❌ <b>Не вдалося підключити дзвінки:</b> <code>{}</code>",
        "playing": (
            "{icon} <b>Зараз грає:</b> {title}\n"
            "⏱ {duration} · {quality}"
        ),
        "queued": "➕ <b>Додано в чергу ({pos}):</b> {title}",
        "queued_many": "➕ <b>Додано в чергу:</b> {count} шт. з плейлиста",
        "play_failed": "❌ <b>Не вдалося відтворити</b> {title}: <code>{error}</code>",
        "not_playing": "❌ <b>Зараз нічого не грає.</b>",
        "paused": "⏸ <b>Пауза.</b> Продовжити: <code>{}vcresume</code>",
        "already_paused": "⏸ <b>Вже на паузі.</b>",
        "resumed": "▶️ <b>Відтворення продовжено.</b>",
        "not_paused": "▶️ <b>Відтворення не на паузі.</b>",
        "skipped": "⏭ <b>Пропущено.</b>",
        "queue_end": "⏹ <b>Черга закінчилась.</b>",
        "stopped": "⏹ <b>Відтворення зупинено, бот вийшов із дзвінка.</b>",
        "reconnected": "🔄 <b>Зв'язок із дзвінком відновлено:</b> {title} з {pos}",
        "reconnect_failed": (
            "❌ <b>Зв'язок із дзвінком втрачено, перепідключитись не вдалося.</b>"
        ),
        "cleared": "🧹 <b>Чергу очищено ({}).</b>",
        "seek_usage": (
            "❌ <b>Вкажіть час:</b> <code>{p}vcseek 1:30</code>, "
            "<code>{p}vcseek +15</code> або <code>{p}vcseek -10</code>"
        ),
        "seek_live": "❌ <b>Трансляцію наживо неможливо перемотати.</b>",
        "seeked": "⏩ <b>Перемотано на {}.</b>",
        "volume_usage": "❌ <b>Гучність — число від 1 до 200.</b>",
        "volume": "🔊 <b>Гучність: {}%.</b>",
        "loop_on": "🔁 <b>Повтор поточного треку увімкнено.</b>",
        "loop_off": "➡️ <b>Повтор вимкнено.</b>",
        "too_big": "❌ <b>Файл більший за {} МБ.</b>",
        "downloading": "📥 <b>Завантажую файл із Telegram…</b>",
        "queue_empty": "📭 <b>Черга порожня.</b>",
        "queue_head": "🎶 <b>Черга ({}):</b>",
        "queue_more": "… і ще {}",
        "now": (
            "{icon} <b>{title}</b>\n"
            "{bar} {pos} / {duration}\n"
            "Стан: {state} · Гучність: {volume}% · Повтор: {loop}\n"
            "Якість: {quality}\n"
            "У черзі: {queued}"
        ),
        "state_playing": "грає",
        "state_paused": "пауза",
        "live": "наживо",
        "unknown": "—",
        "on": "так",
        "off": "ні",
        "audio_only": "лише звук",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "video_quality",
                "4k",
                lambda: self.strings("cfg_video_quality"),
                validator=loader.validators.Choice(list(VIDEO_CAPS)),
            ),
            loader.ConfigValue(
                "audio_quality",
                "high",
                lambda: self.strings("cfg_audio_quality"),
                validator=loader.validators.Choice(list(AUDIO_MODES)),
            ),
            loader.ConfigValue(
                "auto_leave",
                True,
                lambda: self.strings("cfg_auto_leave"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "announce",
                True,
                lambda: self.strings("cfg_announce"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "playlist_limit",
                25,
                lambda: self.strings("cfg_playlist_limit"),
                validator=loader.validators.Integer(minimum=1, maximum=200),
            ),
            loader.ConfigValue(
                "max_file_mb",
                2000,
                lambda: self.strings("cfg_max_file_mb"),
                validator=loader.validators.Integer(minimum=1, maximum=4000),
            ),
            loader.ConfigValue(
                "cookies_file",
                "",
                lambda: self.strings("cfg_cookies"),
                validator=loader.validators.String(),
            ),
        )
        self._client = None
        self._calls = None
        self._tg = None
        self._connect_lock = asyncio.Lock()
        self._players = {}
        self._watchdog = None

    async def client_ready(self, client, db):
        self._client = client
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    async def on_unload(self):
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        for player in list(self._players.values()):
            self._cancel_end_task(player)
            self._cancel_recover(player)
            if self._calls is not None and player.current is not None:
                with contextlib.suppress(Exception):
                    await self._calls.leave_call(player.chat_id)
            self._drop_files(player, everything=True)
        self._players.clear()
        if self._tg is not None:
            with contextlib.suppress(Exception):
                await self._tg.disconnect()
        self._tg = None
        self._calls = None
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    # ------------------------------------------------------------ connection

    def _check_dependencies(self):
        """Return an error string for the user, or ``None`` when ready."""
        missing = []
        for module, package in (
            ("pytgcalls", "py-tgcalls"),
            ("yt_dlp", "yt-dlp"),
            ("telethon", "telethon"),
        ):
            try:
                __import__(module)
            except ImportError:
                missing.append(package)
        if missing:
            return self.strings("deps_missing").format(
                " ".join(missing), self.get_prefix()
            )
        import telethon

        if _version_tuple(telethon.__version__) < MIN_TELETHON:
            return self.strings("deps_old_telethon").format(
                need=".".join(map(str, MIN_TELETHON)),
                have=utils.escape_html(telethon.__version__),
                p=self.get_prefix(),
            )
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return self.strings("deps_ffmpeg")
        return None

    async def _ensure_calls(self):
        if self._calls is not None:
            return self._calls
        async with self._connect_lock:
            if self._calls is not None:
                return self._calls

            from pytgcalls import PyTgCalls, filters
            from pytgcalls.types import ChatUpdate
            from telethon import TelegramClient
            from telethon.crypto import AuthKey
            from telethon.sessions import StringSession

            source = self._client.session
            session = StringSession()
            session.set_dc(source.dc_id, source.server_address, source.port)
            session.auth_key = AuthKey(source.auth_key.key)

            tg = TelegramClient(
                session,
                self._client.api_id,
                self._client.api_hash,
                receive_updates=True,
                catch_up=False,
            )
            await tg.connect()
            if not await tg.is_user_authorized():
                await tg.disconnect()
                raise PermissionError("unauthorized")

            calls = PyTgCalls(tg)
            calls.on_update(filters.stream_end())(self._on_stream_end)
            calls.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))(
                self._on_left_call
            )
            self._hook_connection(calls)
            try:
                await calls.start()
            except Exception:
                await tg.disconnect()
                raise
            self._tg = tg
            self._calls = calls
            if self._watchdog is None or self._watchdog.done():
                self._watchdog = asyncio.ensure_future(self._watchdog_loop())
            return calls

    def _hook_connection(self, calls):
        """Hear about network drops that py-tgcalls otherwise swallows.

        When an established call connection fails, py-tgcalls only clears
        its caches: the dead connection stays registered, nothing is
        reported, and further ``play`` calls feed media into the void.
        """
        original = getattr(calls, "_handle_connection_changed", None)
        if original is None:
            return

        async def handler(chat_id, net_state):
            try:
                await original(chat_id, net_state)
            finally:
                self._on_connection_changed(chat_id, net_state)

        calls._handle_connection_changed = handler

    async def _prime_chat(self, chat_id):
        """Teach the extra connection the channel's access hash."""
        if self._tg is None or not str(chat_id).startswith("-100"):
            return
        from telethon.tl.functions.channels import GetChannelsRequest
        from telethon.tl.types import InputChannel

        try:
            await self._tg.get_input_entity(chat_id)
            return
        except Exception:
            pass
        peer = await self._client.get_input_entity(chat_id)
        await self._tg(
            GetChannelsRequest([InputChannel(peer.channel_id, peer.access_hash)])
        )

    async def _prepare(self, message):
        """Shared checks of the play commands; returns ``None`` on failure."""
        chat_id = _chat_id(message)
        if not str(chat_id).startswith("-"):
            await utils.answer(message, self.strings("only_groups"))
            return None
        error = self._check_dependencies()
        if error:
            await utils.answer(message, error)
            return None
        try:
            await self._ensure_calls()
            await self._prime_chat(chat_id)
        except PermissionError:
            await utils.answer(message, self.strings("unauthorized"))
            return None
        except Exception as error:
            logger.exception("VoiceChat could not connect")
            await utils.answer(
                message,
                self.strings("connect_failed").format(
                    utils.escape_html(str(error) or type(error).__name__)
                ),
            )
            return None
        return chat_id

    def _player(self, chat_id, create=True):
        player = self._players.get(chat_id)
        if player is None and create:
            player = self._players[chat_id] = Player(chat_id)
        return player

    # ------------------------------------------------------------- resolving

    def _ydl_options(self, audio_only):
        cap = self.config["video_quality"]
        short_side = VIDEO_CAPS.get(cap, VIDEO_CAPS["1080p"])[1]
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "default_search": "ytsearch",
        }
        if audio_only:
            options["format"] = "bestaudio/best"
        else:
            # Best video stream up to the cap plus best audio; SDR and codecs
            # up to VP9 are preferred because HDR/AV1 at 4K costs a lot of CPU
            # to decode and colours wash out when HDR is squeezed into yuv420p.
            options["format"] = "bv*+ba/b"
            options["format_sort"] = [
                f"res:{short_side}",
                "fps",
                "hdr:sdr",
                "vcodec:vp9",
            ]
        cookies = str(self.config["cookies_file"] or "").strip()
        if cookies and os.path.isfile(cookies):
            options["cookiefile"] = cookies
        return options

    def _extract_sync(self, track):
        import yt_dlp

        with yt_dlp.YoutubeDL(self._ydl_options(track.audio_only)) as ydl:
            info = ydl.extract_info(track.source, download=False)
        if info.get("entries"):
            entries = [entry for entry in info["entries"] if entry]
            if not entries:
                raise ValueError("nothing found")
            info = entries[0]
        return info

    def _apply_info(self, track, info):
        formats = info.get("requested_formats") or [info]
        video = next(
            (f for f in formats if (f.get("vcodec") or "none") != "none"), None
        )
        audio = next(
            (
                f
                for f in formats
                if (f.get("acodec") or "none") != "none" and f is not video
            ),
            None,
        )
        if audio is None and video is not None and video.get("acodec") not in (
            None,
            "none",
        ):
            audio = video
        if audio is None and video is None:
            audio = formats[0]
        track.video_url = None if track.audio_only or not video else video["url"]
        track.audio_url = (audio or video)["url"]
        track.headers = (
            (video or audio).get("http_headers") or info.get("http_headers") or None
        )
        track.width = (video or {}).get("width") or info.get("width")
        track.height = (video or {}).get("height") or info.get("height")
        track.fps = (video or {}).get("fps") or info.get("fps")
        track.duration = info.get("duration") or track.duration
        track.is_live = bool(info.get("is_live"))
        track.title = info.get("title") or track.title
        track.webpage = info.get("webpage_url") or track.webpage
        track.resolved_at = time.monotonic()

    async def _probe_file(self, track):
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            track.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), 30)
        data = json.loads(stdout.decode() or "{}")
        video = next(
            (
                s
                for s in data.get("streams", [])
                if s.get("codec_type") == "video" and s.get("width")
            ),
            None,
        )
        if video and not track.audio_only:
            track.video_url = track.path
            track.width = video.get("width")
            track.height = video.get("height")
            rate = str(video.get("avg_frame_rate") or "0/1")
            num, _, den = rate.partition("/")
            with contextlib.suppress(ValueError, ZeroDivisionError):
                track.fps = float(num) / float(den or 1)
        track.audio_url = track.path
        with contextlib.suppress(TypeError, ValueError):
            track.duration = track.duration or float(
                data.get("format", {}).get("duration")
            )
        track.resolved_at = time.monotonic()

    async def _resolve(self, track):
        if track.resolved:
            return
        if track.path:
            await self._probe_file(track)
            return
        info = await asyncio.to_thread(self._extract_sync, track)
        self._apply_info(track, info)

    def _playlist_sync(self, url, audio_only, requester):
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": int(self.config["playlist_limit"]),
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        tracks = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            link = entry.get("webpage_url") or entry.get("url")
            if not link:
                continue
            if not URL_RE.match(link) and entry.get("ie_key") == "Youtube":
                link = f"https://www.youtube.com/watch?v={link}"
            tracks.append(
                Track(
                    link,
                    title=entry.get("title"),
                    audio_only=audio_only,
                    duration=entry.get("duration"),
                    requester=requester,
                )
            )
        return tracks

    @staticmethod
    def _is_playlist(url):
        return bool(
            re.search(r"[?&]list=", url)
            and not re.search(r"[?&]v=|youtu\.be/", url)
        ) or "/playlist" in url

    async def _tracks_from_query(self, query, audio_only, requester):
        if URL_RE.match(query) and self._is_playlist(query):
            tracks = await asyncio.to_thread(
                self._playlist_sync, query, audio_only, requester
            )
            if tracks:
                return tracks
        return [Track(query, audio_only=audio_only, requester=requester)]

    @staticmethod
    def _is_media(reply):
        file = getattr(reply, "file", None)
        if file is None:
            return False
        return bool(
            getattr(reply, "audio", None)
            or getattr(reply, "voice", None)
            or getattr(reply, "video", None)
            or getattr(reply, "video_note", None)
            or (getattr(file, "mime_type", "") or "").startswith(
                ("audio/", "video/")
            )
        )

    async def _tracks_from_message(self, message, status, args, audio_only):
        """Tracks from the arguments, the replied media or a replied link.

        Returns ``None`` when the user was already told what went wrong.
        """
        requester = getattr(message, "sender_id", None)
        query = args.strip()
        if query:
            return await self._tracks_from_query(query, audio_only, requester)

        reply = await message.get_reply_message()
        if reply is None:
            return []
        if not self._is_media(reply):
            link = URL_RE.search(getattr(reply, "raw_text", None) or "")
            if not link:
                return []
            return await self._tracks_from_query(
                link.group(0), audio_only, requester
            )

        file = reply.file
        limit = int(self.config["max_file_mb"])
        if (file.size or 0) > limit * 1024 * 1024:
            await utils.answer(status, self.strings("too_big").format(limit))
            return None
        await utils.answer(status, self.strings("downloading"))
        folder = os.path.join(WORK_DIR, str(abs(_chat_id(message))))
        os.makedirs(folder, exist_ok=True)
        path = await self._client.download_media(
            reply, file=os.path.join(folder, f"{reply.id}{file.ext or ''}")
        )
        title = getattr(file, "title", None) or getattr(file, "name", None)
        performer = getattr(file, "performer", None)
        if performer and title:
            title = f"{performer} — {title}"
        return [
            Track(
                path,
                title=title or f"{file.mime_type or 'media'} #{reply.id}",
                audio_only=audio_only,
                path=path,
                duration=getattr(file, "duration", None),
                requester=requester,
            )
        ]

    # --------------------------------------------------------------- playing

    def _stream(self, track, offset=0):
        from pytgcalls.types import MediaStream
        from pytgcalls.types.raw import AudioParameters, VideoParameters

        rate, channels = AUDIO_MODES.get(
            self.config["audio_quality"], AUDIO_MODES["high"]
        )
        width, height = fit_frame(
            track.width, track.height, self.config["video_quality"]
        )
        ffmpeg_parameters = None
        if offset and not track.is_live:
            ffmpeg_parameters = f"-ss {int(offset)}"
        has_video = track.has_video
        return MediaStream(
            track.video_url if has_video else track.audio_url,
            audio_parameters=AudioParameters(rate, channels),
            video_parameters=VideoParameters(
                width, height, clamp_fps(track.fps), True
            ),
            audio_path=track.audio_url,
            audio_flags=MediaStream.Flags.REQUIRED,
            video_flags=(
                MediaStream.Flags.AUTO_DETECT
                if has_video
                else MediaStream.Flags.IGNORE
            ),
            headers=None if track.path else track.headers,
            ffmpeg_parameters=ffmpeg_parameters,
        )

    def _quality_label(self, track):
        if not track.has_video:
            rate, channels = AUDIO_MODES.get(
                self.config["audio_quality"], AUDIO_MODES["high"]
            )
            return f"{self.strings('audio_only')}, {rate // 1000} kHz"
        width, height = fit_frame(
            track.width, track.height, self.config["video_quality"]
        )
        return f"{width}×{height}@{clamp_fps(track.fps)}"

    def _title_html(self, track):
        title = utils.escape_html(str(track.title)[:200])
        if track.webpage:
            return f'<a href="{utils.escape_html(track.webpage)}">{title}</a>'
        return f"<b>{title}</b>"

    def _duration_label(self, track):
        if track.is_live:
            return self.strings("live")
        if track.duration:
            return fmt_time(track.duration)
        return self.strings("unknown")

    async def _start(self, player, track, offset=0):
        """Resolve ``track`` and put it on air.  Caller holds ``player.lock``."""
        await self._resolve(track)
        stream = self._stream(track, offset)
        from pytgcalls.types import GroupCallConfig

        self._cancel_end_task(player)
        player.generation += 1
        previous, previous_ends = player.current, player.pending_ends
        player.current = track
        player.pending_ends = {"audio"} | ({"video"} if track.has_video else set())
        player.switched_at = time.monotonic()
        try:
            await self._calls.play(
                player.chat_id, stream, config=GroupCallConfig(auto_start=True)
            )
        except Exception:
            player.current, player.pending_ends = previous, previous_ends
            raise
        player.switched_at = time.monotonic()
        player.mark_started(offset)
        player.last_played = None
        player.progress_seen = False
        player.progress_at = player.switched_at
        if previous is not None and previous is not track:
            self._drop_track_file(previous, player)
        player.failed_in_row = 0
        if player.volume != 100:
            with contextlib.suppress(Exception):
                await self._calls.change_volume_call(player.chat_id, player.volume)

    async def _announce(self, chat_id, track):
        if not self.config["announce"]:
            return
        with contextlib.suppress(Exception):
            await self._client.send_message(
                chat_id,
                self._playing_text(track),
                link_preview=False,
            )

    def _playing_text(self, track):
        return self.strings("playing").format(
            icon="🎬" if track.has_video else "🎵",
            title=self._title_html(track),
            duration=self._duration_label(track),
            quality=self._quality_label(track),
        )

    async def _advance(self, player, announce=True, force_leave=False):
        """Play the next queue item, or leave when the queue is empty.

        ``force_leave`` is for skips: the replaced stream would otherwise keep
        playing in the call although nothing is "current" any more.
        """
        while True:
            if player.loop and player.current is not None:
                track = player.current
            elif player.queue:
                track = player.queue.popleft()
            else:
                finished = player.current
                player.current = None
                self._drop_track_file(finished, player)
                if self.config["auto_leave"] or force_leave:
                    await self._leave(player)
                return None
            try:
                await self._start(player, track)
            except Exception as error:
                logger.warning("VoiceChat failed to play %s: %s", track.source, error)
                player.failed_in_row += 1
                if player.loop:
                    player.loop = False
                self._drop_track_file(track, player)
                if announce:
                    with contextlib.suppress(Exception):
                        await self._client.send_message(
                            player.chat_id,
                            self._error_text(track, error),
                            link_preview=False,
                        )
                if player.failed_in_row >= MAX_FAILED_IN_ROW:
                    player.queue.clear()
                continue
            if announce:
                await self._announce(player.chat_id, track)
            return track

    async def _leave(self, player):
        self._cancel_end_task(player)
        self._cancel_recover(player)
        player.generation += 1
        if self._calls is not None:
            with contextlib.suppress(Exception):
                await self._calls.leave_call(player.chat_id)
        self._drop_files(player, everything=True)
        self._players.pop(player.chat_id, None)

    def _error_text(self, track, error):
        return self.strings("play_failed").format(
            title=self._title_html(track),
            error=utils.escape_html(str(error)[:300] or type(error).__name__),
        )

    # --------------------------------------------------------- stream events

    def _cancel_end_task(self, player):
        task = player.end_task
        player.end_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _on_stream_end(self, _, update):
        player = self._players.get(update.chat_id)
        if player is None or player.current is None:
            return
        if time.monotonic() - player.switched_at < STALE_END:
            # A source switch (seek, .vcplay over a playing track) may still
            # report the end of the stream it replaced.
            return
        kind = (
            "video"
            if getattr(update.stream_type, "name", "") == "VIDEO"
            else "audio"
        )
        player.pending_ends.discard(kind)
        generation = player.generation
        if not player.pending_ends:
            await self._finish(player, generation)
        elif player.end_task is None:
            # The other half normally ends within milliseconds; do not hang
            # forever if a muxed source closes only one of them.
            player.end_task = asyncio.ensure_future(
                self._finish_later(player, generation)
            )

    async def _finish_later(self, player, generation):
        await asyncio.sleep(END_GRACE)
        await self._finish(player, generation)

    async def _finish(self, player, generation):
        async with player.lock:
            if player.generation != generation or player.current is None:
                return
            self._cancel_end_task(player)
            await self._advance(player)

    async def _on_left_call(self, _, update):
        player = self._players.pop(update.chat_id, None)
        if player is not None:
            self._cancel_end_task(player)
            self._cancel_recover(player)
            player.generation += 1
            self._drop_files(player, everything=True)

    # ------------------------------------------------------------ self-heal

    def _on_connection_changed(self, chat_id, net_state):
        state = getattr(getattr(net_state, "state", None), "name", "")
        if state not in LOST_STATES:
            return
        player = self._players.get(chat_id)
        if player is None or player.current is None:
            return
        if time.monotonic() - player.switched_at < LOST_GRACE:
            # Our own join / rejoin is still settling.
            return
        self._schedule_recover(player, f"connection {state.lower()}")

    async def _watchdog_loop(self):
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            try:
                await self._ensure_tg_connected()
            except Exception:
                logger.debug("VoiceChat could not reconnect Telethon", exc_info=True)
            for player in list(self._players.values()):
                try:
                    await self._check_player(player)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("VoiceChat watchdog failed", exc_info=True)

    async def _ensure_tg_connected(self):
        tg = self._tg
        if tg is not None and not tg.is_connected():
            logger.warning("VoiceChat: Telegram connection lost, reconnecting")
            await tg.connect()

    async def _check_player(self, player):
        """Schedule a rejoin when the call vanished or the stream froze."""
        if self._calls is None or player.current is None:
            return
        if player.lock.locked() or self._recovering(player):
            return
        now = time.monotonic()
        if now - player.switched_at < STALL_TIMEOUT:
            return
        active = await self._calls.calls
        if player.chat_id not in active:
            self._schedule_recover(player, "call dropped")
            return
        if player.paused:
            player.progress_at = now
            return
        try:
            played = await self._calls.time(player.chat_id)
        except Exception:
            return
        if played != player.last_played:
            if player.last_played is not None:
                player.progress_seen = True
            player.last_played = played
            player.progress_at = now
            if player.progress_seen and now - player.switched_at >= HEALTHY_AFTER:
                player.reconnects = 0
        elif player.progress_seen and now - player.progress_at >= STALL_TIMEOUT:
            self._schedule_recover(player, "stream stalled")

    def _recovering(self, player):
        task = player.recover_task
        return task is not None and not task.done()

    def _schedule_recover(self, player, reason):
        if self._recovering(player) and player.recover_task is not asyncio.current_task():
            return
        player.recover_task = asyncio.ensure_future(self._recover(player, reason))

    def _cancel_recover(self, player):
        task = player.recover_task
        player.recover_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _recover(self, player, reason):
        """Leave the broken call and rejoin it at the same position."""
        delay = RECONNECT_DELAYS[min(player.reconnects, len(RECONNECT_DELAYS) - 1)]
        await asyncio.sleep(delay)
        async with player.lock:
            if self._players.get(player.chat_id) is not player:
                return
            track = player.current
            if track is None or self._calls is None:
                return
            if player.reconnects >= len(RECONNECT_DELAYS):
                logger.warning(
                    "VoiceChat gave up reconnecting to %s", player.chat_id
                )
                await self._leave(player)
                with contextlib.suppress(Exception):
                    await self._client.send_message(
                        player.chat_id, self.strings("reconnect_failed")
                    )
                return
            player.reconnects += 1
            logger.warning(
                "VoiceChat: %s in %s, rejoining (attempt %d)",
                reason,
                player.chat_id,
                player.reconnects,
            )
            position = 0 if track.is_live else int(player.position)
            paused = player.paused
            self._cancel_end_task(player)
            with contextlib.suppress(Exception):
                await self._calls.leave_call(player.chat_id)
            with contextlib.suppress(Exception):
                await self._ensure_tg_connected()
            if not track.path:
                # Stream URLs may have expired while the call was down.
                track.resolved_at = 0.0
            try:
                await self._prime_chat(player.chat_id)
                await self._start(player, track, offset=position)
            except Exception as error:
                logger.warning("VoiceChat rejoin failed: %s", error)
                self._schedule_recover(player, reason)
                return
            if paused:
                with contextlib.suppress(Exception):
                    await self._calls.pause(player.chat_id)
                    player.mark_paused()
        if self.config["announce"]:
            with contextlib.suppress(Exception):
                await self._client.send_message(
                    player.chat_id,
                    self.strings("reconnected").format(
                        title=self._title_html(track),
                        pos=fmt_time(position),
                    ),
                    link_preview=False,
                )

    # ----------------------------------------------------------------- files

    def _drop_track_file(self, track, player):
        if track is None or not track.path:
            return
        if track is player.current or any(t is track for t in player.queue):
            return
        with contextlib.suppress(OSError):
            os.remove(track.path)

    def _drop_files(self, player, everything=False):
        tracks = list(player.queue)
        if player.current is not None:
            tracks.append(player.current)
        if everything:
            player.queue.clear()
            player.current = None
        for track in tracks:
            if track.path:
                with contextlib.suppress(OSError):
                    os.remove(track.path)

    # -------------------------------------------------------------- commands

    async def _play_command(self, message, now, audio_only=False):
        args = (utils.get_args_raw(message) or "").strip()
        flags = {"-a", "--audio"}
        words = args.split()
        if words and words[0].lower() in flags:
            audio_only = True
            args = args[len(words[0]):].strip()
        if not args and not message.is_reply:
            return await utils.answer(message, self.strings("no_source"))

        chat_id = await self._prepare(message)
        if chat_id is None:
            return
        status = await utils.answer(message, self.strings("loading"))
        try:
            tracks = await self._tracks_from_message(
                message, status, args, audio_only
            )
        except Exception as error:
            return await utils.answer(
                status,
                self.strings("play_failed").format(
                    title=utils.escape_html(args or "media"),
                    error=utils.escape_html(str(error)[:300]),
                ),
            )
        if tracks is None:
            return
        if not tracks:
            return await utils.answer(status, self.strings("no_source"))

        player = self._player(chat_id)
        async with player.lock:
            if not now and player.current is not None:
                player.queue.extend(tracks)
                if len(tracks) == 1:
                    return await utils.answer(
                        status,
                        self.strings("queued").format(
                            pos=len(player.queue),
                            title=self._title_html(tracks[0]),
                        ),
                    )
                return await utils.answer(
                    status, self.strings("queued_many").format(count=len(tracks))
                )

            first, rest = tracks[0], tracks[1:]
            if now:
                player.queue.extendleft(reversed(rest))
            else:
                player.queue.extend(rest)
            status = await utils.answer(status, self.strings("resolving"))
            player.loop = False
            try:
                await self._start(player, first)
            except Exception as error:
                logger.exception("VoiceChat could not start playback")
                self._drop_track_file(first, player)
                if player.current is None and not player.queue:
                    self._players.pop(chat_id, None)
                return await utils.answer(status, self._error_text(first, error))
            text = self._playing_text(first)
            if rest:
                text += "\n" + self.strings("queued_many").format(count=len(rest))
            await utils.answer(status, text, link_preview=False)

    @loader.command(
        ru_doc="<посилання | запит | відповідь на медіа> [-a] — грати зараз"
    )
    async def vcplay(self, message):
        """<посилання | запит | відповідь на медіа> [-a] — увімкнути зараз (замінює поточне). -a — лише звук"""
        await self._play_command(message, now=True)

    @loader.command(ru_doc="<посилання | запит | відповідь на медіа> [-a] — у чергу")
    async def vcadd(self, message):
        """<посилання | запит | відповідь на медіа> [-a] — додати в чергу (або грати, якщо тиша)"""
        await self._play_command(message, now=False)

    @loader.command(ru_doc="<посилання | запит> — грати лише звук")
    async def vcaudio(self, message):
        """<посилання | запит | відповідь на медіа> — увімкнути лише звук"""
        await self._play_command(message, now=True, audio_only=True)

    async def _active(self, message):
        player = self._players.get(_chat_id(message))
        if player is None or player.current is None or self._calls is None:
            await utils.answer(message, self.strings("not_playing"))
            return None
        return player

    @loader.command(ru_doc="Пауза")
    async def vcpause(self, message):
        """Поставити відтворення на паузу"""
        player = await self._active(message)
        if player is None:
            return
        if player.paused:
            return await utils.answer(message, self.strings("already_paused"))
        await self._calls.pause(player.chat_id)
        player.mark_paused()
        await utils.answer(message, self.strings("paused").format(self.get_prefix()))

    @loader.command(ru_doc="Продовжити")
    async def vcresume(self, message):
        """Продовжити відтворення після паузи"""
        player = await self._active(message)
        if player is None:
            return
        if not player.paused:
            return await utils.answer(message, self.strings("not_paused"))
        await self._calls.resume(player.chat_id)
        player.mark_resumed()
        await utils.answer(message, self.strings("resumed"))

    @loader.command(ru_doc="Наступний трек")
    async def vcskip(self, message):
        """Перейти до наступного в черзі"""
        player = await self._active(message)
        if player is None:
            return
        async with player.lock:
            player.loop = False
            track = await self._advance(player, announce=False, force_leave=True)
        if track is None:
            return await utils.answer(message, self.strings("queue_end"))
        await utils.answer(message, self._playing_text(track), link_preview=False)

    @loader.command(ru_doc="Зупинити й вийти з дзвінка")
    async def vcstop(self, message):
        """Зупинити відтворення, очистити чергу й вийти з дзвінка"""
        chat_id = _chat_id(message)
        player = self._players.get(chat_id)
        if player is None or self._calls is None:
            if self._calls is not None:
                with contextlib.suppress(Exception):
                    await self._calls.leave_call(chat_id)
            return await utils.answer(message, self.strings("stopped"))
        async with player.lock:
            await self._leave(player)
        await utils.answer(message, self.strings("stopped"))

    @loader.command(ru_doc="<1:30 | +15 | -10> — перемотати")
    async def vcseek(self, message):
        """<1:30 | +15 | -10> — перемотати поточний трек"""
        player = await self._active(message)
        if player is None:
            return
        track = player.current
        if track.is_live:
            return await utils.answer(message, self.strings("seek_live"))
        target = parse_seek(
            utils.get_args_raw(message), player.position, track.duration
        )
        if target is None:
            return await utils.answer(
                message, self.strings("seek_usage").format(p=self.get_prefix())
            )
        async with player.lock:
            try:
                await self._start(player, track, offset=target)
            except Exception as error:
                return await utils.answer(message, self._error_text(track, error))
        await utils.answer(message, self.strings("seeked").format(fmt_time(target)))

    @loader.command(ru_doc="<1-200> — гучність")
    async def vcvol(self, message):
        """<1-200> — гучність бота в дзвінку"""
        player = await self._active(message)
        if player is None:
            return
        value = (utils.get_args_raw(message) or "").strip().rstrip("%")
        if not value.isdigit() or not 1 <= int(value) <= 200:
            return await utils.answer(message, self.strings("volume_usage"))
        await self._calls.change_volume_call(player.chat_id, int(value))
        player.volume = int(value)
        await utils.answer(message, self.strings("volume").format(value))

    @loader.command(ru_doc="Повтор поточного треку")
    async def vcloop(self, message):
        """Увімкнути / вимкнути повтор поточного треку"""
        player = await self._active(message)
        if player is None:
            return
        player.loop = not player.loop
        await utils.answer(
            message, self.strings("loop_on" if player.loop else "loop_off")
        )

    @loader.command(ru_doc="Черга")
    async def vcqueue(self, message):
        """Показати чергу"""
        player = self._players.get(_chat_id(message))
        if player is None or (player.current is None and not player.queue):
            return await utils.answer(message, self.strings("queue_empty"))
        lines = []
        if player.current is not None:
            lines.append(f"▶️ {self._title_html(player.current)}")
        lines.append(self.strings("queue_head").format(len(player.queue)))
        for index, track in enumerate(list(player.queue)[:15], 1):
            duration = f" · {fmt_time(track.duration)}" if track.duration else ""
            lines.append(f"{index}. {self._title_html(track)}{duration}")
        if len(player.queue) > 15:
            lines.append(self.strings("queue_more").format(len(player.queue) - 15))
        await utils.answer(message, "\n".join(lines), link_preview=False)

    @loader.command(ru_doc="Очистити чергу")
    async def vcclear(self, message):
        """Очистити чергу (поточний трек грає далі)"""
        player = self._players.get(_chat_id(message))
        count = 0
        if player is not None:
            async with player.lock:
                count = len(player.queue)
                tracks = list(player.queue)
                player.queue.clear()
                for track in tracks:
                    self._drop_track_file(track, player)
        await utils.answer(message, self.strings("cleared").format(count))

    @loader.command(ru_doc="Що зараз грає")
    async def vcnow(self, message):
        """Що зараз грає: позиція, якість, гучність"""
        player = await self._active(message)
        if player is None:
            return
        track = player.current
        position = player.position
        if track.duration and not track.is_live:
            filled = int(min(1.0, position / float(track.duration)) * 12)
            bar = "▰" * filled + "▱" * (12 - filled)
        else:
            bar = "🔴"
        await utils.answer(
            message,
            self.strings("now").format(
                icon="🎬" if track.has_video else "🎵",
                title=self._title_html(track),
                bar=bar,
                pos=fmt_time(position),
                duration=self._duration_label(track),
                state=self.strings(
                    "state_paused" if player.paused else "state_playing"
                ),
                volume=player.volume,
                loop=self.strings("on" if player.loop else "off"),
                quality=self._quality_label(track),
                queued=len(player.queue),
            ),
            link_preview=False,
        )

    @loader.command(ru_doc="Встановити / оновити залежності")
    async def vcsetup(self, message):
        """Встановити або оновити py-tgcalls, yt-dlp і Telethon"""
        if importlib.util.find_spec("hikkatl") is None:
            # Old Hikka builds ship their fork under the ``telethon`` name;
            # replacing it with upstream Telethon would break the userbot.
            return await utils.answer(
                message,
                self.strings("deps_unsafe").format(
                    ".".join(map(str, MIN_TELETHON))
                ),
            )
        message = await utils.answer(message, self.strings("setup_running"))
        command = [sys.executable, "-m", "pip", "install", "-U", *PIP_PACKAGES]
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "")[-800:]
            return await utils.answer(
                message,
                self.strings("setup_failed").format(utils.escape_html(tail)),
            )
        await utils.answer(
            message, self.strings("setup_done").format(self.get_prefix())
        )
