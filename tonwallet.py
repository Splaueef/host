# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 💎 TON (Gram) гаманець: гаманець Telegram (WalletTg) або W5 — GRAM, джетони, NFT.
# requires: tonutils==2.5.1
# scope: inline
# scope: hikka_only

from __future__ import annotations

import time
import typing as t
from decimal import Decimal, InvalidOperation

import aiohttp
from ton_core import Address, NetworkGlobalID, ton_mnemonic_is_valid
from tonutils.clients import TonapiClient
from tonutils.contracts import (
    JettonTransferBuilder,
    NFTTransferBuilder,
    TONTransferBuilder,
    WalletTg,
    WalletV5R1,
)

from .. import loader, utils
from ..inline.types import InlineCall

TONAPI = "https://tonapi.io/v2"
GRAM_DECIMALS = 9
# Газ, що прикладається до переказу джетона/NFT (надлишок повертається)
TOKEN_GAS = 50_000_000  # 0.05 GRAM
FEE_RESERVE = 10_000_000  # 0.01 GRAM на комісію простого переказу


class TonError(Exception):
    pass


def fmt_amount(value: t.Union[int, str], decimals: int) -> str:
    text = f"{Decimal(int(value)).scaleb(-decimals):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def parse_amount(text: str, decimals: int) -> int:
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation:
        raise TonError(f"❌ Некоректна сума: <code>{utils.escape_html(text)}</code>")
    units = value.scaleb(decimals)
    if value <= 0 or units != units.to_integral_value():
        raise TonError(f"❌ Некоректна сума: <code>{utils.escape_html(text)}</code>")
    return int(units)


def friendly(address: t.Union[str, Address]) -> str:
    return Address(address).to_str(is_user_friendly=True, is_bounceable=False)


