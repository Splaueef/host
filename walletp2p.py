# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 💱 Оголошення та ціни Wallet P2P; угоди відкриваються у Wallet.
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
    return format(number, "f").rstrip("0").rstrip(".") if number is not None and number != 0 else ("0" if number == 0 else "—")


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
    CACHE_SECONDS = 30

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
        action = tokens[0].lower() if tokens else "buy"
        if action not in ("buy", "sell", "купити", "продати"):
            raise ValueError("Напиши .p2p buy або .p2p sell.")
        side = "SELL" if action in ("buy", "купити") else "BUY"
        crypto = self._code(tokens[1] if len(tokens) > 1 else self.config["default_crypto"])
        fiat = self._code(tokens[2] if len(tokens) > 2 else self.config["default_fiat"])
        amount = _number(tokens[3]) if len(tokens) > 3 else None
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
    def _eligible(items, amount):
        if amount is None:
            return items
        selected = []
        for item in items:
            if not isinstance(item, dict):
                continue
            low, high = _number(item.get("minAmount")), _number(item.get("maxAmount"))
            if low is not None and high is not None and low <= amount <= high:
                selected.append(item)
        return selected

    def _render(self, items, side, crypto, fiat, amount, page):
        direction = "Купити" if side == "SELL" else "Продати"
        visible = self._eligible(items, amount)
        lines = [
            "💱 <b>Wallet P2P · {}</b> <b>{}</b> за <b>{}</b>".format(direction, crypto, fiat),
            "📄 Сторінка {} · {} оголошень{}".format(page, len(items),
                " · сума {} {}".format(_fmt(amount), fiat) if amount is not None else ""),
            "<i>Купити = оголошення SELL; продати = оголошення BUY.</i>\n",
        ]
        for item in visible:
            if not isinstance(item, dict):
                continue
            price = _fmt(item.get("price"))
            if price == "—":
                continue
            payments = item.get("payments")
            payments = ", ".join(_safe(p, 30) for p in payments[:3]) if isinstance(payments, list) else "—"
            rate = _number(item.get("executeRate"))
            rating = " · виконання {}%".format(_fmt(rate * 100)) if rate is not None and 0 <= rate <= 1 else ""
            lines.append(
                "<b>{} {}/{} </b>· {}{}\n"
                "Ліміти: {}–{} {} · {}\n"
                "Оплата: {} · ID: <code>{}</code>".format(
                    price, fiat, crypto, _safe(item.get("nickname") or "Трейдер", 35), rating,
                    _fmt(item.get("minAmount")), _fmt(item.get("maxAmount")), fiat,
                    "🟢 онлайн" if item.get("isOnline") is True else "⚪ статус невідомий",
                    payments or "—", _safe(item.get("id"), 32),
                )
            )
        if len(lines) == 3:
            lines.append("Для цієї суми на сторінці немає оголошень. Спробуй іншу суму або сторінку.")
        lines.append("\n<i>Ціни оновлюються у Wallet приблизно кожні 30 с. Угода оформлюється в самому Wallet.</i>")
        return "\n\n".join(lines)

    def _buttons(self, side, crypto, fiat, amount, page, has_next):
        other = "BUY" if side == "SELL" else "SELL"
        nav = []
        if page > 1:
            nav.append({"text": "◀️ Назад", "callback": self._change, "args": (side, crypto, fiat, amount, page - 1, False)})
        if has_next:
            nav.append({"text": "Далі ▶️", "callback": self._change, "args": (side, crypto, fiat, amount, page + 1, False)})
        return [
            [{"text": "🔄 Оновити", "callback": self._change, "args": (side, crypto, fiat, amount, page, True)},
             {"text": "🔁 Купити / продати", "callback": self._change, "args": (other, crypto, fiat, amount, 1, False)}],
            *([nav] if nav else []),
            [{"text": "💼 Відкрити Wallet для угоди", "url": WALLET_URL}],
        ]

    async def _change(self, call, side, crypto, fiat, amount, page, refresh):
        try:
            items = await self._request(side, crypto, fiat, page, refresh=refresh)
            await call.edit(
                self._render(items, side, crypto, fiat, amount, page),
                reply_markup=self._buttons(side, crypto, fiat, amount, page, len(items) == self.PAGE_SIZE),
            )
        except WalletAPIError as exc:
            await call.answer(str(exc), show_alert=True)

    async def p2pcmd(self, message):
        """[buy|sell] [USDT] [EUR] [сума EUR] [сторінка] — оголошення Wallet P2P"""
        args = utils.get_args_raw(message).strip()
        if not args:
            return await utils.answer(message,
                "💱 <b>Wallet P2P</b>\n"
                "<code>.p2p buy USDT EUR</code> — купити криптовалюту\n"
                "<code>.p2p sell USDT EUR 100</code> — продати на суму 100 EUR\n"
                "<code>.p2p buy USDT UAH 500 2</code> — друга сторінка\n\n"
                "Ключ: <code>.config WalletP2P</code> → <code>api_key</code>. "
                "Wallet → P2P Market → My Profile → API Keys.\n"
                "Угоди створюються й підтверджуються у Wallet, API показує лише оголошення.")
        try:
            side, crypto, fiat, amount, page = self._params(args)
            items = await self._request(side, crypto, fiat, page)
        except (ValueError, WalletAPIError) as exc:
            return await utils.answer(message, "❌ " + _safe(exc, 180))
        text = self._render(items, side, crypto, fiat, amount, page)
        try:
            opened = await self.inline.form(
                text, message,
                reply_markup=self._buttons(side, crypto, fiat, amount, page, len(items) == self.PAGE_SIZE),
                force_me=True,
            )
            if opened:
                return
        except Exception:
            logger.warning("Wallet P2P inline panel unavailable", exc_info=True)
        await utils.answer(message, text + "\n\n<a href=\"{}\">Відкрити Wallet</a>".format(WALLET_URL))
