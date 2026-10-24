# meta developer: @Huang_Baike
# meta version: 2.6.1
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
import random
import re
import secrets
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
# Self-healing (from Splaueef/host#109): how often the call is checked, how
# long the stream may stand still before it counts as frozen, and the pauses
# between rejoin attempts.
HEALTH_INTERVAL = 5.0
STALL_TIMEOUT = 20.0
LOST_GRACE = 5.0
HEALTHY_AFTER = 60.0
RECONNECT_DELAYS = (1, 3, 5, 10, 20)
LOST_STATES = {"FAILED", "TIMEOUT", "CLOSED"}
# A stream that ends this long before its known duration is treated as a
# dropped connection: the track restarts where it stopped (a few times).
EARLY_END = 15.0
MAX_RECOVERIES = 3
# Automatic quality: when the CPU stays this busy while video plays, the
# picture steps down one resolution (encoding cost follows the pixel count).
OVERLOAD_PERCENT = 90.0
OVERLOAD_SAMPLES = 2  # ticks in a row (PANEL_REFRESH apart)
RELAX_PERCENT = 55.0  # below this a new track may step back up
QUALITY_STEPS = ("4k", "1440p", "1080p", "720p", "480p", "360p")
PANEL_REFRESH = 15.0
# Hikka's inline.form() posts the panel and then waits, with no time limit,
# until the bot hears that it was sent (``chosen_inline_result``, which
# Telegram reports only with inline feedback on in @BotFather); without it
# the panel can never be edited.  Hikka's edit() also reports failure by
# returning False, not by raising.  After this long, or this many failed
# edits in a row, the panel becomes a text message the account edits itself.
PANEL_TIMEOUT = 20.0
PANEL_FAILURES = 3
# When Hikka cannot post the panel at all (e.g. "No query results": the
# bot's answer to the inline query never came), it posts the traceback to
# the chat itself.  That message is removed and the text panel is used;
# the inline one is tried again after this long (or on the next vcnow).
PANEL_RETRY = 5 * 60
# Rich panel: Bot API rich messages (editMessageText with ``rich_message``),
# the same format MiniGames uses.  Buttons live inside the message.
BOT_API = "https://api.telegram.org/bot{token}/{method}"
RICH_STYLES = {"toggle": "primary", "skip": "success", "stop": "danger"}
SEEK_STEP = 10
VOLUME_STEP = 10
RESTORE_DELAY = 15.0
RESTORE_MAX_AGE = 12 * 60 * 60
SEARCH_RESULTS = 5
INFO_CACHE_TTL = 30 * 60  # reuse a resolved link for this long
INFO_CACHE_SIZE = 64
SAVE_EVERY = 60.0  # the playback position is saved at most this often
PREBUFFER_MIN = 3  # seconds, when Telegram downloads twice as fast as needed
# Scaling is done after the frame rate is lowered (the "fps" filter drops
# frames first), and with bilinear: lanczos cost several times more CPU for
# a difference nobody sees after the call's own video compression.
SCALE_FLAGS = "bilinear"
# Automatic quality also watches this module's own CPU (Hikka, which encodes
# the video inside ntgcalls, plus its ffmpeg processes), in percent of one
# core: on a many-core server the whole system rarely looks busy even when
# the stream eats two cores.  ``cpu_limit`` config, 0 turns it off.
DEFAULT_CPU_LIMIT = 150
WARM_UP_DELAY = 20.0
# Radio: music posts read from a channel and played round and round.
RADIO_SKIP = 10  # deleted / non-audio posts skipped in a row before giving up
CHANNEL_LINK_RE = re.compile(
    r"^(?:https?://)?(?:t|telegram)\.me/(?:c/(\d+)|([\w]{4,}))", re.IGNORECASE
)

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
# Without cookies only the clients that usually work anonymously.
YT_ANON_PROFILES = (
    (None, False),
    (["tv_simply", "default", "-tv"], False),
    (["android_vr"], False),
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


def parse_channel(value):
    """``-100…``, ``@name``, ``t.me/name`` or ``t.me/c/123/4`` -> peer for Telethon."""
    value = str(value or "").strip()
    if not value:
        return None
    link = CHANNEL_LINK_RE.match(value)
    if link:
        return int(f"-100{link.group(1)}") if link.group(1) else link.group(2)
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.lstrip("@") or None


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


_SUPPORTED_OPTIONS = {}


def supported_options(program):
    """Options ``program`` accepts, from ``-h full`` (read once, then cached).

    py-tgcalls dumps the whole ffmpeg help again for every command it builds;
    the answer never changes while the process lives.
    """
    if program not in _SUPPORTED_OPTIONS:
        try:
            result = subprocess.run(
                [program, "-h", "full"], capture_output=True, text=True, timeout=20
            )
            text = result.stdout
        except (OSError, subprocess.SubprocessError):
            return None
        names = {"-i"}
        for group in re.findall(r"(?m)^ *(--?\w[\w-]*(?:, *--?\w[\w-]*)*)", text):
            names.update(name.strip() for name in group.split(","))
        _SUPPORTED_OPTIONS[program] = names
    return _SUPPORTED_OPTIONS[program]


def clean_command(command, program=None, blacklist=None):
    """py-tgcalls' ``cleanup_commands`` without a subprocess per call."""
    supported = supported_options(program or command[0])
    if supported is None:
        return list(command)
    result = []
    skip_value = False
    for part in command:
        if not part:
            continue
        is_flag = part[0] == "-" and not part[1:].replace(".", "", 1).isdigit()
        if is_flag:
            skip_value = part not in supported or bool(
                blacklist and part in blacklist
            )
        if not skip_value:
            result.append(part)
        elif not is_flag:
            skip_value = False
    return result


def patch_pytgcalls():
    """Install the ffmpeg fixes into py-tgcalls (idempotent)."""
    from pytgcalls import ffmpeg, ytdlp
    from pytgcalls.types.stream import media_stream

    original = ffmpeg.build_command
    if getattr(original, "_voicechat_patched", False):
        return

    def build_command(*args, **kwargs):
        return _strip_reconnect_at_eof(original(*args, **kwargs))

    async def cleanup_commands(commands, process_name=None, blacklist=None):
        return await asyncio.to_thread(
            clean_command, commands, process_name, blacklist
        )

    build_command._voicechat_patched = True
    # check_stream looks the names up in ffmpeg, MediaStream in its own module.
    for module in (ffmpeg, media_stream):
        module.build_command = build_command
    for module in (ffmpeg, media_stream, ytdlp):
        module.cleanup_commands = cleanup_commands


INFO_KEYS = (
    "url", "manifest_url", "protocol", "vcodec", "acodec", "width", "height",
    "fps", "http_headers", "title", "duration", "is_live", "webpage_url",
    "_scraped",
)


def slim_info(info):
    """Only what playback needs: yt-dlp's full answer lists every format
    (hundreds of KB for a YouTube video) and would sit in the cache."""
    slim = {key: info[key] for key in INFO_KEYS if key in info}
    if info.get("requested_formats"):
        slim["requested_formats"] = [
            {key: fmt[key] for key in INFO_KEYS if key in fmt}
            for fmt in info["requested_formats"]
        ]
    return slim


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


# ------------------------------------------------------- virtual browser
#
# A static cookies.txt dies quickly: Google rotates session cookies all the
# time, and whoever rotates first (the browser it came from, VideoDownloader's
# yt-dlp, this module's yt-dlp) invalidates every other copy.  Here a headless
# Chromium with its own profile owns the session: it revisits YouTube on a
# timer, lets Google rotate the cookies the way a real browser does, and
# exports a fresh cookies.txt.  yt-dlp only ever gets a copy of that file, so
# nothing else rotates the session behind the browser's back.
BROWSER_DIR = os.path.abspath(".voicechat_browser")
BROWSER_HOME = "https://www.youtube.com/"
BROWSER_SETTLE_MS = 10000
SESSION_DOMAINS = ("youtube.com", "google.com", "youtu.be")
LOGIN_COOKIES = ("SAPISID", "__Secure-3PAPISID", "__Secure-1PSID", "LOGIN_INFO")
BROWSER_ARGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
)


def parse_netscape(text):
    """cookies.txt (Netscape format) -> list of cookie dicts."""
    cookies = []
    for line in (text or "").splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 7:
            continue
        domain, _, path, secure, expires, name, value = parts[:7]
        try:
            expires = int(float(expires))
        except ValueError:
            expires = 0
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "expires": expires if expires > 0 else -1,
                "secure": secure.upper() == "TRUE",
                "httpOnly": http_only,
            }
        )
    return cookies


def to_netscape(cookies):
    lines = ["# Netscape HTTP Cookie File", "# Exported by VoiceChat browser", ""]
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        expires = cookie.get("expires")
        expires = int(expires) if expires and expires > 0 else 0
        prefix = "#HttpOnly_" if cookie.get("httpOnly") else ""
        lines.append(
            "\t".join(
                (
                    prefix + domain,
                    "TRUE" if domain.startswith(".") else "FALSE",
                    cookie.get("path") or "/",
                    "TRUE" if cookie.get("secure") else "FALSE",
                    str(expires),
                    cookie.get("name") or "",
                    cookie.get("value") or "",
                )
            )
        )
    return "\n".join(lines) + "\n"


def session_cookies(cookies):
    return [
        cookie
        for cookie in cookies
        if any(
            (cookie.get("domain") or "").lstrip(".").endswith(domain)
            for domain in SESSION_DOMAINS
        )
    ]


def is_logged_in(cookies):
    names = {
        cookie.get("name")
        for cookie in cookies
        if "youtube.com" in (cookie.get("domain") or "")
    }
    return any(name in names for name in LOGIN_COOKIES)


def to_playwright(cookie):
    """A parsed cookie as Playwright's ``add_cookies`` wants it."""
    result = {
        "name": cookie["name"],
        "value": cookie["value"],
        "expires": cookie.get("expires", -1),
        "httpOnly": bool(cookie.get("httpOnly")),
        "secure": bool(cookie.get("secure")),
        "sameSite": "Lax",
    }
    if cookie["name"].startswith("__Host-"):
        # __Host- cookies must not carry a Domain attribute.
        host = cookie["domain"].lstrip(".")
        result["url"] = f"https://{host}/"
    else:
        result["domain"] = cookie["domain"]
        result["path"] = cookie.get("path") or "/"
    return result


