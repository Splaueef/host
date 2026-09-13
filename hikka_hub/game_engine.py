"""Authoritative rules for HikkaNet turn-based games.

The service owns the canonical board and applies compact user actions.  Clients
never upload a replacement game state, so a stale or modified Hikka module
cannot award itself a win or overwrite an opponent's move.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SUPPORTED_GAMES = {"ttt", "checkers", "chess", "go9", "go13"}
CHECKER_DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
CHECKER_DRAW_PLY = 80
CHESS_KNIGHT_STEPS = (
    (-2, -1),
    (-2, 1),
    (-1, -2),
    (-1, 2),
    (1, -2),
    (1, 2),
    (2, -1),
    (2, 1),
)
CHESS_ORTHOGONAL = ((-1, 0), (1, 0), (0, -1), (0, 1))
CHESS_DIAGONAL = ((-1, -1), (-1, 1), (1, -1), (1, 1))
GO_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
GO_KOMI = 6.5
WIN_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


class GameRuleError(ValueError):
    """A safe, user-facing illegal-action error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def new_state(kind: str) -> dict[str, Any]:
    """Return the canonical initial JSON state for one supported game."""
    kind = str(kind).lower()
    if kind not in SUPPORTED_GAMES:
        raise GameRuleError("unsupported_game", "Unsupported game type")
    common = {
        "turn": 0,
        "winner": None,
        "draw": False,
        "finished": False,
        "finish_reason": None,
        "move_number": 0,
        "last_action": None,
    }
    if kind == "ttt":
        return {**common, "board": [None] * 9}
    if kind == "checkers":
        return {
            **common,
            "board": checker_initial_board(),
            "forced_piece": None,
            "quiet_ply": 0,
            "captures": [0, 0],
        }
    if kind == "chess":
        state = {
            **common,
            "board": chess_initial_board(),
            "castling": ["K", "Q", "k", "q"],
            "en_passant": None,
            "halfmove_clock": 0,
            "draw_reason": None,
            "position_counts": {},
        }
        state["position_counts"][chess_position_key(state)] = 1
        return state
    size = int(kind[2:])
    board = [0] * (size * size)
    return {
        **common,
        "go_size": size,
        "board": board,
        "captures": [0, 0],
        "consecutive_passes": 0,
        "last_move": None,
        "history": [go_board_key(board)],
        "scores": None,
        "territory": [0, 0],
    }


def apply_action(
    kind: str, state: dict[str, Any], action: dict[str, Any]
) -> dict[str, Any]:
    """Validate and apply one action, returning a new canonical state."""
    kind = str(kind).lower()
    if kind not in SUPPORTED_GAMES:
        raise GameRuleError("unsupported_game", "Unsupported game type")
    if not isinstance(state, dict) or state.get("finished"):
        raise GameRuleError("game_finished", "The game has already finished")
    if not isinstance(action, dict):
        raise GameRuleError("invalid_action", "Action must be a JSON object")
    result = deepcopy(state)
    if kind == "ttt":
        _apply_ttt(result, action)
    elif kind == "checkers":
        _apply_checkers(result, action)
    elif kind == "chess":
        _apply_chess(result, action)
    else:
        _apply_go(result, action)
    return result


def resign_state(state: dict[str, Any], loser_slot: int) -> dict[str, Any]:
    if not isinstance(state, dict) or state.get("finished"):
        raise GameRuleError("game_finished", "The game has already finished")
    loser_slot = int(loser_slot)
    if loser_slot not in {0, 1}:
        raise GameRuleError("not_participant", "Only a participant may resign")
    result = deepcopy(state)
    result.update(
        finished=True,
        draw=False,
        winner=1 - loser_slot,
        finish_reason="resignation",
        last_action=f"player {loser_slot + 1} resigned",
    )
    return result


def _action_type(action: dict[str, Any], expected: set[str]) -> str:
    value = str(action.get("type", "")).strip().lower()
    if value not in expected:
        raise GameRuleError("invalid_action", "Unsupported action for this game")
    return value


def _position(action: dict[str, Any], key: str, maximum: int) -> int:
    try:
        value = int(action[key])
    except (KeyError, TypeError, ValueError):
        raise GameRuleError("invalid_action", f"{key} must be an integer") from None
    if not 0 <= value < maximum:
        raise GameRuleError("invalid_action", f"Invalid {key}")
    return value


