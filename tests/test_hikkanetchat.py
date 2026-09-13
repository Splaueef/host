"""Tests for the room-based HikkaNet chat module."""

import html
import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value

    def lookup(self, name):
        return getattr(self, "_lookups", {}).get(name)


class _Validator:
    def __init__(self, *args, **kwargs):
        pass


class _ConfigValue:
    def __init__(self, name, default, doc, validator=None):
        self.name = name
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.name, value.default) for value in values)


def _decorator(*args, **kwargs):
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]
    return lambda value: value


def _load_module():
    package = types.ModuleType("chathost")
    package.__path__ = []
    modules = types.ModuleType("chathost.modules")
    modules.__path__ = []
    loader = types.ModuleType("chathost.loader")
    utils = types.ModuleType("chathost.utils")
    loader.Module = _Module
    loader.ModuleConfig = _ModuleConfig
    loader.ConfigValue = _ConfigValue
    loader.tds = _decorator
    loader.command = _decorator
    loader.validators = types.SimpleNamespace(
        String=_Validator,
        Integer=_Validator,
        Boolean=_Validator,
        Series=_Validator,
    )
    utils.get_args_raw = lambda message: message.args

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils
    sys.modules.update(
        {
            "chathost": package,
            "chathost.modules": modules,
            "chathost.loader": loader,
            "chathost.utils": utils,
        }
    )
    spec = importlib.util.spec_from_file_location(
        "chathost.modules.hikkanetchat", ROOT / "hikkanetchat.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hikkanetchat = _load_module()


class _Network:
    def __init__(self):
        self.config = {
            "instance_id": "hikka-one",
            "display_name": "Main Hikka",
        }
        self.published = []
        self.requests = []
        self.responses = []
        self.presence = []
        self.left = []

    def _configured(self):
        return True

    async def api_publish(self, topic, payload, ttl_seconds=0):
        self.published.append((topic, payload, ttl_seconds))
        return {"id": 5}

    async def api_events(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0) if self.responses else {"events": []}

    async def api_chat_presence(self, room, nickname=""):
        self.presence.append((room, nickname))
        return {"room": room}

    async def api_chat_leave(self, room):
        self.left.append(room)
        return {"left": True}

    async def api_chat_rooms(self, **kwargs):
        return {
            "rooms": [
                {"room": "lobby", "online": 2, "messages_24h": 7}
            ]
        }

    async def api_chat_members(self, room, **kwargs):
        return {
            "members": [
                {
                    "instance_id": "hikka-two",
                    "nickname": "Node Two",
                    "display_name": "Second",
                }
            ]
        }


class _Message:
    def __init__(self, args=""):
        self.args = args
        self.answers = []


class _Client:
    def __init__(self):
        self.sent = []

    async def send_message(self, peer, text, **kwargs):
        self.sent.append((peer, text, kwargs))


class HikkaNetChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = hikkanetchat.HikkaNetChatMod()
        self.network = _Network()
        self.module._lookups = {"HikkaNet": self.network}
        self.module._me = types.SimpleNamespace(first_name="Owner", username="owner")
        self.module._client = _Client()

    async def test_send_uses_active_room_and_aggregate_stats(self):
        message = _Message("Привіт із Hikka <b>!")

        await self.module.hksay(message)

        topic, payload, ttl = self.network.published[0]
        self.assertEqual(topic, "chat.lobby")
        self.assertEqual(payload["kind"], "chat.message")
        self.assertEqual(payload["body"], "Привіт із Hikka <b>!")
        self.assertEqual(ttl, 7 * 86400)
        self.assertEqual(self.module.modulehub_stats()["messages_sent"], 1)

    async def test_history_requests_latest_and_escapes_remote_html(self):
        self.network.responses.append(
            {
                "events": [
                    {
                        "id": 9,
                        "created_at": 1700000000,
                        "sender_instance_id": "hikka-two",
                        "payload": {
                            "kind": "chat.message",
                            "nickname": "<Admin>",
                            "body": "<script>alert(1)</script>",
                        },
                    }
                ]
            }
        )
        message = _Message()

        await self.module.hkhistory(message)

        self.assertTrue(self.network.requests[0]["latest"])
        self.assertEqual(self.network.requests[0]["topic"], "chat.lobby")
        rendered = message.answers[0]
        self.assertNotIn("<script>", rendered)
        self.assertIn(html.escape("<script>alert(1)</script>"), rendered)

    async def test_first_poll_primes_cursor_then_notifies_only_new_messages(self):
        self.network.responses.extend(
            [
                {"events": [{"id": 3}]},
                {
                    "events": [
                        {
                            "id": 4,
                            "topic": "chat.lobby",
                            "created_at": 1700000000,
                            "sender_instance_id": "hikka-two",
                            "payload": {
                                "kind": "chat.message",
                                "nickname": "Node Two",
                                "body": "hello",
                            },
                        }
                    ]
                },
            ]
        )

        await self.module._poll_once()
        await self.module._poll_once()

        self.assertEqual(self.network.requests[0]["latest"], True)
        self.assertEqual(self.network.requests[1]["after_id"], 3)
        self.assertNotIn("topic", self.network.requests[1])
        self.assertEqual(len(self.module._client.sent), 1)
        self.assertEqual(self.module.modulehub_stats()["messages_received"], 1)

    async def test_presence_rooms_and_members_use_hikkanet_api(self):
        await self.module._sync_presence(force=True)
        rooms = await self.module._rooms_text()
        members = await self.module._members_text("lobby")

        self.assertEqual(self.network.presence, [("lobby", "Main Hikka")])
        self.assertIn("7 за 24 год", rooms)
        self.assertIn("Node Two", members)
        self.assertIn("hikka-two", members)


if __name__ == "__main__":
    unittest.main()
