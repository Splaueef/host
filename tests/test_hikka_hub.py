import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from hikka_hub.app import (
    MODULE_REPOSITORY,
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


if __name__ == "__main__":
    unittest.main()