def _apply_ttt(state: dict[str, Any], action: dict[str, Any]) -> None:
    _action_type(action, {"place"})
    board = state.get("board")
    if not isinstance(board, list) or len(board) != 9:
        raise GameRuleError("corrupt_game", "Stored tic-tac-toe board is invalid")
    position = _position(action, "position", 9)
    if board[position] is not None:
        raise GameRuleError("occupied", "This cell is already occupied")
    side = int(state.get("turn", 0))
    board[position] = side
    state["move_number"] = int(state.get("move_number", 0)) + 1
    state["last_action"] = f"{position // 3 + 1}:{position % 3 + 1}"
    winner = _ttt_winner(board)
    if winner is not None:
        state.update(
            finished=True,
            winner=winner,
            draw=False,
            finish_reason="line",
        )
    elif all(value is not None for value in board):
        state.update(
            finished=True,
            winner=None,
            draw=True,
            finish_reason="board_full",
        )
    else:
        state["turn"] = 1 - side


def _ttt_winner(board: list[Any]) -> int | None:
    for first, second, third in WIN_LINES:
        if board[first] is not None and board[first] == board[second] == board[third]:
            return int(board[first])
    return None


def checker_initial_board() -> list[int]:
    board = [0] * 64
    for row in range(8):
        for column in range(8):
            if (row + column) % 2:
                if row < 3:
                    board[row * 8 + column] = -1
                elif row > 4:
                    board[row * 8 + column] = 1
    return board


def _checker_owned(piece: int, side: int) -> bool:
    return piece > 0 if int(side) == 0 else piece < 0


def _checker_captures(board: list[int], position: int) -> list[tuple[int, int]]:
    piece = board[int(position)]
    if not piece:
        return []
    row, column = divmod(int(position), 8)
    captures = []
    if abs(piece) == 1:
        for row_step, column_step in CHECKER_DIRECTIONS:
            middle_row, middle_column = row + row_step, column + column_step
            target_row, target_column = row + 2 * row_step, column + 2 * column_step
            if not (
                0 <= middle_row < 8
                and 0 <= middle_column < 8
                and 0 <= target_row < 8
                and 0 <= target_column < 8
            ):
                continue
            middle = middle_row * 8 + middle_column
            target = target_row * 8 + target_column
            if board[middle] and board[middle] * piece < 0 and not board[target]:
                captures.append((target, middle))
        return captures
    for row_step, column_step in CHECKER_DIRECTIONS:
        current_row, current_column = row + row_step, column + column_step
        captured = None
        while 0 <= current_row < 8 and 0 <= current_column < 8:
            target = current_row * 8 + current_column
            target_piece = board[target]
            if not target_piece:
                if captured is not None:
                    captures.append((target, captured))
            elif target_piece * piece > 0 or captured is not None:
                break
            else:
                captured = target
            current_row += row_step
            current_column += column_step
    return captures


def _checker_regular_moves(board: list[int], position: int) -> list[tuple[int, None]]:
    piece = board[int(position)]
    if not piece:
        return []
    row, column = divmod(int(position), 8)
    moves = []
    if abs(piece) == 1:
        directions = ((-1, -1), (-1, 1)) if piece > 0 else ((1, -1), (1, 1))
        for row_step, column_step in directions:
            target_row, target_column = row + row_step, column + column_step
            if 0 <= target_row < 8 and 0 <= target_column < 8:
                target = target_row * 8 + target_column
                if not board[target]:
                    moves.append((target, None))
        return moves
    for row_step, column_step in CHECKER_DIRECTIONS:
        current_row, current_column = row + row_step, column + column_step
        while 0 <= current_row < 8 and 0 <= current_column < 8:
            target = current_row * 8 + current_column
            if board[target]:
                break
            moves.append((target, None))
            current_row += row_step
            current_column += column_step
    return moves


def checker_all_captures(board: list[int], side: int) -> dict[int, list[tuple[int, int]]]:
    return {
        position: captures
        for position, piece in enumerate(board)
        if _checker_owned(piece, side)
        for captures in [_checker_captures(board, position)]
        if captures
    }


