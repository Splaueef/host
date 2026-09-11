# meta developer: @Huang_Baike
# meta version: 1.1.0
# meta description: Постійно підтримує статус акаунта Telegram «онлайн».

import asyncio
import contextlib
import datetime
import logging
import time

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.types import UpdateUserStatus, UserStatusOffline

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class AlwaysOnlineMod(loader.Module):
    """Підтримує статус Telegram «онлайн», доки працює Hikka"""

    strings = {
        "name": "AlwaysOnline",
        "cfg_enabled": "Постійно підтримувати статус Telegram «онлайн»",
        "cfg_interval": (
            "Резервний інтервал між оновленнями статусу в секундах "
            "(від 10 до 300)"
        ),
        "cfg_reassert_delay": (
            "Затримка перед відновленням онлайн після отримання статусу «офлайн» "
            "(від 0 до 15 секунд)"
        ),
        "cfg_offline_on_unload": (
            "Надіслати статус «офлайн» під час вимкнення або видалення модуля"
        ),
        "enabled": (
            "🟢 <b>AlwaysOnline увімкнено.</b>\n"
            "Резервне оновлення кожні <code>{interval}</code> с.; відновлення "
            "після статусу «офлайн» — через <code>{delay}</code> с."
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
            "Резервний інтервал: <code>{interval}</code> с.\n"
            "Затримка відновлення: <code>{delay}</code> с.\n"
            "Автовідновлень: <code>{reassertions}</code>\n"
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
                30,
                lambda: self.strings("cfg_interval"),
                validator=loader.validators.Integer(minimum=10, maximum=300),
            ),
            loader.ConfigValue(
                "reassert_delay",
                2,
                lambda: self.strings("cfg_reassert_delay"),
                validator=loader.validators.Integer(minimum=0, maximum=15),
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
        self._me_id = None
        self._reassert_task = None
        self._reassertions = 0
        self._status_lock = asyncio.Lock()

    async def client_ready(self, client, db):
        self._client = client
        self._unloading = False
        self._me_id = getattr(client, "tg_id", None)
        if self._me_id is None:
            try:
                self._me_id = (await client.get_me()).id
            except (RPCError, ConnectionError, OSError):
                logger.warning("AlwaysOnline could not resolve the current user id")

        # Version 1.0 used 240 seconds by default. Shorten only that old default;
        # preserve every interval the owner selected manually.
        if not self.get("interval_v1_1_migrated", False):
            if int(self.config["interval"]) == 240:
                self.config["interval"] = 30
            self.set("interval_v1_1_migrated", True)

        self._was_enabled = bool(self.config["enabled"])
        if self._was_enabled:
            await self._set_status(offline=False)

    async def on_unload(self):
        self._unloading = True
        await self._cancel_reassert_task()
        if self._client and self.config["offline_on_unload"]:
            await self._set_status(offline=True)

    def _retry_delay(self):
        return max(60, int(self.config["interval"]))

    async def _set_status(self, offline):
        async with self._status_lock:
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
        if not offline:
            self._last_success = datetime.datetime.now(datetime.timezone.utc)
        self._next_refresh = time.monotonic() + int(self.config["interval"])
        return True

    async def _cancel_reassert_task(self):
        task = self._reassert_task
        self._reassert_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _restore_online(self):
        try:
            await asyncio.sleep(int(self.config["reassert_delay"]))
            if self._unloading or not self.config["enabled"]:
                return
            self._next_refresh = 0.0
            if await self._set_status(offline=False):
                self._reassertions += 1
        except asyncio.CancelledError:
            raise
        finally:
            if self._reassert_task is asyncio.current_task():
                self._reassert_task = None

    @loader.raw_handler(UpdateUserStatus)
    async def status_update_handler(self, update):
        """Immediately restore online when another Telegram session sets us offline."""
        if (
            self._unloading
            or not self._client
            or not self.config["enabled"]
            or update.user_id != self._me_id
            or not isinstance(update.status, UserStatusOffline)
        ):
            return

        if self._reassert_task and not self._reassert_task.done():
            return
        self._reassert_task = asyncio.create_task(self._restore_online())

    @loader.loop(interval=10, autostart=True)
    async def presence_loop(self):
        if self._unloading or not self._client:
            return

        enabled = bool(self.config["enabled"])
        if not enabled:
            if self._was_enabled:
                await self._cancel_reassert_task()
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
            text = self.strings("enabled", message).format(
                interval=self.config["interval"],
                delay=self.config["reassert_delay"],
            )
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
        await self._cancel_reassert_task()
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
                delay=self.config["reassert_delay"],
                reassertions=self._reassertions,
                last_success=last_success,
                last_error=utils.escape_html(self._last_error or "немає"),
            ),
        )
