__version__ = (1, 1, 1)

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
from telethon.utils import get_display_name

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


class AirAlertMod(loader.Module):
    """🇺🇦 Сповіщення про повітряну тривогу.
    Потрібно підписатися на @air_alert_ua й увімкнути сповіщення від свого бота."""

    strings = {"name": "AirAlert"}

    async def client_ready(self, client, db) -> None:
        self.regions = db.get(self.strings["name"], "regions", [])
        self.nametag = db.get(self.strings["name"], "nametag", "")
        self.forwards = db.get(self.strings["name"], "forwards", [])

        if hasattr(self, "hikka"):
            self.me = self._client.tg_id
            self.bot_id = self.inline.bot_id
            await self.request_join(
                "@air_alert_ua", "Необхідний для роботи AirAlert", assure_joined=True
            )
            return

        self.db = db
        self.client = client
        self.bot_id = (await self.inline.bot.get_me()).id
        self.me = (await client.get_me()).id
        try:
            await client(
                JoinChannelRequest(await self.client.get_entity("t.me/air_alert_ua"))
            )
        except Exception:
            logger.error("Не вдалося приєднатися до t.me/air_alert_ua")
        try:
            channel = await self.client.get_entity("t.me/morisummermods")
            await client(JoinChannelRequest(channel))
        except Exception:
            logger.error("Не вдалося приєднатися до morisummermods")
        try:
            post = (await client.get_messages("@morisummermods", ids=[15]))[0]
            await post.react("❤️")
        except Exception:
            logger.error("Не вдалося поставити реакцію на допис t.me/morisummermods")

    async def alertforwardcmd(self, message: Message) -> None:
        """Пересилання сповіщень в інші чати.
        Щоб додати або видалити чат, передайте команді посилання на нього.
        Щоб переглянути список чатів, викличте команду без аргументів.
        Щоб установити власний підпис, введіть .alertforward set <текст>"""
        text = utils.get_args_raw(message)
        if text[:3] == "set":
            self.nametag = text[4:]
            self.db.set(self.strings["name"], "nametag", self.nametag)
            return await utils.answer(
                message,
                f"🏷 <b>Підпис успішно встановлено: <code>{self.nametag}</code></b>",
            )
        if not text:
            chats = "<b>Чати для пересилання сповіщень:</b>\n"
            for chat in self.forwards:
                chats += f"{get_display_name(await self.client.get_entity(chat))}\n"
            await utils.answer(message, chats)
            return
        try:
            chat = (await self.client.get_entity(text.replace("https://", ""))).id
        except Exception:
            await utils.answer(message, "<b>Чат не знайдено</b>")
            return
        if chat in self.forwards:
            self.forwards.remove(chat)
            self.db.set(self.strings["name"], "forwards", self.forwards)
            await utils.answer(message, "<b>Чат видалено зі списку пересилання</b>")
        else:
            self.forwards.append(chat)
            self.db.set(self.strings["name"], "forwards", self.forwards)
            await utils.answer(
                message, "<b>Чат додано до списку пересилання</b>"
            )

    async def alert_inline_handler(self, query: GeekInlineQuery) -> None:
        """Вибір регіонів.
        Щоб отримувати всі сповіщення, введіть alert all.
        Щоб переглянути вибрані регіони, введіть alert my."""
        text = query.args
        if not text:
            result = ua
        elif text == "my":
            result = self.regions
        else:
            result = [region for region in ua if text.lower() in region.lower()]
        if not result:
            await query.e404()
            return
        res = [
            InlineQueryResultArticle(
                id=rand(20),
                title=(
                    f"{'✅' if reg in self.regions else '❌'}{reg if reg != 'all' else 'Усі сповіщення'}"
                ),
                description=(
                    f"Натисніть, щоб {'видалити' if reg in self.regions else 'додати'}"
                    if reg != "all"
                    else (
                        "🇺🇦 Натисніть, щоб"
                        f" {'вимкнути' if 'all' in self.regions else 'увімкнути'} всі"
                        " сповіщення"
                    )
                ),
                input_message_content=InputTextMessageContent(
                    f"{REGION_EDIT_PREFIX}<code>{reg}</code>",
                    parse_mode="HTML",
                ),
            )
            for reg in result[:50]
        ]
        await query.answer(res, cache_time=0)

    async def watcher(self, message: Message) -> None:
        if (
            getattr(message, "out", False)
            and getattr(message, "via_bot_id", False)
            and message.via_bot_id == self.bot_id
            and getattr(message, "raw_text", "").startswith(REGION_EDIT_PREFIX)
        ):
            self.regions = self.db.get(self.strings["name"], "regions", [])
            region = message.raw_text[len(REGION_EDIT_PREFIX):]
            state = "додано"
            if region not in self.regions:
                self.regions.append(region)
            else:
                self.regions.remove(region)
                state = "видалено"
            self.db.set(self.strings["name"], "regions", self.regions)
            try:
                e = await self.client.get_entity("t.me/air_alert_ua")
                sub = not e.left
            except Exception:
                sub = False
            n = "\n"
            res = f"<b>Регіон <code>{region}</code> {state}</b>{n}"
            if not sub:
                res += (
                    "<b>Не відписуйтеся від @air_alert_ua, інакше сповіщення не надходитимуть.</b>"
                )
                if not hasattr(self, "hikka"):
                    await self.client(
                        JoinChannelRequest(
                            await self.client.get_entity("t.me/air_alert_ua")
                        )
                    )
            await self.inline.form(res, message=message)
        if (
            getattr(message, "peer_id", False)
            and getattr(message.peer_id, "channel_id", 0) == 1766138888
            and (
                "all" in self.regions
                or any(reg in message.raw_text for reg in self.regions)
            )
        ):
            for _ in range(3):
                await self.inline.bot.send_message(
                    self.me,
                    message.text,
                    parse_mode="HTML",
                )
                await sleep(1)
            for chat in self.forwards:
                await self.client.send_message(
                    chat,
                    message.text + "\n\n" + self.nametag,
                )