def checker_moves_for(
    board: list[int], side: int, position: int
) -> list[tuple[int, int | None]]:
    position = int(position)
    if not 0 <= position < 64 or not _checker_owned(board[position], side):
        return []
    captures = checker_all_captures(board, side)
    return captures.get(position, []) if captures else _checker_regular_moves(board, position)


def _checker_has_move(board: list[int], side: int) -> bool:
    if checker_all_captures(board, side):
        return True
    return any(
        _checker_regular_moves(board, position)
        for position, piece in enumerate(board)
        if _checker_owned(piece, side)
    )


def _checker_coordinate(position: int) -> str:
    row, column = divmod(int(position), 8)
    return f"{'abcdefgh'[column]}{8 - row}"


def _apply_checkers(state: dict[str, Any], action: dict[str, Any]) -> None:
    _action_type(action, {"move"})
    board = state.get("board")
    if (
        not isinstance(board, list)
        or len(board) != 64
        or any(type(piece) is not int or piece not in {-2, -1, 0, 1, 2} for piece in board)
    ):
        raise GameRuleError("corrupt_game", "Stored checkers board is invalid")
    source = _position(action, "source", 64)
    target = _position(action, "target", 64)
    side = int(state.get("turn", 0))
    forced = state.get("forced_piece")
    if forced is not None and source != int(forced):
        raise GameRuleError("forced_capture", "Continue the capture with the same piece")
    legal = dict(checker_moves_for(board, side, source))
    if target not in legal:
        raise GameRuleError("illegal_move", "This checkers move is not legal")
    captured = legal[target]
    piece = board[source]
    board[source] = 0
    board[target] = piece
    captures = list(state.get("captures", [0, 0]))
    if captured is not None:
        board[captured] = 0
        captures[side] = int(captures[side]) + 1
        state["quiet_ply"] = 0
    else:
        state["quiet_ply"] = int(state.get("quiet_ply", 0)) + 1
    target_row, _ = divmod(target, 8)
    if piece == 1 and target_row == 0:
        board[target] = 2
    elif piece == -1 and target_row == 7:
        board[target] = -2
    state["captures"] = captures
    state["move_number"] = int(state.get("move_number", 0)) + 1
    state["last_action"] = f"{_checker_coordinate(source)}–{_checker_coordinate(target)}"
    if captured is not None and _checker_captures(board, target):
        state["forced_piece"] = target
        return
    state["forced_piece"] = None
    opponent = 1 - side
    if not any(_checker_owned(value, opponent) for value in board) or not _checker_has_move(
        board, opponent
    ):
        state.update(
            finished=True,
            winner=side,
            draw=False,
            finish_reason="no_moves",
        )
    elif int(state.get("quiet_ply", 0)) >= CHECKER_DRAW_PLY:
        state.update(
            finished=True,
            winner=None,
            draw=True,
            finish_reason="quiet_limit",
        )
    else:
        state["turn"] = opponent


def chess_initial_board() -> list[str]:
    pieces = "rnbqkbnrpppppppp" + "." * 32 + "PPPPPPPPRNBQKBNR"
    return [piece if piece != "." else "" for piece in pieces]


def chess_side(piece: str) -> int | None:
    if not piece:
        return None
    return 0 if piece.isupper() else 1


def _chess_attacked(board: list[str], square: int, by_side: int) -> bool:
    row, column = divmod(int(square), 8)
    pawn = "P" if int(by_side) == 0 else "p"
    pawn_row = row + (1 if int(by_side) == 0 else -1)
    if 0 <= pawn_row < 8:
        for pawn_column in (column - 1, column + 1):
            if 0 <= pawn_column < 8 and board[pawn_row * 8 + pawn_column] == pawn:
                return True
    knight = "N" if int(by_side) == 0 else "n"
    for row_step, column_step in CHESS_KNIGHT_STEPS:
        source_row, source_column = row + row_step, column + column_step
        if (
            0 <= source_row < 8
            and 0 <= source_column < 8
            and board[source_row * 8 + source_column] == knight
        ):
            return True
    king = "K" if int(by_side) == 0 else "k"
    for row_step, column_step in CHESS_ORTHOGONAL + CHESS_DIAGONAL:
        source_row, source_column = row + row_step, column + column_step
        if (
            0 <= source_row < 8
            and 0 <= source_column < 8
            and board[source_row * 8 + source_column] == king
        ):
            return True
    for directions, attackers in (
        (CHESS_ORTHOGONAL, {"r", "q"}),
        (CHESS_DIAGONAL, {"b", "q"}),
    ):
        for row_step, column_step in directions:
            source_row, source_column = row + row_step, column + column_step
            while 0 <= source_row < 8 and 0 <= source_column < 8:
                piece = board[source_row * 8 + source_column]
                if piece:
                    if chess_side(piece) == int(by_side) and piece.lower() in attackers:
                        return True
                    break
                source_row += row_step
                source_column += column_step
    return False


