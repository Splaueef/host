# meta developer: @Huang_Baike
# meta version: 1.4.0
# meta description: 🎙 Відтворення відео й музики з YouTube та будь-яких сайтів у груповому дзвінку.
# requires: py-tgcalls yt-dlp

"""Stream YouTube (and anything yt-dlp understands) into Telegram group calls.

Builds running on upstream Telethon (1.40+) hand their own client straight to
py-tgcalls.  Original Hikka talks to Telegram through ``hikkatl``, a Telethon
fork frozen at an old API layer without the current group-call requests; there
py-tgcalls gets its own lightweight upstream Telethon connection that reuses
the account's authorisation key: no second login and no extra session file.
"""

import asyncio
import collections
import contextlib
import html
import importlib.util
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urljoin, urlsplit

from .. import loader, utils


logger = logging.getLogger(__name__)

# Upstream Telethon with a layer that has the group/conference call requests
# py-tgcalls 3.x imports at module level.
MIN_TELETHON = (1, 40, 0)
TELETHON_REQUIREMENT = "telethon>=1.40"

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
# py-tgcalls adds -reconnect_at_eof to every URL, which keeps ffmpeg waiting
# at the end of HLS streams, so the end event never comes.  When the known
# duration has passed (pauses excluded), the next track starts anyway.
WATCHDOG_SLACK = 6.0
STALE_END = 1.5
MAX_FAILED_IN_ROW = 3

# YouTube asks datacenter IPs to "confirm you're not a bot".  The same answer
# VideoDownloader (vdlt.py) uses works here: a cookies.txt with a YouTube
# session, a JS runtime for the player challenges and several player clients.
YT_COOKIE_FILES = (
    "/home/rkbot/URKbot/cookies.txt",
    "/data/home/rkbot/URKbot/cookies.txt",
    "/home/rkbot/URKbot/cookies-youtube-com.txt",
    "/data/home/rkbot/URKbot/cookies-youtube-com.txt",
)
YT_COOKIE_DOMAINS = ("youtube.com", "youtu.be", "googlevideo.com")
YT_HOST_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|"
    r"youtube-nocookie\.com)(?:[/:?#]|$)",
    re.IGNORECASE,
)
JS_RUNTIMES = (
    ("deno", ("/usr/local/bin/deno", "/usr/bin/deno")),
    ("node", ("/usr/bin/node", "/usr/local/bin/node")),
    ("node", ("/usr/bin/nodejs", "/usr/local/bin/nodejs")),
)
# (player clients, allow formats without a PO token); ``None`` = yt-dlp default.
YT_CLIENT_PROFILES = (
    (None, False),
    (["web_safari"], False),
    (["tv_simply", "default", "-tv"], False),
    (["android_vr"], False),
    (["mweb"], False),
    (["default", "ios", "web_embedded", "-tv"], True),
)
YT_FATAL_MARKERS = (
    "private video",
    "video unavailable",
    "has been removed",
    "members-only",
    "join this channel",
    "not available in your country",
)
YT_AUTH_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "cookies-from-browser",
    "getpot",
    "po token",
)


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


def clamp_fps(fps, cap=MAX_FPS):
    cap = max(1, min(MAX_FPS, int(cap or MAX_FPS)))
    try:
        fps = float(fps or 0)
    except (TypeError, ValueError):
        fps = 0
    if fps <= 0:
        return min(DEFAULT_FPS, cap)
    return max(1, min(cap, int(round(fps))))


def _chat_id(message):
    """Marked chat ID (``-100…`` for channels), as py-tgcalls expects it."""
    return getattr(message, "chat_id", None) or 0


