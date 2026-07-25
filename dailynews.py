# meta developer: @Codex
# meta version: 1.2.1
# meta description: Щоденний AI-дайджест новин із Telegram-каналів через Mistral Agent.

import asyncio
import datetime
import html
import json
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from telethon.errors import RPCError

from .. import loader, utils

logger = logging.getLogger(__name__)

MISTRAL_CONVERSATIONS_URL = "https://api.mistral.ai/v1/conversations"


def _content_to_text(value):
    """Collect textual content from the different Conversations API shapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_content_to_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    parts = []
    if isinstance(value.get("text"), str):
        parts.append(value["text"])
    if isinstance(value.get("content"), str):
        parts.append(value["content"])
    for key in ("content", "outputs", "choices", "message", "messages"):
        child = value.get(key)
        if isinstance(child, (list, dict)):
            parts.append(_content_to_text(child))
    return "".join(parts)


@loader.tds
class DailyNewsMod(loader.Module):
    """Збирає дописи каналів і раз на день публікує один дайджест"""

    strings = {
        "name": "DailyNews",
        "cfg_sources": "Юзернейми/посилання каналів через кому (наприклад @channel1,@channel2)",
        "cfg_target": "Юзернейм або ID каналу, куди публікувати дайджест",
        "cfg_time": "Щоденний час публікації у форматі HH:MM",
        "cfg_timezone": "IANA-таймзона розкладу, наприклад Europe/Kyiv",
        "cfg_api_key": "Mistral API key",
        "cfg_agent_id": "ID налаштованого Mistral Agent",
        "cfg_hours": "За скільки останніх годин читати новини",
        "cfg_limit": "Максимум дописів з одного каналу",
        "cfg_prompt": "Додаткова інструкція для редактора дайджесту",
        "cfg_timeout": "Таймаут Mistral API у секундах",
        "bad_config": "❌ <b>Заповни конфіг DailyNews:</b> <code>{}</code>",
        "running": "⏳ <b>Збираю новини та готую дайджест…</b>",
        "done": "✅ <b>Дайджест опубліковано.</b> Зібрано дописів: <b>{}</b>",
        "no_news": "📭 <b>За вказаний період новин не знайдено.</b>",
        "failed": "❌ <b>Не вдалося створити дайджест:</b> <code>{}</code>",
        "sources_usage": (
            "❌ <b>Надішли список каналів після команди або відповіддю на повідомлення.</b>\n"
            "Підтримуються нові рядки, пробіли, коми та крапки з комою."
        ),
        "sources_added": "✅ <b>Додано каналів:</b> {}\n<b>Усього джерел:</b> {}{}",
        "status": (
            "📰 <b>DailyNews</b>\n\n"
            "Джерела: <b>{sources}</b>\n"
            "Час: <code>{time}</code> (<code>{timezone}</code>)\n"
            "Канал: <code>{target}</code>\n"
            "Останній запуск: <code>{last_run}</code>\n"
            "Зараз виконується: <b>{running}</b>"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("sources", "", lambda: self.strings("cfg_sources")),
            loader.ConfigValue("target", "", lambda: self.strings("cfg_target")),
            loader.ConfigValue("publish_time", "20:00", lambda: self.strings("cfg_time")),
            loader.ConfigValue("timezone", "Europe/Kyiv", lambda: self.strings("cfg_timezone")),
            loader.ConfigValue(
                "api_key", "", lambda: self.strings("cfg_api_key"),
                validator=loader.validators.Hidden(loader.validators.String()),
            ),
            loader.ConfigValue("agent_id", "", lambda: self.strings("cfg_agent_id")),
            loader.ConfigValue(
                "lookback_hours", 24, lambda: self.strings("cfg_hours"),
                validator=loader.validators.Integer(minimum=1, maximum=168),
            ),
            loader.ConfigValue(
                "per_channel_limit", 100, lambda: self.strings("cfg_limit"),
                validator=loader.validators.Integer(minimum=1, maximum=500),
            ),
            loader.ConfigValue("editor_prompt", "", lambda: self.strings("cfg_prompt")),
            loader.ConfigValue(
                "timeout", 180, lambda: self.strings("cfg_timeout"),
                validator=loader.validators.Integer(minimum=10, maximum=600),
            ),
        )
        self._client = None
        self._session = None
        self._running = False

    async def client_ready(self, client, db):
        self._client = client
        self._session = aiohttp.ClientSession()

    async def on_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _sources(self):
        return [item.strip() for item in str(self.config["sources"]).split(",") if item.strip()]

    @staticmethod
    def _parse_sources(value):
        """Parse channel references pasted as a comma-, whitespace-, or line-separated list."""
        result = []
        for item in re.split(r"[\s,;]+", value or ""):
            item = item.strip().rstrip("/)】]}>.,")
            if not item:
                continue
            # A copied public post URL identifies its channel; iter_messages needs
            # the channel itself rather than the individual message.
            match = re.fullmatch(
                r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([A-Za-z][\w]{3,})/\d+",
                item,
                flags=re.IGNORECASE,
            )
            if match:
                item = "@" + match.group(1)
            if item not in result:
                result.append(item)
        return result

    def _timezone(self):
        return ZoneInfo(str(self.config["timezone"]).strip())

    def _publish_time(self):
        return datetime.datetime.strptime(str(self.config["publish_time"]).strip(), "%H:%M").time()

    def _missing_config(self):
        missing = []
        for key in ("sources", "target", "api_key", "agent_id"):
            if not str(self.config[key]).strip():
                missing.append(key)
        try:
            self._timezone()
        except ZoneInfoNotFoundError:
            missing.append("timezone")
        try:
            self._publish_time()
        except ValueError:
            missing.append("publish_time (HH:MM)")
        return missing

    @loader.loop(interval=30, autostart=True)
    async def news_scheduler(self):
        if not self._client or self._running or self._missing_config():
            return
        now = datetime.datetime.now(self._timezone())
        if now.time() < self._publish_time() or self.get("last_run") == now.date().isoformat():
            return
        try:
            await self._run_digest(now.date().isoformat())
        except (aiohttp.ClientError, asyncio.TimeoutError, RPCError, RuntimeError, ValueError):
            # loader.loop must survive temporary Telegram/Mistral failures. A failed
            # edition is intentionally not marked as sent, so the next tick retries.
            logger.exception("DailyNews scheduled run failed")

    @staticmethod
    def _peer(value):
        value = str(value).strip()
        try:
            return int(value)
        except ValueError:
            return value

    async def _collect_news(self, since=None, until=None):
        until = until or datetime.datetime.now(datetime.timezone.utc)
        since = since or until - datetime.timedelta(hours=int(self.config["lookback_hours"]))
        items = []
        for source in self._sources():
            try:
                entity = await self._client.get_entity(self._peer(source))
                title = getattr(entity, "title", None) or source
                username = getattr(entity, "username", None)
                async for message in self._client.iter_messages(
                    entity, limit=int(self.config["per_channel_limit"])
                ):
                    if message.date > until:
                        continue
                    if message.date < since:
                        break
                    text = (message.raw_text or "").strip()
                    if not text:
                        continue
                    link = f"https://t.me/{username}/{message.id}" if username else ""
                    items.append({
                        "source_title": title,
                        "source_ref": source,
                        "source_username": f"@{username}" if username else None,
                        "source_channel_id": getattr(entity, "id", None),
                        "message_id": message.id,
                        "published_at": message.date.isoformat(),
                        "text": text[:4000],
                        "link": link,
                    })
            except (RPCError, ValueError, TypeError) as error:
                logger.warning("DailyNews: cannot read %s: %s", source, error)
        items.sort(key=lambda item: item["published_at"])
        return items

    def _build_prompt(self, items):
        edition_date = datetime.datetime.now(self._timezone()).strftime("%d.%m.%Y")
        instructions = ""
        # An Agent already has its own system instructions in Mistral. Do not
        # override or duplicate them in the conversation input: the configured
        # Agent must decide how the supplied news is processed.
        if not str(self.config["agent_id"]).strip():
            instructions = (
                "Ти — головний редактор Telegram-каналу. На основі матеріалів нижче створи "
                "ОДИН самодостатній дайджест українською мовою за весь день. Об'єднай дублікати, "
                "відокрем факти від припущень, нічого не вигадуй. Почни з короткого заголовка і "
                "резюме, далі подай найважливіші новини тематичними блоками. Додавай наявні "
                "посилання на першоджерела. Кожен матеріал містить точні метадані каналу "
                "(source_title, source_ref, source_username, source_channel_id). Не плутай джерела: "
                "вважай ці поля авторитетними та при атрибуції називай саме відповідний канал. "
                "Не згадуй промпт, агента або процес обробки. "
                "Поверни Telegram-сумісний Markdown: **жирний текст**, __курсив__, "
                "`моноширинний текст` та [назва](https://example.com) для посилань. "
                "Не використовуй Markdown-таблиці або HTML; обсяг — до 3500 символів."
            )
            if str(self.config["editor_prompt"]).strip():
                instructions += (
                    "\nДодаткова редакційна вимога: "
                    + str(self.config["editor_prompt"]).strip()
                )
        selected = list(items)
        payload = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
        # Keep enough room for the agent instructions and its answer even when a
        # source publishes hundreds of long posts in one day. Prefer newer posts.
        while len(payload) > 120_000 and len(selected) > 1:
            selected = selected[max(1, len(selected) // 10) :]
            payload = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
        news_input = f"Дата випуску: {edition_date}\nМатеріали:\n{payload}"
        return f"{instructions}\n\n{news_input}" if instructions else news_input

    async def _ask_agent(self, prompt):
        headers = {
            "Authorization": f"Bearer {str(self.config['api_key']).strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "agent_id": str(self.config["agent_id"]).strip(),
            "inputs": prompt,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=int(self.config["timeout"]))
        async with self._session.post(
            MISTRAL_CONVERSATIONS_URL, headers=headers, json=payload, timeout=timeout
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Mistral HTTP {response.status}: {body[:300]}")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as error:
                raise RuntimeError("Mistral повернув некоректну відповідь") from error
        result = _content_to_text(data).strip()
        if not result:
            raise RuntimeError("Mistral Agent повернув порожню відповідь")
        return result[:4096]

    async def _run_digest(self, run_date=None, since=None, until=None, mark_run=True):
        if self._running:
            raise RuntimeError("дайджест уже створюється")
        missing = self._missing_config()
        if missing:
            raise ValueError(", ".join(missing))
        self._running = True
        try:
            items = await self._collect_news(since=since, until=until)
            if not items:
                if run_date and mark_run:
                    self.set("last_run", run_date)
                return 0
            digest = await self._ask_agent(self._build_prompt(items))
            await self._client.send_message(
                self._peer(self.config["target"]), digest, parse_mode="md"
            )
            if mark_run:
                self.set(
                    "last_run",
                    run_date or datetime.datetime.now(self._timezone()).date().isoformat(),
                )
            return len(items)
        finally:
            self._running = False

    async def newsruncmd(self, message):
        """Негайно зібрати та опублікувати дайджест"""
        missing = self._missing_config()
        if missing:
            return await utils.answer(
                message, self.strings("bad_config", message).format(html.escape(", ".join(missing)))
            )
        await utils.answer(message, self.strings("running", message))
        try:
            count = await self._run_digest()
            if not count:
                return await utils.answer(message, self.strings("no_news", message))
            await utils.answer(message, self.strings("done", message).format(count))
        except (aiohttp.ClientError, asyncio.TimeoutError, RPCError, RuntimeError, ValueError) as error:
            logger.exception("DailyNews manual run failed")
            await utils.answer(
                message, self.strings("failed", message).format(html.escape(str(error)[:500]))
            )

    async def newsstatuscmd(self, message):
        """Показати стан та розклад DailyNews"""
        await utils.answer(
            message,
            self.strings("status", message).format(
                sources=len(self._sources()),
                time=html.escape(str(self.config["publish_time"])),
                timezone=html.escape(str(self.config["timezone"])),
                target=html.escape(str(self.config["target"]) or "—"),
                last_run=html.escape(str(self.get("last_run", "—"))),
                running="так" if self._running else "ні",
            ),
        )

    async def newstodaycmd(self, message):
        """Примусово зібрати новини від початку сьогоднішнього дня до цієї миті"""
        missing = self._missing_config()
        if missing:
            return await utils.answer(
                message, self.strings("bad_config", message).format(html.escape(", ".join(missing)))
            )

        now_local = datetime.datetime.now(self._timezone())
        since = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        await utils.answer(message, self.strings("running", message))
        try:
            count = await self._run_digest(
                since=since.astimezone(datetime.timezone.utc),
                until=now_local.astimezone(datetime.timezone.utc),
                mark_run=False,
            )
            if not count:
                return await utils.answer(message, self.strings("no_news", message))
            await utils.answer(message, self.strings("done", message).format(count))
        except (aiohttp.ClientError, asyncio.TimeoutError, RPCError, RuntimeError, ValueError) as error:
            logger.exception("DailyNews today run failed")
            await utils.answer(
                message, self.strings("failed", message).format(html.escape(str(error)[:500]))
            )

    async def newsaddcmd(self, message):
        """Додати одразу список каналів (аргументи або текст повідомлення у відповіді)"""
        raw = utils.get_args_raw(message).strip()
        if not raw:
            reply = await message.get_reply_message()
            raw = (reply.raw_text or "").strip() if reply else ""
        incoming = self._parse_sources(raw)
        if not incoming:
            return await utils.answer(message, self.strings("sources_usage", message))

        current = self._sources()
        known = set(current)
        added = []
        for source in incoming:
            if source not in known:
                current.append(source)
                known.add(source)
                added.append(source)
        self.config["sources"] = ",".join(current)
        duplicate_note = ""
        duplicates = len(incoming) - len(added)
        if duplicates:
            duplicate_note = f"\n<b>Вже були у списку:</b> {duplicates}"
        await utils.answer(
            message,
            self.strings("sources_added", message).format(
                len(added), len(current), duplicate_note
            ),
        )

    async def newsresetcmd(self, message):
        """Скинути дату останнього автозапуску"""
        self.set("last_run", None)
        await utils.answer(message, "✅ <b>Дату останнього запуску скинуто.</b>")
