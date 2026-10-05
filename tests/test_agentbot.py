"""Tests for the AgentBot Hikka module."""

import asyncio
import datetime  # Load stdlib math before the repository's math.py can shadow it.
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


class _Config(dict):
    def __init__(self, *values):
        super().__init__(values)


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value

    def get_prefix(self):
        return "."


class _Validators:
    Boolean = staticmethod(lambda: object())
    String = staticmethod(lambda: object())


class _RPCError(Exception):
    pass


class _FloodWaitError(_RPCError):
    def __init__(self, seconds):
        super().__init__(seconds)
        self.seconds = seconds


class _TopicDeletedError(_RPCError):
    def __str__(self):
        return "TOPIC_DELETED"


class _Request:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _request_class(name):
    return type(name, (_Request,), {})


class _PeerChannel:
    def __init__(self, channel_id):
        self.channel_id = channel_id


class _User:
    def __init__(self, user_id, first_name="Ann", **kwargs):
        self.id = user_id
        self.first_name = first_name
        self.last_name = kwargs.get("last_name")
        self.username = kwargs.get("username")
        self.usernames = []
        self.phone = kwargs.get("phone")
        self.bot = kwargs.get("bot", False)
        self.contact = kwargs.get("contact", False)
        self.mutual_contact = False
        self.premium = kwargs.get("premium", False)
        self.deleted = False
        self.photo = None
        self.status = None
        self.is_self = False


class MessageActionTopicCreate:
    pass


class UpdateNewChannelMessage:
    def __init__(self, message):
        self.message = message