# ---------------------------------------------------------------- any site
#
# yt-dlp knows ~1800 sites.  For everything else the page itself is read:
# <video>/<source> tags, og:video meta tags, media URLs inside player configs
# (JW Player, Playerjs, Plyr, Video.js…) and, failing that, the embedded
# <iframe> players, which go through yt-dlp and the same search in turn.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
PAGE_LIMIT = 3 * 1024 * 1024
SCRAPE_DEPTH = 2
SCRAPE_FRAMES = 6
MEDIA_URL_RE = re.compile(
    r"""(?:https?:)?//[^\s"'<>()\\]+?\."""
    r"""(?:m3u8|mpd|mp4|m4v|webm|mkv|mov|flv|mp3|m4a|aac|ogg|oga|opus|flac|wav)"""
    r"""(?:\?[^\s"'<>\\]*)?""",
    re.IGNORECASE,
)
SOURCE_TAG_RE = re.compile(
    r"""<(?:video|audio|source)\b[^>]*?\s(?:data-)?src\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
FRAME_TAG_RE = re.compile(
    r"""<(?:iframe|frame|embed)\b[^>]*?\s(?:data-)?src\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
META_MEDIA_NAMES = (
    "og:video",
    "og:video:url",
    "og:video:secure_url",
    "og:audio",
    "og:audio:url",
    "twitter:player:stream",
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
MEDIA_TYPES = ("video/", "audio/", "application/vnd.apple.mpegurl",
               "application/x-mpegurl", "application/dash+xml")
EXT_RANK = {
    "m3u8": 0, "mpd": 1, "mp4": 2, "webm": 2, "mkv": 2, "mov": 2, "m4v": 2,
    "flv": 3, "mp3": 4, "m4a": 4, "aac": 4, "ogg": 4, "oga": 4, "opus": 4,
    "flac": 4, "wav": 4,
}
CODEC_NAMES = {
    "avc1": "h264", "avc3": "h264", "hev1": "h265", "hvc1": "h265",
    "hevc": "h265", "vp09": "vp9", "vp08": "vp8", "av01": "av1",
    "mp4a": "aac", "ec-3": "eac3", "ac-3": "ac3",
}


def codec_name(codec):
    if not codec or codec == "none":
        return None
    base = str(codec).split(".")[0].lower()
    return CODEC_NAMES.get(base, base)


def http_args(headers):
    """ffmpeg input options for HTTP headers.

    ffmpeg keeps only the last ``-headers`` option, so User-Agent and Referer
    go through their own options and one remaining header through -headers.
    """
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items() if v}
    args = []
    if headers.get("user-agent"):
        args += ["-user_agent", headers["user-agent"]]
    if headers.get("referer"):
        args += ["-referer", headers["referer"]]
    for name in ("cookie", "origin"):
        if headers.get(name):
            args += ["-headers", f"{name.title()}: {headers[name]}"]
            break
    return args


_HLS_OPTIONS = None


def _hls_demuxer_options():
    """Options this ffmpeg build's HLS demuxer knows (they vary by version)."""
    global _HLS_OPTIONS
    if _HLS_OPTIONS is None:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-h", "demuxer=hls"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            _HLS_OPTIONS = set(re.findall(r"^\s*-(\w+)", result.stdout, re.M))
        except (OSError, subprocess.SubprocessError):
            _HLS_OPTIONS = set()
    return _HLS_OPTIONS


def hls_args(*urls):
    """Let HLS playlists use segments with odd extensions (.jpg, .txt …)."""
    if not any(".m3u8" in (url or "").lower() for url in urls):
        return []
    known = _hls_demuxer_options()
    args = []
    if "allowed_extensions" in known:
        args += ["-allowed_extensions", "ALL"]
    if "extension_picky" in known:  # ffmpeg 7.1+
        args += ["-extension_picky", "0"]
    return args


def page_headers(page_url):
    parts = urlsplit(page_url)
    return {
        "User-Agent": BROWSER_UA,
        "Referer": page_url,
        "Origin": f"{parts.scheme}://{parts.netloc}",
    }


def _media_rank(url):
    path = urlsplit(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    height = re.search(r"(2160|1440|1080|720|480|360|240)", url)
    return (EXT_RANK.get(ext, 5), -int(height.group(1)) if height else 0)


def find_media(page, base_url):
    """Media URLs in a page, the most promising first."""
    text = html.unescape(
        page.replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")
    )
    found = []
    for tag in META_TAG_RE.findall(text):
        name = re.search(r"""(?:property|name)\s*=\s*["']([^"']+)""", tag, re.I)
        content = re.search(r"""content\s*=\s*["']([^"']+)""", tag, re.I)
        if name and content and name.group(1).lower() in META_MEDIA_NAMES:
            found.append(content.group(1))
    found += SOURCE_TAG_RE.findall(text)
    found += MEDIA_URL_RE.findall(text)
    urls = []
    for url in found:
        url = urljoin(base_url, url.strip())
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return sorted(urls, key=_media_rank)


def find_frames(page, base_url):
    frames = []
    for url in FRAME_TAG_RE.findall(html.unescape(page)):
        url = urljoin(base_url, url.strip())
        if url.startswith(("http://", "https://")) and url not in frames:
            frames.append(url)
    return frames


def page_title(page):
    match = TITLE_RE.search(page or "")
    if not match:
        return None
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() or None


def _strip_reconnect_at_eof(command):
    """Drop ``-reconnect_at_eof 1`` from an ffmpeg argument list.

    py-tgcalls adds it to every URL.  ffmpeg then treats each normal end of
    file as a dropped connection: HLS playlists never get past their first
    segment and plain files never end.  The other reconnect flags stay.
    """
    result = []
    skip = False
    for index, part in enumerate(command):
        if skip:
            skip = False
            continue
        if part == "-reconnect_at_eof" and index + 1 < len(command):
            skip = True
            continue
        result.append(part)
    return result


def patch_pytgcalls():
    """Install the ``-reconnect_at_eof`` fix into py-tgcalls (idempotent)."""
    from pytgcalls import ffmpeg
    from pytgcalls.types.stream import media_stream

    original = ffmpeg.build_command
    if getattr(original, "_voicechat_patched", False):
        return

    def build_command(*args, **kwargs):
        return _strip_reconnect_at_eof(original(*args, **kwargs))

    build_command._voicechat_patched = True
    # check_stream looks the name up in ffmpeg, MediaStream in its own module.
    ffmpeg.build_command = build_command
    media_stream.build_command = build_command


def is_youtube(source):
    """YouTube links and plain search queries (resolved via ytsearch)."""
    source = (source or "").strip()
    return bool(YT_HOST_RE.match(source)) or not URL_RE.match(source)


def is_auth_error(error):
    text = str(error).lower()
    return any(marker in text for marker in YT_AUTH_MARKERS)


def _cookie_file_has(path, domains=YT_COOKIE_DOMAINS):
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        with open(path, encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return False
    return any(domain in text for domain in domains)


def _registered_domain(host):
    """``cdn.www.example.co`` -> ``example.co`` for cookie lookups."""
    parts = [part for part in (host or "").lower().split(".") if part]
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def _detect_js_runtime():
    for name, paths in JS_RUNTIMES:
        for path in paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return f"{name}:{path}"
        found = shutil.which(os.path.basename(paths[0]))
        if found:
            return f"{name}:{found}"
    return None


def _parse_browser_cookies(value):
    """``firefox:/path/profile`` -> yt-dlp ``cookiesfrombrowser`` tuple."""
    parts = [part.strip() or None for part in (value or "").strip().split(":", 3)]
    if not parts or not parts[0]:
        return None
    while len(parts) < 4:
        parts.append(None)
    return tuple(parts[:4])


def _version_tuple(version):
    parts = []
    for part in str(version).split("."):
        digits = re.match(r"\d+", part)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts[:3])


class YouTubeAuthError(Exception):
    """YouTube's bot check rejected every client and cookie combination."""

    def __init__(self, context):
        super().__init__("YouTube bot check")
        self.context = context


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
        self.has_audio = True
        self.codecs = None
        self.protocol = None
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
        self.watchdog = None
        self.loop = False
        self.volume = 100
        self.offset = 0.0
        self.started_at = 0.0
        self.paused_at = None
        self.switched_at = 0.0
        self.failed_in_row = 0

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
            "Береться найкраща якість джерела, але не вище цієї. Відео "
            "кодується на сервері в реальному часі: вище 1080p потрібен "
            "дуже потужний процесор, інакше картинка зависатиме"
        ),
        "cfg_max_fps": "Макс. кадрів за секунду (30 або 60; 60 — удвічі більше навантаження)",
        "cfg_video_mode": (
            "Як показувати відео: screen — як демонстрацію екрана (велике "
            "вікно), camera — як відео з камери"
        ),
        "cfg_audio_quality": (
            "Якість звуку: high (48 кГц стерео — максимум для дзвінків), "
            "studio (96 кГц), medium"
        ),
        "cfg_auto_leave": "Виходити з дзвінка, коли черга закінчилась",
        "cfg_announce": "Писати в чат, що зараз грає",
        "cfg_playlist_limit": "Скільки відео брати з плейлиста",
        "cfg_max_file_mb": "Макс. розмір файлу з Telegram для відтворення (МБ)",
        "cfg_cookies": (
            "Шлях до cookies.txt з сесією YouTube. Порожньо — взяти cookies "
            "VideoDownloader або /home/rkbot/URKbot/cookies.txt"
        ),
        "yt_auth": (
            "❌ <b>YouTube просить підтвердити, що це не бот, а робочих "
            "cookies не знайдено.</b>\n"
            "Покладіть cookies.txt із входом у YouTube у "
            "<code>/home/rkbot/URKbot/cookies.txt</code> (його ж використовує "
            "VideoDownloader, див. <code>{p}vdlcookies</code>) або вкажіть шлях "
            "у <code>{p}config VoiceChat</code> → <code>cookies_file</code>."
        ),
        "yt_auth_cookies": (
            "❌ <b>YouTube не прийняв cookies</b> (<code>{file}</code>). "
            "Схоже, сесія застаріла — оновіть cookies.txt."
        ),
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
            "⚠️ <b>Hikka сама працює на Telethon {have}, а дзвінкам потрібен "
            "{need}+.</b> Його модуль не чіпає, щоб не зламати Hikka — "
            "оновіть Hikka."
        ),
        "setup_running": "⏳ <b>Встановлюю:</b> <code>{}</code>",
        "setup_done": "✅ <b>Встановлено:</b> <code>{}</code>",
        "setup_restart": (
            "\nОновився Telethon — перезапустіть Hikka: "
            "<code>{}restart</code>."
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
        "no_audio": "без звуку",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "video_quality",
                "1080p",
                lambda: self.strings("cfg_video_quality"),
                validator=loader.validators.Choice(list(VIDEO_CAPS)),
            ),
            loader.ConfigValue(
                "max_fps",
                30,
                lambda: self.strings("cfg_max_fps"),
                validator=loader.validators.Integer(minimum=10, maximum=MAX_FPS),
            ),
            loader.ConfigValue(
                "video_mode",
                "screen",
                lambda: self.strings("cfg_video_mode"),
                validator=loader.validators.Choice(["screen", "camera"]),
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

    async def client_ready(self, client, db):
        self._client = client
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    async def on_unload(self):
        for player in list(self._players.values()):
            self._cancel_end_task(player)
            if self._calls is not None and player.current is not None:
                with contextlib.suppress(Exception):
                    await self._calls.leave_call(player.chat_id)
            self._drop_files(player, everything=True)
        self._players.clear()
        if self._tg is not None:
            with contextlib.suppress(Exception):
                await self._tg.disconnect()
        elif self._calls is not None:
            self._drop_call_handlers()
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
            if importlib.util.find_spec("hikkatl") is None:
                return self.strings("deps_unsafe").format(
                    have=utils.escape_html(telethon.__version__),
                    need=".".join(map(str, MIN_TELETHON)),
                )
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

            from pytgcalls import filters
            from pytgcalls.types import ChatUpdate

            if self._shares_client():
                calls = self._calls_on_hikka_client()
                tg = None
            else:
                calls, tg = await self._calls_on_own_connection()
            calls.on_update(filters.stream_end())(self._on_stream_end)
            calls.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))(
                self._on_left_call
            )
            try:
                await calls.start()
            except Exception:
                if tg is not None:
                    await tg.disconnect()
                else:
                    self._drop_call_handlers()
                raise
            self._tg = tg
            self._calls = calls
            return calls

    def _shares_client(self):
        """Whether Hikka's own client is upstream Telethon usable for calls."""
        try:
            import telethon
        except ImportError:
            return False
        return isinstance(self._client, telethon.TelegramClient) and (
            _version_tuple(telethon.__version__) >= MIN_TELETHON
        )

    def _calls_on_hikka_client(self):
        from pytgcalls import PyTgCalls
        from pytgcalls.mtproto.bridged_client import BridgedClient

        # py-tgcalls picks the MTProto backend from the client's top-level
        # module name, which for Hikka's subclass is ``hikka``, not
        # ``telethon``.  Vouch for this one client while it is wrapped.
        client = self._client
        original = BridgedClient.package_name
        BridgedClient.package_name = staticmethod(
            lambda obj: "telethon" if obj is client else original(obj)
        )
        try:
            return PyTgCalls(client)
        finally:
            BridgedClient.package_name = staticmethod(original)

    def _drop_call_handlers(self):
        """Detach py-tgcalls' update handlers from Hikka's client."""
        with contextlib.suppress(Exception):
            for callback, _ in list(self._client.list_event_handlers()):
                if getattr(callback, "__module__", "").startswith("pytgcalls"):
                    self._client.remove_event_handler(callback)

    async def _calls_on_own_connection(self):
        from pytgcalls import PyTgCalls
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

        return PyTgCalls(tg), tg

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
        return options

    def _vdl(self):
        """VideoDownloader instance, if loaded: its cookie setup is reused."""
        with contextlib.suppress(Exception):
            return self.lookup("VideoDownloader") or None
        return None

    def _yt_context(self, url="https://www.youtube.com/"):
        """Cookies file, JS runtime and browser cookies for ``url``'s site."""
        vdl = self._vdl()
        youtube = is_youtube(url)
        domains = (
            YT_COOKIE_DOMAINS
            if youtube
            else (_registered_domain(urlsplit(url).hostname or ""),)
        )
        candidates = []
        configured = str(self.config["cookies_file"] or "").strip()
        if configured:
            candidates.append(os.path.expanduser(configured))
        if vdl is not None:
            with contextlib.suppress(Exception):
                candidates.append(vdl._cookies_for(url))
        candidates.extend(YT_COOKIE_FILES)
        cookies = next(
            (path for path in candidates if _cookie_file_has(path, domains)), None
        )

        runtime = getattr(vdl, "_js_runtime", None) or _detect_js_runtime()

        browser = None
        if vdl is not None and youtube:
            with contextlib.suppress(Exception):
                browser = _parse_browser_cookies(vdl._yt_browser_cookies_value())
        if browser and browser[1] and not os.path.isdir(browser[1]):
            browser = None
        return {"cookies": cookies, "runtime": runtime, "browser": browser}

    @staticmethod
    def _cookie_copy(path):
        """yt-dlp writes rotated cookies back; use a copy if that is denied."""
        if os.access(path, os.W_OK):
            return path
        os.makedirs(WORK_DIR, exist_ok=True)
        copy = os.path.join(WORK_DIR, "cookies.txt")
        shutil.copyfile(path, copy)
        return copy

    def _youtube_attempts(self, base, context):
        """Option sets to try in order until YouTube hands out the formats."""
        extra = {}
        runtime = context.get("runtime")
        if runtime:
            name, _, path = runtime.partition(":")
            extra["js_runtimes"] = {name: {"path": path} if path else {}}
            extra["remote_components"] = {"ejs:github"}
        cookie_sets = []
        if context.get("cookies"):
            cookie_sets.append({"cookiefile": self._cookie_copy(context["cookies"])})
        else:
            cookie_sets.append({})
        if context.get("browser"):
            cookie_sets.append({"cookiesfrombrowser": context["browser"]})
        attempts = []
        for cookie_opts in cookie_sets:
            for clients, missing_pot in YT_CLIENT_PROFILES:
                options = {**base, **extra, **cookie_opts}
                youtube_args = {}
                if clients:
                    youtube_args["player_client"] = clients
                if missing_pot:
                    youtube_args["formats"] = ["missing_pot"]
                if youtube_args:
                    options["extractor_args"] = {"youtube": youtube_args}
                attempts.append(options)
        return attempts

    def _extract_sync(self, track, context=None):
        import yt_dlp

        base = self._ydl_options(track.audio_only)
        if not is_youtube(track.source):
            return self._extract_any_sync(track, base, context or {})
        attempts = self._youtube_attempts(base, context or {})
        error = None
        for options in attempts:
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(track.source, download=False)
            except Exception as attempt_error:
                # Keep the bot-check error: it explains the failure best.
                if error is None or not is_auth_error(error):
                    error = attempt_error
                text = str(attempt_error).lower()
                if any(marker in text for marker in YT_FATAL_MARKERS):
                    break
                continue
            if info and info.get("entries"):
                entries = [entry for entry in info["entries"] if entry]
                info = entries[0] if entries else None
            if info:
                return info
            error = ValueError("nothing found")
        raise error or ValueError("nothing found")

    def _extract_any_sync(self, track, base, context):
        """Any site: yt-dlp first, then the page and its embedded players."""
        import yt_dlp

        options = dict(base)
        if context.get("cookies"):
            options["cookiefile"] = self._cookie_copy(context["cookies"])
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(track.source, download=False)
            if info and info.get("entries"):
                entries = [entry for entry in info["entries"] if entry]
                info = entries[0] if entries else None
            if info:
                return info
            error = ValueError("nothing found")
        except Exception as ydl_error:
            error = ydl_error
        if not URL_RE.match(track.source):
            raise error
        found = self._scrape_sync(track.source, options)
        if found is None:
            raise error
        return found

    def _fetch_page(self, url, referer=None):
        """``(final_url, html)`` or ``(final_url, None)`` for a media response."""
        headers = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            final = response.geturl()
            kind = (response.headers.get("Content-Type") or "").lower()
            if kind.startswith(MEDIA_TYPES):
                return final, None
            charset = response.headers.get_content_charset() or "utf-8"
            return final, response.read(PAGE_LIMIT).decode(charset, "replace")

    def _ydl_embedded(self, url, referer, base):
        import yt_dlp

        options = {
            **base,
            "http_headers": {"User-Agent": BROWSER_UA, "Referer": referer},
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None
        if info and info.get("entries"):
            entries = [entry for entry in info["entries"] if entry]
            info = entries[0] if entries else None
        if not info:
            return None
        info.setdefault("http_headers", {})
        info["http_headers"].setdefault("Referer", referer)
        return info

    def _scrape_sync(self, url, base, referer=None, depth=0, seen=None):
        """Look for a playable stream in a page and its embedded players."""
        seen = seen if seen is not None else set()
        if url in seen:
            return None
        seen.add(url)
        try:
            final, page = self._fetch_page(url, referer)
        except Exception as error:
            logger.debug("VoiceChat could not fetch %s: %s", url, error)
            return None
        if page is None:
            return {
                "url": final,
                "http_headers": page_headers(referer or final),
                "webpage_url": referer or final,
                "_scraped": True,
            }
        title = page_title(page)
        media = find_media(page, final)
        if media:
            return {
                "url": media[0],
                "title": title,
                "http_headers": page_headers(final),
                "webpage_url": final,
                "_scraped": True,
            }
        if depth >= SCRAPE_DEPTH:
            return None
        for frame in find_frames(page, final)[:SCRAPE_FRAMES]:
            if frame in seen:
                continue
            info = self._ydl_embedded(frame, final, base)
            if info is None:
                info = self._scrape_sync(frame, base, final, depth + 1, seen)
            if info is not None:
                # The linked page names the film/episode; the player page
                # inside the iframe is usually titled "player" or similar.
                if title:
                    info["title"] = title
                return info
        return None

    @staticmethod
    def _format_url(fmt):
        # DASH formats point at fragments; ffmpeg wants the manifest.
        if "dash" in str(fmt.get("protocol") or "") and fmt.get("manifest_url"):
            return fmt["manifest_url"]
        return fmt["url"]

    def _apply_info(self, track, info):
        formats = info.get("requested_formats") or [info]

        def known(fmt, key):
            return fmt.get(key) not in (None, "none")

        video = next((f for f in formats if known(f, "vcodec")), None)
        audio = next(
            (f for f in formats if known(f, "acodec") and f is not video), None
        )
        if audio is None and video is not None and known(video, "acodec"):
            audio = video
        main = video or audio or formats[0]
        if video is None and audio is None:
            # Codecs unknown (direct links, scraped streams): ffprobe decides.
            url = self._format_url(main)
            track.video_url = None if track.audio_only else url
            track.audio_url = url
        else:
            track.video_url = (
                None if track.audio_only or not video else self._format_url(video)
            )
            track.audio_url = self._format_url(audio or video)
        track.has_audio = True
        track.headers = main.get("http_headers") or info.get("http_headers") or None
        track.width = (video or main).get("width") or info.get("width")
        track.height = (video or main).get("height") or info.get("height")
        track.fps = (video or main).get("fps") or info.get("fps")
        codecs = [
            codec_name(video.get("vcodec")) if video and not track.audio_only else None,
            codec_name((audio or {}).get("acodec")),
        ]
        track.codecs = " + ".join(c for c in codecs if c) or None
        protocol = str(main.get("protocol") or "")
        track.protocol = (
            "HLS" if "m3u8" in protocol or ".m3u8" in track.audio_url.lower()
            else "DASH" if "dash" in protocol or ".mpd" in track.audio_url.lower()
            else None
        )
        track.duration = info.get("duration") or track.duration
        track.is_live = bool(info.get("is_live"))
        track.title = info.get("title") or track.title
        track.webpage = info.get("webpage_url") or track.webpage
        track.resolved_at = time.monotonic()

    async def _ffprobe(self, url, headers=None):
        args = ["ffprobe", "-v", "error"]
        if not os.path.exists(url):
            args += http_args(headers) + hls_args(url)
        args += [
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate"
            ":stream_disposition=attached_pic"
            ":format=duration,format_name",
            "-of",
            "json",
            url,
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 30)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            return None
        with contextlib.suppress(ValueError):
            return json.loads(stdout.decode() or "{}")
        return None

    async def _probe_track(self, track):
        """Read the real codecs, frame size and stream layout with ffprobe."""
        target = track.video_url or track.audio_url
        data = await self._ffprobe(target, None if track.path else track.headers)
        streams = (data or {}).get("streams") or []
        if not streams:
            if track.path:
                raise ValueError("ffprobe could not read the file")
            return  # Trust yt-dlp; ffmpeg reports a real problem on play.
        videos = [
            s for s in streams
            if s.get("codec_type") == "video"
            and s.get("width")
            and not (s.get("disposition") or {}).get("attached_pic")
        ]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        if track.video_url and track.audio_url != track.video_url:
            # Separate audio stream (yt-dlp merged formats): keep its codec.
            audios = audios or [{"codec_name": None}]
        video = max(videos, key=lambda s: s["width"] * s.get("height", 0), default=None)
        if not video and not audios:
            raise ValueError("no audio or video stream found")
        if video and not track.audio_only:
            track.video_url = track.video_url or target
            track.width = video.get("width")
            track.height = video.get("height")
            rate = str(video.get("avg_frame_rate") or "0/1")
            num, _, den = rate.partition("/")
            with contextlib.suppress(ValueError, ZeroDivisionError):
                track.fps = float(num) / float(den or 1) or track.fps
        else:
            track.video_url = None
        track.has_audio = bool(audios)
        names = [
            codec_name(video.get("codec_name")) if track.video_url else None,
            codec_name(audios[0].get("codec_name")) if audios else None,
        ]
        if any(names):
            track.codecs = " + ".join(n for n in names if n)
        fmt = (data.get("format") or {})
        if not track.protocol:
            name = str(fmt.get("format_name") or "")
            track.protocol = "HLS" if "hls" in name else "DASH" if "dash" in name else None
        try:
            duration = float(fmt.get("duration"))
        except (TypeError, ValueError):
            duration = None
        if duration:
            track.duration = track.duration or duration
        elif not track.path and not track.duration:
            track.is_live = True

    async def _resolve(self, track):
        if track.resolved:
            return
        if track.path:
            track.video_url = None if track.audio_only else track.path
            track.audio_url = track.path
            await self._probe_track(track)
            track.resolved_at = time.monotonic()
            return
        youtube = is_youtube(track.source)
        context = self._yt_context(track.source) if URL_RE.match(track.source) or youtube else None
        try:
            info = await asyncio.to_thread(self._extract_sync, track, context)
        except Exception as error:
            if youtube and is_auth_error(error):
                raise YouTubeAuthError(context or {}) from error
            raise
        self._apply_info(track, info)
        if not youtube:
            await self._probe_track(track)

    def _playlist_sync(self, url, audio_only, requester, context=None):
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "playlistend": int(self.config["playlist_limit"]),
            "skip_download": True,
        }
        if context and context.get("cookies"):
            options["cookiefile"] = self._cookie_copy(context["cookies"])
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
                self._playlist_sync,
                query,
                audio_only,
                requester,
                self._yt_context() if is_youtube(query) else None,
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

    def _ffmpeg_parameters(self, track, offset=0):
        args = []
        if offset and not track.is_live:
            args += ["-ss", str(int(offset))]
        if not track.path:
            args += http_args(track.headers)
            args += hls_args(track.video_url, track.audio_url)
        return shlex.join(args) if args else None

    def _fps(self, track):
        try:
            cap = int(self.config["max_fps"])
        except (KeyError, TypeError, ValueError):
            cap = MAX_FPS
        return clamp_fps(track.fps, cap)

    async def _stream(self, track, offset=0):
        """The stream for ``track``; video goes out as a screen share if set."""
        media = self._media_stream(track, offset)
        if not track.has_video or self.config["video_mode"] != "screen":
            return media
        from pytgcalls.types.raw import Stream

        # Probe now and hand py-tgcalls a raw Stream with the picture in the
        # ``screen`` slot: it then joins the call's presentation (the large
        # screen-share tile) instead of sending it as the camera.
        await media.check_stream()
        if media.camera is None:
            return Stream(microphone=media.microphone)
        return Stream(microphone=media.microphone, screen=media.camera)

    def _media_stream(self, track, offset=0):
        from pytgcalls.types import MediaStream
        from pytgcalls.types.raw import AudioParameters, VideoParameters

        patch_pytgcalls()
        rate, channels = AUDIO_MODES.get(
            self.config["audio_quality"], AUDIO_MODES["high"]
        )
        width, height = fit_frame(
            track.width, track.height, self.config["video_quality"]
        )
        has_video = track.has_video
        return MediaStream(
            track.video_url if has_video else track.audio_url,
            audio_parameters=AudioParameters(rate, channels),
            video_parameters=VideoParameters(
                width, height, self._fps(track), True
            ),
            audio_path=track.audio_url if track.has_audio else None,
            audio_flags=(
                MediaStream.Flags.REQUIRED
                if track.has_audio
                else MediaStream.Flags.IGNORE
            ),
            video_flags=(
                MediaStream.Flags.AUTO_DETECT
                if has_video
                else MediaStream.Flags.IGNORE
            ),
            # Headers travel in ffmpeg_parameters: py-tgcalls passes one
            # -headers per header and ffmpeg would keep only the last one.
            headers=None,
            ffmpeg_parameters=self._ffmpeg_parameters(track, offset),
        )

    def _quality_label(self, track):
        if not track.has_video:
            rate, channels = AUDIO_MODES.get(
                self.config["audio_quality"], AUDIO_MODES["high"]
            )
            label = f"{self.strings('audio_only')}, {rate // 1000} kHz"
        else:
            width, height = fit_frame(
                track.width, track.height, self.config["video_quality"]
            )
            label = f"{width}×{height}@{self._fps(track)}"
            if not track.has_audio:
                label += f", {self.strings('no_audio')}"
        details = [part for part in (track.codecs, track.protocol) if part]
        if details:
            label += " · " + " · ".join(details)
        return utils.escape_html(label)

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
        stream = await self._stream(track, offset)
        from pytgcalls.types import GroupCallConfig

        self._cancel_end_task(player)
        player.generation += 1
        previous, previous_ends = player.current, player.pending_ends
        player.current = track
        player.pending_ends = ({"audio"} if track.has_audio else set()) | (
            {"video"} if track.has_video else set()
        )
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
        self._arm_watchdog(player, track)
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
        player.generation += 1
        if self._calls is not None:
            with contextlib.suppress(Exception):
                await self._calls.leave_call(player.chat_id)
        self._drop_files(player, everything=True)
        self._players.pop(player.chat_id, None)

    def _error_text(self, track, error):
        if isinstance(error, YouTubeAuthError):
            cookies = error.context.get("cookies")
            if cookies:
                return self.strings("yt_auth_cookies").format(
                    file=utils.escape_html(cookies)
                )
            return self.strings("yt_auth").format(p=self.get_prefix())
        return self.strings("play_failed").format(
            title=self._title_html(track),
            error=utils.escape_html(str(error)[:300] or type(error).__name__),
        )

    # --------------------------------------------------------- stream events

    def _cancel_end_task(self, player):
        for name in ("end_task", "watchdog"):
            task = getattr(player, name)
            setattr(player, name, None)
            if (
                task is not None
                and not task.done()
                and task is not asyncio.current_task()
            ):
                task.cancel()

    def _arm_watchdog(self, player, track):
        if track.is_live or not track.duration:
            return
        player.watchdog = asyncio.ensure_future(
            self._watchdog(player, player.generation, float(track.duration))
        )

    async def _watchdog(self, player, generation, duration):
        while player.generation == generation and player.current is not None:
            left = duration + WATCHDOG_SLACK - player.position
            if left <= 0 and not player.paused:
                logger.info("VoiceChat: no end event, advancing by duration")
                await self._finish(player, generation)
                return
            await asyncio.sleep(min(max(left, 1.0), 30.0))

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
            player.generation += 1
            self._drop_files(player, everything=True)

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

    @staticmethod
    def _telethon_version():
        try:
            import telethon
        except ImportError:
            return None
        return _version_tuple(getattr(telethon, "__version__", "0"))

    def _setup_plan(self):
        """Packages to install, whether a restart is needed, and a warning."""
        packages = []
        if importlib.util.find_spec("pytgcalls") is None:
            packages.append("py-tgcalls")
        # Always refresh yt-dlp: YouTube breaks old versions every few weeks.
        packages.append("yt-dlp")
        restart, warning = False, None
        version = self._telethon_version()
        if version is None or version < MIN_TELETHON:
            if importlib.util.find_spec("hikkatl") is not None:
                # Hikka runs on hikkatl, so upstream Telethon is ours alone.
                packages.append(TELETHON_REQUIREMENT)
                restart = "telethon" in sys.modules
            else:
                # Hikka itself runs on this telethon; never swap it under it.
                warning = self.strings("deps_unsafe").format(
                    have=".".join(map(str, version or (0,))),
                    need=".".join(map(str, MIN_TELETHON)),
                )
        return packages, restart, warning

    @staticmethod
    def _pip_command(packages):
        # Same flags as Hikka's own dependency installer.
        user_install = (
            "PIP_TARGET" not in os.environ
            and "VIRTUAL_ENV" not in os.environ
            and sys.prefix == getattr(sys, "base_prefix", sys.prefix)
        )
        return [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "-q",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            *(["--user"] if user_install else []),
            *packages,
        ]

    @loader.command(ru_doc="Встановити / оновити залежності")
    async def vcsetup(self, message):
        """Встановити відсутні залежності (py-tgcalls, yt-dlp, за потреби Telethon)"""
        packages, restart, warning = self._setup_plan()
        listed = " ".join(packages)
        message = await utils.answer(
            message, self.strings("setup_running").format(listed)
        )
        result = await asyncio.to_thread(
            subprocess.run,
            self._pip_command(packages),
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
        importlib.invalidate_caches()
        text = self.strings("setup_done").format(listed)
        if restart:
            text += self.strings("setup_restart").format(self.get_prefix())
        if warning:
            text += "\n\n" + warning
        await utils.answer(message, text)
