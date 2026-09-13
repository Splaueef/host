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
    loader.validators = types.SimpleNamespace(Integer=lambda **kwargs: object())
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


class _Message:
    def __init__(self):
        self.chat_id = -100
        self.args = ""
        self.answers = []

    async def get_reply_message(self):
        return None


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

    def test_checkers_board_uses_visible_emoji_cells(self):
        token = self.module._new_session("checkers", -100)
        markup = self.module._markup(token)

        self.assertEqual(len(markup[:8]), 8)
        self.assertTrue(all(len(row) == 8 for row in markup[:8]))
        self.assertEqual(markup[0][0]["text"], "🟨")
        self.assertEqual(markup[0][1]["text"], "⚫")
        self.assertEqual(markup[3][0]["text"], "🟫")
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


if __name__ == "__main__":
    unittest.main()
