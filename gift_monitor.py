# meta developer: @Huai_Baike
# meta version: 3.0.0
# meta description: Моніторинг NFT-подарунків на офіційному ринку Telegram

__version__ = (3, 0, 0)

import asyncio
import html
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

from telethon.tl.functions.payments import GetStarGiftsRequest

try:
    from telethon.tl.functions.payments import GetResaleStarGiftsRequest
except ImportError:
    GetResaleStarGiftsRequest = None

from .. import loader, utils

logger = logging.getLogger(__name__)

NANO = 1_000_000_000
KNOWN_LISTINGS_LIMIT = 20_000


@loader.tds
class GiftMonitorMod(loader.Module):
    """Моніторинг collectible NFT gifts на офіційному ринку Telegram."""

    strings = {
        "name": "GiftMonitor",
        "monitoring_on": (
            "✅ <b>NFT-моніторинг запущено</b>\n"
            "⭐ Діапазон: <b>{min}–{max} Stars</b>\n"
            "⏱ Перевірка: кожні <b>{interval} с</b>{baseline}{warning}"
        ),
        "start_warning": (
            "\n\n⚠️ Перша перевірка не вдалася: <code>{}</code>. "
            "Монітор продовжить автоматичні спроби."
        ),
        "baseline_note": (
            "\n\nℹ️ Перший запуск тихо запам'ятовує активні NFT-лістинги. "
            "Далі бот повідомлятиме про нові лістинги та зміни ціни."
        ),
        "monitoring_off": "🛑 <b>NFT-моніторинг зупинено.</b>",
        "already_running": "⚠️ Моніторинг уже працює.",
        "not_running": "⚠️ Моніторинг не запущено.",
        "bad_range": (
            "❌ Мінімальна ціна не може перевищувати максимальну. "
            "Виправ налаштування модуля."
        ),
        "telethon_too_old": (
            "Ця версія Telethon не підтримує "
            "<code>payments.getResaleStarGifts</code>. Онови Hikka/Telethon."
        ),
        "status_on": "🟢 Активний",
        "status_off": "🔴 Вимкнений",
        "status_worker_off": "🟠 Увімкнений, але цикл не працює",
        "status": (
            "📊 <b>GiftMonitor · NFT market</b>\n\n"
            "<b>Стан:</b> {state}\n"
            "<b>Фільтр:</b> {min}–{max} ⭐\n"
            "<b>Інтервал:</b> {interval} с\n"
            "<b>Колекції:</b> {resale}/{catalog} з активним resale "
            "(під фільтром {eligible})\n"
            "<b>Останній скан:</b> {scanned} колекцій, "
            "{loaded} NFT, у діапазоні {in_range}\n"
            "<b>Активних лістингів:</b> ≈{active}\n"
            "<b>Ініціалізовано колекцій:</b> {initialized}\n"
            "<b>Відомо NFT/цін:</b> {known}\n"
            "<b>Перевірки:</b> {checks} "
            "(успішно {successful}, невдало {failures})\n"
            "<b>Помилки запитів:</b> {request_errors}\n"
            "<b>Помилки сповіщень:</b> {notification_errors}\n"
            "<b>Сповіщень за запуск:</b> {found}\n"
            "<b>Остання перевірка:</b> {last_check}\n"
            "<b>Остання успішна:</b> {last_success}\n"
            "<b>Оновлення колекцій:</b> {last_refresh}\n"
            "<b>Результат:</b> {last_result}{error}"
        ),
        "status_error": "\n<b>Остання помилка:</b> <code>{}</code>",
        "never": "ще не було",
        "waiting": "очікування першої перевірки",
        "check_done": (
            "✅ Повний NFT-скан завершено. Колекцій: <b>{collections}</b>, "
            "NFT завантажено: <b>{loaded}</b>, сповіщень: <b>{sent}</b>.\n"
            "Результат: {result}"
        ),
        "check_failed": "❌ Перевірка не вдалася: <code>{}</code>",
        "resynced": (
            "✅ NFT-маркет синхронізовано без сповіщень. "
            "Запам'ятано <b>{listings}</b> лістингів у "
            "<b>{collections}</b> колекціях."
        ),
        "resync_failed": "❌ Не вдалося синхронізувати NFT-маркет: <code>{}</code>",
        "market_loading": "🔎 Перевіряю офіційний NFT-маркет Telegram…",
        "market_usage": (
            "ℹ️ Використання: <code>.gmarket</code> — NFT у заданому "
            "діапазоні Stars; <code>.gmarket all</code> — усі ціни та валюти."
        ),
        "market_empty": (
            "🛍 <b>Офіційний NFT-маркет Telegram</b>\n\n"
            "За поточною вибіркою активних NFT не знайдено.\n"
            "Перевірено колекцій: <b>{collections}</b>."
        ),
        "market_header": (
            "🛍 <b>Офіційний NFT-маркет Telegram</b>\n"
            "<b>Режим:</b> {mode}\n"
            "<b>Перевірено колекцій:</b> {collections}\n"
            "<b>Активних лістингів за даними Telegram:</b> ≈{active}\n"
            "<b>Показано:</b> {shown} із {matched} знайдених у вибірці\n\n"
        ),
        "market_mode_range": "ціна {min}–{max} ⭐",
        "market_mode_all": "усі ціни та валюти",
        "market_errors": (
            "\n\n⚠️ Не вдалося перевірити {errors} колекцій; "
            "показано успішно отримані результати."
        ),
        "cfg_min_stars": "Мінімальна ціна NFT-лістингу (у Stars)",
        "cfg_max_stars": "Максимальна ціна NFT-лістингу (у Stars)",
        "cfg_interval": "Інтервал перевірки (у секундах)",
        "cfg_full_refresh": "Інтервал оновлення списку колекцій (у секундах)",
        "cfg_timeout": "Максимальний час одного запиту до Telegram (у секундах)",
        "cfg_listings": "Скільки найновіших NFT сканувати в кожній колекції",
        "cfg_collections": "Скільки колекцій сканувати за один фоновий цикл",
        "cfg_market_limit": "Максимум NFT у відповіді команди .gmarket",
        "cfg_delay": "Пауза між запитами до колекцій (у мілісекундах)",
        "cfg_notify_chat": "ID чату для сповіщень (0 = Збережені повідомлення)",
        "cfg_send_preview": "Надсилати прев'ю NFT-подарунка",
        "new_listing": (
            "{event}\n\n"
            "🎁 <b>{title}</b>\n"
            "💰 <b>Ціна:</b> {price}\n"
            "🎨 <b>Модель:</b> {model}\n"
            "🌀 <b>Візерунок:</b> {pattern}\n"
            "🌈 <b>Фон:</b> {backdrop}\n"
            "🔢 <b>NFT ID:</b> <code>{gift_id}</code>{link}"
        ),
        "event_new": "🆕 <b>Новий NFT виставлено на продаж!</b>",
        "event_price": "🔔 <b>Ціна NFT увійшла у твій діапазон!</b>",
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
                300,
                lambda: self.strings["cfg_full_refresh"],
                validator=loader.validators.Integer(minimum=60, maximum=86_400),
            ),
            loader.ConfigValue(
                "request_timeout",
                20,
                lambda: self.strings["cfg_timeout"],
                validator=loader.validators.Integer(minimum=5, maximum=120),
            ),
            loader.ConfigValue(
                "listings_per_collection",
                20,
                lambda: self.strings["cfg_listings"],
                validator=loader.validators.Integer(minimum=1, maximum=100),
            ),
            loader.ConfigValue(
                "collections_per_check",
                20,
                lambda: self.strings["cfg_collections"],
                validator=loader.validators.Integer(minimum=1, maximum=200),
            ),
            loader.ConfigValue(
                "market_display_limit",
                50,
                lambda: self.strings["cfg_market_limit"],
                validator=loader.validators.Integer(minimum=1, maximum=100),
            ),
            loader.ConfigValue(
                "request_delay_ms",
                120,
                lambda: self.strings["cfg_delay"],
                validator=loader.validators.Integer(minimum=0, maximum=5_000),
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
        self._known_listings = {}
        self._initialized_collections = set()
        self._collections = []
        self._collection_cursor = 0
        self._catalog_count = 0
        self._resale_collections_count = 0
        self._eligible_collections_count = 0
        self._last_scanned_collections = 0
        self._last_loaded_listings = 0
        self._last_in_range = 0
        self._last_total_active = 0
        self._found_count = 0
        self._checks_count = 0
        self._successful_checks = 0
        self._failed_checks = 0
        self._request_errors = 0
        self._notification_errors = 0
        self._last_check = None
        self._last_success = None
        self._last_catalog_refresh = None
        self._last_result = self.strings["waiting"]
        self._last_error = None
        self._next_check_at = 0.0
        self._next_catalog_refresh_at = 0.0

    async def client_ready(self, client, db):
        self._client = client
        self._db = db
        raw_known = self.get("known_nft_listings", {}) or {}
        self._known_listings = (
            {str(key): str(value) for key, value in raw_known.items()}
            if isinstance(raw_known, dict)
            else {}
        )
        self._initialized_collections = {
            int(gift_id)
            for gift_id in (self.get("initialized_nft_collections", []) or [])
            if str(gift_id).lstrip("-").isdigit()
        }
        self._next_check_at = 0.0
        self._next_catalog_refresh_at = 0.0

    # ── Команди ──────────────────────────────────────────────

    @loader.command(ru_doc="Запустити моніторинг NFT-подарунків на resale-маркеті")
    async def gstart(self, message):
        """Запустити моніторинг NFT-подарунків на офіційному ринку"""
        if self.config["min_stars"] > self.config["max_stars"]:
            await utils.answer(message, self.strings["bad_range"])
            return

        if self.get("enabled", False):
            await utils.answer(message, self.strings["already_running"])
            return

        needs_baseline = not self._initialized_collections
        self._found_count = 0
        self._last_error = None
        self.set("enabled", True)
        self._next_check_at = time.monotonic() + int(self.config["interval"])

        warning = ""
        try:
            await self._check_market(force_catalog=True, scan_all=True)
        except Exception as exc:
            logger.exception("[GiftMonitor] Initial NFT market check failed")
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
                baseline=self.strings["baseline_note"] if needs_baseline else "",
                warning=warning,
            ),
        )

    @loader.command(ru_doc="Зупинити моніторинг NFT-подарунків")
    async def gstop(self, message):
        """Зупинити моніторинг NFT-подарунків"""
        if not self.get("enabled", False):
            await utils.answer(message, self.strings["not_running"])
            return

        self.set("enabled", False)
        await utils.answer(message, self.strings["monitoring_off"])

    @loader.command(ru_doc="Показати стан NFT-моніторингу")
    async def gstatus(self, message):
        """Показати стан NFT-моніторингу"""
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
                resale=self._format_number(self._resale_collections_count),
                catalog=self._format_number(self._catalog_count),
                eligible=self._format_number(self._eligible_collections_count),
                scanned=self._format_number(self._last_scanned_collections),
                loaded=self._format_number(self._last_loaded_listings),
                in_range=self._format_number(self._last_in_range),
                active=self._format_number(self._last_total_active),
                initialized=self._format_number(len(self._initialized_collections)),
                known=self._format_number(len(self._known_listings)),
                checks=self._format_number(self._checks_count),
                successful=self._format_number(self._successful_checks),
                failures=self._format_number(self._failed_checks),
                request_errors=self._format_number(self._request_errors),
                notification_errors=self._format_number(self._notification_errors),
                found=self._format_number(self._found_count),
                last_check=self._last_check or self.strings["never"],
                last_success=self._last_success or self.strings["never"],
                last_refresh=self._last_catalog_refresh or self.strings["never"],
                last_result=html.escape(self._last_result, quote=False),
                error=error,
            ),
        )

    @loader.command(ru_doc="Примусово просканувати весь NFT-маркет зараз")
    async def gcheck(self, message):
        """Перевірити всі NFT-колекції зараз"""
        self._next_check_at = time.monotonic() + int(self.config["interval"])
        try:
            sent = await self._check_market(force_catalog=True, scan_all=True)
        except Exception as exc:
            logger.exception("[GiftMonitor] Manual NFT market check failed")
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
                collections=self._format_number(self._last_scanned_collections),
                loaded=self._format_number(self._last_loaded_listings),
                sent=self._format_number(sent),
                result=html.escape(self._last_result, quote=False),
            ),
        )

    @loader.command(ru_doc="Тихо перебудувати базу NFT-лістингів")
    async def gresync(self, message):
        """Тихо запам'ятати активні NFT-лістинги без сповіщень"""
        try:
            await self._check_market(
                force_catalog=True,
                scan_all=True,
                replace_baseline=True,
            )
        except Exception as exc:
            logger.exception("[GiftMonitor] NFT resync failed")
            await utils.answer(
                message,
                self.strings["resync_failed"].format(
                    html.escape(self._error_text(exc), quote=False)
                ),
            )
            return

        await utils.answer(
            message,
            self.strings["resynced"].format(
                listings=self._format_number(len(self._known_listings)),
                collections=self._format_number(
                    len(self._initialized_collections)
                ),
            ),
        )

    @loader.command(ru_doc="Показати NFT на офіційному ринку: .gmarket [all]")
    async def gmarket(self, message):
        """Показати NFT у діапазоні Stars або весь доступний resale"""
        args = (utils.get_args_raw(message) or "").strip().lower()
        all_aliases = {"all", "full", "всі", "усі"}
        if args and args not in all_aliases:
            await utils.answer(message, self.strings["market_usage"])
            return

        await utils.answer(message, self.strings["market_loading"])
        try:
            async with self._check_lock:
                result = await self._collect_market(show_all=bool(args))
        except Exception as exc:
            logger.exception("[GiftMonitor] Market listing command failed")
            await utils.answer(
                message,
                self.strings["check_failed"].format(
                    html.escape(self._error_text(exc), quote=False)
                ),
            )
            return

        await utils.answer(message, self._format_market(result, bool(args)))

    # ── Моніторинг ───────────────────────────────────────────

    @loader.loop(interval=5, autostart=True)
    async def gift_monitor_loop(self):
        """Стійкий цикл Hikka для регулярного NFT-моніторингу."""
        if not self._client or not self.get("enabled", False):
            return

        now = time.monotonic()
        if now < self._next_check_at:
            return

        self._next_check_at = now + int(self.config["interval"])
        try:
            await self._check_market()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[GiftMonitor] Scheduled NFT market check failed")

    def _require_resale_api(self):
        if GetResaleStarGiftsRequest is None:
            raise RuntimeError(self.strings["telethon_too_old"])

    async def _fetch_collections(self, *, force=False):
        if (
            self._collections
            and not force
            and time.monotonic() < self._next_catalog_refresh_at
        ):
            return list(self._collections)

        result = await asyncio.wait_for(
            self._client(GetStarGiftsRequest(hash=0)),
            timeout=int(self.config["request_timeout"]),
        )
        gifts = getattr(result, "gifts", None)
        if gifts is None:
            raise RuntimeError("Telegram не повернув повний каталог подарунків")

        self._collections = [
            gift
            for gift in gifts
            if getattr(gift, "id", None) is not None
        ]
        self._catalog_count = len(self._collections)
        resale = self._resale_collections(self._collections)
        self._resale_collections_count = len(resale)
        self._eligible_collections_count = len(
            self._eligible_collections(resale)
        )
        self._last_catalog_refresh = self._now_text()
        self._next_catalog_refresh_at = time.monotonic() + int(
            self.config["full_refresh_interval"]
        )
        if self._collection_cursor >= max(1, len(resale)):
            self._collection_cursor = 0
        return list(self._collections)

    def _resale_collections(self, gifts):
        return [
            gift
            for gift in gifts
            if int(getattr(gift, "availability_resale", 0) or 0) > 0
        ]

    def _eligible_collections(self, gifts):
        maximum = int(self.config["max_stars"])
        return [
            gift
            for gift in gifts
            if getattr(gift, "resell_min_stars", None) is None
            or int(getattr(gift, "resell_min_stars", 0) or 0) <= maximum
        ]

    def _select_collection_batch(self, collections, *, scan_all=False):
        if scan_all or not collections:
            return list(collections)

        limit = min(
            len(collections),
            int(self.config["collections_per_check"]),
        )
        start = self._collection_cursor % len(collections)
        selected = [
            collections[(start + index) % len(collections)]
            for index in range(limit)
        ]
        self._collection_cursor = (start + limit) % len(collections)
        return selected

    async def _get_resale(self, collection, *, sort_by_price=False, limit=None):
        self._require_resale_api()
        request = GetResaleStarGiftsRequest(
            gift_id=int(collection.id),
            offset="",
            limit=int(limit or self.config["listings_per_collection"]),
            sort_by_price=True if sort_by_price else None,
        )
        return await asyncio.wait_for(
            self._client(request),
            timeout=int(self.config["request_timeout"]),
        )

    async def _check_market(
        self,
        *,
        force_catalog=False,
        scan_all=False,
        replace_baseline=False,
    ):
        async with self._check_lock:
            self._checks_count += 1
            self._last_check = self._now_text()
            if replace_baseline:
                self._known_listings = {}
                self._initialized_collections = set()
                self._persist_state()

            try:
                collections = await self._fetch_collections(force=force_catalog)
                resale = self._resale_collections(collections)
                selected = self._select_collection_batch(
                    resale,
                    scan_all=scan_all,
                )
                sent, stats, errors = await self._scan_collections(
                    selected,
                    force_baseline=replace_baseline,
                )
                if selected and not stats["scanned"]:
                    raise RuntimeError(errors[0])
            except Exception as exc:
                self._failed_checks += 1
                self._last_error = self._error_text(exc)
                self._last_result = "помилка сканування NFT-маркету"
                raise
            finally:
                self._last_check = self._now_text()

            self._successful_checks += 1
            self._last_success = self._last_check
            self._last_scanned_collections = stats["scanned"]
            self._last_loaded_listings = stats["loaded"]
            self._last_in_range = stats["in_range"]
            self._last_total_active = stats["active"]
            self._last_error = (
                f"{len(errors)} колекцій не перевірено: {errors[0]}"
                if errors
                else None
            )
            self._last_result = (
                f"перевірено {stats['scanned']} колекцій; "
                f"NFT {stats['loaded']}; у діапазоні {stats['in_range']}; "
                f"сповіщень {sent}"
            )
            if errors:
                self._last_result += f"; помилок запитів {len(errors)}"
            return sent

    async def _scan_collections(self, collections, *, force_baseline=False):
        sent = 0
        errors = []
        stats = {"scanned": 0, "loaded": 0, "in_range": 0, "active": 0}
        for index, collection in enumerate(collections):
            try:
                result = await self._get_resale(collection)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._request_errors += 1
                errors.append(
                    f"{getattr(collection, 'id', '?')}: {self._error_text(exc)}"
                )
                continue

            gifts = list(getattr(result, "gifts", []) or [])
            stats["scanned"] += 1
            stats["loaded"] += len(gifts)
            stats["active"] += int(getattr(result, "count", len(gifts)) or 0)
            collection_id = int(collection.id)
            is_baseline = (
                force_baseline
                or collection_id not in self._initialized_collections
            )

            for gift in gifts:
                signature = self._listing_signature(gift)
                key = self._listing_key(gift)
                if not signature or not key:
                    continue

                in_range = self._in_star_range(gift)
                if in_range:
                    stats["in_range"] += 1

                old_signature = self._known_listings.get(key)
                if is_baseline:
                    self._remember_listing(key, signature)
                    continue
                if old_signature == signature:
                    continue

                if not in_range:
                    self._remember_listing(key, signature)
                    continue

                if await self._notify(
                    gift,
                    price_changed=old_signature is not None,
                ):
                    sent += 1
                    self._found_count += 1
                    self._remember_listing(key, signature)

            if is_baseline:
                self._initialized_collections.add(collection_id)
            self._persist_state()

            if index + 1 < len(collections):
                await self._request_pause()

        return sent, stats, errors

    async def _collect_market(self, *, show_all):
        collections = await self._fetch_collections(force=True)
        resale = self._resale_collections(collections)
        selected = resale if show_all else self._eligible_collections(resale)
        listings = []
        errors = []
        active = 0
        request_limit = min(
            100,
            max(
                int(self.config["listings_per_collection"]),
                int(self.config["market_display_limit"]),
            ),
        )

        for index, collection in enumerate(selected):
            try:
                result = await self._get_resale(
                    collection,
                    sort_by_price=True,
                    limit=request_limit,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._request_errors += 1
                errors.append(self._error_text(exc))
                continue

            active += int(getattr(result, "count", 0) or 0)
            for gift in list(getattr(result, "gifts", []) or []):
                if show_all or self._in_star_range(gift):
                    listings.append(gift)

            if index + 1 < len(selected):
                await self._request_pause()

        if selected and len(errors) == len(selected):
            raise RuntimeError(errors[0])

        unique = {}
        for gift in listings:
            key = self._listing_key(gift)
            if key:
                unique[key] = gift
        ordered = sorted(unique.values(), key=self._listing_sort_key)
        return {
            "gifts": ordered,
            "collections": len(selected) - len(errors),
            "active": active,
            "errors": len(errors),
        }

    async def _request_pause(self):
        delay = int(self.config["request_delay_ms"])
        if delay:
            await asyncio.sleep(delay / 1_000)

    # ── Ціни та стан ─────────────────────────────────────────

    def _in_star_range(self, gift):
        stars, _ = self._extract_prices(gift)
        if stars is None:
            return False
        return (
            int(self.config["min_stars"]) * NANO
            <= stars
            <= int(self.config["max_stars"]) * NANO
        )

    @staticmethod
    def _extract_prices(gift):
        star_values = []
        ton_values = []
        for amount in getattr(gift, "resell_amount", []) or []:
            value = int(getattr(amount, "amount", 0) or 0)
            class_name = type(amount).__name__.lower()
            if "ton" in class_name:
                ton_values.append(value)
            else:
                nanos = int(getattr(amount, "nanos", 0) or 0)
                star_values.append(value * NANO + nanos)

        legacy_stars = getattr(gift, "resell_stars", None)
        if legacy_stars is not None:
            star_values.append(int(legacy_stars) * NANO)
        return (
            min(star_values) if star_values else None,
            min(ton_values) if ton_values else None,
        )

    def _listing_signature(self, gift):
        stars, ton = self._extract_prices(gift)
        if stars is None and ton is None:
            return None
        return f"s:{stars if stars is not None else '-'}|t:{ton if ton is not None else '-'}"

    @staticmethod
    def _listing_key(gift):
        slug = getattr(gift, "slug", None)
        if slug:
            return str(slug)
        gift_id = getattr(gift, "id", None)
        return f"id:{gift_id}" if gift_id is not None else None

    def _remember_listing(self, key, signature):
        self._known_listings.pop(key, None)
        self._known_listings[key] = signature
        if len(self._known_listings) > KNOWN_LISTINGS_LIMIT:
            excess = len(self._known_listings) - KNOWN_LISTINGS_LIMIT
            for old_key in list(self._known_listings)[:excess]:
                self._known_listings.pop(old_key, None)

    def _persist_state(self):
        self.set("known_nft_listings", dict(self._known_listings))
        self.set(
            "initialized_nft_collections",
            sorted(self._initialized_collections),
        )

    # ── Сповіщення та форматування ───────────────────────────

    async def _notify(self, gift, *, price_changed):
        target = self.config["notify_chat"] or "me"
        text = self._format_listing(gift, price_changed=price_changed)
        preview = self._preview_document(gift)

        if self.config["send_preview"] and preview is not None:
            try:
                await self._client.send_file(
                    target,
                    preview,
                    caption=text,
                    parse_mode="html",
                    force_document=False,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "[GiftMonitor] NFT preview %s failed: %s",
                    self._listing_key(gift),
                    exc,
                )

        try:
            await self._client.send_message(target, text, parse_mode="html")
            return True
        except Exception as exc:
            self._last_error = f"Не вдалося надіслати сповіщення: {exc}"
            self._notification_errors += 1
            logger.exception(
                "[GiftMonitor] NFT notification %s failed",
                self._listing_key(gift),
            )
            return False

    def _format_listing(self, gift, *, price_changed):
        names = self._attribute_names(gift)
        slug = getattr(gift, "slug", None)
        link = (
            '\n🔗 <a href="https://t.me/nft/{}">Відкрити на ринку</a>'.format(
                quote(str(slug), safe="")
            )
            if slug
            else ""
        )
        return self.strings["new_listing"].format(
            event=self.strings["event_price" if price_changed else "event_new"],
            title=html.escape(self._listing_title(gift), quote=False),
            price=self._format_price(gift),
            model=html.escape(names["model"], quote=False),
            pattern=html.escape(names["pattern"], quote=False),
            backdrop=html.escape(names["backdrop"], quote=False),
            gift_id=html.escape(str(getattr(gift, "id", "—")), quote=False),
            link=link,
        )

    def _format_market(self, result, show_all):
        gifts = result["gifts"]
        if not gifts:
            text = self.strings["market_empty"].format(
                collections=self._format_number(result["collections"])
            )
            if result["errors"]:
                text += self.strings["market_errors"].format(
                    errors=self._format_number(result["errors"])
                )
            return text

        limit = int(self.config["market_display_limit"])
        visible = gifts[:limit]
        mode = (
            self.strings["market_mode_all"]
            if show_all
            else self.strings["market_mode_range"].format(
                min=self._format_number(self.config["min_stars"]),
                max=self._format_number(self.config["max_stars"]),
            )
        )
        text = self.strings["market_header"].format(
            mode=mode,
            collections=self._format_number(result["collections"]),
            active=self._format_number(result["active"]),
            shown=self._format_number(len(visible)),
            matched=self._format_number(len(gifts)),
        )
        text += "\n\n".join(
            self._format_market_item(gift, index)
            for index, gift in enumerate(visible, start=1)
        )
        if result["errors"]:
            text += self.strings["market_errors"].format(
                errors=self._format_number(result["errors"])
            )
        return text

    def _format_market_item(self, gift, index):
        slug = getattr(gift, "slug", None)
        title = html.escape(self._listing_title(gift), quote=False)
        if slug:
            title = '<a href="https://t.me/nft/{}">{}</a>'.format(
                quote(str(slug), safe=""),
                title,
            )
        names = self._attribute_names(gift)
        return (
            f"<b>{index}. {title}</b>\n"
            f"💰 {self._format_price(gift)} · "
            f"🎨 {html.escape(names['model'], quote=False)} · "
            f"🌀 {html.escape(names['pattern'], quote=False)}"
        )

    def _format_price(self, gift):
        stars, ton = self._extract_prices(gift)
        prices = []
        if stars is not None:
            prices.append(f"{self._format_units(stars)} ⭐")
        if ton is not None:
            prices.append(f"{self._format_units(ton)} TON")
        return " / ".join(prices) if prices else "—"

    @staticmethod
    def _listing_sort_key(gift):
        stars, ton = GiftMonitorMod._extract_prices(gift)
        if stars is not None:
            return 0, stars, str(getattr(gift, "slug", ""))
        if ton is not None:
            return 1, ton, str(getattr(gift, "slug", ""))
        return 2, 0, str(getattr(gift, "slug", ""))

    @staticmethod
    def _attribute_names(gift):
        result = {"model": "—", "pattern": "—", "backdrop": "—"}
        for attribute in getattr(gift, "attributes", []) or []:
            class_name = type(attribute).__name__.lower()
            name = str(getattr(attribute, "name", "—") or "—")
            if "model" in class_name:
                result["model"] = name
            elif "pattern" in class_name:
                result["pattern"] = name
            elif "backdrop" in class_name:
                result["backdrop"] = name
        return result

    @staticmethod
    def _preview_document(gift):
        fallback = None
        for attribute in getattr(gift, "attributes", []) or []:
            document = getattr(attribute, "document", None)
            if document is None:
                continue
            if "model" in type(attribute).__name__.lower():
                return document
            fallback = fallback or document
        return fallback

    @staticmethod
    def _listing_title(gift):
        title = str(getattr(gift, "title", None) or "NFT Gift")
        number = getattr(gift, "num", None)
        return f"{title} #{number}" if number is not None else title

    @staticmethod
    def _format_units(value):
        value = int(value)
        whole, fraction = divmod(value, NANO)
        if not fraction:
            return GiftMonitorMod._format_number(whole)
        fraction_text = f"{fraction:09d}".rstrip("0")
        return f"{GiftMonitorMod._format_number(whole)}.{fraction_text}"

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
