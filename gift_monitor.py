# meta developer: @Huai_Baike
# meta version: 2.1.0
# meta description: Моніторинг Telegram Star Gifts зі статистикою

__version__ = (2, 1, 0)

import asyncio
import html
import logging
import time
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
            "⏱ Перевірка: кожні <b>{interval} с</b>{baseline}{warning}"
        ),
        "start_warning": (
            "\n\n⚠️ Перша перевірка не вдалася: <code>{}</code>. "
            "Монітор продовжить автоматичні спроби."
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
        "status_worker_off": "🟠 Увімкнений, але цикл не працює",
        "status": (
            "📊 <b>GiftMonitor</b>\n\n"
            "<b>Стан:</b> {state}\n"
            "<b>Ціна:</b> {min}–{max} ⭐\n"
            "<b>Інтервал:</b> {interval} с\n"
            "<b>Поточний каталог:</b> {catalog} "
            "(доступно {available}, розпродано {sold_out})\n"
            "<b>Відомо ID загалом:</b> {known}\n"
            "<b>Перевірки:</b> {checks} "
            "(успішно {successful}, без змін {unchanged}, "
            "помилок {failures})\n"
            "<b>Помилки сповіщень:</b> {notification_errors}\n"
            "<b>Сповіщень за запуск:</b> {found}\n"
            "<b>Остання перевірка:</b> {last_check}\n"
            "<b>Остання успішна:</b> {last_success}\n"
            "<b>Оновлення каталогу:</b> {last_refresh}\n"
            "<b>Остання зміна каталогу:</b> {last_change}\n"
            "<b>Результат:</b> {last_result}{error}"
        ),
        "status_error": "\n<b>Остання помилка:</b> <code>{}</code>",
        "never": "ще не було",
        "waiting": "очікування першої перевірки",
        "check_done": (
            "✅ Перевірку завершено. Каталог: <b>{catalog}</b>, "
            "нових сповіщень: <b>{sent}</b>.\n"
            "Результат: {result}"
        ),
        "check_failed": "❌ Перевірка не вдалася: <code>{}</code>",
        "resynced": (
            "✅ Каталог синхронізовано без сповіщень. "
            "Запам'ятано <b>{count}</b> типів подарунків."
        ),
        "resync_failed": "❌ Не вдалося синхронізувати каталог: <code>{}</code>",
        "cfg_min_stars": "Мінімальна ціна подарунка (у зірках)",
        "cfg_max_stars": "Максимальна ціна подарунка (у зірках)",
        "cfg_interval": "Інтервал перевірки (у секундах)",
        "cfg_full_refresh": (
            "Інтервал повного оновлення каталогу без кеш-хешу "
            "(у секундах)"
        ),
        "cfg_timeout": (
            "Максимальний час одного запиту до Telegram (у секундах)"
        ),
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
                "full_refresh_interval",
                60,
                lambda: self.strings["cfg_full_refresh"],
                validator=loader.validators.Integer(minimum=30, maximum=3_600),
            ),
            loader.ConfigValue(
                "request_timeout",
                30,
                lambda: self.strings["cfg_timeout"],
                validator=loader.validators.Integer(minimum=5, maximum=120),
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
        self._client = None
        self._check_lock = asyncio.Lock()
        self._known_gifts = set()
        self._catalog_initialized = False
        self._catalog_hash = 0
        self._catalog_signature = None
        self._catalog_count = 0
        self._available_count = 0
        self._sold_out_count = 0
        self._found_count = 0
        self._checks_count = 0
        self._successful_checks = 0
        self._unchanged_checks = 0
        self._failed_checks = 0
        self._notification_errors = 0
        self._last_check = None
        self._last_success = None
        self._last_catalog_refresh = None
        self._last_catalog_change = None
        self._last_result = self.strings["waiting"]
        self._last_error = None
        self._next_check_at = 0.0
        self._next_full_refresh_at = 0.0

    async def client_ready(self, client, db):
        self._client = client
        self._db = db
        self._known_gifts = {
            int(gift_id)
            for gift_id in (self.get("known_gift_ids", []) or [])
            if str(gift_id).lstrip("-").isdigit()
        }
        self._catalog_initialized = bool(
            self.get("catalog_initialized", False) and self._known_gifts
        )
        self._next_check_at = 0.0
        self._next_full_refresh_at = 0.0

    # ── Команди ──────────────────────────────────────────────

    @loader.command(ru_doc="Запустити моніторинг нових Telegram Gifts")
    async def gstart(self, message):
        """Запустити моніторинг нових Telegram Gifts"""
        if self.config["min_stars"] > self.config["max_stars"]:
            await utils.answer(message, self.strings["bad_range"])
            return

        if self.get("enabled", False):
            await utils.answer(message, self.strings["already_running"])
            return

        needs_baseline = not self._catalog_initialized
        self._found_count = 0
        self._last_error = None
        self.set("enabled", True)
        self._next_check_at = time.monotonic() + int(self.config["interval"])

        warning = ""
        try:
            await self._check_gifts(force=True)
        except Exception as exc:
            logger.exception("[GiftMonitor] Initial gift check failed")
            warning = self.strings["start_warning"].format(
                html.escape(self._error_text(exc), quote=False)
            )

        self._next_check_at = time.monotonic() + int(self.config["interval"])
        await utils.answer(
            message,
            self.strings["monitoring_on"].format(
                min=self._format_number(self.config["min_stars"]),
                max=self._format_number(self.config["max_stars"]),
                interval=self._format_number(self.config["interval"]),
                baseline=(
                    self.strings["baseline_note"] if needs_baseline else ""
                ),
                warning=warning,
            ),
        )

    @loader.command(ru_doc="Зупинити моніторинг подарунків")
    async def gstop(self, message):
        """Зупинити моніторинг подарунків"""
        if not self.get("enabled", False):
            await utils.answer(message, self.strings["not_running"])
            return

        self.set("enabled", False)
        await utils.answer(message, self.strings["monitoring_off"])

    @loader.command(ru_doc="Показати стан моніторингу подарунків")
    async def gstatus(self, message):
        """Показати стан моніторингу подарунків"""
        enabled = bool(self.get("enabled", False))
        worker_running = bool(getattr(self.gift_monitor_loop, "status", True))
        if enabled and not worker_running:
            state = self.strings["status_worker_off"]
        else:
            state = self.strings["status_on" if enabled else "status_off"]

        error = ""
        if self._last_error:
            error = self.strings["status_error"].format(
                html.escape(self._last_error, quote=False)
            )

        await utils.answer(
            message,
            self.strings["status"].format(
                state=state,
                min=self._format_number(self.config["min_stars"]),
                max=self._format_number(self.config["max_stars"]),
                interval=self._format_number(self.config["interval"]),
                catalog=self._format_number(self._catalog_count),
                available=self._format_number(self._available_count),
                sold_out=self._format_number(self._sold_out_count),
                known=self._format_number(len(self._known_gifts)),
                checks=self._format_number(self._checks_count),
                successful=self._format_number(self._successful_checks),
                unchanged=self._format_number(self._unchanged_checks),
                failures=self._format_number(self._failed_checks),
                notification_errors=self._format_number(self._notification_errors),
                found=self._format_number(self._found_count),
                last_check=self._last_check or self.strings["never"],
                last_success=self._last_success or self.strings["never"],
                last_refresh=self._last_catalog_refresh or self.strings["never"],
                last_change=self._last_catalog_change or self.strings["never"],
                last_result=html.escape(self._last_result, quote=False),
                error=error,
            ),
        )

    @loader.command(
        ru_doc="Примусово перевірити каталог подарунків зараз"
    )
    async def gcheck(self, message):
        """Перевірити каталог подарунків зараз"""
        self._next_check_at = time.monotonic() + int(self.config["interval"])
        try:
            sent = await self._check_gifts(force=True)
        except Exception as exc:
            logger.exception("[GiftMonitor] Manual gift check failed")
            await utils.answer(
                message,
                self.strings["check_failed"].format(
                    html.escape(self._error_text(exc), quote=False)
                ),
            )
            return

        await utils.answer(
            message,
            self.strings["check_done"].format(
                catalog=self._format_number(self._catalog_count),
                sent=self._format_number(sent),
                result=html.escape(self._last_result, quote=False),
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

    @loader.loop(interval=5, autostart=True)
    async def gift_monitor_loop(self):
        """Стійкий цикл Hikka для регулярних перевірок каталогу."""
        if not self._client or not self.get("enabled", False):
            return

        now = time.monotonic()
        if now < self._next_check_at:
            return

        self._next_check_at = now + int(self.config["interval"])
        try:
            await self._check_gifts()
        except asyncio.CancelledError:
            raise
        except Exception:
            # _check_gifts already records the diagnostic details. The Hikka
            # loop remains alive and retries on the next configured interval.
            logger.exception("[GiftMonitor] Scheduled gift check failed")

    async def _fetch_catalog(self, *, force=False):
        full_refresh = force or time.monotonic() >= self._next_full_refresh_at
        request_hash = 0 if full_refresh else self._catalog_hash
        result = await asyncio.wait_for(
            self._client(GetStarGiftsRequest(hash=request_hash)),
            timeout=int(self.config["request_timeout"]),
        )
        gifts = getattr(result, "gifts", None)
        if gifts is None:
            return None, full_refresh

        result_hash = getattr(result, "hash", None)
        if isinstance(result_hash, int):
            self._catalog_hash = result_hash
        self._next_full_refresh_at = time.monotonic() + int(
            self.config["full_refresh_interval"]
        )
        if full_refresh:
            self._last_catalog_refresh = self._now_text()
        return list(gifts), full_refresh

    async def _check_gifts(self, *, force=False, replace_baseline=False):
        async with self._check_lock:
            self._checks_count += 1
            self._last_check = self._now_text()
            try:
                gifts, full_refresh = await self._fetch_catalog(force=force)
            except Exception as exc:
                self._failed_checks += 1
                self._last_error = self._error_text(exc)
                self._last_result = "помилка запиту до Telegram"
                raise
            finally:
                self._last_check = self._now_text()

            if gifts is None and full_refresh:
                self._catalog_hash = 0
                self._next_full_refresh_at = 0.0
                self._failed_checks += 1
                error = RuntimeError(
                    "Telegram повернув NotModified на повний запит каталогу"
                )
                self._last_error = self._error_text(error)
                self._last_result = (
                    "некоректна відповідь на повне оновлення"
                )
                raise error

            self._last_error = None
            self._successful_checks += 1
            self._last_success = self._last_check
            if gifts is None:  # payments.starGiftsNotModified
                self._unchanged_checks += 1
                self._last_result = (
                    "каталог без змін (відповідь за кеш-хешем)"
                )
                return 0

            self._update_catalog_stats(gifts)
            current_ids = {
                int(gift.id)
                for gift in gifts
                if getattr(gift, "id", None) is not None
            }

            if replace_baseline or not self._catalog_initialized:
                self._replace_known(current_ids)
                self._catalog_initialized = True
                self.set("catalog_initialized", True)
                self._last_result = (
                    "каталог синхронізовано: "
                    f"{len(current_ids)} типів, без сповіщень"
                )
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

            sent = 0
            remembered = set()
            retry_needed = False
            for gift in new_gifts:
                stars = int(getattr(gift, "stars", 0) or 0)
                if not (
                    self.config["min_stars"]
                    <= stars
                    <= self.config["max_stars"]
                ):
                    # Out-of-range items are still known catalog entries. This
                    # prevents an old gift from being announced after a filter edit.
                    remembered.add(int(gift.id))
                    continue

                if await self._notify(gift, stars):
                    sent += 1
                    self._found_count += 1
                    remembered.add(int(gift.id))
                else:
                    # Do not lose the notification forever. Force the next
                    # request to return the full catalog and retry this gift.
                    retry_needed = True
                await asyncio.sleep(0.35)

            if remembered:
                self._known_gifts.update(remembered)
                self._persist_known()

            if retry_needed:
                self._catalog_hash = 0
                self._next_full_refresh_at = 0.0

            self._last_result = (
                f"отримано {len(gifts)} типів; нових {len(new_gifts)}, "
                f"сповіщень {sent}"
            )
            return sent

    async def _sync_catalog(self):
        await self._check_gifts(force=True, replace_baseline=True)
        return self._catalog_count

    def _update_catalog_stats(self, gifts):
        signature = tuple(
            sorted(
                (
                    int(gift.id),
                    int(getattr(gift, "stars", 0) or 0),
                    getattr(gift, "availability_remains", None),
                    bool(getattr(gift, "sold_out", False)),
                )
                for gift in gifts
                if getattr(gift, "id", None) is not None
            )
        )
        if signature != self._catalog_signature:
            self._last_catalog_change = self._now_text()
        self._catalog_signature = signature
        self._catalog_count = len(signature)
        self._sold_out_count = sum(item[3] for item in signature)
        self._available_count = max(0, self._catalog_count - self._sold_out_count)

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
            self._last_error = (
                f"Не вдалося надіслати сповіщення: {exc}"
            )
            self._notification_errors += 1
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

    @staticmethod
    def _error_text(exc):
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
