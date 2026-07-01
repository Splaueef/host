# meta developer: @Huai_Baike

__version__ = (4, 1, 1)

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import aiohttp
from .. import loader, utils

logger = logging.getLogger(__name__)

_MSG_CHAR_LIMIT   = 300   # макс символів одного повідомлення в історії
_SUMMARY_CHAR_LIMIT = 400 # макс символів summary старих повідомлень
_SUMMARY_ITEM_LIMIT = 80  # макс символів одного рядка у summary


# ── Prompt builder ────────────────────────────────────────────────────────────

class _PromptCache:
    """
    Кешує зібраний system prompt.
    Перебудовує тільки якщо конфіг змінився (порівняння по hash).
    """
    __slots__ = ("_hash", "_prompt")

    def __init__(self):
        self._hash   = ""
        self._prompt = ""

    def get(self, cfg: dict) -> str:
        # Мінімальний ключ — тільки змістовні поля
        raw = "".join([
            cfg["system_prompt"],
            cfg["style"],
            cfg["personality"],
            cfg["behavior"],
        ])
        h = hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()
        if h != self._hash:
            self._hash   = h
            self._prompt = _build_system_prompt(cfg)
        return self._prompt


def _build_system_prompt(cfg: dict) -> str:
    """
    Будує компактний system prompt.
    Замість 4 окремих блоків з заголовками — один щільний абзац.
    Менше службових токенів (заголовки СТИЛЬ/ПОВЕДІНКА тощо), більше змісту.
    """
    who   = cfg["system_prompt"].strip().rstrip(".")
    style = cfg["style"].strip().rstrip(".")
    pers  = cfg["personality"].strip().rstrip(".")
    behav = cfg["behavior"].strip().rstrip(".")

    return (
        f"{who}. {pers}. "
        f"{behav}. "
        f"{style}."
    )


# ── History helpers ───────────────────────────────────────────────────────────

def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _compress_history(messages: list, keep_recent: int) -> list:
    """
    Старі повідомлення → один system-рядок summary.
    Нові keep_recent — відправляються як є.
    Результат: мінімальний payload при збереженні контексту.
    """
    if len(messages) <= keep_recent:
        return messages

    old    = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    parts = []
    for m in old:
        label = "Я" if m["role"] == "assistant" else "Юзер"
        parts.append(f"{label}: {_truncate(m['content'], _SUMMARY_ITEM_LIMIT)}")

    summary = _truncate(" | ".join(parts), _SUMMARY_CHAR_LIMIT)
    return [
        {"role": "system", "content": f"[Раніше: {summary}]"},
        *recent,
    ]


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """По одному запиту на чат. Решта отримує 'busy'."""

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]


# ── Module ────────────────────────────────────────────────────────────────────