class BrowserSession:
    """Headless Chromium profile that keeps a YouTube session alive."""

    def __init__(self, root=BROWSER_DIR):
        self.root = root
        self.profile = os.path.join(root, "profile")
        self.cookie_file = os.path.join(root, "cookies.txt")
        self.state_file = os.path.join(root, "state.json")
        self.lock = asyncio.Lock()

    @staticmethod
    def available():
        return importlib.util.find_spec("playwright") is not None

    def state(self):
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def _save_state(self, **values):
        state = {**self.state(), **values}
        os.makedirs(self.root, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        return state

    @property
    def configured(self):
        return os.path.isdir(self.profile)

    def has_session(self):
        return bool(self.state().get("logged_in")) and os.path.isfile(
            self.cookie_file
        )

    def age(self):
        refreshed = self.state().get("refreshed_at") or 0
        return time.time() - refreshed if refreshed else float("inf")

    async def refresh(self, import_cookies=None):
        """Visit YouTube, let Google rotate the cookies, export them."""
        from playwright.async_api import async_playwright

        async with self.lock:
            os.makedirs(self.profile, exist_ok=True)
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    self.profile,
                    headless=True,
                    args=list(BROWSER_ARGS),
                    user_agent=BROWSER_UA,
                    locale="en-US",
                )
                try:
                    if import_cookies:
                        await context.clear_cookies()
                        await context.add_cookies(
                            [to_playwright(c) for c in import_cookies]
                        )
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(
                        BROWSER_HOME, wait_until="domcontentloaded", timeout=60000
                    )
                    # YouTube's page calls RotateCookies by itself; give it time.
                    await page.wait_for_timeout(BROWSER_SETTLE_MS)
                    cookies = session_cookies(await context.cookies())
                finally:
                    await context.close()
            temp = self.cookie_file + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(to_netscape(cookies))
            os.replace(temp, self.cookie_file)
            return self._save_state(
                refreshed_at=time.time(),
                logged_in=is_logged_in(cookies),
                count=len(cookies),
                error=None,
            )

    def record_error(self, error):
        return self._save_state(error=str(error)[:300], failed_at=time.time())

    def reset(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ------------------------------------------------- Telegram file streaming
#
# Files from Telegram are not downloaded before playing.  A small HTTP server
# on 127.0.0.1 hands ffmpeg the file straight from Telegram, chunk by chunk,
# and honours Range requests: playback starts within seconds, MP4 files with
# their index at the end work, and seeking only fetches the part it needs.
TG_CHUNK = 512 * 1024  # Telegram wants offsets aligned to the request size.
TG_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
TG_RETRIES = 4
TG_GIVE_UP = 8  # failed rounds in a row (with backoff ≈ 2 min) before giving up
# Free disk space kept aside when caching a file for streaming.
TG_DISK_RESERVE = 300 * 1024 * 1024


class TgCache:
    """One download of a Telegram file shared by every reader.

    py-tgcalls runs separate ffmpeg processes for sound and picture and
    ffmpeg reopens the stream on every seek, so without this the same bytes
    would be fetched again and again.  Several workers download chunks in
    parallel (Telethon pipelines the requests over one connection), the
    chunks around the current playback position first, then the rest of
    the file in the background, into a sparse file on disk.
    """

    def __init__(self, item, path, workers=4):
        self.item = item
        self.size = item["size"]
        self.chunks = max(1, -(-self.size // TG_CHUNK))
        self.have = bytearray(self.chunks)
        self.done = 0
        self.focus = 0
        self.inflight = set()
        self.path = path
        self.workers = max(1, int(workers))
        self.error = None
        self.window = None  # max chunks ahead of focus to fetch; None = all
        self.started = time.monotonic()
        self.fetched_bytes = 0
        self._cond = asyncio.Condition()
        self._tasks = []
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(self.size)  # sparse: no space used up front
        self._fd = os.open(path, os.O_RDWR)

    @property
    def complete(self):
        return self.done >= self.chunks

    @property
    def progress(self):
        return self.done / self.chunks

    @property
    def speed(self):
        """Download speed in bytes per second."""
        return self.fetched_bytes / max(time.monotonic() - self.started, 0.001)

    def start(self):
        if not self._tasks and not self.complete:
            self.started = time.monotonic()
            self.fetched_bytes = 0
            self._tasks = [
                asyncio.ensure_future(self._worker()) for _ in range(self.workers)
            ]

    def _next(self):
        if self.window is not None:
            stop = min(self.chunks, self.focus + self.window)
            for index in range(self.focus, stop):
                if not self.have[index] and index not in self.inflight:
                    return index
            return None
        for index in range(self.focus, self.chunks):
            if not self.have[index] and index not in self.inflight:
                return index
        for index in range(0, self.focus):
            if not self.have[index] and index not in self.inflight:
                return index
        return None

    async def set_window(self, window):
        """Limit (prefetch) or lift (playing) how far ahead workers go."""
        async with self._cond:
            self.window = window
            self._cond.notify_all()

    async def _worker(self):
        failures = 0
        while True:
            async with self._cond:
                index = self._next()
                while index is None:
                    if self.complete or self.error:
                        return
                    await self._cond.wait()
                    index = self._next()
                self.inflight.add(index)
            try:
                data = await self._fetch(index)
                os.pwrite(self._fd, data, index * TG_CHUNK)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Network trouble: give the chunk back and keep trying with a
                # growing pause; readers fail only after a long outage.
                failures += 1
                logger.warning(
                    "VoiceChat could not fetch chunk %s (%s): %s",
                    index, failures, error,
                )
                async with self._cond:
                    self.inflight.discard(index)
                    if failures >= TG_GIVE_UP:
                        self.error = error
                    self._cond.notify_all()
                if failures >= TG_GIVE_UP:
                    return
                await asyncio.sleep(min(30, 2 ** failures))
                continue
            failures = 0
            async with self._cond:
                self.inflight.discard(index)
                if not self.have[index]:
                    self.have[index] = 1
                    self.done += 1
                    self.fetched_bytes += len(data)
                self._cond.notify_all()

    async def _fetch(self, index):
        expected = min(TG_CHUNK, self.size - index * TG_CHUNK)
        for attempt in range(TG_RETRIES):
            try:
                async for chunk in self.item["client"].iter_download(
                    self.item["message"],
                    offset=index * TG_CHUNK,
                    request_size=TG_CHUNK,
                    limit=1,
                    file_size=self.size,
                ):
                    data = bytes(chunk)
                    if len(data) >= expected:
                        return data[:expected]
                    raise IOError("short chunk")
                raise IOError("empty chunk")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if attempt == TG_RETRIES - 1:
                    raise
                if "FILE_REFERENCE" in str(error).upper():
                    await self._refresh_reference()
                else:
                    await asyncio.sleep(0.5 * (attempt + 1))

    async def _refresh_reference(self):
        message = self.item["message"]
        fresh = await self.item["client"].get_messages(
            message.chat_id, ids=message.id
        )
        if fresh is not None:
            self.item["message"] = fresh

    async def wait_for(self, index):
        """Wait until chunk ``index`` is on disk, steering workers to it."""
        if self.have[index]:
            return
        self.start()
        async with self._cond:
            if not self.have[index]:
                self.focus = index
                self._cond.notify_all()
            while not self.have[index]:
                if self.error is not None:
                    raise IOError(f"Telegram download failed: {self.error}")
                await self._cond.wait()

    def buffered_from(self, index):
        """Number of chunks available in a row starting at ``index``."""
        count = 0
        while index + count < self.chunks and self.have[index + count]:
            count += 1
        return count

    def read(self, start, length):
        return os.pread(self._fd, length, start)

    async def close(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(BaseException):
                await task
        self._tasks = []
        with contextlib.suppress(OSError):
            os.close(self._fd)
        with contextlib.suppress(OSError):
            os.remove(self.path)


def parse_range(header, size):
    """``Range`` header -> ``(start, end)`` inclusive, or ``None`` if invalid."""
    if not header:
        return 0, size - 1
    match = TG_RANGE_RE.match(header.strip())
    if not match or size <= 0:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # the last N bytes
        start = max(0, size - int(last))
        return start, size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return None
    return start, end


class TelegramStreamer:
    """Serves Telegram documents to ffmpeg over local HTTP with Range."""

    def __init__(self, workers=4):
        self._server = None
        self._port = None
        self._items = {}
        self._lock = asyncio.Lock()
        self.workers = workers

    async def _ensure(self):
        async with self._lock:
            if self._server is None:
                self._server = await asyncio.start_server(
                    self._handle, "127.0.0.1", 0
                )
                self._port = self._server.sockets[0].getsockname()[1]

    async def register(self, client, message, size, mime=None, ext=""):
        await self._ensure()
        token = secrets.token_urlsafe(16)
        item = {
            "client": client,
            "message": message,
            "size": int(size),
            "mime": mime or "application/octet-stream",
            "cache": None,
        }
        os.makedirs(WORK_DIR, exist_ok=True)
        if shutil.disk_usage(WORK_DIR).free > int(size) + TG_DISK_RESERVE:
            item["cache"] = TgCache(
                item, os.path.join(WORK_DIR, f"tg-{token}.part"), self.workers
            )
        self._items[token] = item
        safe_ext = ext if re.fullmatch(r"\.\w{1,8}", ext or "") else ""
        return f"http://127.0.0.1:{self._port}/{token}{safe_ext}"

    @staticmethod
    def _token(url):
        return (url or "").rsplit("/", 1)[-1].split(".", 1)[0]

    def cache(self, url):
        item = self._items.get(self._token(url))
        return item and item["cache"]

    def unregister(self, url):
        item = self._items.pop(self._token(url), None)
        if item and item["cache"] is not None:
            asyncio.ensure_future(item["cache"].close())

    async def prebuffer(
        self, url, start_byte, length, report=None, timeout=60, rate=None,
        enough=None,
    ):
        """Wait until ``length`` bytes from ``start_byte`` are downloaded.

        With ``rate`` (bytes per second of playback) and ``enough`` (bytes),
        stop as soon as ``enough`` is buffered and the download runs at least
        twice as fast as playback: the rest arrives in time anyway.
        """
        cache = self.cache(url)
        if cache is None:
            return
        first = min(cache.chunks - 1, max(0, int(start_byte)) // TG_CHUNK)
        need = min(cache.chunks - first, max(1, -(-int(length) // TG_CHUNK)))
        early = -(-int(enough) // TG_CHUNK) if enough else need
        deadline = time.monotonic() + timeout
        await cache.wait_for(first)
        while cache.buffered_from(first) < need and time.monotonic() < deadline:
            if (
                rate
                and cache.buffered_from(first) >= early
                and cache.speed >= 2 * rate
            ):
                break
            if cache.error is not None:
                raise IOError(f"Telegram download failed: {cache.error}")
            if report is not None:
                await report(cache.buffered_from(first) / need, cache)
            # Keep the workers on this region rather than the file's tail.
            cache.focus = first + cache.buffered_from(first)
            await asyncio.sleep(0.5)

    async def close(self):
        for item in list(self._items.values()):
            if item["cache"] is not None:
                await item["cache"].close()
        self._items.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = (lines[0].split(" ", 2) + ["", ""])[:3]
            headers = {}
            for line in lines[1:]:
                name, _, value = line.partition(":")
                if name:
                    headers[name.strip().lower()] = value.strip()
            token = target.split("?", 1)[0].rsplit("/", 1)[-1].split(".", 1)[0]
            item = self._items.get(token)
            if method not in ("GET", "HEAD") or item is None:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                return
            size = item["size"]
            wanted = parse_range(headers.get("range"), size)
            if wanted is None:
                writer.write(
                    f"HTTP/1.1 416 Range Not Satisfiable\r\n"
                    f"Content-Range: bytes */{size}\r\nContent-Length: 0\r\n\r\n"
                    .encode()
                )
                return
            start, end = wanted
            partial = "range" in headers
            status = "206 Partial Content" if partial else "200 OK"
            lines = [
                f"HTTP/1.1 {status}",
                f"Content-Type: {item['mime']}",
                f"Content-Length: {end - start + 1}",
                "Accept-Ranges: bytes",
                "Connection: close",
            ]
            if partial:
                lines.append(f"Content-Range: bytes {start}-{end}/{size}")
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
            if method == "GET":
                await self._send(item, start, end, writer)
            await writer.drain()
        except (
            ConnectionError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            asyncio.TimeoutError,
        ):
            pass  # ffmpeg drops connections when it seeks; that is normal.
        except Exception:
            logger.exception("VoiceChat stream request failed")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _send(self, item, start, end, writer):
        cache = item["cache"]
        if cache is None:
            return await self._send_direct(item, start, end, writer)
        position = start
        while position <= end:
            index = position // TG_CHUNK
            await cache.wait_for(index)
            stop = min(end + 1, (index + 1) * TG_CHUNK)
            writer.write(cache.read(position, stop - position))
            await writer.drain()
            position = stop

    async def _send_direct(self, item, start, end, writer):
        """Without disk space for a cache: stream straight from Telegram."""
        aligned = start - start % TG_CHUNK
        skip = start - aligned
        remaining = end - start + 1
        for attempt in range(2):
            try:
                async for chunk in item["client"].iter_download(
                    item["message"],
                    offset=aligned,
                    request_size=TG_CHUNK,
                    file_size=item["size"],
                ):
                    chunk = bytes(chunk)
                    if skip:
                        chunk, skip = chunk[skip:], 0
                    chunk = chunk[:remaining]
                    writer.write(chunk)
                    await writer.drain()
                    remaining -= len(chunk)
                    aligned += TG_CHUNK
                    if remaining <= 0:
                        return
                return
            except ConnectionError:
                raise
            except Exception as error:
                # File references expire after a while; fetch a fresh one.
                if attempt or "FILE_REFERENCE" not in str(error).upper():
                    raise
                message = item["message"]
                fresh = await item["client"].get_messages(
                    message.chat_id, ids=message.id
                )
                if fresh is None:
                    raise
                item["message"] = fresh


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
        self.stream_url = None
        self.tg_ref = None  # (chat_id, message_id) of a streamed Telegram file
        self.codecs = None
        self.protocol = None
        self.audio_streams = []  # [{"lang", "title", "codec", "channels"}]
        self.audio_index = None  # chosen audio track, None = ffmpeg's pick
        self.prefetch_task = None
        self.recoveries = 0
        self.quality_drop = 0  # resolution steps below video_quality (auto)
        self.probed = False  # stream layout known: py-tgcalls' check skipped
        self.fresh = False  # bypass the resolve cache (expired stream URLs)
        self.radio = False  # picked by the radio, not queued by someone
        self.resolved_at = 0.0

    @property
    def resolved(self):
        if self.path or self.stream_url:
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
        self.panel = None
        self.reconnects = 0
        self.rejoin_task = None
        self.last_played = None
        self.progress_seen = False
        self.progress_at = 0.0
        self.overload_hits = 0
        self.quality_drop = 0
        self.ticker = None
        self.last_panel_text = None
        self.now_message = None  # id of the "now playing" text, edited in place
        self.panel_failures = 0
        self.last_now_text = None
        self.rich_data = {}  # panel action -> callback_data of its rich button
        self.radio = False
        self.radio_channel = None  # overrides the radio_channel config value
        self.radio_order = []  # message ids of the current round
        self.radio_pos = 0
        self.radio_last = None  # message id of the last song the radio played
        self.radio_next = None  # the next radio song, already prepared
        self.radio_task = None
        self.radio_error = None

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
            "кодується на сервері в реальному часі: 1080p вже займає близько "
            "двох ядер, вище — потрібен дуже потужний процесор"
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
            "Налаштуйте віртуальний браузер: <code>{p}vcbrowser</code> — він "
            "сам підтримуватиме сесію YouTube живою."
        ),
        "yt_auth_cookies": (
            "❌ <b>YouTube не прийняв cookies</b> (<code>{file}</code>). "
            "Схоже, сесія застаріла.\n"
            "Щоб це не повторювалось, передайте сесію віртуальному браузеру: "
            "<code>{p}vcbrowser</code>."
        ),
        "cfg_browser_session": (
            "Тримати сесію YouTube у віртуальному браузері (headless Chromium) "
            "і брати cookies з нього"
        ),
        "cfg_browser_refresh": "Як часто браузер оновлює сесію YouTube (хвилин)",
        "br_status": (
            "🌐 <b>Віртуальний браузер</b>\n\n"
            "Playwright: {installed}\n"
            "Сесія YouTube: {session}\n"
            "Cookies: {count}\n"
            "Останнє оновлення: {refreshed}\n"
            "Автооновлення: кожні {every} хв{error}\n\n"
            "<b>Як підключити:</b>\n"
            "1. <code>{p}vcbrowser setup</code> — встановити Chromium (один раз).\n"
            "2. На комп'ютері відкрийте <b>приватне вікно</b>, увійдіть у YouTube "
            "(краще окремим акаунтом), експортуйте cookies.txt розширенням "
            "«Get cookies.txt LOCALLY» і <b>закрийте вікно</b>, не виходячи з "
            "акаунта — тоді сесію не ротуватиме ваш браузер.\n"
            "3. Надішліть файл сюди й відповідайте на нього "
            "<code>{p}vcbrowser import</code>.\n\n"
            "Далі браузер сам відкриває YouTube за розкладом, Google оновлює "
            "cookies як у звичайному браузері, а yt-dlp отримує свіжу копію."
        ),
        "br_yes": "🟢 так",
        "br_no": "🔴 ні",
        "br_none": "—",
        "br_logged": "🟢 активна",
        "br_logged_out": "🔴 немає входу в акаунт",
        "br_error": "\nОстання помилка: <code>{}</code>",
        "br_need_setup": (
            "❌ <b>Playwright не встановлено.</b> Виконайте "
            "<code>{}vcbrowser setup</code>."
        ),
        "br_setup_running": (
            "⏳ <b>Встановлюю Playwright і Chromium…</b> Це може зайняти кілька "
            "хвилин."
        ),
        "br_setup_done": (
            "✅ <b>Віртуальний браузер встановлено.</b> Тепер "
            "<code>{}vcbrowser import</code> у відповідь на cookies.txt."
        ),
        "br_no_file": (
            "❌ <b>Відповідайте на файл cookies.txt</b> або вкажіть шлях: "
            "<code>{}vcbrowser import /шлях/cookies.txt</code>."
        ),
        "br_bad_file": "❌ <b>У файлі немає cookies YouTube.</b>",
        "br_working": "⏳ <b>Відкриваю YouTube у віртуальному браузері…</b>",
        "br_done": (
            "✅ <b>Сесію оновлено.</b> Cookies: {count}, вхід в акаунт: {session}."
        ),
        "br_failed": "❌ <b>Браузер не зміг оновити сесію:</b> <code>{}</code>",
        "br_reset": "🗑 <b>Профіль віртуального браузера видалено.</b>",
        "br_usage": (
            "❌ <b>Дії:</b> <code>setup</code>, <code>import</code>, "
            "<code>refresh</code>, <code>reset</code>."
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
        "cfg_audio_languages": (
            "Бажані мови звукової доріжки по черзі (коди ISO або частина "
            "назви доріжки), напр. uk, ukr, ru, en"
        ),
        "cfg_auto_quality": (
            "Автоматично знижувати роздільність, якщо процесор не встигає "
            "(і повертати, коли звільниться)"
        ),
        "cfg_cpu_limit": (
            "Авто-якість: знижувати роздільність, коли модуль (Hikka з "
            "кодуванням + ffmpeg) займає більше стількох % одного ядра "
            "(100 = одне ядро; 0 — дивитися лише на завантаження всього сервера)"
        ),
        "cfg_decode_threads": (
            "Потоків на декодування відео (0 — половина ядер; менше потоків "
            "лишає більше процесора для кодування в дзвінок)"
        ),
        "quality_down": "📉 Процесор не встигає — знижую якість до {}",
        "stats": (
            "📊 <b>Навантаження</b>\n\n"
            "Процесор сервера: <b>{system}%</b> ({cores} ядер), load {load}\n"
            "Hikka (з кодуванням відео в дзвінок): <b>{hikka}%</b>\n"
            "ffmpeg (декодування): <b>{ffmpeg}%</b> · процесів: {processes}\n"
            "Модуль разом: <b>{own}%</b> одного ядра{limit}\n"
            "Пам'ять: {memory}\n"
            "{players}\n\n{verdict}"
        ),
        "stats_player": "🎬 {title}\n   {quality}{level}",
        "stats_limit": " (ліміт авто-якості {}%)",
        "stats_level": " · авто-якість: −{} крок(и)",
        "stats_ok": "🟢 Запас є — відео має йти плавно.",
        "stats_busy": (
            "🟡 Процесор майже зайнятий — можливі підлагування. Допоможе "
            "<code>{p}config VoiceChat</code> → <code>video_quality</code> = 720p."
        ),
        "stats_over": (
            "🔴 Процесор перевантажений — звук і відео смикатимуться. "
            "Знизьте <code>video_quality</code> або <code>max_fps</code>."
        ),
        "stats_unavailable": "📊 <b>Немає psutil</b> — навантаження не виміряти.",
        "cfg_panel": "Панель керування з кнопками замість текстових повідомлень",
        "cfg_panel_style": (
            "Вигляд панелі: rich — Telegram Rich Text (заголовок, таблиця, "
            "кольорові кнопки в самому повідомленні), html — звичайний текст "
            "із кнопками під ним"
        ),
        "rich_state": "Стан",
        "rich_volume": "Гучність",
        "rich_loop": "Повтор",
        "rich_quality": "Якість",
        "rich_queue": "У черзі",
        "rich_atrack": "Доріжка",
        "rich_download": "Завантажено",
        "rich_radio": "📻 Радіо",
        "rich_radio_on": "грає музика з каналу",
        "rich_next": "Далі: ",
        "rich_footer": "🎙 VoiceChat · оновлюється кожні {} с",
        "cfg_prefetch": "Заздалегідь готувати наступний трек у черзі",
        "cfg_access": (
            "Хто керує: owner — лише власник і довірені; queue — усі в групі "
            "можуть додавати в чергу й дивитися; all — усі можуть усе"
        ),
        "cfg_trusted": "ID користувачів, яким дозволено керувати всім",
        "cfg_resume": "Після перезапуску Hikka відновлювати чергу й позицію",
        "no_access": "🔒 <b>Вам це не дозволено.</b>",
        "atrack_none": "🎚 <b>У цьому треку одна звукова доріжка.</b>",
        "atrack_list": "🎚 <b>Звукові доріжки</b> (зараз: {current}):\n{tracks}\n\nОбрати: <code>{p}vcaudiotrack N</code>",
        "atrack_set": "🎚 <b>Доріжка:</b> {}",
        "atrack_bad": "❌ <b>Немає доріжки з таким номером.</b>",
        "atrack_auto": "авто",
        "removed": "🗑 <b>Прибрано з черги:</b> {}",
        "moved": "↕️ <b>Переміщено на {pos}:</b> {title}",
        "pos_bad": "❌ <b>Вкажіть номер із черги</b> (див. <code>{}vcqueue</code>).",
        "search_usage": "❌ <b>Вкажіть запит:</b> <code>{}vcsearch назва</code>",
        "searching": "🔎 <b>Шукаю…</b>",
        "search_none": "❌ <b>Нічого не знайдено.</b>",
        "search_head": "🔎 <b>Результати:</b> {}",
        "search_text": "{head}\n{lines}\n\nГрати: <code>{p}vcadd посилання</code>",
        "search_picked": "⏳ <b>Додаю:</b> {}",
        "restoring": "♻️ <b>Відновлюю відтворення після перезапуску…</b>",
        "panel_next": "\nДалі: {}",
        "btn_back": "⏪ 10",
        "btn_play": "▶️",
        "btn_pause": "⏸",
        "btn_fwd": "10 ⏩",
        "btn_skip": "⏭",
        "btn_loop": "🔁",
        "btn_stop": "⏹",
        "btn_voldown": "🔉",
        "btn_volup": "🔊",
        "btn_atrack": "🎚",
        "btn_queue": "📜",
        "btn_cancel": "✖️",
        "toast_paused": "⏸ Пауза",
        "toast_resumed": "▶️ Грає",
        "toast_skipped": "⏭ Далі",
        "toast_stopped": "⏹ Зупинено",
        "toast_volume": "🔊 Гучність {}%",
        "toast_loop_on": "🔁 Повтор увімкнено",
        "toast_loop_off": "➡️ Повтор вимкнено",
        "toast_seek": "⏩ {}",
        "toast_atrack": "🎚 {}",
        "toast_no_access": "🔒 Вам це не дозволено",
        "toast_ended": "Відтворення вже завершено",
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
        "download_progress": (
            "📥 <b>Завантажую з Telegram:</b> {percent}%\n"
            "{bar} {done} / {total} МБ · {speed} МБ/с"
        ),
        "cfg_tg_stream": (
            "Відтворювати файли з Telegram одразу, потоком (без очікування "
            "повного завантаження на сервер)"
        ),
        "tg_stream_label": "потік із Telegram",
        "cfg_tg_workers": (
            "Скільки частин файлу з Telegram качати паралельно (більше — "
            "швидше, 4–8 зазвичай достатньо)"
        ),
        "cfg_tg_prebuffer": "Скільки секунд відео набрати в буфер перед стартом",
        "buffering": "⏳ <b>Буферизація:</b> {percent}% · {speed} МБ/с",
        "now_download": "\nЗавантажено: {percent}% · {speed} МБ/с",
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
        "skipped_to": "⏭ <b>Далі:</b> {}",
        "cfg_radio_channel": (
            "Канал із музикою для режиму радіо: ID (напр. -1002562411082), "
            "@username або посилання t.me. Акаунт має бачити цей канал"
        ),
        "cfg_radio_shuffle": "Радіо грає пісні каналу у випадковому порядку",
        "cfg_radio_limit": "Скільки останніх пісень каналу бере радіо",
        "radio_status": (
            "📻 <b>Радіо:</b> {state}\n"
            "Канал: {channel}\n"
            "Порядок: {order}{count}\n\n"
            "<code>{p}vcradio on [канал]</code> — грати музику з каналу по колу\n"
            "<code>{p}vcradio off</code> — вимкнути\n"
            "<code>{p}vcradio reload</code> — перечитати канал"
        ),
        "radio_count": "\nПісень у колі: {}",
        "radio_shuffled": "випадковий",
        "radio_in_order": "за порядком каналу",
        "radio_no_channel": (
            "❌ <b>Канал для радіо не вказано.</b> Задайте "
            "<code>{p}config VoiceChat</code> → <code>radio_channel</code> "
            "(напр. <code>-1002562411082</code>) або "
            "<code>{p}vcradio on -100…</code>."
        ),
        "radio_loading": "📻 <b>Читаю канал…</b>",
        "radio_bad": "❌ <b>Не вдалося прочитати канал радіо:</b> <code>{}</code>",
        "radio_empty": "❌ <b>У каналі радіо немає музики.</b>",
        "radio_on_later": (
            "📻 <b>Радіо увімкнено</b> ({count} пісень) — заграє, коли "
            "закінчиться черга."
        ),
        "radio_off": "📻 <b>Радіо вимкнено.</b> Поточна пісня дограє до кінця.",
        "radio_reloaded": "📻 <b>Канал перечитано:</b> {count} пісень.",
        "radio_stopped": "📻 <b>Радіо зупинено:</b> <code>{}</code>",
        "radio_failed": "❌ <b>Радіо не змогло нічого увімкнути.</b>",
        "radio_usage": (
            "❌ <b>Дії:</b> <code>on [канал]</code>, <code>off</code>, "
            "<code>reload</code>."
        ),
        "radio_next": "📻 Далі з радіо: {}",
        "panel_radio": "\n📻 Радіо: грає музика з каналу",
        "inline_broken": (
            "⚠️ <b>Inline-бот не дає оновлювати панель</b>: Telegram не "
            "повідомляє йому, що панель надіслано. Тому панель тепер текстова "
            "й без кнопок, керуйте командами.\n"
            "Щоб повернути кнопки: @BotFather → <code>/setinlinefeedback</code> "
            "→ {bot} → <b>Enabled</b>, потім <code>{p}vcnow</code>."
        ),
        "inline_your_bot": "бот Hikka",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "video_quality",
                "720p",
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
                "tg_stream",
                True,
                lambda: self.strings("cfg_tg_stream"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "tg_workers",
                4,
                lambda: self.strings("cfg_tg_workers"),
                validator=loader.validators.Integer(minimum=1, maximum=16),
            ),
            loader.ConfigValue(
                "tg_prebuffer",
                8,
                lambda: self.strings("cfg_tg_prebuffer"),
                validator=loader.validators.Integer(minimum=0, maximum=60),
            ),
            loader.ConfigValue(
                "max_file_mb",
                2000,
                lambda: self.strings("cfg_max_file_mb"),
                validator=loader.validators.Integer(minimum=1, maximum=4000),
            ),
            loader.ConfigValue(
                "browser_session",
                True,
                lambda: self.strings("cfg_browser_session"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "browser_refresh_min",
                30,
                lambda: self.strings("cfg_browser_refresh"),
                validator=loader.validators.Integer(minimum=10, maximum=720),
            ),
            loader.ConfigValue(
                "cookies_file",
                "",
                lambda: self.strings("cfg_cookies"),
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "audio_languages",
                ["uk", "ukr"],
                lambda: self.strings("cfg_audio_languages"),
                validator=loader.validators.Series(loader.validators.String()),
            ),
            loader.ConfigValue(
                "auto_quality",
                True,
                lambda: self.strings("cfg_auto_quality"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "cpu_limit",
                DEFAULT_CPU_LIMIT,
                lambda: self.strings("cfg_cpu_limit"),
                validator=loader.validators.Integer(minimum=0, maximum=6400),
            ),
            loader.ConfigValue(
                "decode_threads",
                0,
                lambda: self.strings("cfg_decode_threads"),
                validator=loader.validators.Integer(minimum=0, maximum=16),
            ),
            loader.ConfigValue(
                "panel",
                True,
                lambda: self.strings("cfg_panel"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "panel_style",
                "rich",
                lambda: self.strings("cfg_panel_style"),
                validator=loader.validators.Choice(["rich", "html"]),
            ),
            loader.ConfigValue(
                "prefetch_next",
                True,
                lambda: self.strings("cfg_prefetch"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "access",
                "owner",
                lambda: self.strings("cfg_access"),
                validator=loader.validators.Choice(["owner", "queue", "all"]),
            ),
            loader.ConfigValue(
                "trusted_users",
                [],
                lambda: self.strings("cfg_trusted"),
                validator=loader.validators.Series(loader.validators.Integer()),
            ),
            loader.ConfigValue(
                "resume_after_restart",
                True,
                lambda: self.strings("cfg_resume"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "radio_channel",
                "",
                lambda: self.strings("cfg_radio_channel"),
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "radio_shuffle",
                True,
                lambda: self.strings("cfg_radio_shuffle"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "radio_limit",
                1000,
                lambda: self.strings("cfg_radio_limit"),
                validator=loader.validators.Integer(minimum=10, maximum=10000),
            ),
        )
        self._client = None
        self._calls = None
        self._tg = None
        self._connect_lock = asyncio.Lock()
        self._players = {}
        self._browser = BrowserSession()
        self._streamer = TelegramStreamer()
        self._info_cache = collections.OrderedDict()
        self._process = None
        self._children = {}
        self._last_system = None
        self._last_own = None
        self._yt_first = {}
        self._saved_at = 0.0
        self._browser_task = None
        self._restore_task = None
        self._warm_task = None
        self._health_task = None
        self._inline_broken = False
        self._inline_hint_sent = False
        self._panel_retry_at = 0.0
        self._rich_broken = False

    async def client_ready(self, client, db):
        self._client = client
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        self._browser_task = asyncio.ensure_future(self._browser_loop())
        self._restore_task = asyncio.ensure_future(self._restore_all())
        self._warm_task = asyncio.ensure_future(self._warm_up())

    async def _warm_up(self):
        """Import the heavy libraries and read ffmpeg's options in the
        background, so the first command does not wait for them."""
        await asyncio.sleep(WARM_UP_DELAY)

        def work():
            for name in ("yt_dlp", "pytgcalls"):
                with contextlib.suppress(Exception):
                    importlib.import_module(name)
            if shutil.which("ffmpeg"):
                supported_options("ffmpeg")
                supported_options("ffprobe")
                _hls_demuxer_options()

        with contextlib.suppress(Exception):
            await asyncio.to_thread(work)

    def _browser_enabled(self):
        return bool(self.config["browser_session"]) and self._browser.available()

    async def _browser_loop(self):
        """Revisit YouTube on schedule so the session never goes stale."""
        while True:
            await asyncio.sleep(60)
            if not self._browser_enabled() or not self._browser.configured:
                continue
            if self._browser.age() < int(self.config["browser_refresh_min"]) * 60:
                continue
            try:
                await self._browser.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("VoiceChat browser refresh failed: %s", error)
                self._browser.record_error(error)

    async def on_unload(self):
        for task in (self._restore_task, self._warm_task, self._health_task):
            if task is not None:
                task.cancel()
        self._health_task = None
        for player in list(self._players.values()):
            self._stop_ticker(player)
            self._cancel_rejoin(player)
        await self._streamer.close()
        if self._browser_task is not None:
            self._browser_task.cancel()
            self._browser_task = None
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
            self._hook_connection(calls)
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
            if self._health_task is None or self._health_task.done():
                self._health_task = asyncio.ensure_future(self._health_loop())
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
        error = await self._prepare_chat(chat_id)
        if error:
            await utils.answer(message, error)
            return None
        return chat_id

    async def _prepare_chat(self, chat_id):
        """Get the call machinery ready for ``chat_id``; error text or None."""
        if not str(chat_id).startswith("-"):
            return self.strings("only_groups")
        error = self._check_dependencies()
        if error:
            return error
        try:
            await self._ensure_calls()
            await self._prime_chat(chat_id)
        except PermissionError:
            return self.strings("unauthorized")
        except Exception as error:
            logger.exception("VoiceChat could not connect")
            return self.strings("connect_failed").format(
                utils.escape_html(str(error) or type(error).__name__)
            )
        return None

    def _player(self, chat_id, create=True):
        player = self._players.get(chat_id)
        if player is None and create:
            player = self._players[chat_id] = Player(chat_id)
            settings = self._settings(chat_id)
            with contextlib.suppress(TypeError, ValueError):
                player.volume = max(1, min(200, int(settings.get("volume") or 100)))
            player.radio = bool(settings.get("radio"))
            player.radio_channel = settings.get("radio_channel") or None
            player.radio_last = settings.get("radio_last")
        return player

    # A chat's player settings (volume, radio …) outlive the call: the player
    # is created with them again next time, also after a restart.

    def _settings(self, chat_id):
        with contextlib.suppress(Exception):
            saved = self.get("chat_settings", {}) or {}
            return dict(saved.get(str(chat_id)) or {})
        return {}

    def _store_settings(self, chat_id, **values):
        with contextlib.suppress(Exception):
            saved = dict(self.get("chat_settings", {}) or {})
            saved[str(chat_id)] = {**(saved.get(str(chat_id)) or {}), **values}
            self.set("chat_settings", saved)

    def _remember(self, player):
        # ``radio`` itself is stored only by .vcradio / .vcstop: a radio that
        # failed is off for this session, not for good.
        self._store_settings(
            player.chat_id,
            volume=player.volume,
            radio_channel=player.radio_channel,
            radio_last=player.radio_last,
        )

    # ------------------------------------------------------------- resolving

    def _ydl_options(self, audio_only):
        cap = self.config["video_quality"]
        short_side = VIDEO_CAPS.get(cap, VIDEO_CAPS["1080p"])[1]
        try:
            max_fps = int(self.config["max_fps"])
        except (KeyError, TypeError, ValueError):
            max_fps = DEFAULT_FPS
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
            # Best video stream up to the cap plus best audio.  A source
            # above max_fps would be decoded in full only to drop frames, so
            # the frame rate is capped too.  SDR is preferred (HDR washes
            # out in yuv420p); H.264 decodes far cheaper than VP9/AV1 and
            # YouTube has it up to 1080p, above that VP9 is the limit.
            options["format"] = "bv*+ba/b"
            options["format_sort"] = [
                f"res:{short_side}",
                f"fps:{max_fps}",
                "hdr:sdr",
                "vcodec:h264" if short_side <= 1080 else "vcodec:vp9",
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
        snapshot = None
        if youtube and self._browser_enabled() and self._browser.has_session():
            snapshot = self._browser.cookie_file
            candidates.append(snapshot)
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
        return {
            "cookies": cookies,
            "snapshot": cookies is not None and cookies == snapshot,
            "runtime": runtime,
            "browser": browser,
        }

    @staticmethod
    def _cookie_copy(path, force=False):
        """yt-dlp writes cookies back; give it a copy when it must not.

        ``force`` is for the browser's export: only the browser may rotate
        that session, so yt-dlp never touches the original.
        """
        if not force and os.access(path, os.W_OK):
            return path
        os.makedirs(WORK_DIR, exist_ok=True)
        copy = os.path.join(
            WORK_DIR, f"cookies-{os.getpid()}-{time.monotonic_ns()}.txt"
        )
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
            cookie_sets.append(
                (
                    {
                        "cookiefile": self._cookie_copy(
                            context["cookies"], force=context.get("snapshot")
                        )
                    },
                    YT_CLIENT_PROFILES,
                )
            )
            # A stale session can fail where no cookies at all still pass.
            cookie_sets.append(({}, YT_ANON_PROFILES))
        else:
            cookie_sets.append(({}, YT_CLIENT_PROFILES))
        if context.get("browser"):
            cookie_sets.append(
                ({"cookiesfrombrowser": context["browser"]}, YT_CLIENT_PROFILES)
            )
        attempts = []
        for cookie_opts, profiles in cookie_sets:
            for clients, missing_pot in profiles:
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
        # Start with the combination that worked last time for this setup.
        setup = (bool((context or {}).get("cookies")), bool((context or {}).get("browser")))
        first = self._yt_first.get(setup, 0)
        order = list(range(len(attempts)))
        if 0 < first < len(order):
            order.insert(0, order.pop(first))
        error = None
        for position in order:
            options = attempts[position]
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
                self._yt_first[setup] = position
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
        # yt-dlp's own format data is enough when it names both codecs.
        track.probed = bool(video or audio) and (
            not track.video_url or bool(track.width and track.height)
        )
        track.resolved_at = time.monotonic()

    async def _ffprobe(self, url, headers=None):
        args = ["ffprobe", "-v", "error"]
        if not os.path.exists(url):
            args += http_args(headers) + hls_args(url)
        args += [
            "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,channels"
            ":stream_disposition=attached_pic"
            ":stream_tags=language,title"
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
            if track.path or track.stream_url:
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
        track.probed = True
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
        if track.audio_url == target and len(audios) > 1:
            track.audio_streams = [
                {
                    "lang": ((a.get("tags") or {}).get("language") or "").lower(),
                    "title": (a.get("tags") or {}).get("title") or "",
                    "codec": codec_name(a.get("codec_name")) or "",
                    "channels": a.get("channels") or 0,
                }
                for a in audios
            ]
            if track.audio_index is None:
                track.audio_index = self._preferred_audio(track.audio_streams)
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

    def _preferred_audio(self, streams):
        """Index of the first audio track in a preferred language, or None."""
        wanted = [
            str(lang).strip().lower()
            for lang in (self.config["audio_languages"] or [])
            if str(lang).strip()
        ]
        for lang in wanted:
            # A language tag beats a mention in another track's title.
            for index, stream in enumerate(streams):
                if stream["lang"] and stream["lang"].startswith(lang):
                    return index
            for index, stream in enumerate(streams):
                if lang in stream["title"].lower():
                    return index
        return None

    def _audio_track_label(self, stream, index):
        parts = [stream["lang"].upper() or "—"]
        if stream["title"]:
            parts.append(stream["title"])
        if stream["codec"]:
            parts.append(stream["codec"])
        if stream["channels"]:
            parts.append(f"{stream['channels']}ch")
        return f"{index + 1}. " + " · ".join(parts)

    async def _resolve(self, track):
        task = track.prefetch_task
        if task is not None and task is not asyncio.current_task():
            # The background prefetch is already on it; share its work.
            with contextlib.suppress(Exception):
                await task
        if track.resolved:
            return
        local = track.path or track.stream_url
        if local:
            track.video_url = None if track.audio_only else local
            track.audio_url = local
            await self._probe_track(track)
            if track.stream_url:
                track.protocol = self.strings("tg_stream_label")
                track.is_live = False
            track.resolved_at = time.monotonic()
            return
        youtube = is_youtube(track.source)
        key = (track.source, track.audio_only, self.config["video_quality"])
        cached = None if track.fresh else self._info_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < INFO_CACHE_TTL:
            info = cached[1]
        else:
            context = (
                self._yt_context(track.source)
                if URL_RE.match(track.source) or youtube
                else None
            )
            try:
                info = await asyncio.to_thread(self._extract_sync, track, context)
            except Exception as error:
                if not (youtube and is_auth_error(error)):
                    raise
                info = await self._retry_with_fresh_browser(track)
                if info is None:
                    raise YouTubeAuthError(context or {}) from error
            info = slim_info(info)
            self._info_cache[key] = (time.monotonic(), info)
            while len(self._info_cache) > INFO_CACHE_SIZE:
                self._info_cache.popitem(last=False)
        track.fresh = False
        self._apply_info(track, info)
        if not youtube and not track.probed:
            await self._probe_track(track)

    async def _retry_with_fresh_browser(self, track):
        """After a bot check, renew the browser session once and retry."""
        if not (self._browser_enabled() and self._browser.configured):
            return None
        if self._browser.age() < 120:
            return None
        try:
            await self._browser.refresh()
        except Exception as error:
            logger.warning("VoiceChat browser refresh failed: %s", error)
            self._browser.record_error(error)
            return None
        context = self._yt_context(track.source)
        try:
            return await asyncio.to_thread(self._extract_sync, track, context)
        except Exception:
            return None

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
        track = await self._media_track(reply, audio_only, requester, status)
        return None if track is None else [track]

    async def _media_track(self, reply, audio_only, requester=None, status=None):
        """Track for an audio / video message; ``None`` when it is too big."""
        file = reply.file
        title = getattr(file, "title", None) or getattr(file, "name", None)
        performer = getattr(file, "performer", None)
        if performer and title:
            title = f"{performer} — {title}"
        title = title or f"{file.mime_type or 'media'} #{reply.id}"
        duration = getattr(file, "duration", None)

        if self.config["tg_stream"] and file.size:
            self._streamer.workers = int(self.config["tg_workers"])
            url = await self._streamer.register(
                self._client, reply, file.size, file.mime_type, file.ext or ""
            )
            track = Track(
                url,
                title=title,
                audio_only=audio_only,
                duration=duration,
                requester=requester,
            )
            track.stream_url = url
            track.tg_ref = (reply.chat_id, reply.id)
            track.webpage = None
            return track

        limit = int(self.config["max_file_mb"])
        if (file.size or 0) > limit * 1024 * 1024:
            if status is not None:
                await utils.answer(status, self.strings("too_big").format(limit))
            return None
        if status is not None:
            await utils.answer(status, self.strings("downloading"))
        folder = os.path.join(WORK_DIR, str(abs(_chat_id(reply))))
        os.makedirs(folder, exist_ok=True)
        path = await self._client.download_media(
            reply,
            file=os.path.join(folder, f"{reply.id}{file.ext or ''}"),
            progress_callback=(
                self._progress_reporter(status) if status is not None else None
            ),
        )
        return Track(
            path,
            title=title,
            audio_only=audio_only,
            path=path,
            duration=duration,
            requester=requester,
        )

    def _progress_reporter(self, status, every=3.0):
        """Telethon progress callback that edits ``status`` now and then."""
        started = time.monotonic()
        last = [0.0]

        async def report(done, total):
            now = time.monotonic()
            if now - last[0] < every or not total:
                return
            last[0] = now
            percent = int(done * 100 / total)
            filled = percent // 10
            speed = done / max(now - started, 0.001) / 1024 / 1024
            with contextlib.suppress(Exception):
                await utils.answer(
                    status,
                    self.strings("download_progress").format(
                        percent=percent,
                        bar="▰" * filled + "▱" * (10 - filled),
                        done=f"{done / 1048576:.1f}",
                        total=f"{total / 1048576:.1f}",
                        speed=f"{speed:.1f}",
                    ),
                )

        return report

    # --------------------------------------------------------------- playing

    def _ffmpeg_parameters(self, track, offset=0, frame=None):
        """ffmpeg options for py-tgcalls; ``frame`` is (width, height, fps)
        of the picture sent when the caller knows it exactly."""
        args = []
        if offset and not track.is_live:
            args += ["-ss", str(int(offset))]
        if not track.path:
            args += http_args(track.headers)
            args += hls_args(track.video_url, track.audio_url)
        if track.has_video:
            # Video decoder threads only ("--video" section, before -i): the
            # rest of the cores stay free for encoding into the call.
            args += ["--video", "-threads", str(self._decode_threads())]
            if frame is not None:
                # py-tgcalls scales every decoded frame and only then lets
                # "-r" throw the extra ones away (half of them for 60 fps).
                # The last -vf wins, so this one ("-atend") drops frames
                # first and scales the rest with a cheaper filter.
                width, height, fps = frame
                args += [
                    "-atend", "-an", "-sn", "-dn", "-vf",
                    f"fps={fps},scale={width}:{height}:flags={SCALE_FLAGS}",
                ]
        audio = []
        if track.audio_index is not None and len(track.audio_streams) > 1:
            audio += ["-map", f"0:a:{int(track.audio_index)}"]
        if audio:
            # py-tgcalls: "--audio" starts options for the sound ffmpeg only,
            # "-atmid" puts them after its input.
            args += ["--audio", "-atmid", *audio]
        return shlex.join(args) if args else None

    def _video_cap(self, track):
        """The configured quality cap, lowered by automatic quality steps."""
        cap = self.config["video_quality"]
        if cap not in QUALITY_STEPS:
            cap = "1080p"
        index = min(
            len(QUALITY_STEPS) - 1,
            QUALITY_STEPS.index(cap) + max(0, int(track.quality_drop)),
        )
        return QUALITY_STEPS[index]

    def _decode_threads(self):
        configured = int(self.config["decode_threads"] or 0)
        if configured > 0:
            return configured
        return max(1, min(4, (os.cpu_count() or 2) // 2))

    def _fps(self, track):
        try:
            cap = int(self.config["max_fps"])
        except (KeyError, TypeError, ValueError):
            cap = MAX_FPS
        return clamp_fps(track.fps, cap)

    def _raw_stream(self, track, offset=0):
        """Build py-tgcalls' stream from what is already known about ``track``.

        ``MediaStream.check_stream`` would run two ffprobes over the network
        and two ffmpeg help dumps on every start, seek and track switch; the
        probe that resolving did already answered all of it.  Returns None
        when something is unknown and the full check is needed.
        """
        if track.has_video and not (track.width and track.height):
            return None
        if not (track.has_audio or track.has_video):
            return None
        from ntgcalls import MediaSource
        from pytgcalls.ffmpeg import build_command
        from pytgcalls.list_to_cmd import list_to_cmd
        from pytgcalls.types.raw import (
            AudioParameters,
            AudioStream,
            Stream,
            VideoParameters,
            VideoStream,
        )

        patch_pytgcalls()
        frame = None
        if track.has_video:
            width, height = fit_frame(
                track.width, track.height, self._video_cap(track)
            )
            frame = (width, height, self._fps(track))
        params = self._ffmpeg_parameters(track, offset, frame)

        def shell(path, parameters):
            # Strip here too: ``build_command`` may be py-tgcalls' original.
            command = _strip_reconnect_at_eof(
                build_command(
                    "ffmpeg", params, path, parameters, [], None, track.is_live
                )
            )
            return list_to_cmd(clean_command(command))

        microphone = camera = None
        if track.has_audio:
            rate, channels = AUDIO_MODES.get(
                self.config["audio_quality"], AUDIO_MODES["high"]
            )
            audio = AudioParameters(rate, channels)
            microphone = AudioStream(
                MediaSource.SHELL, shell(track.audio_url, audio), audio
            )
        if track.has_video:
            width, height, fps = frame
            video = VideoParameters(width, height, fps, True)
            camera = VideoStream(
                MediaSource.SHELL, shell(track.video_url, video), video
            )
        if camera is not None and self.config["video_mode"] == "screen":
            return Stream(microphone=microphone, screen=camera)
        return Stream(microphone=microphone, camera=camera)

    async def _stream(self, track, offset=0):
        """The stream for ``track``; video goes out as a screen share if set."""
        if track.probed:
            raw = self._raw_stream(track, offset)
            if raw is not None:
                return raw
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
            track.width, track.height, self._video_cap(track)
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
                track.width, track.height, self._video_cap(track)
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

    async def _prebuffer(self, track, offset=0, status=None):
        """Fill a few seconds of a Telegram file before the call starts it."""
        seconds = int(self.config["tg_prebuffer"])
        if not track.stream_url or seconds <= 0:
            return
        cache = self._streamer.cache(track.stream_url)
        if cache is None or cache.complete:
            return
        duration = float(track.duration or 0)
        rate = cache.size / duration if duration else 1024 * 1024
        start = cache.size * min(1.0, offset / duration) if duration else 0
        last = [0.0]

        async def report(fraction, cache):
            now = time.monotonic()
            if status is None or now - last[0] < 2:
                return
            last[0] = now
            with contextlib.suppress(Exception):
                await utils.answer(
                    status,
                    self.strings("buffering").format(
                        percent=int(fraction * 100),
                        speed=f"{cache.speed / 1048576:.1f}",
                    ),
                )

        await self._streamer.prebuffer(
            track.stream_url,
            start,
            rate * seconds,
            report,
            rate=rate,
            enough=rate * min(seconds, PREBUFFER_MIN),
        )

    async def _start(self, player, track, offset=0, status=None):
        """Resolve ``track`` and put it on air.  Caller holds ``player.lock``."""
        if track is not player.current:
            self._relax_quality(player, track)
        await self._resolve(track)
        cache = self._streamer.cache(track.stream_url) if track.stream_url else None
        if cache is not None:
            await cache.set_window(None)  # playing now: fetch the whole file
        await self._prebuffer(track, offset, status)
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
        player.last_played = None
        player.progress_seen = False
        player.progress_at = player.switched_at
        self._arm_watchdog(player, track)
        if previous is not None and previous is not track:
            self._drop_track_file(previous, player)
        player.failed_in_row = 0
        if player.volume != 100:
            with contextlib.suppress(Exception):
                await self._calls.change_volume_call(player.chat_id, player.volume)
        self._start_ticker(player)
        self._schedule_prefetch(player)
        self._save_state()

    async def _show_now(self, player, status=None, note=None):
        """Show what plays in the chat's one "now playing" message.

        A panel or text the chat already has is edited in place, and
        ``status`` (a command's own answer) only gets ``note``.  Without one,
        ``status`` becomes it, or a new message is posted.
        """
        if player.current is None:
            return
        if await self._edit_now(player):
            if status is not None and note:
                await self._say(player.chat_id, status, note)
            return
        if self._panel_enabled() and await self._show_panel(player, status):
            return
        if self._panel_enabled():
            text = self._now_text(player)  # the text panel
        elif status is None and not self.config["announce"]:
            return
        else:
            text = note or self._now_text(player)
        message = await self._say(player.chat_id, status, text)
        message_id = getattr(message, "id", None)
        player.now_message = message_id if isinstance(message_id, int) else None
        player.last_now_text = text

    def _now_text(self, player):
        """The "now playing" text: the whole panel (progress, volume …) when
        a panel is wanted but the inline bot cannot keep one."""
        if self._panel_enabled():
            return self._panel_text(player)
        return self._playing_text(player.current)

    async def _edit_now(self, player, text=None):
        """Edit the existing panel / "now playing" text; False if there is none."""
        if player.panel is not None and text is None:
            return await self._refresh_panel(player)
        if player.now_message is None:
            return False
        text = text or self._now_text(player)
        if text == player.last_now_text:
            return True
        try:
            await self._client.edit_message(
                player.chat_id, player.now_message, text, link_preview=False
            )
        except Exception as error:
            if "not modified" in str(error).lower():
                return True
            player.now_message = None
            return False
        player.last_now_text = text
        return True

    def _icon(self, track):
        if track.radio:
            return "📻"
        return "🎬" if track.has_video else "🎵"

    def _playing_text(self, track):
        return self.strings("playing").format(
            icon=self._icon(track),
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
                track = await self._take_radio(player) if player.radio else None
            if track is None:
                finished = player.current
                player.current = None
                self._drop_track_file(finished, player)
                if player.radio_error:
                    with contextlib.suppress(Exception):
                        await self._client.send_message(
                            player.chat_id,
                            self.strings("radio_stopped").format(
                                utils.escape_html(player.radio_error[:300])
                            ),
                        )
                    player.radio_error = None
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
                    if player.radio:
                        # Only for now: the setting stays for the next session.
                        player.radio = False
                        player.radio_error = str(error) or type(error).__name__
                        self._drop_radio_next(player)
                continue
            if announce:
                await self._show_now(player)
            return track

    async def _leave(self, player):
        self._cancel_end_task(player)
        self._cancel_rejoin(player)
        self._stop_ticker(player)
        player.generation += 1
        if self._calls is not None:
            with contextlib.suppress(Exception):
                await self._calls.leave_call(player.chat_id)
        self._drop_files(player, everything=True)
        self._players.pop(player.chat_id, None)
        await self._close_panel(player)
        self._save_state()

    def _error_text(self, track, error):
        if isinstance(error, YouTubeAuthError):
            cookies = error.context.get("cookies")
            if cookies:
                return self.strings("yt_auth_cookies").format(
                    file=utils.escape_html(cookies), p=self.get_prefix()
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
            if await self._recover(player):
                return
            await self._advance(player)

    async def _recover(self, player):
        """Restart a track whose stream died long before its end."""
        track = player.current
        position = player.position
        if (
            player.loop
            or track.is_live
            or not track.duration
            or position >= float(track.duration) - EARLY_END
            or track.recoveries >= MAX_RECOVERIES
        ):
            return False
        track.recoveries += 1
        logger.info(
            "VoiceChat: stream ended at %s of %s, restarting",
            fmt_time(position), fmt_time(track.duration),
        )
        if not (track.path or track.stream_url):
            track.resolved_at = 0.0  # stream URLs may have expired
            track.fresh = True
        try:
            await self._start(player, track, offset=max(0, position - 2))
        except Exception as error:
            logger.warning("VoiceChat recovery failed: %s", error)
            return False
        return True

    async def _on_left_call(self, _, update):
        player = self._players.pop(update.chat_id, None)
        if player is not None:
            self._cancel_end_task(player)
            self._cancel_rejoin(player)
            self._stop_ticker(player)
            player.generation += 1
            self._drop_files(player, everything=True)
            await self._close_panel(player)
            self._save_state()

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
        self._schedule_rejoin(player, f"connection {state.lower()}")

    async def _health_loop(self):
        while True:
            await asyncio.sleep(HEALTH_INTERVAL)
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
                    logger.debug("VoiceChat health check failed", exc_info=True)

    async def _ensure_tg_connected(self):
        tg = self._tg  # None when Hikka's own client is shared (it reconnects)
        if tg is not None and not tg.is_connected():
            logger.warning("VoiceChat: Telegram connection lost, reconnecting")
            await tg.connect()

    async def _check_player(self, player):
        """Schedule a rejoin when the call vanished or the stream froze."""
        if self._calls is None or player.current is None:
            return
        if player.lock.locked() or self._rejoining(player):
            return
        now = time.monotonic()
        if now - player.switched_at < STALL_TIMEOUT:
            return
        active = await self._calls.calls
        if player.chat_id not in active:
            self._schedule_rejoin(player, "call dropped")
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
            self._schedule_rejoin(player, "stream stalled")

    def _rejoining(self, player):
        task = player.rejoin_task
        return task is not None and not task.done()

    def _schedule_rejoin(self, player, reason):
        if self._rejoining(player) and player.rejoin_task is not asyncio.current_task():
            return
        player.rejoin_task = asyncio.ensure_future(self._rejoin(player, reason))

    def _cancel_rejoin(self, player):
        task = player.rejoin_task
        player.rejoin_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _rejoin(self, player, reason):
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
                logger.warning("VoiceChat gave up reconnecting to %s", player.chat_id)
                await self._leave(player)
                with contextlib.suppress(Exception):
                    await self._client.send_message(
                        player.chat_id, self.strings("reconnect_failed")
                    )
                return
            player.reconnects += 1
            logger.warning(
                "VoiceChat: %s in %s, rejoining (attempt %d)",
                reason, player.chat_id, player.reconnects,
            )
            position = 0 if track.is_live else int(player.position)
            paused = player.paused
            self._cancel_end_task(player)
            with contextlib.suppress(Exception):
                await self._calls.leave_call(player.chat_id)
            with contextlib.suppress(Exception):
                await self._ensure_tg_connected()
            if not (track.path or track.stream_url):
                # Stream URLs may have expired while the call was down.
                track.resolved_at = 0.0
                track.fresh = True
            try:
                await self._prime_chat(player.chat_id)
                await self._start(player, track, offset=position)
            except Exception as error:
                logger.warning("VoiceChat rejoin failed: %s", error)
                self._schedule_rejoin(player, reason)
                return
            if paused:
                with contextlib.suppress(Exception):
                    await self._calls.pause(player.chat_id)
                    player.mark_paused()
        await self._update_panel(player)
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
        if track is None or not (track.path or track.stream_url):
            return
        if track is player.current or any(t is track for t in player.queue):
            return
        self._release(track)

    def _release(self, track):
        if track.stream_url:
            self._streamer.unregister(track.stream_url)
        if track.path:
            with contextlib.suppress(OSError):
                os.remove(track.path)

    def _drop_files(self, player, everything=False):
        tracks = list(player.queue)
        if player.current is not None:
            tracks.append(player.current)
        if everything:
            player.queue.clear()
            player.current = None
            self._drop_radio_next(player)
        elif player.radio_next is not None:
            tracks.append(player.radio_next)
        for track in tracks:
            self._release(track)

    # ------------------------------------------------------------------ load

    def _load(self):
        """CPU use in percent since the previous call; None without psutil."""
        try:
            import psutil
        except ImportError:
            return None
        me = self._process
        if me is None:
            me = self._process = psutil.Process()
        children = []
        with contextlib.suppress(Exception):
            children = [
                child for child in me.children(recursive=True)
                if "ffmpeg" in (child.name() or "")
            ]
        known = self._children
        ffmpeg = 0.0
        alive = {}
        for child in children:
            proc = known.get(child.pid, child)
            with contextlib.suppress(Exception):
                ffmpeg += proc.cpu_percent(None)
            alive[child.pid] = proc
        self._children = alive
        cores = psutil.cpu_count() or 1
        hikka = 0.0
        with contextlib.suppress(Exception):
            hikka = me.cpu_percent(None)
        load = (0.0, 0.0, 0.0)
        with contextlib.suppress(Exception):
            load = os.getloadavg()
        memory = None
        with contextlib.suppress(Exception):
            memory = psutil.virtual_memory()
        return {
            "system": psutil.cpu_percent(None),
            "cores": cores,
            "hikka": hikka / cores,
            "ffmpeg": ffmpeg / cores,
            # Both together in percent of one core, like top shows them.
            "own": hikka + ffmpeg,
            "processes": len(children),
            "load": load,
            "memory": memory,
        }

    async def _watch_load(self, player):
        """Step the picture down when the CPU cannot keep up."""
        if not self.config["auto_quality"]:
            return
        track = player.current
        if track is None or not track.has_video or player.paused:
            player.overload_hits = 0
            return
        sample = await asyncio.to_thread(self._load)
        if sample is None:
            return
        self._last_system = sample["system"]
        self._last_own = sample["own"]
        if not self._overloaded(sample):
            player.overload_hits = 0
            return
        player.overload_hits += 1
        cap = self._video_cap(track)
        if player.overload_hits < OVERLOAD_SAMPLES or cap == QUALITY_STEPS[-1]:
            return
        player.overload_hits = 0
        player.quality_drop += 1
        track.quality_drop = player.quality_drop
        logger.info(
            "VoiceChat: CPU at %.0f%% (module %.0f%% of a core), "
            "lowering video to %s",
            sample["system"], sample["own"], self._video_cap(track),
        )
        async with player.lock:
            if player.current is track:
                await self._start(player, track, offset=player.position)
        with contextlib.suppress(Exception):
            await self._client.send_message(
                player.chat_id,
                self.strings("quality_down").format(self._video_cap(track)),
            )

    def _cpu_limit(self):
        try:
            return max(0, int(self.config["cpu_limit"]))
        except (KeyError, TypeError, ValueError):
            return DEFAULT_CPU_LIMIT

    def _overloaded(self, sample):
        limit = self._cpu_limit()
        return sample["system"] >= OVERLOAD_PERCENT or bool(
            limit and sample.get("own", 0.0) >= limit
        )

    def _relax_quality(self, player, track):
        """A new track may go one step back up once the CPU has room."""
        limit = self._cpu_limit()
        own_calm = (
            not limit
            or self._last_own is None
            or self._last_own < limit * RELAX_PERCENT / OVERLOAD_PERCENT
        )
        if (
            player.quality_drop > 0
            and self._last_system is not None
            and self._last_system < RELAX_PERCENT
            and own_calm
        ):
            player.quality_drop -= 1
        track.quality_drop = player.quality_drop

    @loader.group_member
    @loader.command(ru_doc="Навантаження сервера й чи встигає відео")
    async def vcstats(self, message):
        """Навантаження процесора (Hikka, ffmpeg), пам'ять і поради щодо якості"""
        if not await self._allowed(message, "view"):
            return
        await asyncio.to_thread(self._load)  # prime the counters
        await asyncio.sleep(1.5)
        sample = await asyncio.to_thread(self._load)
        if sample is None:
            return await utils.answer(message, self.strings("stats_unavailable"))
        self._last_system = sample["system"]
        self._last_own = sample["own"]
        lines = []
        for player in self._players.values():
            if player.current is None:
                continue
            track = player.current
            lines.append(
                self.strings("stats_player").format(
                    title=self._title_html(track),
                    quality=self._quality_label(track),
                    level=(
                        self.strings("stats_level").format(track.quality_drop)
                        if track.quality_drop
                        else ""
                    ),
                )
            )
        memory = sample["memory"]
        memory_text = (
            f"{memory.used / 1073741824:.1f} / {memory.total / 1073741824:.1f} ГБ"
            if memory is not None
            else "—"
        )
        limit = self._cpu_limit()
        if self._overloaded(sample):
            verdict = self.strings("stats_over")
        elif sample["system"] >= 70:
            verdict = self.strings("stats_busy").format(p=self.get_prefix())
        else:
            verdict = self.strings("stats_ok")
        await utils.answer(
            message,
            self.strings("stats").format(
                system=round(sample["system"]),
                cores=sample["cores"],
                load=" ".join(f"{x:.2f}" for x in sample["load"]),
                hikka=round(sample["hikka"]),
                ffmpeg=round(sample["ffmpeg"]),
                processes=sample["processes"],
                own=round(sample["own"]),
                limit=self.strings("stats_limit").format(limit) if limit else "",
                memory=memory_text,
                players="\n".join(lines),
                verdict=verdict,
            ),
            link_preview=False,
        )

    # ---------------------------------------------------------------- access

    def _owner_id(self):
        return getattr(self._client, "tg_id", None)

    def _user_allowed(self, user_id, kind):
        """``kind``: view (look), queue (add tracks) or control (everything)."""
        if user_id is None or user_id == self._owner_id():
            return True
        trusted = {int(u) for u in (self.config["trusted_users"] or [])}
        if user_id in trusted:
            return True
        access = self.config["access"]
        if access == "all":
            return True
        if access == "queue":
            return kind in ("view", "queue")
        return False

    async def _allowed(self, message, kind):
        if getattr(message, "out", False):
            return True
        if self._user_allowed(getattr(message, "sender_id", None), kind):
            return True
        if self.config["access"] != "owner":
            # In owner-only mode the module stays invisible to others.
            with contextlib.suppress(Exception):
                await utils.answer(message, self.strings("no_access"))
        return False

    # -------------------------------------------------------------- prefetch

    def _schedule_prefetch(self, player):
        """Resolve and pre-buffer the next track while this one plays."""
        if not self.config["prefetch_next"]:
            return
        if not player.queue:
            task = player.radio_task
            if player.radio and player.radio_next is None and (
                task is None or task.done()
            ):
                player.radio_task = asyncio.ensure_future(self._prefetch_radio(player))
            return
        track = player.queue[0]
        if track.prefetch_task is not None or track.resolved:
            return
        track.prefetch_task = asyncio.ensure_future(self._prefetch_track(track))

    async def _prefetch_track(self, track):
        try:
            await self._resolve(track)
            cache = (
                self._streamer.cache(track.stream_url) if track.stream_url else None
            )
            if cache is not None:
                # Only the opening seconds: the playing track keeps the bandwidth.
                seconds = max(1, int(self.config["tg_prebuffer"]))
                rate = cache.size / float(track.duration) if track.duration else TG_CHUNK
                await cache.set_window(max(2, int(rate * seconds) // TG_CHUNK + 2))
                await self._prebuffer(track)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.info("VoiceChat prefetch of %s failed: %s", track.source, error)

    # ----------------------------------------------------------------- radio

    def _radio_channel(self, player):
        return parse_channel(player.radio_channel or self.config["radio_channel"])

    def _music_filter(self):
        """``InputMessagesFilterMusic`` of the library Hikka's client is on."""
        library = "telethon"
        if any(
            cls.__module__.startswith("hikkatl") for cls in type(self._client).__mro__
        ):
            library = "hikkatl"
        with contextlib.suppress(Exception):
            return importlib.import_module(f"{library}.tl.types").InputMessagesFilterMusic
        return None

    @staticmethod
    def _is_song(message):
        if message is None or getattr(message, "voice", None):
            return False
        file = getattr(message, "file", None)
        return file is not None and bool(
            getattr(message, "audio", None)
            or (getattr(file, "mime_type", "") or "").startswith("audio/")
        )

    async def _radio_peer(self, channel):
        """The channel as an input peer; a bare ``-100…`` ID the session has
        not seen yet is looked up among the account's dialogs."""
        try:
            return await self._client.get_input_entity(channel)
        except (ValueError, TypeError):
            if not isinstance(channel, int):
                raise
        async for dialog in self._client.iter_dialogs():
            if dialog.id == channel:
                return dialog.input_entity
        raise ValueError(f"channel {channel} not found among the account's chats")

    async def _radio_songs(self, channel):
        """Message ids of the channel's music, oldest first."""
        channel = await self._radio_peer(channel)
        music = self._music_filter()
        ids = []
        async for message in self._client.iter_messages(
            channel, limit=int(self.config["radio_limit"]), filter=music
        ):
            if self._is_song(message):
                ids.append(message.id)
        return sorted(ids)

    async def _radio_reload(self, player):
        """Read the channel again and lay out the next round."""
        channel = self._radio_channel(player)
        if channel is None:
            raise ValueError(self.strings("radio_no_channel").format(p=self.get_prefix()))
        ids = await self._radio_songs(channel)
        last = player.radio_last
        if self.config["radio_shuffle"]:
            random.shuffle(ids)
            if last in ids and len(ids) > 1:
                ids.remove(last)  # the song that just played goes last
                ids.append(last)
        elif last in ids:
            index = ids.index(last)  # carry on after it
            ids = ids[index + 1:] + ids[: index + 1]
        player.radio_order = ids
        player.radio_pos = 0
        return ids

    async def _radio_next(self, player):
        """The next song of the round as a Track (``None``: no music)."""
        channel = self._radio_channel(player)
        if channel is None:
            raise ValueError(self.strings("radio_no_channel").format(p=self.get_prefix()))
        channel = await self._radio_peer(channel)
        for _ in range(RADIO_SKIP):
            if player.radio_pos >= len(player.radio_order):
                if not await self._radio_reload(player):
                    return None
            message_id = player.radio_order[player.radio_pos]
            player.radio_pos += 1
            message = await self._client.get_messages(channel, ids=message_id)
            if not self._is_song(message):
                continue  # deleted or edited since the channel was read
            track = await self._media_track(message, audio_only=True)
            if track is not None:
                track.radio = True
                return track
        return None

    async def _take_radio(self, player):
        """The radio's next song for ``_advance``; ``None`` turns it off."""
        track, player.radio_next = player.radio_next, None
        task, player.radio_task = player.radio_task, None
        if track is None and task is not None and not task.done():
            await asyncio.wait([task])  # it is looking the next song up
            track, player.radio_next = player.radio_next, None
        try:
            if track is None:
                track = await self._radio_next(player)
            if track is None:
                raise LookupError(self.strings("radio_empty"))
        except Exception as error:
            logger.warning("VoiceChat radio in %s failed: %s", player.chat_id, error)
            player.radio = False
            player.radio_error = re.sub(r"<[^>]+>", "", str(error)) or type(
                error
            ).__name__
            return None
        if track.tg_ref:
            player.radio_last = track.tg_ref[1]
            self._remember(player)
        return track

    async def _prefetch_radio(self, player):
        try:
            track = await self._radio_next(player)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.info("VoiceChat radio prefetch failed: %s", error)
            return
        if track is None:
            return
        if not player.radio or self._players.get(player.chat_id) is not player:
            self._release(track)
            return
        player.radio_next = track
        await self._prefetch_track(track)

    def _drop_radio_next(self, player):
        task, player.radio_task = player.radio_task, None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        track, player.radio_next = player.radio_next, None
        if track is not None:
            self._release(track)

    async def _radio_begin(self, player, status=None):
        """Start the radio in a chat where nothing plays; False on failure."""
        async with player.lock:
            if player.current is not None:
                return True
            track = await self._advance(player, announce=False)
        if track is None:
            self._players.pop(player.chat_id, None)
            if status is not None:
                await self._say(player.chat_id, status, self.strings("radio_failed"))
            return False
        await self._show_now(player, status)
        return True

    # ------------------------------------------------------- panel & ticker

    def _panel_enabled(self):
        return bool(self.config["panel"]) and getattr(self, "inline", None) is not None

    def _panel_text(self, player):
        track = player.current
        position = player.position
        if track.duration and not track.is_live:
            filled = int(min(1.0, position / float(track.duration)) * 12)
            bar = "▰" * filled + "▱" * (12 - filled)
        else:
            bar = "🔴"
        text = self.strings("now").format(
            icon=self._icon(track),
            title=self._title_html(track),
            bar=bar,
            pos=fmt_time(position),
            duration=self._duration_label(track),
            state=self.strings("state_paused" if player.paused else "state_playing"),
            volume=player.volume,
            loop=self.strings("on" if player.loop else "off"),
            quality=self._quality_label(track),
            queued=len(player.queue),
        )
        text += self._download_line(track)
        if len(track.audio_streams) > 1:
            current = (
                self._audio_track_label(
                    track.audio_streams[track.audio_index], track.audio_index
                )
                if track.audio_index is not None
                else self.strings("atrack_auto")
            )
            text += "\n" + self.strings("atrack_set").format(utils.escape_html(current))
        if player.radio:
            text += self.strings("panel_radio")
        upcoming = player.queue[0] if player.queue else player.radio_next
        if upcoming is not None:
            text += self.strings("panel_next").format(self._title_html(upcoming))
        return text

    # ------------------------------------------------------------ rich panel

    def _bot_token(self):
        inline = getattr(self, "inline", None)
        return getattr(inline, "_token", None) or getattr(
            getattr(inline, "bot", None), "token", None
        )

    def _rich_wanted(self, player):
        return (
            self.config["panel_style"] == "rich"
            and not self._rich_broken
            and player.panel is not None
            and bool(getattr(player.panel, "inline_message_id", None))
            and bool(self._bot_token())
            and callable(getattr(self.inline, "generate_markup", None))
        )

    async def _bot_api(self, method, payload):
        """Call the Bot API directly (Hikka's aiogram may not know the method)."""
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.post(
                BOT_API.format(token=self._bot_token(), method=method), json=payload
            ) as response:
                return await response.json(content_type=None)

    def _rich_callbacks(self, player, markup):
        """callback_data for each panel button, registered with Hikka once."""
        fresh = []
        for row in markup:
            for button in row:
                action = button["args"][1]
                button["disable_security"] = True  # _panel_action checks access
                if action in player.rich_data:
                    button["_callback_data"] = player.rich_data[action]
                else:
                    fresh.append(button)
        if fresh:
            self.inline.generate_markup([fresh])
            for button in fresh:
                player.rich_data[button["args"][1]] = button["_callback_data"]

    def _panel_rich(self, player, markup):
        """The panel as a Bot API ``InputRichMessage``."""
        track = player.current
        position = player.position

        def cell(text, header=False):
            result = {"align": "left", "valign": "middle", "text": text}
            if header:
                result["is_header"] = True
            return result

        title = str(track.title)[:200]
        if track.webpage:
            title = {"type": "url", "text": title, "url": track.webpage}
        if track.duration and not track.is_live:
            filled = int(min(1.0, position / float(track.duration)) * 12)
            bar = "▰" * filled + "▱" * (12 - filled)
        else:
            bar = "🔴"
        rows = [
            (
                self.strings("rich_state"),
                self.strings("state_paused" if player.paused else "state_playing"),
            ),
            (self.strings("rich_volume"), f"{player.volume}%"),
            (self.strings("rich_loop"), self.strings("on" if player.loop else "off")),
            (self.strings("rich_quality"), html.unescape(self._quality_label(track))),
            (self.strings("rich_queue"), str(len(player.queue))),
        ]
        if len(track.audio_streams) > 1:
            current = (
                self._audio_track_label(
                    track.audio_streams[track.audio_index], track.audio_index
                )
                if track.audio_index is not None
                else self.strings("atrack_auto")
            )
            rows.append((self.strings("rich_atrack"), current))
        cache = self._streamer.cache(track.stream_url) if track.stream_url else None
        if cache is not None and not cache.complete:
            rows.append(
                (
                    self.strings("rich_download"),
                    f"{int(cache.progress * 100)}% · "
                    f"{cache.speed / 1048576:.1f} МБ/с",
                )
            )
        if player.radio:
            rows.append((self.strings("rich_radio"), self.strings("rich_radio_on")))
        blocks = [
            {"type": "heading", "text": [f"{self._icon(track)} ", title], "size": 3},
            {
                "type": "paragraph",
                "text": [
                    f"{bar}  ",
                    {
                        "type": "bold",
                        "text": f"{fmt_time(position)} / {self._duration_label(track)}",
                    },
                ],
            },
            {
                "type": "table",
                "cells": [[cell(name, header=True), cell(value)] for name, value in rows],
                "is_bordered": True,
                "is_compact": True,
            },
        ]
        upcoming = player.queue[0] if player.queue else player.radio_next
        if upcoming is not None:
            blocks.append(
                {
                    "type": "blockquote",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": [
                                self.strings("rich_next"),
                                {"type": "bold", "text": str(upcoming.title)[:200]},
                            ],
                        }
                    ],
                }
            )
        for row in markup:
            buttons = []
            for button in row:
                action = button["args"][1]
                rich = {"text": button["text"], "callback_data": button["_callback_data"]}
                style = RICH_STYLES.get(action)
                if action == "loop" and player.loop:
                    style = "success"
                if style:
                    rich["style"] = style
                buttons.append(rich)
            blocks.append({"type": "buttons", "buttons": buttons, "align": "center"})
        blocks.append(
            {
                "type": "footer",
                "text": self.strings("rich_footer").format(int(PANEL_REFRESH)),
            }
        )
        return {"blocks": blocks, "skip_entity_detection": True}

    async def _edit_rich(self, player):
        """Show the panel as a rich message: True / False like Hikka's edit(),
        ``None`` when Telegram refuses rich messages (the HTML panel takes over)."""
        markup = self._panel_markup(player)
        try:
            self._rich_callbacks(player, markup)
            payload = {
                "inline_message_id": str(player.panel.inline_message_id),
                "rich_message": self._panel_rich(player, markup),
                "reply_markup": {"inline_keyboard": []},
            }
            data = await self._bot_api("editMessageText", payload)
        except Exception as error:
            logger.debug("VoiceChat rich panel edit failed: %s", error)
            return False
        if isinstance(data, dict) and data.get("ok"):
            return True
        description = str((data or {}).get("description") if isinstance(data, dict) else data)
        if "not modified" in description.lower():
            return True
        logger.warning(
            "VoiceChat: Telegram refused the rich panel (%s), using the HTML one",
            description,
        )
        self._rich_broken = True
        return None

    def _panel_markup(self, player):
        chat_id = player.chat_id

        def button(key, action):
            return {
                "text": self.strings(key),
                "callback": self._panel_action,
                "args": (chat_id, action),
            }

        rows = [
            [
                button("btn_back", "back"),
                button("btn_play" if player.paused else "btn_pause", "toggle"),
                button("btn_fwd", "fwd"),
            ],
            [
                button("btn_voldown", "voldown"),
                button("btn_skip", "skip"),
                button("btn_volup", "volup"),
            ],
            [button("btn_loop", "loop"), button("btn_queue", "queue")],
        ]
        if player.current is not None and len(player.current.audio_streams) > 1:
            rows[2].insert(1, button("btn_atrack", "atrack"))
        rows[2].append(button("btn_stop", "stop"))
        return rows

    async def _show_panel(self, player, status=None):
        """Post a fresh control panel (replacing ``status`` if given)."""
        if player.current is None or self._inline_broken:
            return False
        if time.monotonic() < self._panel_retry_at:
            return False
        old = player.panel
        text = self._panel_text(player)
        posted_at = time.time()
        task = asyncio.ensure_future(
            self.inline.form(
                text,
                message=status if status is not None else player.chat_id,
                reply_markup=self._panel_markup(player),
                disable_security=True,
                silent=True,
            )
        )
        try:
            panel = await asyncio.wait_for(task, PANEL_TIMEOUT)
        except asyncio.TimeoutError:
            await self._delete_stray_panel(player.chat_id, posted_at)
            await self._inline_failed(player, "no chosen_inline_result")
            return False
        except Exception as error:
            logger.info("VoiceChat panel unavailable: %s", error)
            self._panel_retry_at = time.monotonic() + PANEL_RETRY
            return False
        if not panel:
            logger.info("VoiceChat: Hikka could not post the panel, using text")
            self._panel_retry_at = time.monotonic() + PANEL_RETRY
            await self._delete_invoke_error(player.chat_id, posted_at, status)
            return False
        player.panel = panel
        player.last_panel_text = text
        player.panel_failures = 0
        if self._rich_wanted(player):
            player.last_panel_text = None
            await self._refresh_panel(player)  # turn it into the rich panel
        if old is not None and old is not panel:
            with contextlib.suppress(Exception):
                await old.delete()
        if player.now_message is not None:
            # The text panel that stood in for it.
            with contextlib.suppress(Exception):
                await self._client.delete_messages(player.chat_id, player.now_message)
            player.now_message = None
        return True

    async def _delete_stray_panel(self, chat_id, since):
        """Remove the panel Hikka posted but never handed over."""
        bot_id = getattr(self.inline, "bot_id", None)
        if not bot_id:
            return
        with contextlib.suppress(Exception):
            async for message in self._client.iter_messages(chat_id, limit=15):
                date = getattr(message, "date", None)
                if date is not None and date.timestamp() < since - 10:
                    break
                if message.out and getattr(message, "via_bot_id", None) == bot_id:
                    await message.delete()

    def _invoke_error_marks(self):
        """Texts that identify Hikka's "unit invoke failed" message."""
        marks = ["inline/form.py", "_invoke_unit"]
        translator = getattr(self.inline, "translator", None)
        for key in ("inline.invoke_failed", "inline.invoke_failed_logs"):
            try:
                text = translator.getkey(key)
            except Exception:
                continue
            if isinstance(text, str):
                first = re.sub(r"<[^>]+>", "", text).split("\n")[0].strip()
                if len(first) > 3:
                    marks.append(first)
        return marks

    async def _delete_invoke_error(self, chat_id, since, status=None):
        """Remove the traceback Hikka posted when the panel failed.

        ``status`` is skipped: Hikka edits it into the error when it is the
        account's own message, and the text panel replaces it next.
        """
        keep = getattr(status, "id", None)
        marks = self._invoke_error_marks()
        with contextlib.suppress(Exception):
            async for message in self._client.iter_messages(chat_id, limit=15):
                date = getattr(message, "date", None)
                if date is not None and date.timestamp() < since - 10:
                    break
                if not message.out or message.id == keep:
                    continue
                text = getattr(message, "raw_text", None) or ""
                if any(mark in text for mark in marks):
                    await message.delete()

    async def _inline_failed(self, player, reason):
        """The inline bot cannot keep a panel: use text for this session."""
        logger.warning(
            "VoiceChat: the inline panel cannot be edited (%s), using a text panel",
            reason,
        )
        self._inline_broken = True
        panel, player.panel = player.panel, None
        player.last_panel_text = None
        if panel is not None:
            with contextlib.suppress(Exception):
                await panel.delete()
        if not self._inline_hint_sent:
            self._inline_hint_sent = True
            bot = getattr(self.inline, "bot_username", None)
            with contextlib.suppress(Exception):
                await self._client.send_message(
                    player.chat_id,
                    self.strings("inline_broken").format(
                        bot=f"@{bot}" if bot else self.strings("inline_your_bot"),
                        p=self.get_prefix(),
                    ),
                )

    async def _update_panel(self, player):
        """Bring the panel, inline or text, up to date (ticker, buttons)."""
        if player.current is None:
            return
        if player.panel is not None:
            if await self._refresh_panel(player) or player.panel is not None:
                return
            await self._show_now(player)  # the inline panel was given up
        elif player.now_message is not None and self._panel_enabled():
            await self._edit_now(player)

    async def _refresh_panel(self, player):
        """Edit the panel in place; False when there is none or it failed."""
        if player.panel is None or player.current is None:
            return False
        text = self._panel_text(player)
        if text == player.last_panel_text:
            return True
        result = await self._edit_rich(player) if self._rich_wanted(player) else None
        if result is None:  # HTML panel
            try:
                result = await player.panel.edit(
                    text, reply_markup=self._panel_markup(player)
                )
            except Exception as error:
                logger.debug("VoiceChat panel refresh failed: %s", error)
                result = False
        if result is False:  # Hikka returns False instead of raising
            player.panel_failures += 1
            if player.panel_failures >= PANEL_FAILURES:
                # Callers that only refresh post the text panel instead.
                await self._inline_failed(player, "edits keep failing")
            return False
        player.panel_failures = 0
        player.last_panel_text = text
        return True

    async def _close_panel(self, player):
        panel, player.panel = player.panel, None
        if panel is not None:
            with contextlib.suppress(Exception):
                await panel.edit(self.strings("stopped"), reply_markup=[])
        if player.now_message is not None:
            await self._edit_now(player, self.strings("stopped"))
            player.now_message = None

    def _start_ticker(self, player):
        if player.ticker is None or player.ticker.done():
            player.ticker = asyncio.ensure_future(self._ticker(player))

    def _stop_ticker(self, player):
        task, player.ticker = player.ticker, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _ticker(self, player):
        """Keep the panel's progress bar and the saved position current."""
        while player.current is not None and self._players.get(player.chat_id) is player:
            await asyncio.sleep(PANEL_REFRESH)
            with contextlib.suppress(Exception):
                await self._watch_load(player)
            await self._update_panel(player)
            self._save_state(periodic=True)

    async def _panel_action(self, call, chat_id, action):
        user = getattr(getattr(call, "from_user", None), "id", None)
        kind = "view" if action == "queue" else "control"

        async def toast(text, alert=False):
            with contextlib.suppress(Exception):
                await call.answer(text, show_alert=alert)

        if not self._user_allowed(user, kind):
            return await toast(self.strings("toast_no_access"), True)
        player = self._players.get(chat_id)
        if player is None or player.current is None or self._calls is None:
            return await toast(self.strings("toast_ended"))
        if action == "queue":
            return await toast(self._queue_plain(player)[:190], True)
        try:
            text = await self._act(player, action)
        except Exception as error:
            logger.warning("VoiceChat panel action %s failed: %s", action, error)
            text = f"❌ {str(error)[:150] or type(error).__name__}"
        await toast(text or "")
        if self._players.get(chat_id) is player:
            await self._update_panel(player)

    async def _act(self, player, action, value=None):
        """Shared by commands and panel buttons; returns a short toast."""
        track = player.current
        if action in ("toggle", "pause", "resume"):
            pause = action == "pause" or (action == "toggle" and not player.paused)
            if pause and not player.paused:
                await self._calls.pause(player.chat_id)
                player.mark_paused()
                return self.strings("toast_paused")
            if not pause and player.paused:
                await self._calls.resume(player.chat_id)
                player.mark_resumed()
                return self.strings("toast_resumed")
            return None
        if action in ("back", "fwd", "seek"):
            if track.is_live:
                return self.strings("seek_live")
            if action == "seek":
                target = value
            else:
                step = -SEEK_STEP if action == "back" else SEEK_STEP
                target = parse_seek(f"{step:+d}", player.position, track.duration)
            async with player.lock:
                await self._start(player, track, offset=target)
            return self.strings("toast_seek").format(fmt_time(target))
        if action in ("voldown", "volup", "volume"):
            if action == "volume":
                volume = value
            else:
                step = -VOLUME_STEP if action == "voldown" else VOLUME_STEP
                volume = max(1, min(200, player.volume + step))
            await self._calls.change_volume_call(player.chat_id, int(volume))
            player.volume = int(volume)
            self._remember(player)
            self._save_state()
            return self.strings("toast_volume").format(player.volume)
        if action == "loop":
            player.loop = not player.loop
            self._save_state()
            return self.strings("toast_loop_on" if player.loop else "toast_loop_off")
        if action == "skip":
            async with player.lock:
                player.loop = False
                await self._advance(player, announce=False, force_leave=True)
            return self.strings("toast_skipped")
        if action == "stop":
            player.radio = False
            self._store_settings(player.chat_id, radio=False)
            async with player.lock:
                await self._leave(player)
            return self.strings("toast_stopped")
        if action in ("atrack", "atrack_set"):
            streams = track.audio_streams
            if len(streams) < 2:
                return self.strings("atrack_none")
            if action == "atrack":
                current = -1 if track.audio_index is None else track.audio_index
                value = (current + 1) % len(streams)
            track.audio_index = value
            async with player.lock:
                await self._start(player, track, offset=player.position)
            return self.strings("toast_atrack").format(
                self._audio_track_label(streams[value], value)
            )
        return None

    def _queue_plain(self, player):
        lines = []
        for index, track in enumerate(list(player.queue)[:10], 1):
            lines.append(f"{index}. {str(track.title)[:40]}")
        return "\n".join(lines) or self.strings("queue_empty").replace(
            "<b>", ""
        ).replace("</b>", "")

    # ----------------------------------------------------------- persistence

    def _track_dict(self, track):
        if track is None:
            return None
        if track.stream_url and track.tg_ref:
            source = None
        elif track.path:
            return None  # downloaded files are gone after a restart
        else:
            source = track.source
        return {
            "source": source,
            "tg": list(track.tg_ref) if track.tg_ref else None,
            "title": str(track.title),
            "audio_only": track.audio_only,
            "duration": track.duration,
            "audio_index": track.audio_index,
            "radio": track.radio,
        }

    def _save_state(self, periodic=False):
        """Persist the queues; ``periodic`` saves are limited to SAVE_EVERY."""
        now = time.monotonic()
        if periodic and now - self._saved_at < SAVE_EVERY:
            return
        self._saved_at = now
        data = {}
        for chat_id, player in self._players.items():
            if player.current is None and not player.queue:
                continue
            data[str(chat_id)] = {
                "current": self._track_dict(player.current),
                "position": round(player.position, 1),
                "queue": [d for d in map(self._track_dict, player.queue) if d],
                "loop": player.loop,
                "volume": player.volume,
                "message": player.now_message,
                "saved": time.time(),
            }
        with contextlib.suppress(Exception):
            self.set("state", data)

    async def _track_from_dict(self, data):
        if data.get("tg"):
            chat_id, message_id = data["tg"]
            message = await self._client.get_messages(chat_id, ids=message_id)
            if message is None or not getattr(message, "file", None):
                return None
            file = message.file
            url = await self._streamer.register(
                self._client, message, file.size, file.mime_type, file.ext or ""
            )
            track = Track(url, title=data.get("title"))
            track.stream_url = url
            track.tg_ref = (chat_id, message_id)
            track.webpage = None
        elif data.get("source"):
            track = Track(data["source"], title=data.get("title"))
        else:
            return None
        track.audio_only = bool(data.get("audio_only"))
        track.duration = data.get("duration")
        track.audio_index = data.get("audio_index")
        track.radio = bool(data.get("radio"))
        return track

    async def _restore_all(self):
        """Pick up the queues that were playing before Hikka restarted."""
        await asyncio.sleep(RESTORE_DELAY)
        try:
            saved = self.get("state", {}) or {}
        except Exception:
            return
        if not self.config["resume_after_restart"]:
            return
        for chat_key, data in saved.items():
            if time.time() - float(data.get("saved") or 0) > RESTORE_MAX_AGE:
                continue
            with contextlib.suppress(Exception):
                await self._restore_chat(int(chat_key), data)
        # The radio was on, but nothing of it was left to resume: start anew.
        with contextlib.suppress(Exception):
            settings = self.get("chat_settings", {}) or {}
            for chat_key, values in list(settings.items()):
                chat_id = int(chat_key)
                if (values or {}).get("radio") and chat_id not in self._players:
                    with contextlib.suppress(Exception):
                        await self._restore_radio(chat_id)
        self._save_state()

    async def _restore_radio(self, chat_id):
        if await self._prepare_chat(chat_id):
            return
        player = self._player(chat_id)
        if not player.radio:
            return
        status = None
        with contextlib.suppress(Exception):
            status = await self._client.send_message(
                chat_id, self.strings("restoring")
            )
        await self._radio_begin(player, status)

    async def _restore_chat(self, chat_id, data):
        tracks = []
        for item in [data.get("current")] + list(data.get("queue") or []):
            if item:
                with contextlib.suppress(Exception):
                    track = await self._track_from_dict(item)
                    if track is not None:
                        tracks.append(track)
        if not tracks:
            return
        if await self._prepare_chat(chat_id):
            return
        player = self._player(chat_id)
        if isinstance(data.get("message"), int):
            player.now_message = data["message"]
        status = None
        if not await self._edit_now(player, self.strings("restoring")):
            with contextlib.suppress(Exception):
                status = await self._client.send_message(
                    chat_id, self.strings("restoring")
                )
        player.volume = int(data.get("volume") or player.volume)
        first, rest = tracks[0], tracks[1:]
        position = float(data.get("position") or 0) if data.get("current") else 0
        async with player.lock:
            player.queue.extend(rest)
            player.loop = bool(data.get("loop"))
            try:
                await self._start(player, first, offset=max(0, position - 3))
            except Exception as error:
                logger.warning("VoiceChat could not resume %s: %s", chat_id, error)
                await self._advance(player)
                return
        await self._show_now(player, status)

    # -------------------------------------------------------------- commands

    async def _play_command(self, message, now, audio_only=False):
        if not await self._allowed(message, "control" if now else "queue"):
            return
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
        await self._enqueue(chat_id, tracks, now, status)

    async def _enqueue(self, chat_id, tracks, now, status=None):
        """Queue ``tracks`` or start the first one; report in ``status``."""
        player = self._player(chat_id)
        async with player.lock:
            if not now and player.current is not None:
                player.queue.extend(tracks)
                self._schedule_prefetch(player)
                self._save_state()
                if len(tracks) == 1:
                    text = self.strings("queued").format(
                        pos=len(player.queue), title=self._title_html(tracks[0])
                    )
                else:
                    text = self.strings("queued_many").format(count=len(tracks))
                await self._say(chat_id, status, text)
                await self._update_panel(player)
                return

            first, rest = tracks[0], tracks[1:]
            if now:
                player.queue.extendleft(reversed(rest))
            else:
                player.queue.extend(rest)
            if status is not None:
                status = await utils.answer(status, self.strings("resolving"))
            player.loop = False
            try:
                await self._start(player, first, status=status)
            except Exception as error:
                logger.exception("VoiceChat could not start playback")
                self._drop_track_file(first, player)
                if player.current is None and not player.queue:
                    self._players.pop(chat_id, None)
                await self._say(chat_id, status, self._error_text(first, error))
                return
            text = self._playing_text(first)
            if rest:
                text += "\n" + self.strings("queued_many").format(count=len(rest))
            await self._show_now(player, status, note=text)

    async def _say(self, chat_id, status, text):
        """Edit the status message, or post to the chat when there is none."""
        with contextlib.suppress(Exception):
            if status is not None:
                return await utils.answer(status, text, link_preview=False)
            return await self._client.send_message(
                chat_id, text, link_preview=False
            )

    @loader.group_member
    @loader.command(
        ru_doc="<посилання | запит | відповідь на медіа> [-a] — грати зараз"
    )
    async def vcplay(self, message):
        """<посилання | запит | відповідь на медіа> [-a] — увімкнути зараз (замінює поточне). -a — лише звук"""
        await self._play_command(message, now=True)

    @loader.group_member
    @loader.command(ru_doc="<посилання | запит | відповідь на медіа> [-a] — у чергу")
    async def vcadd(self, message):
        """<посилання | запит | відповідь на медіа> [-a] — додати в чергу (або грати, якщо тиша)"""
        await self._play_command(message, now=False)

    @loader.group_member
    @loader.command(ru_doc="<посилання | запит> — грати лише звук")
    async def vcaudio(self, message):
        """<посилання | запит | відповідь на медіа> — увімкнути лише звук"""
        await self._play_command(message, now=True, audio_only=True)

    async def _active(self, message, kind="control"):
        if not await self._allowed(message, kind):
            return None
        player = self._players.get(_chat_id(message))
        if player is None or player.current is None or self._calls is None:
            await utils.answer(message, self.strings("not_playing"))
            return None
        return player

    @loader.group_member
    @loader.command(ru_doc="Пауза")
    async def vcpause(self, message):
        """Поставити відтворення на паузу"""
        player = await self._active(message)
        if player is None:
            return
        if player.paused:
            return await utils.answer(message, self.strings("already_paused"))
        await self._act(player, "pause")
        await utils.answer(message, self.strings("paused").format(self.get_prefix()))
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="Продовжити")
    async def vcresume(self, message):
        """Продовжити відтворення після паузи"""
        player = await self._active(message)
        if player is None:
            return
        if not player.paused:
            return await utils.answer(message, self.strings("not_paused"))
        await self._act(player, "resume")
        await utils.answer(message, self.strings("resumed"))
        await self._update_panel(player)

    @loader.group_member
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
        await self._show_now(
            player,
            message,
            note=self.strings("skipped_to").format(self._title_html(track)),
        )

    @loader.group_member
    @loader.command(ru_doc="Зупинити й вийти з дзвінка")
    async def vcstop(self, message):
        """Зупинити відтворення, очистити чергу й вийти з дзвінка"""
        if not await self._allowed(message, "control"):
            return
        chat_id = _chat_id(message)
        self._store_settings(chat_id, radio=False)  # a stop ends the radio too
        player = self._players.get(chat_id)
        if player is not None:
            player.radio = False
        if player is None or self._calls is None:
            if self._calls is not None:
                with contextlib.suppress(Exception):
                    await self._calls.leave_call(chat_id)
            return await utils.answer(message, self.strings("stopped"))
        async with player.lock:
            await self._leave(player)
        await utils.answer(message, self.strings("stopped"))

    @loader.group_member
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
        try:
            await self._act(player, "seek", target)
        except Exception as error:
            return await utils.answer(message, self._error_text(track, error))
        await utils.answer(message, self.strings("seeked").format(fmt_time(target)))
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="<1-200> — гучність")
    async def vcvol(self, message):
        """<1-200> — гучність бота в дзвінку"""
        player = await self._active(message)
        if player is None:
            return
        value = (utils.get_args_raw(message) or "").strip().rstrip("%")
        if not value.isdigit() or not 1 <= int(value) <= 200:
            return await utils.answer(message, self.strings("volume_usage"))
        await self._act(player, "volume", int(value))
        await utils.answer(message, self.strings("volume").format(value))
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="Повтор поточного треку")
    async def vcloop(self, message):
        """Увімкнути / вимкнути повтор поточного треку"""
        player = await self._active(message)
        if player is None:
            return
        await self._act(player, "loop")
        await utils.answer(
            message, self.strings("loop_on" if player.loop else "loop_off")
        )
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="[N] — звукова доріжка (мова озвучки)")
    async def vcaudiotrack(self, message):
        """[N] — показати звукові доріжки поточного треку або обрати N-ту"""
        player = await self._active(message)
        if player is None:
            return
        track = player.current
        streams = track.audio_streams
        if len(streams) < 2:
            return await utils.answer(message, self.strings("atrack_none"))
        args = (utils.get_args_raw(message) or "").strip()
        if not args:
            current = (
                self._audio_track_label(streams[track.audio_index], track.audio_index)
                if track.audio_index is not None
                else self.strings("atrack_auto")
            )
            listing = "\n".join(
                utils.escape_html(self._audio_track_label(stream, index))
                for index, stream in enumerate(streams)
            )
            return await utils.answer(
                message,
                self.strings("atrack_list").format(
                    current=utils.escape_html(current),
                    tracks=listing,
                    p=self.get_prefix(),
                ),
            )
        if not args.isdigit() or not 1 <= int(args) <= len(streams):
            return await utils.answer(message, self.strings("atrack_bad"))
        index = int(args) - 1
        try:
            await self._act(player, "atrack_set", index)
        except Exception as error:
            return await utils.answer(message, self._error_text(track, error))
        await utils.answer(
            message,
            self.strings("atrack_set").format(
                utils.escape_html(self._audio_track_label(streams[index], index))
            ),
        )
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="Черга")
    async def vcqueue(self, message):
        """Показати чергу"""
        if not await self._allowed(message, "view"):
            return
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
        if player.radio_next is not None:
            lines.append(
                self.strings("radio_next").format(self._title_html(player.radio_next))
            )
        await utils.answer(message, "\n".join(lines), link_preview=False)

    def _queue_index(self, player, text):
        text = (text or "").strip()
        if not text.isdigit() or not 1 <= int(text) <= len(player.queue):
            return None
        return int(text) - 1

    @loader.group_member
    @loader.command(ru_doc="<N> — прибрати трек із черги")
    async def vcremove(self, message):
        """<N> — прибрати N-й трек із черги"""
        if not await self._allowed(message, "control"):
            return
        player = self._players.get(_chat_id(message))
        index = (
            self._queue_index(player, utils.get_args_raw(message)) if player else None
        )
        if index is None:
            return await utils.answer(
                message, self.strings("pos_bad").format(self.get_prefix())
            )
        async with player.lock:
            track = player.queue[index]
            del player.queue[index]
            self._drop_track_file(track, player)
            self._schedule_prefetch(player)
            self._save_state()
        await utils.answer(
            message, self.strings("removed").format(self._title_html(track)),
            link_preview=False,
        )
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="<N> <M> — пересунути трек у черзі")
    async def vcmove(self, message):
        """<N> <M> — пересунути N-й трек черги на місце M"""
        if not await self._allowed(message, "control"):
            return
        player = self._players.get(_chat_id(message))
        parts = (utils.get_args_raw(message) or "").split()
        source = target = None
        if player is not None and len(parts) == 2:
            source = self._queue_index(player, parts[0])
            target = self._queue_index(player, parts[1])
        if source is None or target is None:
            return await utils.answer(
                message, self.strings("pos_bad").format(self.get_prefix())
            )
        async with player.lock:
            track = player.queue[source]
            del player.queue[source]
            player.queue.insert(target, track)
            self._schedule_prefetch(player)
            self._save_state()
        await utils.answer(
            message,
            self.strings("moved").format(pos=target + 1, title=self._title_html(track)),
            link_preview=False,
        )
        await self._update_panel(player)

    @loader.group_member
    @loader.command(ru_doc="Очистити чергу")
    async def vcclear(self, message):
        """Очистити чергу (поточний трек грає далі)"""
        if not await self._allowed(message, "control"):
            return
        player = self._players.get(_chat_id(message))
        count = 0
        if player is not None:
            async with player.lock:
                count = len(player.queue)
                tracks = list(player.queue)
                player.queue.clear()
                for track in tracks:
                    self._drop_track_file(track, player)
                self._save_state()
        await utils.answer(message, self.strings("cleared").format(count))

    @loader.group_member
    @loader.command(ru_doc="[on [канал] | off | reload] — радіо з музикою каналу")
    async def vcradio(self, message):
        """[on [канал] | off | reload] — радіо: музика з каналу по колу"""
        if not await self._allowed(message, "control"):
            return
        chat_id = _chat_id(message)
        words = (utils.get_args_raw(message) or "").split(maxsplit=1)
        action = words[0].lower() if words else ""
        player = self._players.get(chat_id)

        if not action:
            settings = self._settings(chat_id)
            radio = player.radio if player else bool(settings.get("radio"))
            channel = (
                player.radio_channel if player else settings.get("radio_channel")
            ) or self.config["radio_channel"]
            count = len(player.radio_order) if player else 0
            return await utils.answer(
                message,
                self.strings("radio_status").format(
                    state=self.strings("on" if radio else "off"),
                    channel=(
                        f"<code>{utils.escape_html(channel)}</code>"
                        if channel
                        else self.strings("unknown")
                    ),
                    order=self.strings(
                        "radio_shuffled"
                        if self.config["radio_shuffle"]
                        else "radio_in_order"
                    ),
                    count=self.strings("radio_count").format(count) if count else "",
                    p=self.get_prefix(),
                ),
            )

        if action == "off":
            self._store_settings(chat_id, radio=False)
            if player is not None:
                player.radio = False
                self._drop_radio_next(player)
                await self._update_panel(player)
            return await utils.answer(message, self.strings("radio_off"))

        if action not in ("on", "reload"):
            return await utils.answer(message, self.strings("radio_usage"))
        chat_id = await self._prepare(message)
        if chat_id is None:
            return
        player = self._player(chat_id)
        idle = player.current is None

        previous_channel = player.radio_channel

        def forget():
            player.radio_channel = previous_channel
            if idle and player.current is None and not player.queue:
                self._players.pop(chat_id, None)

        if action == "on" and len(words) > 1:
            player.radio_channel = words[1].strip()
        if self._radio_channel(player) is None:
            forget()
            return await utils.answer(
                message, self.strings("radio_no_channel").format(p=self.get_prefix())
            )
        status = await utils.answer(message, self.strings("radio_loading"))
        try:
            songs = await self._radio_reload(player)
        except Exception as error:
            forget()
            return await utils.answer(
                status,
                self.strings("radio_bad").format(
                    utils.escape_html(str(error)[:300] or type(error).__name__)
                ),
            )
        if not songs:
            forget()
            return await utils.answer(status, self.strings("radio_empty"))
        self._drop_radio_next(player)  # it came from the old round
        if action == "reload":
            if idle:
                forget()
            else:
                self._schedule_prefetch(player)
            return await utils.answer(
                status, self.strings("radio_reloaded").format(count=len(songs))
            )

        player.radio = True
        self._remember(player)
        self._store_settings(chat_id, radio=True)
        if not idle:
            self._schedule_prefetch(player)
            await self._update_panel(player)
            return await utils.answer(
                status, self.strings("radio_on_later").format(count=len(songs))
            )
        await self._radio_begin(player, status)

    @loader.group_member
    @loader.command(ru_doc="Що зараз грає (панель керування)")
    async def vcnow(self, message):
        """Що зараз грає: позиція, якість, гучність; панель з кнопками"""
        player = await self._active(message, "view")
        if player is None:
            return
        self._inline_broken = False  # e.g. inline feedback was switched on
        self._panel_retry_at = 0.0
        if self._panel_enabled() and await self._show_panel(player, message):
            return
        if self._panel_enabled() and player.now_message is not None:
            with contextlib.suppress(Exception):
                await self._client.delete_messages(player.chat_id, player.now_message)
            player.now_message = None
            await self._show_now(player, message)
            return
        await utils.answer(message, self._panel_text(player), link_preview=False)

    def _search_sync(self, query, count):
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
        results = []
        for entry in (info or {}).get("entries") or []:
            if not entry:
                continue
            link = entry.get("webpage_url") or entry.get("url") or ""
            if link and not URL_RE.match(link):
                link = f"https://www.youtube.com/watch?v={link}"
            if link:
                results.append(
                    {
                        "url": link,
                        "title": entry.get("title") or link,
                        "duration": entry.get("duration"),
                        "channel": entry.get("channel") or entry.get("uploader"),
                    }
                )
        return results

    @loader.group_member
    @loader.command(ru_doc="<запит> [-a] — пошук на YouTube з вибором")
    async def vcsearch(self, message):
        """<запит> [-a] — знайти на YouTube і обрати, що грати (-a — лише звук)"""
        if not await self._allowed(message, "queue"):
            return
        query = (utils.get_args_raw(message) or "").strip()
        audio_only = False
        if query.split()[:1] and query.split()[0].lower() in ("-a", "--audio"):
            audio_only = True
            query = query.split(None, 1)[1] if len(query.split()) > 1 else ""
        if not query:
            return await utils.answer(
                message, self.strings("search_usage").format(self.get_prefix())
            )
        error = self._check_dependencies()
        if error:
            return await utils.answer(message, error)
        status = await utils.answer(message, self.strings("searching"))
        try:
            results = await asyncio.to_thread(self._search_sync, query, SEARCH_RESULTS)
        except Exception as error:
            return await utils.answer(
                status,
                self.strings("play_failed").format(
                    title=utils.escape_html(query),
                    error=utils.escape_html(str(error)[:300]),
                ),
            )
        if not results:
            return await utils.answer(status, self.strings("search_none"))
        chat_id = _chat_id(message)
        head = self.strings("search_head").format(utils.escape_html(query))

        def label(result):
            duration = f" · {fmt_time(result['duration'])}" if result["duration"] else ""
            return f"{str(result['title'])[:50]}{duration}"

        if getattr(self, "inline", None) is not None:
            buttons = [
                [
                    {
                        "text": f"{index}. {label(result)}",
                        "callback": self._search_pick,
                        "args": (chat_id, result, audio_only),
                    }
                ]
                for index, result in enumerate(results, 1)
            ]
            buttons.append(
                [{"text": self.strings("btn_cancel"), "action": "close"}]
            )
            with contextlib.suppress(Exception):
                form = await self.inline.form(
                    head,
                    message=status,
                    reply_markup=buttons,
                    disable_security=True,
                    silent=True,
                )
                if form:
                    return
        lines = "\n".join(
            f"{index}. <a href=\"{utils.escape_html(r['url'])}\">"
            f"{utils.escape_html(label(r))}</a>"
            for index, r in enumerate(results, 1)
        )
        await utils.answer(
            status,
            self.strings("search_text").format(
                head=head, lines=lines, p=self.get_prefix()
            ),
            link_preview=False,
        )

    async def _search_pick(self, call, chat_id, result, audio_only):
        user = getattr(getattr(call, "from_user", None), "id", None)
        if not self._user_allowed(user, "queue"):
            with contextlib.suppress(Exception):
                await call.answer(self.strings("toast_no_access"), show_alert=True)
            return
        with contextlib.suppress(Exception):
            await call.edit(
                self.strings("search_picked").format(
                    utils.escape_html(str(result["title"]))
                ),
                reply_markup=[],
            )
        error = await self._prepare_chat(chat_id)
        if error:
            with contextlib.suppress(Exception):
                await call.edit(error)
            return
        track = Track(
            result["url"],
            title=result["title"],
            audio_only=audio_only,
            duration=result.get("duration"),
            requester=user,
        )
        # Only an owner-level user may replace what is playing.
        now = False
        await self._enqueue(chat_id, [track], now)
        with contextlib.suppress(Exception):
            await call.delete()

    def _download_line(self, track):
        cache = self._streamer.cache(track.stream_url) if track.stream_url else None
        if cache is None or cache.complete:
            return ""
        return self.strings("now_download").format(
            percent=int(cache.progress * 100),
            speed=f"{cache.speed / 1048576:.1f}",
        )

    def _browser_status(self):
        state = self._browser.state()
        refreshed = state.get("refreshed_at")
        if refreshed:
            minutes = int((time.time() - refreshed) // 60)
            refreshed_text = f"{minutes} хв тому"
        else:
            refreshed_text = self.strings("br_none")
        if not self._browser.configured:
            session = self.strings("br_none")
        elif state.get("logged_in"):
            session = self.strings("br_logged")
        else:
            session = self.strings("br_logged_out")
        error = state.get("error")
        return self.strings("br_status").format(
            installed=self.strings(
                "br_yes" if self._browser.available() else "br_no"
            ),
            session=session,
            count=state.get("count", self.strings("br_none")),
            refreshed=refreshed_text,
            every=self.config["browser_refresh_min"],
            error=(
                self.strings("br_error").format(utils.escape_html(error))
                if error
                else ""
            ),
            p=self.get_prefix(),
        )

    async def _browser_setup(self, message):
        message = await utils.answer(message, self.strings("br_setup_running"))
        commands = [
            self._pip_command(["playwright"]),
            [sys.executable, "-m", "playwright", "install", "chromium"],
        ]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # System libraries Chromium needs (apt); only possible as root.
            commands.append(
                [sys.executable, "-m", "playwright", "install-deps", "chromium"]
            )
        for command in commands:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=1200,
            )
            if result.returncode != 0:
                tail = (result.stderr or result.stdout or "")[-800:]
                return await utils.answer(
                    message,
                    self.strings("setup_failed").format(utils.escape_html(tail)),
                )
            importlib.invalidate_caches()
        await utils.answer(
            message, self.strings("br_setup_done").format(self.get_prefix())
        )

    async def _read_cookie_source(self, message, path):
        if path:
            with open(os.path.expanduser(path), encoding="utf-8") as handle:
                return handle.read()
        reply = await message.get_reply_message()
        if reply is None or not getattr(reply, "file", None):
            return None
        data = await self._client.download_media(reply, file=bytes)
        return data.decode("utf-8", "replace") if data else None

    async def _browser_refresh(self, message, cookies=None):
        message = await utils.answer(message, self.strings("br_working"))
        try:
            state = await self._browser.refresh(import_cookies=cookies)
        except Exception as error:
            logger.exception("VoiceChat browser refresh failed")
            self._browser.record_error(error)
            return await utils.answer(
                message,
                self.strings("br_failed").format(
                    utils.escape_html(str(error)[:400] or type(error).__name__)
                ),
            )
        await utils.answer(
            message,
            self.strings("br_done").format(
                count=state.get("count", 0),
                session=self.strings(
                    "br_logged" if state.get("logged_in") else "br_logged_out"
                ),
            ),
        )

    @loader.command(ru_doc="[setup|import|refresh|reset] — віртуальний браузер для YouTube")
    async def vcbrowser(self, message):
        """[setup|import|refresh|reset] — віртуальний браузер, що тримає сесію YouTube живою"""
        args = (utils.get_args_raw(message) or "").strip()
        action, _, rest = args.partition(" ")
        action = action.lower()
        if not action:
            return await utils.answer(message, self._browser_status())
        if action == "setup":
            return await self._browser_setup(message)
        if action == "reset":
            async with self._browser.lock:
                self._browser.reset()
            return await utils.answer(message, self.strings("br_reset"))
        if action not in ("import", "refresh"):
            return await utils.answer(message, self.strings("br_usage"))
        if not self._browser.available():
            return await utils.answer(
                message, self.strings("br_need_setup").format(self.get_prefix())
            )
        if action == "refresh":
            return await self._browser_refresh(message)
        try:
            text = await self._read_cookie_source(message, rest.strip())
        except OSError as error:
            return await utils.answer(
                message,
                self.strings("br_failed").format(utils.escape_html(str(error))),
            )
        if text is None:
            return await utils.answer(
                message, self.strings("br_no_file").format(self.get_prefix())
            )
        cookies = session_cookies(parse_netscape(text))
        if not any("youtube.com" in c["domain"] for c in cookies):
            return await utils.answer(message, self.strings("br_bad_file"))
        await self._browser_refresh(message, cookies)

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
