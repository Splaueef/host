# meta developer: @Huai_Baike
# meta version: 1.1.0
# meta description: 💱 Інтерактивний перегляд цін, фільтрів і оголошень Wallet P2P.
# scope: inline
# scope: hikka_only

"""Read-only Wallet P2P market browser for Hikka."""

import asyncio
import html
import logging
import re
import time
from decimal import Decimal, InvalidOperation

import aiohttp

from .. import loader, utils


logger = logging.getLogger(__name__)
API_URL = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"
WALLET_URL = "https://t.me/wallet"
CODE_RE = re.compile(r"^[A-Z0-9]{2,12}$")


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _fmt(value):
    number = _number(value)
    if number is None:
        return "—"
    displayed = format(number, "f")
    return displayed.rstrip("0").rstrip(".") if "." in displayed else displayed


def _safe(value, limit=60):
    text = str(value if value is not None else "")[:limit]
    return html.escape(text, quote=True)


class WalletAPIError(Exception):
    """A sanitized API error without credentials or response body."""


@loader.tds
class WalletP2PMod(loader.Module):
    """💱 Порівнюй пропозиції Wallet P2P та переходь до угоди в Wallet."""

    strings = {"name": "WalletP2P"}
    PAGE_SIZE = 10
    SHOW_LIMIT = 10
    CACHE_SECONDS = 30
    CRYPTO_CHOICES = ("USDT", "TON", "BTC")
    FIAT_CHOICES = ("EUR", "UAH", "USD")
    AMOUNT_CHOICES = (None, Decimal("50"), Decimal("100"), Decimal("500"), Decimal("1000"))

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "api_key", "", "API-ключ Wallet P2P (лише читання)",
                validator=loader.validators.Hidden(loader.validators.String()),
            ),
            loader.ConfigValue(
                "default_crypto", "USDT", "Криптовалюта за замовчуванням",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "default_fiat", "EUR", "Фіатна валюта за замовчуванням",
                validator=loader.validators.String(),
            ),
        )
        self._session = None
        self._cache = {}
        self._lock = asyncio.Lock()

    async def on_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def _code(value):
        code = str(value or "").strip().upper()
        if not CODE_RE.fullmatch(code):
            raise ValueError("Код валюти: 2–12 латинських літер або цифр.")
        return code

    def _params(self, args):
        tokens = args.split()
        if len(tokens) > 5:
            raise ValueError("Забагато аргументів. Напиши .p2p для прикладів.")
        saved = self._saved_state()
        action = tokens[0].lower() if tokens else ("buy" if saved[0] == "SELL" else "sell")
        if action not in ("buy", "sell", "купити", "продати"):
            raise ValueError("Напиши .p2p buy або .p2p sell.")
        side = "SELL" if action in ("buy", "купити") else "BUY"
        crypto = self._code(tokens[1] if len(tokens) > 1 else (saved[1] if not tokens else self.config["default_crypto"]))
        fiat = self._code(tokens[2] if len(tokens) > 2 else (saved[2] if not tokens else self.config["default_fiat"]))
        amount = _number(tokens[3].replace(",", ".")) if len(tokens) > 3 else (saved[3] if not tokens else None)
        if len(tokens) > 3 and (amount is None or amount <= 0):
            raise ValueError("Сума має бути додатним числом у фіатній валюті.")
        if amount is not None and amount > 1_000_000_000:
            raise ValueError("Сума завелика.")
        try:
            page = int(tokens[4]) if len(tokens) > 4 else 1
        except ValueError as exc:
            raise ValueError("Номер сторінки має бути цілим числом.") from exc
        if not 1 <= page <= 100:
            raise ValueError("Номер сторінки: від 1 до 100.")
        return (side, crypto, fiat, amount, page)

    def _saved_state(self):
        """Keep view choices across reloads; never save API credentials in this state."""
        value = self.get("last_market", {})
        if not isinstance(value, dict):
            value = {}
        try:
            side = value.get("side", "SELL")
            if side not in ("BUY", "SELL"):
                side = "SELL"
            crypto = self._code(value.get("crypto") or self.config["default_crypto"])
            fiat = self._code(value.get("fiat") or self.config["default_fiat"])
            amount = _number(value.get("amount"))
            if amount is not None and not (0 < amount <= 1_000_000_000):
                amount = None
            return (side, crypto, fiat, amount, 1, value.get("online") is True)
        except ValueError:
            return ("SELL", "USDT", "EUR", None, 1, False)

    def _remember(self, state):
        side, crypto, fiat, amount, _, online = state
        self.set("last_market", {"side": side, "crypto": crypto, "fiat": fiat,
                                 "amount": str(amount) if amount is not None else None,
                                 "online": online})

    async def _request(self, side, crypto, fiat, page, refresh=False):
        key = str(self.config["api_key"] or "").strip()
        if not key:
            raise WalletAPIError("Додай ключ через .config WalletP2P → api_key.")
        cache_key = (key, side, crypto, fiat, page)
        now = time.monotonic()
        if not refresh and cache_key in self._cache and now - self._cache[cache_key][0] < self.CACHE_SECONDS:
            return self._cache[cache_key][1]
        async with self._lock:
            now = time.monotonic()
            if not refresh and cache_key in self._cache and now - self._cache[cache_key][0] < self.CACHE_SECONDS:
                return self._cache[cache_key][1]
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
            try:
                async with self._session.post(
                    API_URL,
                    json={"cryptoCurrency": crypto, "fiatCurrency": fiat,
                          "side": side, "page": page, "pageSize": self.PAGE_SIZE},
                    headers={"X-API-Key": key, "Accept": "application/json"},
                ) as response:
                    if response.status != 200:
                        errors = {
                            400: "Некоректна пара валют або параметри запиту.",
                            401: "Невірний API-ключ Wallet P2P.",
                            403: "Немає доступу до Wallet P2P API.",
                            429: "Ліміт запитів Wallet. Спробуй пізніше.",
                            503: "Wallet P2P тимчасово недоступний.",
                        }
                        raise WalletAPIError(errors.get(response.status, "Wallet P2P не відповідає (HTTP {}).".format(response.status)))
                    payload = await response.json(content_type=None)
            except asyncio.CancelledError:
                raise
            except WalletAPIError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                logger.warning("Wallet P2P request failed: %s", type(exc).__name__)
                raise WalletAPIError("Не вдалося отримати дані Wallet. Спробуй пізніше.") from None
            if not isinstance(payload, dict) or payload.get("status") != "SUCCESS" or not isinstance(payload.get("data"), list):
                raise WalletAPIError("Wallet повернув неочікувану відповідь.")
            # No long-lived cache or logging of a user's API key or raw response.
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self.CACHE_SECONDS and k[0] == key}
            self._cache[cache_key] = (time.monotonic(), payload["data"])
            return payload["data"]

    @staticmethod
    def _eligible(items, amount, online_only=False):
        selected = []
        for item in items:
            if not isinstance(item, dict):
                continue
            price = _number(item.get("price"))
            if price is None or price <= 0:
                continue
            if online_only and item.get("isOnline") is not True:
                continue
            if amount is not None:
                low, high = _number(item.get("minAmount")), _number(item.get("maxAmount"))
                if low is None or high is None or not low <= amount <= high:
                    continue
            selected.append(item)
        return selected

    def _offers(self, items, state):
        side, _, _, amount, _, online = state
        return sorted(self._eligible(items, amount, online),
                      key=lambda item: _number(item["price"]), reverse=side == "BUY")

    @staticmethod
    def _state_label(state):
        side, crypto, fiat, amount, page, online = state
        direction = "купити" if side == "SELL" else "продати"
        return (f"{direction} {crypto} за {fiat} · "
                f"{'будь-яка сума' if amount is None else _fmt(amount) + ' ' + fiat} · "
                f"{'лише онлайн' if online else 'усі трейдери'} · стор. {page}")

    def _render(self, items, side, crypto, fiat, amount, page, online_only=False):
        state = (side, crypto, fiat, amount, page, online_only)
        offers = self._offers(items, state)
        lines = [
            "💱 <b>Wallet P2P</b>",
            "<b>{}</b>".format(_safe(self._state_label(state), 130)),
            "📄 Знайдено на сторінці: {} із {} оголошень.".format(len(offers), len(items)),
        ]
        if offers:
            lines.append("💰 Найкраща ціна <i>на цій сторінці:</i> <b>{} {}/{}</b>".format(
                _fmt(offers[0]["price"]), fiat, crypto))
        for index, item in enumerate(offers[:self.SHOW_LIMIT], 1):
            payments = item.get("payments")
            payment = ", ".join(_safe(p, 24) for p in payments[:2]) if isinstance(payments, list) else "—"
            lines.append(
                "<b>{}. {} {}/{}</b> · {} {}\n"
                "{}–{} {} · {}".format(
                    index, _fmt(item["price"]), fiat, crypto,
                    "🟢" if item.get("isOnline") is True else "⚪",
                    _safe(item.get("nickname") or "Трейдер", 30),
                    _fmt(item.get("minAmount")), _fmt(item.get("maxAmount")), fiat,
                    payment or "—",
                )
            )
        if not offers:
            lines.append("Оголошень за цими фільтрами тут немає. Зміни суму або перейди далі.")
        lines.append("<i>Сортування й фільтри застосовано до поточної сторінки API. "
                     "Оголошення можуть змінитися; перед угодою перевір ціну в Wallet.</i>")
        return "\n\n".join(lines)

    def _detail_text(self, item, state, index, total):
        side, crypto, fiat, amount, page, online = state
        payments = item.get("payments")
        payments = ", ".join(_safe(p, 40) for p in payments[:8]) if isinstance(payments, list) else "—"
        rate = _number(item.get("executeRate"))
        rating = _fmt(rate * 100) + "%" if rate is not None and 0 <= rate <= 1 else "—"
        orders = item.get("orderNum")
        try:
            orders = str(int(orders)) if orders is not None and int(orders) >= 0 else "—"
        except (TypeError, ValueError, OverflowError):
            orders = "—"
        try:
            period = int(item.get("paymentPeriod"))
            period = str(period) + " хв" if 0 < period <= 1440 else "—"
        except (TypeError, ValueError, OverflowError):
            period = "—"
        lines = [
            "💱 <b>Оголошення {}/{}</b> · сторінка {}".format(index + 1, total, page),
            "<b>{} {} за {}</b>".format("Купити" if side == "SELL" else "Продати", crypto, fiat),
            "💰 <b>{} {}/{}</b>".format(_fmt(item.get("price")), fiat, crypto),
            "👤 {} · {}".format(_safe(item.get("nickname") or "Трейдер", 50),
                               "🟢 онлайн" if item.get("isOnline") is True else "⚪ офлайн/невідомо"),
            "📊 Угод: {} · виконання: {}".format(orders, rating),
            "💳 Оплата: {}".format(payments or "—"),
            "⏱ Час на оплату: {}".format(period),
            "📏 Ліміти: {}–{} {}".format(_fmt(item.get("minAmount")), _fmt(item.get("maxAmount")), fiat),
            "📦 Доступно: {} {}".format(_fmt(item.get("lastQuantity")), crypto),
            "🆔 ID: <code>{}</code>".format(_safe(item.get("id"), 40)),
            "<i>Ціни та умови перевіряй у Wallet перед підтвердженням угоди.</i>",
        ]
        if amount is not None:
            lines.insert(8, "🎯 Твоя сума: {} {}".format(_fmt(amount), fiat))
        return "\n\n".join(lines)

    def _buttons(self, state, offers, has_next):
        side, crypto, fiat, amount, page, online = state
        markup = [
            [{"text": ("✅ " if side == "SELL" else "") + "Купити", "callback": self._select,
              "args": (state, "side", "SELL")},
             {"text": ("✅ " if side == "BUY" else "") + "Продати", "callback": self._select,
              "args": (state, "side", "BUY")}],
            [{"text": "🪙 " + crypto, "callback": self._menu, "args": (state, "crypto")},
             {"text": "💶 " + fiat, "callback": self._menu, "args": (state, "fiat")},
             {"text": "💰 " + ("Сума" if amount is None else _fmt(amount) + " " + fiat),
              "callback": self._menu, "args": (state, "amount")}],
            [{"text": "🟢 Лише онлайн " + ("✅" if online else "▫️"),
              "callback": self._select, "args": (state, "online", not online)}],
        ]
        shown = min(len(offers), self.SHOW_LIMIT)
        if shown:
            markup.append([{"text": "🔎 Оголошення {}".format(i + 1),
                            "callback": self._detail,
                            "args": (state, str(offers[i].get("id") or ""), i)}
                           for i in range(shown)])
            # Two buttons per row remain readable in Telegram on small screens.
            row = markup.pop()
            markup.extend(row[i:i + 2] for i in range(0, len(row), 2))
        nav = []
        if page > 1:
            nav.append({"text": "◀️", "callback": self._select, "args": (state, "page", page - 1)})
        nav.append({"text": "📄 " + str(page), "action": "answer", "message": "Сторінка {} API".format(page)})
        if has_next and page < 100:
            nav.append({"text": "▶️", "callback": self._select, "args": (state, "page", page + 1)})
        markup.extend([nav,
                       [{"text": "🔄 Оновити", "callback": self._change, "args": (state, True)},
                        {"text": "❔ Допомога", "callback": self._menu, "args": (state, "help")}],
                       [{"text": "💼 Відкрити Wallet", "url": WALLET_URL},
                        {"text": "✖️", "action": "close"}]])
        return markup

    def _menu_markup(self, state, kind):
        if kind == "crypto":
            options = self.CRYPTO_CHOICES
        elif kind == "fiat":
            options = self.FIAT_CHOICES
        elif kind == "amount":
            options = self.AMOUNT_CHOICES
        else:
            options = ()
        rows = []
        for i in range(0, len(options), 3):
            rows.append([{"text": "✅ " + ("Без суми" if x is None else str(x))
                           if x == state[{"crypto": 1, "fiat": 2, "amount": 3}[kind]]
                           else ("Без суми" if x is None else str(x)),
                          "callback": self._select, "args": (state, kind, x)}
                         for x in options[i:i + 3]])
        if kind in ("crypto", "fiat", "amount"):
            rows.append([{"text": "⌨️ Ввести своє значення", "input": "Код валюти" if kind != "amount" else "Сума у фіаті",
                          "handler": self._input, "args": (state, kind)}])
        rows.append([{"text": "◀️ До оголошень", "callback": self._change, "args": (state, False)}])
        return rows

    async def _menu(self, call, state, kind):
        titles = {"crypto": "Криптовалюта", "fiat": "Фіатна валюта",
                  "amount": "Сума угоди", "help": "Як користуватися"}
        if kind not in titles:
            return await call.answer("Невідомий розділ", show_alert=True)
        if kind == "help":
            info = ("Вибери напрямок, валюту й суму кнопками. Кожна сторінка "
                    "містить до 10 оголошень; порядок цін і фільтри застосовуються "
                    "лише до цієї сторінки. Натисни номер оголошення, щоб побачити "
                    "деталі. Твій останній вибір збережеться.\n\n"
                    "Ключ: <code>.config WalletP2P</code> → <code>api_key</code>. "
                    "Саму угоду оформлюй у Wallet: API доступний лише для перегляду.")
        else:
            info = ("Обери значення або введи власне. "
                    "Якщо Wallet не підтримує пару валют, API повідомить про це.")
        await call.edit("💱 <b>{}</b>\n\n{}\n\n<i>Поточний вибір: {}</i>".format(
            titles[kind], info, _safe(self._state_label(state), 130)),
            reply_markup=self._menu_markup(state, kind))

    async def _input(self, call, query, state, kind):
        try:
            raw = str(query or "").strip()
            if kind in ("crypto", "fiat"):
                value = self._code(raw)
            elif kind == "amount":
                value = _number(raw.replace(",", "."))
                if value is None or not 0 < value <= 1_000_000_000:
                    raise ValueError("Введи додатну суму до 1 млрд у фіатній валюті.")
            else:
                raise ValueError("Невідомий параметр")
        except ValueError as exc:
            return await call.answer(str(exc), show_alert=True)
        await self._select(call, state, kind, value)

    async def _select(self, call, state, field, value):
        indexes = {"side": 0, "crypto": 1, "fiat": 2, "amount": 3, "page": 4, "online": 5}
        if field not in indexes:
            return await call.answer("Невідомий параметр", show_alert=True)
        if state[indexes[field]] == value:
            return await call.answer("Уже вибрано")
        updated = list(state)
        updated[indexes[field]] = value
        if field != "page":
            updated[4] = 1
        await self._change(call, tuple(updated))

    async def _detail(self, call, state, offer_id, index):
        side, crypto, fiat, amount, page, online = state
        try:
            items = await self._request(side, crypto, fiat, page)
            offers = self._offers(items, state)[:self.SHOW_LIMIT]
            if offer_id:
                index = next((i for i, item in enumerate(offers)
                              if str(item.get("id")) == offer_id), -1)
            if not 0 <= index < len(offers):
                return await call.answer("Оголошення змінилося. Онови список.", show_alert=True)
            markup = []
            row = []
            if index > 0:
                row.append({"text": "◀️ Попереднє", "callback": self._detail,
                            "args": (state, str(offers[index - 1].get("id") or ""), index - 1)})
            if index + 1 < len(offers):
                row.append({"text": "Наступне ▶️", "callback": self._detail,
                            "args": (state, str(offers[index + 1].get("id") or ""), index + 1)})
            if row:
                markup.append(row)
            markup.extend([[{"text": "◀️ До списку", "callback": self._change, "args": (state, False)}],
                           [{"text": "💼 Відкрити Wallet", "url": WALLET_URL}]])
            await call.edit(self._detail_text(offers[index], state, index, len(offers)), reply_markup=markup)
        except WalletAPIError as exc:
            await call.answer(str(exc), show_alert=True)

    async def _change(self, call, state, refresh=False):
        side, crypto, fiat, amount, page, online = state
        try:
            items = await self._request(side, crypto, fiat, page, refresh=refresh)
            await call.edit(
                self._render(items, side, crypto, fiat, amount, page, online),
                reply_markup=self._buttons(state, self._offers(items, state), len(items) == self.PAGE_SIZE),
            )
            self._remember(state)
        except WalletAPIError as exc:
            await call.answer(str(exc), show_alert=True)

    async def p2pcmd(self, message):
        """[buy|sell] [USDT] [EUR] [сума EUR] [сторінка] — інтерактивний Wallet P2P"""
        args = utils.get_args_raw(message).strip()
        if args.lower() in ("help", "допомога"):
            return await utils.answer(message,
                "💱 <b>Wallet P2P</b>\n"
                "<code>.p2p</code> — кнопкове меню з останнім вибором\n"
                "<code>.p2p buy USDT EUR</code> — купити криптовалюту\n"
                "<code>.p2p sell USDT UAH 500</code> — продати на суму 500 UAH\n\n"
                "Ключ: <code>.config WalletP2P</code> → <code>api_key</code>. "
                "Угоду оформлюй у Wallet.")
        try:
            side, crypto, fiat, amount, page = self._params(args)
            online = self._saved_state()[5] if not args else False
            state = (side, crypto, fiat, amount, page, online)
            if not str(self.config["api_key"] or "").strip():
                setup = ("💱 <b>Wallet P2P · підключення</b>\n\n"
                         "Створи API-ключ: Wallet → P2P Market → My Profile → API Keys.\n"
                         "Введи його в приховане поле <code>api_key</code> через "
                         "<code>.config WalletP2P</code>, а потім знову напиши <code>.p2p</code>.\n\n"
                         "<i>Ключ не надсилай звичайним повідомленням.</i>")
                try:
                    opened = await self.inline.form(setup, message, force_me=True, reply_markup=[
                        [{"text": "💼 Wallet", "url": WALLET_URL},
                         {"text": "📖 Про API", "url": "https://help.wallet.tg/article/934-p2p-api"}],
                        [{"text": "✖️", "action": "close"}],
                    ])
                    if opened:
                        return
                except Exception:
                    logger.warning("Wallet P2P setup panel unavailable", exc_info=True)
                return await utils.answer(message, setup)
            items = await self._request(side, crypto, fiat, page)
        except (ValueError, WalletAPIError) as exc:
            return await utils.answer(message, "❌ " + _safe(exc, 180))
        text = self._render(items, side, crypto, fiat, amount, page, online)
        try:
            opened = await self.inline.form(
                text, message,
                reply_markup=self._buttons(state, self._offers(items, state), len(items) == self.PAGE_SIZE),
                force_me=True,
            )
            if opened:
                self._remember(state)
                return
        except Exception:
            logger.warning("Wallet P2P inline panel unavailable", exc_info=True)
        self._remember(state)
        await utils.answer(message, text + "\n\n<a href=\"{}\">Відкрити Wallet</a>".format(WALLET_URL))
