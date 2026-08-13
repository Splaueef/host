# meta developer: @RotKranz 
# meta syntax: .mistral | .mistralimg | .mistralagent | .mistralagentadd

__version__ = (4, 4, 0)

import asyncio
import base64
import collections
import html
import importlib
import io
import json
import logging
import math
import re
import subprocess
import sys
import time
from urllib.parse import quote
from typing import Dict, List, Optional, Tuple

import aiohttp
from .. import loader, utils

logger = logging.getLogger(__name__)

# ── Авто-встановлення залежностей ────────────────────────────────────────────

def _ensure_pkg(pkg: str, import_name: str | None = None) -> bool:
    name = import_name or pkg
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        try:
            logger.info("MistralAI: встановлюю %s...", pkg)
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "-q"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            importlib.import_module(name)
            logger.info("MistralAI: %s встановлено ✓", pkg)
            return True
        except Exception as e:
            logger.warning("MistralAI: не вдалося встановити %s: %s", pkg, e)
            return False

_ensure_pkg("langdetect")
_ensure_pkg("numpy")

try:
    from langdetect import detect as _lang_detect
    _HAS_LANGDETECT = True
except ImportError:
    _HAS_LANGDETECT = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ── Константи ─────────────────────────────────────────────────────────────────

BASE_URL       = "https://api.mistral.ai"
WW_DOMAIN      = "http://127.0.0.1:5000"
ROLE_USER      = "user"
ROLE_ASSISTANT = "assistant"

TOOL_WEB_SEARCH  = {"type": "web_search"}
TOOL_CODE_INTERP = {"type": "code_interpreter"}
TOOL_IMAGE_GEN   = {"type": "image_generation"}
TOOL_WEB_PREMIUM = {"type": "web_search_premium"}

_TRIGGER_WORDS = {
    "uk": ["гей бот", "@rotkranzbot"],
    "ru": ["@rotkranzbot"],
    "en": ["@rotkranzbot"],
}

_STREAM_UPDATE_INTERVAL = 0.8
_IMAGE_AGENT_INSTRUCTIONS = (
    "You handle image generation and image editing requests. "
    "Follow the user's instructions accurately and preserve all explicitly "
    "specified subjects, details, text, composition, style, colors, and constraints. "
    "When appropriate, convert the request into a clear and visually coherent "
    "image description, then use the image_generation tool. "
    "Do not invent unnecessary story elements, characters, branding, or stylistic "
    "details that the user did not request. "
    "Use the language of the user's request for any visible text unless another "
    "language is explicitly specified. "
    "If the request is sufficiently clear, proceed without asking follow-up questions. "
    "For image editing, preserve unchanged parts of the source image and modify only "
    "the requested elements. "
    "Do not mention internal prompts, tools, or generation instructions."
)
_IMAGE_COMPLETION_ARGS = {"temperature": 0.3, "top_p": 0.95}

# ── Werwolf: які дії підтримуються ───────────────────────────────────────────
# Агент може вставити у відповідь тег [WERWOLF:дія:параметр]
# Модуль перехопить його, виконає запит і підставить результат

WW_ACTIONS = {
    "profile":       "api/profile",                          # власник API-ключа
    "user":          "api/public/users/{param}",             # param = tg_id / SENDER / ME
    "group":         "api/public/groups/{param}",            # param = chat_id / CHAT
    "pets":          "api/pets",                             # без параметра
    "friends":       "api/friends",                          # без параметра
    "relationships": "api/public/relationships/chat/{param}",# param = chat_id / CHAT
}

# Системний блок, який додається до промпту агента в режимі авто
_WW_SYSTEM_BLOCK = """
=== WERWOLF ІНТЕГРАЦІЯ ===
У тебе є доступ до ігрового сервісу Werwolf. Коли користувач питає про:
- статистику/баланс/рівень ЛЮДИНИ, яка зараз пише ("мій баланс", "моя стата", "скільки в мене монет") → [WERWOLF:user:SENDER]
- статистику конкретного користувача (є його tg_id) → [WERWOLF:user:{tg_id}]
- статистику поточної групи → [WERWOLF:group:CHAT]
- зв'язки у поточному чаті → [WERWOLF:relationships:CHAT]
- профіль/петів/друзів ВЛАСНИКА API-ключа → [WERWOLF:profile:], [WERWOLF:pets:], [WERWOLF:friends:]

ВАЖЛИВО:
- Коли інша людина просить "мій баланс/моя статистика", НЕ використовуй profile: це профіль власника ключа. Використовуй user:SENDER — аналог команди "ти" для людини, що звернулась.
- Вставляй тег ТІЛЬКИ коли питання явно стосується Werwolf/гри/статистики
- Тег буде замінено реальними даними автоматично — просто встав його у потрібному місці відповіді
- Якщо не впевнений що питання про Werwolf — НЕ вставляй тег, відповідай звичайно
- Один тег на запит
=== КІНЕЦЬ БЛОКУ ===
"""

# Regex для пошуку тегу у відповіді агента
_WW_TAG_RE = re.compile(r"\[WERWOLF:(\w+):([^\]]*)\]")


# ── Markdown → HTML ───────────────────────────────────────────────────────────

