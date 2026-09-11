# meta developer: @Huang_Baike
# meta version: 1.0.0
# meta description: Постійно підтримує статус акаунта Telegram «онлайн».

import datetime
import logging
import time

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.account import UpdateStatusRequest

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class AlwaysOnlineMod(loader.Module):
    """Підтримує статус Telegram «онлайн», доки працює Hikka"""

    strings = {
        "name": "AlwaysOnline",
        "cfg_enabled": "Постійно підтримувати статус Telegram «онлайн»",
        "cfg_interval": (
            "Інтервал між оновленнями статусу в секундах (від 60 до 300)"
        ),
        "cfg_offline_on_unload": (
            "Надіслати статус «офлайн» під час вимкнення або видалення модуля"
        ),
        "enabled": (
            "🟢 <b>AlwaysOnline увімкнено.</b>\n"
            "Статус оновлюватиметься кожні <code>{}</code> с."
        ),
        "enabled_with_error": (
            "⚠️ <b>AlwaysOnline увімкнено, але перший запит не виконано.</b>\n"
            "Модуль спробує знову автоматично.\n"
            "<code>{}</code>"
        ),
        "disabled": "⚫ <b>AlwaysOnline вимкнено.</b> Статус «офлайн» надіслано.",
        "disabled_with_error": (
            "⚠️ <b>AlwaysOnline вимкнено, але статус «офлайн» не надіслано.</b>\n"
            "<code>{}</code>"
        ),
        "status": (
            "👤 <b>AlwaysOnline</b>\n\n"
            "Стан: {state}\n"
            "Інтервал: <code>{interval}</code> с.\n"
            "Останнє успішне оновлення: <code>{last_success}</code>\n"
            "Остання помилка: <code>{last_error}</code>"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "enabled",
                True,
                lambda: self.strings("cfg_enabled"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "interval",
                240,
                lambda: self.strings("cfg_interval"),
                validator=loader.validators.Integer(minimum=60, maximum=300),
            ),
            loader.ConfigValue(
                "offline_on_unload",
                True,
                lambda: self.strings("cfg_offline_on_unload"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._next_refresh = 0.0
        self._last_success = None
        self._last_error = None
        self._was_enabled = False
        self._unloading = False

    async def client_ready(self, client, db):
        self._client = client
        self._unloading = False
        self._was_enabled = bool(self.config["enabled"])
        if self._was_enabled:
            await self._set_status(offline=False)

    async def on_unload(self):
        self._unloading = True
        if self._client and self.config["offline_on_unload"]:
            await self._set_status(offline=True)

    def _retry_delay(self):
        return max(60, int(self.config["interval"]))

    async def _set_status(self, offline):
        try:
            await self._client(UpdateStatusRequest(offline=offline))
        except FloodWaitError as error:
            seconds = max(1, int(getattr(error, "seconds", 0)))
            self._last_error = f"FloodWaitError: зачекайте {seconds} с."
            self._next_refresh = time.monotonic() + seconds + 1
            logger.warning("AlwaysOnline flood wait: %s seconds", seconds)
            return False
        except (RPCError, ConnectionError, OSError) as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._next_refresh = time.monotonic() + self._retry_delay()
            logger.warning("AlwaysOnline status update failed: %s", error)
            return False
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
            self._next_refresh = time.monotonic() + self._retry_delay()
            logger.exception("Unexpected AlwaysOnline status update failure")
            return False

        self._last_error = None
        self._last_success = datetime.datetime.now(datetime.timezone.utc)
        self._next_refresh = time.monotonic() + int(self.config["interval"])
        return True

    @loader.loop(interval=10, autostart=True)
    async def presence_loop(self):
        if self._unloading or not self._client:
            return

        enabled = bool(self.config["enabled"])
        if not enabled:
            if self._was_enabled:
                await self._set_status(offline=True)
            self._was_enabled = False
            self._next_refresh = 0.0
            return

        self._was_enabled = True
        if time.monotonic() >= self._next_refresh:
            await self._set_status(offline=False)

    async def onlineoncmd(self, message):
        """Увімкнути постійний статус онлайн"""
        self.config["enabled"] = True
        self._was_enabled = True
        self._next_refresh = 0.0
        success = await self._set_status(offline=False)
        if success:
            text = self.strings("enabled", message).format(self.config["interval"])
        else:
            text = self.strings("enabled_with_error", message).format(
                utils.escape_html(self._last_error or "невідома помилка")
            )
        await utils.answer(message, text)

    async def onlineoffcmd(self, message):
        """Вимкнути постійний статус онлайн"""
        self.config["enabled"] = False
        self._was_enabled = False
        self._next_refresh = 0.0
        success = await self._set_status(offline=True)
        if success:
            text = self.strings("disabled", message)
        else:
            text = self.strings("disabled_with_error", message).format(
                utils.escape_html(self._last_error or "невідома помилка")
            )
        await utils.answer(message, text)

    async def onlinestatuscmd(self, message):
        """Показати стан модуля AlwaysOnline"""
        if self._last_success:
            last_success = self._last_success.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            last_success = "ще не було"

        await utils.answer(
            message,
            self.strings("status", message).format(
                state=(
                    "🟢 <b>увімкнено</b>"
                    if self.config["enabled"]
                    else "⚫ <b>вимкнено</b>"
                ),
                interval=self.config["interval"],
                last_success=last_success,
                last_error=utils.escape_html(self._last_error or "немає"),
            ),
        )
