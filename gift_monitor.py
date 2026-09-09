# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: Моніторинг NFT подарунків у Telegram до 150 зірок

__version__ = (1, 0, 0)

from .. import loader, utils
from telethon.tl.functions.payments import GetStarGiftsRequest
import asyncio
import logging

logger = logging.getLogger(__name__)


@loader.tds
class GiftMonitorMod(loader.Module):
    """Моніторинг нових NFT подарунків у Telegram Marketplace (до 150 ⭐)"""

    strings = {
        "name": "GiftMonitor",
        "monitoring_on": (
            "✅ <b>Моніторинг запущено!</b>\n"
            "🔍 Шукаю подарунки від <b>{min}⭐</b> до <b>{max}⭐</b>\n"
            "⏱ Перевірка кожні <b>{interval} сек</b>"
        ),
        "monitoring_off": "🛑 <b>Моніторинг зупинено.</b>",
        "already_running": "⚠️ Моніторинг вже запущено. Спочатку зупини його: <code>.gstop</code>",
        "not_running": "⚠️ Моніторинг не запущено.",
        "new_gift": (
            "🎁 <b>Новий подарунок знайдено!</b>\n\n"
            "📦 <b>Назва:</b> {title}\n"
            "⭐ <b>Ціна:</b> {price} зірок\n"
            "🔢 <b>ID:</b> <code>{gift_id}</code>\n"
            "📊 <b>Кількість:</b> {total} шт.\n"
            "🔗 <b>Посилання:</b> {link}"
        ),
        "cfg_min_stars": "Мінімальна ціна подарунку (зірок)",
        "cfg_max_stars": "Максимальна ціна подарунку (зірок)",
        "cfg_interval": "Інтервал перевірки (секунд)",
        "cfg_notify_chat": "ID чату для сповіщень (0 = себе)",
        "status_on": (
            "📊 <b>Статус моніторингу:</b> 🟢 Активний\n"
            "⭐ Діапазон: {min} – {max} зірок\n"
            "⏱ Інтервал: {interval} сек\n"
            "📦 Знайдено подарунків: {found}"
        ),
        "status_off": "📊 <b>Статус моніторингу:</b> 🔴 Вимкнений",
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
                validator=loader.validators.Integer(minimum=1, maximum=10000),
            ),
            loader.ConfigValue(
                "interval",
                30,
                lambda: self.strings["cfg_interval"],
                validator=loader.validators.Integer(minimum=10),
            ),
            loader.ConfigValue(
                "notify_chat",
                0,
                lambda: self.strings["cfg_notify_chat"],
                validator=loader.validators.Integer(),
            ),
        )
        self._task = None
        self._seen_gifts = set()
        self._found_count = 0

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    # ── Команди ──────────────────────────────────────────────

    @loader.command(ru_doc="Запустити моніторинг подарунків")
    async def gstart(self, message):
        """Запустити моніторинг подарунків"""
        if self._task and not self._task.done():
            await utils.answer(message, self.strings["already_running"])
            return

        self._found_count = 0
        self._task = asyncio.ensure_future(self._monitor_loop())
        await utils.answer(
            message,
            self.strings["monitoring_on"].format(
                min=self.config["min_stars"],
                max=self.config["max_stars"],
                interval=self.config["interval"],
            ),
        )

    @loader.command(ru_doc="Зупинити моніторинг подарунків")
    async def gstop(self, message):
        """Зупинити моніторинг подарунків"""
        if not self._task or self._task.done():
            await utils.answer(message, self.strings["not_running"])
            return

        self._task.cancel()
        self._task = None
        await utils.answer(message, self.strings["monitoring_off"])

    @loader.command(ru_doc="Статус моніторингу")
    async def gstatus(self, message):
        """Перевірити статус моніторингу"""
        if self._task and not self._task.done():
            await utils.answer(
                message,
                self.strings["status_on"].format(
                    min=self.config["min_stars"],
                    max=self.config["max_stars"],
                    interval=self.config["interval"],
                    found=self._found_count,
                ),
            )
        else:
            await utils.answer(message, self.strings["status_off"])

    # ── Логіка моніторингу ────────────────────────────────────

    async def _monitor_loop(self):
        """Основний цикл перевірки подарунків"""
        while True:
            try:
                await self._check_gifts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[GiftMonitor] Помилка: {e}")
            await asyncio.sleep(self.config["interval"])

    async def _check_gifts(self):
        """Отримати список подарунків і перевірити нові"""
        try:
            result = await self._client(GetStarGiftsRequest(hash=0))
        except Exception as e:
            logger.warning(f"[GiftMonitor] Не вдалося отримати подарунки: {e}")
            return

        gifts = getattr(result, "gifts", [])

        for gift in gifts:
            gift_id = getattr(gift, "id", None)
            stars = getattr(gift, "stars", 0)

            if gift_id is None:
                continue

            # Фільтр по ціні
            if not (self.config["min_stars"] <= stars <= self.config["max_stars"]):
                continue

            # Пропустити вже відомі
            if gift_id in self._seen_gifts:
                continue

            self._seen_gifts.add(gift_id)
            self._found_count += 1
            await self._notify(gift, stars)

    async def _notify(self, gift, stars):
        """Надіслати сповіщення про новий подарунок"""
        gift_id = getattr(gift, "id", "?")
        total = getattr(gift, "availability_total", "?")
        title = getattr(gift, "title", None) or f"Подарунок #{gift_id}"

        # Посилання на маркетплейс (формат може змінитись з оновленнями TG)
        link = f"https://t.me/nft/{gift_id}"

        text = self.strings["new_gift"].format(
            title=title,
            price=stars,
            gift_id=gift_id,
            total=total,
            link=link,
        )

        notify_chat = self.config["notify_chat"]
        if notify_chat:
            try:
                await self._client.send_message(notify_chat, text, parse_mode="html")
            except Exception as e:
                logger.warning(f"[GiftMonitor] Не вдалося надіслати в чат {notify_chat}: {e}")
        else:
            # Надіслати собі (Saved Messages)
            await self._client.send_message("me", text, parse_mode="html")