def chess_in_check(board: list[str], side: int) -> bool:
    king = "K" if int(side) == 0 else "k"
    try:
        square = board.index(king)
    except ValueError:
        return True
    return _chess_attacked(board, square, 1 - int(side))


def _chess_pseudo_moves(
    board: list[str],
    side: int,
    position: int,
    castling: set[str] | None = None,
    en_passant: int | None = None,
) -> list[tuple[int, str | None]]:
    position = int(position)
    if not 0 <= position < 64:
        return []
    piece = board[position]
    side = int(side)
    if chess_side(piece) != side:
        return []
    row, column = divmod(position, 8)
    moves: list[tuple[int, str | None]] = []

    def add_if_available(target: int, special: str | None = None) -> None:
        target_piece = board[target]
        if not target_piece or (
            chess_side(target_piece) != side and target_piece.lower() != "k"
        ):
            moves.append((target, special))

    kind = piece.lower()
    if kind == "p":
        row_step = -1 if side == 0 else 1
        start_row = 6 if side == 0 else 1
        target_row = row + row_step
        if 0 <= target_row < 8:
            one_step = target_row * 8 + column
            if not board[one_step]:
                moves.append((one_step, None))
                two_row = row + row_step * 2
                if row == start_row and not board[two_row * 8 + column]:
                    moves.append((two_row * 8 + column, None))
            for target_column in (column - 1, column + 1):
                if not 0 <= target_column < 8:
                    continue
                target = target_row * 8 + target_column
                target_piece = board[target]
                if (
                    target_piece
                    and chess_side(target_piece) != side
                    and target_piece.lower() != "k"
                ):
                    moves.append((target, None))
                elif target == en_passant and not target_piece:
                    captured = row * 8 + target_column
                    expected = "p" if side == 0 else "P"
                    if board[captured] == expected:
                        moves.append((target, "en_passant"))
        return moves
    if kind == "n":
        for row_step, column_step in CHESS_KNIGHT_STEPS:
            target_row, target_column = row + row_step, column + column_step
            if 0 <= target_row < 8 and 0 <= target_column < 8:
                add_if_available(target_row * 8 + target_column)
        return moves
    if kind in {"b", "r", "q"}:
        directions = ()
        if kind in {"r", "q"}:
            directions += CHESS_ORTHOGONAL
        if kind in {"b", "q"}:
            directions += CHESS_DIAGONAL
        for row_step, column_step in directions:
            target_row, target_column = row + row_step, column + column_step
            while 0 <= target_row < 8 and 0 <= target_column < 8:
                target = target_row * 8 + target_column
                target_piece = board[target]
                if not target_piece:
                    moves.append((target, None))
                else:
                    if chess_side(target_piece) != side and target_piece.lower() != "k":
                        moves.append((target, None))
                    break
                target_row += row_step
                target_column += column_step
        return moves
    for row_step, column_step in CHESS_ORTHOGONAL + CHESS_DIAGONAL:
        target_row, target_column = row + row_step, column + column_step
        if 0 <= target_row < 8 and 0 <= target_column < 8:
            add_if_available(target_row * 8 + target_column)
    rights = castling or set()
    opponent = 1 - side
    if side == 0 and position == 60 and piece == "K":
        if (
            "K" in rights
            and board[63] == "R"
            and not board[61]
            and not board[62]
            and not any(_chess_attacked(board, square, opponent) for square in (60, 61, 62))
        ):
            moves.append((62, "castle_k"))
        if (
            "Q" in rights
            and board[56] == "R"
            and not board[57]
            and not board[58]
            and not board[59]
            and not any(_chess_attacked(board, square, opponent) for square in (60, 59, 58))
        ):
            moves.append((58, "castle_q"))
    elif side == 1 and position == 4 and piece == "k":
        if (
            "k" in rights
            and board[7] == "r"
            and not board[5]
            and not board[6]
            and not any(_chess_attacked(board, square, opponent) for square in (4, 5, 6))
        ):
            moves.append((6, "castle_k"))
        if (
            "q" in rights
            and board[0] == "r"
            and not board[1]
            and not board[2]
            and not board[3]
            and not any(_chess_attacked(board, square, opponent) for square in (4, 3, 2))
        ):
            moves.append((2, "castle_q"))
    return moves


