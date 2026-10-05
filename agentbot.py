# meta developer: @Huang_Baike
# meta version: 1.0.0
# meta description: Агент-бот: кожен діалог в особистих стає окремою гілкою у форум-групі.

"""Relay private dialogs into forum topics so a whole team can answer them."""

import asyncio
import collections
import datetime
import logging
import random

from telethon.errors import FloodWaitError, RPCError
from telethon.tl import functions, types

from .. import loader, utils


logger = logging.getLogger(__name__)

MARKER_PREFIX = "#AgentBot"
TELEGRAM_SERVICE_ID = 777000
TOPIC_TITLE_LIMIT = 128
MESSAGE_MAP_LIMIT = 5000
MEDIA_DOWNLOAD_LIMIT = 50 * 1024 * 1024
TOPIC_ERRORS = (
    "TOPIC_DELETED",
    "TOPIC_CLOSED",
    "TOPIC_ID_INVALID",
    "MESSAGE_ID_INVALID",
    "REPLY_MESSAGE_ID_INVALID",
)
GROUP_ERRORS = (
    "CHANNEL_INVALID",
    "CHANNEL_PRIVATE",
    "CHAT_ID_INVALID",
    "PEER_ID_INVALID",
    "COULD NOT FIND THE INPUT ENTITY",
)


