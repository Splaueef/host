"""Rule-level tests for authoritative Hikka Hub games."""

import unittest

from hikka_hub.game_engine import GameRuleError, apply_action, new_state


class HikkaHubGameEngineTests(unittest.TestCase):
    def test_checkers_enforces_capture_and_keeps_turn_for_chain(self):
        state = new_state("checkers")
        state["board"] = [0] * 64
        state["board"][33] = 1
        state["board"][26] = -1
        state["board"][12] = -1

        state = apply_action(
            "checkers", state, {"type": "move", "source": 33, "target": 19}
        )
        self.assertEqual(state["turn"], 0)
        self.assertEqual(state["forced_piece"], 19)
        self.assertEqual(state["captures"], [1, 0])

        with self.assertRaises(GameRuleError) as caught:
            apply_action(
                "checkers", state, {"type": "move", "source": 19, "target": 28}
            )
        self.assertEqual(caught.exception.code, "illegal_move")

        state = apply_action(
            "checkers", state, {"type": "move", "source": 19, "target": 5}
        )
        self.assertTrue(state["finished"])
        self.assertEqual(state["winner"], 0)
        self.assertEqual(state["board"][5], 2)

    def test_chess_fools_mate_is_server_verified(self):
        state = new_state("chess")
        for source, target in ((53, 45), (12, 28), (54, 38), (3, 39)):
            state = apply_action(
                "chess",
                state,
                {"type": "move", "source": source, "target": target},
            )

        self.assertTrue(state["finished"])
        self.assertEqual(state["winner"], 1)
        self.assertEqual(state["finish_reason"], "checkmate")

    def test_chess_rejects_a_move_that_exposes_king(self):
        state = new_state("chess")
        state["board"] = [""] * 64
        state["board"][0] = "k"
        state["board"][4] = "r"
        state["board"][52] = "R"
        state["board"][60] = "K"
        state["castling"] = []
        state["position_counts"] = {}

        with self.assertRaises(GameRuleError) as caught:
            apply_action(
                "chess", state, {"type": "move", "source": 52, "target": 51}
            )
        self.assertEqual(caught.exception.code, "illegal_move")

    def test_go_capture_suicide_and_two_pass_finish(self):
        state = new_state("go9")
        center = 4 * 9 + 4
        state["board"][center] = -1
        for row, column in ((3, 4), (5, 4), (4, 3)):
            state["board"][row * 9 + column] = 1
        state["history"] = []
        state = apply_action(
            "go9", state, {"type": "place", "position": 4 * 9 + 5}
        )
        self.assertEqual(state["board"][center], 0)
        self.assertEqual(state["captures"], [1, 0])

        state = apply_action("go9", state, {"type": "pass"})
        state = apply_action("go9", state, {"type": "pass"})
        self.assertTrue(state["finished"])
        self.assertIsNotNone(state["winner"])
        self.assertEqual(state["finish_reason"], "score")


if __name__ == "__main__":
    unittest.main()