def _chess_apply_to_board(
    board: list[str],
    source: int,
    target: int,
    special: str | None = None,
    promotion: str | None = None,
) -> tuple[str, str, int]:
    piece = board[source]
    captured_position = target
    captured = board[target]
    board[source] = ""
    if special == "en_passant":
        captured_position = target + 8 if piece.isupper() else target - 8
        captured = board[captured_position]
        board[captured_position] = ""
    board[target] = piece
    if special in {"castle_k", "castle_q"}:
        if piece == "K":
            rook_source, rook_target = (63, 61) if special == "castle_k" else (56, 59)
        else:
            rook_source, rook_target = (7, 5) if special == "castle_k" else (0, 3)
        board[rook_target] = board[rook_source]
        board[rook_source] = ""
    target_row, _ = divmod(target, 8)
    if piece.lower() == "p" and target_row in {0, 7} and promotion:
        board[target] = promotion.upper() if piece.isupper() else promotion
    return piece, captured, captured_position


def chess_legal_moves(
    board: list[str],
    side: int,
    position: int,
    castling: set[str] | None = None,
    en_passant: int | None = None,
) -> list[tuple[int, str | None]]:
    legal = []
    for target, special in _chess_pseudo_moves(
        board, side, position, castling, en_passant
    ):
        candidate = list(board)
        _chess_apply_to_board(candidate, int(position), target, special, promotion="q")
        if not chess_in_check(candidate, side):
            legal.append((target, special))
    return legal


def _chess_all_legal_moves(
    board: list[str], side: int, castling: set[str], en_passant: int | None
) -> dict[int, list[tuple[int, str | None]]]:
    result = {}
    for position, piece in enumerate(board):
        if chess_side(piece) == int(side):
            moves = chess_legal_moves(board, side, position, castling, en_passant)
            if moves:
                result[position] = moves
    return result


def chess_position_key(state: dict[str, Any]) -> str:
    board = "".join(piece or "." for piece in state["board"])
    rights = "".join(sorted(state.get("castling", []))) or "-"
    en_passant = state.get("en_passant")
    if en_passant is not None:
        side = int(state["turn"])
        target_row, target_column = divmod(int(en_passant), 8)
        source_row = target_row + (1 if side == 0 else -1)
        pawn = "P" if side == 0 else "p"
        can_capture = False
        if 0 <= source_row < 8:
            for source_column in (target_column - 1, target_column + 1):
                if not 0 <= source_column < 8:
                    continue
                source = source_row * 8 + source_column
                if state["board"][source] == pawn and any(
                    target == en_passant and special == "en_passant"
                    for target, special in chess_legal_moves(
                        state["board"], side, source, set(state.get("castling", [])), en_passant
                    )
                ):
                    can_capture = True
                    break
        if not can_capture:
            en_passant = None
    return f"{board}|{int(state['turn'])}|{rights}|{en_passant if en_passant is not None else '-'}"


def _chess_insufficient_material(board: list[str]) -> bool:
    remaining = [
        (position, piece)
        for position, piece in enumerate(board)
        if piece and piece.lower() != "k"
    ]
    if not remaining:
        return True
    if len(remaining) == 1 and remaining[0][1].lower() in {"b", "n"}:
        return True
    if remaining and all(piece.lower() == "b" for _, piece in remaining):
        return len({sum(divmod(position, 8)) % 2 for position, _ in remaining}) == 1
    return False