@loader.tds
class AgentBotMod(loader.Module):
    """Особисті діалоги як гілки у спільній групі"""

    strings = {
        "name": "AgentBot",
        "cfg_enabled": "Пересилати особисті повідомлення в гілки групи",
        "cfg_group_title": "Назва групи, яку модуль створює автоматично",
        "cfg_ignore_bots": "Не створювати гілки для ботів",
        "cfg_ignore_contacts": "Не створювати гілки для ваших контактів",
        "cfg_send_photo": "Додавати фото профілю до картки користувача",
        "cfg_report_errors": "Повідомляти в гілці, якщо відповідь не доставлено",
        "group_about": (
            "Діалоги AgentBot: кожна гілка — окремий співрозмовник. "
            "Не видаляйте рядок нижче.\n{marker}"
        ),
        "group_welcome": (
            "🤖 <b>Групу AgentBot створено.</b>\n\n"
            "Кожен, хто напише вам в особисті, отримає тут власну гілку. "
            "Будь-яке повідомлення учасника групи в гілці буде надіслано "
            "співрозмовнику від вашого імені.\n\n"
            "Додавайте сюди людей, які мають відповідати на діалоги."
        ),
        "card": (
            "👤 <b>Новий діалог</b>\n\n"
            "<b>Ім'я:</b> {name}\n"
            "<b>ID:</b> <code>{id}</code>\n"
            "<b>Username:</b> {usernames}\n"
            "<b>Телефон:</b> {phone}\n"
            "<b>Профіль:</b> <a href=\"tg://user?id={id}\">відкрити</a>\n"
            "<b>Біо:</b> {bio}\n"
            "<b>Статус:</b> {status}\n"
            "<b>Premium:</b> {premium}\n"
            "<b>Контакт:</b> {contact}\n"
            "<b>Спільних чатів:</b> {common}\n"
            "<b>Позначки:</b> {flags}\n"
            "<b>DC:</b> {dc}\n"
            "<b>Перше повідомлення:</b> {first_seen}"
        ),
        "unknown": "—",
        "yes": "так",
        "no": "ні",
        "deleted": "Видалений акаунт",
        "status_online": "у мережі",
        "status_recently": "був(-ла) нещодавно",
        "status_week": "був(-ла) цього тижня",
        "status_month": "був(-ла) цього місяця",
        "status_offline": "був(-ла) {}",
        "flag_bot": "бот",
        "flag_verified": "верифікований",
        "flag_scam": "scam",
        "flag_fake": "fake",
        "flag_restricted": "обмежений",
        "flag_support": "підтримка Telegram",
        "not_delivered": "⚠️ <b>Не доставлено:</b> <code>{}</code>",
        "media_lost": "[медіа не вдалося скопіювати]",
        "status": (
            "🤖 <b>AgentBot</b>\n\n"
            "Пересилання: {state}\n"
            "Група: {group}\n"
            "Активних гілок: <b>{topics}</b>\n\n"
            "Команди: <code>{p}agentbot on</code>, "
            "<code>{p}agentbot off</code>, "
            "<code>{p}agentbot invite</code>, "
            "<code>{p}agentbot forget</code>, "
            "<code>{p}agentbot new</code>."
        ),
        "state_on": "🟢 увімкнено",
        "state_off": "⚫ вимкнено",
        "no_group": "ще не створено",
        "enabled": "🟢 <b>AgentBot увімкнено.</b>",
        "disabled": (
            "⚫ <b>AgentBot вимкнено.</b> Нові повідомлення не пересилатимуться."
        ),
        "invite": "🔗 <b>Запрошення до групи:</b> {}",
        "invite_failed": "❌ <b>Не вдалося отримати посилання:</b> <code>{}</code>",
        "group_failed": "❌ <b>Не вдалося підготувати групу:</b> <code>{}</code>",
        "new_group": "✅ <b>Створено нову групу:</b> {}",
        "forgot": (
            "✅ <b>Гілку відв'язано від</b> <code>{}</code>. Наступне "
            "повідомлення створить нову гілку."
        ),
        "forget_usage": (
            "Напишіть <code>{p}agentbot forget</code> у гілці користувача або "
            "<code>{p}agentbot forget ID</code>."
        ),
        "not_found": "❌ <b>Гілку для цього користувача не знайдено.</b>",
        "help": (
            "Невідомий параметр. Доступно: <code>on</code>, <code>off</code>, "
            "<code>invite</code>, <code>forget</code>, <code>new</code>."
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
                "group_title",
                "AgentBot · Діалоги",
                lambda: self.strings("cfg_group_title"),
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "ignore_bots",
                True,
                lambda: self.strings("cfg_ignore_bots"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ignore_contacts",
                False,
                lambda: self.strings("cfg_ignore_contacts"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "send_profile_photo",
                True,
                lambda: self.strings("cfg_send_photo"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "report_errors",
                True,
                lambda: self.strings("cfg_report_errors"),
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._me_id = None
        self._group_id = None
        self._group_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._user_locks = {}
        self._own_ids = collections.OrderedDict()
        self._dm_to_group = collections.OrderedDict()
        self._group_to_dm = collections.OrderedDict()

    async def client_ready(self, client, db):
        self._client = client
        self._me_id = getattr(client, "tg_id", None)
        if not self._me_id:
            self._me_id = (await client.get_me()).id
        try:
            await self._ensure_group()
        except FloodWaitError:
            raise
        except Exception:
            logger.exception("AgentBot could not prepare its forum group")

    # ----------------------------------------------------------------- storage

    @property
    def _marker(self):
        return f"{MARKER_PREFIX}:{self._me_id}"

    @property
    def _group_peer(self):
        return int(f"-100{self._group_id}") if self._group_id else None

    def _topics(self):
        raw = self.get("topics", {})
        if not isinstance(raw, dict):
            return {}
        topics = {}
        for key, value in raw.items():
            try:
                topics[str(int(key))] = int(value)
            except (TypeError, ValueError):
                continue
        return topics

    def _save_topics(self, topics):
        self.set("topics", topics)

    def _user_by_topic(self, topic_id):
        for user_id, saved in self._topics().items():
            if saved == topic_id:
                return int(user_id)
        return None

    @staticmethod
    def _remember(mapping, key, value):
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > MESSAGE_MAP_LIMIT:
            mapping.popitem(last=False)

    def _link_messages(self, user_id, dm_id, group_id):
        if not dm_id or not group_id:
            return
        self._remember(self._dm_to_group, (user_id, dm_id), group_id)
        self._remember(self._group_to_dm, group_id, (user_id, dm_id))

    @staticmethod
    def _error_text(error):
        return f"{type(error).__name__} {error}".upper()

    @classmethod
    def _is_topic_error(cls, error):
        text = cls._error_text(error)
        return any(token in text for token in TOPIC_ERRORS)

    @classmethod
    def _is_group_error(cls, error):
        text = cls._error_text(error)
        return any(token in text for token in GROUP_ERRORS)

    # ------------------------------------------------------------------- group

    async def _ensure_group(self, force=False):
        async with self._group_lock:
            if self._group_id and not force:
                return self._group_id

            stored = self.get("group_id")
            if stored and not force and await self._verify_group(int(stored)):
                return self._use_group(int(stored))

            found = await self._find_group(exclude=stored if force else None)
            if found:
                return self._use_group(found)

            return self._use_group(await self._create_group())

    def _use_group(self, group_id):
        if self.get("topics_group") != group_id:
            self._save_topics({})
            self.set("topics_group", group_id)
        self.set("group_id", group_id)
        self._group_id = group_id
        return group_id

    async def _full_channel(self, channel):
        full = await self._client(functions.channels.GetFullChannelRequest(channel))
        chat = next(
            (
                item
                for item in getattr(full, "chats", []) or []
                if getattr(item, "id", None) == getattr(full.full_chat, "id", None)
            ),
            None,
        )
        return full.full_chat, chat

    async def _verify_group(self, group_id):
        try:
            full, chat = await self._full_channel(types.PeerChannel(group_id))
        except FloodWaitError:
            raise
        except (RPCError, ValueError, TypeError) as error:
            logger.info("Stored AgentBot group %s is unavailable: %s", group_id, error)
            return False

        if chat is None or getattr(chat, "left", False):
            return False
        if self._marker not in (getattr(full, "about", "") or ""):
            await self._write_marker(chat)
        if not getattr(chat, "forum", False):
            await self._enable_forum(chat)
        return True

    async def _find_group(self, exclude=None):
        async for dialog in self._client.iter_dialogs():
            entity = getattr(dialog, "entity", None)
            if not getattr(entity, "megagroup", False):
                continue
            if not getattr(entity, "creator", False):
                continue
            if exclude is not None and entity.id == int(exclude):
                continue
            try:
                full, _ = await self._full_channel(entity)
            except FloodWaitError:
                raise
            except RPCError:
                continue
            if self._marker in (getattr(full, "about", "") or ""):
                if not getattr(entity, "forum", False):
                    await self._enable_forum(entity)
                return entity.id
        return None

    async def _create_group(self):
        title = str(self.config["group_title"] or "AgentBot")[:TOPIC_TITLE_LIMIT]
        about = self.strings("group_about").format(marker=self._marker)
        try:
            result = await self._client(
                functions.channels.CreateChannelRequest(
                    title=title, about=about, megagroup=True, forum=True
                )
            )
            chat = result.chats[0]
        except TypeError:
            # Older Telethon layers do not know the forum flag yet.
            result = await self._client(
                functions.channels.CreateChannelRequest(
                    title=title, about=about, megagroup=True
                )
            )
            chat = result.chats[0]
        if not getattr(chat, "forum", False):
            await self._enable_forum(chat)

        self._group_id = chat.id
        await self._send_group(self.strings("group_welcome"))
        return chat.id

    async def _write_marker(self, chat):
        about = self.strings("group_about").format(marker=self._marker)
        try:
            await self._client(
                functions.messages.EditChatAboutRequest(peer=chat, about=about)
            )
        except RPCError as error:
            if "ABOUT_NOT_MODIFIED" not in self._error_text(error):
                logger.warning("Unable to restore AgentBot group marker: %s", error)

    async def _enable_forum(self, chat):
        request = functions.channels.ToggleForumRequest
        try:
            await self._client(request(channel=chat, enabled=True, tabs=False))
        except TypeError:
            await self._client(request(channel=chat, enabled=True))

    # ------------------------------------------------------------------ topics

    def _user_lock(self, user_id):
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = self._user_locks[user_id] = asyncio.Lock()
        return lock

    def _display_name(self, user):
        if getattr(user, "deleted", False):
            return self.strings("deleted")
        parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
        name = " ".join(part for part in parts if part).strip()
        return name or self.strings("deleted")

    def _topic_title(self, user):
        title = f"{user.id} · {self._display_name(user)}"
        return title[:TOPIC_TITLE_LIMIT]

    @staticmethod
    def _topic_from_updates(result):
        updates = getattr(result, "updates", None) or []
        for update in updates:
            message = getattr(update, "message", None)
            action = getattr(message, "action", None)
            if type(action).__name__ == "MessageActionTopicCreate":
                return message.id
        for update in updates:
            if type(update).__name__ == "UpdateMessageID":
                return update.id
        return None

    async def _create_topic(self, title):
        peer = await self._client.get_input_entity(self._group_peer)
        random_id = random.randint(-(2**63), 2**63 - 1)
        request = getattr(functions.channels, "CreateForumTopicRequest", None)
        if request is not None:
            payload = request(channel=peer, title=title, random_id=random_id)
        else:
            payload = functions.messages.CreateForumTopicRequest(
                peer=peer, title=title, random_id=random_id
            )
        topic_id = self._topic_from_updates(await self._client(payload))
        if not topic_id:
            raise RuntimeError("Telegram не повернув ID нової гілки")
        return topic_id

    async def _topic_for(self, user, stale=None):
        key = str(user.id)
        async with self._user_lock(user.id):
            await self._ensure_group()
            topics = self._topics()
            current = topics.get(key)
            if current and current != stale:
                return current

            try:
                topic_id = await self._create_topic(self._topic_title(user))
            except FloodWaitError:
                raise
            except (RPCError, ValueError) as error:
                if not self._is_group_error(error):
                    raise
                await self._ensure_group(force=True)
                topic_id = await self._create_topic(self._topic_title(user))

            topics = self._topics()
            topics[key] = topic_id
            self._save_topics(topics)
            await self._send_card(user, topic_id)
            return topic_id

    # --------------------------------------------------------------- user card

    def _status_text(self, status):
        name = type(status).__name__
        if name == "UserStatusOnline":
            return self.strings("status_online")
        if name == "UserStatusOffline":
            moment = getattr(status, "was_online", None)
            if isinstance(moment, datetime.datetime):
                moment = moment.astimezone().strftime("%Y-%m-%d %H:%M")
                return self.strings("status_offline").format(moment)
        if name == "UserStatusRecently":
            return self.strings("status_recently")
        if name == "UserStatusLastWeek":
            return self.strings("status_week")
        if name == "UserStatusLastMonth":
            return self.strings("status_month")
        return self.strings("unknown")

    def _usernames(self, user):
        names = []
        if getattr(user, "username", None):
            names.append(user.username)
        for item in getattr(user, "usernames", None) or []:
            value = getattr(item, "username", None)
            if value and value not in names:
                names.append(value)
        if not names:
            return self.strings("unknown")
        return ", ".join(f"@{utils.escape_html(name)}" for name in names)

    def _flags(self, user):
        flags = [
            self.strings(key)
            for attribute, key in (
                ("bot", "flag_bot"),
                ("verified", "flag_verified"),
                ("scam", "flag_scam"),
                ("fake", "flag_fake"),
                ("restricted", "flag_restricted"),
                ("support", "flag_support"),
            )
            if getattr(user, attribute, False)
        ]
        return ", ".join(flags) or self.strings("unknown")

    async def _card_text(self, user):
        bio = None
        common = None
        try:
            full = await self._client(functions.users.GetFullUserRequest(user))
            bio = getattr(full.full_user, "about", None)
            common = getattr(full.full_user, "common_chats_count", None)
        except FloodWaitError:
            raise
        except (RPCError, ValueError, TypeError) as error:
            logger.info("AgentBot could not load full user %s: %s", user.id, error)

        yes, no, unknown = self.strings("yes"), self.strings("no"), self.strings("unknown")
        phone = getattr(user, "phone", None)
        photo = getattr(user, "photo", None)
        dc = getattr(photo, "dc_id", None)
        return self.strings("card").format(
            name=utils.escape_html(self._display_name(user)),
            id=user.id,
            usernames=self._usernames(user),
            phone=f"<code>+{utils.escape_html(phone)}</code>" if phone else unknown,
            bio=utils.escape_html(bio) if bio else unknown,
            status=self._status_text(getattr(user, "status", None)),
            premium=yes if getattr(user, "premium", False) else no,
            contact=(
                yes
                if getattr(user, "contact", False)
                or getattr(user, "mutual_contact", False)
                else no
            ),
            common=common if common is not None else unknown,
            flags=self._flags(user),
            dc=dc if dc else unknown,
            first_seen=datetime.datetime.now().astimezone().strftime(
                "%Y-%m-%d %H:%M"
            ),
        )

    async def _send_card(self, user, topic_id):
        text = await self._card_text(user)
        if self.config["send_profile_photo"] and getattr(user, "photo", None):
            try:
                photo = await self._client.download_profile_photo(user, file=bytes)
            except (RPCError, OSError, ValueError, TypeError):
                photo = None
            if photo:
                try:
                    return await self._send_group(text, topic_id, file=photo)
                except FloodWaitError:
                    raise
                except RPCError as error:
                    if self._is_topic_error(error) or self._is_group_error(error):
                        raise
                    logger.info("AgentBot card photo was rejected: %s", error)
        return await self._send_group(text, topic_id)

    # ---------------------------------------------------------------- sending

    def _mark_own(self, sent):
        for item in sent if isinstance(sent, list) else [sent]:
            message_id = getattr(item, "id", None)
            if message_id:
                self._remember(self._own_ids, message_id, True)

    async def _send_group(self, text, topic_id=None, **kwargs):
        async with self._send_lock:
            sent = await self._client.send_message(
                self._group_peer, text, reply_to=topic_id, parse_mode="html", **kwargs
            )
            self._mark_own(sent)
            return sent

    async def _copy(self, target, message, reply_to=None):
        try:
            return await self._client.send_message(target, message, reply_to=reply_to)
        except FloodWaitError:
            raise
        except RPCError as error:
            if not getattr(message, "media", None):
                raise
            if self._is_topic_error(error) or self._is_group_error(error):
                raise
            logger.info("AgentBot re-uploads media that cannot be copied: %s", error)

        text = getattr(message, "message", "") or ""
        entities = getattr(message, "entities", None)
        data = None
        file = getattr(message, "file", None)
        if (getattr(file, "size", 0) or 0) <= MEDIA_DOWNLOAD_LIMIT:
            try:
                data = await message.download_media(file=bytes)
            except (RPCError, OSError, ValueError, TypeError):
                data = None
        if data:
            return await self._client.send_file(
                target,
                data,
                caption=text,
                formatting_entities=entities,
                reply_to=reply_to,
                attributes=getattr(getattr(message, "document", None), "attributes", None),
            )
        lost = self.strings("media_lost")
        return await self._client.send_message(
            target, f"{text}\n\n{lost}" if text else lost, reply_to=reply_to
        )

    async def _copy_to_group(self, message, topic_id, reply_to=None):
        async with self._send_lock:
            sent = await self._copy(self._group_peer, message, reply_to or topic_id)
            self._mark_own(sent)
            return sent

    # ---------------------------------------------------------------- watcher

    async def _accepts_sender(self, message):
        if getattr(message, "out", False) or not getattr(message, "is_private", False):
            return None
        sender_id = getattr(message, "sender_id", None)
        if not sender_id or sender_id in {self._me_id, TELEGRAM_SERVICE_ID}:
            return None
        sender = await message.get_sender()
        if not isinstance(sender, types.User) or getattr(sender, "is_self", False):
            return None
        inline = getattr(self, "inline", None)
        if sender.id == getattr(inline, "bot_id", None):
            return None
        if getattr(sender, "bot", False) and self.config["ignore_bots"]:
            return None
        if getattr(sender, "contact", False) and self.config["ignore_contacts"]:
            return None
        return sender

    async def _handle_private(self, message):
        sender = await self._accepts_sender(message)
        if sender is None:
            return

        topic_id = await self._topic_for(sender)
        reply_to = None
        reply = getattr(message, "reply_to", None)
        if reply and getattr(reply, "reply_to_msg_id", None):
            reply_to = self._dm_to_group.get((sender.id, reply.reply_to_msg_id))

        try:
            sent = await self._copy_to_group(message, topic_id, reply_to)
        except FloodWaitError:
            raise
        except (RPCError, ValueError) as error:
            if self._is_group_error(error):
                await self._ensure_group(force=True)
            elif not self._is_topic_error(error):
                raise
            topic_id = await self._topic_for(sender, stale=topic_id)
            sent = await self._copy_to_group(message, topic_id)
        self._link_messages(sender.id, message.id, getattr(sent, "id", None))

    @staticmethod
    def _topic_of(message):
        reply = getattr(message, "reply_to", None)
        if not reply or not getattr(reply, "forum_topic", False):
            return None, None
        top = getattr(reply, "reply_to_top_id", None)
        if top:
            return top, reply.reply_to_msg_id
        return reply.reply_to_msg_id, None

    async def _handle_group(self, message):
        if getattr(message, "action", None) is not None:
            return
        if getattr(message, "out", False):
            # Wait for an in-flight send so its message ID is already recorded.
            async with self._send_lock:
                pass
            if message.id in self._own_ids:
                return
            text = getattr(message, "raw_text", "") or ""
            if text.startswith(self.get_prefix()):
                return

        topic_id, reply_id = self._topic_of(message)
        if not topic_id:
            return
        user_id = self._user_by_topic(topic_id)
        if user_id is None:
            return

        reply_to = None
        if reply_id:
            linked = self._group_to_dm.get(reply_id)
            if linked and linked[0] == user_id:
                reply_to = linked[1]

        try:
            sent = await self._copy(user_id, message, reply_to)
        except FloodWaitError:
            raise
        except (RPCError, ValueError) as error:
            logger.info("AgentBot could not deliver a reply to %s: %s", user_id, error)
            if self.config["report_errors"]:
                await self._send_group(
                    self.strings("not_delivered").format(
                        utils.escape_html(type(error).__name__)
                    ),
                    message.id,
                )
            return
        self._link_messages(user_id, getattr(sent, "id", None), message.id)

    async def watcher(self, message):
        if not self.config["enabled"] or not self._client:
            return
        if getattr(message, "edit_date", None):
            return
        try:
            if self._group_id and message.chat_id == self._group_peer:
                await self._handle_group(message)
            else:
                await self._handle_private(message)
        except FloodWaitError as error:
            logger.warning("AgentBot flood wait: %s seconds", error.seconds)
        except Exception:
            logger.exception("AgentBot failed to relay a message")

    # --------------------------------------------------------------- commands

    async def _group_link(self):
        if not self._group_id:
            return self.strings("no_group")
        title = utils.escape_html(self.config["group_title"])
        return f'<a href="https://t.me/c/{self._group_id}/1">{title}</a>'

    async def _forget(self, message, args):
        target = None
        if args:
            try:
                target = int(args)
            except ValueError:
                target = None
        elif self._group_id and message.chat_id == self._group_peer:
            topic_id, _ = self._topic_of(message)
            target = self._user_by_topic(topic_id) if topic_id else None
        else:
            return await utils.answer(
                message, self.strings("forget_usage").format(p=self.get_prefix())
            )

        topics = self._topics()
        if target is None or str(target) not in topics:
            return await utils.answer(message, self.strings("not_found"))
        del topics[str(target)]
        self._save_topics(topics)
        await utils.answer(message, self.strings("forgot").format(target))

    async def _new_group(self, message):
        old = self._group_id
        try:
            async with self._group_lock:
                self._use_group(await self._create_group())
        except FloodWaitError:
            raise
        except (RPCError, RuntimeError) as error:
            return await utils.answer(
                message,
                self.strings("group_failed").format(utils.escape_html(str(error))),
            )
        if old:
            try:
                await self._client(
                    functions.messages.EditChatAboutRequest(
                        peer=types.PeerChannel(old), about=""
                    )
                )
            except (RPCError, ValueError):
                logger.info("AgentBot could not clear the old group marker")
        await utils.answer(
            message, self.strings("new_group").format(await self._group_link())
        )

    async def _invite(self, message):
        try:
            await self._ensure_group()
            result = await self._client(
                functions.messages.ExportChatInviteRequest(peer=self._group_peer)
            )
        except FloodWaitError:
            raise
        except (RPCError, ValueError, RuntimeError) as error:
            return await utils.answer(
                message,
                self.strings("invite_failed").format(utils.escape_html(str(error))),
            )
        await utils.answer(
            message, self.strings("invite").format(utils.escape_html(result.link))
        )

    @loader.command(
        ru_doc="[on|off|invite|forget [ID]|new] — керування AgentBot"
    )
    async def agentbot(self, message):
        """[on|off|invite|forget [ID]|new] — керування AgentBot"""
        args = (utils.get_args_raw(message) or "").strip()
        action, _, rest = args.partition(" ")
        action = action.lower()

        if not action:
            try:
                await self._ensure_group()
            except FloodWaitError:
                raise
            except Exception as error:
                logger.warning("AgentBot status could not prepare group: %s", error)
            state = "state_on" if self.config["enabled"] else "state_off"
            return await utils.answer(
                message,
                self.strings("status").format(
                    state=self.strings(state),
                    group=await self._group_link(),
                    topics=len(self._topics()),
                    p=self.get_prefix(),
                ),
            )
        if action == "on":
            self.config["enabled"] = True
            return await utils.answer(message, self.strings("enabled"))
        if action == "off":
            self.config["enabled"] = False
            return await utils.answer(message, self.strings("disabled"))
        if action == "invite":
            return await self._invite(message)
        if action == "forget":
            return await self._forget(message, rest.strip())
        if action == "new":
            return await self._new_group(message)
        await utils.answer(message, self.strings("help"))
