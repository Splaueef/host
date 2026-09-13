# meta developer: @Huang_Baike
# meta version: 1.0.0
# meta description: Безпечне надсилання прихованих історичних Telegram-подарунків
# scope: inline
# scope: hikka_only

__version__ = (1, 0, 0)

import asyncio
import html
import logging
import secrets
import time

try:
    from telethon.errors import RPCError
    from telethon.tl.functions.payments import (
        GetPaymentFormRequest,
        SendStarsFormRequest,
    )
    from telethon.tl.types import InputInvoiceStarGift, TextWithEntities

    GIFT_API_AVAILABLE = True
except ImportError:
    RPCError = Exception
    GetPaymentFormRequest = None
    SendStarsFormRequest = None
    InputInvoiceStarGift = None
    TextWithEntities = None
    GIFT_API_AVAILABLE = False

from .. import loader, utils

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 128
SESSION_TTL = 10 * 60


class GiftFormError(Exception):
    """Telegram returned a payment form that is unsafe to submit."""


@loader.tds
class HiddenGiftsMod(loader.Module):
    """Надсилає 11 прихованих історичних подарунків через Telegram Stars."""

    strings = {
        "name": "HiddenGifts",
        "cfg_hide_name": "За замовчуванням приховувати ім'я відправника у профілі",
        "usage": (
            "🎁 <b>HiddenGifts</b>\n\n"
            "<code>.hgift @username</code> — відкрити каталог\n"
            "<code>.hgift @username | Текст</code> — додати підпис\n"
            "<code>.hgift</code> у приватному чаті — подарунок співрозмовнику\n"
            "<code>.hgift</code> у відповідь — подарунок автору повідомлення\n"
            "<code>.hgiftcheck @username</code> — перевірити доступність усіх 11\n\n"
            "Оплата відбувається лише після окремого підтвердження."
        ),
        "need_recipient": (
            "❌ Не вдалося визначити одержувача. Вкажи користувача: "
            "<code>.hgift @username</code>, відкрий його приватний чат або дай "
            "команду у відповідь на його повідомлення."
        ),
        "bad_recipient": "❌ Не вдалося знайти цього користувача: <code>{}</code>",
        "bot_recipient": "❌ Telegram-подарунок не можна надіслати боту.",
        "message_too_long": (
            "❌ Підпис задовгий: <b>{length}</b>/<b>{maximum}</b> символів."
        ),
        "telethon_too_old": (
            "❌ Ця версія Telethon/Hikka не підтримує оплату Star Gifts. "
            "Онови Hikka та Telethon, а потім перезавантаж модуль."
        ),
        "catalog": (
            "🎁 <b>Приховані історичні подарунки</b>\n\n"
            "👤 <b>Одержувач:</b> {recipient}\n"
            "💬 <b>Підпис:</b> {message}\n"
            "🙈 <b>Приховати ім'я:</b> {hidden}\n\n"
            "Обери подарунок. Telegram перевірить його доступність і поверне "
            "актуальну суму до того, як з'явиться кнопка оплати."
        ),
        "no_message": "немає",
        "yes": "так",
        "no": "ні",
        "session_expired": (
            "⌛ Сесія застаріла або вже використана. Запусти <code>.hgift</code> ще раз."
        ),
        "checking": "🔎 Перевіряю подарунок у Telegram…",
        "confirm": (
            "⚠️ <b>Підтвердження покупки</b>\n\n"
            "🎁 <b>{gift}</b>\n"
            "📅 Тематика: <b>{occasion}</b>\n"
            "🔢 ID: <code>{gift_id}</code>\n"
            "👤 Одержувач: {recipient}\n"
            "💬 Підпис: {message}\n"
            "🙈 Приховати ім'я у профілі: <b>{hidden}</b>\n\n"
            "Telegram підтвердив ціну: <b>{price} ⭐</b>.\n"
            "Після натискання кнопки нижче Stars будуть списані без ще одного вікна."
            "{note}"
        ),
        "price_changed": (
            "\n\n⚠️ <b>Ціна змінилася з {old} до {new} ⭐.</b> "
            "Оплату зупинено — перевір нову суму та підтвердь ще раз."
        ),
        "processing": (
            "⏳ <b>Опрацьовую оплату…</b>\n\n"
            "Не натискай кнопку повторно."
        ),
        "success": (
            "✅ <b>Подарунок надіслано!</b>\n\n"
            "🎁 {gift}\n👤 {recipient}\n💫 Списано: <b>{price} ⭐</b>"
        ),
        "verification": (
            "⚠️ Telegram вимагає додаткового підтвердження платежу.\n"
            "Відкрий посилання: {url}"
        ),
        "payment_uncertain": (
            "⚠️ Під час відправлення платежу зникло з'єднання. Неможливо безпечно "
            "визначити, чи Telegram уже прийняв оплату. Перевір чат з одержувачем "
            "та баланс Stars перед повторною спробою."
        ),
        "cancelled": "❌ Надсилання подарунка скасовано.",
        "check_started": "🔎 Перевіряю 11 прихованих подарунків у Telegram…",
        "check_result": (
            "🎁 <b>Доступність прихованих подарунків</b>\n"
            "👤 {recipient}\n\n{items}\n\n"
            "Перевірка не витрачає Stars. Доступність може змінитися до оплати."
        ),
        "inline_failed": (
            "❌ Не вдалося відкрити inline-меню. Перевір, чи налаштований "
            "inline-бот Hikka."
        ),
    }

    # Retired 50-Star catalogue used by Nostalgift 1.0.0 (August 2026).
    # Telegram remains the source of truth: every item is preflighted before payment.
    GIFTS = (
        {
            "id": 5800655655995968830,
            "emoji": "🤍",
            "name": "Білий ведмедик із серцем",
            "occasion": "14 лютого",
        },
        {
            "id": 5801108895304779062,
            "emoji": "❤️",
            "name": "Серце",
            "occasion": "14 лютого",
        },
        {
            "id": 5866352046986232958,
            "emoji": "🌷",
            "name": "Рожевий ведмедик",
            "occasion": "8 березня",
        },
        {
            "id": 5893356958802511476,
            "emoji": "🍀",
            "name": "Ведмедик-лепрекон",
            "occasion": "17 березня",
        },
        {
            "id": 5935895822435615975,
            "emoji": "🤡",
            "name": "Ведмедик-клоун",
            "occasion": "1 квітня",
        },
        {
            "id": 5969796561943660080,
            "emoji": "🐰",
            "name": "Великодній ведмедик",
            "occasion": "Великдень",
        },
        {
            "id": 6026193266406327981,
            "emoji": "👷",
            "name": "Ведмедик-будівельник",
            "occasion": "1 травня",
        },
        {
            "id": 5974210632977745012,
            "emoji": "⚽",
            "name": "Футбольний ведмедик",
            "occasion": "футбольний фінал",
        },
        {
            "id": 6046178578163303744,
            "emoji": "🎮",
            "name": "Ведмедик CS",
            "occasion": "Counter-Strike",
        },
        {
            "id": 5922558454332916696,
            "emoji": "🎄",
            "name": "Новорічна ялинка",
            "occasion": "31 грудня",
        },
        {
            "id": 5956217000635139069,
            "emoji": "🎅",
            "name": "Новорічний ведмедик",
            "occasion": "31 грудня",
        },
    )
    GIFT_BY_ID = {gift["id"]: gift for gift in GIFTS}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "hide_name_by_default",
                False,
                lambda: self.strings["cfg_hide_name"],
                validator=loader.validators.Boolean(),
            )
        )
        self._client = None
        self._sessions = {}
        self._payment_lock = asyncio.Lock()

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    async def on_unload(self):
        self._sessions.clear()

    @loader.command(ru_doc="Відкрити каталог прихованих історичних подарунків")
    async def hgift(self, message):
        """Відкрити каталог: .hgift [@user] [| підпис]"""
        if not GIFT_API_AVAILABLE:
            await utils.answer(message, self.strings["telethon_too_old"])
            return

        raw = (utils.get_args_raw(message) or "").strip()
        if raw.lower() in {"help", "допомога", "?"}:
            await utils.answer(message, self.strings["usage"])
            return

        target, gift_message = self._split_args(raw)
        if len(gift_message) > MAX_MESSAGE_LENGTH:
            await utils.answer(
                message,
                self.strings["message_too_long"].format(
                    length=len(gift_message), maximum=MAX_MESSAGE_LENGTH
                ),
            )
            return

        recipient = await self._resolve_recipient(message, target)
        if recipient is None:
            return

        token = self._create_session(recipient, gift_message)
        opened = await self.inline.form(
            self._catalog_text(self._sessions[token]),
            message,
            reply_markup=self._catalog_markup(token),
            force_me=True,
        )
        if not opened:
            self._sessions.pop(token, None)
            await utils.answer(message, self.strings["inline_failed"])

    @loader.command(ru_doc="Перевірити доступність 11 прихованих подарунків")
    async def hgiftcheck(self, message):
        """Перевірити доступність: .hgiftcheck [@user]"""
        if not GIFT_API_AVAILABLE:
            await utils.answer(message, self.strings["telethon_too_old"])
            return

        target = (utils.get_args_raw(message) or "").strip()
        recipient = await self._resolve_recipient(message, target)
        if recipient is None:
            return

        await utils.answer(message, self.strings["check_started"])
        session = {
            "peer": recipient["peer"],
            "recipient": recipient["name"],
            "message": "",
            "hide_name": False,
        }
        items = []
        for gift in self.GIFTS:
            try:
                _, _, price = await self._prepare_payment(session, gift["id"])
            except Exception as error:
                detail = self._friendly_error(error, compact=True)
                items.append(
                    f"❌ {gift['emoji']} <b>{html.escape(gift['name'])}</b> — "
                    f"{html.escape(detail)}"
                )
            else:
                items.append(
                    f"✅ {gift['emoji']} <b>{html.escape(gift['name'])}</b> — "
                    f"{price} ⭐"
                )
            await asyncio.sleep(0.15)

        await utils.answer(
            message,
            self.strings["check_result"].format(
                recipient=html.escape(recipient["name"]),
                items="\n".join(items),
            ),
        )

    @staticmethod
    def _split_args(raw):
        if "|" not in raw:
            return raw.strip(), ""
        target, gift_message = raw.split("|", 1)
        return target.strip(), gift_message.strip()

    async def _resolve_recipient(self, message, target):
        try:
            entity = None
            if target:
                entity = await self._client.get_entity(target)
            else:
                reply = None
                if getattr(message, "reply_to_msg_id", None):
                    reply = await message.get_reply_message()
                if reply is not None and getattr(reply, "sender_id", None):
                    entity = await self._client.get_entity(reply.sender_id)
                elif getattr(message, "is_private", False):
                    entity = await message.get_chat()

            if entity is None:
                await utils.answer(message, self.strings["need_recipient"])
                return None
            if getattr(entity, "bot", False):
                await utils.answer(message, self.strings["bot_recipient"])
                return None
            if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
                await utils.answer(message, self.strings["need_recipient"])
                return None

            input_peer = await self._client.get_input_entity(entity)
            return {
                "peer": input_peer,
                "name": self._display_name(entity),
                "id": getattr(entity, "id", None),
            }
        except Exception as error:
            logger.warning("[HiddenGifts] Recipient resolution failed: %s", error)
            shown = target or "поточний чат"
            await utils.answer(
                message,
                self.strings["bad_recipient"].format(html.escape(str(shown))),
            )
            return None

    @staticmethod
    def _display_name(entity):
        parts = [
            str(value).strip()
            for value in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            )
            if value
        ]
        if parts:
            return " ".join(parts)
        return str(
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or getattr(entity, "id", "невідомо")
        )

    def _create_session(self, recipient, gift_message):
        self._purge_sessions()
        token = secrets.token_urlsafe(9)
        self._sessions[token] = {
            "created_at": time.monotonic(),
            "peer": recipient["peer"],
            "recipient": recipient["name"],
            "recipient_id": recipient["id"],
            "message": gift_message,
            "hide_name": bool(self.config["hide_name_by_default"]),
            "gift_id": None,
            "confirmed_price": None,
            "processing": False,
        }
        return token

    def _purge_sessions(self):
        now = time.monotonic()
        for token, session in list(self._sessions.items()):
            if now - session.get("created_at", 0) > SESSION_TTL:
                self._sessions.pop(token, None)

    def _get_session(self, token):
        self._purge_sessions()
        return self._sessions.get(token)

    def _catalog_text(self, session):
        message = session["message"] or self.strings["no_message"]
        return self.strings["catalog"].format(
            recipient=html.escape(session["recipient"]),
            message=html.escape(message),
            hidden=self.strings["yes" if session["hide_name"] else "no"],
        )

    def _catalog_markup(self, token):
        buttons = [
            {
                "text": f"{gift['emoji']} {gift['name']}",
                "callback": self._select_gift,
                "args": (token, gift["id"]),
            }
            for gift in self.GIFTS
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append(
            [
                {
                    "text": "✖️ Скасувати",
                    "callback": self._cancel,
                    "args": (token,),
                }
            ]
        )
        return rows

    async def _select_gift(self, call, token, gift_id):
        session = self._get_session(token)
        gift = self.GIFT_BY_ID.get(int(gift_id))
        if session is None or gift is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return

        await call.answer(self.strings["checking"])
        try:
            _, _, price = await self._prepare_payment(session, gift["id"])
        except Exception as error:
            logger.warning(
                "[HiddenGifts] Gift %s preflight failed: %s", gift["id"], error
            )
            await call.edit(
                self._error_page(gift, error),
                reply_markup=self._back_markup(token),
            )
            return

        session["gift_id"] = gift["id"]
        session["confirmed_price"] = price
        await self._show_confirmation(call, token)

    async def _toggle_hide(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("gift_id") is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        session["hide_name"] = not session["hide_name"]
        await self._show_confirmation(call, token)

    async def _show_confirmation(self, call, token, note=""):
        session = self._get_session(token)
        if session is None or session.get("gift_id") is None:
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        gift = self.GIFT_BY_ID[session["gift_id"]]
        price = session["confirmed_price"]
        await call.edit(
            self.strings["confirm"].format(
                gift=html.escape(f"{gift['emoji']} {gift['name']}"),
                occasion=html.escape(gift["occasion"]),
                gift_id=gift["id"],
                recipient=html.escape(session["recipient"]),
                message=html.escape(session["message"] or self.strings["no_message"]),
                hidden=self.strings["yes" if session["hide_name"] else "no"],
                price=price,
                note=note,
            ),
            reply_markup=[
                [
                    {
                        "text": f"💫 Надіслати за {price} ⭐",
                        "callback": self._pay,
                        "args": (token,),
                    }
                ],
                [
                    {
                        "text": (
                            "🙈 Ім'я приховано"
                            if session["hide_name"]
                            else "👤 Ім'я видиме"
                        ),
                        "callback": self._toggle_hide,
                        "args": (token,),
                    }
                ],
                [
                    {
                        "text": "↩️ Інший подарунок",
                        "callback": self._back_to_catalog,
                        "args": (token,),
                    },
                    {
                        "text": "✖️ Скасувати",
                        "callback": self._cancel,
                        "args": (token,),
                    },
                ],
            ],
        )

    async def _back_to_catalog(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        session["gift_id"] = None
        session["confirmed_price"] = None
        await call.edit(
            self._catalog_text(session), reply_markup=self._catalog_markup(token)
        )

    def _back_markup(self, token):
        return [
            [
                {
                    "text": "↩️ До каталогу",
                    "callback": self._back_to_catalog,
                    "args": (token,),
                },
                {
                    "text": "✖️ Скасувати",
                    "callback": self._cancel,
                    "args": (token,),
                },
            ]
        ]

    async def _pay(self, call, token):
        async with self._payment_lock:
            session = self._get_session(token)
            if (
                session is None
                or session.get("gift_id") is None
                or session.get("confirmed_price") is None
                or session.get("processing")
            ):
                await call.answer(self.strings["session_expired"], show_alert=True)
                return

            gift = self.GIFT_BY_ID[session["gift_id"]]
            session["processing"] = True
            await call.edit(self.strings["processing"], reply_markup=[])

            try:
                invoice, form, price = await self._prepare_payment(
                    session, session["gift_id"]
                )
            except Exception as error:
                session["processing"] = False
                logger.warning(
                    "[HiddenGifts] Final preflight for %s failed: %s",
                    session["gift_id"],
                    error,
                )
                await call.edit(
                    self._error_page(gift, error),
                    reply_markup=self._back_markup(token),
                )
                return

            old_price = session["confirmed_price"]
            if price != old_price:
                session["processing"] = False
                session["confirmed_price"] = price
                note = self.strings["price_changed"].format(old=old_price, new=price)
                await self._show_confirmation(call, token, note=note)
                return

            try:
                result = await self._client(
                    SendStarsFormRequest(form_id=form.form_id, invoice=invoice)
                )
            except (ConnectionError, TimeoutError, OSError):
                self._sessions.pop(token, None)
                logger.exception(
                    "[HiddenGifts] Ambiguous network failure while paying for %s",
                    gift["id"],
                )
                await call.edit(self.strings["payment_uncertain"], reply_markup=[])
                return
            except RPCError as error:
                session["processing"] = False
                logger.warning(
                    "[HiddenGifts] Telegram rejected payment for %s: %s",
                    gift["id"],
                    error,
                )
                await call.edit(
                    self._error_page(gift, error),
                    reply_markup=self._back_markup(token),
                )
                return
            except Exception:
                self._sessions.pop(token, None)
                logger.exception(
                    "[HiddenGifts] Unexpected payment failure for %s", gift["id"]
                )
                await call.edit(self.strings["payment_uncertain"], reply_markup=[])
                return

            self._sessions.pop(token, None)
            verification_url = getattr(result, "url", None)
            if verification_url:
                await call.edit(
                    self.strings["verification"].format(
                        url=html.escape(str(verification_url), quote=True)
                    ),
                    reply_markup=[
                        [
                            {
                                "text": "Відкрити підтвердження",
                                "url": str(verification_url),
                            }
                        ]
                    ],
                )
                return

            await call.edit(
                self.strings["success"].format(
                    gift=html.escape(f"{gift['emoji']} {gift['name']}"),
                    recipient=html.escape(session["recipient"]),
                    price=price,
                ),
                reply_markup=[],
            )

    async def _cancel(self, call, token):
        session = self._sessions.pop(token, None)
        if session is None:
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        await call.edit(self.strings["cancelled"], reply_markup=[])

    def _build_invoice(self, session, gift_id):
        gift_message = session.get("message", "").strip()
        text = (
            TextWithEntities(text=gift_message, entities=[])
            if gift_message
            else None
        )
        return InputInvoiceStarGift(
            peer=session["peer"],
            gift_id=int(gift_id),
            hide_name=bool(session.get("hide_name", False)),
            message=text,
        )

    async def _prepare_payment(self, session, gift_id):
        invoice = self._build_invoice(session, gift_id)
        form = await self._client(GetPaymentFormRequest(invoice=invoice))
        form_invoice = getattr(form, "invoice", None)
        form_id = getattr(form, "form_id", None)
        if form_invoice is None or form_id is None:
            raise GiftFormError("Telegram повернув неповну платіжну форму")

        currency = str(getattr(form_invoice, "currency", "")).upper()
        prices = getattr(form_invoice, "prices", None)
        if currency != "XTR":
            raise GiftFormError("валюта платіжної форми — не Telegram Stars")
        if not prices:
            raise GiftFormError("у платіжній формі немає ціни")

        amounts = [int(getattr(item, "amount", -1)) for item in prices]
        if any(amount < 0 for amount in amounts):
            raise GiftFormError("Telegram повернув некоректну ціну")
        total = sum(amounts)
        if total <= 0:
            raise GiftFormError("Telegram повернув нульову ціну")
        return invoice, form, total

    def _error_page(self, gift, error):
        return (
            "❌ <b>Подарунок зараз не можна надіслати</b>\n\n"
            f"{gift['emoji']} <b>{html.escape(gift['name'])}</b>\n"
            f"🔢 <code>{gift['id']}</code>\n\n"
            f"{html.escape(self._friendly_error(error))}"
        )

    @staticmethod
    def _friendly_error(error, compact=False):
        raw = f"{type(error).__name__} {error}"
        code = raw.upper().replace(" ", "_")
        compact_code = code.replace("_", "")
        known = (
            ("BALANCE_TOO_LOW", "недостатньо Stars на балансі"),
            (
                "USER_DISALLOWED_STARGIFTS",
                "одержувач заборонив цю категорію подарунків",
            ),
            ("STARGIFT_MESSAGE_INVALID", "Telegram відхилив підпис; спробуй без нього"),
            ("STARGIFT_USER_USAGE_LIMITED", "досягнуто ліміт покупок для акаунта"),
            ("STARGIFT_USAGE_LIMITED", "подарунок більше не доступний або вичерпаний"),
            ("STARGIFT_NOT_FOUND", "Telegram більше не знаходить цей подарунок"),
            ("STARGIFT_INVALID", "Telegram більше не приймає цей ID подарунка"),
            ("FORM_EXPIRED", "платіжна форма застаріла; обери подарунок ще раз"),
            ("STARS_FORM_AMOUNT_MISMATCH", "ціна змінилася; повтори перевірку"),
            ("API_GIFT_RESTRICTED_UPDATE_APP", "потрібно оновити Hikka/Telethon"),
            ("FORM_UNSUPPORTED", "ця версія Hikka/Telethon не підтримує форму"),
            ("PEER_ID_INVALID", "Telegram не прийняв одержувача"),
            ("USER_ID_INVALID", "Telegram не прийняв одержувача"),
        )
        for marker, message in known:
            if marker in code or marker.replace("_", "") in compact_code:
                return message
        if isinstance(error, GiftFormError):
            return str(error)
        if compact:
            return "недоступний"
        return f"Telegram повернув помилку: {str(error)[:300]}"