def _chess_update_castling(
    rights: set[str], piece: str, source: int, captured: str, captured_position: int
) -> None:
    if piece == "K":
        rights.difference_update({"K", "Q"})
    elif piece == "k":
        rights.difference_update({"k", "q"})
    rook_rights = {63: "K", 56: "Q", 7: "k", 0: "q"}
    if piece.lower() == "r":
        rights.discard(rook_rights.get(source, ""))
    if captured and captured.lower() == "r":
        rights.discard(rook_rights.get(captured_position, ""))


def _apply_chess(state: dict[str, Any], action: dict[str, Any]) -> None:
    _action_type(action, {"move"})
    board = state.get("board")
    valid_pieces = set("KQRBNPkqrbnp") | {""}
    if (
        not isinstance(board, list)
        or len(board) != 64
        or any(piece not in valid_pieces for piece in board)
    ):
        raise GameRuleError("corrupt_game", "Stored chess board is invalid")
    source = _position(action, "source", 64)
    target = _position(action, "target", 64)
    side = int(state.get("turn", 0))
    castling = set(state.get("castling", []))
    en_passant = state.get("en_passant")
    legal = dict(chess_legal_moves(board, side, source, castling, en_passant))
    if target not in legal:
        raise GameRuleError("illegal_move", "This chess move is not legal")
    piece = board[source]
    target_row, _ = divmod(target, 8)
    promotion = str(action.get("promotion", "q")).lower()
    if piece.lower() == "p" and target_row in {0, 7}:
        if promotion not in {"q", "r", "b", "n"}:
            raise GameRuleError("invalid_promotion", "Choose q, r, b or n")
    else:
        promotion = None
    special = legal[target]
    piece, captured, captured_position = _chess_apply_to_board(
        board, source, target, special, promotion
    )
    _chess_update_castling(castling, piece, source, captured, captured_position)
    state["castling"] = sorted(castling)
    state["en_passant"] = (
        (source + target) // 2
        if piece.lower() == "p" and abs(source - target) == 16
        else None
    )
    state["halfmove_clock"] = (
        0
        if piece.lower() == "p" or captured
        else int(state.get("halfmove_clock", 0)) + 1
    )
    capture_mark = "×" if captured else "–"
    promoted = f"={promotion.upper()}" if promotion else ""
    state["last_action"] = (
        f"{piece.upper()} {chess_coordinate(source)}{capture_mark}"
        f"{chess_coordinate(target)}{promoted}"
    )
    state["move_number"] = int(state.get("move_number", 0)) + 1
    opponent = 1 - side
    state["turn"] = opponent
    counts = dict(state.get("position_counts", {}))
    key = chess_position_key(state)
    counts[key] = int(counts.get(key, 0)) + 1
    state["position_counts"] = counts
    moves = _chess_all_legal_moves(board, opponent, castling, state["en_passant"])
    if not moves:
        if chess_in_check(board, opponent):
            state.update(
                finished=True,
                winner=side,
                draw=False,
                finish_reason="checkmate",
                draw_reason=None,
            )
        else:
            state.update(
                finished=True,
                winner=None,
                draw=True,
                finish_reason="stalemate",
                draw_reason="stalemate",
            )
        return
    draw_reason = None
    if _chess_insufficient_material(board):
        draw_reason = "material"
    elif int(state["halfmove_clock"]) >= 100:
        draw_reason = "fifty_moves"
    elif counts[key] >= 3:
        draw_reason = "repetition"
    if draw_reason:
        state.update(
            finished=True,
            winner=None,
            draw=True,
            finish_reason=draw_reason,
            draw_reason=draw_reason,
        )


def chess_coordinate(position: int) -> str:
    row, column = divmod(int(position), 8)
    return f"{'abcdefgh'[column]}{8 - row}"


def go_board_key(board: list[int]) -> str:
    return "".join("b" if stone == 1 else "w" if stone == -1 else "." for stone in board)


def _go_neighbors(position: int, size: int) -> list[int]:
    row, column = divmod(int(position), int(size))
    result = []
    for row_step, column_step in CHESS_ORTHOGONAL:
        target_row, target_column = row + row_step, column + column_step
        if 0 <= target_row < size and 0 <= target_column < size:
            result.append(target_row * size + target_column)
    return result


