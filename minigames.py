# meta developer: @Huai_Baike
# meta version: 1.4.0
# meta description: Інтерактивні мініігри для чатів із кнопками та рейтингом
# scope: inline
# scope: hikka_only

__version__ = (1, 4, 0)

import asyncio
import contextlib
import html
import random
import secrets
import time

from .. import loader, utils


SESSION_TTL = 30 * 60
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
CHECKER_DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
CHECKER_SYMBOLS = {1: "⚪", 2: "⚪👑", -1: "⚫", -2: "⚫👑"}
CHECKER_LIGHT_CELL = "∙"
CHECKER_DARK_CELL = "∙"
CHECKER_SELECTED_CELL = "🟦"
CHECKER_TARGET_CELL = "🟩"
CHECKER_DRAW_PLY = 80
CHESS_SYMBOLS = {
    "K": "♔",
    "Q": "♕",
    "R": "♖",
    "B": "♗",
    "N": "♘",
    "P": "♙",
    "k": "♚",
    "q": "♛",
    "r": "♜",
    "b": "♝",
    "n": "♞",
    "p": "♟",
}
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
CHESS_EMPTY_CELL = "∙"
CHESS_SELECTED_CELL = "🟦"
CHESS_TARGET_CELL = "🟩"
CHESS_CAPTURE_CELL = "🟥"
GO_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
GO_STONES = {1: "●", -1: "○"}
GO_LAST_STONES = {1: "◆", -1: "◇"}
GO_KOMI = 6.5