def _load_module():
    package = types.ModuleType("agenthost")
    package.__path__ = []
    modules = types.ModuleType("agenthost.modules")
    modules.__path__ = []
    loader = types.ModuleType("agenthost.loader")
    utils = types.ModuleType("agenthost.utils")
    loader.Module = _Module
    loader.validators = _Validators

    def tds(cls):
        strings = cls.strings
        cls.strings = lambda self, key, message=None: strings[key]
        return cls

    loader.tds = tds
    loader.command = lambda *args, **kwargs: lambda function: function
    loader.ConfigValue = lambda name, default, *args, **kwargs: (name, default)
    loader.ModuleConfig = _Config
    utils.escape_html = lambda value: str(value)
    utils.answer = mock.AsyncMock()
    utils.get_args_raw = lambda message: getattr(message, "args", "")
    package.loader = loader
    package.utils = utils

    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")
    tl = types.ModuleType("telethon.tl")
    errors.FloodWaitError = _FloodWaitError
    errors.RPCError = _RPCError

    functions = types.SimpleNamespace(
        channels=types.SimpleNamespace(
            CreateChannelRequest=_request_class("CreateChannelRequest"),
            GetFullChannelRequest=_request_class("GetFullChannelRequest"),
            ToggleForumRequest=_request_class("ToggleForumRequest"),
            CreateForumTopicRequest=_request_class("CreateForumTopicRequest"),
        ),
        messages=types.SimpleNamespace(
            EditChatAboutRequest=_request_class("EditChatAboutRequest"),
            ExportChatInviteRequest=_request_class("ExportChatInviteRequest"),
        ),
        users=types.SimpleNamespace(
            GetFullUserRequest=_request_class("GetFullUserRequest"),
        ),
    )
    tl.functions = functions
    tl.types = types.SimpleNamespace(PeerChannel=_PeerChannel, User=_User)

    sys.modules.update(
        {
            "agenthost": package,
            "agenthost.modules": modules,
            "agenthost.loader": loader,
            "agenthost.utils": utils,
            "telethon": telethon,
            "telethon.errors": errors,
            "telethon.tl": tl,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "agentbot.py"
    spec = importlib.util.spec_from_file_location("agenthost.modules.agentbot", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agentbot = _load_module()

ME = 1000
GROUP = 555
GROUP_PEER = int(f"-100{GROUP}")


class _Client:
    def __init__(self, groups=None, dialogs=()):
        self.tg_id = ME
        self.groups = dict(groups or {})
        self.dialogs = list(dialogs)
        self.requests = []
        self.sent = []
        self.next_topic = 100
        self.next_message = 1
        self.fail_topic_once = None

    async def __call__(self, request):
        name = type(request).__name__
        self.requests.append(request)
        kwargs = request.kwargs
        if name == "GetFullChannelRequest":
            target = request.args[0]
            group_id = getattr(target, "channel_id", getattr(target, "id", None))
            if group_id not in self.groups:
                raise _RPCError("CHANNEL_INVALID")
            chat = types.SimpleNamespace(
                id=group_id, left=False, forum=True, megagroup=True, creator=True
            )
            return types.SimpleNamespace(
                full_chat=types.SimpleNamespace(
                    id=group_id, about=self.groups[group_id]
                ),
                chats=[chat],
            )
        if name == "CreateChannelRequest":
            self.groups[GROUP] = kwargs["about"]
            chat = types.SimpleNamespace(id=GROUP, forum=True)
            return types.SimpleNamespace(chats=[chat])
        if name == "CreateForumTopicRequest":
            self.next_topic += 1
            message = types.SimpleNamespace(
                id=self.next_topic, action=MessageActionTopicCreate()
            )
            return types.SimpleNamespace(updates=[UpdateNewChannelMessage(message)])
        if name == "GetFullUserRequest":
            return types.SimpleNamespace(
                full_user=types.SimpleNamespace(about="hello", common_chats_count=2)
            )
        if name == "EditChatAboutRequest":
            return True
        raise AssertionError(name)

    async def get_me(self):
        return types.SimpleNamespace(id=ME)

    async def get_input_entity(self, peer):
        return peer

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def send_message(self, target, message, reply_to=None, **kwargs):
        if self.fail_topic_once is not None and reply_to == self.fail_topic_once:
            self.fail_topic_once = None
            raise _TopicDeletedError()
        self.next_message += 1
        self.sent.append((target, message, reply_to))
        return types.SimpleNamespace(id=self.next_message)


def _private(user, text="hi", message_id=1):
    message = types.SimpleNamespace(
        id=message_id,
        out=False,
        is_private=True,
        sender_id=user.id,
        chat_id=user.id,
        reply_to=None,
        edit_date=None,
        media=None,
        raw_text=text,
        message=text,
    )
    message.get_sender = mock.AsyncMock(return_value=user)
    return message


def _group(topic_id, text="answer", out=False, message_id=900):
    return types.SimpleNamespace(
        id=message_id,
        out=out,
        is_private=False,
        chat_id=GROUP_PEER,
        reply_to=types.SimpleNamespace(
            forum_topic=True, reply_to_top_id=None, reply_to_msg_id=topic_id
        ),
        edit_date=None,
        action=None,
        media=None,
        raw_text=text,
        message=text,
    )


class AgentBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        agentbot.utils.answer.reset_mock()
        self.module = agentbot.AgentBotMod()

    def _named(self, client, name):
        return [r for r in client.requests if type(r).__name__ == name]

    async def test_first_load_creates_marked_forum_group(self):
        client = _Client()

        await self.module.client_ready(client, None)

        created = self._named(client, "CreateChannelRequest")
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].kwargs["forum"])
        self.assertTrue(created[0].kwargs["megagroup"])
        self.assertIn(f"#AgentBot:{ME}", created[0].kwargs["about"])
        self.assertEqual(self.module.get("group_id"), GROUP)

    async def test_restart_reuses_stored_group_by_marker(self):
        self.module.set("group_id", GROUP)
        self.module.set("topics_group", GROUP)
        self.module.set("topics", {"42": 7})
        client = _Client({GROUP: f"text\n#AgentBot:{ME}"})

        await self.module.client_ready(client, None)

        self.assertFalse(self._named(client, "CreateChannelRequest"))
        self.assertEqual(self.module._topics(), {"42": 7})

    async def test_lost_storage_finds_group_by_description(self):
        entity = types.SimpleNamespace(
            id=GROUP, megagroup=True, creator=True, forum=True
        )
        client = _Client(
            {GROUP: f"#AgentBot:{ME}"},
            dialogs=[types.SimpleNamespace(entity=entity)],
        )

        await self.module.client_ready(client, None)

        self.assertFalse(self._named(client, "CreateChannelRequest"))
        self.assertEqual(self.module.get("group_id"), GROUP)

    async def test_new_user_gets_topic_with_card_then_message(self):
        client = _Client()
        await self.module.client_ready(client, None)
        user = _User(42, "Ann", username="ann", phone="380001112233")

        await self.module.watcher(_private(user, "Привіт"))

        topic = self._named(client, "CreateForumTopicRequest")[0]
        self.assertEqual(topic.kwargs["title"], "42 · Ann")
        topic_id = self.module._topics()["42"]
        card = client.sent[-2]
        self.assertEqual(card[2], topic_id)
        self.assertIn("<code>42</code>", card[1])
        self.assertIn("@ann", card[1])
        self.assertIn("+380001112233", card[1])
        self.assertIn("hello", card[1])
        self.assertEqual(client.sent[-1][2], topic_id)
        self.assertEqual(client.sent[-1][1].raw_text, "Привіт")

        await self.module.watcher(_private(user, "Ще", message_id=2))
        self.assertEqual(len(self._named(client, "CreateForumTopicRequest")), 1)

    async def test_group_reply_is_sent_to_user_without_echo_loop(self):
        client = _Client()
        await self.module.client_ready(client, None)
        user = _User(42)
        await self.module.watcher(_private(user))
        topic_id = self.module._topics()["42"]
        sent_before = len(client.sent)

        # A message the module itself posted into the topic must be ignored.
        own = _group(topic_id, out=True, message_id=client.next_message)
        await self.module.watcher(own)
        self.assertEqual(len(client.sent), sent_before)

        # Any group member writing into the topic reaches the user.
        reply = _group(topic_id, "Відповідь", message_id=901)
        await self.module.watcher(reply)
        self.assertEqual(client.sent[-1][0], 42)
        self.assertIs(client.sent[-1][1], reply)

    async def test_owner_commands_in_topic_are_not_relayed(self):
        client = _Client()
        await self.module.client_ready(client, None)
        await self.module.watcher(_private(_User(42)))
        topic_id = self.module._topics()["42"]
        count = len(client.sent)

        await self.module.watcher(_group(topic_id, ".agentbot", out=True))

        self.assertEqual(len(client.sent), count)

    async def test_service_account_and_bots_are_ignored(self):
        client = _Client()
        await self.module.client_ready(client, None)

        await self.module.watcher(_private(_User(777000, "Telegram")))
        await self.module.watcher(_private(_User(50, "Bot", bot=True)))

        self.assertFalse(self._named(client, "CreateForumTopicRequest"))

    async def test_deleted_topic_is_recreated(self):
        client = _Client()
        await self.module.client_ready(client, None)
        user = _User(42)
        await self.module.watcher(_private(user))
        old_topic = self.module._topics()["42"]

        client.fail_topic_once = old_topic
        await self.module.watcher(_private(user, "again", message_id=3))

        new_topic = self.module._topics()["42"]
        self.assertNotEqual(old_topic, new_topic)
        self.assertEqual(client.sent[-1][2], new_topic)
        self.assertEqual(client.sent[-1][1].raw_text, "again")

    async def test_concurrent_first_messages_create_one_topic(self):
        client = _Client()
        await self.module.client_ready(client, None)
        user = _User(42)

        await asyncio.gather(
            self.module.watcher(_private(user, "a", 1)),
            self.module.watcher(_private(user, "b", 2)),
        )

        self.assertEqual(len(self._named(client, "CreateForumTopicRequest")), 1)

    async def test_forget_inside_topic_unlinks_user(self):
        client = _Client()
        await self.module.client_ready(client, None)
        await self.module.watcher(_private(_User(42)))
        topic_id = self.module._topics()["42"]
        command = _group(topic_id, ".agentbot forget", out=True)
        command.args = "forget"

        await self.module.agentbot(command)

        self.assertEqual(self.module._topics(), {})
        self.assertIn("42", agentbot.utils.answer.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