def _go_group_and_liberties(
    board: list[int], size: int, start: int
) -> tuple[set[int], set[int]]:
    stone = board[int(start)]
    if not stone:
        return set(), set()
    group, liberties, stack = set(), set(), [int(start)]
    while stack:
        position = stack.pop()
        if position in group:
            continue
        group.add(position)
        for neighbor in _go_neighbors(position, size):
            if not board[neighbor]:
                liberties.add(neighbor)
            elif board[neighbor] == stone and neighbor not in group:
                stack.append(neighbor)
    return group, liberties


def _go_try_move(
    board: list[int], size: int, side: int, position: int, history: set[str]
) -> tuple[list[int], int]:
    if board[position]:
        raise GameRuleError("occupied", "This point is already occupied")
    stone = 1 if int(side) == 0 else -1
    candidate = list(board)
    candidate[position] = stone
    captured_positions = set()
    for neighbor in _go_neighbors(position, size):
        if candidate[neighbor] != -stone:
            continue
        group, liberties = _go_group_and_liberties(candidate, size, neighbor)
        if not liberties:
            captured_positions.update(group)
            for captured in group:
                candidate[captured] = 0
    _, liberties = _go_group_and_liberties(candidate, size, position)
    if not liberties:
        raise GameRuleError("suicide", "Suicidal moves are not allowed")
    if go_board_key(candidate) in history:
        raise GameRuleError("ko", "This move repeats an earlier position")
    return candidate, len(captured_positions)


def _go_score(board: list[int], size: int) -> tuple[list[float], list[int]]:
    stones = [sum(stone == 1 for stone in board), sum(stone == -1 for stone in board)]
    territory = [0, 0]
    visited = set()
    for start, stone in enumerate(board):
        if stone or start in visited:
            continue
        region, borders, stack = set(), set(), [start]
        while stack:
            position = stack.pop()
            if position in region:
                continue
            region.add(position)
            for neighbor in _go_neighbors(position, size):
                neighbor_stone = board[neighbor]
                if not neighbor_stone and neighbor not in region:
                    stack.append(neighbor)
                elif neighbor_stone:
                    borders.add(neighbor_stone)
        visited.update(region)
        if borders == {1}:
            territory[0] += len(region)
        elif borders == {-1}:
            territory[1] += len(region)
    return [stones[0] + territory[0], stones[1] + territory[1] + GO_KOMI], territory


def _finish_go(state: dict[str, Any]) -> None:
    scores, territory = _go_score(state["board"], int(state["go_size"]))
    state["scores"] = scores
    state["territory"] = territory
    state["finished"] = True
    state["finish_reason"] = "score"
    if scores[0] == scores[1]:
        state.update(winner=None, draw=True)
    else:
        state.update(winner=0 if scores[0] > scores[1] else 1, draw=False)


def _apply_go(state: dict[str, Any], action: dict[str, Any]) -> None:
    action_type = _action_type(action, {"place", "pass"})
    size = int(state.get("go_size", 0))
    board = state.get("board")
    if (
        size not in {9, 13}
        or not isinstance(board, list)
        or len(board) != size * size
        or any(type(stone) is not int or stone not in {-1, 0, 1} for stone in board)
    ):
        raise GameRuleError("corrupt_game", "Stored Go board is invalid")
    side = int(state.get("turn", 0))
    state["move_number"] = int(state.get("move_number", 0)) + 1
    if action_type == "pass":
        state["consecutive_passes"] = int(state.get("consecutive_passes", 0)) + 1
        state["last_move"] = None
        state["last_action"] = "black passed" if side == 0 else "white passed"
        if state["consecutive_passes"] >= 2:
            _finish_go(state)
        else:
            state["turn"] = 1 - side
        return
    position = _position(action, "position", size * size)
    history = set(state.get("history", []))
    candidate, captured = _go_try_move(board, size, side, position, history)
    key = go_board_key(candidate)
    history.add(key)
    captures = list(state.get("captures", [0, 0]))
    captures[side] = int(captures[side]) + captured
    state["board"] = candidate
    state["history"] = list(history)
    state["captures"] = captures
    state["consecutive_passes"] = 0
    state["last_move"] = position
    row, column = divmod(position, size)
    state["last_action"] = f"{GO_COLUMNS[column]}{size - row}"
    if all(candidate):
        _finish_go(state)
    else:
        state["turn"] = 1 - side
