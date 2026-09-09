# meta developer: @Huai_Baike
# meta version: 2.0.0
# meta description: Надійний моніторинг нових Telegram Star Gifts із прев'ю

__version__ = (2, 0, 0)

import asyncio
import contextlib
import html
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from telethon.tl.functions.payments import GetStarGiftsRequest

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class GiftMonitorMod(loader.Module):
    """Моніторинг появи нових типів Telegram Star Gifts."""

    strings = {
        "name": "GiftMonitor",
        "monitoring_on": (
            "✅ <b>Моніторинг запущено</b>\n"
            "⭐ Діапазон: <b>{min}–{max} зірок</b>\n"
            "⏱ Перевірка: кожні <b>{interval} с</b>{baseline}"
        ),
        "baseline_note": (
            "\n\nℹ️ Перший запит лише запам'ятає поточний каталог — "
            "старі подарунки не будуть надіслані як нові."
        ),
        "monitoring_off": "🛑 <b>Моніторинг зупинено.</b>",
        "already_running": "⚠️ Моніторинг уже працює.",
        "not_running": "⚠️ Моніторинг не запущено.",
        "bad_range": (
            "❌ Мінімальна ціна не може перевищувати максимальну. "
            "Виправ налаштування модуля."
        ),
        "status_on": "🟢 Активний",
        "status_off": "🔴 Вимкнений",
        "status": (
            "📊 <b>GiftMonitor</b>\n\n"
            "<b>Стан:</b> {state}\n"
            "<b>Ціна:</b> {min}–{max} ⭐\n"
            "<b>Інтервал:</b> {interval} с\n"
            "<b>Відомо типів:</b> {known}\n"
            "<b>Сповіщень за запуск:</b> {found}\n"
            "<b>Остання перевірка:</b> {last_check}{error}"
        ),
        "status_error": "\n<b>Остання помилка:</b> <code>{}</code>",
        "never": "ще не було",
        "resynced": (
            "✅ Каталог синхронізовано без сповіщень. "
            "Запам'ятано <b>{count}</b> типів подарунків."
        ),
        "resync_failed": "❌ Не вдалося синхронізувати каталог: <code>{}</code>",
        "cfg_min_stars": "Мінімальна ціна подарунка (у зірках)",
        "cfg_max_stars": "Максимальна ціна подарунка (у зірках)",
        "cfg_interval": "Інтервал перевірки (у секундах)",
        "cfg_notify_chat": "ID чату для сповіщень (0 = Збережені повідомлення)",
        "cfg_send_preview": "Надсилати стікер-прев'ю подарунка",
        "new_gift": (
            "🎁 <b>З'явився новий подарунок Telegram!</b>\n\n"
            "📦 <b>Назва:</b> {title}\n"
            "⭐ <b>Ціна:</b> {price}\n"
            "🔢 <b>ID типу:</b> <code>{gift_id}</code>\n"
            "📊 <b>Тираж:</b> {supply}\n"
            "🛍 <b>Залишилось:</b> {remains}\n"
            "📌 <b>Стан:</b> {state}{upgrade}{resale}{link}"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "min_stars",
                1,
                lambda: self.strings["cfg_min_stars"],
                validator=loader.validators.Integer(minimum=1),
            ),
            loader.ConfigValue(
                "max_stars",
                150,
                lambda: self.strings["cfg_max_stars"],
                validator=loader.validators.Integer(minimum=1, maximum=1_000_000),
            ),
            loader.ConfigValue(
                "interval",
                30,
                lambda: self.strings["cfg_interval"],
                validator=loader.validators.Integer(minimum=10, maximum=86_400),
            ),
            loader.ConfigValue(
                "notify_chat",
                0,
                lambda: self.strings["cfg_notify_chat"],
                validator=loader.validators.Integer(),
            ),
            loader.ConfigValue(
                "send_preview",
                True,
                lambda: self.strings["cfg_send_preview"],
                validator=loader.validators.Boolean(),
            ),
        )
        self._task = None
        self._client = None
        self._check_lock = asyncio.Lock()
        self._known_gifts = set()
        self._catalog_initialized = False
        self._catalog_hash = 0
        self._found_count = 0
        self._last_check = None
        self._last_error = None

    async def client_ready(self, client, db):
        self._client = client
        self._db = db
        self._known_gifts = {
            int(gift_id)
            for gift_id in (self.get("known_gift_ids", []) or [])
            if str(gift_id).lstrip("-").isdigit()
        }
        self._catalog_initialized = bool(self.get("catalog_initialized", False))

        if self.get("enabled", False):
            self._start_task()

    async def on_unload(self):
        await self._stop_task()

    # ── Команди ──────────────────────────────────────────────

    @loader.command(ru_doc="Запустити моніторинг нових Telegram Gifts")
    async def gstart(self, message):
        """Запустити моніторинг нових Telegram Gifts"""
        if self.config["min_stars"] > self.config["max_stars"]:
            await utils.answer(message, self.strings["bad_range"])
            return

        if self._task and not self._task.done():
            await utils.answer(message, self.strings["already_running"])
            return

        self._found_count = 0
        self._last_error = None
        self.set("enabled", True)
        self._start_task()
        await utils.answer(
            message,
            self.strings["monitoring_on"].format(
                min=self._format_number(self.config["min_stars"]),
                max=self._format_number(self.config["max_stars"]),
                interval=self._format_number(self.config["interval"]),
                baseline=(
                    ""
                    if self._catalog_initialized
                    else self.strings["baseline_note"]
                ),
            ),
        )

    @loader.command(ru_doc="Зупинити моніторинг подарунків")
    async def gstop(self, message):
        """Зупинити моніторинг подарунків"""
        if not self._task or self._task.done():
            self.set("enabled", False)
            await utils.answer(message, self.strings["not_running"])
            return

        self.set("enabled", False)
        await self._stop_task()
        await utils.answer(message, self.strings["monitoring_off"])

    @loader.command(ru_doc="Показати стан моніторингу подарунків")
    async def gstatus(self, message):
        """Показати стан моніторингу подарунків"""
        running = bool(self._task and not self._task.done())
        error = ""
        if self._last_error:
            error = self.strings["status_error"].format(
                html.escape(self._last_error, quote=False)
            )

        await utils.answer(
            message,
            self.strings["status"].format(
                state=self.strings["status_on" if running else "status_off"],
                min=self._format_number(self.config["min_stars"]),
                max=self._format_number(self.config["max_stars"]),
                interval=self._format_number(self.config["interval"]),
                known=self._format_number(len(self._known_gifts)),
                found=self._format_number(self._found_count),
                last_check=self._last_check or self.strings["never"],
                error=error,
            ),
        )

    @loader.command(ru_doc="Тихо перебудувати базу відомих подарунків")
    async def gresync(self, message):
        """Тихо запам'ятати поточний каталог, не надсилаючи старі подарунки"""
        try:
            count = await self._sync_catalog()
        except Exception as exc:
            logger.exception("[GiftMonitor] Resync failed")
            await utils.answer(
                message,
                self.strings["resync_failed"].format(
                    html.escape(str(exc), quote=False)
                ),
            )
            return

        await utils.answer(
            message,
            self.strings["resynced"].format(
                count=self._format_number(count)
            ),
        )

    # ── Моніторинг ───────────────────────────────────────────

    def _start_task(self):
        if not self._task or self._task.done():
            self._task = asyncio.ensure_future(self._monitor_loop())

    async def _stop_task(self):
        task, self._task = self._task, None
        if not task or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _monitor_loop(self):
        while True:
            try:
                await self._check_gifts()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("[GiftMonitor] Gift check failed")

            await asyncio.sleep(self.config["interval"])

    async def _fetch_catalog(self, *, force=False):
        request_hash = 0 if force else self._catalog_hash
        result = await self._client(GetStarGiftsRequest(hash=request_hash))
        gifts = getattr(result, "gifts", None)
        if gifts is None:
            return None

        result_hash = getattr(result, "hash", None)
        if isinstance(result_hash, int):
            self._catalog_hash = result_hash
        return list(gifts)

    async def _check_gifts(self):
        async with self._check_lock:
            try:
                gifts = await self._fetch_catalog()
            except Exception as exc:
                self._last_error = str(exc)
                raise
            finally:
                self._last_check = self._now_text()

            self._last_error = None
            if gifts is None:  # payments.starGiftsNotModified
                return 0

            current_ids = {
                int(gift.id)
                for gift in gifts
                if getattr(gift, "id", None) is not None
            }

            if not self._catalog_initialized:
                self._replace_known(current_ids)
                self._catalog_initialized = True
                self.set("catalog_initialized", True)
                logger.info(
                    "[GiftMonitor] Initial catalog saved: %s gifts",
                    len(current_ids),
                )
                return 0

            new_gifts = [
                gift
                for gift in gifts
                if getattr(gift, "id", None) is not None
                and int(gift.id) not in self._known_gifts
            ]

            # Remember every catalog item, not only items inside the price filter.
            # This prevents old gifts from becoming "new" after config changes.
            if current_ids - self._known_gifts:
                self._known_gifts.update(current_ids)
                self._persist_known()

            sent = 0
            for gift in new_gifts:
                stars = int(getattr(gift, "stars", 0) or 0)
                if not (
                    self.config["min_stars"]
                    <= stars
                    <= self.config["max_stars"]
                ):
                    continue

                if await self._notify(gift, stars):
                    sent += 1
                    self._found_count += 1
                await asyncio.sleep(0.35)

            return sent

    async def _sync_catalog(self):
        async with self._check_lock:
            gifts = await self._fetch_catalog(force=True)
            gifts = gifts or []
            current_ids = {
                int(gift.id)
                for gift in gifts
                if getattr(gift, "id", None) is not None
            }
            self._replace_known(current_ids)
            self._catalog_initialized = True
            self.set("catalog_initialized", True)
            self._last_check = self._now_text()
            self._last_error = None
            return len(current_ids)

    def _replace_known(self, gift_ids):
        self._known_gifts = set(gift_ids)
        self._persist_known()

    def _persist_known(self):
        self.set("known_gift_ids", sorted(self._known_gifts))

    # ── Сповіщення ───────────────────────────────────────────

    async def _notify(self, gift, stars):
        target = self.config["notify_chat"] or "me"
        text = self._format_gift(gift, stars)
        sticker = getattr(gift, "sticker", None)

        if self.config["send_preview"] and sticker is not None:
            try:
                await self._client.send_file(
                    target,
                    sticker,
                    caption=text,
                    parse_mode="html",
                    force_document=False,
                )
                return True
            except Exception as exc:
                # A stale file_reference or an older Telethon layer must not
                # prevent the actual notification from being delivered.
                logger.warning(
                    "[GiftMonitor] Preview for gift %s failed: %s",
                    getattr(gift, "id", "?"),
                    exc,
                )

        try:
            await self._client.send_message(target, text, parse_mode="html")
            return True
        except Exception as exc:
            self._last_error = f"Не вдалося надіслати сповіщення: {exc}"
            logger.exception(
                "[GiftMonitor] Notification for gift %s failed",
                getattr(gift, "id", "?"),
            )
            return False

    def _format_gift(self, gift, stars):
        gift_id = int(getattr(gift, "id", 0) or 0)
        title = html.escape(self._gift_title(gift), quote=False)
        limited = bool(getattr(gift, "limited", False))
        sold_out = bool(getattr(gift, "sold_out", False))
        auction = bool(getattr(gift, "auction", False))

        total = getattr(gift, "availability_total", None)
        remains = getattr(gift, "availability_remains", None)
        supply = self._format_number(total) if total is not None else "необмежений"
        remains_text = (
            self._format_number(remains)
            if limited and remains is not None
            else ("0" if sold_out else "необмежено")
        )

        if auction:
            state = "аукціон"
        elif sold_out:
            state = "розпродано"
        elif getattr(gift, "locked_until_date", None):
            state = "ще недоступний"
        else:
            state = "доступний"

        if getattr(gift, "require_premium", False):
            state += ", лише Premium"

        upgrade_stars = getattr(gift, "upgrade_stars", None)
        upgrade = (
            "\n⬆️ <b>Апгрейд:</b> {} ⭐".format(
                self._format_number(upgrade_stars)
            )
            if upgrade_stars is not None
            else ""
        )

        resale_count = getattr(gift, "availability_resale", None)
        resale = (
            "\n♻️ <b>На перепродажі:</b> {}".format(
                self._format_number(resale_count)
            )
            if resale_count is not None
            else ""
        )

        auction_slug = getattr(gift, "auction_slug", None)
        link = (
            '\n🔗 <a href="https://t.me/auction/{}">Відкрити аукціон</a>'.format(
                quote(str(auction_slug), safe="")
            )
            if auction_slug
            else ""
        )

        return self.strings["new_gift"].format(
            title=title,
            price=self._format_number(stars),
            gift_id=gift_id,
            supply=supply,
            remains=remains_text,
            state=state,
            upgrade=upgrade,
            resale=resale,
            link=link,
        )

    @staticmethod
    def _gift_title(gift):
        title = getattr(gift, "title", None)
        if title:
            return str(title)

        sticker = getattr(gift, "sticker", None)
        for attribute in getattr(sticker, "attributes", []) or []:
            emoji = getattr(attribute, "alt", None)
            if emoji:
                return f"Подарунок {emoji}"
        return "Подарунок без назви"

    @staticmethod
    def _format_number(value):
        if value is None:
            return "—"
        try:
            return f"{int(value):,}".replace(",", " ")
        except (TypeError, ValueError):
            return html.escape(str(value), quote=False)

    @staticmethod
    def _now_text():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
