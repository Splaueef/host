__version__ = (6, 9, 0)
VERSION = ".".join(map(str, __version__))

import os
import re
import glob
import time
import shutil
import logging
import subprocess
import sys
import asyncio
import mimetypes
import unicodedata
import ipaddress
import socket
import json
import signal
import getpass
import sqlite3
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from collections import defaultdict

from telethon.tl.types import InputMessagesFilterMusic
from telethon.tl.functions.messages import CheckChatInviteRequest

from .. import loader, utils

logger = logging.getLogger(__name__)

COOKIES_DIR     = "/home/rkbot/URKbot/"
COOKIES_DEFAULT = os.path.join(COOKIES_DIR, "cookies.txt")
COOKIES_YOUTUBE = os.path.join(COOKIES_DIR, "cookies-youtube-com.txt")
PREFERRED_DENO_PATH = "/usr/local/bin/deno"
PREFERRED_JS_RUNTIME = f"deno:{PREFERRED_DENO_PATH}"
FIREFOX_PROFILE = "/home/rkbot/URKbot/firefox-profile"
FIREFOX_USER = "rkbot"
FIREFOX_WARMUP_SECONDS = 15

PIP_DEPENDENCIES = {
    "yt-dlp": "yt_dlp",
    "gallery-dl": "gallery_dl",
    "instaloader": "instaloader",
    "requests": "requests",
    "spotdl": "spotdl",
}

SUPPORTED_HOSTS = [
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "youtu.be", "youtube.com", "music.youtube.com",
    "instagram.com/", "instagr.am/", "threads.net/",
    "x.com/", "twitter.com/", "t.co/",
    "pinterest.com/", "pin.it/",
    "vimeo.com/", "reddit.com/", "redd.it/",
    "twitch.tv/", "dailymotion.com/",
    "bilibili.com/", "b23.tv/", "facebook.com/", "fb.watch/",
    "soundcloud.com/", "snapchat.com/", "likee.video/", "kwai.com/",
    "open.spotify.com/", "spotify.link/",
    "bandcamp.com/", "audiomack.com/", "mixcloud.com/",
]

MUSIC_HOSTS = (
    "open.spotify.com", "spotify.link", "soundcloud.com", "music.youtube.com",
    "bandcamp.com", "audiomack.com", "mixcloud.com",
)


def _is_music_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return False
    return any(_hostname_matches(hostname, host) for host in MUSIC_HOSTS)


def _is_spotify_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return False
    return any(_hostname_matches(hostname, host) for host in ("open.spotify.com", "spotify.link"))


class _OpenGraphParser(HTMLParser):
    """Collect public Open Graph metadata without depending on BeautifulSoup."""

    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "meta":
            return
        values = {str(key).lower(): value for key, value in attrs if value is not None}
        name = values.get("property") or values.get("name")
        content = values.get("content")
        if name and content:
            self.values[name.lower()] = content.strip()


def _spotify_track_metadata(html_text: str) -> tuple[str, str] | None:
    """Extract a track title and artist from Spotify's public share page."""
    parser = _OpenGraphParser()
    try:
        parser.feed(html_text or "")
    except Exception:
        return None

    title = parser.values.get("og:title", "").strip()
    description = parser.values.get("og:description", "").strip()
    if not title:
        return None

    # Spotify descriptions normally start with ``Artist · Track · ...``.
    # Do not use the track itself as the artist if the page layout changes.
    parts = [part.strip() for part in re.split(r"\s*[·•]\s*", description) if part.strip()]
    artist = parts[0] if parts and parts[0].casefold() != title.casefold() else ""
    return title, artist

def _hostname_matches(hostname: str, domain: str) -> bool:
    domain = domain.lower().strip().strip("/")
    hostname = hostname.lower().strip().lstrip("www.")
    return hostname == domain or hostname.endswith(f".{domain}")


def _is_supported_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return False
    if not hostname:
        return False
    return any(_hostname_matches(hostname, host) for host in SUPPORTED_HOSTS)

COOKIE_DOMAINS = {
    "YouTube": ("youtube.com", "youtu.be", "googlevideo.com"),
    "Instagram": ("instagram.com", "instagr.am", "threads.net"),
    "TikTok": ("tiktok.com", "tiktokv.com", "muscdn.com"),
    "X/Twitter": ("twitter.com", "x.com", "twimg.com"),
    "Reddit": ("reddit.com", "redd.it"),
    "Pinterest": ("pinterest.com", "pinimg.com"),
}

PLATFORM_COOKIES = {
    # Legacy path: auto-merged into cookies.txt and used only as a last resort.
    "youtube.com": COOKIES_YOUTUBE,
    "youtu.be":    COOKIES_YOUTUBE,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac"}

VERTICAL_URL_PATTERNS = [
    r"instagram\.com/(reel|reels|stories)/",
    r"youtube\.com/shorts/",
    r"tiktok\.com/",
    r"x\.com/.+/status/",
    r"twitter\.com/.+/status/",
    r"pinterest\.com/pin/",
]

_INVALID_FNAME_CHARS = r'[\\/:*?"<>|]'
_MAX_FNAME_LEN = 180

# Таймаут на одне завдання в черзі (10 хвилин)
_TASK_TIMEOUT = 600


def _is_safe_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return False
        if parts.username or parts.password:
            return False
        host = parts.hostname.strip().lower().rstrip(".")
        if (
            host in {"localhost", "localhost.localdomain"}
            or host.endswith(".localhost")
            or host.endswith(".local")
        ):
            return False

        def _ip_is_public(value: str) -> bool:
            ip = ipaddress.ip_address(value)
            return not (
                ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            )

        try:
            return _ip_is_public(host)
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except socket.gaierror:
                return False
            addresses = {info[4][0] for info in infos if info and info[4]}
            return bool(addresses) and all(_ip_is_public(address) for address in addresses)
    except Exception:
        return False


def _safe_requests_get(
    requests_module, url: str, *, timeout: int,
    headers: dict | None = None, stream: bool = False, max_redirects: int = 5
):
    current = url
    for _ in range(max_redirects + 1):
        if not _is_safe_http_url(current):
            raise ValueError(f"Unsafe URL blocked: {current}")
        resp = requests_module.get(
            current, timeout=timeout, headers=headers, stream=stream, allow_redirects=False
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("location")
            resp.close()
            if not location:
                raise ValueError("Redirect without location")
            current = urljoin(current, location)
            continue
        return resp
    raise ValueError("Too many redirects")


def _safe_requests_post(requests_module, url: str, *, timeout: int, json_data: dict,
                        headers: dict | None = None):
    """POST JSON only to a validated public endpoint (no implicit redirects)."""
    if not _is_safe_http_url(url):
        raise ValueError(f"Unsafe URL blocked: {url}")
    return requests_module.post(
        url, timeout=timeout, json=json_data, headers=headers, allow_redirects=False
    )


def _response_too_large(resp, max_bytes: int) -> bool:
    try:
        content_length = int(resp.headers.get("content-length") or 0)
    except Exception:
        content_length = 0
    return bool(max_bytes and content_length and content_length > max_bytes)


def _write_response_limited(resp, path: str, max_bytes: int) -> bool:
    written = 0
    with open(path, "wb") as f:
        for chunk in resp.iter_content(1024 * 1024):
            if not chunk:
                continue
            written += len(chunk)
            if max_bytes and written > max_bytes:
                raise ValueError("download exceeds max_size")
            f.write(chunk)
    return os.path.isfile(path) and os.path.getsize(path) > 0

def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(_INVALID_FNAME_CHARS, "_", name)
    name = name.strip(". ")
    if len(name) > _MAX_FNAME_LEN:
        name = name[:_MAX_FNAME_LEN].rstrip()
    return name or "media"


def _parse_music_channels(value) -> list[str | int]:
    """Normalize configured Telegram channel usernames and ``-100`` IDs."""
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[,;\s]+", str(value or ""))

    result: list[str | int] = []
    for raw in raw_items:
        item = str(raw or "").strip()
        if not item:
            continue
        item = re.sub(r"^https?://(?:www\.)?t\.me/", "", item, flags=re.I).strip("/")
        item = re.sub(r"^s/", "", item, flags=re.I)
        private_link = re.fullmatch(r"c/(\d+)(?:/\d+)?", item, flags=re.I)
        if private_link:
            # Telegram's private-channel links omit the ``-100`` peer prefix.
            # Treating ``t.me/c/...`` as a username used to resolve it as
            # ``@c`` and consequently produced an empty index.
            item = f"-100{private_link.group(1)}"
        # People commonly copy the visible channel ID in several forms.  An
        # ``@`` does not turn a numeric ID into a username; canonicalize all
        # of these to Telethon's marked channel ID.
        numeric = re.fullmatch(r"@?(-?)(?:100)?(\d+)", item)
        if numeric:
            item = f"-100{numeric.group(2)}"
        # Invite links are not usernames either. Keep the hash in a form our
        # resolver can recognize instead of passing the invalid ``@+hash``.
        if re.fullmatch(r"@?\+[A-Za-z0-9_-]+", item):
            item = "+" + item.lstrip("@+")
        # A copied post URL points to the channel, not to a separate source.
        item = item.split("/", 1)[0]
        if re.fullmatch(r"-100\d+", item):
            source: str | int = int(item)
        elif item.startswith("+"):
            source = f"https://t.me/{item}"
        else:
            source = "@" + item.lstrip("@")
        if source not in result:
            result.append(source)
    return result


def _normalize_music_text(value: str) -> str:
    """Normalize titles, performers and captions for reliable comparisons."""
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _music_search_queries(query: str) -> list[str]:
    """Build Telegram search fallbacks for titles separated by punctuation."""
    normalized = _normalize_music_text(query)
    words = normalized.split()
    queries = [query.strip(), normalized]
    # Telegram's full-text search may not match ID3 metadata or a caption when
    # the user enters both performer and title. Searching meaningful words one
    # by one gives us candidates that are then ranked locally.
    queries.extend(word for word in words if len(word) > 1)
    return list(dict.fromkeys(item for item in queries if item))


def _ig_shortcode(url: str) -> str | None:
    m = re.search(r"instagram\.com/(?:p|reel|tv|reels)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _cookie_file_has_domain(path: str, domains: tuple[str, ...]) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
                    continue
                low = line.lower()
                if any(d in low for d in domains):
                    return True
    except Exception as e:
        logger.warning("Could not read cookies file %s: %s", path, e)
    return False


def _cookie_domains_status(path: str = COOKIES_DEFAULT) -> dict[str, bool]:
    return {name: _cookie_file_has_domain(path, domains) for name, domains in COOKIE_DOMAINS.items()}


def _merge_platform_cookies() -> bool:
    """Move legacy per-platform cookies into the main cookies.txt file."""
    changed = False
    os.makedirs(COOKIES_DIR, exist_ok=True)
    for host, path in PLATFORM_COOKIES.items():
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            continue
        domains = (host,)
        if _cookie_file_has_domain(COOKIES_DEFAULT, domains):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as src, \
                 open(COOKIES_DEFAULT, "a", encoding="utf-8") as dst:
                if os.path.getsize(COOKIES_DEFAULT) == 0:
                    dst.write("# Netscape HTTP Cookie File\n")
                dst.write(f"\n# Imported from {os.path.basename(path)} by VideoDownloader\n")
                dst.write(src.read().rstrip() + "\n")
            changed = True
            logger.info("Merged legacy cookies for %s into %s", host, COOKIES_DEFAULT)
        except Exception as e:
            logger.warning("Could not merge cookies %s -> %s: %s", path, COOKIES_DEFAULT, e)
    return changed


def _is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).netloc or "").lower().lstrip("www.")
    return host == "youtu.be" or host.endswith(".youtu.be") or host == "youtube.com" or host.endswith(".youtube.com")


def _get_cookies(url: str) -> str | None:
    hostname = (urlsplit(url).netloc or "").lower().lstrip("www.")
    if os.path.isfile(COOKIES_DEFAULT) and os.path.getsize(COOKIES_DEFAULT) > 0:
        matched = [domains for domains in COOKIE_DOMAINS.values() if any(d in hostname for d in domains)]
        if not matched or _cookie_file_has_domain(COOKIES_DEFAULT, matched[0]):
            return COOKIES_DEFAULT
        logger.info("cookies.txt has no cookies for %s; skipping it", hostname)
        return None
    for host, path in PLATFORM_COOKIES.items():
        if host in hostname and os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


class CookieManager:
    """Resolve and validate file/browser cookies without leaking browser details.

    Paths and the browser account are supplied by module configuration, so the
    downloader is no longer tied to one deployment user.
    """

    def __init__(self, cookies_file: str, firefox_profile: str, browser_user: str = ""):
        self.cookies_file = os.path.expanduser(cookies_file or "")
        self.firefox_profile = os.path.expanduser(firefox_profile or "")
        self.browser_user = (browser_user or getpass.getuser()).strip()

    @staticmethod
    def domains_for_url(url: str) -> tuple[str, ...]:
        hostname = (urlsplit(url).hostname or "").lower().lstrip("www.")
        for domains in COOKIE_DOMAINS.values():
            if any(_hostname_matches(hostname, domain) for domain in domains):
                return domains
        return (hostname,) if hostname else ()

    def file_for(self, url: str) -> str | None:
        domains = self.domains_for_url(url)
        if not domains or not os.path.isfile(self.cookies_file):
            return None
        if os.path.getsize(self.cookies_file) <= 0:
            return None
        if _cookie_file_has_domain(self.cookies_file, domains):
            return self.cookies_file
        logger.info("Cookie file %s has no cookies for %s", self.cookies_file, domains[0])
        return None

    @property
    def cookie_db(self) -> str:
        return os.path.join(self.firefox_profile, "cookies.sqlite")

    def firefox_profile_valid(self) -> bool:
        return bool(
            self.firefox_profile
            and os.path.isdir(self.firefox_profile)
            and os.access(self.firefox_profile, os.R_OK | os.W_OK)
            and os.path.isfile(self.cookie_db)
        )

    def firefox_has_url(self, url: str) -> bool:
        if not self.firefox_profile_valid():
            return False
        domains = self.domains_for_url(url)
        try:
            connection = sqlite3.connect(f"file:{self.cookie_db}?mode=ro", uri=True, timeout=2)
            try:
                hosts = (row[0] for row in connection.execute("SELECT host FROM moz_cookies"))
                return any(
                    any(_hostname_matches(str(host).lstrip("."), domain) for domain in domains)
                    for host in hosts
                )
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as error:
            logger.warning("Could not inspect Firefox cookies: %s", error)
            return False




def _is_youtube_auth_error(error: Exception | str) -> bool:
    text = str(error).lower()
    markers = (
        "sign in to confirm",
        "not a bot",
        "cookies-from-browser",
        "getpot",
        "po token",
        "missing pot",
    )
    return any(marker in text for marker in markers)

def _subprocess_env_for_cookie_owner() -> dict:
    """Build an env for yt-dlp subprocesses that matches the cookie owner.

    Manual YouTube checks are commonly run as the ``rkbot`` user.  When Hikka is
    started with a different HOME, Deno/yt-dlp may miss the user's cached JS
    runtime data even though the same command works from the shell.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        if os.path.exists(COOKIES_DEFAULT):
            import pwd

            owner = pwd.getpwuid(os.stat(COOKIES_DEFAULT).st_uid)
            if owner.pw_dir:
                env["HOME"] = owner.pw_dir
    except Exception as e:
        logger.debug("Could not derive subprocess HOME from cookies owner: %s", e)
    return env


def _parse_browser_cookies(value: str | None) -> tuple | None:
    """Parse yt-dlp cookies-from-browser config.

    Accepts the same compact form users know from yt-dlp CLI, for example:
    ``chrome``, ``firefox:/path/to/profile`` or ``chrome:Default``.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    parts = [part.strip() or None for part in raw.split(":", 3)]
    browser = parts[0]
    if not browser:
        return None
    while len(parts) < 4:
        parts.append(None)
    return tuple(parts[:4])

def _find_file(base_name: str) -> str | None:
    for ext in ("mp4", "mp3", "webm", "mkv", "m4a", "ogg", "opus",
                "jpg", "jpeg", "png", "gif", "webp", "bmp", "wav", "aac", "flac"):
        p = f"{base_name}.{ext}"
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    for p in sorted(glob.glob(f"{base_name}.*")):
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    for p in sorted(glob.glob(f"{base_name}*")):
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            ext = os.path.splitext(p)[1].lower()
            if ext in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS:
                return p
    return None


def _cleanup(base_name: str):
    for p in glob.glob(f"{base_name}.*") + glob.glob(f"{base_name}*"):
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p) and p.endswith(("_gallery", "_ig", "_spotify")):
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _file_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    mime, _ = mimetypes.guess_type(path)
    if mime:
        if mime.startswith("video"):
            return "video"
        if mime.startswith("audio"):
            return "audio"
        if mime.startswith("image"):
            return "image"
    return "other"


def _video_has_audio(path: str) -> bool:
    """Return True when ffprobe can see at least one audio stream."""
    if _file_type(path) != "video":
        return True
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Do not reject downloads on systems without ffprobe; the format
        # selectors above already prefer audio and merged outputs.
        return True
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception as e:
        logger.debug("Could not probe audio stream for %s: %s", path, e)
        return True


def _rotation_filter_from_stream(stream: dict) -> str | None:
    """Return the filter needed to bake a video's display rotation into pixels.

    The legacy ``rotate`` tag uses clockwise degrees, while ffprobe's Display
    Matrix reports counter-clockwise degrees.  Treating them as equivalent
    rotates phone recordings in the opposite direction.  Prefer the modern
    display matrix and translate it to the legacy/FFmpeg filter convention.
    """
    rotation = None
    for side_data in stream.get("side_data_list", []):
        if side_data.get("side_data_type") == "Display Matrix":
            try:
                rotation = -int(round(float(side_data.get("rotation", 0))))
            except (TypeError, ValueError):
                pass
            break

    if rotation is None:
        try:
            rotation = int(round(float(stream.get("tags", {}).get("rotate", 0))))
        except (TypeError, ValueError):
            rotation = 0

    rotation %= 360
    return {
        90: "transpose=clock",
        180: "hflip,vflip",
        270: "transpose=cclock",
    }.get(rotation)

