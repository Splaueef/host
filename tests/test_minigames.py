"""Tests for public MiniGames sessions and scoring."""

import importlib.util
import pathlib
import sys
import types
import unittest


class _Module:
    def get(self, key, default=None):
        return getattr(self, "_storage", {}).get(key, default)

    def set(self, key, value):
        if not hasattr(self, "_storage"):
            self._storage = {}
        self._storage[key] = value


class _ConfigValue:
    def __init__(self, key, default, doc, validator=None):
        self.key = key
        self.default = default


class _ModuleConfig(dict):
    def __init__(self, *values):
        super().__init__((value.key, value.default) for value in values)


def _decorator(*args, **kwargs):
    def decorate(function):
        function.is_command = True
        return function

    return decorate


def _load_module():
    package = types.ModuleType("gameshost")
    package.__path__ = []
    modules = types.ModuleType("gameshost.modules")
    modules.__path__ = []
    loader = types.ModuleType("gameshost.loader")
    utils = types.ModuleType("gameshost.utils")
    loader.Module = _Module
    loader.ConfigValue = _ConfigValue
    loader.ModuleConfig = _ModuleConfig
    loader.tds = lambda value: value
    loader.command = _decorator
    loader.validators = types.SimpleNamespace(
        Integer=lambda **kwargs: object(), Boolean=lambda **kwargs: object()
    )
    utils.get_chat_id = lambda message: message.chat_id
    utils.get_args_raw = lambda message: getattr(message, "args", "")

    async def answer(message, text):
        message.answers.append(text)

    utils.answer = answer
    package.loader, package.utils = loader, utils
    sys.modules.update(
        {
            "gameshost": package,
            "gameshost.modules": modules,
            "gameshost.loader": loader,
            "gameshost.utils": utils,
        }
    )
    path = pathlib.Path(__file__).parents[1] / "minigames.py"
    spec = importlib.util.spec_from_file_location("gameshost.modules.minigames", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


minigames = _load_module()


class _Call:
    def __init__(self, user_id, name):
        self.from_user = types.SimpleNamespace(
            id=user_id, first_name=name, last_name=None, username=name.lower()
        )
        self.edits = []
        self.answers = []

    async def edit(self, text, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})


class _Inline:
    def __init__(self):
        self.forms = []

    async def form(self, text, message, **kwargs):
        self.forms.append({"text": text, **kwargs})
        return True


class _RichInline:
    def __init__(self):
        self._token = "test-token"
        self._units = {"rich-unit": {"buttons": [], "disable_security": True}}
        self._counter = 0

    def generate_markup(self, value):
        rows = self._units[value]["buttons"] if isinstance(value, str) else value
        for row in rows:
            for button in row:
                if button.get("callback") and not button.get("_callback_data"):
                    self._counter += 1
                    button["_callback_data"] = f"rich-{self._counter}"
        return object()


class _Message:
    def __init__(self):
        self.chat_id = -100
        self.args = ""
        self.answers = []

    async def get_reply_message(self):
        return None


class _Network:
    def __init__(self):
        self.config = {"instance_id": "hikka-one"}
        self.moves = []

    def _configured(self):
        return True

    async def api_game_create(self, kind, opponent_instance_id=None):
        return {}

    async def api_games(self, **kwargs):
        return {"games": []}

    async def api_game_get(self, game_id):
        return {}

    async def api_game_join(self, game_id):
        return {}

    async def api_game_move(self, game_id, revision, action):
        self.moves.append((game_id, revision, action))
        board = [None] * 9
        board[int(action["position"])] = 0
        return {
            "game_id": game_id,
            "kind": "ttt",
            "status": "active",
            "my_slot": 0,
            "revision": revision + 1,
            "players": [
                {"slot": 0, "instance_id": "hikka-one", "display_name": "Host"},
                {"slot": 1, "instance_id": "hikka-two", "display_name": "Guest"},
            ],
            "state": {
                "board": board,
                "turn": 1,
                "winner": None,
                "draw": False,
                "finished": False,
                "finish_reason": None,
                "move_number": 1,
                "last_action": "1:1",
            },
        }

    async def api_game_leaderboard(self, **kwargs):
        return {"ranking": []}

    async def api_game_resign(self, game_id):
        return {}

    async def api_game_cancel(self, game_id):
        return {}

    async def api_game_profile(self, **kwargs):
        return {"games": []}


class MiniGamesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = minigames.MiniGamesMod()
        self.module._me_id = 1
        self.module._me_name = "Host"

    def test_tic_tac_toe_detects_rows_columns_and_diagonals(self):
        self.assertEqual(self.module._winner_ttt([0, 0, 0, None, None, None, None, None, None]), 0)
        self.assertEqual(self.module._winner_ttt([1, None, None, 1, None, None, 1, None, None]), 1)
        self.assertEqual(self.module._winner_ttt([0, None, None, None, 0, None, None, None, 0]), 0)
        self.assertIsNone(self.module._winner_ttt([0, 1, 0, 1, 0, 1, 1, 0, 1]))

    def test_finished_games_have_aggregate_modulehub_snapshot(self):
        reported = []
        hub = types.SimpleNamespace(
            report_stat=lambda module, metric, delta: reported.append((metric, delta))
        )
        self.module.lookup = lambda name: hub if name == "ModuleHub" else None
        session = {
            "kind": "rps",
            "chat_id": -100,
            "players": [1, 2],
            "names": {"1": "Host", "2": "Guest"},
        }

        self.module._record_result(session, [1])
        snapshot = self.module.modulehub_stats()

        self.assertEqual(snapshot["games_completed"], 1)
        self.assertEqual(snapshot["by_game"]["rps"]["completed"], 1)
        self.assertIn(("games.completed", 1), reported)
        self.assertNotIn("chat_id", repr(snapshot))

    def test_checkers_initial_board_has_twelve_pieces_per_side(self):
        board = self.module._checker_initial_board()

        self.assertEqual(len(board), 64)
        self.assertEqual(sum(piece > 0 for piece in board), 12)
        self.assertEqual(sum(piece < 0 for piece in board), 12)
        self.assertTrue(all(not piece or (divmod(index, 8)[0] + divmod(index, 8)[1]) % 2 for index, piece in enumerate(board)))

    def test_checkers_enforces_capture_and_allows_man_to_capture_backwards(self):
        board = [0] * 64
        board[42] = 1
        board[35] = -1
        board[46] = 1

        self.assertEqual(self.module._checker_moves_for(board, 0, 42), [(28, 35)])
        self.assertEqual(self.module._checker_moves_for(board, 0, 46), [])

        backward = [0] * 64
        backward[17] = 1
        backward[26] = -1
        self.assertIn((35, 26), self.module._checker_captures(backward, 17))

    def test_flying_king_can_land_anywhere_beyond_captured_piece(self):
        board = [0] * 64
        board[49] = 2
        board[35] = -1

        self.assertEqual(
            self.module._checker_captures(board, 49),
            [(28, 35), (21, 35), (14, 35), (7, 35)],
        )

    async def test_checkers_multi_capture_continues_and_promotes_to_king(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("checkers", -100, invited)
        session = self.module._session(token)
        session["board"] = [0] * 64
        session["board"][33] = 1
        session["board"][26] = -1
        session["board"][12] = -1
        call = _Call(1, "Host")

        await self.module._checker_click(call, token, 33)
        await self.module._checker_click(call, token, 19)

        self.assertEqual(session["forced_piece"], 19)
        self.assertFalse(session["finished"])

        await self.module._checker_click(call, token, 5)

        self.assertEqual(session["board"][5], 2)
        self.assertEqual(session["captures"], [2, 0])
        self.assertTrue(session["finished"])
        self.assertEqual(session["winner"], 1)

    def test_checkers_board_uses_current_dot_cells(self):
        token = self.module._new_session("checkers", -100)
        markup = self.module._markup(token)

        self.assertEqual(len(markup[:8]), 8)
        self.assertTrue(all(len(row) == 8 for row in markup[:8]))
        self.assertEqual(markup[0][0]["text"], "∙")
        self.assertEqual(markup[0][1]["text"], "⚫")
        self.assertEqual(markup[3][0]["text"], "∙")
        self.assertEqual(markup[7][0]["text"], "⚪")

    def test_checkers_board_highlights_selection_targets_and_kings(self):
        token = self.module._new_session("checkers", -100)
        session = self.module._session(token)
        session["board"] = [0] * 64
        session["board"][42] = 2
        session["board"][23] = -2

        markup = self.module._markup(token)
        self.assertEqual(markup[5][2]["text"], "⚪👑")
        self.assertEqual(markup[2][7]["text"], "⚫👑")

        session["selected"] = 42
        markup = self.module._markup(token)
        self.assertEqual(markup[5][2]["text"], "🟦")
        self.assertEqual(markup[4][1]["text"], "🟩")
        self.assertEqual(markup[4][3]["text"], "🟩")

    def test_chess_initial_position_has_twenty_legal_moves(self):
        board = self.module._chess_initial_board()
        rights = {"K", "Q", "k", "q"}
        moves = self.module._chess_all_legal_moves(board, 0, rights)

        self.assertEqual(board[:8], list("rnbqkbnr"))
        self.assertEqual(board[-8:], list("RNBQKBNR"))
        self.assertEqual(sum(bool(piece) for piece in board), 32)
        self.assertEqual(sum(len(options) for options in moves.values()), 20)
        self.assertEqual(
            self.module._chess_legal_moves(board, 0, 52, rights),
            [(44, None), (36, None)],
        )

    def test_chess_has_premium_emoji_for_every_piece_and_empty_cell(self):
        self.assertEqual(
            set(minigames.CHESS_PREMIUM_EMOJI_IDS), set(minigames.CHESS_SYMBOLS)
        )
        self.assertEqual(len(set(minigames.CHESS_PREMIUM_EMOJI_IDS.values())), 12)
        self.assertEqual(
            minigames.CHESS_CELL_PREMIUM_EMOJI_ID, "5220005833110199517"
        )
        self.assertEqual(
            minigames.CHESS_MOVE_PREMIUM_EMOJI_ID, "5463362846219836734"
        )

    def test_chess_rejects_move_that_exposes_own_king(self):
        board = [""] * 64
        board[0] = "k"
        board[4] = "r"
        board[52] = "R"
        board[60] = "K"

        moves = dict(self.module._chess_legal_moves(board, 0, 52, set()))

        self.assertNotIn(51, moves)
        self.assertIn(44, moves)

    def test_chess_castling_requires_safe_empty_path(self):
        board = [""] * 64
        board[0] = "k"
        board[60] = "K"
        board[63] = "R"

        self.assertIn(
            (62, "castle_k"),
            self.module._chess_legal_moves(board, 0, 60, {"K"}),
        )

        castled = list(board)
        self.module._chess_apply_to_board(castled, 60, 62, "castle_k")
        self.assertEqual(castled[62], "K")
        self.assertEqual(castled[61], "R")
        self.assertFalse(castled[60])
        self.assertFalse(castled[63])

        board[13] = "r"
        self.assertNotIn(
            (62, "castle_k"),
            self.module._chess_legal_moves(board, 0, 60, {"K"}),
        )

    def test_chess_en_passant_removes_the_passed_pawn(self):
        board = [""] * 64
        board[4] = "k"
        board[27] = "p"
        board[28] = "P"
        board[60] = "K"

        self.assertIn(
            (19, "en_passant"),
            self.module._chess_legal_moves(board, 0, 28, set(), 19),
        )
        self.module._chess_apply_to_board(board, 28, 19, "en_passant")
        self.assertEqual(board[19], "P")
        self.assertEqual(board[27], "")

    async def test_chess_fools_mate_finishes_match_and_updates_winner(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("chess", -100, invited)
        host = _Call(1, "Host")
        guest = _Call(2, "Guest")

        async def move(call, source, target):
            await self.module._chess_click(call, token, source)
            await self.module._chess_click(call, token, target)

        await move(host, 53, 45)
        await move(guest, 12, 28)
        await move(host, 54, 38)
        await move(guest, 3, 39)

        session = self.module._session(token)
        self.assertTrue(session["finished"])
        self.assertEqual(session["finish_reason"], "checkmate")
        self.assertEqual(session["winner"], 2)
        self.assertIn("Мат", guest.edits[-1]["text"])

    async def test_chess_promotion_waits_for_piece_choice(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("chess", -100, invited)
        session = self.module._session(token)
        session["board"] = [""] * 64
        session["board"][7] = "k"
        session["board"][8] = "P"
        session["board"][63] = "K"
        session["castling"] = set()
        session["position_counts"] = {}
        host = _Call(1, "Host")

        await self.module._chess_click(host, token, 8)
        await self.module._chess_click(host, token, 0)

        self.assertIsNotNone(session["promotion"])
        self.assertEqual(session["turn"], 0)
        promotion_row = next(
            row
            for row in self.module._markup(token)
            if row and row[0].get("callback") == self.module._chess_promote
        )
        self.assertEqual(len(promotion_row), 4)

        await self.module._chess_promote(host, token, "q")

        self.assertEqual(session["board"][0], "Q")
        self.assertIsNone(session["promotion"])
        self.assertEqual(session["turn"], 1)

    def test_chess_detects_stalemate_and_insufficient_material(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("chess", -100, invited)
        session = self.module._session(token)
        session["board"] = [""] * 64
        session["board"][0] = "k"
        session["board"][10] = "Q"
        session["board"][18] = "K"
        session["castling"] = set()
        session["position_counts"] = {}

        self.module._chess_finish_turn(session, 0)

        self.assertTrue(session["draw"])
        self.assertEqual(session["draw_reason"], "stalemate")

        material = [""] * 64
        material[0] = "k"
        material[58] = "B"
        material[63] = "K"
        self.assertTrue(self.module._chess_insufficient_material(material))

    def test_chess_detects_fifty_moves_and_repetition(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)

        def session_with_rook():
            token = self.module._new_session("chess", -100, invited)
            session = self.module._session(token)
            session["board"] = [""] * 64
            session["board"][0] = "k"
            session["board"][55] = "R"
            session["board"][63] = "K"
            session["castling"] = set()
            session["en_passant"] = None
            session["position_counts"] = {}
            return session

        fifty = session_with_rook()
        fifty["halfmove_clock"] = 100
        self.module._chess_finish_turn(fifty, 0)
        self.assertEqual(fifty["draw_reason"], "fifty_moves")

        repeated = session_with_rook()
        repeated["turn"] = 1
        key = self.module._chess_position_key(repeated)
        repeated["position_counts"] = {key: 2}
        repeated["turn"] = 0
        self.module._chess_finish_turn(repeated, 0)
        self.assertEqual(repeated["draw_reason"], "repetition")

    def test_chess_board_highlights_moves_and_captures(self):
        token = self.module._new_session("chess", -100)
        session = self.module._session(token)
        session["board"] = [""] * 64
        session["board"][4] = "k"
        session["board"][44] = "p"
        session["board"][52] = "R"
        session["board"][60] = "K"
        session["castling"] = set()
        session["selected"] = 52

        markup = self.module._markup(token)

        self.assertEqual([button["text"] for button in markup[0]], list("abcdefgh"))
        self.assertEqual([button["text"] for button in markup[9]], list("abcdefgh"))
        self.assertTrue(all(len(row) == 8 for row in markup[1:9]))
        self.assertEqual(markup[7][4]["text"], "🔷♖")
        self.assertEqual(markup[6][4]["text"], "🔴♟")
        self.assertEqual(markup[7][3]["text"], "🟢")
        self.assertTrue(markup[1][0]["text"].startswith("8 "))
        self.assertTrue(markup[8][7]["text"].endswith(" 1"))

    async def test_chess_board_can_flip_and_finish_requires_confirmation(self):
        invited = types.SimpleNamespace(
            id=2,
            first_name="Guest",
            last_name=None,
            username="guest",
            bot=False,
        )
        token = self.module._new_session("chess", -100, invited)
        host = _Call(1, "Host")

        await self.module._chess_flip(host, token)

        session = self.module._session(token)
        markup = self.module._markup(token)
        self.assertTrue(session["board_flipped"])
        self.assertEqual([button["text"] for button in markup[0]], list("hgfedcba"))
        self.assertEqual(markup[1][0]["args"], (token, 63))
        self.assertTrue(markup[1][0]["text"].startswith("1 "))
        self.assertIn("чорні знизу", self.module._render(token))

        await self.module._chess_finish_prompt(host, token)
        self.assertEqual(session["finish_confirm"], 1)
        self.assertIn("підтвердіть завершення", self.module._render(token).lower())

        await self.module._chess_cancel_finish(host, token)
        self.assertIsNone(session["finish_confirm"])

    def test_chess_rich_board_never_hides_pieces_without_emoji_metadata(self):
        token = self.module._new_session("chess", -100)
        session = self.module._session(token)
        rich_message = self.module._build_rich_chess_message(
            session, self.module._markup(token)
        )

        table = next(
            block
            for block in rich_message["blocks"]
            if block["type"] == "table"
        )
        self.assertEqual(table["cells"][1][1]["text"]["button"]["text"], "♜")
        self.assertEqual(
            table["cells"][3][1]["text"]["button"]["text"],
            minigames.CHESS_RICH_EMPTY_CELL,
        )

    async def test_chess_renders_as_interactive_telegram_rich_message(self):
        token = self.module._new_session("chess", -100)
        session = self.module._session(token)
        session["selected"] = 52
        call = _Call(1, "Host")
        call.unit_id = "rich-unit"
        call.inline_message_id = "inline-message-id"
        self.module.inline = _RichInline()
        self.module._chess_emoji_alternatives = {
            **{
                emoji_id: "♟️"
                for emoji_id in minigames.CHESS_PREMIUM_EMOJI_IDS.values()
            },
            minigames.CHESS_CELL_PREMIUM_EMOJI_ID: "▫️",
            minigames.CHESS_MOVE_PREMIUM_EMOJI_ID: "🟩",
        }
        rendered = []

        async def capture_rich_edit(target, record, rich_message):
            rendered.append((target, record, rich_message))
            return True

        self.module._edit_rich_chess_message = capture_rich_edit

        result = await self.module._edit_chess_panel(call, token)

        self.assertTrue(result)
        self.assertFalse(call.edits)
        rich_message = rendered[0][2]
        blocks = rich_message["blocks"]
        table = next(block for block in blocks if block["type"] == "table")
        self.assertTrue(table["is_bordered"])
        self.assertTrue(table["is_striped"])
        self.assertTrue(table["is_compact"])
        self.assertEqual(len(table["cells"]), 10)
        self.assertTrue(all(len(row) == 10 for row in table["cells"]))
        self.assertEqual(
            [cell["text"] for cell in table["cells"][0][1:9]],
            list("abcdefgh"),
        )
        first_square = table["cells"][1][1]["text"]
        self.assertEqual(first_square["type"], "button")
        self.assertEqual(
            first_square["button"]["text"],
            {
                "type": "custom_emoji",
                "custom_emoji_id": "5470104136693362691",
                "alternative_text": "♟️",
            },
        )
        self.assertEqual(
            first_square["button"]["text"]["custom_emoji_id"],
            "5470104136693362691",
        )
        self.assertEqual(first_square["button"]["style"], "link")
        self.assertTrue(first_square["button"]["callback_data"].startswith("rich-"))
        self.assertEqual(
            table["cells"][3][1]["text"]["button"]["text"],
            {
                "type": "custom_emoji",
                "custom_emoji_id": "5220005833110199517",
                "alternative_text": "▫️",
            },
        )
        self.assertEqual(
            table["cells"][3][1]["text"]["button"]["text"]["custom_emoji_id"],
            "5220005833110199517",
        )
        move_square = table["cells"][5][5]["text"]["button"]
        self.assertEqual(
            move_square["text"],
            {
                "type": "custom_emoji",
                "custom_emoji_id": "5463362846219836734",
                "alternative_text": "🟩",
            },
        )
        self.assertEqual(move_square["style"], "link")
        board_emoji_ids = {
            cell["text"]["button"]["text"]["custom_emoji_id"]
            for row in table["cells"][1:9]
            for cell in row[1:9]
        }
        self.assertEqual(
            board_emoji_ids,
            set(minigames.CHESS_PREMIUM_EMOJI_IDS.values())
            | {
                minigames.CHESS_CELL_PREMIUM_EMOJI_ID,
                minigames.CHESS_MOVE_PREMIUM_EMOJI_ID,
            },
        )
        self.assertTrue(
            all(
                "icon_custom_emoji_id" not in cell["text"]["button"]
                for row in table["cells"][1:9]
                for cell in row[1:9]
            )
        )
        self.assertIn("blockquote", [block["type"] for block in blocks])

        controls = [block for block in blocks if block["type"] == "buttons"]
        undo = next(
            button
            for block in controls
            for button in block["buttons"]
            if button["text"].startswith("↶")
        )
        finish = next(
            button
            for block in controls
            for button in block["buttons"]
            if "Завершити" in button["text"]
        )
        self.assertEqual(undo["disabled"], {})
        self.assertNotIn("callback_data", undo)
        self.assertEqual(finish["style"], "danger")
        self.assertTrue(finish["callback_data"].startswith("rich-"))
        self.assertEqual(session["rich_unit_id"], "rich-unit")
        self.assertEqual(session["rich_inline_message_id"], "inline-message-id")

        callback_count = self.module.inline._counter
        refreshed_markup = self.module._markup(token)
        self.assertTrue(
            self.module._register_rich_chess_callbacks(
                call, session, refreshed_markup
            )
        )
        self.assertEqual(self.module.inline._counter, callback_count)
        self.assertEqual(
            refreshed_markup[1][0]["_callback_data"],
            first_square["button"]["callback_data"],
        )

    def test_go_creates_nine_and_thirteen_line_boards(self):
        for kind, size, button_rows in (("go9", 9, (7, 2)), ("go13", 13, (7, 6))):
            with self.subTest(kind=kind):
                token = self.module._new_session(kind, -100)
                session = self.module._session(token)
                markup = self.module._markup(token)

                self.assertEqual(session["go_size"], size)
                self.assertEqual(len(session["board"]), size * size)
                self.assertEqual([len(row) for row in markup[:2]], list(button_rows))
                self.assertEqual(len(markup[-1]), 3)

        text = self.module._render(self.module._new_session("go13", -100))
        self.assertIn("A B C D E F G H J K L M N", text)
        self.assertIn("<pre>", text)
        self.assertIn("+", text)

    async def test_go_size_menu_preserves_invited_player(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("gomenu", -100, invited)
        host = _Call(1, "Host")

        self.assertEqual(len(self.module._markup(token)[0]), 2)
        await self.module._select_go_size(host, token, "go13")

        session = self.module._session(token)
        self.assertEqual(session["kind"], "go13")
        self.assertEqual(session["go_size"], 13)
        self.assertEqual(session["players"], [1, 2])

    def test_go_captures_surrounded_group_and_rejects_suicide(self):
        size = 9
        center = self.module._go_index(4, 4, size)
        board = [0] * (size * size)
        board[center] = -1
        for row, column in ((3, 4), (5, 4), (4, 3)):
            board[self.module._go_index(row, column, size)] = 1
        closing = self.module._go_index(4, 5, size)

        result, captured, error = self.module._go_try_move(
            board,
            size,
            0,
            closing,
            set(),
        )

        self.assertIsNone(error)
        self.assertEqual(captured, 1)
        self.assertEqual(result[center], 0)

        suicide = [0] * (size * size)
        for row, column in ((3, 4), (5, 4), (4, 3), (4, 5)):
            suicide[self.module._go_index(row, column, size)] = 1
        result, captured, error = self.module._go_try_move(
            suicide,
            size,
            1,
            center,
            set(),
        )
        self.assertIsNone(result)
        self.assertEqual(captured, 0)
        self.assertEqual(error, "suicide")

    def test_go_superko_rejects_repeated_board(self):
        size = 9
        board = [0] * (size * size)
        center = self.module._go_index(4, 4, size)
        capture_point = self.module._go_index(4, 5, size)
        board[center] = -1
        for row, column in ((3, 4), (5, 4), (4, 3)):
            board[self.module._go_index(row, column, size)] = 1
        for row, column in ((3, 5), (5, 5), (4, 6)):
            board[self.module._go_index(row, column, size)] = -1
        original_key = self.module._go_board_key(board)

        captured_board, captured, error = self.module._go_try_move(
            board,
            size,
            0,
            capture_point,
            {original_key},
        )

        self.assertIsNone(error)
        self.assertEqual(captured, 1)
        history = {original_key, self.module._go_board_key(captured_board)}

        result, captured, error = self.module._go_try_move(
            captured_board,
            size,
            1,
            center,
            history,
        )

        self.assertIsNone(result)
        self.assertEqual(captured, 0)
        self.assertEqual(error, "ko")

    def test_go_chinese_scoring_counts_stones_and_territory(self):
        board = [1] * 9
        board[4] = 0

        scores, territory = self.module._go_score(board, 3)

        self.assertEqual(territory, [1, 0])
        self.assertEqual(scores, [9, 6.5])

    async def test_go_buttons_place_stones_and_join_second_player(self):
        token = self.module._new_session("go9", -100)
        host = _Call(1, "Host")
        guest = _Call(2, "Guest")

        await self.module._go_select_row(host, token, 0)
        await self.module._go_place(host, token, 0)
        await self.module._go_select_row(guest, token, 0)
        await self.module._go_place(guest, token, 1)

        session = self.module._session(token)
        self.assertEqual(session["players"][1], 2)
        self.assertEqual(session["board"][:2], [1, -1])
        self.assertEqual(session["move_number"], 2)
        self.assertEqual(session["turn"], 0)
        self.assertIn("B9", session["last_action"])

    async def test_go_two_passes_finish_with_komi_score(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("go9", -100, invited)
        host = _Call(1, "Host")
        guest = _Call(2, "Guest")

        await self.module._go_pass(host, token)
        await self.module._go_pass(guest, token)

        session = self.module._session(token)
        self.assertTrue(session["finished"])
        self.assertEqual(session["scores"], [0, 6.5])
        self.assertEqual(session["winner"], 2)
        self.assertIn("Китайський підрахунок", guest.edits[-1]["text"])

    def test_go_column_picker_is_split_for_thirteen_board(self):
        token = self.module._new_session("go13", -100)
        session = self.module._session(token)
        session["selected_row"] = 0

        markup = self.module._markup(token)

        self.assertEqual([len(markup[0]), len(markup[1])], [7, 6])
        self.assertEqual(markup[0][0]["text"], "A ·")
        self.assertEqual(markup[1][-1]["text"], "N ·")
        self.assertEqual(markup[2][0]["text"], "↩ Інший рядок")

    async def test_rps_choices_are_hidden_until_both_players_answer(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("rps", -100, invited)
        host_call = _Call(1, "Host")
        guest_call = _Call(2, "Guest")

        await self.module._rps_choose(host_call, token, "rock")
        self.assertIn("вибір зроблено", host_call.edits[-1]["text"])
        self.assertNotIn("Камінь</b>", host_call.edits[-1]["text"])

        await self.module._rps_choose(guest_call, token, "scissors")
        result = guest_call.edits[-1]["text"]
        self.assertIn("Камінь</b>", result)
        self.assertIn("Ножиці</b>", result)
        self.assertIn("Host", result)

    async def test_opened_games_are_public_but_menu_selection_is_creator_guarded(self):
        self.module.inline = _Inline()
        message = _Message()

        await self.module.games(message)

        form = self.module.inline.forms[0]
        self.assertTrue(form["disable_security"])
        select_button = form["reply_markup"][0][0]
        token = select_button["args"][0]
        stranger = _Call(2, "Guest")
        await self.module._select_game(stranger, token, "ttt")
        self.assertTrue(stranger.answers[-1]["show_alert"])
        self.assertEqual(self.module._session(token)["kind"], "menu")

    def test_finished_match_updates_per_chat_rating(self):
        invited = types.SimpleNamespace(id=2, first_name="Guest", last_name=None, username="guest", bot=False)
        token = self.module._new_session("dice", -100, invited)
        session = self.module._session(token)

        self.module._record_result(session, [2])

        top = self.module._top_text(-100)
        self.assertIn("Guest", top)
        self.assertIn("1</b> перемог", top)
        self.assertIn("Host", top)

    async def test_hikkanet_move_uses_server_revision_and_updates_remote_view(self):
        network = _Network()
        self.module.lookup = lambda name: network if name == "HikkaNet" else None
        game = {
            "game_id": "ng_0123456789abcdef",
            "kind": "ttt",
            "status": "active",
            "my_slot": 0,
            "revision": 4,
            "players": [
                {"slot": 0, "instance_id": "hikka-one", "display_name": "Host"},
                {"slot": 1, "instance_id": "hikka-two", "display_name": "Guest"},
            ],
            "state": {
                "board": [None] * 9,
                "turn": 0,
                "winner": None,
                "draw": False,
                "finished": False,
                "finish_reason": None,
                "move_number": 0,
                "last_action": None,
            },
        }
        token = self.module._new_network_view(game)
        call = _Call(1, "Host")

        await self.module._net_ttt_move(call, token, 0)

        self.assertEqual(
            network.moves,
            [("ng_0123456789abcdef", 4, {"type": "place", "position": 0})],
        )
        self.assertEqual(self.module._network_view(token)["game"]["revision"], 5)
        self.assertIn("Хід", call.edits[-1]["text"])

    async def test_hikkanet_chess_uses_player_orientation_and_network_controls(self):
        game = {
            "game_id": "ng_chess0123456789",
            "kind": "chess",
            "status": "active",
            "my_slot": 1,
            "revision": 3,
            "players": [
                {"slot": 0, "instance_id": "hikka-one", "display_name": "Host"},
                {"slot": 1, "instance_id": "hikka-two", "display_name": "Guest"},
            ],
            "state": {
                "board": self.module._chess_initial_board(),
                "turn": 1,
                "winner": None,
                "draw": False,
                "finished": False,
                "finish_reason": None,
                "move_number": 0,
                "last_action": None,
                "castling": ["K", "Q", "k", "q"],
                "en_passant": None,
                "halfmove_clock": 0,
                "position_counts": {},
            },
        }
        token = self.module._new_network_view(game)
        call = _Call(1, "Host")

        markup = self.module._markup_network(token)
        self.assertEqual([button["text"] for button in markup[0]], list("hgfedcba"))
        flip = next(
            button
            for row in markup
            for button in row
            if button["text"].startswith("↻")
        )
        self.assertEqual(flip["callback"], self.module._net_chess_flip)

        await self.module._net_chess_flip(call, token)
        self.assertFalse(self.module._network_view(token)["board_flipped"])

        await self.module._net_chess_finish_prompt(call, token)
        self.assertTrue(self.module._network_view(token)["finish_confirm"])
        self.assertIn("підтвердіть завершення", call.edits[-1]["text"].lower())

        await self.module._net_chess_cancel_finish(call, token)
        self.assertIsNone(self.module._network_view(token)["finish_confirm"])

    def test_games_menu_offers_local_and_hikkanet_modes(self):
        token = self.module._new_session("menu", -100)

        text = self.module._render(token)
        markup = self.module._markup(token)

        self.assertIn("HikkaNet", text)
        self.assertTrue(
            any(
                "HikkaNet" in button["text"]
                for row in markup
                for button in row
            )
        )


if __name__ == "__main__":
    unittest.main()
