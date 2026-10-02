#    Friendly Telegram (telegram userbot)
# meta version: 1.1.0
#    Copyright (C) 2018-2019 The Authors

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.

#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

from .. import loader, utils
import logging
import telethon

logger = logging.getLogger(__name__)


@loader.tds
class PurgeMod(loader.Module):
    """Deletes your messages"""

    strings = {
        "name": "Purge",
        "from_where": "<b>Which messages should be purged?</b>",
        "not_supergroup_bot": "<b>Purges can only take place in supergroups</b>",
        "delete_what": "<b>What message should be deleted?</b>",
        "delme_usage": "<b>Usage:</b> <code>{}delme 10</code> (1–500)",
    }

    strings_uk = {
        "from_where": "<b>Дайте відповідь на повідомлення, з якого почати очищення.</b>",
        "not_supergroup_bot": "<b>Очищення можливе лише в супергрупах.</b>",
        "delete_what": "<b>Яке повідомлення видалити?</b>",
        "delme_usage": "<b>Використання:</b> <code>{}delme 10</code> (1–500)",
    }

    @loader.group_admin_delete_messages
    @loader.ratelimit
    async def purgecmd(self, message):
        """Purge from the replied message"""
        if not message.is_reply:
            await utils.answer(message, self.strings("from_where", message))
            return

        from_users = set()
        args = utils.get_args(message)
        for arg in args:
            try:
                entity = await message.client.get_entity(arg)
                if isinstance(entity, telethon.tl.types.User):
                    from_users.add(entity.id)
            except ValueError:
                pass

        msgs = []
        from_ids = set()
        if await message.client.is_bot():
            if not message.is_channel:
                await utils.answer(message, self.strings("not_supergroup_bot", message))
                return
            for msg in range(message.reply_to_msg_id, message.id + 1):
                msgs.append(msg)
                if len(msgs) >= 99:
                    logger.debug(msgs)
                    await message.client.delete_messages(message.to_id, msgs)
                    msgs.clear()
        else:
            async for msg in message.client.iter_messages(
                entity=message.to_id, min_id=message.reply_to_msg_id - 1, reverse=True
            ):
                if from_users and msg.sender_id not in from_users:
                    continue
                msgs.append(msg.id)
                from_ids.add(msg.sender_id)
                if len(msgs) >= 99:
                    logger.debug(msgs)
                    await message.client.delete_messages(message.to_id, msgs)
                    msgs.clear()
        if msgs:
            logger.debug(msgs)
            await message.client.delete_messages(message.to_id, msgs)
        await self.allmodules.log("purge", group=message.to_id, affected_uids=from_ids)

    @loader.group_admin_delete_messages
    @loader.ratelimit
    async def delcmd(self, message):
        """Delete the replied message"""
        msgs = [message.id]
        if not message.is_reply:
            if await message.client.is_bot():
                await utils.answer(message, self.strings("delete_what", message))
                return
            msg = None
            async for msg in message.client.iter_messages(
                message.to_id, 1, max_id=message.id
            ):
                break
        else:
            msg = await message.get_reply_message()
        if msg is None:
            await utils.answer(message, self.strings("delete_what", message))
            return
        msgs.append(msg.id)
        logger.debug(msgs)
        await message.client.delete_messages(message.to_id, msgs)
        await self.allmodules.log(
            "delete", group=message.to_id, affected_uids=[msg.sender_id]
        )

    async def delmecmd(self, message):
        """<N> - Delete your last N messages in this chat"""
        args = utils.get_args(message)
        try:
            limit = int(args[0]) if args else 0
        except ValueError:
            limit = 0
        if not 1 <= limit <= 500:
            await utils.answer(
                message,
                self.strings("delme_usage", message).format(
                    utils.escape_html(self.get_prefix())
                ),
            )
            return

        ids = [message.id]
        async for msg in message.client.iter_messages(
            message.to_id, from_user="me", max_id=message.id, limit=limit
        ):
            ids.append(msg.id)
        deleted = len(ids) - 1
        for start in range(0, len(ids), 100):
            await message.client.delete_messages(message.to_id, ids[start:start + 100])
        logger.debug("delme removed %s messages", deleted)