def _media_dimensions_from_info(info: dict | None) -> tuple[int | None, int | None]:
    """Return the most reliable width/height pair available in yt-dlp info."""
    if not info:
        return None, None
    width = info.get("width")
    height = info.get("height")
    if width and height:
        return int(width), int(height)

    formats = [
        f for f in (info.get("requested_formats") or info.get("formats") or [])
        if f and f.get("vcodec") not in (None, "none", "")
    ]
    if not formats:
        return None, None
    # Prefer the selected/requested format; otherwise the biggest available
    # video format gives a stable orientation signal for short-form platforms.
    formats.sort(key=lambda f: (int(f.get("height") or 0), int(f.get("width") or 0)))
    width = formats[-1].get("width")
    height = formats[-1].get("height")
    return (int(width), int(height)) if width and height else (None, None)


def _info_is_vertical(info: dict | None, fallback: bool = False) -> bool:
    width, height = _media_dimensions_from_info(info)
    if width and height:
        return height > width
    return fallback


def _is_vertical_url(url: str) -> bool:
    u = url.lower()
    for pattern in VERTICAL_URL_PATTERNS:
        if re.search(pattern, u):
            return True
    return False


def _normalize_youtube_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.netloc.lower().lstrip("www.")
        if "youtu.be" in host:
            vid = parts.path.strip("/").split("/")[0]
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
        if "youtube.com" in host:
            if parts.path.startswith("/shorts/"):
                vid = parts.path.replace("/shorts/", "").split("/")[0].split("?")[0]
                if vid:
                    return f"https://www.youtube.com/shorts/{vid}"
            params = dict(parse_qsl(parts.query))
            clean = {}
            if "v" in params:
                clean["v"] = params["v"]
            if "list" in params:
                clean["list"] = params["list"]
            if clean:
                return f"https://www.youtube.com/watch?{urlencode(clean)}"
    except Exception:
        pass
    return url


def _detect_js_runtime() -> tuple[str, str] | None:
    candidates = [
        ("deno", ["/usr/local/bin/deno", "/usr/bin/deno"]),
        ("node", ["/usr/bin/node", "/usr/local/bin/node"]),
        ("nodejs", ["/usr/bin/nodejs", "/usr/local/bin/nodejs"]),
    ]
    for name, paths in candidates:
        for p in paths:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return (name, p)
        found = shutil.which(name)
        if found:
            return (name, found)
    return None


def _js_runtime_arg() -> str | None:
    rt = _detect_js_runtime()
    if rt:
        name, path = rt
        runtime_key = "node" if name == "nodejs" else name
        return f"{runtime_key}:{path}"
    return None


def _preferred_js_runtime_arg() -> str | None:
    """Prefer the known-good Deno binary for YouTube player challenges."""
    if os.path.isfile(PREFERRED_DENO_PATH) and os.access(PREFERRED_DENO_PATH, os.X_OK):
        return PREFERRED_JS_RUNTIME
    return _js_runtime_arg()


def _js_runtime_opts(runtime: str | None) -> dict:
    """Return yt-dlp Python API options for an explicit JS runtime.

    ``--js-runtimes node:/path`` is a CLI option; the Python API expects a
    top-level ``js_runtimes`` mapping.  Keeping this out of ``extractor_args``
    is required for yt-dlp to actually detect Node/Deno/Bun/QuickJS.
    """
    if not runtime:
        return {}
    name, _, path = runtime.partition(":")
    if not name:
        return {}
    runtime_cfg = {"path": path} if path else {}
    opts = {"js_runtimes": {name: runtime_cfg}}
    # Allow yt-dlp's EJS component loader to fetch challenge solver scripts
    # when available. If the runtime has no network access, yt-dlp simply falls
    # back to installed/local providers.
    opts["remote_components"] = {"ejs:github"}
    return opts