def _html_escape(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _md_to_html(text: str) -> str:
    text = _html_escape(text)
    text = re.sub(
        r"```(\w+)?\n?(.*?)```",
        lambda m: f"<pre><code>{m.group(2).strip()}</code></pre>",
        text, flags=re.DOTALL,
    )
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__",
                  lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = re.sub(r"\*(.+?)\*|_(.+?)_",
                  lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text.strip()



def _mistral_content_to_text(content) -> str:
    """Normalize Mistral content chunks to plain text.

    Mistral can return streamed/non-streamed content either as a string or as
    a list of typed blocks. Older code only handled strings, so list blocks
    were silently ignored and the module kept typing without sending anything.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("value")
                if isinstance(value, str):
                    parts.append(value)
                elif value is not None:
                    parts.append(str(value))
        return "".join(parts)
    return str(content)


def _mistral_delta_to_text(obj: dict) -> str:
    try:
        choice = (obj.get("choices") or [{}])[0]
        delta = choice.get("delta") or choice.get("message") or {}
        if isinstance(delta, dict):
            return _mistral_content_to_text(delta.get("content"))
        return _mistral_content_to_text(delta)
    except Exception:
        return ""


def _telegram_image_file(image: bytes) -> io.BytesIO:
    """Give generated bytes a filename so Telegram treats them as a photo."""
    file = io.BytesIO(image)
    file.name = "mistral-generated.png"
    return file

def _detect_lang(text: str) -> str:
    if not _HAS_LANGDETECT or not text.strip():
        return "uk"
    try:
        return _lang_detect(text[:500])
    except Exception:
        return "uk"


def _is_addressed_to_bot(text: str, bot_name: str = "") -> bool:
    tl = text.lower()
    if bot_name and bot_name.lower() in tl:
        return True
    for words in _TRIGGER_WORDS.values():
        for w in words:
            if tl.startswith(w) or f" {w}" in tl or f"@{w}" in tl:
                return True
    return False


def _cosine_similarity(a: list, b: list) -> float:
    if not _HAS_NUMPY:
        dot = sum(x * y for x, y in zip(a, b))
        na  = math.sqrt(sum(x * x for x in a))
        nb  = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb + 1e-9)
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


def _parse_conversation_output(data: dict) -> tuple[str, list[bytes]]:
    """Extract text and generated images from a Conversations response.

    In addition to ``message.output`` blocks, an agent may return the result of
    ``image_generation`` inside a tool execution/result wrapper.  Mistral has
    used both singular ``file_id`` and plural ``file_ids`` response shapes, so
    this parser deliberately follows nested result objects as well.
    """
    texts: list[str] = []
    images: list[bytes] = []
    seen_images: set[bytes] = set()

    def add_image(image: bytes):
        if image and image not in seen_images:
            seen_images.add(image)
            images.append(image)

    def add_image_url(url: str):
        if not url:
            return
        if url.startswith("data:"):
            try:
                add_image(base64.b64decode(url.split(",", 1)[-1]))
            except (ValueError, base64.binascii.Error):
                logger.warning("MistralAI: invalid generated-image data URL")
        else:
            add_image(url.encode())

    def add_file_id(file_id):
        if isinstance(file_id, str) and file_id.strip():
            add_image(f"mistral-file://{file_id.strip()}".encode())

    seen_texts: set[str] = set()

    def add_text(value):
        text = _mistral_content_to_text(value).strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            texts.append(text)

    def walk(obj, key_hint: str = "", image_context: bool = False):
        if isinstance(obj, str):
            if key_hint in {"content", "text", "output_text"}:
                add_text(obj)
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item, key_hint, image_context)
            return
        if not isinstance(obj, dict):
            return

        ctype = str(obj.get("type", "")).lower()
        tool_name = str(obj.get("tool") or obj.get("name") or "").lower()
        is_image = image_context or tool_name == "image_generation" or "image_generation" in ctype
        if ctype in {"text", "output_text", "message.output", "message"}:
            for key in ("text", "content"):
                if key in obj:
                    add_text(obj[key])
        elif ctype in {"image_url", "input_image", "output_image", "generated_image"}:
            img = obj.get("image_url") or obj.get("url") or ""
            add_image_url(
                img if isinstance(img, str)
                else img.get("url", "") if isinstance(img, dict)
                else ""
            )
            encoded = obj.get("image_data") or obj.get("data") or obj.get("base64")
            if isinstance(encoded, str) and encoded:
                try:
                    add_image(base64.b64decode(encoded))
                except (ValueError, base64.binascii.Error):
                    logger.warning("MistralAI: invalid generated-image base64 block")
        elif ctype in {"tool_file", "file"}:
            file_id = obj.get("file_id") or obj.get("id")
            tool = obj.get("tool") or obj.get("source") or ""
            file_type = str(obj.get("file_type") or obj.get("mime_type") or "").lower()
            if file_id and (
                tool == "image_generation" or is_image
                or file_type in {
                    "png", "jpg", "jpeg", "webp",
                    "image/png", "image/jpeg", "image/webp",
                }
            ):
                add_file_id(file_id)

        # Tool execution results sometimes contain IDs without a ``tool_file``
        # type (for example ``output: {file_ids: [...]}``).  Only accept these
        # generic IDs while below an image_generation node to avoid sending an
        # unrelated document produced by another agent tool.
        if is_image:
            add_file_id(obj.get("file_id"))
            file_ids = obj.get("file_ids")
            if isinstance(file_ids, list):
                for file_id in file_ids:
                    add_file_id(file_id)
            for key in ("image_url", "url"):
                if isinstance(obj.get(key), str):
                    add_image_url(obj[key])

        # Conversations responses have changed shape over time. Some responses
        # put text directly into output.content as a string, while others use
        # typed blocks under content/chunks/data. Preserve both forms.
        for key in (
            "content", "outputs", "output", "result", "results", "chunks",
            "data", "message", "files", "artifacts",
        ):
            if key in obj and obj[key] is not obj:
                walk(obj[key], key, is_image)

    # Walk the complete envelope: some API versions expose generated artifacts
    # beside ``outputs`` rather than inside it.
    walk(data)
    if not texts:
        # Defensive fallback for chat-completion-like payloads or future aliases.
        for choice in data.get("choices", []) if isinstance(data.get("choices"), list) else []:
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
            if isinstance(message, dict) and "content" in message:
                add_text(message.get("content"))
        for key in ("output_text", "text", "content"):
            if key in data:
                add_text(data[key])
    return "\n".join(t for t in texts if t).strip(), images


# ── Werwolf форматери (компактні) ─────────────────────────────────────────────

def _ww_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _ww_fmt_profile(data: dict) -> str:
    """Компактний вивід профілю для вставки у відповідь агента."""
    bal   = data.get("balance", {})
    stats = data.get("stats", {})
    lvl   = data.get("level", {})
    prem  = data.get("premium", {})

    coins = bal.get("coins", 0)
    bank  = bal.get("bank", 0)
    gold  = bal.get("gold", 0)
    total = stats.get("total_messages", 0)
    level = lvl.get("level", 0)
    prem_active = prem.get("is_active", False)
    prem_lvl    = lvl.get("level", 0) if prem_active else 0

    lines = [
        "📊 <b>Werwolf профіль</b>",
        f"⭐ Монети: <b>{coins:.0f}</b>  🏦 Банк: <b>{bank:.1f}</b>  🥇 Золото: <b>{gold:.1f}</b>",
        f"📈 Рівень: <b>{level}</b>  💬 Повідомлень: <b>{total:,}</b>",
    ]
    if prem_active:
        lines.append(f"👑 Преміум {prem_lvl}")
    return "\n".join(lines)


def _ww_fmt_user(data: dict) -> str:
    u     = data.get("user", {})
    stats = data.get("stats", {})
    bdown = stats.get("message_breakdown", {})

    name     = u.get("display_name") or u.get("name") or "?"
    total    = bdown.get("total") or stats.get("total_messages", 0)
    stars    = u.get("stars", 0)
    gold     = u.get("gold", 0)
    wins     = u.get("wins", 0)
    losses   = u.get("losses", 0)

    lines = [
        f"👤 <b>{name}</b>",
        f"💬 Повідомлень: <b>{total:,}</b>",
        f"⭐ {stars:.0f}  🥇 {gold:.1f}",
    ]
    if wins or losses:
        tg = wins + losses
        wr = round(wins / tg * 100) if tg else 0
        lines.append(f"🏆 {wins}W/{losses}L  WR {wr}%")
    return "\n".join(lines)


def _ww_fmt_group(data: dict) -> str:
    title   = data.get("title") or data.get("name") or "?"
    members = _ww_int(data.get("members"))
    stats   = data.get("stats", {})
    summary = stats.get("summary", {})
    today   = summary.get("day", {})
    month   = summary.get("month", {})
    total   = (
        stats.get("total_messages")
        or summary.get("all", {}).get("messages")
        or data.get("total_messages")
        or 0
    )
    total = _ww_int(total)
    top     = (stats.get("top_users") or data.get("top_users") or [])[:5]

    lines = [
        f"👥 <b>{title}</b>  ({members:,} учасників)",
        f"💬 Всього повідомлень: <b>{total:,}</b>",
    ]
    if today:
        lines.append(
            f"📅 Сьогодні: <b>{_ww_int(today.get('messages')):,}</b> "
            f"({_ww_int(today.get('unique_users'))} юзерів)"
        )
    if month:
        lines.append(
            f"🗓 Місяць: <b>{_ww_int(month.get('messages')):,}</b> "
            f"({_ww_int(month.get('unique_users'))} юзерів)"
        )
    if top:
        top_names = []
        for u in top:
            name = u.get("name") or u.get("display_name") or u.get("username") or "?"
            messages = u.get("messages")
            top_names.append(f"{name} ({messages})" if messages is not None else name)
        lines.append(f"🏆 Топ: {', '.join(top_names)}")
    return "\n".join(lines)


def _ww_fmt_pets(data: dict) -> str:
    pets = data.get("pets", [])
    if not pets:
        return "🐾 Петів немає."
    lines = ["🐾 <b>Пети:</b>"]
    for p in pets:
        name  = p.get("name", "?")
        stage = p.get("stage_label") or p.get("stage", "?")
        lvl   = p.get("level", 0)
        lines.append(f"· {name}  {stage}  lvl {lvl}")
    return "\n".join(lines)


def _ww_fmt_friends(data: dict) -> str:
    friends = data.get("friends", [])
    if not friends:
        return "👥 Друзів немає."
    names = [f.get("name") or f.get("username") or "?" for f in friends[:5]]
    more  = f"  (+{len(friends)-5})" if len(friends) > 5 else ""
    return f"👥 <b>Друзі:</b> {', '.join(names)}{more}"


def _ww_fmt_relationships(data: dict) -> str:
    entries = (data if isinstance(data, list)
               else data.get("relationships") or data.get("items") or [])
    if not entries:
        return "🔗 Зв'язків немає."
    lines = ["🔗 <b>Топ зв'язки:</b>"]
    for e in entries[:5]:
        n1    = e.get("user1_name") or e.get("name") or "?"
        n2    = e.get("user2_name", "")
        score = e.get("score") or e.get("messages") or 0
        pair  = f"{n1} ↔ {n2}" if n2 else n1
        lines.append(f"· {pair}  ({score})")
    return "\n".join(lines)


_WW_FORMATTERS = {
    "profile":       _ww_fmt_profile,
    "user":          _ww_fmt_user,
    "group":         _ww_fmt_group,
    "pets":          _ww_fmt_pets,
    "friends":       _ww_fmt_friends,
    "relationships": _ww_fmt_relationships,
}


# ── Структура для векторної пам'яті ──────────────────────────────────────────

class MemoryEntry:
    __slots__ = ("role", "content", "embedding", "timestamp")

    def __init__(self, role: str, content: str, embedding: list | None = None):
        self.role      = role
        self.content   = content
        self.embedding = embedding
        self.timestamp = time.time()


# ── Лічильник статистики ──────────────────────────────────────────────────────

class UsageStats:
    def __init__(self):
        self.requests   = 0
        self.errors     = 0
        self.start_time = time.time()

    def inc(self, ok: bool = True):
        self.requests += 1
        if not ok:
            self.errors += 1

    def summary(self) -> str:
        uptime = int(time.time() - self.start_time)
        h, m = divmod(uptime // 60, 60)
        return (
            f"📊 Запитів: <b>{self.requests}</b> | "
            f"Помилок: <b>{self.errors}</b> | "
            f"Аптайм: <b>{h}г {m}хв</b>"
        )


# ══════════════════════════════════════════════════════════════════════════════

@loader.tds
class MistralModule(loader.Module):
    """Mistral AI v4.1: чат, зображення, OCR, TTS, транскрипція, агент-режим,
    векторна пам'ять, стрімінг, авто-мова, статистика, Werwolf інтеграція."""

    strings = {
        "name": "MistralAI",
        "no_api_key":   "<b>🔑 Встанови API ключ у <code>.config MistralAI</code>!</b>",
        "no_args":      "<b>❌ Введи аргумент після команди!</b>",
        "loading":      "<b>⏳ Опрацьовую...</b>",
        "generating":   "<b>🎨 Генерую зображення...</b>",
        "error":        "<b>❌ Помилка:</b> <code>{error}</code>",
        "api_unavailable": (
            "<b>⚠️ Mistral API зараз не відповідає.</b>\n"
            "<i>{reason}</i>\n\n"
            "Спробуй повторити запит трохи пізніше."
        ),
        "chat_answer": (
            "<b>👤</b> {question}\n"
            "<b>🤖</b> {answer}\n\n"
            "<i>⚡ {model} • {time:.1f}с</i>"
        ),
        "img_caption":    "🎨 <i>{prompt}</i>",
        "img_no_result":  "<b>❌ Зображення не згенеровано. Спробуй інший промт.</b>",
        "agents_header":  "<b>🤖 Твої Mistral агенти:</b>\n\n",
        "agents_empty":   "<b>🤖 У тебе ще немає агентів на Mistral.</b>",
        "agent_created":  "✅ <b>Агент створено!</b>\nID: <code>{id}</code>\nНазва: <b>{name}</b>",
        "agent_deleted":  "🗑 <b>Агент видалено:</b> <code>{id}</code>",
        "agent_set":      "✅ <b>Поточний агент:</b> <code>{id}</code> (<b>{name}</b>)",
        "agent_unset":    "ℹ️ <b>Агент скинуто.</b> Використовується звичайна модель.",
        "agent_info": (
            "<b>🤖 Агент:</b> <code>{id}</code>\n"
            "<b>Назва:</b> {name}\n"
            "<b>Модель:</b> {model}\n"
            "<b>Інструменти:</b> {tools}\n"
            "<b>Інструкція:</b>\n<i>{instructions}</i>"
        ),
        "autoagent_on":         "🤖 <b>Авто-агент увімкнено</b> у чаті <code>{chat}</code>",
        "autoagent_off":        "😴 <b>Авто-агент вимкнено</b> у чаті <code>{chat}</code>",
        "autoagent_list_empty": "<b>🤖 Авто-агент ніде не активний.</b>",
        "autoagent_list":       "<b>🤖 Активні авто-агент чати:</b>\n{chats}",
        "agent_thoughts_unavailable": (
            "<b>❌ Інсайт доступний тільки коли у цьому чаті увімкнено авто-агент "
            "і підключено Mistral-агента.</b>"
        ),
        "agent_thoughts_empty": "<b>ℹ️ Авто-агент ще не має історії цього чату.</b>",
        "agent_thoughts_result": "<b>🧠 Що агент думає про співрозмовника:</b>\n\n{text}",
        "memory_cleared":       "🧹 <b>Пам'ять очищена</b> у чаті <code>{chat}</code>",
        "models_header":        "<b>📋 Доступні моделі:</b>\n\n",
        "ocr_result":           "<b>📄 OCR:</b>\n\n{text}",
        "ocr_no_media":         "<b>❌ Відповідь на фото або PDF!</b>",
        "tts_generating":       "<b>🎙 Генерую голос...</b>",
        "tts_done":             "<b>🔊 Mistral TTS</b>",
        "transcription":        "<b>🎤 Транскрипція:</b>\n\n{text}",
        "no_audio":             "<b>❌ Відповідь на аудіо/голосове повідомлення!</b>",
        "moderation":           "<b>🛡 Модерація:</b>\nБезпечно: {safe}\nКатегорії: {cats}",
        "embed_result": (
            "<b>🔢 Ембединги ({model}):</b>\n"
            "<code>Розмірність: {dim}</code>\n"
            "<code>Перші 5: {preview}</code>"
        ),
        "stats":            "<b>📊 Статистика MistralAI:</b>\n{summary}",
        "deps_status":      "<b>📦 Залежності:</b>\n{status}",
        "streaming_think":  "<b>🤖</b> <i>думаю...</i>",
        "ask_no_reply":     "<b>❌ Відповідай на повідомлення або додай текст після команди!</b>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            # ── Основні ────────────────────────────────────────────────────
            loader.ConfigValue(
                "api_key", "",
                "Mistral API Key (https://console.mistral.ai)",
                validator=loader.validators.Hidden(loader.validators.String()),
            ),
            loader.ConfigValue(
                "chat_model", "mistral-large-latest",
                "Модель для .mistral",
            ),
            loader.ConfigValue(
                "system_prompt",
                "Ти — корисний асистент. Відповідай чітко і зрозуміло.",
                "Системний промпт для .mistral",
            ),
            loader.ConfigValue("max_tokens", 2048, "Ліміт токенів"),
            loader.ConfigValue("timeout", 120, "Таймаут (с)"),
            loader.ConfigValue(
                "retry_count", 2,
                "Кількість повторних спроб при помилці",
                validator=loader.validators.Integer(minimum=0, maximum=5),
            ),
            # ── Авто-агент ─────────────────────────────────────────────────
            loader.ConfigValue(
                "agent_system_prompt",
                (
                    "Ти — особистий асистент власника цього Telegram-акаунту. "
                    "Спілкуйся природно, як жива людина. "
                    "Відповідай мовою співрозмовника. "
                    "Відповідай коротко і по суті. "
                    "Не розкривай що ти AI якщо не питають напряму."
                ),
                "Промпт поведінки авто-агента",
            ),
            loader.ConfigValue(
                "agent_model", "mistral-large-latest",
                "Модель для авто-агента",
            ),
            loader.ConfigValue(
                "agent_mistral_id", "",
                "ID Mistral-агента для авто-режиму (порожньо = модель+промпт)",
            ),
            loader.ConfigValue(
                "agent_history_limit", 50,
                "Глибина пам'яті авто-агента",
                validator=loader.validators.Integer(minimum=5, maximum=500),
            ),
            loader.ConfigValue(
                "agent_typing", True,
                "Показувати 'друкує...' перед відповіддю",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "agent_group_mode", "addressed",
                "Режим відповіді в групах: 'all' / 'addressed' / 'reply'",
                validator=loader.validators.Choice(["all", "addressed", "reply"]),
            ),
            loader.ConfigValue(
                "agent_auto_lang", True,
                "Автоматично відповідати мовою співрозмовника",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "agent_stream", False,
                "Стрімінг відповіді",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "agent_limit_daily", 10,
                "Максимум авто-відповідей на день для 1 групи/OP-користувача (0 = без ліміту)",
                validator=loader.validators.Integer(minimum=0, maximum=10000),
            ),
            loader.ConfigValue(
                "agent_limit_monthly", 80,
                "Максимум авто-відповідей на місяць для 1 групи/OP-користувача (0 = без ліміту)",
                validator=loader.validators.Integer(minimum=0, maximum=100000),
            ),
            loader.ConfigValue(
                "agent_limit_exempt_chats",
                "-1003988122379,-1003321833550",
                "Chat IDs груп без лімітів через кому",
            ),
            loader.ConfigValue(
                "agent_limit_exempt_titles",
                "Хелп група",
                "Назви груп без лімітів через кому",
            ),
            loader.ConfigValue(
                "agent_limit_exempt_users",
                "7818078126",
                "User IDs без лімітів через кому; власник Hikka також завжди без ліміту",
            ),
            loader.ConfigValue(
                "agent_ignored_users",
                "7635755119",
                "User IDs, яких авто-агент повністю ігнорує через кому",
            ),
            loader.ConfigValue(
                "vector_memory", False,
                "Векторна пам'ять (потребує numpy)",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "vector_top_k", 5,
                "Скільки релевантних спогадів підтягувати",
                validator=loader.validators.Integer(minimum=1, maximum=20),
            ),
            # ── Werwolf ────────────────────────────────────────────────────
            loader.ConfigValue(
                "werwolf_api_key", "T9Igz3OeJHvgEHlyqnQBPY8I0BjrYMvBTLDvKs8qopY",
                "API ключ Werwolf (отримати через /api у боті)",
                validator=loader.validators.Hidden(loader.validators.String()),
            ),
            loader.ConfigValue(
                "werwolf_enabled", True,
                "Увімкнути Werwolf інтеграцію в авто-агенті",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "werwolf_domain", "http://127.0.0.1:5000",
                "Домен Werwolf API",
            ),
            # ── Інші моделі ────────────────────────────────────────────────
            loader.ConfigValue("ocr_model",           "mistral-ocr-latest",  "Модель OCR"),
            loader.ConfigValue("tts_model",           "mistral-tts-latest",  "Модель TTS"),
            loader.ConfigValue("tts_voice",           "fr:emma",             "Голос TTS"),
            loader.ConfigValue("transcription_model", "voxtral-mini-2507",   "Модель транскрипції"),
            loader.ConfigValue("embed_model",         "mistral-embed",       "Модель ембединів"),
            loader.ConfigValue(
                "image_model", "mistral-medium-latest",
                "Модель для Mistral image_generation агента",
            ),
            loader.ConfigValue(
                "image_agent_id", "",
                "ID Mistral-агента з image_generation (порожньо = створити автоматично)",
            ),
            loader.ConfigValue(
                "image_prompt_prefix",
                "Generate a high-quality image. Return the generated image file.",
                "Додаткова інструкція для .mistralimg",
            ),
            loader.ConfigValue(
                "image_fallback_enabled", True,
                "Якщо Mistral не повернув файл — генерувати через HTTP fallback",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "image_fallback_base_url", "https://image.pollinations.ai",
                "HTTP fallback для генерації зображень (сумісний з /prompt/{prompt})",
            ),
        )

        self._session: aiohttp.ClientSession | None = None
        self._me = None
        self._auto_chats: Dict[int, bool] = {}

        # Групова пам'ять: {chat_id: deque[dict]}  — завжди спільна для чату
        self._memory: Dict[int, object] = {}

        self._active_agent_id:   Optional[str] = None
        self._active_agent_name: Optional[str] = None
        self._stats = UsageStats()
        self._bot_name: str = ""

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def client_ready(self, client, db):
        self._session = aiohttp.ClientSession()
        me = await client.get_me()
        self._me = me
        self._bot_name = (me.first_name or me.username or "").lower()
        saved = self.db.get("MistralAI", "auto_chats", [])
        self._auto_chats = {int(c): True for c in saved}
        self._active_agent_id   = self.db.get("MistralAI", "active_agent_id",   "") or None
        self._active_agent_name = self.db.get("MistralAI", "active_agent_name", "") or None

    async def on_unload(self):
        if self._session:
            await self._session.close()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type": "application/json"}

    def _to(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.config["timeout"])

    def _ok(self) -> bool:
        return bool(self.config["api_key"].strip())

    def _is_retryable_error(self, error: Exception) -> bool:
        text = str(error)
        return (
            "таймаут" in text
            or "HTTP 408" in text
            or "HTTP 409" in text
            or "HTTP 429" in text
            or "HTTP 5" in text
            or isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError))
        )

    def _friendly_api_error(self, error: Exception) -> str:
        text = str(error).strip() or error.__class__.__name__
        if "таймаут" in text:
            return (
                f"Таймаут запиту до Mistral API ({self.config['timeout']}с). "
                "Сервіс не встиг відповісти."
            )
        if "HTTP 429" in text:
            return "Ліміт або rate limit Mistral API. Зачекай і повтори запит."
        if "HTTP 401" in text or "HTTP 403" in text:
            return "Mistral API відхилив ключ або доступ до моделі. Перевір .config MistralAI."
        if "HTTP 404" in text:
            return "Mistral API не знайшов endpoint, модель або агента. Перевір налаштування моделі/agent_id."
        if "HTTP 5" in text or "Server disconnected" in text:
            return f"Проблема на боці Mistral API: {text}"
        if isinstance(error, aiohttp.ClientError):
            return f"Мережева помилка підключення до Mistral API: {text}"
        return text

    def _api_error_answer(self, error: Exception) -> str:
        reason = _html_escape(self._friendly_api_error(error))
        if self._is_retryable_error(error):
            return self.strings["api_unavailable"].format(reason=reason)
        return self.strings["error"].format(error=reason)

    def _save_auto(self):
        self.db.set("MistralAI", "auto_chats", list(self._auto_chats.keys()))

    def _save_active_agent(self):
        self.db.set("MistralAI", "active_agent_id",   self._active_agent_id   or "")
        self.db.set("MistralAI", "active_agent_name", self._active_agent_name or "")


    def _csv_ints(self, value) -> set[int]:
        result = set()
        for item in str(value or "").replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.add(int(item))
            except ValueError:
                logger.warning("MistralAI: некоректний ID у списку лімітів: %s", item)
        return result

    def _csv_titles(self, value) -> set[str]:
        return {item.strip().casefold() for item in str(value or "").split(",") if item.strip()}

    def _limit_periods(self) -> tuple[str, str]:
        now = time.gmtime()
        return time.strftime("%Y-%m-%d", now), time.strftime("%Y-%m", now)

    def _limit_scope(
        self,
        chat_id: int,
        sender_id: int,
        is_group: bool,
        group_title: str | None = None,
    ) -> str | None:
        if self._me and sender_id == self._me.id:
            return None
        if sender_id in self._csv_ints(self.config["agent_limit_exempt_users"]):
            return None
        if is_group:
            if chat_id in self._csv_ints(self.config["agent_limit_exempt_chats"]):
                return None
            if group_title and group_title.casefold() in self._csv_titles(self.config["agent_limit_exempt_titles"]):
                return None
            return f"group:{chat_id}"
        return f"user:{sender_id}"

    def _limit_state(self) -> dict:
        state = self.db.get("MistralAI", "agent_limits", {})
        return state if isinstance(state, dict) else {}

    def _limit_allowed(self, scope: str | None) -> bool:
        if not scope:
            return True
        daily = int(self.config["agent_limit_daily"] or 0)
        monthly = int(self.config["agent_limit_monthly"] or 0)
        if daily <= 0 and monthly <= 0:
            return True
        day, month = self._limit_periods()
        item = self._limit_state().get(scope, {})
        day_count = item.get("day_count", 0) if item.get("day") == day else 0
        month_count = item.get("month_count", 0) if item.get("month") == month else 0
        if daily > 0 and day_count >= daily:
            return False
        if monthly > 0 and month_count >= monthly:
            return False
        return True

    def _limit_inc(self, scope: str | None):
        if not scope:
            return
        day, month = self._limit_periods()
        state = self._limit_state()
        item = state.get(scope, {})
        day_count = item.get("day_count", 0) if item.get("day") == day else 0
        month_count = item.get("month_count", 0) if item.get("month") == month else 0
        state[scope] = {
            "day": day,
            "day_count": day_count + 1,
            "month": month,
            "month_count": month_count + 1,
        }
        self.db.set("MistralAI", "agent_limits", state)

    def _get_plain_mem(self, chat_id: int) -> collections.deque:
        """Пам'ять для чату — завжди спільна.
        В групах ім'я відправника вшивається у content: '[Ім'я]: текст'."""
        lim = self.config["agent_history_limit"]
        if chat_id not in self._memory or not isinstance(self._memory[chat_id], collections.deque):
            self._memory[chat_id] = collections.deque(maxlen=lim)
        return self._memory[chat_id]

    def _agent_id(self) -> str | None:
        return self._active_agent_id or (str(self.config["agent_mistral_id"] or "").strip() or None)

    def _history_for_prompt(self, history) -> str:
        lines = []
        for m in list(history):
            role = "Assistant" if m.get("role") == ROLE_ASSISTANT else "User"
            content = str(m.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _get_vec_mem(self, chat_id: int) -> list:
        if not hasattr(self, "_vec_mem"):
            self._vec_mem: Dict[int, list] = {}
        if chat_id not in self._vec_mem:
            self._vec_mem[chat_id] = []
        return self._vec_mem[chat_id]

    def _vec_mem_add(self, chat_id: int, role: str,
                     content: str, embedding: list | None = None):
        mem = self._get_vec_mem(chat_id)
        mem.append(MemoryEntry(role, content, embedding))
        lim = self.config["agent_history_limit"]
        if len(mem) > lim:
            del mem[0]

    def _vec_mem_relevant(self, chat_id: int,
                           query_emb: list, top_k: int) -> list[MemoryEntry]:
        mem = self._get_vec_mem(chat_id)
        scored = []
        for entry in mem:
            if entry.embedding:
                sim = _cosine_similarity(query_emb, entry.embedding)
                scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    # ── Werwolf HTTP ──────────────────────────────────────────────────────────

    def _ww_ok(self) -> bool:
        return bool(self.config["werwolf_api_key"].strip()) and self.config["werwolf_enabled"]

    def _ww_headers(self) -> dict:
        return {"X-API-Key": self.config["werwolf_api_key"].strip()}

    def _ww_url(self, path: str) -> str:
        domain = str(self.config["werwolf_domain"]).rstrip("/")
        return f"{domain}/{path.lstrip('/')}"

    async def _ww_get(self, path: str) -> dict:
        url = self._ww_url(path)
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with self._session.get(url, headers=self._ww_headers(), timeout=timeout) as r:
                body = await r.text()
                if r.status >= 400:
                    raise RuntimeError(f"Werwolf HTTP {r.status}")
                return json.loads(body)
        except asyncio.TimeoutError:
            raise RuntimeError("Werwolf таймаут")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Werwolf: {e}")

    def _ww_param(self, param: str, chat_id: int, sender_id: int) -> str:
        """Підставляє службові параметри з контексту Telegram."""
        value = (param or "").strip()
        upper = value.upper()
        if not value or upper in {"SENDER", "ME", "USER"}:
            return str(sender_id)
        if upper in {"CHAT", "GROUP"}:
            return str(chat_id)
        return value

    async def _ww_get_group(self, chat_id_or_param: str) -> dict:
        """Отримує групу з fallback для Telegram supergroup id (-100...)."""
        cid = str(chat_id_or_param).strip()
        try:
            return await self._ww_get(f"api/public/groups/{cid}")
        except RuntimeError as e:
            if "404" in str(e) and cid.startswith("-100"):
                return await self._ww_get(f"api/public/groups/{cid[4:]}")
            raise

    async def _ww_get_relationships(self, chat_id_or_param: str) -> dict:
        """Отримує зв'язки чату з fallback для -100 id."""
        cid = str(chat_id_or_param).strip()
        try:
            return await self._ww_get(f"api/public/relationships/chat/{cid}")
        except RuntimeError as e:
            if "404" in str(e) and cid.startswith("-100"):
                return await self._ww_get(f"api/public/relationships/chat/{cid[4:]}")
            raise

    async def _ww_resolve_tag(self, action: str, param: str,
                               chat_id: int, sender_id: int) -> str:
        """Виконує Werwolf запит і повертає відформатований рядок."""
        if not self._ww_ok():
            return ""
        try:
            # Підставляємо реальні значення якщо param порожній
            if action == "profile":
                data = await self._ww_get("api/profile")
                return _ww_fmt_profile(data)

            elif action == "user":
                uid = self._ww_param(param, chat_id, sender_id)
                data = await self._ww_get(f"api/public/users/{uid}")
                return _ww_fmt_user(data)

            elif action == "group":
                cid = self._ww_param(param or "CHAT", chat_id, sender_id)
                data = await self._ww_get_group(cid)
                return _ww_fmt_group(data)

            elif action == "pets":
                data = await self._ww_get("api/pets")
                return _ww_fmt_pets(data)

            elif action == "friends":
                data = await self._ww_get("api/friends")
                return _ww_fmt_friends(data)

            elif action == "relationships":
                cid = self._ww_param(param or "CHAT", chat_id, sender_id)
                data = await self._ww_get_relationships(cid)
                return _ww_fmt_relationships(data)

            else:
                return ""
        except RuntimeError as e:
            logger.warning("Werwolf tag error: %s", e)
            return f"⚠️ Не вдалося отримати дані Werwolf: {e}"

    async def _ww_process_reply(self, text: str,
                                  chat_id: int, sender_id: int) -> str:
        """Знаходить [WERWOLF:...] теги у відповіді і замінює їх даними."""
        match = _WW_TAG_RE.search(text)
        if not match:
            return text

        action = match.group(1)
        param  = match.group(2)
        ww_data = await self._ww_resolve_tag(action, param, chat_id, sender_id)

        if ww_data:
            # Замінюємо тег на дані
            replacement = f"\n\n{ww_data}"
            text = text[:match.start()] + text[match.end():]
            text = text.rstrip() + replacement
        else:
            # Прибираємо тег без заміни
            text = text[:match.start()] + text[match.end():]

        return text.strip()

    # ── Low-level HTTP з retry ─────────────────────────────────────────────────

    async def _read_json_response(self, response) -> dict:
        body = await response.text()
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
            raise RuntimeError(f"Некоректний JSON від API: {body[:300]}")

        if response.status >= 400:
            err = data.get("error", data) if isinstance(data, dict) else data
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"HTTP {response.status}: {msg}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(msg)
        return data

    async def _post(self, path: str, payload: dict, headers: dict | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        h = headers or self._h()
        retries = self.config["retry_count"]
        last_err = None
        for attempt in range(retries + 1):
            try:
                async with self._session.post(
                    url, json=payload, headers=h, timeout=self._to()
                ) as r:
                    return await self._read_json_response(r)
            except asyncio.TimeoutError:
                last_err = RuntimeError(f"таймаут ({self.config['timeout']}с)")
            except RuntimeError as e:
                if not self._is_retryable_error(e) or attempt >= retries:
                    raise
                last_err = e
            except aiohttp.ClientError as e:
                last_err = RuntimeError(str(e))
            except Exception as e:
                raise RuntimeError(str(e))
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_err

    async def _post_stream(self, path: str, payload: dict) -> str:
        url = f"{BASE_URL}{path}"
        payload = {**payload, "stream": True}
        chunks = []
        try:
            async with self._session.post(
                url, json=payload, headers=self._h(), timeout=self._to()
            ) as r:
                if r.status >= 400:
                    await self._read_json_response(r)
                async for line in r.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        obj = json.loads(raw)
                        delta = _mistral_delta_to_text(obj)
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        pass
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))
        return "".join(chunks)

    async def _get(self, path: str) -> dict:
        url = f"{BASE_URL}{path}"
        try:
            async with self._session.get(url, headers=self._h(), timeout=self._to()) as r:
                return await self._read_json_response(r)
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))

    async def _download_file(self, file_id: str) -> bytes:
        """Download a generated file from Mistral Files API."""
        last_error = None
        for path in (f"/v1/files/{file_id}/content", f"/v1/files/{file_id}/download"):
            url = f"{BASE_URL}{path}"
            try:
                async with self._session.get(url, headers=self._h(), timeout=self._to()) as r:
                    body = await r.read()
                    if r.status < 400:
                        return body
                    last_error = RuntimeError(
                        f"Files HTTP {r.status}: {body[:200].decode(errors='ignore')}"
                    )
            except RuntimeError as e:
                last_error = e
            except Exception as e:
                last_error = RuntimeError(str(e))
        raise last_error or RuntimeError("не вдалося завантажити файл")

    async def _delete(self, path: str) -> dict:
        url = f"{BASE_URL}{path}"
        try:
            async with self._session.delete(url, headers=self._h(), timeout=self._to()) as r:
                return await self._read_json_response(r)
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))

    async def _patch(self, path: str, payload: dict) -> dict:
        url = f"{BASE_URL}{path}"
        try:
            async with self._session.patch(
                url, json=payload, headers=self._h(), timeout=self._to()
            ) as r:
                return await self._read_json_response(r)
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))

    # ── API: Chat ──────────────────────────────────────────────────────────────

    async def _chat(self, prompt: str, model: str | None = None,
                    system: str | None = None,
                    stream: bool = False) -> tuple[str, str, float]:
        m   = model  or self.config["chat_model"]
        sys = system if system is not None else self.config["system_prompt"]
        payload = {
            "model": m,
            "max_tokens": self.config["max_tokens"],
            "messages": [
                {"role": "system", "content": sys},
                {"role": ROLE_USER,  "content": prompt},
            ],
        }
        t0 = time.monotonic()
        if stream:
            text = await self._post_stream("/v1/chat/completions", payload)
            if not text.strip():
                logger.warning("MistralAI: stream returned empty content; retrying without stream")
                data = await self._post("/v1/chat/completions", payload)
                text = _mistral_content_to_text(data["choices"][0]["message"].get("content"))
        else:
            data = await self._post("/v1/chat/completions", payload)
            text = _mistral_content_to_text(data["choices"][0]["message"].get("content"))
        return text, m, time.monotonic() - t0

    async def _chat_history(
        self,
        history: collections.deque,
        new_msg: str,
        chat_id: int | None = None,
        sender_id: int | None = None,
        sender_name: str | None = None,
        is_group: bool = False,
        group_title: str | None = None,
        extra_context: list | None = None,
        system_override: str | None = None,
    ) -> str:
        """Чат з пам'яттю.

        В групах повідомлення зберігаються у форматі '[Ім'я]: текст',
        тому модель знає хто що написав.

        Якщо Werwolf увімкнено — додаємо блок з інструкцією до системного промпту.
        """
        base_system = system_override or self.config["agent_system_prompt"]

        # Додаємо контекст про групу
        if is_group:
            group_ctx = (
                f"\nТи зараз у груповому чаті{f' «{group_title}»' if group_title else ''}. "
                f"Поточний chat_id: {chat_id}. "
                "З тобою спілкуються різні люди — їх імена вказані у квадратних дужках, "
                "наприклад [Олексій]: привіт. "
                "Завжди звертайся до людини по імені. "
                "Пам'ятай всю розмову — хто що питав і що ти відповідав."
            )
            system = base_system + group_ctx
        else:
            system = base_system

        context_lines = []
        if sender_id is not None:
            context_lines.append(
                f"Поточний співрозмовник: {sender_name or sender_id}; tg_id={sender_id}. "
                "Для його/її RK/Werwolf статистики використовуй тег [WERWOLF:user:SENDER]."
            )
        if is_group:
            context_lines.append(
                "Для статистики цієї групи використовуй [WERWOLF:group:CHAT], "
                "для зв'язків — [WERWOLF:relationships:CHAT]."
            )
        if context_lines:
            system += "\n\nКонтекст Telegram:\n" + "\n".join(context_lines)

        # Додаємо Werwolf інструкцію якщо є ключ
        if self._ww_ok():
            system = system + _WW_SYSTEM_BLOCK

        messages = [{"role": "system", "content": system}]

        # Релевантний контекст з векторної пам'яті
        if extra_context:
            ctx_text = "Корисний контекст з попередніх розмов:\n" + "\n".join(
                f"{e.content}" for e in extra_context
            )
            messages.append({"role": "system", "content": ctx_text})

        # Вся поточна пам'ять чату (вже містить '[Ім'я]: текст' для груп)
        messages.extend(list(history))

        # Поточне повідомлення (ще не збережене в history на момент виклику)
        content = f"[{sender_name}]: {new_msg}" if (is_group and sender_name) else new_msg
        messages.append({"role": ROLE_USER, "content": content})

        payload = {
            "model": self.config["agent_model"],
            "max_tokens": self.config["max_tokens"],
            "messages": messages,
        }
        data = await self._post("/v1/chat/completions", payload)
        return _mistral_content_to_text(data["choices"][0]["message"].get("content"))

    async def _chat_with_reply(self, question: str, reply_text: str,
                                model: str | None = None) -> tuple[str, str, float]:
        m   = model or self.config["chat_model"]
        sys = self.config["system_prompt"]
        payload = {
            "model": m,
            "max_tokens": self.config["max_tokens"],
            "messages": [
                {"role": "system", "content": sys},
                {"role": ROLE_USER,      "content": f"Контекст повідомлення:\n{reply_text}"},
                {"role": ROLE_ASSISTANT, "content": "Зрозумів, маю цей контекст."},
                {"role": ROLE_USER,      "content": question},
            ],
        }
        t0   = time.monotonic()
        data = await self._post("/v1/chat/completions", payload)
        text = _mistral_content_to_text(data["choices"][0]["message"].get("content"))
        return text, m, time.monotonic() - t0

    # ── API: Стрімінг ──────────────────────────────────────────────────────────

    async def _stream_to_message(self, msg, prompt: str, model: str | None = None,
                                  system: str | None = None):
        m   = model  or self.config["chat_model"]
        sys = system if system is not None else self.config["system_prompt"]
        payload = {
            "model": m,
            "max_tokens": self.config["max_tokens"],
            "stream": True,
            "messages": [
                {"role": "system", "content": sys},
                {"role": ROLE_USER,  "content": prompt},
            ],
        }
        url = f"{BASE_URL}/v1/chat/completions"
        chunks = []
        last_update = time.monotonic()
        t0 = time.monotonic()

        try:
            async with self._session.post(
                url, json=payload, headers=self._h(), timeout=self._to()
            ) as r:
                if r.status >= 400:
                    await self._read_json_response(r)
                async for line in r.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        obj   = json.loads(raw)
                        delta = _mistral_delta_to_text(obj)
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        pass
                    now = time.monotonic()
                    if now - last_update >= _STREAM_UPDATE_INTERVAL and chunks:
                        partial = "".join(chunks)
                        try:
                            await utils.answer(msg, f"<b>🤖</b> {_md_to_html(partial)}▌")
                            last_update = now
                        except Exception:
                            pass
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))

        full = "".join(chunks)
        if not full.strip():
            logger.warning("MistralAI: live stream returned empty content; retrying without stream")
            data = await self._post("/v1/chat/completions", {k: v for k, v in payload.items() if k != "stream"})
            full = _mistral_content_to_text(data["choices"][0]["message"].get("content"))
        elapsed = time.monotonic() - t0
        return full, m, elapsed

    # ── API: Conversations ─────────────────────────────────────────────────────

    async def _conversation(
        self,
        prompt: str,
        tools: list | None = None,
        agent_id: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
    ) -> tuple[str, list[bytes]]:
        payload: dict = {"inputs": prompt, "stream": False}
        if agent_id:
            payload["agent_id"] = agent_id
        elif model:
            payload["model"] = model
        if tools:
            payload["tools"] = tools
        if instructions:
            payload["instructions"] = instructions
        data = await self._post("/v1/conversations", payload)
        text, images = _parse_conversation_output(data)
        resolved_images = []
        for img in images:
            if img.startswith(b"mistral-file://"):
                file_id = img.decode().split("://", 1)[1]
                resolved_images.append(await self._download_file(file_id))
            else:
                resolved_images.append(img)
        return text, resolved_images

    async def _ensure_image_agent(self) -> str | None:
        agent_id = str(self.config["image_agent_id"] or "").strip()
        if agent_id:
            return agent_id
        if not self._ok():
            return None

        agent = await self._agent_create(
            name="Lyra Image Agent",
            model=self.config["image_model"],
            instructions=_IMAGE_AGENT_INSTRUCTIONS,
            tools=[TOOL_IMAGE_GEN],
            description="Generates vivid images for the Telegram bot.",
            completion_args=_IMAGE_COMPLETION_ARGS,
        )
        agent_id = agent.get("id")
        if not agent_id:
            raise RuntimeError("створення image-агента не повернуло ID")
        self.config["image_agent_id"] = agent_id
        return agent_id

    async def _generate_image(self, prompt: str) -> tuple[str, list[bytes]]:
        image_prompt = f"{self.config['image_prompt_prefix']}\n\nUser prompt: {prompt}"
        text = ""
        images: list[bytes] = []

        if self._ok():
            try:
                agent_id = await self._ensure_image_agent()
                if agent_id:
                    text, images = await self._conversation(
                        prompt=image_prompt,
                        agent_id=agent_id,
                    )
            except RuntimeError:
                if not self.config["image_fallback_enabled"]:
                    raise
                logger.warning("Mistral image generation failed; trying HTTP fallback", exc_info=True)

        if not images and self.config["image_fallback_enabled"]:
            fallback = await self._generate_image_via_http(prompt)
            if fallback:
                images = [fallback]
                if not text:
                    text = "Згенеровано через HTTP fallback."

        return text, images

    async def _generate_image_via_http(self, prompt: str) -> bytes | None:
        base_url = str(self.config["image_fallback_base_url"] or "").strip().rstrip("/")
        if not base_url:
            return None
        encoded = quote(prompt.strip(), safe="")
        url = f"{base_url}/prompt/{encoded}?n=1&size=1024x1024&model=flux"
        try:
            async with self._session.get(url, timeout=self._to()) as r:
                data = await r.read()
                if r.status != 200:
                    logger.warning("Image HTTP fallback failed (%s): %s", r.status, data[:200])
                    return None
                return data or None
        except asyncio.TimeoutError:
            logger.warning("Image HTTP fallback timed out for prompt: %s", prompt)
        except Exception as e:
            logger.warning("Image HTTP fallback error for prompt %s: %s", prompt, e)
        return None

    # ── API: Platform Agents CRUD ──────────────────────────────────────────────

    async def _agents_list(self) -> list:
        data = await self._get("/v1/agents?page_size=50")
        return data.get("data", [])

    async def _agent_get(self, agent_id: str) -> dict:
        return await self._get(f"/v1/agents/{agent_id}")

    async def _agent_create(
        self,
        name: str,
        model: str,
        instructions: str,
        tools: list | None = None,
        description: str | None = None,
        completion_args: dict | None = None,
    ) -> dict:
        payload = {"name": name, "model": model, "instructions": instructions,
                   "tools": tools or []}
        if description:
            payload["description"] = description
        if completion_args:
            payload["completion_args"] = completion_args
        return await self._post("/v1/agents", payload)

    async def _agent_update(self, agent_id: str, **kwargs) -> dict:
        return await self._patch(f"/v1/agents/{agent_id}", kwargs)

    async def _agent_delete(self, agent_id: str) -> dict:
        return await self._delete(f"/v1/agents/{agent_id}")

    # ── API: OCR, TTS, Transcription, Embeddings, Moderation ──────────────────

    async def _ocr_b64(self, data: bytes, mime: str) -> str:
        b64 = base64.b64encode(data).decode()
        doc = (
            {"type": "base64_document", "document_media_type": mime, "document_data": b64}
            if "pdf" in mime else
            {"type": "base64_image", "image_media_type": mime, "image_data": b64}
        )
        resp = await self._post("/v1/ocr", {"model": self.config["ocr_model"], "document": doc})
        return "\n\n---\n\n".join(p.get("markdown", "") for p in resp.get("pages", [])).strip()

    async def _embeddings(self, text: str) -> tuple[list, str]:
        data = await self._post("/v1/embeddings", {
            "model": self.config["embed_model"], "input": [text], "encoding_format": "float",
        })
        return data["data"][0]["embedding"], data.get("model", self.config["embed_model"])

    async def _tts(self, text: str) -> bytes:
        url = f"{BASE_URL}/v1/audio/speech"
        h   = {"Authorization": f"Bearer {self.config['api_key']}", "Content-Type": "application/json"}
        payload = {"model": self.config["tts_model"], "voice": self.config["tts_voice"],
                   "input": text, "output_format": "pcm"}
        try:
            async with self._session.post(url, json=payload, headers=h, timeout=self._to()) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:200]}")
                raw = await r.read()
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))
        rate, ch, bits = 24000, 1, 16
        buf = io.BytesIO()
        buf.write(b"RIFF"); buf.write((36 + len(raw)).to_bytes(4, "little"))
        buf.write(b"WAVEfmt "); buf.write((16).to_bytes(4, "little"))
        buf.write((1).to_bytes(2, "little")); buf.write(ch.to_bytes(2, "little"))
        buf.write(rate.to_bytes(4, "little")); buf.write((rate * ch * bits // 8).to_bytes(4, "little"))
        buf.write((ch * bits // 8).to_bytes(2, "little")); buf.write(bits.to_bytes(2, "little"))
        buf.write(b"data"); buf.write(len(raw).to_bytes(4, "little")); buf.write(raw)
        return buf.getvalue()

    async def _transcribe(self, audio: bytes, fname: str = "audio.ogg") -> str:
        url  = f"{BASE_URL}/v1/audio/transcriptions"
        form = aiohttp.FormData()
        form.add_field("model", self.config["transcription_model"])
        form.add_field("file", audio, filename=fname, content_type="audio/ogg")
        try:
            async with self._session.post(
                url, data=form,
                headers={"Authorization": f"Bearer {self.config['api_key']}"},
                timeout=self._to()
            ) as r:
                d = await self._read_json_response(r)
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except aiohttp.ClientError as e:
            raise RuntimeError(str(e))
        if "error" in d:
            raise RuntimeError(str(d["error"]))
        return d.get("text", "")

    async def _moderate(self, text: str) -> dict:
        return await self._post("/v1/moderations",
                                {"model": "mistral-moderation-latest", "input": text})

    async def _models(self) -> list:
        return (await self._get("/v1/models")).get("data", [])

    # ════════════════════════════════════════════════════════════════════════════
    # WATCHER — авто-агент
    # ════════════════════════════════════════════════════════════════════════════

    async def watcher(self, message):
        if not message or not hasattr(message, "sender_id"):
            return
        if not self._ok():
            return
        if self._me and message.sender_id == self._me.id:
            return
        if message.sender_id in self._csv_ints(self.config["agent_ignored_users"]):
            return

        chat_id = message.chat_id
        if chat_id not in self._auto_chats:
            return

        text = (message.raw_text or "").strip()
        if not text:
            return

        # ── Визначаємо тип чату ───────────────────────────────────────────
        is_group    = False
        group_title = None
        try:
            chat     = await message.get_chat()
            is_group = hasattr(chat, "title")
            if is_group:
                group_title = getattr(chat, "title", None)
        except Exception:
            pass

        # ── Фільтрація в групах ───────────────────────────────────────────
        if is_group:
            mode = self.config["agent_group_mode"]
            if mode == "reply":
                if not message.is_reply:
                    return
                rep = await message.get_reply_message()
                if not rep or (self._me and rep.sender_id != self._me.id):
                    return
            elif mode == "addressed":
                is_reply_to_us = False
                if message.is_reply:
                    try:
                        rep = await message.get_reply_message()
                        if rep and self._me and rep.sender_id == self._me.id:
                            is_reply_to_us = True
                    except Exception:
                        pass
                if not is_reply_to_us and not _is_addressed_to_bot(text, self._bot_name):
                    return
            # mode == "all": відповідаємо на все

        # ── Ім'я відправника ──────────────────────────────────────────────
        sender_id = message.sender_id
        try:
            sender      = await message.get_sender()
            sender_name = (
                getattr(sender, "first_name", None)
                or getattr(sender, "username", None)
                or str(sender_id)
            )
        except Exception:
            sender_name = str(sender_id)

        # ── Ліміти авто-відповідей ───────────────────────────────────────
        # Для груп ліміт рахується на чат; для OP/особистих — на користувача.
        limit_scope = self._limit_scope(chat_id, sender_id, is_group, group_title)
        if not self._limit_allowed(limit_scope):
            return

        # ── Спільна пам'ять чату ──────────────────────────────────────────
        # Одна deque на весь чат (і для груп, і для особистих).
        # В групах content зберігається як '[Ім'я]: текст'.
        history = self._get_plain_mem(chat_id)

        # ── Векторна пам'ять ──────────────────────────────────────────────
        extra_context: list | None = None
        query_emb:     list | None = None
        if self.config["vector_memory"] and _HAS_NUMPY:
            try:
                query_emb, _ = await self._embeddings(text)
                extra_context = self._vec_mem_relevant(
                    chat_id, query_emb, self.config["vector_top_k"]
                )
            except Exception:
                pass

        # ── Мовна адаптація ───────────────────────────────────────────────
        system_override = None
        if self.config["agent_auto_lang"] and _HAS_LANGDETECT:
            lang = _detect_lang(text)
            if lang and lang not in ("uk", "ru"):
                system_override = (
                    self.config["agent_system_prompt"]
                    + f"\nВідповідай мовою: {lang}."
                )

        # ── Зберігаємо вхідне повідомлення у пам'ять ─────────────────────
        # В групах — з іменем, щоб модель знала хто говорив
        stored_content = f"[{sender_name}]: {text}" if is_group else text
        history.append({"role": ROLE_USER, "content": stored_content})

        agent_id = self._agent_id()

        # ── Виклик моделі ─────────────────────────────────────────────────
        try:
            if self.config["agent_typing"]:
                async with message.client.action(chat_id, "typing"):
                    reply_text, images = await self._watcher_call(
                        agent_id=agent_id,
                        history=history,
                        text=text,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        is_group=is_group,
                        group_title=group_title,
                        extra_context=extra_context,
                        system_override=system_override,
                    )
            else:
                reply_text, images = await self._watcher_call(
                    agent_id=agent_id,
                    history=history,
                    text=text,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    is_group=is_group,
                    group_title=group_title,
                    extra_context=extra_context,
                    system_override=system_override,
                )
        except RuntimeError as e:
            self._stats.inc(ok=False)
            logger.error("MistralAI watcher error: %s", e)
            try:
                await message.client.send_message(
                    chat_id,
                    self._api_error_answer(e),
                    parse_mode="html",
                    reply_to=message.id,
                )
            except Exception:
                logger.exception("MistralAI watcher failed to send API error message")
            return

        if not (reply_text or "").strip() and not images:
            logger.warning("MistralAI watcher got an empty response")
            reply_text = "⚠️ Mistral API повернув порожню відповідь. Спробуй вимкнути agent_stream або повторити запит."

        # ── Обробляємо Werwolf теги у відповіді ──────────────────────────
        if self._ww_ok():
            try:
                reply_text = await self._ww_process_reply(reply_text, chat_id, sender_id)
            except Exception as e:
                logger.warning("Werwolf process error: %s", e)

        self._stats.inc()
        self._limit_inc(limit_scope)

        # ── Зберігаємо відповідь у пам'ять ───────────────────────────────
        history.append({"role": ROLE_ASSISTANT, "content": reply_text})

        # Векторна пам'ять
        if self.config["vector_memory"] and _HAS_NUMPY:
            self._vec_mem_add(chat_id, ROLE_USER, stored_content, query_emb)
            try:
                rep_emb, _ = await self._embeddings(reply_text)
            except Exception:
                rep_emb = None
            self._vec_mem_add(chat_id, ROLE_ASSISTANT, reply_text, rep_emb)

        # ── Надсилаємо відповідь РЕПЛЕЄМ на повідомлення ─────────────────
        # reply_to=message.id гарантує що бот завжди відповідає на конкретне повідомлення
        for img in images:
            if img.startswith(b"http"):
                await message.client.send_file(
                    chat_id, img.decode(),
                    reply_to=message.id,
                )
            else:
                await message.client.send_file(
                    chat_id, _telegram_image_file(img),
                    reply_to=message.id,
                )

        if reply_text:
            await message.client.send_message(
                chat_id,
                _md_to_html(reply_text),
                parse_mode="html",
                reply_to=message.id,  # ← завжди реплай на повідомлення що спричинило реакцію
            )

    async def _watcher_call(
        self,
        agent_id: str | None,
        history: collections.deque,
        text: str,
        chat_id: int,
        sender_id: int,
        sender_name: str | None,
        is_group: bool,
        group_title: str | None,
        extra_context: list | None,
        system_override: str | None,
    ) -> tuple[str, list]:
        if agent_id:
            # Для Mistral Platform агента — передаємо всю пам'ять як текст
            history_text = "\n".join(
                f"{'User' if m['role'] == ROLE_USER else 'Assistant'}: {m['content']}"
                for m in history
            )
            if self._ww_ok():
                history_text = (
                    _WW_SYSTEM_BLOCK
                    + "\nКонтекст Telegram:\n"
                    + f"sender_id={sender_id}; sender_name={sender_name}; chat_id={chat_id}; "
                    + ("це група." if is_group else "це приватний чат.")
                    + "\n\n"
                    + history_text
                )
            reply_text, images = await self._conversation(
                prompt=history_text,
                agent_id=agent_id,
            )
        else:
            original = self.config["agent_system_prompt"]
            if system_override:
                self.config["agent_system_prompt"] = system_override
            try:
                reply_text = await self._chat_history(
                    history=history,
                    new_msg=text,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    is_group=is_group,
                    group_title=group_title,
                    extra_context=extra_context,
                    system_override=system_override,
                )
            finally:
                if system_override:
                    self.config["agent_system_prompt"] = original
            images = []
        return reply_text, images

    # ════════════════════════════════════════════════════════════════════════════
    # КОМАНДИ
    # ════════════════════════════════════════════════════════════════════════════

    @loader.command(ru_doc="<питання> — Запитати Mistral AI")
    async def mistral(self, message):
        """<питання> — Запитати Mistral AI"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message, self.strings["no_args"])

        agent_id = self._agent_id()
        msg = await utils.answer(message, self.strings["loading"])

        try:
            self._stats.inc()
            if agent_id:
                t0 = time.monotonic()
                text, images = await self._conversation(prompt=args, agent_id=agent_id)
                elapsed     = time.monotonic() - t0
                model_label = f"agent:{agent_id[:8]}"
            elif self.config["agent_stream"]:
                await utils.answer(msg, self.strings["streaming_think"])
                text, model_label, elapsed = await self._stream_to_message(msg, args)
                images = []
            else:
                text, model_label, elapsed = await self._chat(args)
                images = []

            for img in images:
                if img.startswith(b"http"):
                    await message.client.send_file(message.peer_id, img.decode())
                else:
                    await message.client.send_file(message.peer_id, _telegram_image_file(img))

            await utils.answer(
                msg,
                self.strings["chat_answer"].format(
                    question=_html_escape(args), answer=_md_to_html(text),
                    model=model_label, time=elapsed,
                ),
            )
        except RuntimeError as e:
            self._stats.inc(ok=False)
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<питання> — Запитати з контекстом реплаю")
    async def mistralask(self, message):
        """<питання> — Чат з контекстом реплаю або .mistralask контекст :: питання"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])

        args  = utils.get_args_raw(message).strip()
        reply = await message.get_reply_message()

        reply_text = ""
        question   = args

        if reply:
            reply_text = (reply.raw_text or "").strip()
            if not reply_text and reply.photo:
                try:
                    reply_text = await self._ocr_b64(await reply.download_media(bytes), "image/jpeg")
                except Exception:
                    pass
        elif "::" in args:
            parts      = args.split("::", 1)
            reply_text = parts[0].strip()
            question   = parts[1].strip()

        if not question and not reply_text:
            return await utils.answer(message, self.strings["ask_no_reply"])
        if not question:
            question = "Що тут написано / про що це?"

        msg = await utils.answer(message, self.strings["loading"])
        try:
            self._stats.inc()
            if reply_text:
                text, model_label, elapsed = await self._chat_with_reply(question, reply_text)
            else:
                text, model_label, elapsed = await self._chat(question)

            await utils.answer(
                msg,
                self.strings["chat_answer"].format(
                    question=_html_escape(question), answer=_md_to_html(text),
                    model=model_label, time=elapsed,
                ),
            )
        except RuntimeError as e:
            self._stats.inc(ok=False)
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<модель> <питання> — Чат із конкретною моделлю")
    async def mistralm(self, message):
        """<модель> <питання> — Чат із конкретною моделлю"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        args = utils.get_args_raw(message).strip()
        if not args or " " not in args:
            return await utils.answer(
                message,
                "<b>❌ Формат:</b> <code>.mistralm &lt;модель&gt; &lt;текст&gt;</code>",
            )
        model, _, prompt = args.partition(" ")
        msg = await utils.answer(message, self.strings["loading"])
        try:
            self._stats.inc()
            text, used, elapsed = await self._chat(prompt.strip(), model=model.strip())
            await utils.answer(
                msg,
                self.strings["chat_answer"].format(
                    question=_html_escape(prompt.strip()), answer=_md_to_html(text),
                    model=used, time=elapsed,
                ),
            )
        except RuntimeError as e:
            self._stats.inc(ok=False)
            await utils.answer(msg, self._api_error_answer(e))

    # ── Генерація зображень ────────────────────────────────────────────────────

    @loader.command(ru_doc="<промт> — Згенерувати зображення через Mistral")
    async def mistralimg(self, message):
        """<промт> — Генерація зображення"""
        if not self._ok() and not self.config["image_fallback_enabled"]:
            return await utils.answer(message, self.strings["no_api_key"])
        prompt = utils.get_args_raw(message).strip()
        if not prompt:
            return await utils.answer(message, self.strings["no_args"])

        msg = await utils.answer(message, self.strings["generating"])
        try:
            self._stats.inc()
            text, images = await self._generate_image(prompt)
            if not images:
                details = f"\n\n<i>{_md_to_html(text)}</i>" if text else ""
                return await utils.answer(msg, self.strings["img_no_result"] + details)
            caption = self.strings["img_caption"].format(prompt=_html_escape(prompt))
            if text:
                caption = f"{caption}\n\n{_md_to_html(text)}"
            if len(caption) > 1024:
                caption = caption[:1018] + "…"
            for img in images:
                if img.startswith(b"http"):
                    await message.client.send_file(
                        message.peer_id, img.decode(), caption=caption, parse_mode="html"
                    )
                else:
                    await message.client.send_file(
                        message.peer_id, _telegram_image_file(img), caption=caption, parse_mode="html"
                    )
            await msg.delete()
        except RuntimeError as e:
            self._stats.inc(ok=False)
            await utils.answer(msg, self._api_error_answer(e))

    # ── Mistral Platform Agents ────────────────────────────────────────────────

    @loader.command(ru_doc="— Список твоїх Mistral-агентів")
    async def mistralagents(self, message):
        """— Список агентів на Mistral Platform"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            agents = await self._agents_list()
            if not agents:
                return await utils.answer(msg, self.strings["agents_empty"])
            lines = []
            for a in agents:
                active = " ✅" if a["id"] == self._active_agent_id else ""
                tools  = ", ".join(t.get("type", "?") for t in a.get("tools", [])) or "—"
                lines.append(
                    f"• <code>{a['id']}</code> — <b>{a.get('name', '?')}</b>{active}\n"
                    f"  <i>Модель: {a.get('model','?')} | Інструменти: {tools}</i>"
                )
            text = self.strings["agents_header"] + "\n".join(lines)
            if len(text) > 4096:
                text = text[:4090] + "\n..."
            await utils.answer(msg, text)
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<agent_id> — Вибрати активного Mistral-агента")
    async def mistraluse(self, message):
        """<agent_id> — Встановити активного агента (або 'none' щоб скинути)"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        agent_id = utils.get_args_raw(message).strip()
        if not agent_id:
            return await utils.answer(message, self.strings["no_args"])
        if agent_id.lower() == "none":
            self._active_agent_id   = None
            self._active_agent_name = None
            self._save_active_agent()
            return await utils.answer(message, self.strings["agent_unset"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            info = await self._agent_get(agent_id)
            self._active_agent_id   = info["id"]
            self._active_agent_name = info.get("name", agent_id)
            self._save_active_agent()
            await utils.answer(
                msg,
                self.strings["agent_set"].format(id=info["id"], name=info.get("name", "?")),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<agent_id> — Інфо про Mistral-агента")
    async def mistralагентinfo(self, message):
        """<agent_id> — Детальна інформація про агента"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        agent_id = utils.get_args_raw(message).strip() or self._active_agent_id
        if not agent_id:
            return await utils.answer(message, self.strings["no_args"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            a     = await self._agent_get(agent_id)
            tools = ", ".join(t.get("type", "?") for t in a.get("tools", [])) or "—"
            await utils.answer(
                msg,
                self.strings["agent_info"].format(
                    id=a["id"], name=a.get("name", "?"),
                    model=a.get("model", "?"), tools=tools,
                    instructions=a.get("instructions", "—") or "—",
                ),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<назва> | <модель> | <інструкція> | [tools] — Створити агента")
    async def mistralcreate(self, message):
        """Формат: <назва> | <модель> | <інструкція> | [web_search,image_generation,code_interpreter]"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        raw = utils.get_args_raw(message).strip()
        if not raw or "|" not in raw:
            return await utils.answer(
                message,
                "<b>❌ Формат:</b> <code>.mistralcreate Назва | модель | Інструкція | tool1,tool2</code>",
            )
        parts        = [p.strip() for p in raw.split("|")]
        name         = parts[0] if len(parts) > 0 else "My Agent"
        model        = parts[1] if len(parts) > 1 else self.config["chat_model"]
        instructions = parts[2] if len(parts) > 2 else ""
        tool_names   = [t.strip() for t in parts[3].split(",")] if len(parts) > 3 else []
        TOOL_MAP = {
            "web_search":         {"type": "web_search"},
            "image_generation":   {"type": "image_generation"},
            "code_interpreter":   {"type": "code_interpreter"},
            "web_search_premium": {"type": "web_search_premium"},
        }
        tools = [TOOL_MAP[t] for t in tool_names if t in TOOL_MAP]
        msg = await utils.answer(message, self.strings["loading"])
        try:
            agent = await self._agent_create(name, model, instructions, tools)
            await utils.answer(
                msg,
                self.strings["agent_created"].format(id=agent["id"], name=agent.get("name", name)),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<agent_id> — Видалити Mistral-агента")
    async def mistraldelete(self, message):
        """<agent_id> — Видалити агента з Mistral Platform"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        agent_id = utils.get_args_raw(message).strip()
        if not agent_id:
            return await utils.answer(message, self.strings["no_args"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            await self._agent_delete(agent_id)
            if self._active_agent_id == agent_id:
                self._active_agent_id   = None
                self._active_agent_name = None
                self._save_active_agent()
            await utils.answer(msg, self.strings["agent_deleted"].format(id=agent_id))
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    # ── Авто-агент ────────────────────────────────────────────────────────────

    @loader.command(ru_doc="— Увімкнути/вимкнути авто-агент у поточному чаті")
    async def mistralauto(self, message):
        """— Увімкнути/вимкнути авто-агент"""
        chat_id = message.chat_id
        if chat_id in self._auto_chats:
            del self._auto_chats[chat_id]
            self._save_auto()
            await utils.answer(message, self.strings["autoagent_off"].format(chat=chat_id))
        else:
            self._auto_chats[chat_id] = True
            self._save_auto()
            await utils.answer(message, self.strings["autoagent_on"].format(chat=chat_id))

    @loader.command(ru_doc="— Список чатів з активним авто-агентом")
    async def mistralautolist(self, message):
        """— Список чатів де активний авто-агент"""
        if not self._auto_chats:
            return await utils.answer(message, self.strings["autoagent_list_empty"])
        lines = [f"• <code>{cid}</code>" for cid in self._auto_chats]
        await utils.answer(
            message,
            self.strings["autoagent_list"].format(chats="\n".join(lines)),
        )

    @loader.command(ru_doc="— Очистити пам'ять авто-агента у поточному чаті")
    async def mistralclear(self, message):
        """— Очистити пам'ять авто-агента"""
        chat_id = message.chat_id
        if chat_id in self._memory:
            del self._memory[chat_id]
        if hasattr(self, "_vec_mem"):
            for key in list(self._vec_mem.keys()):
                if (isinstance(key, tuple) and key[0] == chat_id) or key == chat_id:
                    del self._vec_mem[key]
        await utils.answer(message, self.strings["memory_cleared"].format(chat=chat_id))

    @loader.command(ru_doc="— Думка агента про співрозмовника та короткий переказ історії")
    async def mistralthoughts(self, message):
        """— Показати, що авто-агент думає про співрозмовника, без запису у пам'ять"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])

        chat_id = message.chat_id
        agent_id = self._agent_id()
        if chat_id not in self._auto_chats or not agent_id:
            return await utils.answer(message, self.strings["agent_thoughts_unavailable"])

        history = self._memory.get(chat_id)
        if not history:
            return await utils.answer(message, self.strings["agent_thoughts_empty"])

        history_text = self._history_for_prompt(history)
        if not history_text:
            return await utils.answer(message, self.strings["agent_thoughts_empty"])

        msg = await utils.answer(message, self.strings["loading"])
        prompt = (
            "Ти маєш історію розмови авто-агента нижче. "
            "Це одноразовий службовий запит для власника акаунту: НЕ змінюй свою поведінку, "
            "НЕ вважай цей запит частиною майбутньої розмови і НЕ давай інструкцій собі на майбутнє.\n\n"
            "Стисло українською дай:\n"
            "1) що ти думаєш про поточного співрозмовника / людей у чаті;\n"
            "2) короткий переказ важливої історії розмови;\n"
            "3) важливі факти або патерни, які варто пам'ятати власнику.\n"
            "Не вигадуй того, чого немає в історії. Якщо даних мало — так і скажи.\n\n"
            f"Історія:\n{history_text}"
        )
        try:
            answer, _images = await self._conversation(prompt=prompt, agent_id=agent_id)
            await utils.answer(
                msg,
                self.strings["agent_thoughts_result"].format(text=_md_to_html(answer or "—")),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    # ── Werwolf команди ───────────────────────────────────────────────────────

    @loader.command(ru_doc="<ключ> — Встановити Werwolf API ключ")
    async def wwkey(self, message):
        """<ключ> — Встановити Werwolf API ключ для інтеграції з агентом"""
        key = utils.get_args_raw(message).strip()
        if not key:
            cur = str(self.config["werwolf_api_key"]).strip()
            if cur:
                await utils.answer(message, f"🔑 Werwolf ключ: <code>{cur[:6]}***{cur[-4:]}</code>")
            else:
                await utils.answer(message, "❌ Werwolf ключ не встановлено.\n<code>.wwkey YOUR_KEY</code>")
            return
        self.config["werwolf_api_key"] = key
        masked = f"{key[:6]}***{key[-4:]}"
        msg = await utils.answer(message, f"✅ Werwolf ключ збережено: <code>{masked}</code>")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await (msg[0] if isinstance(msg, list) else msg).delete()
        except Exception:
            pass

    @loader.command(ru_doc="— Перевірити Werwolf підключення")
    async def wwtest(self, message):
        """— Перевірити Werwolf API підключення"""
        if not self._ww_ok():
            return await utils.answer(message, "❌ Werwolf ключ не вказано або інтеграцію вимкнено.")
        msg = await utils.answer(message, "<code>⏳ Тестую Werwolf...</code>")
        try:
            data  = await self._ww_get("api/profile")
            bal   = data.get("balance", {})
            coins = bal.get("coins", 0)
            gold  = bal.get("gold", 0)
            await utils.answer(msg, f"✅ <b>Werwolf API працює</b>\n⭐ {coins:.0f}  🥇 {gold:.1f}")
        except RuntimeError as e:
            await utils.answer(msg, f"❌ <b>Помилка:</b> <code>{e}</code>")

    # ── Інструменти ───────────────────────────────────────────────────────────

    @loader.command(ru_doc="— Список моделей Mistral")
    async def mistralmodels(self, message):
        """— Показати всі доступні моделі"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            models = await self._models()
            if not models:
                return await utils.answer(msg, "<b>❌ Моделі не знайдено.</b>")
            lines = []
            for m in sorted(models, key=lambda x: x.get("id", "")):
                caps = m.get("capabilities", {})
                tags = (
                    ("💬" if caps.get("completion_chat") else "") +
                    ("👁" if caps.get("vision") else "") +
                    ("🔧" if caps.get("function_calling") else "") +
                    ("🎯" if caps.get("fine_tuning") else "")
                )
                lines.append(f"• <code>{m.get('id','?')}</code> {tags}")
            text = self.strings["models_header"] + "\n".join(lines)
            if len(text) > 4096:
                text = text[:4090] + "\n..."
            await utils.answer(msg, text)
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="— OCR зображення або PDF")
    async def mistralocr(self, message):
        """— OCR зображення/PDF"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        reply = await message.get_reply_message()
        msg   = await utils.answer(message, self.strings["loading"])
        try:
            if reply and reply.photo:
                result = await self._ocr_b64(await reply.download_media(bytes), "image/jpeg")
            elif reply and reply.document:
                mime   = reply.document.mime_type or ""
                result = await self._ocr_b64(
                    await reply.download_media(bytes),
                    "application/pdf" if "pdf" in mime else "image/jpeg",
                )
            else:
                return await utils.answer(msg, self.strings["ocr_no_media"])
            output = self.strings["ocr_result"].format(
                text=_md_to_html(result) if result else "<i>(текст не знайдено)</i>"
            )
            if len(output) > 4096:
                output = output[:4090] + "\n..."
            await utils.answer(msg, output)
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<текст> — Синтез мовлення (TTS)")
    async def mistralvoice(self, message):
        """<текст> — TTS"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message, self.strings["no_args"])
        msg = await utils.answer(message, self.strings["tts_generating"])
        try:
            wav = await self._tts(args)
            await message.client.send_file(
                message.peer_id, io.BytesIO(wav),
                voice_note=True, caption=self.strings["tts_done"],
                reply_to=message.reply_to_msg_id,
            )
            await msg.delete()
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="— Транскрипція аудіо")
    async def mistraltranscribe(self, message):
        """— Транскрипція голосового/аудіо"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        reply = await message.get_reply_message()
        if not reply or not (reply.voice or reply.audio or reply.document):
            return await utils.answer(message, self.strings["no_audio"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            audio = await reply.download_media(bytes)
            fname = "audio.mp3" if reply.audio else "audio.ogg"
            text  = await self._transcribe(audio, fname)
            await utils.answer(
                msg,
                self.strings["transcription"].format(
                    text=_html_escape(text) if text else "<i>(не розпізнано)</i>"
                ),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<текст> — Ембединг тексту")
    async def mistralembed(self, message):
        """<текст> — Векторне представлення"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message, self.strings["no_args"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            vec, model = await self._embeddings(args)
            await utils.answer(
                msg,
                self.strings["embed_result"].format(
                    model=model, dim=len(vec),
                    preview=", ".join(f"{v:.4f}" for v in vec[:5]),
                ),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="<текст> — Модерація тексту")
    async def mistralmod(self, message):
        """<текст> — Перевірити текст"""
        if not self._ok():
            return await utils.answer(message, self.strings["no_api_key"])
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message, self.strings["no_args"])
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data    = await self._moderate(args)
            res     = data.get("results", [{}])[0]
            flagged = res.get("flagged", False)
            active  = [k for k, v in res.get("categories", {}).items() if v]
            await utils.answer(
                msg,
                self.strings["moderation"].format(
                    safe="✅ Так" if not flagged else "🚫 Ні",
                    cats=", ".join(active) or "немає",
                ),
            )
        except RuntimeError as e:
            await utils.answer(msg, self._api_error_answer(e))

    @loader.command(ru_doc="— Статистика використання")
    async def mistralstats(self, message):
        """— Статистика запитів та аптайм"""
        await utils.answer(
            message,
            self.strings["stats"].format(summary=self._stats.summary()),
        )

    @loader.command(ru_doc="— Перевірити статус залежностей")
    async def mistraldeps(self, message):
        """— Перевірити залежності"""
        ww_status = "✅" if self._ww_ok() else ("⚠️ немає ключа" if not self.config["werwolf_api_key"] else "❌ вимкнено")
        lines = [
            f"{'✅' if _HAS_LANGDETECT else '❌'} <code>langdetect</code> — авто-мова",
            f"{'✅' if _HAS_NUMPY else '❌'} <code>numpy</code> — векторна пам'ять",
            f"{ww_status} <code>werwolf</code> — ігрова інтеграція",
        ]
        await utils.answer(
            message,
            self.strings["deps_status"].format(status="\n".join(lines)),
        )

    @loader.command(ru_doc="— Список команд")
    async def mistralhelp(self, message):
        """— Список усіх команд MistralAI"""
        active = (
            f"\n<b>🤖 Активний агент:</b> <code>{self._active_agent_id}</code> "
            f"(<b>{self._active_agent_name}</b>)"
            if self._active_agent_id else ""
        )
        ww = "✅" if self._ww_ok() else "❌"
        deps = (
            f"\n<b>📦 Deps:</b> langdetect={'✅' if _HAS_LANGDETECT else '❌'} "
            f"numpy={'✅' if _HAS_NUMPY else '❌'} "
            f"werwolf={ww}"
        )
        await utils.answer(
            message,
            f"<b>🤖 MistralAI v4.3 — команди:</b>{active}{deps}\n\n"
            "<b>💬 Чат</b>\n"
            "<code>.mistral &lt;питання&gt;</code> — Чат\n"
            "<code>.mistralask [питання]</code> — Чат з контекстом реплаю\n"
            "<code>.mistralm &lt;модель&gt; &lt;питання&gt;</code> — Конкретна модель\n"
            "<code>.mistralmodels</code> — Список моделей\n\n"
            "<b>🎨 Зображення</b>\n"
            "<code>.mistralimg &lt;промт&gt;</code> — Генерація зображення через image_generation tool\n"
            "<i>Порада: налаштуй image_model та image_prompt_prefix у .config MistralAI</i>\n\n"
            "<b>🤖 Mistral Platform Agents</b>\n"
            "<code>.mistralagents</code> — Список агентів\n"
            "<code>.mistraluse &lt;agent_id&gt;</code> — Вибрати агента\n"
            "<code>.mistraluse none</code> — Скинути агента\n"
            "<code>.mistralагентinfo [id]</code> — Інфо про агента\n"
            "<code>.mistralcreate Назва | модель | Інструкція | tools</code>\n"
            "<code>.mistraldelete &lt;agent_id&gt;</code>\n\n"
            "<b>👻 Авто-агент</b>\n"
            "<code>.mistralauto</code> — Увімк/вимк у чаті\n"
            "<code>.mistralautolist</code> — Активні чати\n"
            "<code>.mistralclear</code> — Очистити пам'ять\n"
            "<code>.mistralthoughts</code> — Думка агента + коротка історія\n"
            "<i>Ліміти авто-відповідей: 10/день і 80/місяць на групу або OP-користувача; винятки у .config</i>\n\n"
            "<b>🎮 Werwolf інтеграція</b>\n"
            "<code>.wwkey &lt;ключ&gt;</code> — Встановити Werwolf ключ\n"
            "<code>.wwtest</code> — Перевірити підключення\n"
            "<i>Агент сам звертається до Werwolf коли питають про статистику/монети/петів тощо</i>\n\n"
            "<b>🛠 Інструменти</b>\n"
            "<code>.mistralocr</code> — OCR фото/PDF\n"
            "<code>.mistralvoice &lt;текст&gt;</code> — TTS\n"
            "<code>.mistraltranscribe</code> — Транскрипція аудіо\n"
            "<code>.mistralembed &lt;текст&gt;</code> — Ембединг\n"
            "<code>.mistralmod &lt;текст&gt;</code> — Модерація\n\n"
            "<b>📊 Утиліти</b>\n"
            "<code>.mistralstats</code> — Статистика запитів\n"
            "<code>.mistraldeps</code> — Статус залежностей\n\n"
            "<i>⚙️ <code>.config MistralAI</code></i>",
        )