@loader.tds
class MiniGamesMod(loader.Module):
    """Інтерактивні ігри для двох гравців і всього чату."""

    strings = {
        "name": "MiniGames",
        "inline_failed": "❌ Не вдалося відкрити гру. Перевірте inline-бота Hikka.",
        "expired": "⌛ Ця гра вже завершилась або застаріла. Запустіть нову через <code>.games</code>.",
        "not_yours": "Ця кнопка зараз не для вас 🙂",
        "already_played": "Ви вже зробили свій хід.",
        "wait_opponent": "Спочатку хід має зробити творець гри.",
        "closed": "🎮 <b>Гру закрито.</b>",
    }

    RPS = {
        "rock": ("🪨", "Камінь"),
        "paper": ("📄", "Папір"),
        "scissors": ("✂️", "Ножиці"),
    }
    RPS_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

    QUIZ_BANK = (
        ("Яка планета найближча до Сонця?", ("Венера", "Марс", "Меркурій", "Земля"), 2),
        ("Скільки водневих зв'язків утворює пара G–C у ДНК?", ("1", "2", "3", "4"), 2),
        ("Столиця Австралії?", ("Сідней", "Канберра", "Мельбурн", "Перт"), 1),
        ("Який океан найбільший?", ("Атлантичний", "Індійський", "Тихий", "Північний Льодовитий"), 2),
        ("Скільки хвилин у двох годинах?", ("100", "110", "120", "140"), 2),
        ("Яка річка довша?", ("Дунай", "Ніл", "Рейн", "Ельба"), 1),
        ("Хімічний символ золота?", ("Ag", "Au", "Fe", "Gd"), 1),
        ("Скільки сторін має шестикутник?", ("5", "6", "7", "8"), 1),
        ("Яка мова має розширення файлів .py?", ("Python", "PHP", "Perl", "Pascal"), 0),
        ("Яке число є простим?", ("21", "27", "29", "33"), 2),
        ("Столиця Данії?", ("Осло", "Стокгольм", "Копенгаген", "Орхус"), 2),
        ("Скільки бітів у байті?", ("4", "8", "16", "32"), 1),
        ("Хто написав «Кобзар»?", ("Франко", "Шевченко", "Леся Українка", "Коцюбинський"), 1),
        ("Який газ переважає в атмосфері Землі?", ("Кисень", "Азот", "Вуглекислий газ", "Водень"), 1),
        ("Чому дорівнює 9 × 7?", ("54", "56", "63", "72"), 2),
        ("Яка країна має форму чобота?", ("Іспанія", "Греція", "Італія", "Португалія"), 2),
    )

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "quiz_rounds",
                5,
                lambda: "Кількість запитань в одній вікторині",
                validator=loader.validators.Integer(minimum=3, maximum=10),
            ),
        )
        self._client = None
        self._me_id = None
        self._me_name = "Гравець"
        self._sessions = {}
        self._locks = {}
        self._rng = random.SystemRandom()

    async def client_ready(self, client, db):
        self._client = client
        me = await client.get_me()
        self._me_id = int(me.id)
        self._me_name = self._display_name(me)

    @staticmethod
    def _display_name(user):
        name = " ".join(
            value
            for value in (
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            )
            if value
        ).strip()
        username = getattr(user, "username", None)
        return name or (f"@{username}" if username else str(getattr(user, "id", "Гравець")))

    @staticmethod
    def _chat_id(message):
        with contextlib.suppress(Exception):
            return int(utils.get_chat_id(message))
        return int(getattr(message, "chat_id", 0) or 0)

    @staticmethod
    def _actor(call):
        user = getattr(call, "from_user", None)
        return int(getattr(user, "id", 0) or 0), user

    def _purge_sessions(self):
        now = time.monotonic()
        for token, session in list(self._sessions.items()):
            if now - session["created_at"] > SESSION_TTL:
                self._sessions.pop(token, None)
                self._locks.pop(token, None)

    def _session(self, token):
        self._purge_sessions()
        return self._sessions.get(token)

    def _new_session(self, kind, chat_id, invited=None):
        self._purge_sessions()
        token = secrets.token_urlsafe(7)
        invited_id = int(invited.id) if invited is not None else None
        names = {str(self._me_id): self._me_name}
        if invited is not None:
            names[str(invited_id)] = self._display_name(invited)
        self._sessions[token] = {
            "kind": kind,
            "chat_id": int(chat_id),
            "creator_id": int(self._me_id),
            "players": [int(self._me_id), invited_id],
            "invited_id": invited_id,
            "names": names,
            "created_at": time.monotonic(),
            "finished": False,
        }
        self._locks[token] = asyncio.Lock()
        self._reset_game(self._sessions[token], kind)
        return token

    def _reset_game(self, session, kind=None):
        kind = kind or session["kind"]
        session["kind"] = kind
        session["finished"] = False
        session["created_at"] = time.monotonic()
        if kind == "ttt":
            session.update(board=[None] * 9, turn=0, winner=None, draw=False)
        elif kind == "rps":
            session.update(choices={}, winner=None, draw=False)
        elif kind == "dice":
            session.update(rolls={}, winner=None, draw=False)
        elif kind == "checkers":
            session.update(
                board=self._checker_initial_board(),
                turn=0,
                selected=None,
                forced_piece=None,
                winner=None,
                draw=False,
                quiet_ply=0,
                captures=[0, 0],
            )
        elif kind == "chess":
            session.update(
                board=self._chess_initial_board(),
                turn=0,
                selected=None,
                winner=None,
                draw=False,
                draw_reason=None,
                finish_reason=None,
                castling={"K", "Q", "k", "q"},
                en_passant=None,
                halfmove_clock=0,
                promotion=None,
                last_move=None,
                position_counts={},
            )
            key = self._chess_position_key(session)
            session["position_counts"][key] = 1
        elif kind in {"go9", "go13"}:
            size = int(kind[2:])
            board = [0] * (size * size)
            session.update(
                go_size=size,
                board=board,
                turn=0,
                selected_row=None,
                winner=None,
                draw=False,
                finish_reason=None,
                captures=[0, 0],
                consecutive_passes=0,
                move_number=0,
                last_move=None,
                last_action=None,
                history={self._go_board_key(board)},
                scores=None,
                territory=[0, 0],
            )
        elif kind == "quiz":
            rounds = min(int(self.config["quiz_rounds"]), len(self.QUIZ_BANK))
            session.update(
                questions=self._rng.sample(list(self.QUIZ_BANK), rounds),
                question_index=0,
                scores={},
                wrong=set(),
                last_winner=None,
            )

    def _name(self, session, user_id):
        return html.escape(session["names"].get(str(user_id), f"ID {user_id}"))

    def _remember_actor(self, session, user_id, user):
        if user_id and user is not None:
            session["names"][str(user_id)] = self._display_name(user)

    async def _resolve_invited(self, message):
        reply = None
        with contextlib.suppress(Exception):
            reply = await message.get_reply_message()
        target = getattr(reply, "sender_id", None) if reply is not None else None
        if target is None:
            raw = str(utils.get_args_raw(message) or "").strip()
            target = raw.split(maxsplit=1)[0] if raw else None
        if target is None:
            return None
        try:
            entity = await self._client.get_entity(
                int(target) if str(target).lstrip("-").isdigit() else target
            )
        except Exception:
            await utils.answer(message, f"❌ Не вдалося знайти гравця: <code>{html.escape(str(target))}</code>")
            return False
        if int(entity.id) == int(self._me_id):
            await utils.answer(message, "❌ Для гри потрібен інший гравець.")
            return False
        if getattr(entity, "bot", False):
            await utils.answer(message, "❌ Боти не можуть бути суперниками в цій грі.")
            return False
        return entity

    async def _open(self, message, kind="menu", invited=None):
        token = self._new_session(kind, self._chat_id(message), invited)
        try:
            opened = await self.inline.form(
                self._render(token),
                message,
                reply_markup=self._markup(token),
                disable_security=True,
            )
        except Exception:
            opened = False
        if not opened:
            self._sessions.pop(token, None)
            self._locks.pop(token, None)
            await utils.answer(message, self.strings["inline_failed"])

    def _render(self, token):
        session = self._session(token)
        if session is None:
            return self.strings["expired"]
        renderer = getattr(self, f"_render_{session['kind']}")
        return renderer(session)

    def _markup(self, token):
        session = self._session(token)
        if session is None:
            return []
        builder = getattr(self, f"_markup_{session['kind']}")
        return builder(token, session)

    def _render_menu(self, session):
        return (
            "🎮 <b>MiniGames · ігрова кімната</b>\n\n"
            "❌⭕ <b>Хрестики-нулики</b> — класика для двох\n"
            "🪨📄✂️ <b>Камінь, ножиці, папір</b> — вибір прихований\n"
            "🎲 <b>Кубик-дуель</b> — найбільше число перемагає\n"
            "⚪⚫ <b>Шашки</b> — обов'язкове взяття та дамки\n"
            "♟ <b>Шахи</b> — повні правила, шах, мат і пат\n"
            "⚫⚪ <b>Ґо</b> — дошки 9×9 і 13×13\n"
            "🧠 <b>Вікторина</b> — перший правильний отримує бал\n\n"
            "<i>Ігри відкриті для учасників поточного чату.</i>"
        )

    def _markup_menu(self, token, session):
        return [
            [
                {"text": "❌⭕ Хрестики-нулики", "callback": self._select_game, "args": (token, "ttt")},
            ],
            [
                {"text": "🪨📄✂️ КНП", "callback": self._select_game, "args": (token, "rps")},
                {"text": "🎲 Кубик", "callback": self._select_game, "args": (token, "dice")},
            ],
            [
                {"text": "⚪⚫ Шашки", "callback": self._select_game, "args": (token, "checkers")},
                {"text": "♟ Шахи", "callback": self._select_game, "args": (token, "chess")},
            ],
            [
                {"text": "⚫⚪ Ґо 9×9", "callback": self._select_game, "args": (token, "go9")},
                {"text": "⚫⚪ Ґо 13×13", "callback": self._select_game, "args": (token, "go13")},
            ],
            [
                {"text": "🧠 Вікторина", "callback": self._select_game, "args": (token, "quiz")},
                {"text": "🏆 Рейтинг", "callback": self._show_top, "args": (token,)},
            ],
            [{"text": "✖️ Закрити", "callback": self._close, "args": (token,)}],
        ]

    async def _select_game(self, call, token, kind):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id != session["creator_id"]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        session["players"] = [session["creator_id"], None]
        session["invited_id"] = None
        self._reset_game(session, kind)
        await call.edit(self._render(token), reply_markup=self._markup(token))

    @staticmethod
    def _winner_ttt(board):
        for first, second, third in WIN_LINES:
            if board[first] is not None and board[first] == board[second] == board[third]:
                return board[first]
        return None

    def _render_ttt(self, session):
        first, second = session["players"]
        second_name = self._name(session, second) if second else "очікується суперник"
        text = (
            "❌⭕ <b>Хрестики-нулики</b>\n\n"
            f"❌ {self._name(session, first)}\n"
            f"⭕ {second_name}\n\n"
        )
        if session["winner"] is not None:
            winner_id = session["players"][session["winner"]]
            text += f"🏆 Переміг: <b>{self._name(session, winner_id)}</b>"
        elif session["draw"]:
            text += "🤝 <b>Нічия!</b>"
        else:
            turn_id = session["players"][session["turn"]]
            turn = self._name(session, turn_id) if turn_id else "другого гравця"
            text += f"Хід: <b>{turn}</b>"
        return text

    def _markup_ttt(self, token, session):
        rows = []
        for row in range(3):
            buttons = []
            for index in range(row * 3, row * 3 + 3):
                value = session["board"][index]
                buttons.append(
                    {
                        "text": "❌" if value == 0 else "⭕" if value == 1 else "·",
                        "callback": self._ttt_move,
                        "args": (token, index),
                    }
                )
            rows.append(buttons)
        rows.extend(self._footer(token, session))
        return rows

    async def _ttt_move(self, call, token, index):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Гру вже завершено")
                return
            if session["players"][1] is None and session["turn"] == 1 and user_id != session["players"][0]:
                if session.get("invited_id") and user_id != session["invited_id"]:
                    await call.answer(self.strings["not_yours"], show_alert=True)
                    return
                session["players"][1] = user_id
                self._remember_actor(session, user_id, user)
            current = session["players"][session["turn"]]
            if user_id != current:
                await call.answer(
                    self.strings["wait_opponent"] if session["players"][1] is None else self.strings["not_yours"],
                    show_alert=True,
                )
                return
            index = int(index)
            if not 0 <= index < 9 or session["board"][index] is not None:
                await call.answer("Ця клітинка вже зайнята")
                return
            session["board"][index] = session["turn"]
            winner = self._winner_ttt(session["board"])
            if winner is not None:
                session["winner"] = winner
                session["finished"] = True
                self._record_result(session, [session["players"][winner]])
            elif all(value is not None for value in session["board"]):
                session["draw"] = True
                session["finished"] = True
                self._record_result(session, [], draw=True)
            else:
                session["turn"] = 1 - session["turn"]
            await call.edit(self._render(token), reply_markup=self._markup(token))

    @staticmethod
    def _checker_initial_board():
        board = [0] * 64
        for row in range(8):
            for column in range(8):
                if (row + column) % 2 == 0:
                    continue
                if row < 3:
                    board[row * 8 + column] = -1
                elif row > 4:
                    board[row * 8 + column] = 1
        return board

    @staticmethod
    def _checker_position(index):
        index = int(index)
        return divmod(index, 8)

    @staticmethod
    def _checker_index(row, column):
        return row * 8 + column

    @staticmethod
    def _checker_coordinate(index):
        row, column = divmod(int(index), 8)
        return f"{'abcdefgh'[column]}{8 - row}"

    @staticmethod
    def _checker_owned(piece, side):
        return piece > 0 if int(side) == 0 else piece < 0

    @classmethod
    def _checker_captures(cls, board, position):
        position = int(position)
        piece = board[position]
        if not piece:
            return []
        row, column = cls._checker_position(position)
        captures = []
        if abs(piece) == 1:
            for row_step, column_step in CHECKER_DIRECTIONS:
                middle_row = row + row_step
                middle_column = column + column_step
                target_row = row + row_step * 2
                target_column = column + column_step * 2
                if not (
                    0 <= middle_row < 8
                    and 0 <= middle_column < 8
                    and 0 <= target_row < 8
                    and 0 <= target_column < 8
                ):
                    continue
                middle = cls._checker_index(middle_row, middle_column)
                target = cls._checker_index(target_row, target_column)
                if board[middle] and board[middle] * piece < 0 and board[target] == 0:
                    captures.append((target, middle))
            return captures

        for row_step, column_step in CHECKER_DIRECTIONS:
            current_row = row + row_step
            current_column = column + column_step
            captured = None
            while 0 <= current_row < 8 and 0 <= current_column < 8:
                target = cls._checker_index(current_row, current_column)
                target_piece = board[target]
                if target_piece == 0:
                    if captured is not None:
                        captures.append((target, captured))
                elif target_piece * piece > 0 or captured is not None:
                    break
                else:
                    captured = target
                current_row += row_step
                current_column += column_step
        return captures

    @classmethod
    def _checker_regular_moves(cls, board, position):
        position = int(position)
        piece = board[position]
        if not piece:
            return []
        row, column = cls._checker_position(position)
        moves = []
        if abs(piece) == 1:
            directions = ((-1, -1), (-1, 1)) if piece > 0 else ((1, -1), (1, 1))
            for row_step, column_step in directions:
                target_row = row + row_step
                target_column = column + column_step
                if 0 <= target_row < 8 and 0 <= target_column < 8:
                    target = cls._checker_index(target_row, target_column)
                    if board[target] == 0:
                        moves.append((target, None))
            return moves

        for row_step, column_step in CHECKER_DIRECTIONS:
            current_row = row + row_step
            current_column = column + column_step
            while 0 <= current_row < 8 and 0 <= current_column < 8:
                target = cls._checker_index(current_row, current_column)
                if board[target] != 0:
                    break
                moves.append((target, None))
                current_row += row_step
                current_column += column_step
        return moves

    @classmethod
    def _checker_all_captures(cls, board, side):
        result = {}
        for position, piece in enumerate(board):
            if not cls._checker_owned(piece, side):
                continue
            captures = cls._checker_captures(board, position)
            if captures:
                result[position] = captures
        return result

    @classmethod
    def _checker_moves_for(cls, board, side, position):
        if not 0 <= int(position) < 64 or not cls._checker_owned(board[int(position)], side):
            return []
        captures = cls._checker_all_captures(board, side)
        if captures:
            return captures.get(int(position), [])
        return cls._checker_regular_moves(board, int(position))

    @classmethod
    def _checker_has_move(cls, board, side):
        if cls._checker_all_captures(board, side):
            return True
        return any(
            cls._checker_regular_moves(board, position)
            for position, piece in enumerate(board)
            if cls._checker_owned(piece, side)
        )

    def _render_checkers(self, session):
        white, black = session["players"]
        black_name = self._name(session, black) if black else "очікується суперник"
        white_count = sum(1 for piece in session["board"] if piece > 0)
        black_count = sum(1 for piece in session["board"] if piece < 0)
        lines = [
            "⚪⚫ <b>Шашки · 8×8</b>",
            "",
            f"⚪ {self._name(session, white)} — <b>{white_count}</b>",
            f"⚫ {black_name} — <b>{black_count}</b>",
            "",
        ]
        if session["winner"] is not None:
            lines.append(f"🏆 Переміг: <b>{self._name(session, session['winner'])}</b>")
        elif session["draw"]:
            lines.append("🤝 <b>Нічия: 40 ходів без взяття.</b>")
        else:
            turn_id = session["players"][session["turn"]]
            turn_name = self._name(session, turn_id) if turn_id else "другого гравця"
            lines.append(f"Хід: <b>{turn_name}</b>")
            if session["selected"] is not None:
                lines.append(
                    f"🔸 Обрано: <b>{self._checker_coordinate(session['selected'])}</b> · "
                    "натисніть 🟩"
                )
            if session["forced_piece"] is not None:
                lines.append("⚔️ <b>Продовжуйте серію взяття.</b>")
            elif self._checker_all_captures(session["board"], session["turn"]):
                lines.append("⚔️ <b>Є обов'язкове взяття.</b>")
        lines.extend(
            (
                "",
                "<i>∙ — порожня клітинка · ⚪/⚫ — шашки · 👑 — дамка</i>",
                "<i>🟦 — вибрано · 🟩 — доступний хід</i>",
            )
        )
        return "\n".join(lines)

    def _markup_checkers(self, token, session):
        legal_targets = set()
        if session["selected"] is not None and not session["finished"]:
            legal_targets = {
                target
                for target, _ in self._checker_moves_for(
                    session["board"], session["turn"], session["selected"]
                )
            }
        rows = []
        for row in range(8):
            buttons = []
            for column in range(8):
                position = self._checker_index(row, column)
                piece = session["board"][position]
                if position == session["selected"]:
                    symbol = CHECKER_SELECTED_CELL
                elif position in legal_targets:
                    symbol = CHECKER_TARGET_CELL
                elif piece:
                    symbol = CHECKER_SYMBOLS[piece]
                else:
                    symbol = (
                        CHECKER_DARK_CELL
                        if (row + column) % 2
                        else CHECKER_LIGHT_CELL
                    )
                buttons.append(
                    {
                        "text": symbol,
                        "callback": self._checker_click,
                        "args": (token, position),
                    }
                )
            rows.append(buttons)
        if session["finished"]:
            rows.extend(self._footer(token, session))
        else:
            rows.append(
                [
                    {"text": "🏳 Здатися", "callback": self._checker_resign, "args": (token,)},
                    {"text": "✖️ Закрити", "callback": self._close, "args": (token,)},
                ]
            )
        return rows

    async def _checker_click(self, call, token, position):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Гру вже завершено")
                return
            position = int(position)
            if not 0 <= position < 64:
                await call.answer("Некоректна клітинка")
                return

            side = int(session["turn"])
            board = session["board"]
            if session["players"][1] is None:
                can_join = (
                    side == 1
                    and user_id != session["players"][0]
                    and self._checker_owned(board[position], 1)
                    and bool(self._checker_moves_for(board, 1, position))
                )
                if can_join:
                    if session.get("invited_id") and user_id != session["invited_id"]:
                        await call.answer(self.strings["not_yours"], show_alert=True)
                        return
                    session["players"][1] = user_id
                    self._remember_actor(session, user_id, user)

            current = session["players"][side]
            if user_id != current:
                await call.answer(
                    self.strings["wait_opponent"]
                    if session["players"][1] is None
                    else self.strings["not_yours"],
                    show_alert=True,
                )
                return

            selected = session["selected"]
            clicked_piece = board[position]
            if self._checker_owned(clicked_piece, side):
                if session["forced_piece"] is not None and position != session["forced_piece"]:
                    await call.answer("⚔️ Потрібно продовжити взяття цією ж шашкою", show_alert=True)
                    return
                moves = self._checker_moves_for(board, side, position)
                if not moves:
                    message = (
                        "⚔️ Треба бити іншою шашкою"
                        if self._checker_all_captures(board, side)
                        else "У цієї шашки немає доступних ходів"
                    )
                    await call.answer(message, show_alert=True)
                    return
                session["selected"] = position
                await call.edit(self._render(token), reply_markup=self._markup(token))
                return

            if selected is None:
                await call.answer("Спочатку оберіть свою шашку", show_alert=True)
                return
            legal = {
                target: captured
                for target, captured in self._checker_moves_for(board, side, selected)
            }
            if position not in legal:
                await call.answer("Ця клітинка недоступна", show_alert=True)
                return

            captured = legal[position]
            piece = board[selected]
            board[selected] = 0
            board[position] = piece
            if captured is not None:
                board[captured] = 0
                session["captures"][side] += 1
                session["quiet_ply"] = 0
            else:
                session["quiet_ply"] += 1

            target_row, _ = self._checker_position(position)
            if piece == 1 and target_row == 0:
                board[position] = 2
            elif piece == -1 and target_row == 7:
                board[position] = -2

            if captured is not None and self._checker_captures(board, position):
                session["selected"] = position
                session["forced_piece"] = position
                await call.answer("⚔️ Є ще одне взяття")
                await call.edit(self._render(token), reply_markup=self._markup(token))
                return

            session["selected"] = None
            session["forced_piece"] = None
            opponent = 1 - side
            if not any(self._checker_owned(value, opponent) for value in board) or not self._checker_has_move(board, opponent):
                session["winner"] = session["players"][side]
                session["finished"] = True
                self._record_result(session, [session["winner"]])
            elif session["quiet_ply"] >= CHECKER_DRAW_PLY:
                session["draw"] = True
                session["finished"] = True
                self._record_result(session, [], draw=True)
            else:
                session["turn"] = opponent
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _checker_resign(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        players = [player for player in session.get("players", []) if player]
        if user_id not in players:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        if session["finished"]:
            await call.answer("Гру вже завершено")
            return
        opponents = [player for player in players if player != user_id]
        if not opponents:
            self._sessions.pop(token, None)
            self._locks.pop(token, None)
            await call.edit(self.strings["closed"], reply_markup=[])
            return
        session["winner"] = opponents[0]
        session["finished"] = True
        self._record_result(session, [opponents[0]])
        await call.edit(
            self._render(token) + f"\n\n🏳 {self._name(session, user_id)} здався.",
            reply_markup=self._markup(token),
        )

    @staticmethod
    def _chess_initial_board():
        pieces = "rnbqkbnrpppppppp" + "." * 32 + "PPPPPPPPRNBQKBNR"
        return [piece if piece != "." else "" for piece in pieces]

    @staticmethod
    def _chess_position(index):
        return divmod(int(index), 8)

    @staticmethod
    def _chess_index(row, column):
        return row * 8 + column

    @staticmethod
    def _chess_coordinate(index):
        row, column = divmod(int(index), 8)
        return f"{'abcdefgh'[column]}{8 - row}"

    @staticmethod
    def _chess_side(piece):
        if not piece:
            return None
        return 0 if piece.isupper() else 1

    @classmethod
    def _chess_attacked(cls, board, square, by_side):
        row, column = cls._chess_position(square)
        pawn = "P" if int(by_side) == 0 else "p"
        pawn_row = row + (1 if int(by_side) == 0 else -1)
        if 0 <= pawn_row < 8:
            for pawn_column in (column - 1, column + 1):
                if 0 <= pawn_column < 8:
                    if board[cls._chess_index(pawn_row, pawn_column)] == pawn:
                        return True

        knight = "N" if int(by_side) == 0 else "n"
        for row_step, column_step in CHESS_KNIGHT_STEPS:
            source_row = row + row_step
            source_column = column + column_step
            if 0 <= source_row < 8 and 0 <= source_column < 8:
                if board[cls._chess_index(source_row, source_column)] == knight:
                    return True

        king = "K" if int(by_side) == 0 else "k"
        for row_step, column_step in CHESS_ORTHOGONAL + CHESS_DIAGONAL:
            source_row = row + row_step
            source_column = column + column_step
            if 0 <= source_row < 8 and 0 <= source_column < 8:
                if board[cls._chess_index(source_row, source_column)] == king:
                    return True

        for directions, attackers in (
            (CHESS_ORTHOGONAL, {"r", "q"}),
            (CHESS_DIAGONAL, {"b", "q"}),
        ):
            for row_step, column_step in directions:
                source_row = row + row_step
                source_column = column + column_step
                while 0 <= source_row < 8 and 0 <= source_column < 8:
                    piece = board[cls._chess_index(source_row, source_column)]
                    if piece:
                        if cls._chess_side(piece) == int(by_side) and piece.lower() in attackers:
                            return True
                        break
                    source_row += row_step
                    source_column += column_step
        return False

    @classmethod
    def _chess_in_check(cls, board, side):
        king = "K" if int(side) == 0 else "k"
        try:
            square = board.index(king)
        except ValueError:
            return True
        return cls._chess_attacked(board, square, 1 - int(side))

    @classmethod
    def _chess_pseudo_moves(
        cls,
        board,
        side,
        position,
        castling=None,
        en_passant=None,
    ):
        position = int(position)
        if not 0 <= position < 64:
            return []
        piece = board[position]
        side = int(side)
        if cls._chess_side(piece) != side:
            return []
        row, column = cls._chess_position(position)
        moves = []

        def add_if_available(target, special=None):
            target_piece = board[target]
            if not target_piece:
                moves.append((target, special))
            elif cls._chess_side(target_piece) != side and target_piece.lower() != "k":
                moves.append((target, special))

        kind = piece.lower()
        if kind == "p":
            row_step = -1 if side == 0 else 1
            start_row = 6 if side == 0 else 1
            target_row = row + row_step
            if 0 <= target_row < 8:
                one_step = cls._chess_index(target_row, column)
                if not board[one_step]:
                    moves.append((one_step, None))
                    two_row = row + row_step * 2
                    if row == start_row and not board[cls._chess_index(two_row, column)]:
                        moves.append((cls._chess_index(two_row, column), None))
                for target_column in (column - 1, column + 1):
                    if not 0 <= target_column < 8:
                        continue
                    target = cls._chess_index(target_row, target_column)
                    target_piece = board[target]
                    if (
                        target_piece
                        and cls._chess_side(target_piece) != side
                        and target_piece.lower() != "k"
                    ):
                        moves.append((target, None))
                    elif target == en_passant and not target_piece:
                        captured = cls._chess_index(row, target_column)
                        expected = "p" if side == 0 else "P"
                        if board[captured] == expected:
                            moves.append((target, "en_passant"))
            return moves

        if kind == "n":
            for row_step, column_step in CHESS_KNIGHT_STEPS:
                target_row = row + row_step
                target_column = column + column_step
                if 0 <= target_row < 8 and 0 <= target_column < 8:
                    add_if_available(cls._chess_index(target_row, target_column))
            return moves

        if kind in {"b", "r", "q"}:
            directions = ()
            if kind in {"r", "q"}:
                directions += CHESS_ORTHOGONAL
            if kind in {"b", "q"}:
                directions += CHESS_DIAGONAL
            for row_step, column_step in directions:
                target_row = row + row_step
                target_column = column + column_step
                while 0 <= target_row < 8 and 0 <= target_column < 8:
                    target = cls._chess_index(target_row, target_column)
                    target_piece = board[target]
                    if not target_piece:
                        moves.append((target, None))
                    else:
                        if cls._chess_side(target_piece) != side and target_piece.lower() != "k":
                            moves.append((target, None))
                        break
                    target_row += row_step
                    target_column += column_step
            return moves

        for row_step, column_step in CHESS_ORTHOGONAL + CHESS_DIAGONAL:
            target_row = row + row_step
            target_column = column + column_step
            if 0 <= target_row < 8 and 0 <= target_column < 8:
                add_if_available(cls._chess_index(target_row, target_column))

        rights = castling or set()
        opponent = 1 - side
        if side == 0 and position == 60 and piece == "K":
            if (
                "K" in rights
                and board[63] == "R"
                and not board[61]
                and not board[62]
                and not any(cls._chess_attacked(board, square, opponent) for square in (60, 61, 62))
            ):
                moves.append((62, "castle_k"))
            if (
                "Q" in rights
                and board[56] == "R"
                and not board[57]
                and not board[58]
                and not board[59]
                and not any(cls._chess_attacked(board, square, opponent) for square in (60, 59, 58))
            ):
                moves.append((58, "castle_q"))
        elif side == 1 and position == 4 and piece == "k":
            if (
                "k" in rights
                and board[7] == "r"
                and not board[5]
                and not board[6]
                and not any(cls._chess_attacked(board, square, opponent) for square in (4, 5, 6))
            ):
                moves.append((6, "castle_k"))
            if (
                "q" in rights
                and board[0] == "r"
                and not board[1]
                and not board[2]
                and not board[3]
                and not any(cls._chess_attacked(board, square, opponent) for square in (4, 3, 2))
            ):
                moves.append((2, "castle_q"))
        return moves

    @classmethod
    def _chess_apply_to_board(
        cls,
        board,
        source,
        target,
        special=None,
        promotion=None,
    ):
        source = int(source)
        target = int(target)
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
        target_row, _ = cls._chess_position(target)
        if piece.lower() == "p" and target_row in {0, 7} and promotion:
            promoted = str(promotion).lower()
            board[target] = promoted.upper() if piece.isupper() else promoted
        return piece, captured, captured_position

    @classmethod
    def _chess_legal_moves(
        cls,
        board,
        side,
        position,
        castling=None,
        en_passant=None,
    ):
        legal = []
        for target, special in cls._chess_pseudo_moves(
            board,
            side,
            position,
            castling,
            en_passant,
        ):
            candidate = list(board)
            cls._chess_apply_to_board(
                candidate,
                position,
                target,
                special,
                promotion="q",
            )
            if not cls._chess_in_check(candidate, side):
                legal.append((target, special))
        return legal

    @classmethod
    def _chess_all_legal_moves(
        cls,
        board,
        side,
        castling=None,
        en_passant=None,
    ):
        result = {}
        for position, piece in enumerate(board):
            if cls._chess_side(piece) != int(side):
                continue
            moves = cls._chess_legal_moves(
                board,
                side,
                position,
                castling,
                en_passant,
            )
            if moves:
                result[position] = moves
        return result

    @classmethod
    def _chess_position_key(cls, session):
        board = "".join(piece or "." for piece in session["board"])
        rights = "".join(sorted(session.get("castling", set()))) or "-"
        en_passant = session.get("en_passant")
        if en_passant is not None:
            side = int(session["turn"])
            target_row, target_column = cls._chess_position(en_passant)
            source_row = target_row + (1 if side == 0 else -1)
            pawn = "P" if side == 0 else "p"
            can_capture = False
            if 0 <= source_row < 8:
                for source_column in (target_column - 1, target_column + 1):
                    if not 0 <= source_column < 8:
                        continue
                    source = cls._chess_index(source_row, source_column)
                    if session["board"][source] != pawn:
                        continue
                    can_capture = any(
                        target == en_passant and special == "en_passant"
                        for target, special in cls._chess_legal_moves(
                            session["board"],
                            side,
                            source,
                            session.get("castling", set()),
                            en_passant,
                        )
                    )
                    if can_capture:
                        break
            if not can_capture:
                en_passant = None
        return f"{board}|{int(session['turn'])}|{rights}|{en_passant if en_passant is not None else '-'}"

    @staticmethod
    def _chess_insufficient_material(board):
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
            square_colors = {
                sum(divmod(position, 8)) % 2
                for position, _ in remaining
            }
            return len(square_colors) == 1
        return False

    @staticmethod
    def _chess_update_castling(
        session,
        piece,
        source,
        captured,
        captured_position,
    ):
        rights = session["castling"]
        if piece == "K":
            rights.difference_update({"K", "Q"})
        elif piece == "k":
            rights.difference_update({"k", "q"})
        rook_rights = {63: "K", 56: "Q", 7: "k", 0: "q"}
        if piece.lower() == "r":
            right = rook_rights.get(int(source))
            if right:
                rights.discard(right)
        if captured and captured.lower() == "r":
            right = rook_rights.get(int(captured_position))
            if right:
                rights.discard(right)

    def _chess_finish_turn(self, session, side):
        opponent = 1 - int(side)
        session["promotion"] = None
        session["turn"] = opponent
        key = self._chess_position_key(session)
        counts = session["position_counts"]
        counts[key] = int(counts.get(key, 0)) + 1
        moves = self._chess_all_legal_moves(
            session["board"],
            opponent,
            session["castling"],
            session["en_passant"],
        )
        if not moves:
            session["finished"] = True
            if self._chess_in_check(session["board"], opponent):
                session["winner"] = session["players"][side]
                session["finish_reason"] = "checkmate"
                self._record_result(session, [session["winner"]])
            else:
                session["draw"] = True
                session["draw_reason"] = "stalemate"
                self._record_result(session, [], draw=True)
            return
        draw_reason = None
        if self._chess_insufficient_material(session["board"]):
            draw_reason = "material"
        elif int(session["halfmove_clock"]) >= 100:
            draw_reason = "fifty_moves"
        elif counts[key] >= 3:
            draw_reason = "repetition"
        if draw_reason:
            session["draw"] = True
            session["draw_reason"] = draw_reason
            session["finished"] = True
            self._record_result(session, [], draw=True)

    def _render_chess(self, session):
        white, black = session["players"]
        black_name = self._name(session, black) if black else "очікується суперник"
        lines = [
            "♟ <b>Шахи · 8×8</b>",
            "",
            f"♔ {self._name(session, white)}",
            f"♚ {black_name}",
            "",
        ]
        if session["winner"] is not None:
            title = "Мат" if session.get("finish_reason") == "checkmate" else "Перемога"
            lines.append(f"🏆 <b>{title}: {self._name(session, session['winner'])}</b>")
        elif session["draw"]:
            reasons = {
                "stalemate": "Пат",
                "material": "Недостатньо матеріалу для мату",
                "fifty_moves": "50 ходів без взяття або ходу пішаком",
                "repetition": "Триразове повторення позиції",
            }
            lines.append(f"🤝 <b>Нічия: {reasons.get(session.get('draw_reason'), 'за правилами гри')}.</b>")
        else:
            turn_id = session["players"][session["turn"]]
            turn_name = self._name(session, turn_id) if turn_id else "другого гравця"
            lines.append(f"Хід: <b>{turn_name}</b>")
            if session.get("promotion"):
                lines.append("👑 <b>Оберіть фігуру для перетворення пішака.</b>")
            elif session["selected"] is not None:
                lines.append(
                    f"🔹 Обрано: <b>{self._chess_coordinate(session['selected'])}</b> · "
                    "🟩 хід · 🟥 взяття"
                )
            if self._chess_in_check(session["board"], session["turn"]):
                lines.append("⚠️ <b>Шах королю.</b>")
        if session.get("last_move"):
            lines.extend(("", f"Останній хід: <b>{session['last_move']}</b>"))
        lines.extend(
            (
                "",
                "<i>Натисніть свою фігуру, потім підсвічену клітинку.</i>",
            )
        )
        return "\n".join(lines)

    def _markup_chess(self, token, session):
        legal = {}
        if (
            session["selected"] is not None
            and not session["finished"]
            and not session.get("promotion")
        ):
            legal = dict(
                self._chess_legal_moves(
                    session["board"],
                    session["turn"],
                    session["selected"],
                    session["castling"],
                    session["en_passant"],
                )
            )
        rows = []
        for row in range(8):
            buttons = []
            for column in range(8):
                position = self._chess_index(row, column)
                piece = session["board"][position]
                special = legal.get(position)
                if position == session["selected"]:
                    symbol = CHESS_SELECTED_CELL
                elif position in legal:
                    symbol = (
                        CHESS_CAPTURE_CELL
                        if piece or special == "en_passant"
                        else CHESS_TARGET_CELL
                    )
                else:
                    symbol = CHESS_SYMBOLS.get(piece, CHESS_EMPTY_CELL)
                buttons.append(
                    {
                        "text": symbol,
                        "callback": self._chess_click,
                        "args": (token, position),
                    }
                )
            rows.append(buttons)
        if session.get("promotion") and not session["finished"]:
            side = int(session["promotion"]["side"])
            labels = {"q": "Ферзь", "r": "Тура", "b": "Слон", "n": "Кінь"}
            rows.append(
                [
                    {
                        "text": f"{CHESS_SYMBOLS[piece.upper() if side == 0 else piece]} {label}",
                        "callback": self._chess_promote,
                        "args": (token, piece),
                    }
                    for piece, label in labels.items()
                ]
            )
        if session["finished"]:
            rows.extend(self._footer(token, session))
        else:
            rows.append(
                [
                    {"text": "🏳 Здатися", "callback": self._chess_resign, "args": (token,)},
                    {"text": "✖️ Закрити", "callback": self._close, "args": (token,)},
                ]
            )
        return rows

    async def _chess_click(self, call, token, position):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            if session.get("promotion"):
                await call.answer("Спочатку оберіть фігуру для перетворення", show_alert=True)
                return
            position = int(position)
            if not 0 <= position < 64:
                await call.answer("Некоректна клітинка")
                return

            side = int(session["turn"])
            board = session["board"]
            if session["players"][1] is None:
                can_join = (
                    side == 1
                    and user_id != session["players"][0]
                    and self._chess_side(board[position]) == 1
                    and bool(
                        self._chess_legal_moves(
                            board,
                            1,
                            position,
                            session["castling"],
                            session["en_passant"],
                        )
                    )
                )
                if can_join:
                    if session.get("invited_id") and user_id != session["invited_id"]:
                        await call.answer(self.strings["not_yours"], show_alert=True)
                        return
                    session["players"][1] = user_id
                    self._remember_actor(session, user_id, user)

            current = session["players"][side]
            if user_id != current:
                await call.answer(
                    self.strings["wait_opponent"]
                    if session["players"][1] is None
                    else self.strings["not_yours"],
                    show_alert=True,
                )
                return

            clicked_piece = board[position]
            if self._chess_side(clicked_piece) == side:
                moves = self._chess_legal_moves(
                    board,
                    side,
                    position,
                    session["castling"],
                    session["en_passant"],
                )
                if not moves:
                    await call.answer("У цієї фігури немає дозволених ходів", show_alert=True)
                    return
                session["selected"] = position
                await call.edit(self._render(token), reply_markup=self._markup(token))
                return

            selected = session["selected"]
            if selected is None:
                await call.answer("Спочатку оберіть свою фігуру", show_alert=True)
                return
            legal = dict(
                self._chess_legal_moves(
                    board,
                    side,
                    selected,
                    session["castling"],
                    session["en_passant"],
                )
            )
            if position not in legal:
                await call.answer("Цей хід неможливий", show_alert=True)
                return

            special = legal[position]
            piece, captured, captured_position = self._chess_apply_to_board(
                board,
                selected,
                position,
                special,
            )
            self._chess_update_castling(
                session,
                piece,
                selected,
                captured,
                captured_position,
            )
            session["en_passant"] = (
                (selected + position) // 2
                if piece.lower() == "p" and abs(selected - position) == 16
                else None
            )
            session["halfmove_clock"] = (
                0
                if piece.lower() == "p" or captured
                else int(session["halfmove_clock"]) + 1
            )
            separator = "×" if captured else "–"
            session["last_move"] = (
                f"{CHESS_SYMBOLS[piece]} {self._chess_coordinate(selected)}"
                f"{separator}{self._chess_coordinate(position)}"
            )
            session["selected"] = None
            target_row, _ = self._chess_position(position)
            if piece.lower() == "p" and target_row in {0, 7}:
                session["promotion"] = {"position": position, "side": side}
            else:
                self._chess_finish_turn(session, side)
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _chess_promote(self, call, token, choice):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            pending = session.get("promotion")
            choice = str(choice).lower()
            if session["finished"] or not pending or choice not in {"q", "r", "b", "n"}:
                await call.answer("Перетворення вже недоступне", show_alert=True)
                return
            user_id, _ = self._actor(call)
            side = int(pending["side"])
            if user_id != session["players"][side]:
                await call.answer(self.strings["not_yours"], show_alert=True)
                return
            position = int(pending["position"])
            promoted = choice.upper() if side == 0 else choice
            session["board"][position] = promoted
            session["last_move"] += f"={CHESS_SYMBOLS[promoted]}"
            self._chess_finish_turn(session, side)
            labels = {"q": "ферзя", "r": "туру", "b": "слона", "n": "коня"}
            await call.answer(f"Пішака перетворено на {labels[choice]}")
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _chess_resign(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, _ = self._actor(call)
            players = [player for player in session.get("players", []) if player]
            if user_id not in players:
                await call.answer(self.strings["not_yours"], show_alert=True)
                return
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            opponents = [player for player in players if player != user_id]
            if not opponents:
                self._sessions.pop(token, None)
                self._locks.pop(token, None)
                await call.edit(self.strings["closed"], reply_markup=[])
                return
            session["winner"] = opponents[0]
            session["finish_reason"] = "resignation"
            session["finished"] = True
            self._record_result(session, [opponents[0]])
            await call.edit(
                self._render(token) + f"\n\n🏳 {self._name(session, user_id)} здався.",
                reply_markup=self._markup(token),
            )

    @staticmethod
    def _go_board_key(board):
        return "".join("b" if stone == 1 else "w" if stone == -1 else "." for stone in board)

    @staticmethod
    def _go_index(row, column, size):
        return int(row) * int(size) + int(column)

    @staticmethod
    def _go_position(index, size):
        return divmod(int(index), int(size))

    @staticmethod
    def _go_coordinate(index, size):
        row, column = divmod(int(index), int(size))
        return f"{GO_COLUMNS[column]}{int(size) - row}"

    @classmethod
    def _go_neighbors(cls, position, size):
        row, column = cls._go_position(position, size)
        neighbors = []
        for row_step, column_step in CHESS_ORTHOGONAL:
            target_row = row + row_step
            target_column = column + column_step
            if 0 <= target_row < size and 0 <= target_column < size:
                neighbors.append(cls._go_index(target_row, target_column, size))
        return neighbors

    @classmethod
    def _go_group_and_liberties(cls, board, size, start):
        stone = board[int(start)]
        if not stone:
            return set(), set()
        group = set()
        liberties = set()
        stack = [int(start)]
        while stack:
            position = stack.pop()
            if position in group:
                continue
            group.add(position)
            for neighbor in cls._go_neighbors(position, size):
                if not board[neighbor]:
                    liberties.add(neighbor)
                elif board[neighbor] == stone and neighbor not in group:
                    stack.append(neighbor)
        return group, liberties

    @classmethod
    def _go_try_move(cls, board, size, side, position, history=None):
        position = int(position)
        size = int(size)
        if not 0 <= position < size * size:
            return None, 0, "invalid"
        if board[position]:
            return None, 0, "occupied"
        stone = 1 if int(side) == 0 else -1
        candidate = list(board)
        candidate[position] = stone
        captured_positions = set()
        for neighbor in cls._go_neighbors(position, size):
            if candidate[neighbor] != -stone:
                continue
            group, liberties = cls._go_group_and_liberties(candidate, size, neighbor)
            if liberties:
                continue
            captured_positions.update(group)
            for captured in group:
                candidate[captured] = 0
        _, liberties = cls._go_group_and_liberties(candidate, size, position)
        if not liberties:
            return None, 0, "suicide"
        if history and cls._go_board_key(candidate) in history:
            return None, 0, "ko"
        return candidate, len(captured_positions), None

    @classmethod
    def _go_score(cls, board, size, komi=GO_KOMI):
        stones = [sum(stone == 1 for stone in board), sum(stone == -1 for stone in board)]
        territory = [0, 0]
        visited = set()
        for start, stone in enumerate(board):
            if stone or start in visited:
                continue
            region = set()
            borders = set()
            stack = [start]
            while stack:
                position = stack.pop()
                if position in region:
                    continue
                region.add(position)
                for neighbor in cls._go_neighbors(position, size):
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
        scores = [stones[0] + territory[0], stones[1] + territory[1] + float(komi)]
        return scores, territory

    @staticmethod
    def _go_hoshi(size):
        if int(size) == 9:
            return {(2, 2), (2, 6), (4, 4), (6, 2), (6, 6)}
        if int(size) == 13:
            return {(3, 3), (3, 9), (6, 6), (9, 3), (9, 9)}
        return set()

    @classmethod
    def _go_board_text(cls, session):
        size = int(session["go_size"])
        columns = GO_COLUMNS[:size]
        lines = ["   " + " ".join(columns)]
        last_move = session.get("last_move")
        hoshi = cls._go_hoshi(size)
        for row in range(size):
            cells = []
            for column in range(size):
                position = cls._go_index(row, column, size)
                stone = session["board"][position]
                if position == last_move and stone:
                    symbol = GO_LAST_STONES[stone]
                elif stone:
                    symbol = GO_STONES[stone]
                else:
                    symbol = "+" if (row, column) in hoshi else "·"
                cells.append(symbol)
            lines.append(f"{size - row:>2} " + " ".join(cells))
        lines.append("   " + " ".join(columns))
        return "<pre>" + "\n".join(lines) + "</pre>"

    @staticmethod
    def _go_format_score(value):
        return f"{float(value):.1f}".rstrip("0").rstrip(".")

    def _go_finish(self, session):
        scores, territory = self._go_score(session["board"], session["go_size"])
        session["scores"] = scores
        session["territory"] = territory
        session["finished"] = True
        session["finish_reason"] = "score"
        if scores[0] == scores[1]:
            session["draw"] = True
            self._record_result(session, [], draw=True)
            return
        winner_side = 0 if scores[0] > scores[1] else 1
        session["winner"] = session["players"][winner_side]
        self._record_result(session, [session["winner"]])

    def _render_gomenu(self, session):
        invited = session["players"][1]
        opponent = (
            f"\nСуперник: <b>{self._name(session, invited)}</b>"
            if invited
            else "\nСуперник зможе приєднатися після першого ходу."
        )
        return (
            "⚫⚪ <b>Ґо · вибір дошки</b>\n\n"
            "Оберіть компактну <b>9×9</b> або більшу <b>13×13</b>."
            f"{opponent}\n\n"
            f"<i>Китайський підрахунок території · комі {GO_KOMI}</i>"
        )

    def _markup_gomenu(self, token, session):
        return [
            [
                {
                    "text": "⚫⚪ 9×9",
                    "callback": self._select_go_size,
                    "args": (token, "go9"),
                },
                {
                    "text": "⚫⚪ 13×13",
                    "callback": self._select_go_size,
                    "args": (token, "go13"),
                },
            ],
            [{"text": "✖️ Закрити", "callback": self._close, "args": (token,)}],
        ]

    async def _select_go_size(self, call, token, kind):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id != session["creator_id"] or kind not in {"go9", "go13"}:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        self._reset_game(session, kind)
        await call.edit(self._render(token), reply_markup=self._markup(token))

    def _render_go(self, session):
        size = int(session["go_size"])
        black, white = session["players"]
        white_name = self._name(session, white) if white else "очікується суперник"
        lines = [
            f"⚫⚪ <b>Ґо · {size}×{size}</b>",
            "",
            f"● {self._name(session, black)} · полон: <b>{session['captures'][0]}</b>",
            f"○ {white_name} · полон: <b>{session['captures'][1]}</b>",
            "",
        ]
        if session["winner"] is not None:
            lines.append(f"🏆 Переміг: <b>{self._name(session, session['winner'])}</b>")
        elif session["draw"]:
            lines.append("🤝 <b>Нічия.</b>")
        else:
            turn_id = session["players"][session["turn"]]
            turn_name = self._name(session, turn_id) if turn_id else "другого гравця"
            stone = GO_STONES[1 if session["turn"] == 0 else -1]
            lines.append(f"Хід {session['move_number'] + 1}: {stone} <b>{turn_name}</b>")
            if session["selected_row"] is None:
                lines.append("Оберіть <b>номер рядка</b> кнопкою нижче.")
            else:
                row_label = size - int(session["selected_row"])
                lines.append(f"Обрано рядок <b>{row_label}</b> · тепер оберіть стовпець.")
            if session["consecutive_passes"] == 1:
                lines.append("⏭ <b>Був пас. Наступний пас завершить партію.</b>")
        if session.get("scores"):
            black_score, white_score = session["scores"]
            lines.extend(
                (
                    "",
                    "📊 <b>Китайський підрахунок</b>",
                    f"● {self._go_format_score(black_score)} · "
                    f"○ {self._go_format_score(white_score)} <i>(комі {GO_KOMI})</i>",
                )
            )
        if session.get("last_action"):
            lines.extend(("", f"Остання дія: <b>{session['last_action']}</b>"))
        lines.extend(
            (
                "",
                self._go_board_text(session),
                "<i>●/○ — камені · ◆/◇ — останній хід · + — хосі</i>",
            )
        )
        return "\n".join(lines)

    def _render_go9(self, session):
        return self._render_go(session)

    def _render_go13(self, session):
        return self._render_go(session)

    def _markup_go(self, token, session):
        if session["finished"]:
            return self._footer(token, session)
        size = int(session["go_size"])
        rows = []
        if session["selected_row"] is None:
            choices = [
                {
                    "text": str(size - row),
                    "callback": self._go_select_row,
                    "args": (token, row),
                }
                for row in range(size)
            ]
            rows.extend(choices[index : index + 7] for index in range(0, len(choices), 7))
        else:
            selected_row = int(session["selected_row"])
            choices = []
            for column, letter in enumerate(GO_COLUMNS[:size]):
                position = self._go_index(selected_row, column, size)
                symbol = GO_STONES.get(session["board"][position], "·")
                choices.append(
                    {
                        "text": f"{letter} {symbol}",
                        "callback": self._go_place,
                        "args": (token, column),
                    }
                )
            rows.extend(choices[index : index + 7] for index in range(0, len(choices), 7))
            rows.append(
                [
                    {
                        "text": "↩ Інший рядок",
                        "callback": self._go_clear_row,
                        "args": (token,),
                    }
                ]
            )
        rows.append(
            [
                {"text": "⏭ Пас", "callback": self._go_pass, "args": (token,)},
                {"text": "🏳 Здатися", "callback": self._go_resign, "args": (token,)},
                {"text": "✖️ Закрити", "callback": self._close, "args": (token,)},
            ]
        )
        return rows

    def _markup_go9(self, token, session):
        return self._markup_go(token, session)

    def _markup_go13(self, token, session):
        return self._markup_go(token, session)

    def _go_authorized(self, session, user_id, user):
        side = int(session["turn"])
        if (
            session["players"][1] is None
            and side == 1
            and user_id != session["players"][0]
        ):
            if session.get("invited_id") and user_id != session["invited_id"]:
                return False
            session["players"][1] = user_id
            self._remember_actor(session, user_id, user)
        return user_id == session["players"][side]

    async def _go_select_row(self, call, token, row):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            row = int(row)
            if not 0 <= row < session["go_size"]:
                await call.answer("Некоректний рядок", show_alert=True)
                return
            if not self._go_authorized(session, user_id, user):
                await call.answer(
                    self.strings["wait_opponent"]
                    if session["players"][1] is None
                    else self.strings["not_yours"],
                    show_alert=True,
                )
                return
            session["selected_row"] = row
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _go_clear_row(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if not self._go_authorized(session, user_id, user):
                await call.answer(self.strings["not_yours"], show_alert=True)
                return
            session["selected_row"] = None
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _go_place(self, call, token, column):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            if not self._go_authorized(session, user_id, user):
                await call.answer(self.strings["not_yours"], show_alert=True)
                return
            if session["selected_row"] is None:
                await call.answer("Спочатку оберіть рядок", show_alert=True)
                return
            column = int(column)
            size = int(session["go_size"])
            if not 0 <= column < size:
                await call.answer("Некоректний стовпець", show_alert=True)
                return
            position = self._go_index(session["selected_row"], column, size)
            board, captured, error = self._go_try_move(
                session["board"],
                size,
                session["turn"],
                position,
                session["history"],
            )
            if error:
                errors = {
                    "invalid": "Некоректна точка",
                    "occupied": "Ця точка вже зайнята",
                    "suicide": "Самогубний хід заборонений",
                    "ko": "Не можна повторювати попередню позицію",
                }
                await call.answer(errors[error], show_alert=True)
                return
            side = int(session["turn"])
            stone = 1 if side == 0 else -1
            session["board"] = board
            session["history"].add(self._go_board_key(board))
            session["captures"][side] += captured
            session["consecutive_passes"] = 0
            session["move_number"] += 1
            session["last_move"] = position
            session["last_action"] = f"{GO_STONES[stone]} {self._go_coordinate(position, size)}"
            session["selected_row"] = None
            if all(board):
                self._go_finish(session)
            else:
                session["turn"] = 1 - side
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _go_pass(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            if not self._go_authorized(session, user_id, user):
                await call.answer(
                    self.strings["wait_opponent"]
                    if session["players"][1] is None
                    else self.strings["not_yours"],
                    show_alert=True,
                )
                return
            side = int(session["turn"])
            stone = 1 if side == 0 else -1
            session["consecutive_passes"] += 1
            session["move_number"] += 1
            session["last_move"] = None
            session["last_action"] = f"{GO_STONES[stone]} пас"
            session["selected_row"] = None
            if session["consecutive_passes"] >= 2:
                self._go_finish(session)
            else:
                session["turn"] = 1 - side
            await call.answer("⏭ Пас")
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _go_resign(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, _ = self._actor(call)
            players = [player for player in session.get("players", []) if player]
            if user_id not in players:
                await call.answer(self.strings["not_yours"], show_alert=True)
                return
            if session["finished"]:
                await call.answer("Партію вже завершено")
                return
            opponents = [player for player in players if player != user_id]
            if not opponents:
                self._sessions.pop(token, None)
                self._locks.pop(token, None)
                await call.edit(self.strings["closed"], reply_markup=[])
                return
            session["winner"] = opponents[0]
            session["finish_reason"] = "resignation"
            session["finished"] = True
            self._record_result(session, [opponents[0]])
            await call.edit(
                self._render(token) + f"\n\n🏳 {self._name(session, user_id)} здався.",
                reply_markup=self._markup(token),
            )

    def _render_rps(self, session):
        first, second = session["players"]
        lines = ["🪨📄✂️ <b>Камінь, ножиці, папір</b>", ""]
        for user_id in (first, second):
            if user_id is None:
                lines.append("👤 <i>Очікується суперник…</i>")
            elif session["finished"]:
                choice = session["choices"].get(str(user_id))
                icon, label = self.RPS[choice]
                lines.append(f"{icon} {self._name(session, user_id)} — <b>{label}</b>")
            else:
                ready = "✅ вибір зроблено" if str(user_id) in session["choices"] else "⏳ обирає"
                lines.append(f"👤 {self._name(session, user_id)} — {ready}")
        if session["finished"]:
            lines.append("")
            if session["draw"]:
                lines.append("🤝 <b>Нічия!</b>")
            else:
                lines.append(f"🏆 Переміг: <b>{self._name(session, session['winner'])}</b>")
        else:
            lines.extend(("", "<i>Вибір не показується до завершення раунду.</i>"))
        return "\n".join(lines)

    def _markup_rps(self, token, session):
        rows = [
            [
                {"text": f"{icon} {label}", "callback": self._rps_choose, "args": (token, key)}
                for key, (icon, label) in self.RPS.items()
            ]
        ]
        rows.extend(self._footer(token, session))
        return rows

    async def _rps_choose(self, call, token, choice):
        session = self._session(token)
        if session is None or choice not in self.RPS:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Раунд уже завершено")
                return
            if user_id not in session["players"]:
                if session["players"][1] is not None:
                    await call.answer(self.strings["not_yours"], show_alert=True)
                    return
                if session.get("invited_id") and user_id != session["invited_id"]:
                    await call.answer(self.strings["not_yours"], show_alert=True)
                    return
                session["players"][1] = user_id
                self._remember_actor(session, user_id, user)
            if str(user_id) in session["choices"]:
                await call.answer(self.strings["already_played"])
                return
            session["choices"][str(user_id)] = choice
            first, second = session["players"]
            if second is not None and all(str(player) in session["choices"] for player in (first, second)):
                first_choice = session["choices"][str(first)]
                second_choice = session["choices"][str(second)]
                session["finished"] = True
                if first_choice == second_choice:
                    session["draw"] = True
                    self._record_result(session, [], draw=True)
                else:
                    session["winner"] = first if self.RPS_BEATS[first_choice] == second_choice else second
                    self._record_result(session, [session["winner"]])
            await call.answer("✅ Вибір прийнято")
            await call.edit(self._render(token), reply_markup=self._markup(token))

    def _render_dice(self, session):
        first, second = session["players"]
        lines = ["🎲 <b>Кубик-дуель</b>", ""]
        for user_id in (first, second):
            if user_id is None:
                lines.append("👤 <i>Очікується суперник…</i>")
                continue
            roll = session["rolls"].get(str(user_id))
            lines.append(f"👤 {self._name(session, user_id)} — {'🎲 ще не кинув' if roll is None else f'<b>{roll}</b>'}")
        if session["finished"]:
            lines.append("")
            if session["draw"]:
                lines.append("🤝 <b>Однакові числа — нічия!</b>")
            else:
                lines.append(f"🏆 Переміг: <b>{self._name(session, session['winner'])}</b>")
        return "\n".join(lines)

    def _markup_dice(self, token, session):
        rows = [[{"text": "🎲 Кинути кубик", "callback": self._dice_roll, "args": (token,)}]]
        rows.extend(self._footer(token, session))
        return rows

    async def _dice_roll(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Дуель уже завершено")
                return
            if user_id not in session["players"]:
                if session["players"][1] is not None:
                    await call.answer(self.strings["not_yours"], show_alert=True)
                    return
                if session.get("invited_id") and user_id != session["invited_id"]:
                    await call.answer(self.strings["not_yours"], show_alert=True)
                    return
                session["players"][1] = user_id
                self._remember_actor(session, user_id, user)
            if str(user_id) in session["rolls"]:
                await call.answer(self.strings["already_played"])
                return
            session["rolls"][str(user_id)] = self._rng.randint(1, 6)
            first, second = session["players"]
            if second is not None and all(str(player) in session["rolls"] for player in (first, second)):
                first_roll = session["rolls"][str(first)]
                second_roll = session["rolls"][str(second)]
                session["finished"] = True
                if first_roll == second_roll:
                    session["draw"] = True
                    self._record_result(session, [], draw=True)
                else:
                    session["winner"] = first if first_roll > second_roll else second
                    self._record_result(session, [session["winner"]])
            await call.edit(self._render(token), reply_markup=self._markup(token))

    def _render_quiz(self, session):
        if session["finished"]:
            ranking = sorted(session["scores"].items(), key=lambda item: (-item[1], self._name(session, int(item[0]))))
            lines = ["🧠 <b>Вікторину завершено!</b>", "", "🏆 <b>Результати</b>"]
            if not ranking:
                lines.append("<i>Ніхто не набрав балів.</i>")
            else:
                medals = ("🥇", "🥈", "🥉")
                for index, (user_id, score) in enumerate(ranking[:10]):
                    lines.append(f"{medals[index] if index < 3 else '▫️'} {self._name(session, int(user_id))} — <b>{score}</b>")
            return "\n".join(lines)
        question, _, _ = session["questions"][session["question_index"]]
        leader = ""
        if session["scores"]:
            best = max(session["scores"], key=session["scores"].get)
            leader = f"\n👑 Лідер: {self._name(session, int(best))} · {session['scores'][best]}"
        return (
            "🧠 <b>Швидка вікторина</b>\n"
            f"Питання <b>{session['question_index'] + 1}/{len(session['questions'])}</b>{leader}\n\n"
            f"<b>{html.escape(question)}</b>\n\n"
            "<i>Перший правильний варіант отримує бал. Після помилки в цьому раунді відповісти ще раз не можна.</i>"
        )

    def _markup_quiz(self, token, session):
        if session["finished"]:
            return self._footer(token, session)
        _, options, _ = session["questions"][session["question_index"]]
        buttons = [
            {"text": f"{index + 1}. {option}", "callback": self._quiz_answer, "args": (token, index)}
            for index, option in enumerate(options)
        ]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.extend(self._footer(token, session))
        return rows

    async def _quiz_answer(self, call, token, answer):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            user_id, user = self._actor(call)
            if session["finished"]:
                await call.answer("Вікторину вже завершено")
                return
            self._remember_actor(session, user_id, user)
            session["scores"].setdefault(str(user_id), 0)
            if user_id in session["wrong"]:
                await call.answer("У цьому раунді ви вже помилилися", show_alert=True)
                return
            _, _, correct = session["questions"][session["question_index"]]
            if int(answer) != int(correct):
                session["wrong"].add(user_id)
                await call.answer("❌ Неправильно", show_alert=True)
                return
            session["scores"][str(user_id)] += 1
            session["last_winner"] = user_id
            session["question_index"] += 1
            session["wrong"] = set()
            await call.answer("✅ Правильно! +1 бал")
            if session["question_index"] >= len(session["questions"]):
                session["finished"] = True
                top_score = max(session["scores"].values(), default=0)
                winners = [int(uid) for uid, score in session["scores"].items() if score == top_score and score > 0]
                self._record_result(session, winners, draw=len(winners) > 1)
            await call.edit(self._render(token), reply_markup=self._markup(token))

    def _footer(self, token, session):
        if session["finished"]:
            return [
                [
                    {"text": "🔄 Реванш", "callback": self._rematch, "args": (token,)},
                    {"text": "🏆 Рейтинг", "callback": self._show_top, "args": (token,)},
                ],
                [{"text": "✖️ Закрити", "callback": self._close, "args": (token,)}],
            ]
        return [[{"text": "✖️ Закрити", "callback": self._close, "args": (token,)}]]

    async def _rematch(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        participants = {player for player in session.get("players", []) if player}
        if session["kind"] == "quiz":
            participants.update(int(value) for value in session.get("scores", {}))
        if user_id not in participants and user_id != session["creator_id"]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        if session["kind"] in {
            "ttt",
            "rps",
            "dice",
            "checkers",
            "chess",
            "go9",
            "go13",
        } and session["players"][1] is not None:
            session["players"] = [session["players"][1], session["players"][0]]
            session["invited_id"] = session["players"][1]
        self._reset_game(session)
        await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _close(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id != session["creator_id"]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        self._sessions.pop(token, None)
        self._locks.pop(token, None)
        await call.edit(self.strings["closed"], reply_markup=[])

    def _stats(self):
        value = self.get("game_stats", {})
        return value if isinstance(value, dict) else {}

    def _record_result(self, session, winners, draw=False):
        participants = [player for player in session.get("players", []) if player]
        if session["kind"] == "quiz":
            participants = [int(value) for value in session.get("scores", {})]
        stats = self._stats()
        chat = stats.setdefault(str(session["chat_id"]), {})
        winner_set = {int(value) for value in winners}
        for user_id in set(participants):
            key = str(user_id)
            row = chat.setdefault(key, {"played": 0, "wins": 0, "draws": 0, "name": ""})
            row["played"] = int(row.get("played", 0)) + 1
            row["wins"] = int(row.get("wins", 0)) + (1 if user_id in winner_set else 0)
            is_draw_player = draw and (not winner_set or user_id in winner_set)
            row["draws"] = int(row.get("draws", 0)) + (1 if is_draw_player else 0)
            row["name"] = session["names"].get(key, row.get("name") or f"ID {user_id}")
        self.set("game_stats", stats)

        kind = str(session.get("kind") or "other").lower()
        totals = self.get("game_totals", {})
        totals = dict(totals) if isinstance(totals, dict) else {}
        total = dict(totals.get(kind, {}))
        total["completed"] = int(total.get("completed", 0) or 0) + 1
        total["draws"] = int(total.get("draws", 0) or 0) + (1 if draw else 0)
        totals[kind] = total
        self.set("game_totals", totals)
        with contextlib.suppress(Exception):
            hub = self.lookup("ModuleHub")
            if hub is not None:
                hub.report_stat(self, "games.completed", 1)
                hub.report_stat(self, f"game.{kind}", 1)

    def modulehub_stats(self):
        """Return aggregate game data without chat or player identifiers."""
        raw = self.get("game_totals", {})
        raw = raw if isinstance(raw, dict) else {}
        by_game = {}
        for kind, value in raw.items():
            if not isinstance(value, dict):
                continue
            by_game[str(kind)[:24]] = {
                "completed": max(0, int(value.get("completed", 0) or 0)),
                "draws": max(0, int(value.get("draws", 0) or 0)),
            }
        return {
            "games_completed": sum(item["completed"] for item in by_game.values()),
            "draws": sum(item["draws"] for item in by_game.values()),
            "active_games": sum(
                1 for session in self._sessions.values() if not session.get("finished")
            ),
            "by_game": by_game,
        }

    def _top_text(self, chat_id):
        rows = list(self._stats().get(str(chat_id), {}).values())
        rows.sort(key=lambda row: (-int(row.get("wins", 0)), -int(row.get("played", 0)), str(row.get("name", "")).casefold()))
        lines = ["🏆 <b>Рейтинг MiniGames</b>", ""]
        if not rows:
            lines.append("<i>У цьому чаті ще немає завершених ігор.</i>")
            return "\n".join(lines)
        medals = ("🥇", "🥈", "🥉")
        for index, row in enumerate(rows[:10]):
            lines.append(
                f"{medals[index] if index < 3 else '▫️'} {html.escape(str(row.get('name') or 'Гравець'))} — "
                f"<b>{int(row.get('wins', 0))}</b> перемог · {int(row.get('played', 0))} ігор"
            )
        return "\n".join(lines)

    async def _show_top(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        await call.edit(
            self._top_text(session["chat_id"]),
            reply_markup=[[{"text": "↩️ До гри", "callback": self._back_to_game, "args": (token,)}]],
        )

    async def _back_to_game(self, call, token):
        if self._session(token) is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _direct_game(self, message, kind):
        invited = await self._resolve_invited(message)
        if invited is False:
            return
        await self._open(message, kind, invited)

    @loader.command(ru_doc="Відкрити меню інтерактивних мініігор")
    async def games(self, message):
        """🎮 Меню мініігор з кнопками"""
        await self._open(message)

    @loader.command(ru_doc="Хрестики-нулики: .ttt [@user] або у відповідь")
    async def ttt(self, message):
        """❌⭕ Почати гру в хрестики-нулики"""
        await self._direct_game(message, "ttt")

    @loader.command(ru_doc="Камінь, ножиці, папір: .rps [@user] або у відповідь")
    async def rps(self, message):
        """🪨📄✂️ Почати прихований раунд КНП"""
        await self._direct_game(message, "rps")

    @loader.command(ru_doc="Кубик-дуель: .dicegame [@user] або у відповідь")
    async def dicegame(self, message):
        """🎲 Почати дуель на кубиках"""
        await self._direct_game(message, "dice")

    @loader.command(ru_doc="Шашки 8×8: .checkers [@user] або у відповідь")
    async def checkers(self, message):
        """⚪⚫ Почати партію в шашки з обов'язковим взяттям"""
        await self._direct_game(message, "checkers")

    @loader.command(ru_doc="Шахи 8×8: .chess [@user] або у відповідь")
    async def chess(self, message):
        """♟ Почати шахову партію з повними правилами"""
        await self._direct_game(message, "chess")

    @loader.command(ru_doc="Ґо: .go [@user] або у відповідь, потім вибір дошки")
    async def go(self, message):
        """⚫⚪ Обрати дошку 9×9 або 13×13 кнопкою"""
        await self._direct_game(message, "gomenu")

    @loader.command(ru_doc="Ґо на дошці 9×9: .go9 [@user] або у відповідь")
    async def go9(self, message):
        """⚫⚪ Почати партію в ґо 9×9"""
        await self._direct_game(message, "go9")

    @loader.command(ru_doc="Ґо на дошці 13×13: .go13 [@user] або у відповідь")
    async def go13(self, message):
        """⚫⚪ Почати партію в ґо 13×13"""
        await self._direct_game(message, "go13")

    @loader.command(ru_doc="Запустити швидку вікторину для всього чату")
    async def quiz(self, message):
        """🧠 Вікторина на швидкість"""
        await self._open(message, "quiz")

    @loader.command(ru_doc="Показати рейтинг мініігор поточного чату")
    async def gametop(self, message):
        """🏆 Перемоги та кількість зіграних ігор"""
        await utils.answer(message, self._top_text(self._chat_id(message)))