@loader.tds
class GemmaSelf(loader.Module):
    """ШІ-двійник на базі Ollama"""

    strings = {
        "name": "GemmaSelf",
        "added":   "✅ <b>Чат додано до білого списку</b>",
        "removed": "❌ <b>Чат видалено з білого списку</b>",
        "cleared": "🗑 <b>Історію чату очищено</b>",
        "busy":    "⏳ Зачекай, ще обробляю попереднє повідомлення…",
        "thinking": "⏳ Думаю…",
        "quota_exhausted": (
            "⛔️ Ліміт ШІ-запитів вичерпано. "
            "Нові запити відновляться <b>{reset_at}</b>."
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("allowed_chats", [], "Список ID чатів"),
            loader.ConfigValue("api_url", "http://localhost:11434/v1", "URL Ollama"),
            loader.ConfigValue("model", "gemma3-limited:latest", "Назва моделі"),
            loader.ConfigValue(
                "system_prompt",
                "Ти — Станіслав (Стас). Відповідаєш у Telegram від свого імені. "
                "Розробник Telegram-ботів і backend систем. Працюєш з Python, aiogram, "
                "hydrogram, Linux/VPS і Debian. Власні проєкти під брендом RotKranz.",
                "Хто ти",
            ),
            loader.ConfigValue(
                "style",
                "Пиши як звичайна людина в Telegram: 1-2 речення, без markdown і форматування.",
                "Стиль",
            ),
            loader.ConfigValue(
                "personality",
                "Спокійний, технічний, трохи іронічний. Думаєш як програміст. Не любиш воду.",
                "Характер",
            ),
            loader.ConfigValue(
                "behavior",
                "Відповідай як реальна людина. Не кажи що ти AI. Можна вживати repo, bot, server.",
                "Поведінка",
            ),
            loader.ConfigValue("max_history",  20, "Максимум повідомлень в історії"),
            loader.ConfigValue("keep_recent",   6, "Скільки останніх не стискати"),
            loader.ConfigValue("max_tokens",  120, "Ліміт токенів відповіді"),
            loader.ConfigValue("timeout",     120, "Таймаут запиту (секунди)"),
            loader.ConfigValue("stream", True, "Поступово редагувати повідомлення під час генерації"),
            loader.ConfigValue("stream_edit_interval", 1.2, "Мінімальна пауза між редагуваннями (секунди)"),
            loader.ConfigValue("stream_min_chars", 24, "Мінімум нових символів перед редагуванням"),
            loader.ConfigValue("daily_user_limit", 0, "Денний ліміт ШІ-запитів на користувача (0=∞)"),
            loader.ConfigValue("limit_exempt_chats", [], "ID чатів, де ліміти ШІ-запитів не діють"),
            loader.ConfigValue("reply_all_in_allowed_groups", True, "Відповідати всім користувачам у дозволених групах"),
        )
        self._me      = None
        self._rl      = _RateLimiter()
        self._session = None
        self._pcache  = _PromptCache()

    async def client_ready(self, client, db):
        self._me      = await client.get_me()
        self._session = aiohttp.ClientSession()

    async def on_unload(self):
        if self._session:
            await self._session.close()

    # ── Storage ───────────────────────────────────────────────────────────

    def _get_history(self, chat_id: int) -> list:
        return self.get("history", {}).get(str(chat_id), [])

    def _save_history(self, chat_id: int, messages: list):
        history = self.get("history", {})
        history[str(chat_id)] = messages[-self.config["max_history"]:]
        self.set("history", history)

    def _append(self, chat_id: int, role: str, content: str):
        msgs = self._get_history(chat_id)
        msgs.append({"role": role, "content": _truncate(content, _MSG_CHAR_LIMIT)})
        self._save_history(chat_id, msgs)


    # ── Quotas ───────────────────────────────────────────────────────────

    @staticmethod
    def _quota_day() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _quota_reset_at() -> str:
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        reset_at = datetime.combine(tomorrow, datetime.min.time(), timezone.utc)
        return reset_at.strftime("%Y-%m-%d %H:%M UTC")

    def _quota_state(self) -> dict:
        state = self.get("quota", {})
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _ids(value) -> set[int]:
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = str(value or "").replace(";", ",").split(",")

        result = set()
        for item in items:
            try:
                result.add(int(item))
            except (TypeError, ValueError):
                logger.warning("GemmaSelf: некоректний ID у списку лімітів: %s", item)
        return result

    def _quota_key(self, user_id: int) -> str:
        return str(user_id or 0)

    def _quota_exempt(self, chat_id: int) -> bool:
        return chat_id in self._ids(self.config["limit_exempt_chats"])

    def _quota_used(self, user_id: int) -> int:
        item = self._quota_state().get(self._quota_key(user_id), {})
        return int(item.get("count", 0)) if item.get("day") == self._quota_day() else 0

    def _quota_allowed(self, chat_id: int, user_id: int) -> bool:
        if self._quota_exempt(chat_id):
            return True

        limit = int(self.config["daily_user_limit"] or 0)
        return limit <= 0 or self._quota_used(user_id) < limit

    def _quota_inc(self, user_id: int):
        limit = int(self.config["daily_user_limit"] or 0)
        if limit <= 0:
            return

        state = self._quota_state()
        key = self._quota_key(user_id)
        state[key] = {"day": self._quota_day(), "count": self._quota_used(user_id) + 1}
        self.set("quota", state)

    @staticmethod
    def _retry_after_reset_at(value: str | None) -> str | None:
        if not value:
            return None
        try:
            seconds = max(0, int(value))
        except (TypeError, ValueError):
            return None

        reset_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        return reset_at.strftime("%Y-%m-%d %H:%M UTC")

    def _quota_message(self, reset_at: str | None = None) -> str:
        return self.strings["quota_exhausted"].format(reset_at=reset_at or self._quota_reset_at())

    @staticmethod
    def _is_quota_message(text: str) -> bool:
        return text.startswith("⛔️ Ліміт ШІ-запитів вичерпано.")

    # ── AI ────────────────────────────────────────────────────────────────

    def _build_payload(self, history: list, stream: bool = False) -> dict:
        system     = self._pcache.get(dict(self.config))   # з кешу, не перебудовує щоразу
        compressed = _compress_history(history, self.config["keep_recent"])

        return {
            "model":      self.config["model"],
            "max_tokens": self.config["max_tokens"],
            "temperature": 0.8,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                *compressed,
            ],
        }

    async def _call_ollama(self, history: list):
        url     = f"{self.config['api_url'].rstrip('/')}/chat/completions"
        payload = self._build_payload(history)

        t0 = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=self.config["timeout"])
            async with self._session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status == 429:
                    logger.warning("GemmaSelf: API quota/rate limit exhausted")
                    reset_at = self._retry_after_reset_at(resp.headers.get("Retry-After"))
                    return self._quota_message(reset_at)

                data = await resp.json()
                logger.debug("GemmaSelf: %.1fс", time.monotonic() - t0)
                return data["choices"][0]["message"]["content"].strip()
        except asyncio.TimeoutError:
            logger.warning("GemmaSelf: таймаут")
        except Exception as e:
            logger.error("GemmaSelf: %s", e)
        return None

    async def _stream_ollama(self, history: list):
        url     = f"{self.config['api_url'].rstrip('/')}/chat/completions"
        payload = self._build_payload(history, stream=True)

        t0 = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=self.config["timeout"])
            async with self._session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status == 429:
                    logger.warning("GemmaSelf stream: API quota/rate limit exhausted")
                    reset_at = self._retry_after_reset_at(resp.headers.get("Retry-After"))
                    yield self._quota_message(reset_at)
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue

                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break

                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content

                logger.debug("GemmaSelf stream: %.1fс", time.monotonic() - t0)
        except asyncio.TimeoutError:
            logger.warning("GemmaSelf stream: таймаут")
        except Exception as e:
            logger.error("GemmaSelf stream: %s", e)

    async def _safe_edit(self, message, text: str):
        try:
            await message.edit(text[:4096])
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                logger.debug("GemmaSelf edit: %s", e)

    # ── Commands ──────────────────────────────────────────────────────────

    @loader.command()
    async def gmself(self, message):
        """Додати/видалити поточний чат з білого списку"""
        chat_id = message.chat_id
        allowed = list(self.config["allowed_chats"])

        if chat_id in allowed:
            allowed.remove(chat_id)
            self.config["allowed_chats"] = allowed
            msg = await utils.answer(message, self.strings["removed"])
        else:
            allowed.append(chat_id)
            self.config["allowed_chats"] = allowed
            msg = await utils.answer(message, self.strings["added"])

        await asyncio.sleep(2)
        await (msg[0] if isinstance(msg, list) else msg).delete()

    @loader.command()
    async def gmclear(self, message):
        """Очистити історію поточного чату"""
        self._save_history(message.chat_id, [])
        msg = await utils.answer(message, self.strings["cleared"])
        await asyncio.sleep(2)
        await (msg[0] if isinstance(msg, list) else msg).delete()

    # ── Watcher ───────────────────────────────────────────────────────────

    async def watcher(self, message):
        if not hasattr(message, "out") or message.out or not getattr(message, "text", None):
            return

        chat_id = message.chat_id
        if chat_id not in self.config["allowed_chats"]:
            return

        is_private    = getattr(message, "is_private", False)
        is_reply_to_me = False
        if message.is_reply:
            try:
                reply = await message.get_reply_message()
                if reply and self._me and reply.sender_id == self._me.id:
                    is_reply_to_me = True
            except Exception:
                pass

        if not is_private and not is_reply_to_me and not self.config["reply_all_in_allowed_groups"]:
            return

        sender_id = getattr(message, "sender_id", 0) or 0
        if not self._quota_allowed(chat_id, sender_id):
            await message.reply(self._quota_message())
            return

        lock = self._rl.get(chat_id)
        if lock.locked():
            await message.reply(self.strings["busy"])
            return

        async with lock:
            name = "Друже"
            try:
                sender = await message.get_sender()
                name   = getattr(sender, "first_name", None) or "Друже"
            except Exception:
                pass

            self._append(chat_id, "user", f"{name}: {message.text}")

            try:
                if self.config["stream"]:
                    answer = await message.reply(self.strings["thinking"])
                    response = ""
                    last_edit_at = 0
                    last_edit_len = 0

                    async with message.client.action(chat_id, "typing"):
                        async for part in self._stream_ollama(self._get_history(chat_id)):
                            response += part
                            now = time.monotonic()
                            enough_time = now - last_edit_at >= self.config["stream_edit_interval"]
                            enough_text = len(response) - last_edit_len >= self.config["stream_min_chars"]

                            if enough_time and enough_text:
                                await self._safe_edit(answer, response.strip() or self.strings["thinking"])
                                last_edit_at = now
                                last_edit_len = len(response)

                    response = response.strip()
                    if response:
                        await self._safe_edit(answer, response)
                        self._append(chat_id, "assistant", response)
                        if not self._is_quota_message(response) and not self._quota_exempt(chat_id):
                            self._quota_inc(sender_id)
                    else:
                        await answer.delete()
                else:
                    async with message.client.action(chat_id, "typing"):
                        response = await self._call_ollama(self._get_history(chat_id))

                    if response:
                        await message.reply(response)
                        self._append(chat_id, "assistant", response)
                        if not self._is_quota_message(response) and not self._quota_exempt(chat_id):
                            self._quota_inc(sender_id)
            except Exception as e:
                logger.error("GemmaSelf: %s", e)