@loader.tds
class TonWalletMod(loader.Module):
    """Гаманець TON (Gram): гаманець Telegram (WalletTg) або W5, GRAM, джетони, NFT. Лише mainnet."""

    strings = {
        "name": "TonWallet",
        "no_wallet": (
            "❌ Гаманець не імпортовано. Надішліть <code>.tonimport &lt;seed-фраза&gt;</code>"
            " (краще в «Збережених»)."
        ),
        "no_mnemonic": "❌ Немає seed-фрази: гаманець відкрито тільки для перегляду.",
        "imported": (
            "✅ Гаманець імпортовано, повідомлення з фразою видалено.\n"
            "<b>Тип:</b> {}\n<b>Адреса:</b> <code>{}</code>"
        ),
        "bad_phrase": (
            "❌ Фраза не підходить ні для гаманця Telegram (12/24 слова BIP-39),"
            " ні для W5 (24 слова TON)."
        ),
        "forgot": "🗑 Seed-фразу видалено з конфігу.",
        "address_mismatch": (
            "❌ Фраза дає адресу <code>{}</code>, а в конфігу вказано <code>{}</code>.\n"
            "Для гаманця Telegram після зміни ключа потрібна повна фраза з 24 слів."
        ),
        "error": "❌ {}",
        "args": "❌ Використання: <code>{}</code>",
        "too_much": "❌ Сума {} GRAM більша за ліміт max_amount ({} GRAM).",
        "low_balance": "❌ Недостатньо GRAM: потрібно ≈{}, на балансі {}.",
        "confirm": "⚠️ <b>Підтвердіть переказ</b>\n\n{}\n\nЗапит дійсний 2 хвилини.",
        "sent": (
            "✅ Надіслано в мережу.\n<b>Хеш повідомлення:</b> <code>{}</code>\n"
            '<a href="https://tonviewer.com/{}">Переглянути гаманець</a>'
        ),
        "expired": "⌛ Час на підтвердження вийшов, переказ не надіслано.",
        "cancelled": "Скасовано.",
        "jetton_not_found": "❌ Джетон <code>{}</code> не знайдено на гаманці.",
        "nft_not_found": "❌ NFT <code>{}</code> не знайдено на гаманці.",
        "loading": "⏳ Завантаження…",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "mnemonic",
                "",
                "Seed-фраза. Гаманець Telegram: 12 або 24 слова BIP-39; W5: 24 слова TON."
                " Зберігається в базі Hikka відкритим текстом!",
                validator=loader.validators.Hidden(),
            ),
            loader.ConfigValue(
                "wallet_type",
                "tg",
                "tg: гаманець Telegram (WalletTg); w5: Wallet V5R1",
                validator=loader.validators.Choice(["tg", "w5"]),
            ),
            loader.ConfigValue(
                "address",
                "",
                "Адреса гаманця (необов'язково). Без фрази модуль працює тільки для перегляду;"
                " з фразою адреса перевіряється",
            ),
            loader.ConfigValue(
                "tonapi_key",
                "",
                "Ключ TonAPI з tonconsole.com (необов'язково, без нього ліміт ~1 запит/с)",
                validator=loader.validators.Hidden(),
            ),
            loader.ConfigValue(
                "max_amount",
                100.0,
                "Максимальна сума одного переказу GRAM (0 = без ліміту)",
                validator=loader.validators.Float(minimum=0),
            ),
        )
        self._nft_cache: t.List[str] = []
        self._used_forms: t.Set[str] = set()

    # ---------- helpers ----------

    def _ton(self) -> TonapiClient:
        return TonapiClient(NetworkGlobalID.MAINNET, api_key=self.config["tonapi_key"] or None)

    def _open_wallet(self, client, phrase: str, kind: str):
        if kind == "tg":
            return WalletTg.from_mnemonic(client, phrase)[0]
        return WalletV5R1.from_mnemonic(client, phrase)[0]

    def _wallet(self, client):
        phrase = (self.config["mnemonic"] or "").strip()
        if not phrase:
            raise TonError(self.strings("no_mnemonic"))
        try:
            wallet = self._open_wallet(client, phrase, self.config["wallet_type"])
        except ValueError as e:
            raise TonError("❌ " + utils.escape_html(str(e)))
        expected = (self.config["address"] or "").strip()
        if expected and Address(expected) != wallet.address:
            raise TonError(
                self.strings("address_mismatch").format(friendly(wallet.address), expected)
            )
        return wallet

    def _address(self) -> str:
        expected = (self.config["address"] or "").strip()
        if expected:
            return friendly(expected)
        if not (self.config["mnemonic"] or "").strip():
            raise TonError(self.strings("no_wallet"))
        return friendly(self._wallet(self._ton()).address)

    async def _api(self, path: str, **params) -> dict:
        headers = {}
        if self.config["tonapi_key"]:
            headers["Authorization"] = f"Bearer {self.config['tonapi_key']}"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(TONAPI + path, params=params, headers=headers) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:200]
                    raise TonError(f"❌ TonAPI {resp.status}: {utils.escape_html(text)}")
                return await resp.json()

    async def _gram_balance(self, address: str) -> int:
        return int((await self._api(f"/accounts/{address}"))["balance"])

    async def _find_jetton(self, address: str, query: str) -> dict:
        data = await self._api(f"/accounts/{address}/jettons")
        q = query.lower()
        for item in data.get("balances", []):
            jetton = item["jetton"]
            if jetton.get("symbol", "").lower() == q:
                return item
            try:
                if Address(query) == Address(jetton["address"]):
                    return item
            except Exception:
                pass
        raise TonError(self.strings("jetton_not_found").format(utils.escape_html(query)))

    def _usage(self, text: str) -> str:
        return self.strings("args").format(utils.escape_html(text))

    def _resolve_nft(self, ref: str) -> str:
        if ref.isdigit():
            idx = int(ref) - 1
            if not 0 <= idx < len(self._nft_cache):
                raise TonError("❌ Немає такого номера. Спочатку виконайте <code>.tonnfts</code>.")
            return self._nft_cache[idx]
        return friendly(ref)

    async def _confirm(self, message, summary: str, builder, gram_needed: int):
        """Перевіряє баланс і показує inline-форму підтвердження."""
        address = friendly(self._wallet(self._ton()).address)
        balance = await self._gram_balance(address)
        if balance < gram_needed:
            raise TonError(
                self.strings("low_balance").format(
                    fmt_amount(gram_needed, GRAM_DECIMALS), fmt_amount(balance, GRAM_DECIMALS)
                )
            )
        form_id = utils.rand(16)
        await self.inline.form(
            message=message,
            text=self.strings("confirm").format(summary),
            reply_markup=[
                [
                    {
                        "text": "✅ Надіслати",
                        "callback": self._do_send,
                        "args": (builder, form_id, time.time() + 120),
                    },
                    {"text": "❌ Скасувати", "action": "close"},
                ]
            ],
        )

    async def _do_send(self, call: InlineCall, builder, form_id: str, deadline: float):
        if form_id in self._used_forms:
            return
        self._used_forms.add(form_id)
        if time.time() > deadline:
            await call.edit(self.strings("expired"))
            return
        await call.edit(self.strings("loading"))
        try:
            async with self._ton() as client:
                wallet = self._wallet(client)
                ext = await wallet.transfer_message(builder)
            await call.edit(
                self.strings("sent").format(ext.normalized_hash, friendly(wallet.address))
            )
        except Exception as e:
            await call.edit(self.strings("error").format(utils.escape_html(str(e))))

    async def _run(self, message, coro):
        try:
            await coro
        except TonError as e:
            await utils.answer(message, str(e))
        except Exception as e:
            await utils.answer(message, self.strings("error").format(utils.escape_html(repr(e))))

    # ---------- commands ----------

    @loader.command()
    async def tonimport(self, message):
        """<seed-фраза> — імпортувати гаманець (тип визначається автоматично)"""
        phrase = " ".join((utils.get_args_raw(message) or "").lower().split())
        await message.delete()
        if not phrase:
            await self._client_send(message, self._usage(".tonimport <seed-фраза>"))
            return

        kind = None
        try:
            WalletTg.validate_mnemonic(phrase)
            kind = "tg"
        except ValueError:
            if len(phrase.split()) == 24 and ton_mnemonic_is_valid(phrase.split()):
                kind = "w5"
        if kind is None:
            await self._client_send(message, self.strings("bad_phrase"))
            return

        wallet = self._open_wallet(self._ton(), phrase, kind)
        self.config["mnemonic"] = phrase
        self.config["wallet_type"] = kind
        self.config["address"] = ""
        await self._client_send(
            message,
            self.strings("imported").format(
                "Telegram (WalletTg)" if kind == "tg" else "W5 (V5R1)", friendly(wallet.address)
            ),
        )

    async def _client_send(self, message, text: str):
        # Початкове повідомлення вже видалено, тому шлемо нове
        await self._client.send_message(message.peer_id, text, parse_mode="html")

    @loader.command()
    async def tonforget(self, message):
        """— видалити seed-фразу з конфігу"""
        self.config["mnemonic"] = ""
        await utils.answer(message, self.strings("forgot"))

    @loader.command()
    async def tonwallet(self, message):
        """— інформація про гаманець"""

        async def inner():
            address = self._address()
            info = await self._api(f"/accounts/{address}")
            lines = [
                f"<b>Тип:</b> {'Telegram (WalletTg)' if self.config['wallet_type'] == 'tg' else 'W5 (V5R1)'}",
                f"<b>Адреса:</b> <code>{address}</code>",
                f"<b>Статус:</b> {info.get('status')}",
                f"<b>Баланс:</b> {fmt_amount(info['balance'], GRAM_DECIMALS)} GRAM",
            ]
            if (self.config["mnemonic"] or "").strip():
                async with self._ton() as client:
                    wallet = self._wallet(client)
                    await wallet.refresh()
                    if wallet.is_active:
                        data = wallet.state_data
                        lines.append(f"<b>Seqno:</b> {data.seqno}")
                        key_ok = data.public_key.as_bytes == wallet.public_key.as_bytes
                        lines.append(
                            "<b>Ключ:</b> " + ("✅ збігається" if key_ok else "❌ не збігається з on-chain")
                        )
                    else:
                        lines.append("<i>Контракт ще не розгорнуто: розгорнеться з першим переказом.</i>")
            else:
                lines.append("<i>Режим тільки для перегляду (без фрази).</i>")
            lines.append(f'<a href="https://tonviewer.com/{address}">Tonviewer</a>')
            await utils.answer(message, "\n".join(lines))

        await self._run(message, inner())

    @loader.command()
    async def tonbal(self, message):
        """— баланс GRAM і джетонів"""

        async def inner():
            address = self._address()
            balance = await self._gram_balance(address)
            data = await self._api(f"/accounts/{address}/jettons", currencies="usd")
            lines = [f"💎 <b>{fmt_amount(balance, GRAM_DECIMALS)} GRAM</b>", ""]
            for item in data.get("balances", []):
                jetton = item["jetton"]
                if jetton.get("verification") == "blacklist" or int(item["balance"]) == 0:
                    continue
                amount = fmt_amount(item["balance"], int(jetton.get("decimals", 9)))
                usd = ""
                price = (item.get("price") or {}).get("prices", {}).get("USD")
                if price:
                    value = Decimal(int(item["balance"])).scaleb(-int(jetton.get("decimals", 9))) * Decimal(str(price))
                    usd = f" (≈${value:.2f})"
                lines.append(
                    f"• {amount} <b>{utils.escape_html(jetton.get('symbol', '?'))}</b>{usd}"
                )
            if len(lines) == 2:
                lines.append("<i>Джетонів немає</i>")
            await utils.answer(message, "\n".join(lines))

        await self._run(message, inner())

    @loader.command()
    async def tonsend(self, message):
        """<адреса|домен> <сума> [коментар] — надіслати GRAM"""

        async def inner():
            args = (utils.get_args_raw(message) or "").split(maxsplit=2)
            if len(args) < 2:
                raise TonError(self._usage(".tonsend <адреса> <сума> [коментар]"))
            dest, amount_text = args[0], args[1]
            comment = args[2] if len(args) > 2 else None
            amount = parse_amount(amount_text, GRAM_DECIMALS)
            limit = Decimal(str(self.config["max_amount"]))
            if limit and Decimal(amount).scaleb(-GRAM_DECIMALS) > limit:
                raise TonError(self.strings("too_much").format(fmt_amount(amount, GRAM_DECIMALS), limit))
            builder = TONTransferBuilder(destination=dest, amount=amount, body=comment)
            summary = (
                f"<b>Сума:</b> {fmt_amount(amount, GRAM_DECIMALS)} GRAM\n"
                f"<b>Кому:</b> <code>{utils.escape_html(dest)}</code>"
            )
            if comment:
                summary += f"\n<b>Коментар:</b> {utils.escape_html(comment)}"
            await self._confirm(message, summary, builder, amount + FEE_RESERVE)

        await self._run(message, inner())

    @loader.command()
    async def tonjsend(self, message):
        """<адреса|домен> <сума> <символ|адреса джетона> [коментар] — надіслати джетон"""

        async def inner():
            args = (utils.get_args_raw(message) or "").split(maxsplit=3)
            if len(args) < 3:
                raise TonError(
                    self._usage(".tonjsend <адреса> <сума> <USDT|адреса джетона> [коментар]")
                )
            dest, amount_text, query = args[:3]
            comment = args[3] if len(args) > 3 else None
            address = friendly(self._wallet(self._ton()).address)
            item = await self._find_jetton(address, query)
            jetton = item["jetton"]
            decimals = int(jetton.get("decimals", 9))
            amount = parse_amount(amount_text, decimals)
            if amount > int(item["balance"]):
                raise TonError(
                    f"❌ На балансі лише {fmt_amount(item['balance'], decimals)} {utils.escape_html(jetton['symbol'])}."
                )
            builder = JettonTransferBuilder(
                destination=dest,
                jetton_amount=amount,
                jetton_wallet_address=item["wallet_address"]["address"],
                forward_payload=comment,
                amount=TOKEN_GAS,
            )
            summary = (
                f"<b>Сума:</b> {fmt_amount(amount, decimals)} {utils.escape_html(jetton['symbol'])}\n"
                f"<b>Джетон:</b> <code>{friendly(jetton['address'])}</code>\n"
                f"<b>Кому:</b> <code>{utils.escape_html(dest)}</code>\n"
                f"<b>Газ:</b> ≤{fmt_amount(TOKEN_GAS, GRAM_DECIMALS)} GRAM (надлишок повернеться)"
            )
            if comment:
                summary += f"\n<b>Коментар:</b> {utils.escape_html(comment)}"
            await self._confirm(message, summary, builder, TOKEN_GAS + FEE_RESERVE)

        await self._run(message, inner())

    @loader.command()
    async def tonnfts(self, message):
        """— список NFT (зокрема подарунки та юзернейми Telegram)"""

        async def inner():
            address = self._address()
            data = await self._api(
                f"/accounts/{address}/nfts", limit="50", offset="0", indirect_ownership="false"
            )
            items = data.get("nft_items", [])
            self._nft_cache = [friendly(i["address"]) for i in items]
            if not items:
                await utils.answer(message, "<i>NFT немає</i>")
                return
            lines = ["🖼 <b>NFT:</b>"]
            for n, item in enumerate(items, 1):
                name = (item.get("metadata") or {}).get("name") or item.get("dns") or "Без назви"
                collection = (item.get("collection") or {}).get("name") or "—"
                sale = " 🏷 на продажу" if item.get("sale") else ""
                lines.append(
                    f"{n}. <b>{utils.escape_html(name)}</b> · {utils.escape_html(collection)}{sale}\n"
                    f"<code>{friendly(item['address'])}</code>"
                )
            lines.append("\nНадіслати: <code>.tonnft &lt;номер|адреса&gt; &lt;кому&gt;</code>")
            await utils.answer(message, "\n".join(lines))

        await self._run(message, inner())

    @loader.command()
    async def tonnft(self, message):
        """<номер|адреса NFT> <адреса|домен> [коментар] — надіслати NFT"""

        async def inner():
            args = (utils.get_args_raw(message) or "").split(maxsplit=2)
            if len(args) < 2:
                raise TonError(self._usage(".tonnft <номер|адреса NFT> <кому> [коментар]"))
            nft = self._resolve_nft(args[0])
            dest = args[1]
            comment = args[2] if len(args) > 2 else None
            address = friendly(self._wallet(self._ton()).address)
            item = await self._api(f"/nfts/{nft}")
            owner = (item.get("owner") or {}).get("address")
            if not owner or Address(owner) != Address(address):
                raise TonError(self.strings("nft_not_found").format(nft))
            if item.get("sale"):
                raise TonError("❌ NFT виставлено на продаж, спершу зніміть його з продажу.")
            name = (item.get("metadata") or {}).get("name") or item.get("dns") or nft
            builder = NFTTransferBuilder(
                destination=dest,
                nft_address=nft,
                forward_payload=comment,
                amount=TOKEN_GAS,
            )
            summary = (
                f"<b>NFT:</b> {utils.escape_html(name)}\n<code>{nft}</code>\n"
                f"<b>Кому:</b> <code>{utils.escape_html(dest)}</code>\n"
                f"<b>Газ:</b> ≤{fmt_amount(TOKEN_GAS, GRAM_DECIMALS)} GRAM (надлишок повернеться)"
            )
            if comment:
                summary += f"\n<b>Коментар:</b> {utils.escape_html(comment)}"
            await self._confirm(message, summary, builder, TOKEN_GAS + FEE_RESERVE)

        await self._run(message, inner())

    @loader.command()
    async def tonhist(self, message):
        """[кількість] — останні операції"""

        async def inner():
            arg = utils.get_args_raw(message)
            limit = min(int(arg), 30) if arg and arg.isdigit() else 10
            address = self._address()
            data = await self._api(f"/accounts/{address}/events", limit=str(limit))
            lines = ["📜 <b>Останні операції:</b>"]
            for event in data.get("events", []):
                when = time.strftime("%d.%m %H:%M", time.localtime(event["timestamp"]))
                pending = " ⏳" if event.get("in_progress") else ""
                for action in event.get("actions", []):
                    preview = action.get("simple_preview") or {}
                    failed = " ❌" if action.get("status") != "ok" else ""
                    text = preview.get("description") or preview.get("name") or action.get("type")
                    lines.append(f"<code>{when}</code> {utils.escape_html(text)}{failed}{pending}")
            if len(lines) == 1:
                lines.append("<i>Операцій немає</i>")
            await utils.answer(message, "\n".join(lines))

        await self._run(message, inner())