@loader.tds
class VideoDownloaderMod(loader.Module):
    """Завантаження відео й музики зі Spotify, YouTube, TikTok та інших платформ."""

    strings = {
        "name": "VideoDownloader",
        "loading":            "<b>📥 Завантажую...</b>",
        "loading_progress":   "<b>📥 Завантажую... {}%</b>",
        "loading_retry":      "<b>🔄 Знижую якість, повторюю... ({}/{})</b>",
        "loading_playlist":   "<b>📋 Плейлист: {}/{}...</b>",
        "loading_photo":      "<b>🖼 Завантажую медіа...</b>",
        "loading_fix":        "<b>🔧 Виправляю орієнтацію відео...</b>",
        "loading_transcript": "<b>📝 Витягую транскрипт...</b>",
        "loading_music":      "<b>🎵 Шукаю та завантажую музику...</b>",
        "searching_music":    "<b>🔎 Шукаю музику в налаштованих каналах...</b>",
        "music_no_query":     "<b>❌ Вкажи назву та/або автора композиції чи URL.</b>",
        "music_no_channels":  "<b>❌ Додай канали для пошуку в <code>music_channels</code> (юзернейм або <code>-100ID</code>).</b>",
        "music_not_found":    "<b>❌ У налаштованих каналах нічого не знайдено.</b>",
        "music_found":        "<b>🎵 {title}</b>\n<b>Виконавець:</b> {artist}",
        "music_channels_list":"<b>🎵 Канали для пошуку:</b>\n{}",
        "music_channels_added":"<b>✅ Додано каналів: {added}.</b> Всього: <code>{total}</code>.",
        "music_channels_removed":"<b>🗑 Видалено каналів: {removed}.</b> Залишилось: <code>{total}</code>.",
        "music_channels_empty":"<b>📋 Канали для пошуку не налаштовані.</b>",
        "music_channels_usage":"<b>ℹ️ Використання:</b> <code>.vdlchannels add @channel1 @channel2</code>\n<code>.vdlchannels rm @channel</code> · <code>.vdlchannels clear</code>",
        "music_indexing":     "<b>🎵 Індексую аудіо в каналах...</b>",
        "music_indexed":      "<b>✅ Індексацію завершено.</b> Треків у каталозі: <code>{total}</code>.",
        "music_index_failed": "<b>⚠️ Канали збережено, але індексацію не вдалося завершити.</b>",
        "music_choose":       "<b>🎤 Знайдені треки виконавця {artist}:</b>\nОберіть потрібний трек:",
        "music_choose_match": "<b>🎵 Знайдено кілька треків:</b>\nОберіть потрібний:",
        "err_file":           "<b>❌ Не вдалося отримати файл.</b>",
        "err_youtube_auth":   "<b>❌ YouTube просить підтвердити, що це не бот. Якщо відео приватне/18+, онови cookies: <code>.vdlcookies</code>. Для публічних відео модуль спершу пробує режим без cookies, щоб YouTube рідше ротував сесію.</b>",
        "err_size":           "<b>❌ Файл завеликий ({} МБ). Знижую якість...</b>",
        "err_size_final":     "<b>❌ Файл завеликий навіть у найнижчій якості.</b>",
        "err_limit":          "<b>🚫 Денний ліміт ({} завантажень) вичерпано.</b>",
        "err_cooldown":       "<b>⏳ Зачекай {} сек.</b>",
        "err_playlist_off":   "<b>❌ Плейлисти вимкнено: <code>.vdlset playlist 1</code></b>",
        "err_queue_full":     "<b>⏳ Черга повна ({} завдань).</b>",
        "err_no_transcript":  "<b>❌ Транскрипт недоступний для цього відео.</b>",
        "err_timeout":        "<b>❌ Завантаження перервано: перевищено ліміт часу.</b>",
        "playlist_done":      "<b>✅ Плейлист: {ok}/{total} завантажено.</b>",
        "queue_pos":          "<b>📋 Черга: позиція {pos}</b>",
        "toggled_on":         "<b>✅ Downloader: ON</b>",
        "toggled_off":        "<b>❌ Downloader: OFF</b>",
        "audio_on":           "<b>🎵 Аудіо-режим: ON</b>",
        "audio_off":          "<b>🎬 Відео-режим: ON</b>",
        "whitelist_added":    "<b>✅ Групу <code>{}</code> додано.</b>",
        "whitelist_removed":  "<b>🗑 Групу <code>{}</code> видалено.</b>",
        "whitelist_empty":    "<b>📋 Білий список порожній.</b>",
        "whitelist_list":     "<b>📋 Групи:</b>\n{}",
        "not_a_group":        "<b>❌ Тільки в групах.</b>",
        "already_in":         "<b>⚠️ Вже є в списку.</b>",
        "not_in":             "<b>⚠️ Немає в списку.</b>",
        "bl_added":           "<b>🚫 <code>{}</code> заблоковано.</b>",
        "bl_removed":         "<b>✅ <code>{}</code> розблоковано.</b>",
        "bl_empty":           "<b>📋 Чорний список порожній.</b>",
        "bl_list":            "<b>📋 Заблоковані:</b>\n{}",
        "bl_need_reply":      "<b>❌ Відповідай на повідомлення.</b>",
        "bl_not_in":          "<b>⚠️ Немає в чорному списку.</b>",
        "bl_already_in":      "<b>⚠️ Вже в чорному списку.</b>",
        "dl_started":         "<b>📥 Завантажую: <code>{url}</code></b>",
        "dl_no_url":          "<b>❌ Вкажи URL або відповідай на повідомлення з посиланням.</b>",
        "cookies_refreshed":  "<b>✅ Cookies оновлено ({} байт).</b>",
        "cookies_refresh_err":"<b>❌ Помилка оновлення cookies: {}</b>",
        "update_ok":          "<b>✅ yt-dlp оновлено до останньої версії.</b>",
        "update_err":         "<b>❌ Не вдалося оновити yt-dlp: {}</b>",
        "stats": (
            "<b>📊 Статистика:</b>\n"
            "├ Всього: <code>{total}</code>\n"
            "├ Успішних: <code>{ok}</code>\n"
            "├ Помилок: <code>{err}</code>\n"
            "├ Retry: <code>{retried}</code>\n"
            "├ Таймаутів: <code>{timeouts}</code>\n"
            "├ MP3: <code>{audio}</code>\n"
            "├ Фото: <code>{photos}</code>\n"
            "├ Плейлистів: <code>{playlists}</code>\n"
            "├ Транскриптів: <code>{transcripts}</code>\n"
            "├ Сьогодні: <code>{today}</code> / <code>{limit}</code>\n"
            "└ Платформи:\n{platforms}"
        ),
        "stats_reset":    "<b>🗑 Статистику скинуто.</b>",
        "cookies_status": (
            "<b>🍪 Cookies:</b> <code>cookies.txt</code>\n"
            "├ Файл: {default}\n"
            "├ Legacy YouTube: {yt}\n"
            "├ Шлях: <code>/home/rkbot/URKbot/cookies.txt</code>\n"
            "├ Режим YouTube: <code>{mode}</code>\n"
            "├ Browser cookies: <code>{browser}</code>\n"
            "├ Порядок: файл cookies → оновлення Firefox → browser cookies.\n"
            "└ Домени в cookies.txt:\n{domains}"
        ),
        "js_runtime_status":  "<b>🟢 JS Runtime: <code>{rt}</code></b>",
        "js_runtime_missing": "<b>🔴 JS Runtime: не знайдено (YouTube може не працювати!)</b>",
        "caption_video":      "<b>✅ <a href=\"https://t.me/RotKranz\">VIA</a></b>",
        "caption_audio":      "<b>🎵 <a href=\"https://t.me/RotKranz\">VIA</a></b>",
        "caption_photo":      "<b>🖼 <a href=\"https://t.me/RotKranz\">VIA</a></b>",
        "caption_file":       "<b>📎 <a href=\"https://t.me/RotKranz\">VIA</a></b>",
        "caption_playlist":   "<b>📋 {title} ({idx}/{total})</b>",
        "transcript_header":  "<b>📝 Транскрипт: {title}</b>\n\n",
        "help_text": (
            f"<b>🎬 VideoDownloader v{VERSION}</b>\n\n"
            "<b>Основні команди:</b>\n"
            "• <code>.vdl</code> — увімк/вимк авто-завантаження\n"
            "• <code>.vdldl [URL]</code> — ручне завантаження\n"
            "• <code>.vdlmusic [назва / автор / URL]</code> — знайти музику в каналах або сервісах\n"
            "• <code>.vdlchannels add @one @two ...</code> — додати одразу кілька каналів\n"
            "• <code>.vdlaudio</code> — перемкнути MP3/відео\n"
            "• <code>.vdlq [360/480/720/1080/best]</code> — якість\n"
            "• <code>.vdlcookies</code> — статус cookies\n"
            "• <code>.vdlupdate</code> — оновити yt-dlp\n"
            "• <code>.vdlqueue</code> — черга\n"
            "• <code>.vdlruntime</code> — статус JS runtime\n\n"
            "• <code>.vdldiag</code> — повна діагностика\n\n"
            "<b>Транскрипт:</b>\n"
            "• <code>.vdlt [URL]</code> — транскрипт YouTube/Bilibili\n\n"
            "<b>Групи:</b> .vdladd / .vdlrm / .vdllist\n"
            "<b>ЛС:</b> .vdlpm / .vdlpmlist — увімкнути автозавантаження для контакту\n"
            "<b>Бан:</b> .vdlban / .vdlunban / .vdlbans\n"
            "<b>Стат:</b> .vdlstats / .vdlreset\n\n"
            "<b>.vdlset [параметр] [значення]:</b>\n"
            "cooldown, limit, size, auto_delete,\n"
            "retries, queue_max, notify_dm,\n"
            "playlist, playlist_max, audio_format, workers, cli, any_url, ipv4"
        ),
    }

    requires = ["yt-dlp", "requests", "instaloader", "gallery-dl", "spotdl"]

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("enabled",          True,  "Увімкнути?"),
            loader.ConfigValue("max_size",         500,   "Макс. розмір файлу (МБ)"),
            loader.ConfigValue("audio_mode",       False, "MP3 замість відео?"),
            loader.ConfigValue("audio_format",     "mp3", "Формат аудіо: mp3/m4a/wav/opus/flac"),
            loader.ConfigValue("quality",          "720", "Якість: 360/480/720/1080/best"),
            loader.ConfigValue("cooldown",         0,     "Кулдаун (сек)"),
            loader.ConfigValue("daily_limit",      0,     "Денний ліміт (0=∞)"),
            loader.ConfigValue("auto_delete",      0,     "Авто-видалення (сек, 0=вимкнено)"),
            loader.ConfigValue("retries",          3,     "Спроб зі зниженням якості"),
            loader.ConfigValue("queue_max",        5,     "Макс. черга"),
            loader.ConfigValue("queue_workers",    2,     "Паралельних завантажень (1-4)"),
            loader.ConfigValue("notify_dm",        False, "Сповіщення в ЛС?"),
            loader.ConfigValue("fix_orientation",  True,  "Авто-виправлення орієнтації відео?"),
            loader.ConfigValue("playlist_enabled", False, "Дозволити плейлисти?"),
            loader.ConfigValue("playlist_max",     10,    "Макс. відео з плейлиста"),
            loader.ConfigValue("group_whitelist",  [],    "Білий список груп"),
            loader.ConfigValue("private_whitelist", [],    "Контакти в ЛС з дозволеним автозавантаженням"),
            loader.ConfigValue("user_blacklist",   [],    "Чорний список юзерів"),
            loader.ConfigValue("ig_username",      "",    "Instagram логін"),
            loader.ConfigValue("ig_password",      "",    "Instagram пароль"),
            loader.ConfigValue("transcript_lang",  "uk",  "Мова транскрипту"),
            loader.ConfigValue("task_timeout",     600,   "Таймаут завдання (сек)"),
            loader.ConfigValue("auto_update_ytdlp", True, "Автоматично оновлювати yt-dlp раз на добу"),
            loader.ConfigValue("auto_install_deps", True, "Автоматично ставити відсутні бібліотеки"),
            loader.ConfigValue("use_gallery_dl",    True, "Fallback через gallery-dl для Reddit/Pinterest/X/Instagram тощо"),
            loader.ConfigValue("use_cli_ytdlp",     True, "Використовувати універсальний yt-dlp CLI режим як у tuitube"),
            loader.ConfigValue("force_ipv4",        False, "Додавати --force-ipv4 для yt-dlp CLI"),
            loader.ConfigValue("allow_any_url",     False, "Автозавантажувати будь-які URL, які підтримує yt-dlp"),
            loader.ConfigValue("yt_dlp_path",       "", "Шлях до yt-dlp binary (порожньо = auto/python -m yt_dlp)"),
            loader.ConfigValue("ffmpeg_path",       "", "Шлях до ffmpeg або директорії з ffmpeg (порожньо = auto)"),
            loader.ConfigValue("yt_browser_cookies", f"firefox:{FIREFOX_PROFILE}", "Browser cookies fallback для yt-dlp: browser:profile"),
            loader.ConfigValue("cookies_file", COOKIES_DEFAULT, "Спільний cookies.txt"),
            loader.ConfigValue("firefox_profile", FIREFOX_PROFILE, "Firefox profile для оновлення cookies"),
            loader.ConfigValue("browser_user", "", "Користувач Firefox (порожньо = поточний)"),
            loader.ConfigValue("yt_cookies_mode",   "auto", "Cookies: auto/always/never. auto = файл cookies, потім Firefox fallback"),
            loader.ConfigValue("songlink_enabled",  True, "Шукати альтернативні музичні платформи через song.link"),
            loader.ConfigValue("music_channels", [], "Канали для пошуку музики: @username або -100ID"),
            loader.ConfigValue("music_search_limit", 25, "Скільки повідомлень перевіряти в кожному музичному каналі"),
            loader.ConfigValue("cobalt_api_url", "", "Cobalt API fallback для YouTube (URL власного/доступного інстансу)"),
            loader.ConfigValue("cobalt_api_key", "", "Необов'язковий API key для Cobalt"),
        )
        self._stats = {
            "total": 0, "ok": 0, "err": 0, "retried": 0,
            "audio": 0, "photos": 0, "playlists": 0, "today": 0,
            "transcripts": 0, "timeouts": 0,
            "day": time.strftime("%Y-%m-%d"),
            "platforms": defaultdict(int),
        }
        self._last_dl: float = 0.0
        self._queue: asyncio.Queue | None = None
        self._worker_task = None
        self._worker_tasks: list[asyncio.Task] = []
        self._client = None
        self._music_index_lock = asyncio.Lock()
        self._music_index_task = None
        self._js_runtime: str | None = _preferred_js_runtime_arg()
        if self._js_runtime:
            logger.info("VideoDownloader: JS runtime detected: %s", self._js_runtime)
        else:
            logger.warning("VideoDownloader: No JS runtime found! YouTube may fail.")

    def _cookie_manager(self) -> CookieManager:
        return CookieManager(
            str(self.config.get("cookies_file", COOKIES_DEFAULT) or COOKIES_DEFAULT),
            str(self.config.get("firefox_profile", FIREFOX_PROFILE) or FIREFOX_PROFILE),
            str(self.config.get("browser_user", "") or ""),
        )

    def _cookies_for(self, url: str) -> str | None:
        return self._cookie_manager().file_for(url)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def client_ready(self, client, db):
        self._client = client
        _merge_platform_cookies()
        self._queue = asyncio.Queue(maxsize=self.config["queue_max"])
        self._start_queue_workers()
        if self.config.get("auto_install_deps", True):
            asyncio.ensure_future(self._ensure_runtime_dependencies())
        elif self.config.get("auto_update_ytdlp", True):
            asyncio.ensure_future(self._auto_update_ytdlp())
        if _parse_music_channels(self.config.get("music_channels", [])):
            self._music_index_task = asyncio.ensure_future(self._index_music_channels())

    async def on_unload(self):
        if self._music_index_task:
            self._music_index_task.cancel()
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []
        self._worker_task = None


    def _queue_workers_count(self) -> int:
        try:
            return max(1, min(4, int(self.config.get("queue_workers", 2))))
        except Exception:
            return 2

    def _start_queue_workers(self):
        # Кілька воркерів прибирають головний bottleneck: короткі відео більше не чекають,
        # доки попереднє завдання повністю завантажиться та відправиться.
        for task in self._worker_tasks:
            task.cancel()
        self._worker_tasks = [
            asyncio.ensure_future(self._queue_worker())
            for _ in range(self._queue_workers_count())
        ]
        self._worker_task = self._worker_tasks[0] if self._worker_tasks else None

    async def _queue_worker(self):
        while True:
            try:
                coro = await self._queue.get()
                try:
                    timeout = self.config.get("task_timeout", _TASK_TIMEOUT)
                    await asyncio.wait_for(coro, timeout=timeout)
                except asyncio.TimeoutError:
                    self._stats["timeouts"] += 1
                    logger.warning("Queue task timed out after %s sec", timeout)
                except Exception:
                    logger.exception("Queue worker task error")
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Queue worker error")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _reset_daily(self):
        today = time.strftime("%Y-%m-%d")
        if self._stats["day"] != today:
            self._stats["today"] = 0
            self._stats["day"] = today

    def _private_peer_id(self, message):
        return getattr(message, "chat_id", None) or getattr(message, "sender_id", None)

    def _is_allowed(self, message) -> bool:
        if message.is_private:
            return self._private_peer_id(message) in self.config.get("private_whitelist", [])
        return message.chat_id in self.config["group_whitelist"]

    def _is_banned(self, message) -> bool:
        uid = getattr(message.sender_id, "user_id", message.sender_id)
        return uid in self.config["user_blacklist"]

    def _cooldown_left(self) -> int:
        cd = self.config["cooldown"]
        if not cd:
            return 0
        return max(0, int(cd - (time.time() - self._last_dl)))

    def _limit_reached(self) -> bool:
        lim = self.config["daily_limit"]
        if not lim:
            return False
        self._reset_daily()
        return self._stats["today"] >= lim

    def _is_playlist(self, url: str) -> bool:
        u = url.lower()
        if "youtube.com" not in u and "youtu.be" not in u:
            return False
        params = dict(parse_qsl(urlsplit(url).query))
        path = urlsplit(url).path
        if path.startswith("/shorts/"):
            return False
        return "list" in params or path.startswith("/playlist")

    def _platform(self, url: str) -> str:
        u = url.lower()
        for host, name in [
            ("spotify.com", "Spotify"), ("spotify.link", "Spotify"),
            ("soundcloud.com", "SoundCloud"), ("bandcamp.com", "Bandcamp"),
            ("audiomack.com", "Audiomack"), ("mixcloud.com", "Mixcloud"),
            ("tiktok.com", "TikTok"), ("youtu", "YouTube"),
            ("instagram.com", "Instagram"), ("instagr.am", "Instagram"),
            ("x.com", "X/Twitter"), ("twitter.com", "X/Twitter"),
            ("pinterest.com", "Pinterest"), ("pin.it", "Pinterest"),
            ("vimeo.com", "Vimeo"), ("reddit.com", "Reddit"), ("redd.it", "Reddit"),
            ("twitch.tv", "Twitch"), ("dailymotion.com", "Dailymotion"),
            ("bilibili.com", "Bilibili"), ("b23.tv", "Bilibili"),
        ]:
            if host in u:
                return name
        return "Other"

    def _extract_url(self, text: str) -> str | None:
        m = re.search(r'https?://[^\s<>"\'\]\)]+|(?:www\.)[^\s<>"\'\]\)]+', text)
        if not m:
            return None
        url = m.group(0).rstrip(".,!?:;)]}>\"'")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _normalize(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            h = (parts.netloc or "").lower()
            if "youtube.com" in h or "youtu.be" in h:
                return _normalize_youtube_url(url)
            if "instagram.com" in h or "instagr.am" in h:
                clean_path = parts.path.rstrip("/")
                params = dict(parse_qsl(parts.query))
                clean_params = {k: v for k, v in params.items() if not k.startswith("utm_")}
                qs = urlencode(clean_params) if clean_params else ""
                return urlunsplit((parts.scheme, parts.netloc, clean_path, qs, ""))
            if "x.com" in h or "twitter.com" in h:
                return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        except Exception:
            pass
        return url

    def _audio_postprocessor(self) -> list[dict]:
        fmt = self.config.get("audio_format", "mp3").lower()
        if fmt not in {"mp3", "m4a", "wav", "opus", "flac", "aac"}:
            fmt = "mp3"
        quality = "0" if fmt == "flac" else "192"
        return [{"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": quality}]

    def _fast_ytdlp_opts(self) -> dict:
        return {
            "concurrent_fragment_downloads": 8,
            "buffersize": 4 * 1024 * 1024,
            "http_chunk_size": 10 * 1024 * 1024,
            "socket_timeout": 20,
            "retries": 5,
            "fragment_retries": 5,
            "file_access_retries": 3,
        }

    def _format_chain(self, quality: str, vertical: bool = False) -> list[str]:
        q = quality.lower().replace("p", "")
        if vertical:
            h = {"360": 640, "480": 854, "720": 1280, "1080": 1920}.get(q, 1280)
        else:
            h = {"360": 360, "480": 480, "720": 720, "1080": 1080}.get(q, 720)
        limit = "" if q == "best" else f"[height<={h}]"
        # Prefer progressive files that already contain audio.  Some platforms
        # expose video-only MP4 as ``best[ext=mp4]``; selecting that first was
        # the main reason Instagram/X/etc. videos arrived muted.  If a
        # progressive audio+video file is unavailable, explicitly merge best
        # video with best audio and only then fall back to generic best/worst.
        return [
            f"best[ext=mp4][vcodec^=avc1][acodec!=none]{limit}",
            f"best[ext=mp4][vcodec!=none][acodec!=none]{limit}",
            f"best[vcodec!=none][acodec!=none]{limit}",
            f"bestvideo[ext=mp4][vcodec^=avc1]{limit}+bestaudio[ext=m4a]/bestvideo[ext=mp4]{limit}+bestaudio",
            f"bestvideo{limit}+bestaudio",
            "best[ext=mp4][vcodec!=none][acodec!=none]",
            "best[vcodec!=none][acodec!=none]",
            "bestvideo+bestaudio",
            "best",
            "worst",
        ]

    def _youtube_format_chain(self, quality: str, vertical: bool = False) -> list[str]:
        q = quality.lower().replace("p", "")
        h = {"360": 360, "480": 480, "720": 720, "1080": 1080}.get(q, 720) if q != "best" else 9999
        if vertical:
            h = {"360": 640, "480": 854, "720": 1280, "1080": 1920}.get(q, 1280)
        limit = "" if q == "best" else f"[height<={h}]"
        return [
            f"best[ext=mp4][vcodec^=avc1][acodec!=none]{limit}",
            f"best[ext=mp4][vcodec!=none][acodec!=none]{limit}",
            f"best[vcodec!=none][acodec!=none]{limit}",
            f"bestvideo[protocol=m3u8_native][ext=mp4]{limit}+bestaudio[ext=m4a]",
            f"bestvideo[protocol=m3u8_native]{limit}+bestaudio",
            f"bestvideo[ext=mp4][vcodec^=avc1]{limit}+bestaudio[ext=m4a]",
            f"bestvideo{limit}+bestaudio",
            "best[ext=mp4][vcodec!=none][acodec!=none]",
            "bestvideo+bestaudio",
            "best",
            "worst",
        ]

    def _quality_steps(self) -> list[str]:
        order = ["1080", "720", "480", "360", "best"]
        cur = str(self.config["quality"]).replace("p", "")
        try:
            idx = order.index(cur)
        except ValueError:
            idx = 1
        return order[idx:]

    def _build_yt_extractor_args(
        self, player_clients: str | list[str], allow_missing_pot: bool = False
    ) -> dict:
        """Build yt-dlp YouTube extractor args compatible with recent YouTube changes.

        yt-dlp expects player clients as separate values. Passing
        ``"tv,tv_simply"`` as one value made newer yt-dlp releases treat it
        as an unknown client. ``formats=missing_pot`` is reserved for fallback
        attempts because those formats can still fail with HTTP 403, but it
        lets yt-dlp try formats skipped when no PO token is available.
        """
        if isinstance(player_clients, str):
            clients = [c.strip() for c in player_clients.split(",") if c.strip()]
        else:
            clients = [c.strip() for c in player_clients if c and c.strip()]

        youtube_args: dict = {}
        if clients:
            youtube_args["player_client"] = clients
        if allow_missing_pot:
            youtube_args["formats"] = ["missing_pot"]
        return {"youtube": youtube_args}

    def _find_audio_output(self, base_path: str, audio_fmt: str) -> str | None:
        """
        FIX: Надійно знаходить аудіофайл після FFmpegExtractAudio postprocessor.
        prepare_filename повертає оригінальний шлях (.webm/.m4a), а не .mp3
        тому шукаємо за базою імені.
        """
        # Спробуємо очікуваний шлях
        expected = re.sub(r"\.\w+$", f".{audio_fmt}", base_path)
        if os.path.isfile(expected) and os.path.getsize(expected) > 0:
            return expected
        # Шукаємо за базою (без розширення)
        base_no_ext = re.sub(r"\.\w+$", "", base_path)
        found = _find_file(base_no_ext)
        if found and os.path.splitext(found)[1].lower() in AUDIO_EXTS:
            return found
        return None

    # ── progress hook ─────────────────────────────────────────────────────────

    def _progress_hook(self, status_msg, loop):
        last = [-1]
        last_update = [0.0]

        def hook(d):
            if d.get("status") != "downloading":
                return
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            dl = d.get("downloaded_bytes", 0)
            if not total:
                return
            pct = int(dl / total * 100)
            if pct - last[0] < 5:
                return
            now = time.monotonic()
            if pct < 100 and now - last_update[0] < 2:
                return
            last[0] = pct
            last_update[0] = now
            asyncio.run_coroutine_threadsafe(
                status_msg.edit(self.strings("loading_progress").format(pct)), loop
            )

        return hook

    # ── orientation fix ───────────────────────────────────────────────────────

    async def _normalize_video_container(self, path: str) -> str:
        """Produce a compact, streamable MP4 that works reliably on iOS.

        A .mp4 suffix alone does not imply iPhone compatibility: downloads may
        contain AV1/VP9, an unsupported pixel format, or Opus audio.  Compatible
        H.264/AAC files are only remuxed when needed; other files are encoded at
        a visually transparent CRF and the optimized copy is kept only when it
        is smaller (unless conversion is required for compatibility).
        """
        if _file_type(path) != "video":
            return path
        ext = os.path.splitext(path)[1].lower()

        def _convert():
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
                capture_output=True, text=True, timeout=20,
            )
            streams = json.loads(probe.stdout).get("streams", []) if probe.returncode == 0 else []
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            compatible = (
                ext == ".mp4"
                and video.get("codec_name") == "h264"
                and video.get("pix_fmt") in {"yuv420p", "yuvj420p"}
                and all(s.get("codec_name") in {"aac", "mp3"} for s in audio_streams)
            )

            output = os.path.splitext(path)[0] + "_ios.mp4"
            if compatible:
                cmd = ["ffmpeg", "-i", path, "-map", "0:v:0", "-map", "0:a?",
                       "-c", "copy", "-movflags", "+faststart", "-y", output]
            else:
                # CRF 21 preserves perceived source quality while H.264 + AAC +
                # yuv420p avoids the black-screen/unplayable files seen on iOS.
                cmd = [
                    "ffmpeg", "-i", path, "-map", "0:v:0", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-profile:v", "high",
                    "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                    "-movflags", "+faststart", "-y", output,
                ]
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode != 0 or not os.path.isfile(output) or os.path.getsize(output) <= 0:
                if os.path.isfile(output):
                    os.remove(output)
                return path

            # A compatible source is already usable. Avoid replacing it with a
            # larger remux; incompatible sources must use the iOS-safe result.
            if compatible and os.path.getsize(output) >= os.path.getsize(path):
                os.remove(output)
                return path
            os.remove(path)
            return output

        try:
            return await utils.run_sync(_convert)
        except Exception as e:
            logger.warning("Video compatibility conversion failed for %s: %s", path, e)
            return path

    async def _maybe_fix_orientation(self, path: str, status_msg) -> str:
        if not self.config["fix_orientation"] or _file_type(path) != "video":
            return path

        def _check_and_fix():
            import json
            import subprocess
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_streams", "-select_streams", "v:0", path],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode != 0:
                    return path, None
                streams = json.loads(r.stdout).get("streams", [])
                if not streams:
                    return path, None
                return path, _rotation_filter_from_stream(streams[0])
            except Exception as e:
                logger.warning("_check_and_fix error: %s", e)
                return path, None

        original_path, vf_filter = await utils.run_sync(_check_and_fix)
        if not vf_filter:
            return original_path

        await status_msg.edit(self.strings("loading_fix"))
        fixed_path = re.sub(r"\.\w+$", "_fixed.mp4", original_path)
        if fixed_path == original_path:
            fixed_path = original_path + "_fixed.mp4"

        def _do_fix():
            import subprocess
            cmd = [
                # We apply the display transform explicitly.  Without
                # -noautorotate FFmpeg applies it once on input and the filter
                # applies it again, which swaps portrait/landscape on phones.
                "ffmpeg", "-noautorotate", "-i", original_path,
                "-vf", vf_filter,
                "-metadata:s:v:0", "rotate=0",
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "copy", "-movflags", "+faststart",
                "-y", fixed_path,
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.isfile(fixed_path) and os.path.getsize(fixed_path) > 0:
                try:
                    os.remove(original_path)
                except Exception:
                    pass
                return fixed_path
            return original_path

        return await utils.run_sync(_do_fix)

    # ── direct download ───────────────────────────────────────────────────────

    async def _try_direct(self, url: str, base_name: str) -> str | None:
        import requests

        def _fetch():
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                }
                max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                resp = _safe_requests_get(requests, url, timeout=30, stream=True, headers=headers)
                if resp.status_code != 200 or _response_too_large(resp, max_bytes):
                    resp.close()
                    return None
                ct = resp.headers.get("content-type", "")
                ext_map = {
                    "jpeg": "jpg", "jpg": "jpg", "png": "png",
                    "gif": "gif", "webp": "webp",
                    "mp4": "mp4", "webm": "webm",
                    "mpeg": "mp3", "mp3": "mp3",
                }
                ext = next((v for k, v in ext_map.items() if k in ct), None)
                if not ext:
                    url_ext = os.path.splitext(urlsplit(url).path)[1].lstrip(".")
                    if url_ext in ext_map.values():
                        ext = url_ext
                if not ext:
                    return None
                p = f"{base_name}_direct.{ext}"
                try:
                    return p if _write_response_limited(resp, p, max_bytes) else None
                finally:
                    resp.close()
            except Exception as e:
                logger.warning("Direct download failed: %s", e)
            return None

        return await utils.run_sync(_fetch)

    # ── Instagram ─────────────────────────────────────────────────────────────

    async def _dl_instagram_instaloader(self, url: str, base_name: str, audio: bool) -> list | None:
        shortcode = _ig_shortcode(url)
        if not shortcode:
            return None

        out_dir = base_name + "_ig"

        def _fetch():
            try:
                import instaloader
            except ImportError:
                return None
            try:
                il = instaloader.Instaloader(
                    download_videos=True,
                    download_video_thumbnails=False,
                    download_geotags=False,
                    download_comments=False,
                    save_metadata=False,
                    compress_json=False,
                    post_metadata_txt_pattern="",
                    dirname_pattern=out_dir,
                    filename_pattern="{owner_username}_{shortcode}_{mediaid}",
                    quiet=True,
                )
                user = self.config["ig_username"]
                pwd  = self.config["ig_password"]
                if user and pwd:
                    try:
                        il.login(user, pwd)
                    except Exception as e:
                        logger.warning("Instagram login failed: %s", e)

                post = instaloader.Post.from_shortcode(il.context, shortcode)
                os.makedirs(out_dir, exist_ok=True)
                il.download_post(post, target=out_dir)

                results = []
                for fname in sorted(os.listdir(out_dir)):
                    fpath = os.path.join(out_dir, fname)
                    if not os.path.isfile(fpath) or os.path.getsize(fpath) == 0:
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in VIDEO_EXTS or ext in IMAGE_EXTS:
                        new_path = f"{base_name}_ig{len(results)}{ext}"
                        os.rename(fpath, new_path)
                        results.append(new_path)

                if not results:
                    return None

                if audio:
                    import subprocess
                    audio_fmt = self.config["audio_format"]
                    audio_results = []
                    for f in results:
                        if _file_type(f) == "video":
                            out_audio = re.sub(r"\.\w+$", f".{audio_fmt}", f)
                            r = subprocess.run(
                                ["ffmpeg", "-i", f, "-vn",
                                 "-acodec", audio_fmt if audio_fmt != "mp3" else "libmp3lame",
                                 "-q:a", "2", "-y", out_audio],
                                capture_output=True, timeout=120
                            )
                            if r.returncode == 0 and os.path.isfile(out_audio):
                                try:
                                    os.remove(f)
                                except Exception:
                                    pass
                                audio_results.append(out_audio)
                            else:
                                audio_results.append(f)
                        else:
                            audio_results.append(f)
                    results = audio_results

                return results
            except Exception as e:
                logger.warning("instaloader failed for %s: %s", shortcode, e)
                return None
            finally:
                # FIX: завжди чистимо тимчасову директорію
                shutil.rmtree(out_dir, ignore_errors=True)

        return await utils.run_sync(_fetch)

    async def _dl_instagram_ytdlp(self, url: str, base_name: str, audio: bool) -> list | None:
        import yt_dlp

        cookies = self._cookies_for(url)
        is_vertical = _is_vertical_url(url)

        def _dl():
            fmt_chain = (
                ["bestaudio/best"] if audio
                else [
                    "bestvideo[ext=mp4][height<=1280]+bestaudio[ext=m4a]" if is_vertical
                    else "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]",
                    "bestvideo+bestaudio", "best[ext=mp4]", "best",
                ]
            )
            postprocessors = self._audio_postprocessor() if audio else []
            for fmt in fmt_chain:
                opts = {
                    "format": fmt,
                    "merge_output_format": "mp4" if not audio else None,
                    "outtmpl": f"{base_name}_ytdlp_%(autonumber)s.%(ext)s",
                    "quiet": True, "no_warnings": True,
                    "noplaylist": False,
                    "ignoreerrors": True,
                    "postprocessors": postprocessors,
                }
                opts.update(self._fast_ytdlp_opts())
                ffmpeg_location = self._ffmpeg_location()
                if ffmpeg_location:
                    opts["ffmpeg_location"] = ffmpeg_location
                if cookies:
                    opts["cookiefile"] = cookies
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])

                    found_files = sorted([
                        p for p in glob.glob(f"{base_name}_ytdlp_*")
                        if os.path.isfile(p) and os.path.getsize(p) > 0
                        and os.path.splitext(p)[1].lower() in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
                    ])
                    if found_files:
                        return found_files
                except Exception as e:
                    logger.warning("Instagram yt-dlp fmt '%s' failed: %s", fmt, e)
                    for p in glob.glob(f"{base_name}_ytdlp_*"):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            return None

        return await utils.run_sync(_dl)

    # ── Pinterest ─────────────────────────────────────────────────────────────

    async def _dl_pinterest(self, url: str, base_name: str) -> list | None:
        import requests

        def _fetch():
            try:
                headers = {"User-Agent": (
                    "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Mobile Safari/537.36"
                )}
                target_url = url
                if "pin.it" in url:
                    r = _safe_requests_get(requests, url, timeout=15, headers=headers)
                    target_url = r.url

                r = _safe_requests_get(requests, target_url, timeout=20, headers=headers)
                if r.status_code != 200:
                    return None
                html = r.text

                for pat in [
                    r'"url"\s*:\s*"(https://v\.pinimg\.com/[^"]+\.mp4[^"]*)"',
                    r'<meta\s+property="og:video:url"\s+content="([^"]+)"',
                    r'<meta\s+property="og:video"\s+content="([^"]+)"',
                ]:
                    m = re.search(pat, html)
                    if m:
                        v_url = m.group(1).replace("\\u002F", "/")
                        try:
                            max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                            vr = _safe_requests_get(requests, v_url, timeout=30, headers=headers, stream=True)
                            p = f"{base_name}_pin.mp4"
                            try:
                                if vr.status_code == 200 and not _response_too_large(vr, max_bytes) and _write_response_limited(vr, p, max_bytes):
                                    return [p]
                            finally:
                                vr.close()
                        except Exception:
                            pass
                        break

                seen = set()
                media_urls = []
                for pat in [
                    r'"orig"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
                    r'"736x"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
                    r'<meta\s+property="og:image"\s+content="([^"]+)"',
                ]:
                    for m in re.finditer(pat, html):
                        img_url = m.group(1).replace("\\u002F", "/")
                        if img_url in seen or not img_url.startswith("http"):
                            continue
                        seen.add(img_url)
                        try:
                            max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                            rc = _safe_requests_get(requests, img_url, timeout=20, headers=headers, stream=True)
                            try:
                                if rc.status_code == 200 and not _response_too_large(rc, max_bytes):
                                    ct = rc.headers.get("content-type", "")
                                    ext = "png" if "png" in ct else "webp" if "webp" in ct else "jpg"
                                    p = f"{base_name}_pin{len(media_urls)}.{ext}"
                                    if _write_response_limited(rc, p, max_bytes):
                                        media_urls.append(p)
                            finally:
                                rc.close()
                        except Exception:
                            pass
                    if media_urls:
                        break

                return media_urls if media_urls else None
            except Exception as e:
                logger.warning("Pinterest fetch failed: %s", e)
                return None

        return await utils.run_sync(_fetch)

    # ── TikTok ────────────────────────────────────────────────────────────────

    async def _dl_tiktok(self, url: str, base_name: str, audio: bool) -> list | None:
        import requests

        def _fetch():
            try:
                res = requests.post(
                    "https://www.tikwm.com/api/",
                    data={"url": url}, timeout=20,
                ).json()
                if res.get("code") == 0:
                    data = res.get("data", {})
                    images = data.get("images") or []
                    if images and not audio:
                        paths = []
                        for i, img_url in enumerate(images):
                            try:
                                max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                                ir = _safe_requests_get(requests, img_url, timeout=30, stream=True)
                                p = f"{base_name}_img{i}.jpg"
                                try:
                                    if ir.status_code == 200 and not _response_too_large(ir, max_bytes) and _write_response_limited(ir, p, max_bytes):
                                        paths.append(p)
                                finally:
                                    ir.close()
                            except Exception as e:
                                logger.warning("TikTok image %d failed: %s", i, e)
                        return paths if paths else None

                    key = "music" if audio else "play"
                    v_url = data.get(key) or data.get("play")
                    if not v_url:
                        return None
                    ext = self.config["audio_format"] if audio else "mp4"
                    max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                    vr = _safe_requests_get(requests, v_url, timeout=30, stream=True)
                    p = f"{base_name}.{ext}"
                    try:
                        return [p] if vr.status_code == 200 and not _response_too_large(vr, max_bytes) and _write_response_limited(vr, p, max_bytes) else None
                    finally:
                        vr.close()
                logger.warning("TikWM API error: code=%s", res.get("code"))
            except Exception as e:
                logger.exception("TikTok failed: %s", e)
            return None

        result = await utils.run_sync(_fetch)
        if result is None:
            r = await self._dl_ytdlp(url, base_name + "_tk", None, audio,
                                      self.config["quality"], True)
            if r:
                result = [r] if isinstance(r, str) else [r]
        if result is None and not audio:
            result = await self._dl_gallery_dl(url, base_name)
        return result

    # ── Twitter/X ─────────────────────────────────────────────────────────────

    async def _dl_twitter_photos(self, url: str, base_name: str) -> list | None:
        import requests
        import yt_dlp

        cookies = self._cookies_for(url)

        def _dl():
            opts = {"quiet": True, "no_warnings": True,
                    "skip_download": True, "ignoreerrors": True}
            if cookies:
                opts["cookiefile"] = cookies
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.warning("Twitter info failed: %s", e)
                return None

            if not info:
                return None

            results = []
            entries = info.get("entries") or [info]
            for i, entry in enumerate(entries):
                if not entry:
                    continue
                formats = entry.get("formats") or []
                has_video = any(f.get("vcodec") not in (None, "none", "") for f in formats)
                if has_video:
                    return None
                thumb = entry.get("thumbnail") or entry.get("url")
                if not thumb:
                    continue
                try:
                    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
                    max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
                    r = _safe_requests_get(requests, thumb, timeout=30, headers=headers, stream=True)
                    p = f"{base_name}_tw{i}.jpg"
                    try:
                        if r.status_code == 200 and not _response_too_large(r, max_bytes) and _write_response_limited(r, p, max_bytes):
                            results.append(p)
                    finally:
                        r.close()
                except Exception as e:
                    logger.warning("Twitter photo %d failed: %s", i, e)

            return results if results else None

        return await utils.run_sync(_dl)

    # ── YouTube ───────────────────────────────────────────────────────────────


    def _yt_cookies_mode(self) -> str:
        mode = str(self.config.get("yt_cookies_mode", "auto") or "auto").strip().lower()
        return mode if mode in {"auto", "always", "never"} else "auto"

    def _yt_browser_cookies_value(self) -> str:
        configured = str(self.config.get("yt_browser_cookies", "") or "").strip()
        profile = self._cookie_manager().firefox_profile
        if not configured or configured == f"firefox:{FIREFOX_PROFILE}":
            return f"firefox:{profile}" if profile else "firefox"
        return configured

    def _youtube_cookie_candidates(self, url: str) -> list[tuple[str | None, bool, str]]:
        cookies = self._cookies_for(url)
        if not _is_youtube_url(url):
            return [(cookies, False, "cookies" if cookies else "anon")]
        mode = self._yt_cookies_mode()
        if mode == "never":
            return [(None, False, "anon")]

        cookie_candidate = (cookies, False, "cookies" if cookies else "anon")
        if mode == "always":
            return [cookie_candidate]

        # The normal pass always uses the cookie file first. Browser cookies
        # are deliberately reserved for the explicit Firefox refresh fallback.
        return [cookie_candidate]

    def _apply_browser_cookies(self, opts: dict, url: str, allow: bool = True) -> None:
        if not allow or not _is_youtube_url(url):
            return
        browser_cookies = _parse_browser_cookies(
            self._yt_browser_cookies_value()
        )
        if browser_cookies:
            opts["cookiesfrombrowser"] = browser_cookies

    def _try_ydl_format_youtube(
        self, url: str, base_name: str, fmt: str,
        audio: bool, cookies: str | None, use_browser_cookies: bool,
        status_msg, loop, player_clients: str | list[str], allow_missing_pot: bool = False
    ) -> str | None:
        import yt_dlp

        audio_fmt = self.config["audio_format"]
        vertical = _is_vertical_url(url)
        extractor_args = self._build_yt_extractor_args(player_clients, allow_missing_pot)

        opts = {
            "format": fmt,
            "merge_output_format": "mp4" if not audio else None,
            "outtmpl": f"{base_name}.%(ext)s",
            "quiet": False, "no_warnings": False,
            "noplaylist": True, "ignoreerrors": False,
            "postprocessors": self._audio_postprocessor() if audio else [],
            "progress_hooks": [self._progress_hook(status_msg, loop)],
            "extractor_args": extractor_args,
            "external_downloader_args": {
                "ffmpeg_i": ["-reconnect", "1", "-reconnect_streamed", "1",
                             "-reconnect_delay_max", "5"],
            },
        }
        opts.update(self._fast_ytdlp_opts())
        ffmpeg_location = self._ffmpeg_location()
        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location
        opts.update(_js_runtime_opts(self._js_runtime))
        if cookies:
            opts["cookiefile"] = cookies
        self._apply_browser_cookies(opts, url, allow=use_browser_cookies)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None

                fsize = info.get("filesize") or info.get("filesize_approx") or 0
                if fsize > 0 and fsize > self.config["max_size"] * 1024 * 1024:
                    _cleanup(base_name)
                    return "TOO_LARGE"
                if not audio and vertical:
                    width, height = _media_dimensions_from_info(info)
                    if width and height and height <= width:
                        logger.info(
                            "Skipping mismatched orientation fmt='%s': %sx%s expected_vertical=%s",
                            fmt, width, height, vertical,
                        )
                        _cleanup(base_name)
                        return None

                requested = ydl.prepare_filename(info)

                # FIX: надійний пошук аудіо після постпроцесингу
                if audio:
                    found = self._find_audio_output(requested, audio_fmt)
                    if found:
                        return found
                    found = _find_file(re.sub(r"\.\w+$", "", requested))
                    if found:
                        return found

                if os.path.isfile(requested) and os.path.getsize(requested) > 0:
                    if not audio and _file_type(requested) == "video" and not _video_has_audio(requested):
                        logger.warning("Downloaded video has no audio, retrying with merged format: %s", requested)
                        _cleanup(base_name)
                        return None
                    return requested

                found = _find_file(base_name)
                if found and not audio and _file_type(found) == "video" and not _video_has_audio(found):
                    logger.warning("Found video has no audio, retrying with merged format: %s", found)
                    _cleanup(base_name)
                    return None
                return found

        except yt_dlp.utils.DownloadError as e:
            client_label = ",".join(player_clients) if isinstance(player_clients, list) else player_clients
            if _is_youtube_auth_error(e):
                logger.warning(
                    "YT auth/POT challenge fmt='%s' client=%s; cookies=%s browser_cookies=%s",
                    fmt, client_label, bool(cookies), use_browser_cookies,
                )
                _cleanup(base_name)
                return "AUTH_REQUIRED"
            logger.warning(
                "YT DownloadError fmt='%s' client=%s: %s",
                fmt, client_label, str(e)[:300],
            )
            _cleanup(base_name)
        except Exception as e:
            client_label = ",".join(player_clients) if isinstance(player_clients, list) else player_clients
            logger.exception("YT error fmt='%s' client=%s: %s", fmt, client_label, e)
            _cleanup(base_name)
        return None

    async def _dl_youtube(
        self, url: str, base_name: str, status_msg, audio: bool, quality: str
    ) -> str | None:
        loop = asyncio.get_event_loop()
        cookie_candidates = self._youtube_cookie_candidates(url)
        vertical = _is_vertical_url(url)

        fmt_chain = (
            ["bestaudio/best", "bestaudio", "best"]
            if audio
            else self._youtube_format_chain(quality, vertical)
        )

        client_profiles = [
            ("web_safari", ["web_safari"], False),
            ("tv_simply", ["tv_simply", "default", "-tv"], False),
            ("default_notv", ["default", "-tv"], False),
            ("android_vr", ["android_vr"], False),
            ("android", ["android"], False),
            ("mweb", ["mweb"], False),
            ("missing_pot", ["default", "ios", "web_embedded", "-tv"], True),
        ]
        saw_auth_required = False
        for cookies, use_browser_cookies, cookie_suffix in cookie_candidates:
            for client_label, clients, allow_missing_pot in client_profiles:
                for fmt in fmt_chain[:4]:
                    result = await utils.run_sync(
                        self._try_ydl_format_youtube,
                        url, f"{base_name}_{cookie_suffix}_{client_label}",
                        fmt, audio, cookies, use_browser_cookies, status_msg, loop, clients, allow_missing_pot
                    )
                    if result == "TOO_LARGE":
                        return result
                    if result == "AUTH_REQUIRED":
                        saw_auth_required = True
                        continue
                    if result:
                        logger.info(
                            "YT OK: cookies=%s client=%s fmt='%s' missing_pot=%s",
                            bool(cookies), clients, fmt, allow_missing_pot,
                        )
                        return result

            # Останній шанс — стандартний yt-dlp без extractor_args
            for fmt in fmt_chain:
                result = await utils.run_sync(
                    self._try_ydl_format,
                    url, f"{base_name}_{cookie_suffix}_default", fmt, audio, cookies, status_msg, loop, False, use_browser_cookies
                )
                if result == "TOO_LARGE":
                    return "TOO_LARGE"
                if result == "AUTH_REQUIRED":
                    saw_auth_required = True
                    continue
                if result:
                    return result

        if saw_auth_required and all(not c and not b for c, b, _ in cookie_candidates):
            return "AUTH_REQUIRED"

        if self.config.get("auto_update_ytdlp", True):
            ok, _ = await self._auto_update_ytdlp(force=True)
            if ok:
                for fmt in fmt_chain[-2:]:
                    result = await utils.run_sync(
                        self._try_ydl_format,
                        url, f"{base_name}_updated", fmt, audio, cookie_candidates[-1][0], status_msg, loop, False, cookie_candidates[-1][1]
                    )
                    if result == "TOO_LARGE":
                        return "TOO_LARGE"
                    if result:
                        return result

        return None

    # ── yt-dlp загальний ──────────────────────────────────────────────────────

    def _try_ydl_format(
        self, url: str, base_name: str, fmt: str,
        audio: bool, cookies: str | None,
        status_msg, loop, vertical: bool = False, use_browser_cookies: bool | None = None
    ) -> str | None:
        import yt_dlp

        audio_fmt = self.config["audio_format"]

        opts = {
            "format": fmt,
            "merge_output_format": "mp4" if not audio else None,
            "outtmpl": f"{base_name}.%(ext)s",
            "quiet": False, "no_warnings": False,
            "noplaylist": True, "ignoreerrors": False,
            "postprocessors": self._audio_postprocessor() if audio else [],
            "progress_hooks": [self._progress_hook(status_msg, loop)] if status_msg else [],
        }
        opts.update(self._fast_ytdlp_opts())
        ffmpeg_location = self._ffmpeg_location()
        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location
        if cookies:
            opts["cookiefile"] = cookies
        self._apply_browser_cookies(
            opts, url, allow=bool(cookies) if use_browser_cookies is None else use_browser_cookies
        )

        u_lower = url.lower()
        if "youtube.com" in u_lower or "youtu.be" in u_lower:
            opts.update(_js_runtime_opts(self._js_runtime))
            opts["extractor_args"] = self._build_yt_extractor_args(
                ["default", "ios", "web_embedded", "-tv"],
                allow_missing_pot=True,
            )

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None

                fsize = info.get("filesize") or info.get("filesize_approx") or 0
                if fsize > 0 and fsize > self.config["max_size"] * 1024 * 1024:
                    _cleanup(base_name)
                    return "TOO_LARGE"
                if not audio and _is_vertical_url(url):
                    width, height = _media_dimensions_from_info(info)
                    if width and height and height <= width:
                        logger.info(
                            "Skipping mismatched YouTube orientation fmt='%s': %sx%s",
                            fmt, width, height,
                        )
                        _cleanup(base_name)
                        return None

                requested = ydl.prepare_filename(info)

                # FIX: надійний пошук аудіо після постпроцесингу
                if audio:
                    found = self._find_audio_output(requested, audio_fmt)
                    if found:
                        return found
                    found = _find_file(re.sub(r"\.\w+$", "", requested))
                    if found:
                        return found

                if os.path.isfile(requested) and os.path.getsize(requested) > 0:
                    if not audio and _file_type(requested) == "video" and not _video_has_audio(requested):
                        logger.warning("Downloaded video has no audio, retrying with merged format: %s", requested)
                        _cleanup(base_name)
                        return None
                    return requested
                found = _find_file(base_name)
                if found and not audio and _file_type(found) == "video" and not _video_has_audio(found):
                    logger.warning("Found video has no audio, retrying with merged format: %s", found)
                    _cleanup(base_name)
                    return None
                return found

        except yt_dlp.utils.DownloadError as e:
            if ("youtube.com" in url.lower() or "youtu.be" in url.lower()) and _is_youtube_auth_error(e):
                logger.warning("YouTube auth/POT challenge url=%s fmt='%s'", url, fmt)
                _cleanup(base_name)
                return "AUTH_REQUIRED"
            logger.warning("DownloadError url=%s fmt='%s': %s", url, fmt, str(e)[:200])
            _cleanup(base_name)
        except Exception as e:
            logger.exception("Error url=%s fmt='%s': %s", url, fmt, e)
            _cleanup(base_name)
        return None

    async def _dl_ytdlp(
        self, url: str, base_name: str, status_msg,
        audio: bool, quality: str, vertical: bool = False
    ) -> str | None:
        loop = asyncio.get_event_loop()
        cookies = self._cookies_for(url)
        chain = (
            ["bestaudio/best", "bestaudio", "best"]
            if audio
            else self._format_chain(quality, vertical)
        )
        for fmt in chain:
            result = await utils.run_sync(
                self._try_ydl_format,
                url, base_name, fmt, audio, cookies, status_msg, loop, vertical
            )
            if result == "TOO_LARGE":
                return "TOO_LARGE"
            if result:
                return result
        if self.config.get("auto_update_ytdlp", True):
            ok, _ = await self._auto_update_ytdlp(force=True)
            if ok:
                for fmt in chain[-2:]:
                    result = await utils.run_sync(
                        self._try_ydl_format,
                        url, f"{base_name}_updated", fmt, audio, cookies, status_msg, loop, vertical
                    )
                    if result == "TOO_LARGE":
                        return "TOO_LARGE"
                    if result:
                        return result

        return None


    def _find_executable(self, configured: str, names: list[str]) -> str | None:
        """Find an executable in config, Hikka venv, PATH and common system paths."""
        candidates: list[str] = []
        if configured:
            candidates.append(configured)
            if os.path.isdir(configured):
                for name in names:
                    candidates.append(os.path.join(configured, name))

        # Prefer Hikka's own virtualenv binary when present. The module runs
        # inside Hikka, and users commonly test yt-dlp successfully through
        # /home/rkbot/hikka/.venv/bin/yt-dlp even when PATH points elsewhere.
        for base in ("/home/rkbot/hikka/.venv/bin",):
            for name in names:
                candidates.append(os.path.join(base, name))

        for name in names:
            found = shutil.which(name)
            if found:
                candidates.append(found)
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
            for name in names:
                candidates.append(os.path.join(base, name))
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _ytdlp_cli_prefix(self) -> list[str]:
        configured = str(self.config.get("yt_dlp_path", "") or "").strip()
        ytdlp = self._find_executable(configured, ["yt-dlp", "yt_dlp"])
        return [ytdlp] if ytdlp else [sys.executable, "-m", "yt_dlp"]

    def _ffmpeg_location(self) -> str | None:
        configured = str(self.config.get("ffmpeg_path", "") or "").strip()
        if configured:
            return configured
        ffmpeg = self._find_executable("", ["ffmpeg"])
        return os.path.dirname(ffmpeg) if ffmpeg else None

    def _tuitube_format_value(self, info: dict, audio: bool) -> tuple[str, str | None]:
        if audio:
            return "bestaudio/best", None
        formats = info.get("formats") or []
        want_vertical = _info_is_vertical(info, _is_vertical_url(info.get("webpage_url") or info.get("original_url") or ""))

        def _is_real_video(fmt: dict) -> bool:
            return (
                bool(fmt.get("format_id"))
                and fmt.get("vcodec") not in (None, "none")
                and not fmt.get("has_drm")
                and fmt.get("ext") not in ("mhtml", "images")
            )

        def _orientation_matches(fmt: dict) -> bool:
            width, height = fmt.get("width"), fmt.get("height")
            if not width or not height:
                return True
            return (height > width) if want_vertical else (width >= height)

        video_formats = [fmt for fmt in formats if _is_real_video(fmt) and _orientation_matches(fmt)]
        if not video_formats:
            video_formats = [fmt for fmt in formats if _is_real_video(fmt)]

        quality = str(self.config.get("quality", "720")).lower().replace("p", "")
        limit = 99999 if quality == "best" else int(quality) if quality.isdigit() else 720
        if want_vertical and limit != 99999:
            limit = {360: 640, 480: 854, 720: 1280, 1080: 1920}.get(limit, limit)
        bounded = [fmt for fmt in video_formats if not fmt.get("height") or int(fmt["height"]) <= limit]
        if bounded:
            video_formats = bounded

        def _rank(fmt: dict) -> tuple:
            codec = str(fmt.get("vcodec") or "")
            ios_codec = codec.startswith(("avc1", "h264"))
            return (ios_codec, int(fmt.get("height") or 0), float(fmt.get("tbr") or 0))

        video_formats.sort(key=_rank)

        # Prefer progressive H.264 files within the configured resolution.
        # This avoids downloading an enormous "best" source merely to encode
        # it later and normally eliminates a second audio request that may hit
        # YouTube POT restrictions.
        for fmt in reversed(video_formats):
            if fmt.get("acodec") not in (None, "none"):
                fmt_id = fmt.get("format_id")
                ext = fmt.get("ext") or "mp4"
                return fmt_id, ext

        for fmt in reversed(video_formats):
            fmt_id = fmt.get("format_id")
            ext = fmt.get("ext") or "mp4"
            return f"{fmt_id}+bestaudio/best", ext
        return "best[ext=mp4]/bestvideo*+bestaudio/best", "mp4"

    def _run_ytdlp_cli_sync(
        self, url: str, base_name: str, audio: bool, browser_retry: bool = False
    ) -> list[str] | str | None:
        cmd = self._ytdlp_cli_prefix()
        browser_cookies = self._yt_browser_cookies_value()
        if browser_retry:
            sudo = self._find_executable("", ["sudo"])
            if not sudo:
                logger.warning("Browser-cookie retry unavailable: sudo not found")
                return None
            cmd = [sudo, "-u", FIREFOX_USER, *cmd]
        cookie_candidates = (
            [(None, True, "browser")]
            if browser_retry
            else self._youtube_cookie_candidates(url)
        )
        saw_auth_required = False

        for cookies, use_browser_cookies, _cookie_suffix in cookie_candidates:
            common = []
            if self.config.get("force_ipv4", False):
                common.append("--force-ipv4")
            if cookies:
                common += ["--cookies", cookies]
            if browser_cookies and use_browser_cookies:
                common += ["--cookies-from-browser", browser_cookies]
            runtime = self._js_runtime or _preferred_js_runtime_arg()
            if runtime:
                common += ["--js-runtimes", runtime]
            ffmpeg_location = self._ffmpeg_location()
            if ffmpeg_location:
                common += ["--ffmpeg-location", ffmpeg_location]

            info_cmd = cmd + common + ["--no-playlist", "--dump-json", "--format-sort=resolution,ext,tbr", url]
            try:
                info_proc = subprocess.run(info_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90, env=_subprocess_env_for_cookie_owner())
            except Exception as e:
                logger.warning("yt-dlp CLI info failed: %s", e)
                continue
            if info_proc.returncode != 0 or not info_proc.stdout.strip():
                info_err = info_proc.stderr or info_proc.stdout
                if _is_youtube_auth_error(info_err):
                    saw_auth_required = True
                    logger.warning("yt-dlp CLI YouTube auth/POT challenge; cookies=%s browser_cookies=%s", bool(cookies), bool(browser_cookies))
                    continue
                logger.warning("yt-dlp CLI info error: %s", info_err[-500:])
                continue
            try:
                import json
                info = json.loads(info_proc.stdout)
            except Exception as e:
                logger.warning("yt-dlp CLI JSON parse failed: %s", e)
                continue
            if info.get("live_status") not in (None, "not_live"):
                logger.warning("Live streams are not supported by CLI fallback: %s", info.get("live_status"))
                continue

            outtmpl = f"{base_name}_cli_%(id)s.%(ext)s"
            dl_cmd = cmd + common + ["--no-playlist", "--newline", "--print", "after_move:filepath", "-o", outtmpl]
            if audio:
                dl_cmd += ["--format", self._tuitube_format_value(info, True)[0], "--extract-audio", "--audio-format", self.config.get("audio_format", "mp3")]
            else:
                fmt, recode = self._tuitube_format_value(info, False)
                dl_cmd += ["--format", fmt]
                if recode:
                    dl_cmd += ["--recode-video", recode]
            max_size = int(self.config.get("max_size", 0) or 0)
            if max_size > 0:
                dl_cmd += ["--max-filesize", f"{max_size}M"]
            try:
                proc = subprocess.run(dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=int(self.config.get("task_timeout", _TASK_TIMEOUT)), env=_subprocess_env_for_cookie_owner())
            except subprocess.TimeoutExpired:
                continue
            except Exception as e:
                logger.warning("yt-dlp CLI download failed: %s", e)
                continue
            if proc.returncode != 0:
                output = proc.stdout or ""
                if "File is larger than max-filesize" in output or "exceeds limit" in output:
                    _cleanup(f"{base_name}_cli")
                    return "TOO_LARGE"
                if _is_youtube_auth_error(output):
                    saw_auth_required = True
                    logger.warning("yt-dlp CLI YouTube auth/POT challenge during download")
                    _cleanup(f"{base_name}_cli")
                    continue
                logger.warning("yt-dlp CLI error: %s", output[-700:])
                _cleanup(f"{base_name}_cli")
                continue
            paths = []
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if os.path.isabs(line) and os.path.isfile(line) and os.path.getsize(line) > 0:
                    paths.append(line)
            if not paths:
                paths = [p for p in glob.glob(f"{base_name}_cli_*") if os.path.isfile(p) and os.path.getsize(p) > 0 and os.path.splitext(p)[1].lower() in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS]
            if paths:
                return sorted(dict.fromkeys(paths))

        return "AUTH_REQUIRED" if saw_auth_required else None

    async def _dl_ytdlp_cli(self, url: str, base_name: str, audio: bool) -> list[str] | str | None:
        if not self.config.get("use_cli_ytdlp", True):
            return None
        return await utils.run_sync(self._run_ytdlp_cli_sync, url, base_name, audio)

    def _refresh_firefox_cookies_sync(self, url: str) -> bool:
        """Open the failed site's origin in Firefox and persist its profile cookies."""
        manager = self._cookie_manager()
        firefox = self._find_executable("", ["firefox-esr", "firefox"])
        if not firefox:
            logger.warning("Firefox cookie refresh unavailable: Firefox not found")
            return False
        if not manager.firefox_profile_valid():
            logger.warning("Firefox profile is not readable/writable or has no cookies.sqlite: %s", manager.firefox_profile)
            return False

        parts = urlsplit(url)
        site_url = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        command = [firefox, "--headless", "--no-remote", "--profile", manager.firefox_profile, site_url]
        if manager.browser_user != getpass.getuser():
            sudo = self._find_executable("", ["sudo"])
            if not sudo:
                logger.warning("Firefox refresh needs sudo to run as %s", manager.browser_user)
                return False
            command = [sudo, "-u", manager.browser_user, *command]
        process = None
        started = False
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, env=_subprocess_env_for_cookie_owner(),
                start_new_session=True,
            )
            time.sleep(FIREFOX_WARMUP_SECONDS)
            if process.poll() is not None and process.returncode != 0:
                logger.warning("Firefox cookie refresh exited with code %s", process.returncode)
                return False
            started = True
        except Exception as e:
            logger.warning("Could not refresh browser cookies for %s: %s", site_url, e)
            return False
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        return started and manager.firefox_has_url(url)

    async def _dl_with_refreshed_browser(
        self, url: str, base_name: str, audio: bool
    ) -> list[str] | str | None:
        """Refresh Firefox state, then make the one final browser-cookie attempt."""
        refreshed = await utils.run_sync(self._refresh_firefox_cookies_sync, url)
        if not refreshed:
            return None
        return await utils.run_sync(
            self._run_ytdlp_cli_sync, url, base_name, audio, True
        )


    def _pip_install_sync(self, packages: list[str], upgrade: bool = False) -> tuple[bool, str]:
        if not packages:
            return True, "nothing to install"
        os.makedirs(COOKIES_DIR, exist_ok=True)
        cmd = [sys.executable, "-m", "pip", "install"]
        if upgrade:
            cmd.append("-U")
        cmd.extend(packages)
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=240
        )
        return p.returncode == 0, p.stdout[-1500:]

    async def _ensure_runtime_dependencies(self) -> tuple[bool, str]:
        def _missing_packages():
            missing = []
            for package, module in PIP_DEPENDENCIES.items():
                try:
                    __import__(module)
                except Exception:
                    missing.append(package)
            return missing

        missing = await utils.run_sync(_missing_packages)
        ok, info = True, "all present"
        if missing:
            ok, info = await utils.run_sync(self._pip_install_sync, missing, False)
            if not ok:
                logger.warning("Dependency install failed for %s: %s", missing, info)
                return ok, info
        if self.config.get("auto_update_ytdlp", True):
            return await self._auto_update_ytdlp()
        return ok, info

    async def _auto_update_ytdlp(self, force: bool = False) -> tuple[bool, str]:
        stamp = os.path.join(COOKIES_DIR, ".yt_dlp_update_stamp")
        if not force and os.path.isfile(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
            return True, "recent"

        try:
            ok, info = await utils.run_sync(self._pip_install_sync, ["yt-dlp"], True)
            if ok:
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
                return True, info
            return False, info
        except Exception as e:
            logger.warning("yt-dlp update failed: %s", e)
            return False, str(e)

    async def _dl_gallery_dl(self, url: str, base_name: str) -> list | None:
        if not self.config.get("use_gallery_dl", True):
            return None
        cookies = self._cookies_for(url)

        def _run():
            try:
                __import__("gallery_dl")
            except Exception:
                if not self.config.get("auto_install_deps", True):
                    return None
                ok, _ = self._pip_install_sync(["gallery-dl"], False)
                if not ok:
                    return None

            out_dir = base_name + "_gallery"
            os.makedirs(out_dir, exist_ok=True)
            args = [
                sys.executable, "-m", "gallery_dl",
                "-D", out_dir,
                "-f", "{category}_{id}_{num}.{extension}",
                "--no-mtime",
            ]
            if cookies:
                args += ["--cookies", cookies]
            args.append(url)

            try:
                proc = subprocess.run(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, timeout=240
                )
                if proc.returncode != 0:
                    logger.warning("gallery-dl failed: %s", proc.stdout[-500:])
                    return None
            except Exception as e:
                logger.warning("gallery-dl failed: %s", e)
                return None

            files = []
            allowed_exts = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
            for root, _, names in os.walk(out_dir):
                for name in names:
                    path = os.path.join(root, name)
                    ext = os.path.splitext(path)[1].lower()
                    if os.path.isfile(path) and os.path.getsize(path) > 0 and ext in allowed_exts:
                        files.append(path)
            return sorted(files) or None

        return await utils.run_sync(_run)

    # ── download dispatcher ───────────────────────────────────────────────────

    def _run_spotdl_sync(self, url: str, base_name: str) -> list[str] | str | None:
        """Download Spotify metadata matches as audio via spotDL.

        Spotify streams are DRM protected, so spotDL resolves the track metadata
        and obtains a matching audio source instead of attempting to bypass DRM.
        """
        out_dir = f"{base_name}_spotify"
        os.makedirs(out_dir, exist_ok=True)
        audio_format = str(self.config.get("audio_format", "mp3")).lower()
        if audio_format not in {"mp3", "m4a", "wav", "opus", "flac", "ogg"}:
            audio_format = "mp3"
        output = os.path.join(out_dir, "{artists} - {title}.{output-ext}")
        cmd = [
            sys.executable, "-m", "spotdl", "download", url,
            "--format", audio_format, "--output", output,
            "--threads", "2",
        ]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                timeout=int(self.config.get("task_timeout", _TASK_TIMEOUT)),
                env=_subprocess_env_for_cookie_owner(),
            )
        except subprocess.TimeoutExpired:
            logger.warning("spotDL timed out for %s", url)
            return None
        except Exception as e:
            logger.warning("spotDL failed to start: %s", e)
            return None
        if proc.returncode != 0:
            logger.warning("spotDL failed: %s", (proc.stdout or "")[-700:])
            return None

        files = []
        max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
        for root, _, names in os.walk(out_dir):
            for name in names:
                path = os.path.join(root, name)
                if (_file_type(path) == "audio" and os.path.getsize(path) > 0):
                    if max_bytes and os.path.getsize(path) > max_bytes:
                        continue
                    files.append(path)
        max_items = max(1, int(self.config.get("playlist_max", 10)))
        return sorted(files)[:max_items] or None

    def _spotify_public_metadata_sync(self, url: str) -> tuple[str, str] | None:
        """Read only the title/artist exposed by Spotify's public share page."""
        try:
            import requests

            response = _safe_requests_get(
                requests,
                url,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                    ),
                    "Accept-Language": "uk,en;q=0.8",
                },
            )
            response.raise_for_status()
            return _spotify_track_metadata(response.text)
        except Exception as e:
            logger.warning("Could not read public Spotify metadata: %s", e)
            return None

    def _songlink_urls_sync(self, url: str) -> list[str]:
        """Return legal storefront/source links discovered by song.link.

        song.link is a resolver, not a file host.  Prefer platforms whose
        public pages yt-dlp can read without relying on YouTube.
        """
        if not self.config.get("songlink_enabled", True):
            return []
        try:
            import requests

            endpoint = "https://api.song.link/v1-alpha.1/links?" + urlencode({"url": url})
            response = _safe_requests_get(requests, endpoint, timeout=20)
            response.raise_for_status()
            links = response.json().get("linksByPlatform") or {}
            preferred = (
                "soundcloud", "bandcamp", "audiomack", "audius",
                "youtubeMusic", "youtube",
            )
            result = []
            for platform in preferred:
                candidate = (links.get(platform) or {}).get("url")
                if candidate and _is_safe_http_url(candidate) and candidate not in result:
                    result.append(candidate)
            return result
        except Exception as e:
            logger.warning("song.link lookup failed: %s", e)
            return []

    def _run_songlink_music_sync(self, url: str, base_name: str):
        for index, candidate in enumerate(self._songlink_urls_sync(url)):
            logger.info("Trying song.link music source: %s", urlsplit(candidate).hostname)
            result = self._run_ytdlp_cli_sync(
                candidate, f"{base_name}_songlink_{index}", True
            )
            if result and result not in ("AUTH_REQUIRED", "TOO_LARGE"):
                return result
            if result == "TOO_LARGE":
                return result
        return None

    def _run_spotify_track_search_sync(
        self, url: str, base_name: str
    ) -> list[str] | str | None:
        """Resolve Spotify metadata and download the closest public audio result.

        Spotify audio is never requested or decrypted.  The share page supplies
        only a search phrase, which yt-dlp resolves through its normal supported
        public sources (YouTube search by default).
        """
        metadata = self._spotify_public_metadata_sync(url)
        if not metadata:
            return None
        title, artist = metadata
        search_terms = " - ".join(part for part in (artist, title) if part)
        if not search_terms:
            return None
        query = f"ytsearch1:{search_terms} audio"
        logger.info("Spotify metadata resolved; searching public source for %r", search_terms)
        return self._run_ytdlp_cli_sync(query, f"{base_name}_spotify_search", True)

    async def _dl_spotify(self, url: str, base_name: str, status_msg):
        await status_msg.edit(self.strings("loading_music"))
        # First ask song.link for the same release on SoundCloud/Bandcamp/etc.
        # This keeps Spotify as metadata only and avoids depending on YouTube.
        result = await utils.run_sync(self._run_songlink_music_sync, url, base_name)
        if result:
            return result

        path = urlsplit(url).path.lower()
        # A single track is more reliable when Spotify is used only as a
        # metadata catalogue and yt-dlp performs the public-source search.
        if "/track/" in path:
            result = await utils.run_sync(
                self._run_spotify_track_search_sync, url, base_name
            )
            if result and result != "AUTH_REQUIRED":
                return result

        # spotDL remains useful for albums/playlists and as a compatibility
        # fallback when Spotify changes its public page metadata.
        return await utils.run_sync(self._run_spotdl_sync, url, base_name)

    def _run_cobalt_sync(self, url: str, base_name: str, audio: bool):
        """Use a Cobalt-compatible API as the final YouTube transport fallback."""
        endpoint = str(self.config.get("cobalt_api_url", "") or "").strip()
        if not endpoint:
            return None
        endpoint = endpoint.rstrip("/") + "/"
        try:
            import requests

            quality = str(self.config.get("quality", "720")).replace("best", "1080")
            payload = {
                "url": url,
                "downloadMode": "audio" if audio else "auto",
                "videoQuality": quality,
                "audioFormat": str(self.config.get("audio_format", "mp3")),
                "filenameStyle": "basic",
            }
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            api_key = str(self.config.get("cobalt_api_key", "") or "").strip()
            if api_key:
                headers["Authorization"] = f"Api-Key {api_key}"
            response = _safe_requests_post(
                requests, endpoint, timeout=30, json_data=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            media_url = data.get("url")
            if data.get("status") not in {"tunnel", "redirect"} or not media_url:
                logger.warning("Cobalt did not return a media URL: %s", data.get("status"))
                return None

            media = _safe_requests_get(requests, media_url, timeout=180, stream=True)
            media.raise_for_status()
            filename = _sanitize_filename(data.get("filename") or "youtube_media")
            ext = os.path.splitext(filename)[1].lower()
            if ext not in AUDIO_EXTS | VIDEO_EXTS:
                ext = ".mp3" if audio else ".mp4"
            path = f"{base_name}_cobalt{ext}"
            max_bytes = int(self.config.get("max_size", 0) or 0) * 1024 * 1024
            if _response_too_large(media, max_bytes):
                media.close()
                return "TOO_LARGE"
            _write_response_limited(media, path, max_bytes)
            media.close()
            return [path]
        except ValueError as e:
            if "max_size" in str(e):
                return "TOO_LARGE"
            logger.warning("Cobalt response rejected: %s", e)
        except Exception as e:
            logger.warning("Cobalt fallback failed: %s", e)
        return None

    async def _dl_cobalt(self, url: str, base_name: str, audio: bool):
        return await utils.run_sync(self._run_cobalt_sync, url, base_name, audio)

    async def _download(
        self, url: str, base_name: str, status_msg, audio: bool
    ) -> list | str | None:
        u = url.lower()

        if _is_spotify_url(url):
            return await self._dl_spotify(url, base_name, status_msg)

        if "tiktok.com" in u:
            return await self._dl_tiktok(url, base_name, audio)

        if ("pinterest.com" in u or "pin.it" in u) and not audio:
            result = await self._dl_pinterest(url, base_name)
            if result:
                return result

        if "instagram.com" in u or "instagr.am" in u or "threads.net" in u:
            result = await self._dl_instagram_instaloader(url, base_name, audio)
            if result:
                return result
            result = await self._dl_instagram_ytdlp(url, base_name, audio)
            if result:
                return result
            if not audio:
                return await self._dl_gallery_dl(url, base_name)
            return None

        if ("x.com" in u or "twitter.com" in u) and not audio:
            photo_result = await self._dl_twitter_photos(url, base_name)
            if photo_result:
                return photo_result

        if any(h in u for h in ("reddit.com", "redd.it", "vimeo.com", "dailymotion.com", "twitch.tv", "facebook.com", "fb.watch", "soundcloud.com")):
            gallery_result = await self._dl_gallery_dl(url, base_name)
            if gallery_result:
                return gallery_result

        if "youtube.com" in u or "youtu.be" in u:
            cli_result = await self._dl_ytdlp_cli(url, base_name, audio)
            youtube_auth_failed = cli_result == "AUTH_REQUIRED"
            if cli_result and cli_result not in ("AUTH_REQUIRED", "TOO_LARGE"):
                return cli_result if isinstance(cli_result, list) else [cli_result]
            if cli_result == "TOO_LARGE":
                return "TOO_LARGE"

            steps = self._quality_steps() if not audio else ["best"]
            max_retries = self.config["retries"]

            for attempt, q in enumerate(steps[: max_retries + 1]):
                if attempt > 0:
                    self._stats["retried"] += 1
                    await status_msg.edit(
                        self.strings("loading_retry").format(
                            attempt, min(max_retries, len(steps) - 1)
                        )
                    )

                result = await self._dl_youtube(
                    url, f"{base_name}_yt{attempt}", status_msg, audio, q
                )
                if result == "TOO_LARGE":
                    if attempt < len(steps) - 1:
                        await status_msg.edit(
                            self.strings("err_size").format(self.config["max_size"])
                        )
                        continue
                    return "TOO_LARGE"
                if result == "AUTH_REQUIRED":
                    youtube_auth_failed = True
                    break
                if result:
                    return [result]

            cli_result = await self._dl_ytdlp_cli(url, base_name, audio)
            if cli_result and cli_result != "AUTH_REQUIRED":
                return cli_result if isinstance(cli_result, list) else [cli_result]
            if cli_result == "AUTH_REQUIRED":
                youtube_auth_failed = True

            # Independent transport: useful when every local yt-dlp client is
            # rejected by YouTube's bot/PO-token checks.
            cobalt_result = await self._dl_cobalt(url, base_name, audio)
            if cobalt_result:
                return cobalt_result
            if youtube_auth_failed:
                await status_msg.edit(self.strings("err_youtube_auth"))
                return "AUTH_REQUIRED"
            return None

        if (not self.config.get("allow_any_url", False)
                and not _is_supported_url(url)):
            logger.info("Skipping unsupported URL with allow_any_url disabled: %s", url)
            return None

        vertical = _is_vertical_url(url)
        steps = self._quality_steps() if not audio else ["best"]
        max_retries = self.config["retries"]

        for attempt, q in enumerate(steps[: max_retries + 1]):
            if attempt > 0:
                self._stats["retried"] += 1
                await status_msg.edit(
                    self.strings("loading_retry").format(
                        attempt, min(max_retries, len(steps) - 1)
                    )
                )
            result = await self._dl_ytdlp(
                url, f"{base_name}_a{attempt}", status_msg, audio, q, vertical
            )
            if result == "TOO_LARGE":
                if attempt < len(steps) - 1:
                    await status_msg.edit(
                        self.strings("err_size").format(self.config["max_size"])
                    )
                    continue
                return "TOO_LARGE"
            if result:
                return [result]

        cli_result = await self._dl_ytdlp_cli(url, base_name, audio)
        if cli_result:
            if cli_result == "TOO_LARGE":
                return "TOO_LARGE"
            return cli_result

        gallery_result = await self._dl_gallery_dl(url, base_name)
        if gallery_result:
            return gallery_result

        return None

    # ── transcript ────────────────────────────────────────────────────────────

    async def _get_transcript(self, url: str) -> tuple[str, str] | None:
        import yt_dlp
        import requests

        cookies = self._cookies_for(url)
        lang = self.config.get("transcript_lang", "uk")

        def _lang_candidates() -> list[str]:
            ordered = []
            for candidate in (lang, "en", "uk", "ru"):
                if candidate and candidate not in ordered:
                    ordered.append(candidate)
            return ordered

        def _pick_caption_track(info: dict) -> dict | None:
            requested = _lang_candidates()
            sources = (info.get("subtitles") or {}, info.get("automatic_captions") or {})
            for captions in sources:
                if not captions:
                    continue
                keys = list(captions)
                ordered_keys = []
                for wanted in requested:
                    ordered_keys.extend(
                        key for key in keys
                        if key == wanted or key.startswith(f"{wanted}-")
                    )
                ordered_keys.extend(key for key in keys if key not in ordered_keys)
                for key in ordered_keys:
                    tracks = captions.get(key) or []
                    if not tracks:
                        continue
                    for track in tracks:
                        if track.get("ext") == "vtt" and track.get("url"):
                            return track
                    for track in tracks:
                        if track.get("url"):
                            return track
            return None

        def _fetch():
            opts = {
                "quiet": True, "no_warnings": True,
                "skip_download": True,
                "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": _lang_candidates(),
                "subtitlesformat": "vtt",
            }
            if cookies:
                opts["cookiefile"] = cookies

            if "youtube.com" in url or "youtu.be" in url:
                opts.update(_js_runtime_opts(self._js_runtime))
                opts["extractor_args"] = self._build_yt_extractor_args(
                    ["default", "ios", "web_embedded", "-tv"],
                    allow_missing_pot=True,
                )

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                title = _sanitize_filename(info.get("title", "Video"))
                track = _pick_caption_track(info)
                if not track:
                    return None

                headers = {"User-Agent": "Mozilla/5.0"}
                resp = _safe_requests_get(requests, track["url"], headers=headers, timeout=15)
                resp.raise_for_status()
                text = _parse_vtt_text(resp.text)
                return (title, text) if text.strip() else None
            except Exception as e:
                logger.warning("Transcript fetch failed: %s", e)
                return None

        return await utils.run_sync(_fetch)

    # ── send ──────────────────────────────────────────────────────────────────

    async def _send(self, message, path: str, caption: str, force_document: bool = False):
        sent = await message.client.send_file(
            message.chat_id, path,
            reply_to=message.id, caption=caption,
            parse_mode="html",
            force_document=force_document,
            part_size_kb=512,
        )
        ad = self.config["auto_delete"]
        if ad > 0:
            async def _del(m=sent, d=ad):
                await asyncio.sleep(d)
                try:
                    await m.delete()
                except Exception:
                    pass
            asyncio.ensure_future(_del())
        return sent

    async def _send_album(self, message, paths: list[str], caption: str):
        """
        Надсилає файли як grouped media (альбом) в Telegram.
        Групує фото окремо від відео (Telegram не підтримує мікс).
        MAX_ALBUM=10 — ліміт Telegram.
        """
        valid = [p for p in paths
                 if isinstance(p, str) and os.path.isfile(p) and os.path.getsize(p) > 0]
        if not valid:
            return

        if len(valid) == 1:
            ftype = _file_type(valid[0])
            await self._send(message, valid[0], caption, force_document=(ftype == "other"))
            return

        images    = [p for p in valid if _file_type(p) == "image"]
        non_images = [p for p in valid if _file_type(p) != "image"]

        groups: list[list[str]] = []
        if images:
            groups.append(images)
        if non_images:
            groups.append(non_images)

        first_group = True
        MAX_ALBUM = 10

        for group in groups:
            for chunk_start in range(0, len(group), MAX_ALBUM):
                chunk = group[chunk_start: chunk_start + MAX_ALBUM]
                # FIX: caption для першого файлу першої групи, решта — порожній рядок
                chunk_caption = caption if (first_group and chunk_start == 0) else ""
                first_group = False

                if len(chunk) == 1:
                    ftype = _file_type(chunk[0])
                    try:
                        await message.client.send_file(
                            message.chat_id, chunk[0],
                            reply_to=message.id,
                            caption=chunk_caption,
                            parse_mode="html",
                            force_document=(ftype == "other"),
                            part_size_kb=512,
                        )
                    except Exception as e:
                        logger.warning("Single file send failed: %s", e)
                    continue

                # FIX: captions_list завжди рівної довжини з chunk
                captions_list = [chunk_caption] + [""] * (len(chunk) - 1)
                try:
                    await message.client.send_file(
                        message.chat_id,
                        chunk,
                        reply_to=message.id,
                        caption=captions_list,
                        parse_mode="html",
                        part_size_kb=512,
                    )
                except Exception as e:
                    logger.warning("Album send failed, trying individually: %s", e)
                    for i, p in enumerate(chunk):
                        ftype = _file_type(p)
                        # FIX: перший файл fallback отримує caption, решта — порожній рядок
                        fb_caption = captions_list[i] if i < len(captions_list) else ""
                        try:
                            await message.client.send_file(
                                message.chat_id, p,
                                reply_to=message.id,
                                caption=fb_caption,
                                parse_mode="html",
                                force_document=(ftype == "other"),
                                part_size_kb=512,
                            )
                        except Exception as e2:
                            logger.warning("Single file send failed: %s", e2)

    @staticmethod
    def _telegram_audio_metadata(candidate) -> tuple[str, str] | None:
        """Return Telegram's embedded title/performer for an audio message."""
        document = getattr(candidate, "document", None)
        if document is None:
            media = getattr(candidate, "media", None)
            document = getattr(media, "document", None)
        if document is None:
            return None

        mime = str(getattr(document, "mime_type", "") or "").lower()
        title = artist = filename = ""
        has_audio_attribute = False
        for attribute in getattr(document, "attributes", []) or []:
            if hasattr(attribute, "title") or hasattr(attribute, "performer"):
                has_audio_attribute = True
                title = str(getattr(attribute, "title", "") or "").strip()
                artist = str(getattr(attribute, "performer", "") or "").strip()
            attribute_filename = str(getattr(attribute, "file_name", "") or "").strip()
            if attribute_filename:
                filename = attribute_filename

        extension = os.path.splitext(filename)[1].lower()
        if not (mime.startswith("audio/") or has_audio_attribute or extension in AUDIO_EXTS):
            return None

        # Telegram files without ID3 tags often still have a useful filename.
        if not title and filename:
            title = os.path.splitext(filename)[0]
        return (title or "Без назви", artist or "Невідомий виконавець")

    @staticmethod
    def _music_message_link(source, entity, message_id: int) -> str:
        """Build a stable post link without resolving or downloading the file."""
        username = str(getattr(entity, "username", "") or "").strip("@")
        if username:
            return f"https://t.me/{username}/{message_id}"
        channel_id = str(getattr(entity, "id", "") or "")
        return f"https://t.me/c/{channel_id}/{message_id}" if channel_id else str(source)

    async def _resolve_music_channel(self, source):
        """Resolve marked/bare IDs and invite links, even without entity cache."""
        source_text = str(source)
        invite = re.fullmatch(
            r"(?:https?://(?:www\.)?t\.me/)?\+([A-Za-z0-9_-]+)", source_text, re.I
        )
        if invite:
            result = await self._client(CheckChatInviteRequest(invite.group(1)))
            chat = getattr(result, "chat", None)
            if chat is None:
                raise ValueError("the account has not joined this invite-link channel")
            return chat

        try:
            return await self._client.get_entity(source)
        except (ValueError, TypeError):
            # ``get_entity(-100...)`` needs an access hash from Telethon's
            # session cache. Dialog iteration refreshes that cache and also
            # lets us match private channels by their unmarked ``entity.id``.
            if not isinstance(source, int):
                raise
            wanted = int(str(abs(source)).removeprefix("100"))
            async for dialog in self._client.iter_dialogs():
                entity = getattr(dialog, "entity", None)
                if int(getattr(entity, "id", 0) or 0) == wanted:
                    return entity
            raise

    async def _index_music_channels(self, sources=None) -> int:
        """Index Telegram audio metadata; media bytes never leave Telegram."""
        sources = sources or _parse_music_channels(self.config.get("music_channels", []))
        if not self._client:
            return 0
        async with self._music_index_lock:
            existing = self.get("music_index", []) or []
            by_title = {
                item.get("key"): item for item in existing
                if isinstance(item, dict) and item.get("key")
            }
            for source in sources:
                source_key = str(source)
                # Re-indexing a channel replaces its stale locations while
                # preserving copies of the same title found in other channels.
                for track in by_title.values():
                    track["locations"] = [
                        loc for loc in track.get("locations", [])
                        if str(loc.get("source")) != source_key
                    ]
                try:
                    entity = await self._resolve_music_channel(source)
                    # Do not rely on InputMessagesFilterMusic here. Telegram's
                    # UI can count an audio file while the search API omits it
                    # (notably files with a generic MIME type or missing ID3
                    # attributes). Walking channel history and inspecting each
                    # document locally makes the index agree with the channel.
                    async for candidate in self._client.iter_messages(entity):
                        metadata = self._telegram_audio_metadata(candidate)
                        if not metadata:
                            continue
                        title, artist = metadata
                        key = _normalize_music_text(title)
                        if not key:
                            continue
                        track = by_title.setdefault(key, {
                            "key": key, "title": title, "artist": artist,
                            "locations": [],
                        })
                        if track.get("artist") == "Невідомий виконавець" and artist:
                            track["artist"] = artist
                        location = {
                            "source": source_key,
                            "message_id": int(candidate.id),
                            "link": self._music_message_link(source, entity, candidate.id),
                        }
                        if not any(
                            loc.get("message_id") == location["message_id"]
                            and str(loc.get("source")) == source_key
                            for loc in track["locations"]
                        ):
                            track["locations"].append(location)
                except Exception as e:
                    logger.warning("Music indexing failed in %s: %s", source, e)
            index = [track for track in by_title.values() if track.get("locations")]
            index.sort(key=lambda item: _normalize_music_text(item.get("title", "")))
            self.set("music_index", index)
            return len(index)

    def _search_music_index(self, query: str) -> list[dict]:
        normalized = _normalize_music_text(query)
        message_id = int(query) if str(query).strip().isdigit() else None
        ranked = []
        for track in self.get("music_index", []) or []:
            title = _normalize_music_text(track.get("title", ""))
            artist = _normalize_music_text(track.get("artist", ""))
            id_match = message_id is not None and any(
                loc.get("message_id") == message_id for loc in track.get("locations", [])
            )
            if id_match:
                score = 100
            elif normalized and normalized == artist:
                score = 80
            elif normalized and normalized == title:
                score = 70
            elif normalized and (normalized in title or normalized in artist):
                score = 50
            else:
                words = normalized.split()
                score = sum(word in f"{title} {artist}" for word in words) * 10
                if not score:
                    continue
            ranked.append((score, track))
        return [track for _, track in sorted(ranked, key=lambda item: -item[0])]

    async def _send_indexed_music(self, target, track: dict):
        """Send Telegram media as a new message, without a forward attribution."""
        for location in track.get("locations", []):
            try:
                entity = await self._resolve_music_channel(location["source"])
                candidate = await self._client.get_messages(
                    entity, ids=int(location["message_id"])
                )
                if candidate and candidate.media:
                    await self._client.send_file(target, candidate.media)
                    self._stats["total"] += 1
                    self._stats["ok"] += 1
                    self._stats["audio"] += 1
                    self._stats["today"] += 1
                    self._stats["platforms"]["Telegram"] += 1
                    return True
            except Exception as e:
                logger.warning("Indexed music location is unavailable: %s", e)
        return False

    async def _music_button_callback(self, call, track_key: str):
        track = next((item for item in self.get("music_index", []) or []
                      if item.get("key") == track_key), None)
        if not track:
            return await call.answer("Трек більше недоступний", show_alert=True)
        target = call.form.get("chat")
        if not await self._send_indexed_music(target, track):
            return await call.answer("Файл більше недоступний", show_alert=True)
        await call.answer("Надіслано")

    def _music_buttons(self, tracks: list[dict]) -> list:
        buttons = [{
            "text": f"{item.get('artist', '—')} — {item.get('title', '—')}"[:64],
            "callback": self._music_button_callback,
            "args": (item["key"],),
        } for item in tracks]
        return utils.chunks(buttons, 2)

    async def _find_music_in_channels(self, query: str):
        """Compatibility fallback for catalogues created by older versions."""
        sources = _parse_music_channels(self.config.get("music_channels", []))
        if not sources or not self._client:
            return None

        normalized_query = _normalize_music_text(query)
        query_words = set(normalized_query.split())
        best = None
        best_score = 0
        limit = max(1, min(100, int(self.config.get("music_search_limit", 25) or 25)))
        for source in sources:
            try:
                entity = await self._resolve_music_channel(source)
                seen = set()
                for search_query in _music_search_queries(query):
                    async for candidate in self._client.iter_messages(
                        entity, search=search_query, filter=InputMessagesFilterMusic,
                        limit=limit,
                    ):
                        candidate_id = getattr(candidate, "id", None)
                        if candidate_id in seen:
                            continue
                        seen.add(candidate_id)
                        metadata = self._telegram_audio_metadata(candidate)
                        if not metadata:
                            continue
                        title, artist = metadata
                        searchable = _normalize_music_text(
                            f"{title} {artist} {getattr(candidate, 'raw_text', '')}"
                        )
                        matched = sum(word in searchable for word in query_words)
                        if not matched:
                            continue
                        score = matched * 10
                        if normalized_query and normalized_query in searchable:
                            score += 5
                        if score > best_score:
                            best = (candidate, title, artist)
                            best_score = score
                        if query_words and matched == len(query_words):
                            return best
            except Exception as e:
                logger.warning("Music search failed in %s: %s", source, e)
        return best

    async def _send_channel_music(self, message, status, query: str) -> bool:
        await status.edit(self.strings("searching_music"))
        found = await self._find_music_in_channels(query)
        if not found:
            return False
        candidate, title, artist = found
        caption = self.strings("music_found").format(
            title=utils.escape_html(title), artist=utils.escape_html(artist)
        )
        await message.client.send_file(
            message.chat_id, candidate.media, reply_to=message.id,
            caption=caption, parse_mode="html",
        )
        self._stats["total"] += 1
        self._stats["ok"] += 1
        self._stats["audio"] += 1
        self._stats["today"] += 1
        self._stats["platforms"]["Telegram"] += 1
        await status.delete()
        return True

    # ── notify ────────────────────────────────────────────────────────────────

    async def _notify(self, platform: str, url: str):
        if not self.config["notify_dm"] or not self._client:
            return
        try:
            await self._client.send_message(
                "me", f"<b>✅ [{platform}]</b> <code>{url}</code>", parse_mode="html"
            )
        except Exception:
            pass

    # ── playlist ──────────────────────────────────────────────────────────────

    async def _dl_playlist(self, url: str, status_msg, message, audio: bool):
        import yt_dlp

        cookies = self._cookies_for(url)
        max_v = self.config["playlist_max"]

        def _info():
            opts = {
                "quiet": True, "extract_flat": True,
                "noplaylist": False, "playlistend": max_v,
            }
            if cookies:
                opts["cookiefile"] = cookies
            if "youtube.com" in url or "youtu.be" in url:
                opts.update(_js_runtime_opts(self._js_runtime))
                opts["extractor_args"] = self._build_yt_extractor_args(
                    ["default", "ios", "web_embedded", "-tv"],
                    allow_missing_pot=True,
                )
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await utils.run_sync(_info)
        except Exception as e:
            logger.exception("Playlist info error: %s", e)
            await status_msg.edit(self.strings("err_file"))
            return

        entries = (info.get("entries") or [])[:max_v]
        if not entries:
            await status_msg.edit(self.strings("err_file"))
            return

        total = len(entries)
        ok = 0
        self._stats["playlists"] += 1

        for idx, entry in enumerate(entries, 1):
            v_url = entry.get("url") or entry.get("webpage_url") or ""
            if not v_url.startswith("http"):
                vid = entry.get("id", "")
                if not vid:
                    continue
                v_url = f"https://www.youtube.com/watch?v={vid}"
            v_url = _normalize_youtube_url(v_url)
            await status_msg.edit(self.strings("loading_playlist").format(idx, total))

            base = f"plvid_{os.urandom(3).hex()}"
            result = None
            try:
                result = await self._download(v_url, base, status_msg, audio)
                if result in (None, "TOO_LARGE"):
                    self._stats["err"] += 1
                    continue

                files = result if isinstance(result, list) else [result]
                valid = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > 0]
                if not valid:
                    self._stats["err"] += 1
                    continue
                for i, path in enumerate(valid):
                    if _file_type(path) == "video":
                        path = await self._normalize_video_container(path)
                        if len(valid) == 1:
                            path = await self._maybe_fix_orientation(path, status_msg)
                        valid[i] = path

                raw_title = entry.get("title") or f"Video {idx}"
                cap = self.strings("caption_playlist").format(
                    title=_sanitize_filename(raw_title), idx=idx, total=total
                )
                await self._send_album(message, valid, cap)

                ok += 1
                self._stats["ok"] += 1
                self._stats["today"] += 1
                self._stats["platforms"]["YouTube"] += 1
                await asyncio.sleep(1.5)
            except Exception:
                logger.exception("Playlist item %s error", idx)
                self._stats["err"] += 1
            finally:
                for f in (result if isinstance(result, list) else [result] if result else []):
                    try:
                        if isinstance(f, str) and os.path.isfile(f):
                            os.remove(f)
                    except Exception:
                        pass
                _cleanup(base)

        await status_msg.edit(self.strings("playlist_done").format(ok=ok, total=total))
        await asyncio.sleep(3)
        try:
            await status_msg.delete()
        except Exception:
            pass

    # ── main process ──────────────────────────────────────────────────────────

    async def _process(self, url: str, message, status_msg, audio_override: bool = False):
        # Music links always produce an audio file; users need not toggle the
        # module-wide video/audio mode before sharing a Spotify/SoundCloud URL.
        audio    = bool(audio_override or _is_music_url(url) or self.config["audio_mode"])
        platform = self._platform(url)
        self._stats["total"] += 1
        self._last_dl = time.time()

        if self._is_playlist(url):
            if not self.config["playlist_enabled"]:
                await status_msg.edit(self.strings("err_playlist_off"))
                await asyncio.sleep(5)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                return
            await self._dl_playlist(url, status_msg, message, audio)
            return

        spotify_collection = _is_spotify_url(url) and re.search(
            r"/(?:playlist|album|artist|show)/", urlsplit(url).path, re.I
        )
        if spotify_collection and not self.config["playlist_enabled"]:
            await status_msg.edit(self.strings("err_playlist_off"))
            await asyncio.sleep(5)
            try:
                await status_msg.delete()
            except Exception:
                pass
            return

        base = f"media_{os.urandom(3).hex()}"
        result = None
        send_ok = False
        try:
            result = await self._download(url, base, status_msg, audio)

            if (result is None and (self.config.get("allow_any_url", False)
                    or _is_supported_url(url))):
                await status_msg.edit(self.strings("loading_photo"))
                direct = await self._try_direct(url, base)
                if direct:
                    result = [direct]

            # Firefox is a session refresh service, not a generic downloader.
            # Start it only after yt-dlp explicitly classified an auth failure.
            if result == "AUTH_REQUIRED":
                browser_result = await self._dl_with_refreshed_browser(
                    url, f"{base}_browser", audio
                )
                if browser_result == "TOO_LARGE":
                    result = "TOO_LARGE"
                elif browser_result and browser_result != "AUTH_REQUIRED":
                    result = (
                        browser_result if isinstance(browser_result, list)
                        else [browser_result]
                    )
                else:
                    result = None

            if result == "TOO_LARGE":
                self._stats["err"] += 1
                await status_msg.edit(self.strings("err_size_final"))
                return

            if isinstance(result, list):
                valid = [f for f in result
                         if isinstance(f, str) and os.path.isfile(f) and os.path.getsize(f) > 0]
                if not valid:
                    self._stats["err"] += 1
                    await status_msg.edit(self.strings("err_file"))
                    return

                for i, path in enumerate(valid):
                    if _file_type(path) == "video":
                        path = await self._normalize_video_container(path)
                        if len(valid) == 1:
                            path = await self._maybe_fix_orientation(path, status_msg)
                        valid[i] = path

                all_images = all(_file_type(f) == "image" for f in valid)
                all_audio  = all(_file_type(f) == "audio" for f in valid)

                if all_images:
                    cap = self.strings("caption_photo")
                    self._stats["photos"] += len(valid)
                elif all_audio or audio:
                    cap = self.strings("caption_audio")
                    self._stats["audio"] += len(valid)
                else:
                    cap = self.strings("caption_video")

                await self._send_album(message, valid, cap)
                send_ok = True

                self._stats["ok"] += 1
                self._stats["today"] += 1
                self._stats["platforms"][platform] += 1
                await self._notify(platform, url)
            else:
                logger.warning("All methods failed for url=%s", url)
                self._stats["err"] += 1
                await status_msg.edit(self.strings("err_file"))

        except asyncio.TimeoutError:
            self._stats["err"] += 1
            self._stats["timeouts"] += 1
            try:
                await status_msg.edit(self.strings("err_timeout"))
            except Exception:
                pass
        except Exception:
            self._stats["err"] += 1
            logger.exception("Process error for url=%s", url)
            try:
                await status_msg.edit("<b>❌ Помилка. Дивись лог.</b>")
            except Exception:
                pass
        finally:
            if send_ok:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            else:
                await asyncio.sleep(3)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            yt_clients = ["tv", "web_safari", "mweb", "default"]
            all_bases = (
                [base]
                + [f"{base}_yt{i}" for i in range(self.config["retries"] + 1)]
                + [f"{base}_a{i}"  for i in range(self.config["retries"] + 1)]
                + [f"{base}_{c}"   for c in yt_clients]
                + [f"{base}_tk"]
                + [f"{base}_cli"]
                + [f"{base}_browser_cli"]
            )
            for b in all_bases:
                _cleanup(b)
            for f in (result if isinstance(result, list) else [result] if result else []):
                try:
                    if isinstance(f, str) and os.path.isfile(f):
                        os.remove(f)
                except Exception:
                    pass

    # ── watcher ───────────────────────────────────────────────────────────────

    @loader.watcher(only_messages=True)
    async def watcher(self, message):
        if not self.config["enabled"]:
            return
        if not self._is_allowed(message):
            return
        if self._is_banned(message):
            return

        text = getattr(message, "raw_text", "") or ""
        if not text or text.startswith("."):
            return
        url = self._extract_url(text)
        if not url:
            return
        url = self._normalize(url)
        if (not self.config.get("allow_any_url", False)
                and not _is_supported_url(url)):
            return

        cd = self._cooldown_left()
        if cd:
            try:
                m = await message.reply(self.strings("err_cooldown").format(cd))
                await asyncio.sleep(5)
                await m.delete()
            except Exception:
                pass
            return

        if self._limit_reached():
            try:
                m = await message.reply(self.strings("err_limit").format(self.config["daily_limit"]))
                await asyncio.sleep(5)
                await m.delete()
            except Exception:
                pass
            return

        if self._queue is None:
            return

        qsize = self._queue.qsize()
        if qsize >= self.config["queue_max"]:
            try:
                m = await message.reply(self.strings("err_queue_full").format(self.config["queue_max"]))
                await asyncio.sleep(5)
                await m.delete()
            except Exception:
                pass
            return

        try:
            status = await message.reply(
                self.strings("queue_pos").format(pos=qsize + 1) if qsize > 0
                else self.strings("loading")
            )
        except Exception:
            return

        await self._queue.put(self._process(url, message, status))

    # ── commands ──────────────────────────────────────────────────────────────

    @loader.command()
    async def vdl(self, message):
        """Перемкнути автозавантажувач"""
        self.config["enabled"] = not self.config["enabled"]
        await utils.answer(
            message,
            self.strings("toggled_on" if self.config["enabled"] else "toggled_off")
        )

    @loader.command()
    async def vdldl(self, message):
        """Ручне завантаження: .vdldl [URL або reply з посиланням]"""
        args = utils.get_args_raw(message).strip()
        url = self._extract_url(args) if args else None

        if not url:
            reply = await message.get_reply_message()
            if reply and reply.raw_text:
                url = self._extract_url(reply.raw_text)

        if not url:
            return await utils.answer(message, self.strings("dl_no_url"))

        url = self._normalize(url)

        if self._queue is None:
            return

        qsize = self._queue.qsize()
        if qsize >= self.config["queue_max"]:
            return await utils.answer(
                message,
                self.strings("err_queue_full").format(self.config["queue_max"])
            )

        status = await utils.answer(
            message,
            self.strings("dl_started").format(url=url)
        )
        await self._queue.put(self._process(url, message, status))

    @loader.command()
    async def vdlmusic(self, message):
        """Знайти музику: .vdlmusic [назва, автор або URL]"""
        args = utils.get_args_raw(message).strip()
        url = self._extract_url(args) if args else None
        if not url:
            reply = await message.get_reply_message()
            if reply and reply.raw_text:
                url = self._extract_url(reply.raw_text)
                if not args:
                    args = reply.raw_text.strip()
        if not url:
            if not args:
                return await utils.answer(message, self.strings("music_no_query"))
            if not _parse_music_channels(self.config.get("music_channels", [])):
                return await utils.answer(message, self.strings("music_no_channels"))
            matches = self._search_music_index(args)
            if matches:
                normalized = _normalize_music_text(args)
                artist_matches = [item for item in matches if
                                  _normalize_music_text(item.get("artist", "")) == normalized]
                choices = artist_matches or matches
                if len(choices) > 1:
                    heading = self.strings("music_choose").format(
                        artist=utils.escape_html(choices[0].get("artist", args))
                    ) if artist_matches else self.strings("music_choose_match")
                    await self.inline.form(
                        heading, message, reply_markup=self._music_buttons(choices)
                    )
                    return
                if await self._send_indexed_music(message.chat_id, choices[0]):
                    await message.delete()
                    return
            status = await utils.answer(message, self.strings("searching_music"))
            try:
                if not await self._send_channel_music(message, status, args):
                    await status.edit(self.strings("music_not_found"))
            except Exception:
                logger.exception("Telegram music search failed")
                await status.edit(self.strings("music_not_found"))
            return
        url = self._normalize(url)
        if self._queue is None:
            return
        if self._queue.qsize() >= self.config["queue_max"]:
            return await utils.answer(
                message, self.strings("err_queue_full").format(self.config["queue_max"])
            )
        status = await utils.answer(message, self.strings("loading_music"))
        await self._queue.put(self._process(url, message, status, audio_override=True))

    @loader.command()
    async def vdlchannels(self, message):
        """Керувати каналами: .vdlchannels add/rm/clear [@channel ...]"""
        raw = utils.get_args_raw(message).strip()
        action, _, value = raw.partition(" ")
        action = action.casefold()
        current = _parse_music_channels(self.config.get("music_channels", []))

        if not raw or action in {"list", "ls"}:
            if not current:
                return await utils.answer(message, self.strings("music_channels_empty"))
            items = "\n".join(f"• <code>{utils.escape_html(str(item))}</code>" for item in current)
            return await utils.answer(message, self.strings("music_channels_list").format(items))
        if action == "clear":
            self.config["music_channels"] = []
            self.set("music_index", [])
            return await utils.answer(
                message, self.strings("music_channels_removed").format(
                    removed=len(current), total=0
                )
            )
        if action not in {"add", "rm", "remove", "del"} or not value.strip():
            return await utils.answer(message, self.strings("music_channels_usage"))

        requested = _parse_music_channels(value)
        if action == "add":
            updated = current + [item for item in requested if item not in current]
            changed = len(updated) - len(current)
            self.config["music_channels"] = updated
            text = self.strings("music_channels_added").format(
                added=changed, total=len(updated)
            )
        else:
            updated = [item for item in current if item not in requested]
            changed = len(current) - len(updated)
            self.config["music_channels"] = updated
            text = self.strings("music_channels_removed").format(
                removed=changed, total=len(updated)
            )
            removed = {str(item) for item in requested}
            index = []
            for track in self.get("music_index", []) or []:
                track["locations"] = [loc for loc in track.get("locations", [])
                                      if str(loc.get("source")) not in removed]
                if track["locations"]:
                    index.append(track)
            self.set("music_index", index)
        status = await utils.answer(message, text)
        if action == "add" and requested:
            await status.edit(self.strings("music_indexing"))
            try:
                total = await self._index_music_channels(requested)
                await status.edit(self.strings("music_indexed").format(total=total))
            except Exception:
                logger.exception("Could not update music index")
                await status.edit(self.strings("music_index_failed"))

    @loader.command()
    async def vdlaudio(self, message):
        """Перемкнути аудіо-режим"""
        self.config["audio_mode"] = not self.config["audio_mode"]
        await utils.answer(
            message,
            self.strings("audio_on" if self.config["audio_mode"] else "audio_off")
        )

    @loader.command()
    async def vdlq(self, message):
        """Якість: .vdlq [360/480/720/1080/best]"""
        args = utils.get_args_raw(message).strip().lower()
        if args not in {"360", "480", "720", "1080", "best"}:
            return await utils.answer(
                message,
                f"<b>Поточна: <code>{self.config['quality']}</code>\n"
                f"Доступні: 360, 480, 720, 1080, best</b>"
            )
        self.config["quality"] = args
        await utils.answer(message, f"<b>✅ Якість: <code>{args}</code></b>")

    @loader.command()
    async def vdlset(self, message):
        """Налаштування: .vdlset [параметр] [значення]"""
        args = utils.get_args_raw(message).split()
        if len(args) != 2:
            return await utils.answer(
                message,
                "<b>Параметри:</b>\ncooldown, limit, size, auto_delete,\n"
                "retries, queue_max, notify_dm,\n"
                "workers (1-4 паралельних завантажень),\n"
                "fix_orientation, playlist, playlist_max,\n"
                "audio_format (mp3/m4a/wav/opus/flac),\n"
                "timeout (сек, таймаут завдання),\n"
                "cli, any_url, ipv4 (0/1),\n"
                "yt_browser, yt_cookies_mode"
            )
        key, raw = args[0].lower(), args[1]
        mapping = {
            "cooldown":        ("cooldown",         int,  "сек"),
            "limit":           ("daily_limit",       int,  "на день"),
            "size":            ("max_size",          int,  "МБ"),
            "auto_delete":     ("auto_delete",       int,  "сек"),
            "retries":         ("retries",           int,  "спроб"),
            "queue_max":       ("queue_max",         int,  "завдань"),
            "workers":         ("queue_workers",     int,  "воркерів"),
            "notify_dm":       ("notify_dm",         bool, ""),
            "fix_orientation": ("fix_orientation",   bool, ""),
            "playlist":        ("playlist_enabled",  bool, ""),
            "playlist_max":    ("playlist_max",      int,  "відео"),
            "timeout":         ("task_timeout",      int,  "сек"),
            "cli":             ("use_cli_ytdlp",    bool, ""),
            "any_url":         ("allow_any_url",    bool, ""),
            "ipv4":            ("force_ipv4",       bool, ""),
        }
        if key == "yt_browser":
            self.config["yt_browser_cookies"] = raw.strip() or "firefox"
            browser = utils.escape_html(self.config["yt_browser_cookies"])
            return await utils.answer(message, f"<b>✅ yt_browser = <code>{browser}</code></b>")

        if key == "yt_cookies_mode":
            mode = raw.strip().lower()
            if mode not in {"auto", "always", "never"}:
                return await utils.answer(message, "<b>❌ yt_cookies_mode: auto / always / never</b>")
            self.config["yt_cookies_mode"] = mode
            return await utils.answer(message, f"<b>✅ yt_cookies_mode = <code>{mode}</code></b>")

        if key == "audio_format":
            valid = {"mp3", "m4a", "wav", "opus", "flac", "aac"}
            if raw.lower() not in valid:
                return await utils.answer(
                    message,
                    f"<b>❌ Доступні формати: {', '.join(sorted(valid))}</b>"
                )
            self.config["audio_format"] = raw.lower()
            return await utils.answer(message, f"<b>✅ audio_format = <code>{raw.lower()}</code></b>")

        if key not in mapping:
            return await utils.answer(message, "<b>❌ Невідомий параметр.</b>")
        cfg_key, cast, unit = mapping[key]
        try:
            val = bool(int(raw)) if cast is bool else int(raw)
        except ValueError:
            return await utils.answer(message, "<b>❌ Значення має бути числом.</b>")
        self.config[cfg_key] = val
        if key == "workers":
            self.config[cfg_key] = max(1, min(4, val))
            self._start_queue_workers()
            val = self.config[cfg_key]
        if key == "queue_max" and self._queue is not None:
            if val < 1:
                return await utils.answer(message, "<b>❌ queue_max має бути не менше 1.</b>")
            pending = self._queue.qsize()
            if val < pending:
                self.config[cfg_key] = self._queue.maxsize
                return await utils.answer(
                    message,
                    f"<b>❌ У черзі вже <code>{pending}</code> завдань; queue_max не може бути меншим.</b>",
                )
            old_queue = self._queue
            new_queue = asyncio.Queue(maxsize=val)
            while not old_queue.empty():
                new_queue.put_nowait(old_queue.get_nowait())
                old_queue.task_done()
            self._queue = new_queue
            self._start_queue_workers()
        if cast is bool:
            await utils.answer(message, f"<b>{key}: {'✅ ON' if val else '❌ OFF'}</b>")
        else:
            await utils.answer(message, f"<b>✅ {key} = <code>{val}</code> {unit}</b>")

    @loader.command()
    async def vdlt(self, message):
        """Витягти транскрипт: .vdlt [URL або reply]"""
        args = utils.get_args_raw(message).strip()
        url = self._extract_url(args) if args else None
        if not url:
            reply = await message.get_reply_message()
            if reply and reply.raw_text:
                url = self._extract_url(reply.raw_text)
        if not url:
            return await utils.answer(
                message,
                "<b>❌ Вкажи URL або відповідай на повідомлення з посиланням.</b>"
            )

        url = self._normalize(url)
        status = await utils.answer(message, self.strings("loading_transcript"))
        result = await self._get_transcript(url)
        if not result:
            return await status.edit(self.strings("err_no_transcript"))

        title, text = result
        self._stats["transcripts"] += 1
        full = self.strings("transcript_header").format(title=title) + text

        if len(full) > 4096:
            tmp_path = f"/tmp/transcript_{os.urandom(4).hex()}.txt"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(full)
            await message.client.send_file(
                message.chat_id, tmp_path,
                reply_to=message.id,
                caption=f"<b>📝 {title}</b>",
            )
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            await status.delete()
        else:
            await status.edit(full)

    @loader.command()
    async def vdlqueue(self, message):
        """Стан черги"""
        if self._queue is None:
            return await utils.answer(message, "<b>Черга не ініціалізована.</b>")
        await utils.answer(
            message,
            f"<b>📋 Черга: <code>{self._queue.qsize()}</code> / <code>{self.config['queue_max']}</code></b>"
            f"\n<b>⚡ Воркери: <code>{self._queue_workers_count()}</code></b>"
        )

    @loader.command()
    async def vdlcookies(self, message):
        """Статус cookies"""
        def _s(path):
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                age = int((time.time() - os.path.getmtime(path)) / 86400)
                return f"✅ є ({age} дн. тому)"
            return "❌ відсутній"
        _merge_platform_cookies()
        statuses = _cookie_domains_status()
        domains = "\n".join(
            f"   {'✅' if ok else '❌'} {name}" for name, ok in statuses.items()
        )
        await utils.answer(
            message,
            self.strings("cookies_status").format(
                yt=_s(COOKIES_YOUTUBE), default=_s(COOKIES_DEFAULT),
                mode=self.config.get("yt_cookies_mode", "auto"),
                browser=self._yt_browser_cookies_value(), domains=domains
            )
        )

    @loader.command()
    async def vdlupdate(self, message):
        """Оновити yt-dlp до останньої версії"""
        if self.config.get("auto_install_deps", True):
            await self._ensure_runtime_dependencies()
        ok, info = await self._auto_update_ytdlp(force=True)
        if ok:
            await utils.answer(message, self.strings("update_ok"))
        else:
            await utils.answer(message, self.strings("update_err").format(utils.escape_html(info)))

    @loader.command()
    async def vdlruntime(self, message):
        """Статус JS Runtime"""
        rt = _detect_js_runtime()
        if rt:
            name, path = rt
            await utils.answer(
                message,
                self.strings("js_runtime_status").format(rt=f"{name}:{path}")
            )
        else:
            await utils.answer(message, self.strings("js_runtime_missing"))

    @loader.command()
    async def vdldiag(self, message):
        """Діагностика downloader, runtime, cookies і fallback-ів."""
        import importlib.util
        import platform

        def executable(name: str, configured: str = "") -> str | None:
            path = self._find_executable(configured, [name])
            return path if path and os.access(path, os.X_OK) else None

        def mark(value) -> str:
            return "✅" if value else "❌"

        manager = self._cookie_manager()
        runtime = _detect_js_runtime()
        cookie_domains = _cookie_domains_status(manager.cookies_file)
        firefox = executable("firefox-esr") or executable("firefox")
        ytdlp_cli = self._find_ytdlp_command()
        fallbacks = {
            "gallery-dl": importlib.util.find_spec("gallery_dl") is not None,
            "instaloader": importlib.util.find_spec("instaloader") is not None,
            "spotdl": importlib.util.find_spec("spotdl") is not None,
            "Cobalt": bool(self.config.get("cobalt_api_url", "")),
        }
        domain_lines = "\n".join(
            f"│  ├ {name}: {mark(ok)}" for name, ok in cookie_domains.items()
        )
        fallback_lines = "\n".join(
            f"├ {name}: {mark(ok)}" for name, ok in fallbacks.items()
        )
        text = (
            "<b>🔧 VideoDownloader diagnostics</b>\n\n"
            f"<b>Core</b>\n├ Version: <code>{VERSION}</code>\n"
            f"├ Python: <code>{platform.python_version()}</code>\n"
            f"└ User: <code>{utils.escape_html(getpass.getuser())}</code>\n\n"
            f"<b>yt-dlp</b>\n├ Python API: {mark(importlib.util.find_spec('yt_dlp'))}\n"
            f"└ CLI: {mark(ytdlp_cli)} <code>{utils.escape_html(' '.join(ytdlp_cli or []))}</code>\n\n"
            f"<b>Runtime</b>\n├ JS: {mark(runtime)} <code>{utils.escape_html(':'.join(runtime) if runtime else 'not found')}</code>\n"
            f"├ FFmpeg: {mark(executable('ffmpeg', self.config.get('ffmpeg_path', '')))}\n"
            f"└ ffprobe: {mark(executable('ffprobe'))}\n\n"
            f"<b>Cookies</b>\n├ File: {mark(os.path.isfile(manager.cookies_file))} <code>{utils.escape_html(manager.cookies_file)}</code>\n"
            f"{domain_lines}\n└ Firefox DB: {mark(manager.firefox_profile_valid())}\n\n"
            f"<b>Firefox</b>\n├ Binary: {mark(firefox)} <code>{utils.escape_html(firefox or 'not found')}</code>\n"
            f"├ Profile: <code>{utils.escape_html(manager.firefox_profile or 'not configured')}</code>\n"
            f"└ User: <code>{utils.escape_html(manager.browser_user)}</code>\n\n"
            f"<b>Fallbacks</b>\n{fallback_lines}"
        )
        await utils.answer(message, text)

    @loader.command()
    async def vdladd(self, message):
        """Додати групу до білого списку"""
        if message.is_private:
            return await utils.answer(message, self.strings("not_a_group"))
        wl = self.config["group_whitelist"]
        if message.chat_id in wl:
            return await utils.answer(message, self.strings("already_in"))
        wl.append(message.chat_id)
        self.config["group_whitelist"] = wl
        await utils.answer(message, self.strings("whitelist_added").format(message.chat_id))

    @loader.command()
    async def vdlrm(self, message):
        """Видалити групу з білого списку"""
        if message.is_private:
            return await utils.answer(message, self.strings("not_a_group"))
        wl = self.config["group_whitelist"]
        if message.chat_id not in wl:
            return await utils.answer(message, self.strings("not_in"))
        wl.remove(message.chat_id)
        self.config["group_whitelist"] = wl
        await utils.answer(message, self.strings("whitelist_removed").format(message.chat_id))

    @loader.command()
    async def vdllist(self, message):
        """Білий список груп"""
        wl = self.config["group_whitelist"]
        if not wl:
            return await utils.answer(message, self.strings("whitelist_empty"))
        await utils.answer(
            message,
            self.strings("whitelist_list").format(
                "\n".join(f"• <code>{g}</code>" for g in wl)
            )
        )


    @loader.command()
    async def vdlpm(self, message):
        """Увімкнути/вимкнути автозавантаження для поточного контакту в ЛС"""
        if not message.is_private:
            return await utils.answer(message, "<b>❌ Тільки в особистих повідомленнях.</b>")
        peer_id = self._private_peer_id(message)
        if peer_id is None:
            return await utils.answer(message, "<b>❌ Не вдалося визначити контакт.</b>")
        wl = list(self.config.get("private_whitelist", []))
        if peer_id in wl:
            wl.remove(peer_id)
            enabled = False
        else:
            wl.append(peer_id)
            enabled = True
        self.config["private_whitelist"] = wl
        await utils.answer(
            message,
            f"<b>ЛС автозавантаження для <code>{peer_id}</code>: {'✅ ON' if enabled else '❌ OFF'}</b>",
        )

    @loader.command()
    async def vdlpmlist(self, message):
        """Список контактів у ЛС з дозволеним автозавантаженням"""
        wl = self.config.get("private_whitelist", [])
        if not wl:
            return await utils.answer(message, "<b>📋 ЛС-білий список порожній.</b>")
        await utils.answer(
            message,
            "<b>📋 ЛС автозавантаження дозволено для:</b>\n"
            + "\n".join(f"• <code>{u}</code>" for u in wl),
        )

    @loader.command()
    async def vdlban(self, message):
        """(reply) Заблокувати юзера"""
        reply = await message.get_reply_message()
        if not reply:
            return await utils.answer(message, self.strings("bl_need_reply"))
        uid = reply.sender_id
        bl = self.config["user_blacklist"]
        if uid in bl:
            return await utils.answer(message, self.strings("bl_already_in"))
        bl.append(uid)
        self.config["user_blacklist"] = bl
        await utils.answer(message, self.strings("bl_added").format(uid))

    @loader.command()
    async def vdlunban(self, message):
        """(reply) Розблокувати юзера"""
        reply = await message.get_reply_message()
        if not reply:
            return await utils.answer(message, self.strings("bl_need_reply"))
        uid = reply.sender_id
        bl = self.config["user_blacklist"]
        if uid not in bl:
            return await utils.answer(message, self.strings("bl_not_in"))
        bl.remove(uid)
        self.config["user_blacklist"] = bl
        await utils.answer(message, self.strings("bl_removed").format(uid))

    @loader.command()
    async def vdlbans(self, message):
        """Чорний список"""
        bl = self.config["user_blacklist"]
        if not bl:
            return await utils.answer(message, self.strings("bl_empty"))
        await utils.answer(
            message,
            self.strings("bl_list").format(
                "\n".join(f"• <code>{u}</code>" for u in bl)
            )
        )

    @loader.command()
    async def vdlstats(self, message):
        """Статистика"""
        self._reset_daily()
        s = self._stats
        platforms = "\n".join(
            f"   └ {p}: <code>{c}</code>"
            for p, c in sorted(s["platforms"].items(), key=lambda x: -x[1])
        ) or "   └ поки порожньо"
        await utils.answer(
            message,
            self.strings("stats").format(
                total=s["total"], ok=s["ok"], err=s["err"],
                retried=s["retried"],
                timeouts=s.get("timeouts", 0),
                audio=s["audio"],
                photos=s["photos"], playlists=s["playlists"],
                transcripts=s.get("transcripts", 0),
                today=s["today"],
                limit=self.config["daily_limit"] or "∞",
                platforms=platforms,
            )
        )

    @loader.command()
    async def vdlreset(self, message):
        """Скинути статистику"""
        self._stats = {
            "total": 0, "ok": 0, "err": 0, "retried": 0,
            "audio": 0, "photos": 0, "playlists": 0, "today": 0,
            "transcripts": 0, "timeouts": 0,
            "day": time.strftime("%Y-%m-%d"),
            "platforms": defaultdict(int),
        }
        await utils.answer(message, self.strings("stats_reset"))

    @loader.command()
    async def vdlhelp(self, message):
        """Всі команди"""
        await utils.answer(message, self.strings("help_text"))


# ── VTT parser ────────────────────────────────────────────────────────────────

def _parse_vtt_text(raw: str) -> str:
    lines = []
    seen = set()
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    for block in re.split(r"\n\n+", raw.strip()):
        block = block.strip()
        if not block or block.startswith("WEBVTT") or block.startswith("NOTE"):
            continue
        text_lines = []
        for line in block.splitlines():
            if re.match(r"^\d{2}:\d{2}", line) or re.match(r"^\d+$", line):
                continue
            clean = re.sub(r"<[^>]+>", "", line).strip()
            if clean:
                text_lines.append(clean)
        text = " ".join(text_lines)
        if text and text not in seen:
            seen.add(text)
            lines.append(text)

    return "\n".join(lines)


def _parse_vtt(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _parse_vtt_text(f.read())
    except Exception:
        return ""
