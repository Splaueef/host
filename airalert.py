__version__ = (1, 2, 0)

"""
    █▀▄▀█ █▀█ █▀█ █ █▀ █ █ █▀▄▀█ █▀▄▀█ █▀▀ █▀█
    █ ▀ █ █▄█ █▀▄ █ ▄█ █▄█ █ ▀ █ █ ▀ █ ██▄ █▀▄
    Copyright 2022 t.me/morisummermods
    Licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
"""
# scope: inline_content
# meta developer: @morisummermods
# meta banner: https://i.imgur.com/V0Qhyi0.jpg
# meta pic: https://i.imgur.com/AwKGCQe.png

import logging
from asyncio import sleep

from aiogram.types import InlineQueryResultArticle, InputTextMessageContent
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Message
from telethon.utils import get_display_name, get_peer_id

from .. import loader, utils
from ..inline import GeekInlineQuery, rand

logger = logging.getLogger(__name__)

ua = [
    "all",
    "Кіровоградська_область",
    "Попаснянська_територіальна_громада",
    "Бердянський_район",
    "Полтавська_область",
    "м_Краматорськ_та_Краматорська_територіальна_громада",
    "м_Старокостянтинів_та_Старокостянтинівська_територіальна_громада",
    "Ізюмський_район",
    "Покровська_територіальна_громада",
    "Волноваський_район",
    "Краматорський_район",
    "Київська_область",
    "м_Київ",
    "Херсонська_область",
    "Ніжинський_район",
    "Бахмутська_територіальна_громада",
    "м_Кремінна_та_Кремінська_територіальна_громада",
    "Рівненська_область",
    "Запорізька_область",
    "м_Маріуполь_та_Маріупольська_територіальна_громада",
    "м_Рівне_та_Рівненська_територіальна_громада",
    "м_Черкаси_та_Черкаська_територіальна_громада",
    "Марїнська_територіальна_громада",
    "Сквирська_територіальна_громада",
    "Охтирський_район",
    "м_Конотоп_та_Конотопська_територіальна_громада",
    "Вознесенський_район",
    "Сарненський_район",
    "Миколаївський_район",
    "Смілянська_територіальна_громада",
    "Сєвєродонецький_район",
    "Гірська_територіальна_громада",
    "Костянтинівська_територіальна_громада",
    "Прилуцький_район",
    "м_Пирятин_та_Пирятинська_територіальна_громада",
    "Вишгородська_територіальна_громада",
    "Воскресенська_територіальна_громада",
    "м_Переяслав_та_Переяславська_територіальна_громада",
    "м_Полтава_та_Полтавська_територіальна_громада",
    "м_Вознесенськ_та_Вознесенська_територіальна_громада",
    "Дружківська_територіальна_громада",
    "Золотоніський_район",
    "Макарівська_територіальна_громада",
    "Дубровицька_територіальна_громада",
    "Хмельницька_область",
    "Великоновосілківська_територіальна_громада",
    "м_Шостка_та_Шосткинська_територіальна_громада",
    "Львівська_область",
    "Волинська_область",
    "Первомайський_район",
    "м_Запоріжжя_та_Запорізька_територіальна_громада",
    "м_Бровари_та_Броварська_територіальна_громада",
    "Лиманська_територіальна_громада",
    "м_Лисичанськ_та_Лисичанська_територіальна_громада",
    "м_Бориспіль_та_Бориспільська_територіальна_громада",
    "м_Обухів_та_Обухівська_територіальна_громада",
    "Звенигородський_район",
    "Роздільнянський_район",
    "м_Нікополь_та_Нікопольська_територіальна_громада",
    "м_Першотравенськ_та_Першотравенська_територіальна_громада",
    "м_Васильків_та_Васильківська_територіальна_громада",
    "Кропивницький_район",
    "Шепетівський_район",
    "Житомирська_область",
    "Вараський_район",
    "Болградський_район",
    "Закарпатська_область",
    "Шосткинський_район",
    "Гребінківська_територіальна_громада",
    "Чернівецька_область",
    "Синельниківський_район",
    "Уманська_територіальна_громада",
    "Олешківська_територіальна_громада",
    "м_Кременчук_та_Кременчуцька_територіальна_громада",
    "Коростенський_район",
    "Купянський_район",
    "Подільський_район",
    "м_Мелітополь_та_Мелітопольська_територіальна_громада",
    "Ізмаїльський_район",
    "Вінницька_область",
    "м_Славутич_та_Славутицька_територіальна_громада",
    "Бородянська_територіальна_громада",
    "Святогірська_територіальна_громада",
    "Добропільська_територіальна_громада",
    "Черкаський_район",
    "Пологівський_район",
    "м_Сарни_та_Сарненська_територіальна_громада",
    "Маріупольський_район",
    "Лозівський_район",
    "Березівський_район",
    "Українська_територіальна_громада",
    "м_Охтирка_та_Охтирська_територіальна_громада",
    "Жашківська_територіальна_громада",
    "Житомирський_район",
    "Донецький_район",
    "м_Кривий_Ріг_та_Криворізька_територіальна_громада",
    "Радомишльська_територіальна_громада",
    "м_Дніпро_та_Дніпровська_територіальна_громада",
    "м_Миколаїв_та_Миколаївська_територіальна_громада",
    "Гостомелська_територіальна_громада",
    "м_Миргород_та_Миргородська_територіальна_громада",
    "Сумська_область",
    "Торецька_територіальна_громада",
    "м_Ватутіне_та_Ватутінська_територіальна_громада",
    "м_Коростень_та_Коростенська_територіальна_громада",
    "Харківський_район",
    "Уманський_район",
    "Сумський_район",
    "Одеський_район",
    "БілгородДністровський_район",
    "Тернопільська_область",
    "Первомайська_територіальна_громада",
    "м_Первомайськ_та_Первомайська_територіальна_громада",
    "Чугуївський_район",
    "м_Фастів_та_Фастівська_територіальна_громада",
    "Миронівська_територіальна_громада",
    "м_Лубни_та_Лубенська_територіальна_громада",
    "Черкаська_область",
    "Луганська_область",
    "м_Житомир_та_Житомирська_територіальна_громада",
    "Новоукраїнський_район",
    "м_Словянськ_та_Словянська_територіальна_громада",
    "Чернігівський_район",
    "м_Очаків_та_Очаківська_територіальна_громада",
    "Вугледарська_територіальна_громада",
    "м_Сєвєродонецьк_та_Сєвєродонецька_територіальна_громада",
    "Дніпропетровська_область",
    "Запорізький_район",
    "Широківська_територіальна_громада",
    "Узинська_територіальна_громада",
    "Миколаївська_область",
    "Харківська_область",
    "НовоградВолинський_район",
    "Курахівська_територіальна_громада",
    "м_Рубіжне_та_Рубіжанська_територіальна_громада",
    "Донецька_область",
    "м_Суми_та_Сумська_територіальна_громада",
    "м_Біла_Церква_та_Білоцерківська_територіальна_громада",
    "Голованівський_район",
    "Одеська_область",
    "Павлоградський_район",
    "Чернігівська_область",
    "Сватівський_район",
    "ІваноФранківська_область",
    "Покровський_район",
    "Бахмутський_район",
]


