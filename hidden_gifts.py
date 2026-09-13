# meta developer: @Huang_Baike
# meta version: 1.1.0
# meta description: Безпечне надсилання актуальних та прихованих Telegram-подарунків
# scope: inline
# scope: hikka_only

__version__ = (1, 1, 0)

import asyncio
import html
import logging
import secrets
import time

try:
    from telethon.errors import RPCError
    from telethon.tl.functions.payments import (
        GetPaymentFormRequest,
        GetStarGiftsRequest,
        SendStarsFormRequest,
    )
    from telethon.tl.types import InputInvoiceStarGift, TextWithEntities

    GIFT_API_AVAILABLE = True
except ImportError:
    RPCError = Exception
    GetPaymentFormRequest = None
    GetStarGiftsRequest = None
    SendStarsFormRequest = None
    InputInvoiceStarGift = None
    TextWithEntities = None
    GIFT_API_AVAILABLE = False

from .. import loader, utils

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 128
SESSION_TTL = 10 * 60
CATALOG_TTL = 5 * 60
PAGE_SIZE = 8


class GiftFormError(Exception):
    """Telegram returned a payment form that is unsafe to submit."""


@loader.tds
class HiddenGiftsMod(loader.Module):
    """Надсилає актуальні та приховані історичні подарунки через Stars."""

    strings = {
        "name": "HiddenGifts",
        "cfg_hide_name": "За замовчуванням приховувати ім'я відправника у профілі",
        "usage": (
            "🎁 <b>HiddenGifts</b>\n\n"
            "<code>.hgift @username</code> — відкрити весь каталог\n"
            "<code>.hgift @username | Текст</code> — додати підпис\n"
            "<code>.hgift</code> у приватному чаті — подарунок співрозмовнику\n"
            "<code>.hgift</code> у відповідь — подарунок автору повідомлення\n"
            "<code>.hgiftcheck @username</code> — перевірити 11 історичних ID\n\n"
            "Актуальні подарунки завантажуються безпосередньо з Telegram. "
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
            "❌ Ця версія Telethon/Hikka не підтримує каталог або оплату Star Gifts. "
            "Онови Hikka та Telethon, а потім перезавантаж модуль."
        ),
        "home": (
            "🎁 <b>Каталог Telegram-подарунків</b>\n\n"
            "👤 <b>Одержувач:</b> {recipient}\n"
            "💬 <b>Підпис:</b> {message}\n"
            "🙈 <b>Приховати ім'я:</b> {hidden}\n\n"
            "🛍 Доступні зараз: <b>{current_count}</b>\n"
            "🕰 Приховані історичні: <b>{historical_count}</b>\n\n"
            "Обери розділ. Перед оплатою Telegram перевірить подарунок і поверне "
            "точну суму.{warning}"
        ),
        "catalog_warning": (
            "\n\n⚠️ Актуальний каталог не завантажився: {error}. "
            "Історичні подарунки все одно доступні."
        ),
        "catalog_page": (
            "🎁 <b>{title}</b>\n\n"
            "👤 <b>Одержувач:</b> {recipient}\n"
            "📄 Сторінка <b>{page}/{pages}</b> · подарунків: <b>{count}</b>\n\n"
            "{hint}"
        ),
        "current_hint": (
            "Показані всі звичайні подарунки, які Telegram зараз дозволяє "
            "купувати. Ціна на кнопці довідкова; перед списанням вона буде "
            "перевірена ще раз."
        ),
        "historical_hint": (
            "Ці старі подарунки приховані у стандартному каталозі. Кожен ID "
            "перевіряється через Telegram перед появою кнопки оплати."
        ),
        "current_empty": (
            "Актуальних подарунків для звичайного надсилання зараз немає або "
            "Telegram не повернув каталог."
        ),
        "no_message": "немає",
        "yes": "так",
        "no": "ні",
        "session_expired": (
            "⌛ Сесія застаріла або вже використана. Запусти <code>.hgift</code> ще раз."
        ),
        "refreshing": "🔄 Оновлюю каталог безпосередньо з Telegram…",
        "checking": "🔎 Перевіряю подарунок у Telegram…",
        "confirm": (
            "⚠️ <b>Підтвердження покупки</b>\n\n"
            "🎁 <b>{gift}</b>\n"
            "📚 Категорія: <b>{category}</b>\n"
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
        self._current_gifts = ()
        self._current_loaded_at = 0.0
        self._catalog_lock = asyncio.Lock()
        self._payment_lock = asyncio.Lock()

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    async def on_unload(self):
        self._sessions.clear()
        self._current_gifts = ()

    @loader.command(ru_doc="Відкрити каталог усіх доступних Telegram-подарунків")
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

        catalog_error = ""
        try:
            current_gifts = await self._get_current_gifts()
        except Exception as error:
            logger.warning("[HiddenGifts] Current catalogue load failed: %s", error)
            current_gifts = ()
            catalog_error = self._friendly_error(error)

        token = self._create_session(
            recipient,
            gift_message,
            current_gifts=current_gifts,
            catalog_error=catalog_error,
        )
        opened = await self.inline.form(
            self._home_text(self._sessions[token]),
            message,
            reply_markup=self._home_markup(token),
            force_me=True,
        )
        if not opened:
            self._sessions.pop(token, None)
            await utils.answer(message, self.strings["inline_failed"])

    @loader.command(ru_doc="Перевірити доступність 11 прихованих подарунків")
    async def hgiftcheck(self, message):
        """Перевірити історичні ID: .hgiftcheck [@user]"""
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

    async def _get_current_gifts(self, force=False):
        if (
            not force
            and self._current_loaded_at
            and time.monotonic() - self._current_loaded_at < CATALOG_TTL
        ):
            return self._current_gifts

        async with self._catalog_lock:
            if (
                not force
                and self._current_loaded_at
                and time.monotonic() - self._current_loaded_at < CATALOG_TTL
            ):
                return self._current_gifts

            response = await self._client(GetStarGiftsRequest(hash=0))
            raw_gifts = getattr(response, "gifts", None)
            if raw_gifts is None:
                raise GiftFormError("Telegram повернув неповний каталог подарунків")
            self._current_gifts = tuple(self._normalise_current_gifts(raw_gifts))
            self._current_loaded_at = time.monotonic()
            return self._current_gifts

    def _normalise_current_gifts(self, raw_gifts):
        historical_ids = set(self.GIFT_BY_ID)
        seen = set()
        result = []
        for item in raw_gifts:
            gift_id = int(getattr(item, "id", 0) or 0)
            stars = int(getattr(item, "stars", 0) or 0)
            remains = getattr(item, "availability_remains", None)
            per_user_remains = getattr(item, "per_user_remains", None)
            if (
                gift_id <= 0
                or gift_id in historical_ids
                or gift_id in seen
                or stars <= 0
                or bool(getattr(item, "sold_out", False))
                or bool(getattr(item, "auction", False))
                or remains == 0
                or per_user_remains == 0
                or self._is_locked(item)
            ):
                continue

            seen.add(gift_id)
            title = str(getattr(item, "title", "") or "").strip()
            emoji = self._gift_emoji(getattr(item, "sticker", None))
            result.append(
                {
                    "id": gift_id,
                    "emoji": emoji,
                    "name": title or f"Подарунок {gift_id}",
                    "occasion": (
                        "день народження"
                        if bool(getattr(item, "birthday", False))
                        else "актуальний каталог Telegram"
                    ),
                    "source": "current",
                    "stars": stars,
                    "premium": bool(getattr(item, "require_premium", False)),
                    "availability_remains": remains,
                    "availability_total": getattr(item, "availability_total", None),
                }
            )
        return result

    @staticmethod
    def _is_locked(item):
        locked_until = getattr(item, "locked_until_date", None)
        if not locked_until:
            return False
        try:
            timestamp = locked_until.timestamp()
        except AttributeError:
            try:
                timestamp = float(locked_until)
            except (TypeError, ValueError):
                return True
        return timestamp > time.time()

    @staticmethod
    def _gift_emoji(sticker):
        for attribute in getattr(sticker, "attributes", ()) or ():
            alt = str(getattr(attribute, "alt", "") or "").strip()
            if alt:
                return alt[:8]
        return "🎁"

    def _create_session(
        self,
        recipient,
        gift_message,
        current_gifts=None,
        catalog_error="",
    ):
        self._purge_sessions()
        token = secrets.token_urlsafe(9)
        current_gifts = tuple(current_gifts or ())
        self._sessions[token] = {
            "created_at": time.monotonic(),
            "peer": recipient["peer"],
            "recipient": recipient["name"],
            "recipient_id": recipient["id"],
            "message": gift_message,
            "hide_name": bool(self.config["hide_name_by_default"]),
            "current_gifts": current_gifts,
            "current_by_id": {gift["id"]: gift for gift in current_gifts},
            "catalog_error": catalog_error,
            "category": "home",
            "page": 0,
            "gift_id": None,
            "gift": None,
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

    def _home_text(self, session):
        warning = ""
        if session.get("catalog_error"):
            warning = self.strings["catalog_warning"].format(
                error=html.escape(session["catalog_error"])
            )
        return self.strings["home"].format(
            recipient=html.escape(session["recipient"]),
            message=html.escape(session["message"] or self.strings["no_message"]),
            hidden=self.strings["yes" if session["hide_name"] else "no"],
            current_count=len(session["current_gifts"]),
            historical_count=len(self.GIFTS),
            warning=warning,
        )

    def _home_markup(self, token):
        session = self._get_session(token)
        current_count = len(session["current_gifts"]) if session else 0
        return [
            [
                {
                    "text": f"🛍 Актуальні · {current_count}",
                    "callback": self._open_catalog,
                    "args": (token, "current", 0),
                }
            ],
            [
                {
                    "text": f"🕰 Історичні · {len(self.GIFTS)}",
                    "callback": self._open_catalog,
                    "args": (token, "historical", 0),
                }
            ],
            [
                {
                    "text": "🔄 Оновити каталог",
                    "callback": self._refresh_catalog,
                    "args": (token,),
                },
                {
                    "text": "✖️ Скасувати",
                    "callback": self._cancel,
                    "args": (token,),
                },
            ],
        ]

    def _gifts_for_category(self, session, category):
        if category == "historical":
            return self.GIFTS
        if category == "current":
            return session["current_gifts"]
        return ()

    async def _open_catalog(self, call, token, category, page=0):
        session = self._get_session(token)
        if session is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        gifts = self._gifts_for_category(session, category)
        if not gifts:
            await call.answer(self.strings["current_empty"], show_alert=True)
            return

        pages = max(1, (len(gifts) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(int(page), pages - 1))
        session["category"] = category
        session["page"] = page
        await call.edit(
            self._catalog_page_text(session, category, page),
            reply_markup=self._catalog_page_markup(token, category, page),
        )

    def _catalog_page_text(self, session, category, page):
        gifts = self._gifts_for_category(session, category)
        pages = max(1, (len(gifts) + PAGE_SIZE - 1) // PAGE_SIZE)
        return self.strings["catalog_page"].format(
            title=(
                "Актуальні подарунки Telegram"
                if category == "current"
                else "Приховані історичні подарунки"
            ),
            recipient=html.escape(session["recipient"]),
            page=page + 1,
            pages=pages,
            count=len(gifts),
            hint=self.strings[
                "current_hint" if category == "current" else "historical_hint"
            ],
        )

    def _catalog_page_markup(self, token, category, page):
        session = self._get_session(token)
        if session is None:
            return []
        gifts = self._gifts_for_category(session, category)
        pages = max(1, (len(gifts) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(int(page), pages - 1))
        start = page * PAGE_SIZE
        visible = gifts[start : start + PAGE_SIZE]
        buttons = [
            {
                "text": self._gift_button_text(gift),
                "callback": self._select_gift,
                "args": (token, gift["id"], category, page),
            }
            for gift in visible
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]

        navigation = []
        if page > 0:
            navigation.append(
                {
                    "text": "⬅️",
                    "callback": self._open_catalog,
                    "args": (token, category, page - 1),
                }
            )
        if page + 1 < pages:
            navigation.append(
                {
                    "text": "➡️",
                    "callback": self._open_catalog,
                    "args": (token, category, page + 1),
                }
            )
        if navigation:
            rows.append(navigation)
        rows.append(
            [
                {
                    "text": "↩️ До розділів",
                    "callback": self._back_home,
                    "args": (token,),
                },
                {
                    "text": "✖️ Скасувати",
                    "callback": self._cancel,
                    "args": (token,),
                }
            ]
        )
        return rows

    @staticmethod
    def _gift_button_text(gift):
        price = f" · {gift['stars']} ⭐" if gift.get("stars") else ""
        premium = "🔒 " if gift.get("premium") else ""
        label = f"{premium}{gift['emoji']} {gift['name']}{price}"
        return label if len(label) <= 60 else f"{label[:57]}…"

    async def _refresh_catalog(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        await call.answer(self.strings["refreshing"])
        try:
            gifts = tuple(await self._get_current_gifts(force=True))
        except Exception as error:
            logger.warning("[HiddenGifts] Catalogue refresh failed: %s", error)
            session["catalog_error"] = self._friendly_error(error)
        else:
            session["current_gifts"] = gifts
            session["current_by_id"] = {gift["id"]: gift for gift in gifts}
            session["catalog_error"] = ""
        session["category"] = "home"
        session["page"] = 0
        await call.edit(
            self._home_text(session), reply_markup=self._home_markup(token)
        )

    async def _back_home(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        session["category"] = "home"
        session["page"] = 0
        session["gift_id"] = None
        session["gift"] = None
        session["confirmed_price"] = None
        await call.edit(
            self._home_text(session), reply_markup=self._home_markup(token)
        )

    def _gift_for_session(self, session, gift_id):
        gift_id = int(gift_id)
        return self.GIFT_BY_ID.get(gift_id) or session["current_by_id"].get(gift_id)

    async def _select_gift(self, call, token, gift_id, category=None, page=0):
        session = self._get_session(token)
        gift = self._gift_for_session(session, gift_id) if session else None
        if session is None or gift is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return

        session["category"] = category or gift.get("source", "historical")
        session["page"] = int(page)
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
        session["gift"] = gift
        session["confirmed_price"] = price
        await self._show_confirmation(call, token)

    async def _toggle_hide(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("gift") is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        session["hide_name"] = not session["hide_name"]
        await self._show_confirmation(call, token)

    async def _show_confirmation(self, call, token, note=""):
        session = self._get_session(token)
        if session is None or session.get("gift") is None:
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        gift = session["gift"]
        price = session["confirmed_price"]
        await call.edit(
            self.strings["confirm"].format(
                gift=html.escape(f"{gift['emoji']} {gift['name']}"),
                category=html.escape(self._gift_category(gift)),
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

    @staticmethod
    def _gift_category(gift):
        if gift.get("source") != "current":
            return f"історичний · {gift['occasion']}"
        details = [gift.get("occasion") or "актуальний каталог Telegram"]
        if gift.get("premium"):
            details.append("тільки Premium")
        remains = gift.get("availability_remains")
        total = gift.get("availability_total")
        if remains is not None:
            details.append(f"залишилося {remains}/{total or '?'}")
        return " · ".join(details)

    async def _back_to_catalog(self, call, token):
        session = self._get_session(token)
        if session is None or session.get("processing"):
            await call.answer(self.strings["session_expired"], show_alert=True)
            return
        session["gift_id"] = None
        session["gift"] = None
        session["confirmed_price"] = None
        category = session.get("category", "historical")
        if category not in {"current", "historical"}:
            await self._back_home(call, token)
            return
        await self._open_catalog(call, token, category, session.get("page", 0))

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
                or session.get("gift") is None
                or session.get("confirmed_price") is None
                or session.get("processing")
            ):
                await call.answer(self.strings["session_expired"], show_alert=True)
                return

            gift = session["gift"]
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
            ("PREMIUM_ACCOUNT_REQUIRED", "цей подарунок доступний лише з Telegram Premium"),
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
