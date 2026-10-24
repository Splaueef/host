import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from hikka_hub.app import (
    CATALOG_IMPORT_STATE_KEY,
    MODULE_REPOSITORY,
    _import_official_modules,
    MODULE_STATE_KEY,
    MODULE_UPDATE_TOPIC,
    _process_module_manifest,
    create_app,
)
from hikka_hub.config import Settings
from hikka_hub.security import derive_hmac_key, sign_with_secret
from hikka_hub.storage import Store


class HikkaHubApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "hub.sqlite3"
        self.settings = Settings(
            bind="127.0.0.1",
            port=8765,
            database=self.database,
            max_clock_skew=90,
            rate_limit_per_minute=1000,
            request_body_limit=131072,
            event_retention_hours=168,
            offline_after_seconds=120,
            module_updates_enabled=False,
            module_update_interval=300,
        )
        self.store = Store(self.database)
        self.credentials = {
            "one": {
                "key_id": "hk_testkey000001",
                "instance_id": "hikka-one",
                "owner_id": 1001,
                "secret": "a" * 48,
                "display_name": "One",
            },
            "two": {
                "key_id": "hk_testkey000002",
                "instance_id": "hikka-two",
                "owner_id": 1002,
                "secret": "b" * 48,
                "display_name": "Two",
            },
        }
        now = int(time.time())
        for credential in self.credentials.values():
            self.store._db.execute(
                """
                INSERT INTO instances(key_id, instance_id, owner_id, display_name,
                                      derived_key_hex, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    credential["key_id"],
                    credential["instance_id"],
                    credential["owner_id"],
                    credential["display_name"],
                    derive_hmac_key(credential["secret"]).hex(),
                    now,
                    now,
                ),
            )
        self.client = TestClient(TestServer(create_app(self.settings, self.store)))
        await self.client.start_server()
        self.nonce_counter = 0

    async def asyncTearDown(self):
        await self.client.close()
        self.tempdir.cleanup()

    def _signed(self, method, target, payload=None, who="one", **overrides):
        credential = dict(self.credentials[who])
        credential.update(overrides)
        body = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if payload is not None
            else b""
        )
        self.nonce_counter += 1
        timestamp = str(int(time.time()))
        nonce = f"testnonce{self.nonce_counter:016d}"
        content_hash = hashlib.sha256(body).hexdigest()
        signature = sign_with_secret(
            credential["secret"],
            method,
            target,
            timestamp,
            nonce,
            credential["instance_id"],
            str(credential["owner_id"]),
            content_hash,
        )
        headers = {
            "Authorization": f"Hikka-HMAC {credential['key_id']}:{signature}",
            "X-Hikka-Timestamp": timestamp,
            "X-Hikka-Nonce": nonce,
            "X-Hikka-Instance": credential["instance_id"],
            "X-Hikka-Owner": str(credential["owner_id"]),
            "X-Hikka-Content-SHA256": content_hash,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return body, headers

    async def _request(self, method, target, payload=None, who="one", **overrides):
        body, headers = self._signed(method, target, payload, who, **overrides)
        return await self.client.request(method, target, data=body or None, headers=headers)

    async def test_health_is_public_but_api_requires_auth(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"]["status"], "ok")
        health = (await response.json())["data"]
        self.assertEqual(health["api_version"], 2)
        self.assertIn("network_games", health["capabilities"])

        response = await self.client.get("/v1/me")
        self.assertEqual(response.status, 401)
        self.assertEqual((await response.json())["error"]["code"], "authentication_failed")

    async def test_valid_auth_is_bound_to_owner_and_replay_protected(self):
        body, headers = self._signed("GET", "/v1/me")
        response = await self.client.get("/v1/me", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"]["instance_id"], "hikka-one")

        replay = await self.client.get("/v1/me", headers=headers)
        self.assertEqual(replay.status, 409)
        self.assertEqual((await replay.json())["error"]["code"], "replayed_request")

        wrong_owner = await self._request("GET", "/v1/me", owner_id=9999)
        self.assertEqual(wrong_owner.status, 401)

        wrong_secret = await self._request("GET", "/v1/me", secret="x" * 48)
        self.assertEqual(wrong_secret.status, 401)

    async def test_heartbeat_and_instance_list(self):
        response = await self._request(
            "POST",
            "/v1/heartbeat",
            {
                "display_name": "Primary",
                "hikka_version": "1.6.3",
                "module_version": "1.0.0",
                "capabilities": ["events", "kv"],
            },
        )
        self.assertEqual(response.status, 200)

        response = await self._request("GET", "/v1/instances")
        payload = (await response.json())["data"]
        first = next(x for x in payload["instances"] if x["instance_id"] == "hikka-one")
        self.assertTrue(first["online"])
        self.assertEqual(first["display_name"], "Primary")
        self.assertNotIn("owner_id", first)
        self.assertNotIn("derived_key_hex", first)

    async def test_events_are_exchanged_and_cursor_advances(self):
        response = await self._request(
            "POST",
            "/v1/events",
            {"topic": "deploy", "payload": {"version": 7}, "ttl_seconds": 600},
        )
        self.assertEqual(response.status, 201)
        event_id = (await response.json())["data"]["id"]

        target = "/v1/events?after_id=0&limit=50&topic=deploy"
        response = await self._request("GET", target, who="two")
        data = (await response.json())["data"]
        self.assertEqual(data["events"][0]["payload"], {"version": 7})
        self.assertEqual(data["next_after_id"], event_id)

        target = f"/v1/events?after_id={event_id}&limit=50&topic=deploy"
        response = await self._request("GET", target, who="two")
        self.assertEqual((await response.json())["data"]["events"], [])

    async def test_clients_cannot_publish_reserved_system_events(self):
        response = await self._request(
            "POST",
            "/v1/events",
            {
                "topic": MODULE_UPDATE_TOPIC,
                "payload": {"filename": "minigames.py"},
                "ttl_seconds": 600,
            },
        )

        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["error"]["code"], "reserved_topic")

    async def test_module_version_endpoint_uses_server_cache(self):
        response = await self._request("GET", "/v1/modules/versions")
        self.assertEqual(response.status, 503)

        await self.store.set_service_state(
            MODULE_STATE_KEY,
            {
                "schema": 1,
                "repository": MODULE_REPOSITORY,
                "modules": {"minigames.py": "1.4.0"},
            },
        )
        response = await self._request("GET", "/v1/modules/versions", who="two")
        data = (await response.json())["data"]

        self.assertEqual(response.status, 200)
        self.assertEqual(data["repository"], MODULE_REPOSITORY)
        self.assertEqual(data["modules"], {"minigames.py": "1.4.0"})
        self.assertIsInstance(data["updated_at"], int)

    async def test_manifest_change_emits_one_internal_update_event(self):
        app = self.client.server.app

        first = await _process_module_manifest(
            app, {"minigames.py": "1.4.0"}, detected_at=100
        )
        unchanged = await _process_module_manifest(
            app, {"minigames.py": "1.4.0"}, detected_at=101
        )
        changed = await _process_module_manifest(
            app, {"minigames.py": "1.5.0"}, detected_at=102
        )
        events = await self.store.list_events(0, MODULE_UPDATE_TOPIC, 10)

        self.assertEqual(first, [])
        self.assertEqual(unchanged, [])
        self.assertEqual(changed[0]["filename"], "minigames.py")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sender_instance_id"], "hikka-hub")
        self.assertEqual(events[0]["payload"]["detected_at"], 102)
        self.assertEqual(
            events[0]["payload"]["changed"][0],
            {
                "filename": "minigames.py",
                "previous_version": "1.4.0",
                "version": "1.5.0",
            },
        )

    async def test_latest_events_returns_tail_in_chronological_order(self):
        ids = []
        for number in range(4):
            response = await self._request(
                "POST",
                "/v1/events",
                {
                    "topic": "chat.lobby",
                    "payload": {"number": number},
                    "ttl_seconds": 600,
                },
            )
            ids.append((await response.json())["data"]["id"])

        target = "/v1/events?after_id=0&latest=1&limit=2&topic=chat.lobby"
        response = await self._request("GET", target, who="two")
        data = (await response.json())["data"]

        self.assertEqual([item["id"] for item in data["events"]], ids[-2:])
        self.assertEqual(
            [item["payload"]["number"] for item in data["events"]], [2, 3]
        )

    async def test_kv_is_shared_but_only_creator_can_modify(self):
        path = "/v1/kv/config/release"
        response = await self._request("PUT", path, {"value": {"stable": 3}})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"]["revision"], 1)

        response = await self._request("GET", path, who="two")
        self.assertEqual((await response.json())["data"]["value"], {"stable": 3})

        response = await self._request("PUT", path, {"value": 4}, who="two")
        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["error"]["code"], "not_item_owner")

        response = await self._request(
            "PUT", path, {"value": {"stable": 4}, "if_revision": 1}
        )
        self.assertEqual((await response.json())["data"]["revision"], 2)

        response = await self._request(
            "PUT", path, {"value": {"stable": 5}, "if_revision": 1}
        )
        self.assertEqual(response.status, 409)

    async def test_metrics_and_overview(self):
        path = "/v1/metrics/jobs.completed/increment"
        response = await self._request("POST", path, {"delta": 3})
        self.assertEqual((await response.json())["data"]["value"], 3)
        response = await self._request("POST", path, {"delta": 2})
        self.assertEqual((await response.json())["data"]["value"], 5)

        response = await self._request("GET", "/v1/stats/jobs.completed?limit=20", who="two")
        stats = (await response.json())["data"]
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["ranking"][0]["instance_id"], "hikka-one")

        response = await self._request("GET", "/v1/stats")
        overview = (await response.json())["data"]
        self.assertGreaterEqual(overview["today"]["requests"], 1)
        self.assertEqual(overview["instances"]["total"], 2)

    async def test_metric_batch_is_atomic_and_validated(self):
        response = await self._request(
            "POST",
            "/v1/metrics/batch",
            {"metrics": {"module.games.commands": 2, "module.games.finished": 3}},
        )
        self.assertEqual(response.status, 200)
        values = (await response.json())["data"]["metrics"]
        self.assertEqual(values["module.games.commands"]["value"], 2)
        self.assertEqual(values["module.games.finished"]["value"], 3)

        response = await self._request(
            "POST",
            "/v1/metrics/batch",
            {"metrics": {"module.games.commands": 1, "Bad metric": 2}},
        )
        self.assertEqual(response.status, 400)
        response = await self._request(
            "GET", "/v1/stats/module.games.commands", who="two"
        )
        self.assertEqual((await response.json())["data"]["total"], 2)

    async def test_network_game_matchmaking_moves_and_global_stats(self):
        response = await self._request(
            "POST", "/v1/games", {"kind": "ttt"}, who="one"
        )
        self.assertEqual(response.status, 201)
        game = (await response.json())["data"]
        game_id = game["game_id"]
        self.assertEqual(game["status"], "waiting")
        self.assertEqual(game["my_slot"], 0)

        response = await self._request(
            "GET", "/v1/games?scope=open&kind=ttt", who="two"
        )
        open_games = (await response.json())["data"]["games"]
        self.assertEqual(open_games[0]["game_id"], game_id)

        response = await self._request(
            "POST", f"/v1/games/{game_id}/join", {}, who="two"
        )
        game = (await response.json())["data"]
        self.assertEqual(game["status"], "active")
        self.assertEqual(game["my_slot"], 1)
        revision = game["revision"]

        response = await self._request(
            "POST",
            f"/v1/games/{game_id}/move",
            {"if_revision": revision, "action": {"type": "place", "position": 1}},
            who="two",
        )
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["error"]["code"], "not_your_turn")

        for who, position in (
            ("one", 0),
            ("two", 3),
            ("one", 1),
            ("two", 4),
            ("one", 2),
        ):
            current = await self._request(
                "GET", f"/v1/games/{game_id}", who=who
            )
            revision = (await current.json())["data"]["revision"]
            moved = await self._request(
                "POST",
                f"/v1/games/{game_id}/move",
                {
                    "if_revision": revision,
                    "action": {"type": "place", "position": position},
                },
                who=who,
            )
            self.assertEqual(moved.status, 200)
            game = (await moved.json())["data"]

        self.assertEqual(game["status"], "finished")
        self.assertEqual(game["state"]["winner"], 0)
        self.assertEqual(game["state"]["board"][:5], [0, 0, 0, 1, 1])

        response = await self._request(
            "GET", "/v1/games/leaderboard?kind=ttt&sort=rating", who="two"
        )
        ranking = (await response.json())["data"]["ranking"]
        self.assertEqual([row["display_name"] for row in ranking], ["One", "Two"])
        self.assertEqual(ranking[0]["wins"], 1)
        self.assertGreater(ranking[0]["rating"], ranking[1]["rating"])

        response = await self._request("GET", "/v1/games/profile", who="one")
        profile = (await response.json())["data"]
        self.assertEqual(profile["totals"]["played"], 1)
        self.assertEqual(profile["totals"]["wins"], 1)
        self.assertTrue(profile["public_id"].startswith("p_"))

    async def test_private_game_invite_is_visible_only_to_invited_node(self):
        response = await self._request(
            "POST",
            "/v1/games",
            {"kind": "chess", "opponent_instance_id": "hikka-two"},
        )
        game = (await response.json())["data"]
        self.assertEqual(game["status"], "invited")

        response = await self._request(
            "GET", f"/v1/games/{game['game_id']}", who="two"
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"]["my_slot"], 1)

        response = await self._request(
            "GET", "/v1/games?scope=open", who="two"
        )
        self.assertNotIn(
            game["game_id"],
            [item["game_id"] for item in (await response.json())["data"]["games"]],
        )

    async def test_game_revision_and_result_are_recorded_once(self):
        response = await self._request(
            "POST", "/v1/games", {"kind": "ttt"}, who="one"
        )
        game_id = (await response.json())["data"]["game_id"]
        response = await self._request(
            "POST", f"/v1/games/{game_id}/join", {}, who="two"
        )
        revision = (await response.json())["data"]["revision"]

        response = await self._request(
            "POST",
            f"/v1/games/{game_id}/move",
            {
                "if_revision": revision,
                "action": {"type": "place", "position": 0},
            },
            who="one",
        )
        self.assertEqual(response.status, 200)

        stale = await self._request(
            "POST",
            f"/v1/games/{game_id}/move",
            {
                "if_revision": revision,
                "action": {"type": "place", "position": 1},
            },
            who="two",
        )
        self.assertEqual(stale.status, 409)
        self.assertEqual((await stale.json())["error"]["code"], "revision_conflict")

        resigned = await self._request(
            "POST", f"/v1/games/{game_id}/resign", {}, who="two"
        )
        self.assertEqual(resigned.status, 200)
        self.assertEqual((await resigned.json())["data"]["state"]["winner"], 0)

        repeated = await self._request(
            "POST", f"/v1/games/{game_id}/resign", {}, who="two"
        )
        self.assertEqual(repeated.status, 409)
        self.assertEqual((await repeated.json())["error"]["code"], "game_unavailable")

        for who, wins in (("one", 1), ("two", 0)):
            profile_response = await self._request(
                "GET", "/v1/games/profile", who=who
            )
            totals = (await profile_response.json())["data"]["totals"]
            self.assertEqual(totals["played"], 1)
            self.assertEqual(totals["wins"], wins)

    async def test_chat_presence_exposes_rooms_and_online_members(self):
        response = await self._request(
            "POST",
            "/v1/chat/presence",
            {"room": "lobby", "nickname": "Player One"},
            who="one",
        )
        self.assertEqual(response.status, 200)
        await self._request(
            "POST",
            "/v1/chat/presence",
            {"room": "lobby", "nickname": "Player Two"},
            who="two",
        )
        await self._request(
            "POST",
            "/v1/events",
            {
                "topic": "chat.lobby",
                "payload": {"kind": "chat.message", "body": "hello"},
                "ttl_seconds": 600,
            },
            who="one",
        )

        response = await self._request("GET", "/v1/chat/rooms", who="one")
        rooms = (await response.json())["data"]["rooms"]
        self.assertEqual(rooms[0]["room"], "lobby")
        self.assertEqual(rooms[0]["online"], 2)
        self.assertEqual(rooms[0]["messages_24h"], 1)

        response = await self._request(
            "GET", "/v1/chat/rooms/lobby/members", who="two"
        )
        members = (await response.json())["data"]["members"]
        self.assertEqual({item["nickname"] for item in members}, {"Player One", "Player Two"})

        response = await self._request(
            "DELETE", "/v1/chat/presence/lobby", who="one"
        )
        self.assertTrue((await response.json())["data"]["left"])


    MODULE_SOURCE = (
        "# meta developer: @tester\n"
        "# meta description: Says hello\n"
        "from .. import loader, utils\n"
        "__version__ = (1, 0, 0)\n\n"
        "@loader.tds\n"
        "class HelloWorldMod(loader.Module):\n"
        "    \"\"\"Docstring\"\"\"\n"
        "    strings = {\"name\": \"HelloWorld\"}\n\n"
        "    @loader.command()\n"
        "    async def hello(self, message):\n"
        "        await utils.answer(message, \"hi\")\n\n"
        "    async def byecmd(self, message):\n"
        "        pass\n"
    )

    async def _publish(self, source, who="one", **extra):
        return await self._request(
            "POST", "/v1/catalog/modules", {"source": source, **extra}, who=who
        )

    async def test_catalog_publish_list_install_and_rate(self):
        response = await self._publish(
            self.MODULE_SOURCE, tags=["fun", "#Text"], changelog="First"
        )
        self.assertEqual(response.status, 201, await response.text())
        module = (await response.json())["data"]
        self.assertEqual(module["module_id"], "helloworld")
        self.assertEqual(module["title"], "HelloWorld")
        self.assertEqual(module["latest_version"], "1.0.0")
        self.assertEqual(module["developer"], "@tester")
        self.assertEqual(module["description"], "Says hello")
        self.assertEqual(module["tags"], ["fun", "text"])
        self.assertEqual(module["commands"], ["hello", "bye"])
        self.assertTrue(module["created"])
        self.assertNotIn("owner_id", module)

        response = await self._request(
            "GET", "/v1/catalog/modules?q=hello&sort=new", who="two"
        )
        listing = (await response.json())["data"]
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["modules"][0]["owner_display_name"], "One")

        response = await self._request(
            "GET", "/v1/catalog/modules?tag=fun", who="two"
        )
        self.assertEqual((await response.json())["data"]["total"], 1)
        response = await self._request("GET", "/v1/catalog/tags", who="two")
        self.assertEqual(
            (await response.json())["data"]["tags"][0], {"tag": "fun", "modules": 1}
        )

        response = await self._request(
            "GET", "/v1/catalog/modules/helloworld/source", who="two"
        )
        source = (await response.json())["data"]
        self.assertEqual(source["source"], self.MODULE_SOURCE)
        self.assertEqual(
            source["sha256"], hashlib.sha256(self.MODULE_SOURCE.encode()).hexdigest()
        )
        self.assertEqual(source["class_name"], "HelloWorldMod")

        response = await self._request(
            "POST",
            "/v1/catalog/modules/helloworld/installs",
            {"version": "1.0.0"},
            who="two",
        )
        self.assertEqual((await response.json())["data"]["installs"], 1)

        response = await self._request(
            "POST", "/v1/catalog/modules/helloworld/rating", {"stars": 4}, who="two"
        )
        self.assertEqual((await response.json())["data"]["rating"], 4)
        response = await self._request(
            "POST", "/v1/catalog/modules/helloworld/rating", {"stars": 5}, who="one"
        )
        self.assertEqual((await response.json())["error"]["code"], "own_module")

        response = await self._request(
            "GET", "/v1/catalog/modules/helloworld", who="two"
        )
        detail = (await response.json())["data"]
        self.assertEqual(detail["my_rating"], 4)
        self.assertEqual(detail["installed_version"], "1.0.0")
        self.assertFalse(detail["is_owner"])
        self.assertEqual(detail["changelog"], "First")

        response = await self._request("GET", "/v1/stats", who="two")
        self.assertEqual(
            (await response.json())["data"]["catalog"], {"modules": 1, "installs": 1}
        )

    async def test_catalog_versions_ownership_and_updates(self):
        await self._publish(self.MODULE_SOURCE)
        same = await self._publish(self.MODULE_SOURCE)
        self.assertEqual(same.status, 409)
        self.assertEqual((await same.json())["error"]["code"], "version_not_newer")

        stolen = await self._publish(
            self.MODULE_SOURCE.replace("(1, 0, 0)", "(9, 0, 0)"), who="two"
        )
        self.assertEqual(stolen.status, 403)
        self.assertEqual((await stolen.json())["error"]["code"], "not_module_owner")

        newer = self.MODULE_SOURCE.replace("(1, 0, 0)", "(1, 1, 0)")
        response = await self._publish(newer, changelog="Faster")
        self.assertEqual(response.status, 201)
        self.assertEqual((await response.json())["data"]["previous_version"], "1.0.0")

        response = await self._request(
            "POST",
            "/v1/catalog/updates",
            {"modules": {"helloworld": "1.0.0", "missing": "1.0.0"}},
            who="two",
        )
        updates = (await response.json())["data"]["updates"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["latest_version"], "1.1.0")
        self.assertEqual(updates[0]["changelog"], "Faster")

        response = await self._request(
            "GET", "/v1/catalog/modules/helloworld/source?version=1.0.0", who="two"
        )
        self.assertEqual(response.status, 200)

        events = await self._request(
            "GET", "/v1/events?after_id=0&limit=10&topic=system.catalog", who="two"
        )
        self.assertEqual(
            [e["payload"]["version"] for e in (await events.json())["data"]["events"]],
            ["1.0.0", "1.1.0"],
        )

        response = await self._request(
            "DELETE", "/v1/catalog/modules/helloworld?version=1.1.0", who="one"
        )
        self.assertEqual((await response.json())["data"]["latest_version"], "1.0.0")
        response = await self._request(
            "DELETE", "/v1/catalog/modules/helloworld", who="two"
        )
        self.assertEqual(response.status, 403)
        response = await self._request(
            "DELETE", "/v1/catalog/modules/helloworld", who="one"
        )
        self.assertEqual(response.status, 200)
        response = await self._request(
            "GET", "/v1/catalog/modules/helloworld", who="two"
        )
        self.assertEqual(response.status, 404)

    async def test_catalog_rejects_bad_sources_and_flags_risky_code(self):
        response = await self._publish("def broken(:\n")
        self.assertEqual((await response.json())["error"]["code"], "syntax_error")
        response = await self._publish("x = 1\n")
        self.assertEqual((await response.json())["error"]["code"], "not_a_module")
        response = await self._publish(
            self.MODULE_SOURCE.replace("__version__ = (1, 0, 0)\n", "")
        )
        self.assertEqual((await response.json())["error"]["code"], "version_required")

        risky = self.MODULE_SOURCE + (
            "\nimport subprocess\n"
            "from telethon.sessions import StringSession\n"
            "def run(x):\n"
            "    return eval(x)\n"
        )
        response = await self._publish(risky)
        warnings = (await response.json())["data"]["warnings"]
        self.assertEqual(warnings, ["dynamic_code", "shell", "session_access"])

    async def test_catalog_upload_may_exceed_regular_body_limit(self):
        filler = "# " + "x" * 78 + "\n"
        source = self.MODULE_SOURCE + filler * 2000
        self.assertGreater(len(source), self.settings.request_body_limit)
        response = await self._publish(source)
        self.assertEqual(response.status, 201, await response.text())

        response = await self._request(
            "POST",
            "/v1/events",
            {"topic": "big", "payload": "x" * 140000, "ttl_seconds": 600},
        )
        self.assertEqual(response.status, 413)

    async def test_direct_messages_reach_only_the_recipient(self):
        response = await self._request(
            "POST", "/v1/inbox", {"to": "hikka-two", "text": "Привіт"}, who="one"
        )
        self.assertEqual(response.status, 201)
        message_id = (await response.json())["data"]["id"]

        response = await self._request("GET", "/v1/inbox?after_id=0", who="two")
        data = (await response.json())["data"]
        self.assertEqual(data["messages"][0]["text"], "Привіт")
        self.assertEqual(data["messages"][0]["sender_display_name"], "One")
        self.assertEqual(data["next_after_id"], message_id)

        response = await self._request("GET", "/v1/inbox?after_id=0", who="one")
        self.assertEqual((await response.json())["data"]["messages"], [])

        response = await self._request(
            "POST", "/v1/inbox", {"to": "hikka-one", "text": "me"}, who="one"
        )
        self.assertEqual((await response.json())["error"]["code"], "self_message")
        response = await self._request(
            "POST", "/v1/inbox", {"to": "hikka-nobody", "text": "?"}, who="one"
        )
        self.assertEqual(response.status, 404)


    async def test_official_modules_are_imported_from_the_manifest(self):
        sources = {
            "hello.py": self.MODULE_SOURCE,
            "broken.py": "x = 1\n",
        }
        fetched = []

        async def fetch(filename):
            fetched.append(filename)
            if filename == "offline.py":
                raise RuntimeError("HTTP 503")
            return sources[filename]

        app = self.client.server.app
        manifest = {"hello.py": "1.4.0", "broken.py": "1.0.0", "offline.py": "1.0.0"}
        imported = await _import_official_modules(
            app, manifest, fetch, {"hello.py": "Нове"}
        )
        self.assertEqual([item["module_id"] for item in imported], ["helloworld"])
        self.assertTrue(imported[0]["official"])
        # The manifest version wins over __version__ in the file.
        self.assertEqual(imported[0]["latest_version"], "1.4.0")

        state = (await self.store.get_service_state(CATALOG_IMPORT_STATE_KEY))["value"]
        self.assertEqual(state["broken.py"]["status"], "not_a_module")
        self.assertNotIn("offline.py", state)  # retried on the next check

        fetched.clear()
        await _import_official_modules(app, manifest, fetch)
        self.assertEqual(fetched, ["offline.py"])

        response = await self._request(
            "GET", "/v1/catalog/modules?official=1", who="two"
        )
        listing = (await response.json())["data"]
        self.assertEqual(listing["total"], 1)
        item = listing["modules"][0]
        self.assertTrue(item["official"])
        self.assertEqual(item["owner_display_name"], "Splaueef/host")
        self.assertEqual(item["tags"], ["official"])

        hijack = await self._publish(
            self.MODULE_SOURCE.replace("(1, 0, 0)", "(9, 0, 0)")
        )
        self.assertEqual((await hijack.json())["error"]["code"], "not_module_owner")

        events = await self._request(
            "GET", "/v1/events?after_id=0&limit=10&topic=system.catalog", who="two"
        )
        payload = (await events.json())["data"]["events"][0]["payload"]
        self.assertTrue(payload["official"])
        self.assertEqual(payload["changelog"], "Нове")

        imported = await _import_official_modules(
            app, {"hello.py": "1.5.0"}, fetch
        )
        self.assertEqual(imported[0]["previous_version"], "1.4.0")


if __name__ == "__main__":
    unittest.main()