REGION_EDIT_PREFIX = "⌛ Налаштування регіону "
SOURCE_CHANNEL_ID = 1766138888
SOURCE_CHANNEL = "@air_alert_ua"


@loader.tds
class AirAlertMod(loader.Module):
    """🇺🇦 Сповіщення про повітряну тривогу.
    Підпишіться на @air_alert_ua; налаштування: .config AirAlert."""

    strings = {"name": "AirAlert"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "enabled", True, "Увімкнути обробку тривог",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "regions", [], "Регіони з @air_alert_ua: all — всі; порожній список — жоден",
                validator=loader.validators.Series(loader.validators.String()),
            ),
            loader.ConfigValue(
                "forward_chats", [], "ID або @username чатів для пересилання",
                validator=loader.validators.Series(loader.validators.String()),
            ),
            loader.ConfigValue(
                "nametag", "", "Підпис наприкінці пересланого повідомлення",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "notify_pm", True, "Надсилати сповіщення в особисті повідомлення бота",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "repeat_count", 1, "Кількість копій у приватних повідомленнях (1–3)",
                validator=loader.validators.Integer(minimum=1, maximum=3),
            ),
            loader.ConfigValue(
                "repeat_delay", 1, "Інтервал між копіями в секундах (1–30)",
                validator=loader.validators.Integer(minimum=1, maximum=30),
            ),
        )
        self._seen_alerts = set()

    async def client_ready(self, client, db) -> None:
        self.db = db
        self.client = client
        if not db.get("AirAlert", "config_migrated", False):
            for config_key, old_key in (
                ("regions", "regions"),
                ("forward_chats", "forwards"),
                ("nametag", "nametag"),
            ):
                old_value = db.get("AirAlert", old_key, None)
                if old_value and not self.config[config_key]:
                    if config_key != "nametag":
                        old_value = [str(value) for value in old_value]
                    self.config[config_key] = old_value
            db.set("AirAlert", "config_migrated", True)

        if hasattr(self, "hikka"):
            self.me = self._client.tg_id
            self.bot_id = self.inline.bot_id
            try:
                await self.request_join(
                    SOURCE_CHANNEL, "Необхідний для роботи AirAlert", assure_joined=True
                )
            except Exception:
                logger.warning("Не вдалося підписатися на %s", SOURCE_CHANNEL, exc_info=True)
            return

        self.bot_id = (await self.inline.bot.get_me()).id
        self.me = (await client.get_me()).id
        try:
            await client(JoinChannelRequest(await client.get_entity(SOURCE_CHANNEL)))
        except Exception:
            logger.warning("Не вдалося підписатися на %s", SOURCE_CHANNEL, exc_info=True)

    @staticmethod
    def _chat_ref(value):
        value = str(value).strip()
        return int(value) if value.lstrip("-").isdigit() else value

    @staticmethod
    def _region_matches(region, text):
        name = str(region).strip().casefold().replace("_", " ")
        return bool(name) and name in text.casefold().replace("_", " ")

    async def alertforwardcmd(self, message: Message) -> None:
        """.alertforward @chat — додати/видалити чат; без аргументів — список.
        .alertforward set <підпис> — змінити підпис (без тексту — очистити)."""
        text = utils.get_args_raw(message).strip()
        if text == "set" or text.startswith("set "):
            self.config["nametag"] = text[4:].strip()
            return await utils.answer(
                message,
                f"🏷 <b>Підпис:</b> <code>{utils.escape_html(self.config['nametag']) or '—'}</code>",
            )
        if not text:
            lines = ["<b>Чати для пересилання:</b>"]
            for chat in self.config["forward_chats"]:
                try:
                    entity = await self.client.get_entity(self._chat_ref(chat))
                    label = get_display_name(entity)
                except Exception:
                    label = str(chat)
                lines.append(
                    f"• {utils.escape_html(label)} (<code>{utils.escape_html(str(chat))}</code>)"
                )
            if not self.config["forward_chats"]:
                lines.append("Список порожній.")
            lines.append("\nНалаштування: <code>.config AirAlert</code>")
            await utils.answer(message, "\n".join(lines))
            return
        try:
            entity = await self.client.get_entity(self._chat_ref(text))
            chat = str(get_peer_id(entity))
        except Exception:
            await utils.answer(message, "<b>Чат не знайдено</b>")
            return
        chats = [str(value) for value in self.config["forward_chats"]]
        aliases = {chat, str(entity.id), text}
        if any(value in aliases for value in chats):
            self.config["forward_chats"] = [value for value in chats if value not in aliases]
            await utils.answer(message, "<b>Чат видалено зі списку пересилання</b>")
        else:
            self.config["forward_chats"] = chats + [chat]
            await utils.answer(message, "<b>Чат додано до списку пересилання</b>")

    async def alert_inline_handler(self, query: GeekInlineQuery) -> None:
        """Вибір регіонів: @бот alert <пошук>, alert my або alert all."""
        text = (query.args or "").strip()
        regions = set(self.config["regions"])
        if not text:
            result = ua
        elif text.lower() == "my":
            result = [region for region in ua if region in regions]
        else:
            result = [region for region in ua if text.casefold() in region.casefold()]
        if not result:
            await query.e404()
            return
        res = [
            InlineQueryResultArticle(
                id=rand(20),
                title=f"{'✅' if reg in regions else '❌'} {reg if reg != 'all' else 'Усі сповіщення'}",
                description=(
                    f"Натисніть, щоб {'видалити' if reg in regions else 'додати'}"
                    if reg != "all"
                    else f"🇺🇦 Натисніть, щоб {'вимкнути' if 'all' in regions else 'увімкнути'} всі сповіщення"
                ),
                input_message_content=InputTextMessageContent(
                    f"{REGION_EDIT_PREFIX}<code>{reg}</code>", parse_mode="HTML"
                ),
            )
            for reg in result[:50]
        ]
        await query.answer(res, cache_time=0)

    async def watcher(self, message: Message) -> None:
        if (
            getattr(message, "out", False)
            and getattr(message, "via_bot_id", None) == self.bot_id
            and (getattr(message, "raw_text", "") or "").startswith(REGION_EDIT_PREFIX)
        ):
            region = message.raw_text[len(REGION_EDIT_PREFIX):].strip()
            if region not in ua:
                return
            regions = list(self.config["regions"])
            if region in regions:
                regions.remove(region)
                state = "видалено"
            else:
                if region == "all":
                    regions = ["all"]
                else:
                    regions = [value for value in regions if value != "all"]
                    regions.append(region)
                state = "додано"
            self.config["regions"] = regions
            await self.inline.form(
                f"<b>Регіон <code>{utils.escape_html(region)}</code> {state}.</b>\n"
                "Налаштування: <code>.config AirAlert</code>",
                message=message,
            )
            return

        if (
            not self.config["enabled"]
            or getattr(getattr(message, "peer_id", None), "channel_id", None) != SOURCE_CHANNEL_ID
        ):
            return
        alert_text = getattr(message, "raw_text", "") or ""
        regions = self.config["regions"]
        if not alert_text or not regions or not (
            "all" in regions
            or any(self._region_matches(region, alert_text) for region in regions)
        ):
            return
        alert_id = getattr(message, "id", None)
        if alert_id is not None:
            if alert_id in self._seen_alerts:
                return
            if len(self._seen_alerts) >= 256:
                self._seen_alerts.clear()
            self._seen_alerts.add(alert_id)

        if self.config["notify_pm"]:
            for attempt in range(self.config["repeat_count"]):
                if attempt:
                    await sleep(self.config["repeat_delay"])
                try:
                    await self.inline.bot.send_message(
                        self.me, utils.escape_html(alert_text), parse_mode="HTML"
                    )
                except Exception:
                    logger.warning("Не вдалося надіслати сповіщення в особисті повідомлення", exc_info=True)
                    break

        signature = self.config["nametag"].strip()
        forward_text = alert_text + ("\n\n" + signature if signature else "")
        for chat in self.config["forward_chats"]:
            try:
                await self.client.send_message(self._chat_ref(chat), forward_text, parse_mode=None)
            except Exception:
                logger.warning("Не вдалося переслати тривогу в чат %s", chat, exc_info=True)
