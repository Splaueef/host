"""Tests for ContactStatus watch-list management and statistics."""

import datetime
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from zoneinfo import ZoneInfo


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        self._storage = getattr(self, "_storage", {})
        self._storage[key] = value


def _decorator(*args, **kwargs):
    return lambda value: value


def _load_module():
    package = types.ModuleType("testhost")
    package.__path__ = []
    modules = types.ModuleType("testhost.modules")
    modules.__path__ = []
    loader = types.ModuleType("testhost.loader")
    utils = types.ModuleType("testhost.utils")
    loader.Module = _Module
    loader.tds = lambda value: value
    loader.raw_handler = _decorator
    loader.command = _decorator
    loader.loop = _decorator
    utils.escape_html = (
        lambda value: str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    utils.get_args_raw = lambda message: message.args

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils

    telethon = types.ModuleType("telethon")
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    contacts = types.ModuleType("telethon.tl.functions.contacts")
    types_mod = types.ModuleType("telethon.tl.types")

    class GetContactsRequest:
        def __init__(self, hash):
            self.hash = hash

    class UpdateUserStatus:
        pass

    class UserStatusOnline:
        pass

    class UserStatusOffline:
        def __init__(self, was_online=None):
            self.was_online = was_online

    contacts.GetContactsRequest = GetContactsRequest
    types_mod.UpdateUserStatus = UpdateUserStatus
    types_mod.UserStatusOnline = UserStatusOnline
    types_mod.UserStatusOffline = UserStatusOffline
    sys.modules.update(
        {
            "testhost": package,
            "testhost.modules": modules,
            "testhost.loader": loader,
            "testhost.utils": utils,
            "telethon": telethon,
            "telethon.tl": tl,
            "telethon.tl.functions": functions,
            "telethon.tl.functions.contacts": contacts,
            "telethon.tl.types": types_mod,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "contactstatus.py"
    spec = importlib.util.spec_from_file_location(
        "testhost.modules.contactstatus",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


contactstatus = _load_module()
UTC = datetime.timezone.utc


def _user(
    user_id,
    name,
    username=None,
    *,
    contact=True,
    status=None,
):
    return types.SimpleNamespace(
        id=user_id,
        first_name=name,
        last_name=None,
        username=username,
        contact=contact,
        status=status,
        bot=False,
        self=False,
    )


class _Client:
    def __init__(self, contacts=None, entities=None):
        self.contacts = contacts or []
        self.entities = entities or {}
        self.sent_files = []

    async def __call__(self, request):
        return types.SimpleNamespace(users=self.contacts)

    async def get_entity(self, identifier):
        key = str(identifier).lstrip("@").lower()
        if key not in self.entities:
            raise ValueError("not found")
        return self.entities[key]

    async def send_file(self, peer_id, file, **kwargs):
        self.sent_files.append(
            {
                "peer_id": peer_id,
                "name": file.name,
                "content": file.getvalue(),
                **kwargs,
            }
        )


class _Message:
    def __init__(self, args="", reply=None):
        self.args = args
        self.reply = reply
        self.answers = []
        self.peer_id = 100
        self.reply_to_msg_id = None
        self.deleted = False

    async def get_reply_message(self):
        return self.reply

    async def delete(self):
        self.deleted = True


class _Reply:
    def __init__(self, sender):
        self.sender = sender

    async def get_sender(self):
        return self.sender


class _InlineCall:
    def __init__(self):
        self.form = {"chat": 100}
        self.edits = []
        self.callback_answers = []

    async def edit(self, text, reply_markup=None):
        self.edits.append(
            {"text": text, "reply_markup": reply_markup}
        )
        return True

    async def answer(self, text):
        self.callback_answers.append(text)


class _InlineManager:
    def __init__(self):
        self.forms = []

    async def form(self, text, message, reply_markup=None):
        self.forms.append(
            {
                "text": text,
                "message": message,
                "reply_markup": reply_markup,
            }
        )
        return True


class ContactStatusTests(unittest.TestCase):
    def setUp(self):
        self.module = contactstatus.ContactStatusMod()
        self.module._ensure_storage()
        self.module.set(
            "contacts",
            {
                "1": {
                    "name": "<Alice>",
                    "username": "alice",
                    "is_contact": True,
                    "manual": False,
                },
                "2": {
                    "name": "Bob",
                    "username": "bob",
                    "is_contact": True,
                    "manual": False,
                },
            },
        )
        self.module.set("watchlist", ["1", "2"])
        self.module._syncing = False

    def test_v2_profiles_are_migrated_to_watchlist(self):
        module = contactstatus.ContactStatusMod()
        module.set("contacts", {"7": {"name": "Legacy"}})

        module._ensure_storage()

        self.assertEqual(module.get("watchlist"), ["7"])
        self.assertTrue(module.get("auto_watch_contacts"))

    def test_sessions_are_closed_and_split_at_midnight(self):
        start = datetime.datetime(2026, 9, 7, 23, 59, tzinfo=UTC)
        end = datetime.datetime(2026, 9, 8, 0, 1, tzinfo=UTC)
        self.module._set_online(1, True, start)
        self.module._set_online(1, False, end)

        days = self.module.get("days")
        self.assertEqual(len(days["2026-09-07"]["1"]), 1)
        self.assertEqual(len(days["2026-09-08"]["1"]), 1)
        self.assertEqual(
            days["2026-09-07"]["1"][0][1],
            end.replace(minute=0).timestamp(),
        )

    def test_report_has_summary_ranking_and_shared_online(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=10),
        )
        self.module._store_interval(
            2,
            day.replace(hour=9),
            day.replace(hour=11),
        )

        report = self.module._report(day.replace(hour=12))

        self.assertIn("&lt;Alice&gt;", report)
        self.assertIn("08:00:00–10:00:00", report)
        self.assertIn("Сумарна активність: <b>4 год</b>", report)
        self.assertIn("Одночасний пік: <b>2</b>", report)
        self.assertIn(
            "Щонайменше двоє online: <b>1 год</b>",
            report,
        )

    def test_person_report_has_session_details(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=10),
        )

        report = self.module._report(
            day.replace(hour=12),
            user_id="1",
        )

        self.assertIn("👤 <b>ContactStatus</b>", report)
        self.assertIn("У мережі: <b>2 год</b>", report)
        self.assertIn("Входів: <b>1</b>", report)
        self.assertIn("08:00:00–10:00:00", report)
        self.assertIn("@alice", report)

    def test_all_sessions_are_shown_and_long_reports_are_paginated(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        for minute in range(0, 360, 3):
            start = day + datetime.timedelta(hours=6, minutes=minute)
            self.module._store_interval(
                1,
                start,
                start + datetime.timedelta(seconds=45),
            )

        pages = self.module._report_pages(
            day.replace(hour=13),
            user_id="1",
        )
        complete = "\n".join(pages)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) < 4096 for page in pages))
        self.assertIn("01. <code>06:00:00–06:00:45</code>", complete)
        self.assertIn("120. <code>11:57:00–11:57:45</code>", complete)

    def test_short_activity_still_has_visible_bar(self):
        self.assertEqual(
            self.module._bar(1, 1000),
            "█░░░░░░░░░",
        )

    def test_chart_renderer_returns_a_png_dashboard(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=10),
        )
        self.module._store_interval(
            2,
            day.replace(hour=9),
            day.replace(hour=11),
        )
        intervals, start, end = self.module._period_intervals(
            day.replace(hour=12)
        )

        chart = self.module._render_chart(
            intervals,
            self.module.get("contacts"),
            start,
            end,
            1,
            "Сьогодні · 00:00–12:00",
        )

        self.assertEqual(chart.name, "contactstatus-chart.png")
        self.assertTrue(chart.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(chart.getvalue()), 20000)
        chart.close()

    def test_daily_chart_buckets_include_empty_days(self):
        start = datetime.datetime(2026, 9, 6, tzinfo=UTC)
        end = datetime.datetime(2026, 9, 9, tzinfo=UTC)
        intervals = {
            "1": [
                [
                    start.timestamp(),
                    (start + datetime.timedelta(hours=1)).timestamp(),
                ]
            ]
        }

        totals = self.module._daily_totals(
            intervals,
            start,
            end,
            UTC,
        )

        self.assertEqual(len(totals), 3)
        self.assertEqual(totals[start.date()], 3600)
        self.assertEqual(
            totals[(start + datetime.timedelta(days=1)).date()],
            0,
        )

    def test_export_payload_is_complete_and_excludes_secrets(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module.get("contacts")["1"]["access_hash"] = "secret"
        for hour in (8, 10):
            self.module._store_interval(
                1,
                day.replace(hour=hour),
                day.replace(hour=hour, minute=15),
            )

        payload = self.module._build_export_payload(day.replace(hour=12))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["summary"]["session_count"], 2)
        self.assertEqual(len(payload["users"][0]["sessions"]), 2)
        self.assertEqual(payload["users"][0]["median_session_seconds"], 900)
        self.assertNotIn("access_hash", json.dumps(payload))

    def test_json_export_is_valid_utf8_with_all_sessions(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        payload = self.module._build_export_payload(day.replace(hour=12))

        export = self.module._render_json_export(payload)
        decoded = json.loads(export.getvalue().decode("utf-8"))

        self.assertEqual(decoded["users"][0]["name"], "<Alice>")
        self.assertEqual(decoded["users"][0]["sessions"][0]["duration_seconds"], 3600)
        self.assertTrue(export.name.endswith(".json"))
        export.close()

    def test_html_export_is_self_contained_escaped_and_searchable(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        payload = self.module._build_export_payload(day.replace(hour=12))

        export = self.module._render_html_export(payload)
        document = export.getvalue().decode("utf-8")

        self.assertIn("<!doctype html>", document)
        self.assertIn("&lt;Alice&gt;", document)
        self.assertNotIn("<Alice>", document)
        self.assertIn("08.09.2026 08:00:00", document)
        self.assertIn('id="search"', document)
        self.assertNotIn("https://", document)
        self.assertTrue(export.name.endswith(".html"))
        export.close()

    def test_weekday_hour_heatmap_splits_cross_hour_session(self):
        monday = datetime.datetime(2026, 9, 7, 8, 30, tzinfo=UTC)
        intervals = {
            "1": [
                [
                    monday.timestamp(),
                    (monday + datetime.timedelta(hours=1)).timestamp(),
                ]
            ]
        }

        heatmap = self.module._weekday_hour_totals(intervals, UTC)

        self.assertEqual(heatmap[0][8], 1800)
        self.assertEqual(heatmap[0][9], 1800)

    def test_insights_report_explains_rhythm_and_peak_hours(self):
        start = datetime.datetime(2026, 9, 6, tzinfo=UTC)
        for offset in range(3):
            day = start + datetime.timedelta(days=offset)
            self.module._store_interval(
                1,
                day.replace(hour=18),
                day.replace(hour=19),
            )

        report = self.module._insights_report(
            start.replace(day=8, hour=23),
            days_count=3,
            user_id="1",
        )

        self.assertIn("ContactStatus · інсайти", report)
        self.assertIn("Медіанний сеанс: <b>1 год</b>", report)
        self.assertIn("Пікові години: <b>18:00</b>", report)
        self.assertIn("Найдовша серія: <b>3 дн.</b>", report)

    def test_open_session_is_counted_without_being_closed(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._set_online(1, True, day.replace(hour=7))
        intervals = self.module._today_intervals(day.replace(hour=8))

        self.assertEqual(
            intervals["1"],
            [
                [
                    day.replace(hour=7).timestamp(),
                    day.replace(hour=8).timestamp(),
                ]
            ],
        )
        self.assertIn("1", self.module.get("active"))

    def test_adjacent_status_updates_are_coalesced(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        self.module._store_interval(
            1,
            day.replace(hour=9),
            day.replace(hour=10),
        )

        self.assertEqual(
            self.module.get("days")["2026-09-08"]["1"],
            [
                [
                    day.replace(hour=8).timestamp(),
                    day.replace(hour=10).timestamp(),
                ]
            ],
        )

    def test_out_of_order_intervals_are_sorted_and_merged(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=10),
            day.replace(hour=11),
        )
        self.module._store_interval(
            1,
            day.replace(hour=9),
            day.replace(hour=10, minute=30),
        )

        self.assertEqual(
            self.module.get("days")["2026-09-08"]["1"],
            [
                [
                    day.replace(hour=9).timestamp(),
                    day.replace(hour=11).timestamp(),
                ]
            ],
        )

    def test_history_can_be_rebucketed_for_another_timezone(self):
        start = datetime.datetime(2026, 9, 7, 22, 30, tzinfo=UTC)
        end = datetime.datetime(2026, 9, 7, 23, 30, tzinfo=UTC)
        self.module._store_interval(1, start, end)

        self.module._rebucket_history(ZoneInfo("Europe/Berlin"))

        self.assertNotIn("2026-09-07", self.module.get("days"))
        self.assertIn(
            "1",
            self.module.get("days")["2026-09-08"],
        )

    def test_top_pair_returns_exact_shared_periods(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        intervals = {
            "1": [
                [
                    day.replace(hour=8).timestamp(),
                    day.replace(hour=10).timestamp(),
                ]
            ],
            "2": [
                [
                    day.replace(hour=9).timestamp(),
                    day.replace(hour=11).timestamp(),
                ]
            ],
            "3": [
                [
                    day.replace(hour=12).timestamp(),
                    day.replace(hour=13).timestamp(),
                ]
            ],
        }

        pair, spans, total = self.module._top_pair(intervals)

        self.assertEqual(pair, ("1", "2"))
        self.assertEqual(total, 3600)
        self.assertEqual(len(spans), 1)

    def test_comparison_includes_all_shared_moments(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=10),
        )
        self.module._store_interval(
            2,
            day.replace(hour=9),
            day.replace(hour=11),
        )

        report = "\n".join(
            self.module._compare_report_pages(
                day.replace(hour=12),
                "1",
                "2",
            )
        )

        self.assertIn("ContactStatus · порівняння", report)
        self.assertIn("Разом online: <b>1 год</b>", report)
        self.assertIn("09:00:00–10:00:00", report)

    def test_restart_recovery_does_not_count_downtime(self):
        start = datetime.datetime(2026, 9, 8, 8, tzinfo=UTC)
        restart = start + datetime.timedelta(hours=4)
        self.module.set("active", {"1": start.timestamp()})
        self.module.set(
            "heartbeat",
            (start + datetime.timedelta(minutes=2)).timestamp(),
        )

        self.module._recover_open_sessions(restart)

        span = self.module.get("days")["2026-09-08"]["1"][0]
        self.assertEqual(
            span[1] - span[0],
            3 * 60 + 30,
        )
        self.assertEqual(self.module.get("active"), {})

    def test_old_history_is_pruned_to_retention_window(self):
        self.module.set(
            "days",
            {
                "2026-08-01": {"1": [[1, 2]]},
                "2026-09-08": {"1": [[3, 4]]},
            },
        )

        self.module._prune(datetime.date(2026, 9, 8))

        self.assertEqual(list(self.module.get("days")), ["2026-09-08"])


class ContactStatusAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.online_type = contactstatus.UserStatusOnline
        self.module = contactstatus.ContactStatusMod()
        self.module._ensure_storage()
        self.module._syncing = False

    async def test_sync_auto_adds_new_contacts_and_refreshes_profiles(self):
        self.module.set(
            "contacts",
            {
                "1": {
                    "name": "Old",
                    "username": "old",
                    "is_contact": True,
                    "manual": False,
                }
            },
        )
        self.module.set("watchlist", ["1"])
        self.module._client = _Client(
            [
                _user(
                    2,
                    "New",
                    "new_user",
                    status=self.online_type(),
                )
            ]
        )

        result = await self.module._sync_contacts(
            datetime.datetime(2026, 9, 8, 12, tzinfo=UTC)
        )

        self.assertEqual(result["added"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.module.get("watchlist"), ["2"])
        self.assertTrue(
            self.module.get("contacts")["2"]["is_contact"]
        )
        self.assertFalse(
            self.module.get("contacts")["1"]["is_contact"]
        )
        self.assertIn("2", self.module.get("active"))

    async def test_paused_contact_is_not_auto_added_again(self):
        user = _user(2, "Paused", "paused")
        self.module.set("excluded_contacts", ["2"])
        self.module._client = _Client([user])

        await self.module._sync_contacts(
            datetime.datetime(2026, 9, 8, 12, tzinfo=UTC)
        )

        self.assertNotIn("2", self.module.get("watchlist"))
        self.assertIn("2", self.module.get("contacts"))

    async def test_username_can_be_added_manually(self):
        user = _user(
            9,
            "Manual",
            "manual_user",
            contact=False,
            status=self.online_type(),
        )
        self.module._client = _Client(
            entities={"manual_user": user}
        )
        message = _Message("@manual_user")

        await self.module.contactadd(message)

        self.assertIn("9", self.module.get("watchlist"))
        self.assertTrue(
            self.module.get("contacts")["9"]["manual"]
        )
        self.assertIn("9", self.module.get("active"))
        self.assertIn("додано", message.answers[-1])

    async def test_manual_profile_identity_is_refreshed(self):
        self.module.set(
            "contacts",
            {
                "9": {
                    "name": "Old name",
                    "username": "old_name",
                    "is_contact": False,
                    "manual": True,
                }
            },
        )
        self.module.set("watchlist", ["9"])
        self.module._client = _Client(
            entities={
                "9": _user(
                    9,
                    "New name",
                    "new_name",
                    contact=False,
                )
            }
        )

        changed = await self.module._refresh_manual_profiles(
            datetime.datetime(2026, 9, 8, 12, tzinfo=UTC)
        )

        self.assertEqual(changed, 1)
        self.assertEqual(
            self.module.get("contacts")["9"]["username"],
            "new_name",
        )

    async def test_removing_contact_preserves_history_and_prevents_readd(self):
        user = _user(4, "Contact", "contact")
        self.module._client = _Client(
            contacts=[user],
            entities={"contact": user},
        )
        await self.module._sync_contacts(
            datetime.datetime(2026, 9, 8, 8, tzinfo=UTC)
        )
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module._store_interval(
            4,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        message = _Message("@contact")

        await self.module.contactremove(message)
        await self.module._sync_contacts(day.replace(hour=10))

        self.assertNotIn("4", self.module.get("watchlist"))
        self.assertIn("4", self.module.get("excluded_contacts"))
        self.assertIn(
            "4",
            self.module.get("days")["2026-09-08"],
        )

    async def test_watcher_ignores_untracked_user(self):
        update = types.SimpleNamespace(
            user_id=77,
            status=self.online_type(),
        )

        await self.module.status_watcher(update)

        self.assertNotIn("77", self.module.get("active"))

    async def test_html_export_command_sends_a_complete_file(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module.set(
            "contacts",
            {
                "1": {
                    "name": "Alice",
                    "username": "alice",
                    "is_contact": True,
                }
            },
        )
        self.module.set("watchlist", ["1"])
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        self.module._now = lambda: day.replace(hour=12)
        self.module._client = _Client()
        message = _Message("html today")

        await self.module.contactexport(message)

        self.assertTrue(message.deleted)
        self.assertEqual(len(self.module._client.sent_files), 1)
        sent = self.module._client.sent_files[0]
        self.assertTrue(sent["name"].endswith(".html"))
        self.assertIn(b"<!doctype html>", sent["content"])
        self.assertIn("HTML-звіт", sent["caption"])

    async def test_contactstats_opens_paginated_inline_navigator(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module.set(
            "contacts",
            {"1": {"name": "Alice", "username": "alice"}},
        )
        self.module.set("watchlist", ["1"])
        for minute in range(0, 360, 3):
            start = day + datetime.timedelta(hours=6, minutes=minute)
            self.module._store_interval(
                1,
                start,
                start + datetime.timedelta(seconds=45),
            )
        self.module._now = lambda: day.replace(hour=13)
        self.module.inline = _InlineManager()

        await self.module.contactstats(_Message("today"))

        self.assertEqual(len(self.module.inline.forms), 1)
        form = self.module.inline.forms[0]
        labels = [
            button["text"]
            for row in form["reply_markup"]
            for button in row
        ]
        self.assertIn("◀️", labels)
        self.assertIn("▶️", labels)
        self.assertIn("• Сьогодні", labels)
        self.assertIn("🌐 HTML", labels)
        self.assertIn("📈 PNG-графік", labels)

    async def test_inline_picker_pages_through_all_watched_users(self):
        profiles = {
            str(user_id): {"name": f"User {user_id}"}
            for user_id in range(1, 19)
        }
        self.module.set("contacts", profiles)
        self.module.set("watchlist", list(profiles))
        call = _InlineCall()

        await self.module._inline_user_picker(call, 7, 0)

        self.assertEqual(len(call.edits), 1)
        edit = call.edits[0]
        self.assertIn("У списку спостереження: <b>18</b>", edit["text"])
        labels = [
            button["text"]
            for row in edit["reply_markup"]
            for button in row
        ]
        self.assertIn("1/3", labels)
        user_buttons = [label for label in labels if label.startswith("⚪️")]
        self.assertEqual(len(user_buttons), self.module.INLINE_LIST_PAGE_SIZE)

    async def test_contactstatus_opens_main_inline_panel(self):
        self.module.inline = _InlineManager()

        await self.module.contactstatus(_Message())

        form = self.module.inline.forms[0]
        labels = [
            button["text"]
            for row in form["reply_markup"]
            for button in row
        ]
        self.assertIn("📊 Статистика", labels)
        self.assertIn("👥 Список", labels)
        self.assertIn("🔄 Синхронізувати", labels)

    async def test_inline_export_button_sends_file_to_form_chat(self):
        day = datetime.datetime(2026, 9, 8, tzinfo=UTC)
        self.module.set(
            "contacts",
            {"1": {"name": "Alice", "username": "alice"}},
        )
        self.module.set("watchlist", ["1"])
        self.module._store_interval(
            1,
            day.replace(hour=8),
            day.replace(hour=9),
        )
        self.module._now = lambda: day.replace(hour=12)
        self.module._client = _Client()
        call = _InlineCall()

        await self.module._inline_send_asset(call, "json", 1, 0)

        self.assertEqual(call.callback_answers, ["Готую файл…"])
        self.assertEqual(len(self.module._client.sent_files), 1)
        sent = self.module._client.sent_files[0]
        self.assertEqual(sent["peer_id"], call.form["chat"])
        self.assertTrue(sent["name"].endswith(".json"))


if __name__ == "__main__":
    unittest.main()
