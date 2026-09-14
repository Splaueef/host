# meta developer: @Huai_Baike
# meta version: 2.3.3
# meta description: Локальні та глобальні HikkaNet-ігри з рейтингом і матчмейкінгом
# scope: inline
# scope: hikka_only

__version__ = (2, 3, 3)

import asyncio
import contextlib
import html
import logging
import random
import secrets
import time

import aiohttp

from .. import loader, utils


logger = logging.getLogger(__name__)
SESSION_TTL = 30 * 60
NETWORK_VIEW_TTL = 7 * 86400
NETWORK_KINDS = ("ttt", "checkers", "chess", "go9", "go13")
NETWORK_LABELS = {
    "ttt": "❌⭕ Хрестики-нулики",
    "checkers": "⚪⚫ Шашки",
    "chess": "♟ Шахи",
    "go9": "⚫⚪ Ґо 9×9",
    "go13": "⚫⚪ Ґо 13×13",
}
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
CHESS_PREMIUM_EMOJI_IDS = {
    "P": "5469921214036223278",
    "p": "5469995525560381442",
    "N": "5469865151828110432",
    "n": "5469892403395602271",
    "B": "5469665856755638615",
    "b": "5469961088512598930",
    "R": "5469768635323032101",
    "r": "5470104136693362691",
    "Q": "5467752654983702502",
    "q": "5469713225949948533",
    "K": "5467393935020170256",
    "k": "5470074673217787995",
}
CHESS_CELL_PREMIUM_EMOJI_ID = "5220005833110199517"
CHESS_MOVE_PREMIUM_EMOJI_ID = "5463362846219836734"
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
CHESS_FILES = "abcdefgh"
CHESS_LIGHT_CELL = "·"
CHESS_DARK_CELL = "•"
CHESS_SELECTED_CELL = "🔷"
CHESS_TARGET_CELL = "🟢"
CHESS_CAPTURE_CELL = "🔴"
CHESS_RICH_EMPTY_CELL = "\u00a0"
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
            loader.ConfigValue(
                "network_poll_interval",
                12,
                lambda: "Інтервал автооновлення відкритих HikkaNet-партій",
                validator=loader.validators.Integer(minimum=5, maximum=120),
            ),
            loader.ConfigValue(
                "network_notifications",
                True,
                lambda: "Сповіщати у Збережених про запрошення та свій хід",
                validator=loader.validators.Boolean(),
            ),
        )
        self._client = None
        self._me_id = None
        self._me_name = "Гравець"
        self._sessions = {}
        self._locks = {}
        self._rng = random.SystemRandom()
        self._network_views = {}
        self._network_task = None
        self._network_stop = asyncio.Event()
        self._rich_warning_shown = False
        self._rich_emoji_warning_shown = False
        self._chess_emoji_alternatives = {}
        self._chess_emoji_alternatives_retry_at = 0.0

    async def client_ready(self, client, db):
        self._client = client
        me = await client.get_me()
        self._me_id = int(me.id)
        self._me_name = self._display_name(me)
        self._network_stop.clear()
        if not self._network_task or self._network_task.done():
            self._network_task = asyncio.create_task(
                self._network_worker(), name="minigames-hikkanet-refresh"
            )

    async def on_unload(self):
        self._network_stop.set()
        if self._network_task:
            self._network_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._network_task
        self._network_task = None

    def _network(self):
        network = None
        with contextlib.suppress(Exception):
            network = self.lookup("HikkaNet")
        if network is None:
            network = next(
                (
                    module
                    for module in getattr(
                        getattr(self, "allmodules", None), "modules", []
                    )
                    if module.__class__.__name__ == "HikkaNetMod"
                ),
                None,
            )
        configured = getattr(network, "_configured", None) if network else None
        if network is None or (callable(configured) and not configured()):
            return None
        required = (
            "api_game_create",
            "api_games",
            "api_game_get",
            "api_game_join",
            "api_game_move",
            "api_game_resign",
            "api_game_cancel",
            "api_game_leaderboard",
            "api_game_profile",
        )
        return (
            network
            if all(callable(getattr(network, name, None)) for name in required)
            else None
        )

    async def _network_wait(self, seconds):
        try:
            await asyncio.wait_for(
                self._network_stop.wait(), timeout=max(1, int(seconds))
            )
        except asyncio.TimeoutError:
            pass

    async def _network_worker(self):
        await self._network_wait(8)
        while not self._network_stop.is_set():
            network = self._network()
            if network is not None:
                if self.config["network_notifications"]:
                    with contextlib.suppress(Exception):
                        await self._poll_network_notifications(network)
                now = time.monotonic()
                views = sorted(
                    self._network_views.items(),
                    key=lambda item: float(item[1].get("created_at", 0)),
                    reverse=True,
                )[:12]
                for token, view in views:
                    if now - float(view.get("created_at", now)) > NETWORK_VIEW_TTL:
                        self._network_views.pop(token, None)
                        self._locks.pop(token, None)
                        continue
                    game = view.get("game", {})
                    if game.get("status") not in {"waiting", "invited", "active"}:
                        continue
                    try:
                        fresh = await network.api_game_get(view["game_id"])
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    if int(fresh.get("revision", 0)) <= int(game.get("revision", 0)):
                        continue
                    view["game"] = fresh
                    view["selected"] = None
                    view["selected_row"] = None
                    view["promotion"] = None
                    view["finish_confirm"] = None
                    handle = view.get("handle")
                    if handle is not None and callable(getattr(handle, "edit", None)):
                        with contextlib.suppress(Exception):
                            await self._edit_network_panel(handle, token)
            await self._network_wait(int(self.config["network_poll_interval"]))

    async def _poll_network_notifications(self, network):
        data = await network.api_games(scope="mine", limit=50)
        seen = self.get("network_seen_revisions", {})
        seen = dict(seen) if isinstance(seen, dict) else {}
        open_ids = {
            str(view.get("game_id"))
            for view in self._network_views.values()
            if view.get("handle") is not None
        }
        for game in data.get("games", []):
            game_id = str(game.get("game_id", ""))
            revision = int(game.get("revision", 0) or 0)
            if not game_id or revision <= int(seen.get(game_id, 0) or 0):
                continue
            seen[game_id] = revision
            if game_id in open_ids:
                continue
            status = game.get("status")
            state = game.get("state", {})
            my_slot = game.get("my_slot")
            label = NETWORK_LABELS.get(game.get("kind"), game.get("kind"))
            text = None
            if status == "invited" and my_slot == 1:
                text = (
                    f"✉️ <b>Нове запрошення HikkaNet</b>\n\n{label}\n"
                    f"Прийняти: <code>.netjoin {html.escape(game_id)}</code>"
                )
            elif status == "active" and my_slot == state.get("turn"):
                text = (
                    f"🎮 <b>Ваш хід у HikkaNet</b>\n\n{label}\n"
                    f"Відкрити: <code>.netgame {html.escape(game_id)}</code>"
                )
            elif status == "finished":
                winner = state.get("winner")
                if state.get("draw"):
                    result = "нічия"
                elif winner == my_slot:
                    result = "ви перемогли 🏆"
                else:
                    result = "переміг суперник"
                text = (
                    f"🏁 <b>HikkaNet-партію завершено</b>\n\n{label} · {result}\n"
                    f"Переглянути: <code>.netgame {html.escape(game_id)}</code>"
                )
            if text and self._client is not None:
                await self._client.send_message("me", text, parse_mode="html")
        # Keep the persisted map bounded while retaining all currently visible games.
        current = {str(game.get("game_id")) for game in data.get("games", [])}
        self.set(
            "network_seen_revisions",
            {key: value for key, value in seen.items() if key in current},
        )

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
                board_flipped=False,
                finish_confirm=None,
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
            return
        if kind == "chess" and callable(getattr(opened, "edit", None)):
            await self._edit_chess_panel(opened, token)

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
            "🏠 <b>Локально</b> — учасники поточного чату\n"
            "🌐 <b>HikkaNet</b> — суперник може бути в іншому чаті або на іншій Hikka"
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
            [
                {
                    "text": "🌐 Грати через HikkaNet",
                    "callback": self._network_lobby_callback,
                    "args": (token,),
                }
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
        if kind == "chess":
            await self._edit_chess_panel(call, token)
        else:
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
        flipped = bool(session.get("board_flipped"))
        lines = [
            "♟ <b>Шахи · 8×8</b>",
            "",
            f"♔ {self._name(session, white)}",
            f"♚ {black_name}",
            f"Орієнтація: <b>{'чорні знизу' if flipped else 'білі знизу'}</b>",
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
            elif session.get("finish_confirm") is not None:
                lines.append("⚠️ <b>Підтвердіть завершення партії нижче.</b>")
            elif session["selected"] is not None:
                lines.append(
                    f"🔹 Обрано: <b>{self._chess_coordinate(session['selected'])}</b> · "
                    "🟢 хід · 🔴 взяття"
                )
            if self._chess_in_check(session["board"], session["turn"]):
                lines.append("⚠️ <b>Шах королю.</b>")
        if session.get("last_move"):
            lines.extend(("", f"Останній хід: <b>{session['last_move']}</b>"))
        lines.extend(
            (
                "",
                "💡 <i>Натисніть фігуру, потім клітинку, куди вона має піти. "
                "Дошку можна перевернути кнопкою нижче.</i>",
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _chess_display_axes(session):
        if session.get("board_flipped"):
            return range(7, -1, -1), range(7, -1, -1), CHESS_FILES[::-1]
        return range(8), range(8), CHESS_FILES

    @staticmethod
    def _chess_cell_text(session, row, column, display_column, legal):
        position = row * 8 + column
        piece = session["board"][position]
        special = legal.get(position)
        piece_symbol = CHESS_SYMBOLS.get(piece, "")
        if position == session["selected"]:
            symbol = CHESS_SELECTED_CELL + piece_symbol
        elif position in legal:
            symbol = (
                CHESS_CAPTURE_CELL + piece_symbol
                if piece or special == "en_passant"
                else CHESS_TARGET_CELL
            )
        else:
            symbol = piece_symbol or (
                CHESS_LIGHT_CELL if (row + column) % 2 == 0 else CHESS_DARK_CELL
            )
        rank = str(8 - row)
        if display_column == 0:
            return f"{rank} {symbol}"
        if display_column == 7:
            return f"{symbol} {rank}"
        return symbol

    @staticmethod
    def _chess_rich_cell(session, row, column, legal):
        position = row * 8 + column
        piece = session["board"][position]
        piece_symbol = CHESS_SYMBOLS.get(piece, "")
        special = legal.get(position)
        if position == session.get("selected"):
            return piece_symbol or CHESS_RICH_EMPTY_CELL, "primary", (
                CHESS_PREMIUM_EMOJI_IDS.get(piece) or CHESS_CELL_PREMIUM_EMOJI_ID
            )
        if position in legal:
            if piece or special == "en_passant":
                return piece_symbol or CHESS_RICH_EMPTY_CELL, "danger", (
                    CHESS_PREMIUM_EMOJI_IDS.get(piece) or CHESS_CELL_PREMIUM_EMOJI_ID
                )
            return CHESS_TARGET_CELL, "link", CHESS_MOVE_PREMIUM_EMOJI_ID
        return (
            piece_symbol or CHESS_RICH_EMPTY_CELL,
            "link",
            CHESS_PREMIUM_EMOJI_IDS.get(piece) or CHESS_CELL_PREMIUM_EMOJI_ID,
        )

    @staticmethod
    def _chess_plain_name(session, user_id):
        if user_id is None:
            return "очікується суперник"
        value = session.get("names", {}).get(str(user_id), f"ID {user_id}")
        return " ".join(str(value).split())[:48] or f"ID {user_id}"

    def _chess_rich_status(self, session):
        if session.get("winner") is not None:
            title = "Мат" if session.get("finish_reason") == "checkmate" else "Перемога"
            return f"🏆 {title}: {self._chess_plain_name(session, session['winner'])}"
        if session.get("draw"):
            reasons = {
                "stalemate": "Пат",
                "material": "Недостатньо матеріалу для мату",
                "fifty_moves": "Правило 50 ходів",
                "repetition": "Триразове повторення позиції",
            }
            return f"🤝 Нічия: {reasons.get(session.get('draw_reason'), 'за правилами гри')}"
        turn_id = session["players"][int(session["turn"])]
        return f"Хід: {self._chess_plain_name(session, turn_id) if turn_id else 'очікується суперник'}"

    @staticmethod
    def _chess_rich_table_cell(text=None, *, header=False):
        cell = {"align": "center", "valign": "middle"}
        if text is not None:
            cell["text"] = text
        if header:
            cell["is_header"] = True
        return cell

    @staticmethod
    def _chess_rich_button(
        button,
        *,
        text=None,
        style=None,
        disabled=False,
        custom_emoji_id=None,
        alternative_text=None,
    ):
        if custom_emoji_id and alternative_text:
            result = {
                "text": {
                    "type": "custom_emoji",
                    "custom_emoji_id": str(custom_emoji_id),
                    "alternative_text": str(alternative_text),
                }
            }
        else:
            result = {
                "text": str(button.get("text", "") if text is None else text)
            }
        if disabled:
            result["disabled"] = {}
            return result
        callback_data = button.get("_callback_data") or button.get("data")
        if callback_data:
            result["callback_data"] = str(callback_data)
        elif button.get("url"):
            result["url"] = str(button["url"])
        else:
            result["disabled"] = {}
        if style and "disabled" not in result:
            result["style"] = style
        return result

    @staticmethod
    def _chess_rich_control_style(text):
        value = str(text).casefold()
        if "здатися" in value or "завершити" in value:
            return "danger"
        if "продовжити" in value or "прийняти" in value:
            return "success"
        if "перевернути" in value or "оновити" in value:
            return "primary"
        return None

    def _register_rich_chess_callbacks(self, call, record, markup):
        generator = getattr(getattr(self, "inline", None), "generate_markup", None)
        if not callable(generator):
            return False
        cache = record.setdefault("rich_callback_data", {})
        for row in markup:
            for button in row:
                if button.get("callback"):
                    button["disable_security"] = True
                    callback = button["callback"]
                    key = "|".join(
                        (
                            getattr(callback, "__name__", callback.__class__.__name__),
                            repr(tuple(button.get("args", ()))),
                            repr(sorted(button.get("kwargs", {}).items())),
                        )
                    )
                    button["_rich_callback_key"] = key
                    if key in cache:
                        button["_callback_data"] = cache[key]
        unit_id = getattr(call, "unit_id", None) or record.get("rich_unit_id")
        units = getattr(self.inline, "_units", {})
        if unit_id and unit_id in units:
            record["rich_unit_id"] = unit_id
            units[unit_id]["buttons"] = markup
            units[unit_id]["disable_security"] = True
            generator(unit_id)
        else:
            generator(markup)
        for row in markup:
            for button in row:
                key = button.pop("_rich_callback_key", None)
                if key and button.get("_callback_data"):
                    cache[key] = button["_callback_data"]
        inline_message_id = getattr(call, "inline_message_id", None)
        if inline_message_id:
            record["rich_inline_message_id"] = inline_message_id
        chat_id = getattr(call, "chat_id", None)
        message_id = getattr(call, "message_id", None)
        message = getattr(call, "message", None)
        if message is not None:
            chat_id = chat_id or getattr(getattr(message, "chat", None), "id", None)
            message_id = message_id or getattr(message, "message_id", None)
        if chat_id is not None and message_id is not None:
            record["rich_chat_id"] = int(chat_id)
            record["rich_message_id"] = int(message_id)
        return True

    def _build_rich_chess_message(self, session, markup, network_game=None):
        legal = {}
        if (
            session.get("selected") is not None
            and not session.get("finished")
            and not session.get("promotion")
        ):
            legal = dict(
                self._chess_legal_moves(
                    session["board"],
                    session["turn"],
                    session["selected"],
                    session["castling"],
                    session.get("en_passant"),
                )
            )
        row_order, column_order, files = self._chess_display_axes(session)
        table = [
            [self._chess_rich_table_cell(CHESS_RICH_EMPTY_CELL, header=True)]
            + [self._chess_rich_table_cell(letter, header=True) for letter in files]
            + [self._chess_rich_table_cell(CHESS_RICH_EMPTY_CELL, header=True)]
        ]
        for display_row, row in enumerate(row_order):
            rank = str(8 - row)
            cells = [self._chess_rich_table_cell(rank, header=True)]
            for display_column, column in enumerate(column_order):
                symbol, style, emoji_id = self._chess_rich_cell(
                    session, row, column, legal
                )
                source = markup[display_row + 1][display_column]
                alternative_text = self._chess_emoji_alternatives.get(
                    str(emoji_id)
                )
                rich_button = self._chess_rich_button(
                    source,
                    text=symbol,
                    style=style,
                    custom_emoji_id=emoji_id if alternative_text else None,
                    alternative_text=alternative_text,
                )
                cells.append(
                    self._chess_rich_table_cell(
                        {"type": "button", "button": rich_button}
                    )
                )
            cells.append(self._chess_rich_table_cell(rank, header=True))
            table.append(cells)
        table.append(
            [self._chess_rich_table_cell(CHESS_RICH_EMPTY_CELL, header=True)]
            + [self._chess_rich_table_cell(letter, header=True) for letter in files]
            + [self._chess_rich_table_cell(CHESS_RICH_EMPTY_CELL, header=True)]
        )

        white, black = session["players"]
        title = "🌐 HikkaNet · Шахи" if network_game else "♟ Шахи · 8×8"
        players = [
            {"type": "bold", "text": f"♔ {self._chess_plain_name(session, white)}"},
            "    ",
            {"type": "bold", "text": f"♚ {self._chess_plain_name(session, black)}"},
        ]
        if session.get("promotion"):
            hint = "Оберіть фігуру, на яку перетвориться пішак."
        elif session.get("finish_confirm") is not None:
            hint = "Підтвердіть здачу або продовжте партію кнопками нижче."
        elif session.get("selected") is not None:
            hint = (
                f"Обрано {self._chess_coordinate(session['selected'])}. "
                "Зелена клітинка — хід, червона — взяття."
            )
        else:
            hint = (
                "Натисніть фігуру, потім клітинку, куди вона має піти. "
                "За потреби переверніть дошку."
            )
        blocks = [
            {"type": "heading", "text": title, "size": 4},
            {"type": "paragraph", "text": players},
            {
                "type": "paragraph",
                "text": {"type": "bold", "text": self._chess_rich_status(session)},
            },
            {
                "type": "table",
                "cells": table,
                "is_bordered": True,
                "is_striped": True,
                "is_compact": True,
            },
            {
                "type": "blockquote",
                "blocks": [{"type": "paragraph", "text": hint}],
            },
        ]
        for row in markup[10:]:
            buttons = []
            for button in row:
                disabled = str(button.get("text", "")).startswith("↶ Хід назад")
                buttons.append(
                    self._chess_rich_button(
                        button,
                        style=self._chess_rich_control_style(button.get("text")),
                        disabled=disabled,
                    )
                )
            if buttons:
                blocks.append({"type": "buttons", "buttons": buttons, "align": "center"})
        footer = [
            "чорні знизу" if session.get("board_flipped") else "білі знизу"
        ]
        if session.get("last_move"):
            footer.append(f"останній хід: {session['last_move']}")
        if network_game:
            footer.append(f"HikkaNet ID: {network_game.get('game_id', '')}")
        blocks.append({"type": "footer", "text": " · ".join(footer)})
        return {"blocks": blocks, "skip_entity_detection": True}

    def _inline_bot_token(self):
        inline = getattr(self, "inline", None)
        return getattr(inline, "_token", None) or getattr(
            getattr(inline, "bot", None), "token", None
        )

    async def _ensure_chess_emoji_alternatives(self):
        emoji_ids = tuple(
            dict.fromkeys(
                (
                    *CHESS_PREMIUM_EMOJI_IDS.values(),
                    CHESS_CELL_PREMIUM_EMOJI_ID,
                    CHESS_MOVE_PREMIUM_EMOJI_ID,
                )
            )
        )
        if all(
            emoji_id in self._chess_emoji_alternatives
            for emoji_id in emoji_ids
        ):
            return True
        if time.monotonic() < self._chess_emoji_alternatives_retry_at:
            return False
        token = self._inline_bot_token()
        if not token:
            return False

        self._chess_emoji_alternatives_retry_at = time.monotonic() + 300
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(
                    f"https://api.telegram.org/bot{token}/getCustomEmojiStickers",
                    json={"custom_emoji_ids": list(emoji_ids)},
                ) as response:
                    data = await response.json(content_type=None)
            if not isinstance(data, dict) or not data.get("ok"):
                description = (
                    data.get("description")
                    if isinstance(data, dict)
                    else "неочікувана відповідь Telegram"
                )
                raise ValueError(str(description))

            resolved = {}
            for sticker in data.get("result") or []:
                if not isinstance(sticker, dict):
                    continue
                emoji_id = str(sticker.get("custom_emoji_id") or "")
                alternative = str(sticker.get("emoji") or "").strip()
                if emoji_id in emoji_ids and alternative:
                    resolved[emoji_id] = alternative
            self._chess_emoji_alternatives.update(resolved)

            complete = all(
                emoji_id in self._chess_emoji_alternatives
                for emoji_id in emoji_ids
            )
            self._chess_emoji_alternatives_retry_at = time.monotonic() + (
                6 * 3600 if complete else 300
            )
            if not complete and not self._rich_emoji_warning_shown:
                logger.warning(
                    "Telegram повернув метадані лише для %d/%d шахових emoji; "
                    "для решти використано звичайні символи",
                    len(self._chess_emoji_alternatives),
                    len(emoji_ids),
                )
                self._rich_emoji_warning_shown = True
            return complete
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
        ) as error:
            if not self._rich_emoji_warning_shown:
                logger.warning(
                    "Не вдалося перевірити шахові custom emoji (%s); "
                    "використано звичайні символи",
                    error.__class__.__name__,
                )
                self._rich_emoji_warning_shown = True
            return False

    async def _edit_rich_chess_message(self, call, record, rich_message):
        inline_message_id = (
            getattr(call, "inline_message_id", None)
            or record.get("rich_inline_message_id")
        )
        chat_id = record.get("rich_chat_id")
        message_id = record.get("rich_message_id")
        if not inline_message_id and (chat_id is None or message_id is None):
            return False
        token = self._inline_bot_token()
        if not token:
            return False
        payload = {
            "rich_message": rich_message,
            "reply_markup": {"inline_keyboard": []},
        }
        if inline_message_id:
            payload["inline_message_id"] = str(inline_message_id)
        else:
            payload["chat_id"] = int(chat_id)
            payload["message_id"] = int(message_id)
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(
                    f"https://api.telegram.org/bot{token}/editMessageText",
                    json=payload,
                ) as response:
                    data = await response.json(content_type=None)
            if not isinstance(data, dict):
                raise ValueError("Telegram повернув неочікувану відповідь")
            if data.get("ok"):
                record["rich_mode"] = True
                return True
            description = str(data.get("description") or "невідома помилка")
            if "message is not modified" in description.casefold():
                return True
            if not self._rich_warning_shown:
                logger.warning("Telegram Rich Message недоступне: %s", description)
                self._rich_warning_shown = True
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as error:
            if not self._rich_warning_shown:
                logger.warning(
                    "Не вдалося відкрити Telegram Rich Message (%s)",
                    error.__class__.__name__,
                )
                self._rich_warning_shown = True
        return False

    async def _try_edit_rich_chess(self, call, token, *, network=False):
        try:
            if network:
                record = self._network_view(token)
                if record is None:
                    return False
                game = record["game"]
                if game.get("kind") != "chess" or game.get("status") not in {
                    "active",
                    "finished",
                }:
                    return False
                session = self._network_as_local_session(record)
                markup = self._markup_network(token)
                network_game = game
            else:
                record = self._session(token)
                if record is None or record.get("kind") != "chess":
                    return False
                session = record
                markup = self._markup(token)
                network_game = None
            if not self._register_rich_chess_callbacks(call, record, markup):
                return False
            await self._ensure_chess_emoji_alternatives()
            rich_message = self._build_rich_chess_message(
                session, markup, network_game=network_game
            )
            return await self._edit_rich_chess_message(call, record, rich_message)
        except Exception:
            logger.debug("Не вдалося зібрати Rich Message для шахів", exc_info=True)
            return False

    async def _edit_chess_panel(self, call, token):
        if await self._try_edit_rich_chess(call, token):
            return True
        return await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _edit_network_panel(self, call, token):
        if await self._try_edit_rich_chess(call, token, network=True):
            return True
        return await call.edit(
            self._render_network(token), reply_markup=self._markup_network(token)
        )

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
        row_order, column_order, files = self._chess_display_axes(session)
        coordinate_row = [
            {
                "text": letter,
                "callback": self._chess_coordinates,
                "args": (token,),
            }
            for letter in files
        ]
        rows = [coordinate_row]
        for row in row_order:
            buttons = []
            for display_column, column in enumerate(column_order):
                position = self._chess_index(row, column)
                buttons.append(
                    {
                        "text": self._chess_cell_text(
                            session, row, column, display_column, legal
                        ),
                        "callback": self._chess_click,
                        "args": (token, position),
                    }
                )
            rows.append(buttons)
        rows.append([dict(button) for button in coordinate_row])
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
                    {
                        "text": "↻ Перевернути дошку",
                        "callback": self._chess_flip,
                        "args": (token,),
                    },
                    {
                        "text": "↶ Хід назад",
                        "callback": self._chess_undo_info,
                        "args": (token,),
                    },
                ]
            )
            if session.get("finish_confirm") is not None:
                rows.append(
                    [
                        {
                            "text": "🏳 Так, здатися",
                            "callback": self._chess_confirm_resign,
                            "args": (token,),
                        },
                        {
                            "text": "↩ Продовжити гру",
                            "callback": self._chess_cancel_finish,
                            "args": (token,),
                        },
                    ]
                )
            else:
                rows.append(
                    [
                        {
                            "text": "🏳 Завершити партію",
                            "callback": self._chess_finish_prompt,
                            "args": (token,),
                        }
                    ]
                )
        return rows

    async def _chess_coordinates(self, call, token):
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        await call.answer(
            "Координати: літери — стовпці, цифри — горизонталі."
        )

    async def _chess_flip(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id not in [player for player in session.get("players", []) if player]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        session["board_flipped"] = not bool(session.get("board_flipped"))
        await self._edit_chess_panel(call, token)
        await call.answer("Дошку перевернуто")

    async def _chess_undo_info(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id not in [player for player in session.get("players", []) if player]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        await call.answer(
            "Підтверджені ходи не скасовуються — так суперник не може переписати партію.",
            show_alert=True,
        )

    async def _chess_finish_prompt(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if user_id not in [player for player in session.get("players", []) if player]:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        if session["finished"]:
            await call.answer("Партію вже завершено")
            return
        session["finish_confirm"] = user_id
        await self._edit_chess_panel(call, token)

    async def _chess_cancel_finish(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if session.get("finish_confirm") != user_id:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        session["finish_confirm"] = None
        await self._edit_chess_panel(call, token)

    async def _chess_confirm_resign(self, call, token):
        session = self._session(token)
        user_id, _ = self._actor(call)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if session.get("finish_confirm") != user_id:
            await call.answer(self.strings["not_yours"], show_alert=True)
            return
        await self._chess_resign(call, token)

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

            session["finish_confirm"] = None
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
                await self._edit_chess_panel(call, token)
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
            await self._edit_chess_panel(call, token)

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
            session["finish_confirm"] = None
            self._chess_finish_turn(session, side)
            labels = {"q": "ферзя", "r": "туру", "b": "слона", "n": "коня"}
            await call.answer(f"Пішака перетворено на {labels[choice]}")
            await self._edit_chess_panel(call, token)

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
            session["finish_confirm"] = None
            session["finished"] = True
            self._record_result(session, [opponents[0]])
            await self._edit_chess_panel(call, token)

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
        if session["kind"] == "chess":
            await self._edit_chess_panel(call, token)
        else:
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
            "active_network_games": sum(
                1
                for view in self._network_views.values()
                if view.get("game", {}).get("status") == "active"
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
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if session["kind"] == "chess":
            await self._edit_chess_panel(call, token)
        else:
            await call.edit(self._render(token), reply_markup=self._markup(token))

    def _purge_network_views(self):
        now = time.monotonic()
        for token, view in list(self._network_views.items()):
            if now - float(view.get("created_at", now)) > NETWORK_VIEW_TTL:
                self._network_views.pop(token, None)
                self._locks.pop(token, None)

    def _new_network_view(self, game, handle=None):
        self._purge_network_views()
        token = "n" + secrets.token_urlsafe(7)
        self._network_views[token] = {
            "game_id": str(game.get("game_id", "")),
            "game": game,
            "selected": None,
            "selected_row": None,
            "promotion": None,
            "board_flipped": game.get("my_slot") == 1,
            "finish_confirm": None,
            "handle": handle,
            "created_at": time.monotonic(),
        }
        self._locks[token] = asyncio.Lock()
        return token

    def _network_view(self, token):
        self._purge_network_views()
        return self._network_views.get(str(token))

    async def _network_owner_only(self, call):
        user_id, _ = self._actor(call)
        if user_id == int(self._me_id or 0):
            return True
        await call.answer(
            "🌐 Мережевою HikkaNet-партією керує власник цієї Hikka.",
            show_alert=True,
        )
        return False

    @staticmethod
    def _network_status(value):
        return {
            "waiting": "🔎 відкрита",
            "invited": "✉️ запрошення",
            "active": "🟢 триває",
            "finished": "🏁 завершена",
            "cancelled": "🚫 скасована",
        }.get(str(value), str(value))

    def _network_as_local_session(self, view):
        game = view["game"]
        players_data = list(game.get("players", []))
        players = [
            str(item.get("instance_id")) for item in players_data[:2]
        ]
        while len(players) < 2:
            players.append(None)
        names = {
            str(item.get("instance_id")): str(
                item.get("display_name") or item.get("instance_id") or "Hikka"
            )
            for item in players_data
            if item.get("instance_id")
        }
        state = dict(game.get("state", {}))
        state.update(
            kind=game.get("kind"),
            players=players,
            names=names,
            creator_id=players[0],
            invited_id=players[1],
            chat_id=0,
            selected=view.get("selected"),
            selected_row=view.get("selected_row"),
            board_flipped=bool(view.get("board_flipped")),
            finish_confirm=view.get("finish_confirm"),
        )
        if game.get("kind") == "chess":
            state["castling"] = set(state.get("castling", []))
            state["promotion"] = view.get("promotion")
            state["last_move"] = state.get("last_action")
        elif str(game.get("kind", "")).startswith("go"):
            state["history"] = set(state.get("history", []))
        winner = state.get("winner")
        if winner is not None and game.get("kind") != "ttt":
            with contextlib.suppress(TypeError, ValueError, IndexError):
                state["winner"] = players[int(winner)]
        return state

    def _render_network(self, token):
        view = self._network_view(token)
        if view is None:
            return "⌛ <b>Панель мережевої гри застаріла.</b> Відкрийте <code>.netgames</code>."
        game = view["game"]
        game_id = html.escape(str(game.get("game_id", "")))
        kind = str(game.get("kind", ""))
        status = str(game.get("status", ""))
        players = list(game.get("players", []))
        heading = (
            f"🌐 <b>HikkaNet · {NETWORK_LABELS.get(kind, kind)}</b>\n"
            f"ID: <code>{game_id}</code> · {self._network_status(status)}\n\n"
        )
        if status in {"waiting", "invited"}:
            creator = players[0].get("display_name", "Hikka") if players else "Hikka"
            if status == "waiting":
                detail = (
                    f"Створив: <b>{html.escape(str(creator))}</b>\n"
                    "Очікуємо суперника з будь-якого чату HikkaNet.\n\n"
                    f"Він може відкрити <code>.netgame {game_id}</code> або знайти партію в <code>.netgames</code>."
                )
            else:
                opponent = players[1].get("display_name", "Hikka") if len(players) > 1 else "Hikka"
                detail = (
                    f"<b>{html.escape(str(creator))}</b> запросив "
                    f"<b>{html.escape(str(opponent))}</b>.\n"
                    "Запрошений вузол має прийняти партію."
                )
            return heading + detail
        if status == "cancelled":
            return heading + "Партію скасовано або запрошення відхилено."
        session = self._network_as_local_session(view)
        renderer = getattr(self, f"_render_{kind}", None)
        if not callable(renderer):
            return heading + "Ця версія MiniGames не вміє показати гру."
        return heading + renderer(session)

    def _markup_network(self, token):
        view = self._network_view(token)
        if view is None:
            return []
        game = view["game"]
        status = str(game.get("status", ""))
        my_slot = game.get("my_slot")
        if status in {"waiting", "invited"}:
            rows = []
            if status == "waiting" and my_slot is None:
                rows.append(
                    [{"text": "✅ Приєднатися", "callback": self._net_join, "args": (token,)}]
                )
            elif status == "invited" and my_slot == 1:
                rows.append(
                    [
                        {"text": "✅ Прийняти", "callback": self._net_join, "args": (token,)},
                        {"text": "🚫 Відхилити", "callback": self._net_cancel, "args": (token,)},
                    ]
                )
            elif my_slot == 0:
                rows.append(
                    [{"text": "🚫 Скасувати", "callback": self._net_cancel, "args": (token,)}]
                )
            rows.append(
                [
                    {"text": "🔄 Оновити", "callback": self._net_refresh, "args": (token,)},
                    {"text": "✖️ Закрити", "callback": self._net_close, "args": (token,)},
                ]
            )
            return rows
        if status == "cancelled":
            return [[{"text": "✖️ Закрити", "callback": self._net_close, "args": (token,)}]]
        session = self._network_as_local_session(view)
        builder = getattr(self, f"_markup_{game.get('kind')}", None)
        if not callable(builder):
            return [[{"text": "✖️ Закрити", "callback": self._net_close, "args": (token,)}]]
        rows = builder(token, session)
        callback_map = {
            "_ttt_move": self._net_ttt_move,
            "_checker_click": self._net_checker_click,
            "_checker_resign": self._net_resign,
            "_chess_click": self._net_chess_click,
            "_chess_promote": self._net_chess_promote,
            "_chess_resign": self._net_resign,
            "_chess_coordinates": self._net_chess_coordinates,
            "_chess_flip": self._net_chess_flip,
            "_chess_undo_info": self._net_chess_undo_info,
            "_chess_finish_prompt": self._net_chess_finish_prompt,
            "_chess_confirm_resign": self._net_chess_confirm_resign,
            "_chess_cancel_finish": self._net_chess_cancel_finish,
            "_go_select_row": self._net_go_select_row,
            "_go_clear_row": self._net_go_clear_row,
            "_go_place": self._net_go_place,
            "_go_pass": self._net_go_pass,
            "_go_resign": self._net_resign,
            "_rematch": self._net_rematch,
            "_show_top": self._net_game_top,
            "_close": self._net_close,
        }
        for row in rows:
            for button in row:
                callback = button.get("callback")
                name = getattr(callback, "__name__", "")
                if name in callback_map:
                    button["callback"] = callback_map[name]
                if (
                    game.get("kind") == "ttt"
                    and status == "active"
                    and name == "_close"
                ):
                    button["text"] = "🏳 Здатися"
                    button["callback"] = self._net_resign
        if status == "active":
            rows.append(
                [
                    {"text": "🔄 Оновити", "callback": self._net_refresh, "args": (token,)},
                    {"text": "↩️ Закрити панель", "callback": self._net_close, "args": (token,)},
                ]
            )
        return rows

    async def _network_lobby_data(self):
        network = self._network()
        if network is None:
            raise RuntimeError("Встанови й налаштуй HikkaNet 2.0.0")
        mine, opened = await asyncio.gather(
            network.api_games(scope="mine", limit=12),
            network.api_games(scope="open", limit=12),
        )
        return {
            "mine": list(mine.get("games", [])),
            "open": list(opened.get("games", [])),
        }

    def _network_lobby_text(self, data):
        mine = data.get("mine", [])
        opened = data.get("open", [])
        active = sum(game.get("status") == "active" for game in mine)
        invited = sum(game.get("status") == "invited" and game.get("my_slot") == 1 for game in mine)
        return (
            "🌐 <b>MiniGames · HikkaNet</b>\n\n"
            "Грайте між різними чатами й різними Hikka. Ходи перевіряє сервер, "
            "а результати потрапляють у глобальний рейтинг.\n\n"
            f"Ваші активні: <b>{active}</b> · запрошення: <b>{invited}</b>\n"
            f"Відкриті партії: <b>{len(opened)}</b>\n\n"
            "Створіть відкриту партію або оберіть наявну нижче. Для приватного "
            "запрошення: <code>.netgame chess instance-id</code>."
        )

    def _network_lobby_markup(self, data, back_token=""):
        rows = [
            [
                {"text": "❌⭕ Створити", "callback": self._net_create, "args": ("ttt",)},
                {"text": "⚪⚫ Шашки", "callback": self._net_create, "args": ("checkers",)},
            ],
            [
                {"text": "♟ Шахи", "callback": self._net_create, "args": ("chess",)},
                {"text": "⚫⚪ Ґо 9×9", "callback": self._net_create, "args": ("go9",)},
            ],
            [{"text": "⚫⚪ Ґо 13×13", "callback": self._net_create, "args": ("go13",)}],
        ]
        actionable = [
            game
            for game in data.get("mine", [])
            if game.get("status") in {"waiting", "invited", "active"}
        ][:6]
        if actionable:
            rows.append([{"text": "— Мої партії —", "callback": self._net_noop}])
            for game in actionable:
                label = NETWORK_LABELS.get(game.get("kind"), game.get("kind"))
                rows.append(
                    [
                        {
                            "text": f"{label} · {self._network_status(game.get('status'))}",
                            "callback": self._net_open,
                            "args": (game.get("game_id"),),
                        }
                    ]
                )
        opened = data.get("open", [])[:6]
        if opened:
            rows.append([{"text": "— Відкриті партії —", "callback": self._net_noop}])
            for game in opened:
                creator = (game.get("players") or [{}])[0].get("display_name", "Hikka")
                creator = " ".join(str(creator).split())[:20] or "Hikka"
                label = NETWORK_LABELS.get(game.get("kind"), game.get("kind"))
                rows.append(
                    [
                        {
                            "text": f"➕ {label} · {creator}",
                            "callback": self._net_join_id,
                            "args": (game.get("game_id"),),
                        }
                    ]
                )
        rows.append(
            [
                {"text": "🏆 Глобальний топ", "callback": self._net_lobby_top, "args": (back_token,)},
                {"text": "🔄 Оновити", "callback": self._network_lobby_callback, "args": (back_token,)},
            ]
        )
        if back_token and self._session(back_token) is not None:
            rows.append(
                [{"text": "↩️ Локальні ігри", "callback": self._net_back_local, "args": (back_token,)}]
            )
        else:
            rows.append([{"text": "✖️ Закрити", "action": "close"}])
        return rows

    async def _net_noop(self, call):
        await call.answer("Оберіть партію нижче")

    async def _network_lobby_callback(self, call, back_token=""):
        if not await self._network_owner_only(call):
            return
        try:
            data = await self._network_lobby_data()
            await call.edit(
                self._network_lobby_text(data),
                reply_markup=self._network_lobby_markup(data, back_token),
            )
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_back_local(self, call, token):
        if not await self._network_owner_only(call):
            return
        session = self._session(token)
        if session is None:
            await call.answer(self.strings["expired"], show_alert=True)
            return
        if session["kind"] == "chess":
            await self._edit_chess_panel(call, token)
        else:
            await call.edit(self._render(token), reply_markup=self._markup(token))

    async def _net_switch(self, call, game):
        token = self._new_network_view(game, handle=call)
        await self._edit_network_panel(call, token)

    async def _net_create(self, call, kind):
        if not await self._network_owner_only(call):
            return
        network = self._network()
        if network is None:
            await call.answer("HikkaNet 2.0.0 недоступна", show_alert=True)
            return
        try:
            await self._net_switch(call, await network.api_game_create(kind))
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_open(self, call, game_id):
        if not await self._network_owner_only(call):
            return
        try:
            await self._net_switch(call, await self._network().api_game_get(game_id))
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_join_id(self, call, game_id):
        if not await self._network_owner_only(call):
            return
        try:
            game = await self._network().api_game_join(game_id)
            await self._net_switch(call, game)
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_join(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        try:
            was_unassigned = view["game"].get("my_slot") is None
            view["game"] = await self._network().api_game_join(view["game_id"])
            if was_unassigned and view["game"].get("my_slot") == 1:
                view["board_flipped"] = True
            view["handle"] = call
            await self._edit_network_panel(call, token)
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_refresh(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        try:
            view["game"] = await self._network().api_game_get(view["game_id"])
            view["selected"] = None
            view["selected_row"] = None
            view["promotion"] = None
            view["finish_confirm"] = None
            view["handle"] = call
            await self._edit_network_panel(call, token)
            await call.answer("Оновлено")
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_close(self, call, token):
        if not await self._network_owner_only(call):
            return
        self._network_views.pop(token, None)
        self._locks.pop(token, None)
        await call.edit(
            "🌐 <b>Панель HikkaNet-гри закрито.</b> Сама партія збережена в мережі.",
            reply_markup=[],
        )

    async def _net_cancel(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        try:
            view["game"] = await self._network().api_game_cancel(view["game_id"])
            await call.edit(
                self._render_network(token), reply_markup=self._markup_network(token)
            )
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_resign(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        try:
            view["game"] = await self._network().api_game_resign(view["game_id"])
            view["selected"] = None
            view["selected_row"] = None
            view["finish_confirm"] = None
            view["handle"] = call
            await self._edit_network_panel(call, token)
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_rematch(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        game = view["game"]
        network = self._network()
        own_instance = str(network.config.get("instance_id", ""))
        opponent = next(
            (
                item.get("instance_id")
                for item in game.get("players", [])
                if item.get("instance_id") != own_instance
            ),
            None,
        )
        if not opponent:
            await call.answer("Не вдалося визначити суперника", show_alert=True)
            return
        try:
            fresh = await network.api_game_create(game.get("kind"), opponent)
            self._network_views.pop(token, None)
            self._locks.pop(token, None)
            await self._net_switch(call, fresh)
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_submit(self, call, token, action):
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return False
        lock = self._locks.setdefault(token, asyncio.Lock())
        async with lock:
            try:
                view["game"] = await self._network().api_game_move(
                    view["game_id"], view["game"]["revision"], action
                )
            except Exception as error:
                if getattr(error, "code", "") == "revision_conflict":
                    with contextlib.suppress(Exception):
                        view["game"] = await self._network().api_game_get(
                            view["game_id"]
                        )
                    await call.answer(
                        "Суперник уже оновив партію. Стан синхронізовано.",
                        show_alert=True,
                    )
                    view["handle"] = call
                    await self._edit_network_panel(call, token)
                    return False
                await call.answer(str(error)[:180], show_alert=True)
                return False
            view["selected"] = None
            view["selected_row"] = None
            view["promotion"] = None
            view["finish_confirm"] = None
            view["handle"] = call
            await self._edit_network_panel(call, token)
            return True

    async def _net_require_turn(self, call, view):
        game = view["game"]
        if game.get("status") != "active":
            await call.answer("Партія ще не активна або вже завершена", show_alert=True)
            return False
        if game.get("my_slot") != game.get("state", {}).get("turn"):
            await call.answer("Зараз хід суперника", show_alert=True)
            return False
        return True

    async def _net_ttt_move(self, call, token, position):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        await self._net_submit(
            call, token, {"type": "place", "position": int(position)}
        )

    async def _net_checker_click(self, call, token, position):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        session = self._network_as_local_session(view)
        side = int(session["turn"])
        position = int(position)
        board = session["board"]
        if self._checker_owned(board[position], side):
            forced = session.get("forced_piece")
            if forced is not None and position != int(forced):
                await call.answer("Продовжуйте взяття цією ж шашкою", show_alert=True)
                return
            if not self._checker_moves_for(board, side, position):
                await call.answer("У цієї шашки немає доступного ходу", show_alert=True)
                return
            view["selected"] = position
            view["handle"] = call
            await call.edit(
                self._render_network(token), reply_markup=self._markup_network(token)
            )
            return
        selected = view.get("selected")
        if selected is None:
            await call.answer("Спочатку оберіть свою шашку", show_alert=True)
            return
        await self._net_submit(
            call,
            token,
            {"type": "move", "source": int(selected), "target": position},
        )

    async def _net_chess_coordinates(self, call, token):
        if not await self._network_owner_only(call):
            return
        if self._network_view(token) is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        await call.answer("Координати шахової дошки")

    async def _net_chess_flip(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        view["board_flipped"] = not bool(view.get("board_flipped"))
        view["handle"] = call
        await self._edit_network_panel(call, token)
        await call.answer("Дошку перевернуто")

    async def _net_chess_undo_info(self, call, token):
        if not await self._network_owner_only(call):
            return
        if self._network_view(token) is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        await call.answer(
            "Підтверджені сервером ходи не скасовуються — суперник не може переписати партію.",
            show_alert=True,
        )

    async def _net_chess_finish_prompt(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        game = view["game"]
        if game.get("status") != "active":
            await call.answer("Партія ще не активна або вже завершена", show_alert=True)
            return
        if game.get("my_slot") not in {0, 1}:
            await call.answer("Ви не учасник цієї партії", show_alert=True)
            return
        view["finish_confirm"] = True
        view["handle"] = call
        await self._edit_network_panel(call, token)

    async def _net_chess_cancel_finish(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        if view.get("finish_confirm") is None:
            await call.answer("Підтвердження вже закрито")
            return
        view["finish_confirm"] = None
        view["handle"] = call
        await self._edit_network_panel(call, token)

    async def _net_chess_confirm_resign(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        if view.get("finish_confirm") is None:
            await call.answer("Спочатку підтвердьте завершення", show_alert=True)
            return
        await self._net_resign(call, token)

    async def _net_chess_click(self, call, token, position):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        view["finish_confirm"] = None
        if view.get("promotion"):
            await call.answer("Спочатку оберіть фігуру перетворення", show_alert=True)
            return
        session = self._network_as_local_session(view)
        side = int(session["turn"])
        position = int(position)
        board = session["board"]
        if self._chess_side(board[position]) == side:
            if not self._chess_legal_moves(
                board,
                side,
                position,
                session["castling"],
                session.get("en_passant"),
            ):
                await call.answer("У цієї фігури немає дозволених ходів", show_alert=True)
                return
            view["selected"] = position
            view["handle"] = call
            await self._edit_network_panel(call, token)
            return
        selected = view.get("selected")
        if selected is None:
            await call.answer("Спочатку оберіть свою фігуру", show_alert=True)
            return
        legal = dict(
            self._chess_legal_moves(
                board,
                side,
                selected,
                session["castling"],
                session.get("en_passant"),
            )
        )
        if position not in legal:
            await call.answer("Цей хід неможливий", show_alert=True)
            return
        piece = board[int(selected)]
        target_row, _ = self._chess_position(position)
        if piece.lower() == "p" and target_row in {0, 7}:
            view["promotion"] = {
                "source": int(selected),
                "target": position,
                "side": side,
                "position": position,
            }
            view["handle"] = call
            await self._edit_network_panel(call, token)
            return
        await self._net_submit(
            call,
            token,
            {"type": "move", "source": int(selected), "target": position},
        )

    async def _net_chess_promote(self, call, token, choice):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        pending = view.get("promotion") if view else None
        if not pending or str(choice).lower() not in {"q", "r", "b", "n"}:
            await call.answer("Перетворення вже недоступне", show_alert=True)
            return
        await self._net_submit(
            call,
            token,
            {
                "type": "move",
                "source": int(pending["source"]),
                "target": int(pending["target"]),
                "promotion": str(choice).lower(),
            },
        )

    async def _net_go_select_row(self, call, token, row):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        size = int(view["game"].get("state", {}).get("go_size", 0))
        row = int(row)
        if not 0 <= row < size:
            await call.answer("Некоректний рядок", show_alert=True)
            return
        view["selected_row"] = row
        view["handle"] = call
        await call.edit(
            self._render_network(token), reply_markup=self._markup_network(token)
        )

    async def _net_go_clear_row(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        view["selected_row"] = None
        await call.edit(
            self._render_network(token), reply_markup=self._markup_network(token)
        )

    async def _net_go_place(self, call, token, column):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        row = view.get("selected_row")
        size = int(view["game"].get("state", {}).get("go_size", 0))
        column = int(column)
        if row is None or not 0 <= column < size:
            await call.answer("Спочатку оберіть рядок", show_alert=True)
            return
        await self._net_submit(
            call,
            token,
            {"type": "place", "position": int(row) * size + column},
        )

    async def _net_go_pass(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None or not await self._net_require_turn(call, view):
            return
        await self._net_submit(call, token, {"type": "pass"})

    async def _network_top_text(self, kind=None):
        data = await self._network().api_game_leaderboard(
            kind=kind or None, sort="rating" if kind else "wins", limit=20
        )
        label = NETWORK_LABELS.get(kind, "усі ігри") if kind else "усі ігри"
        lines = [f"🏆 <b>HikkaNet · {label}</b>", ""]
        medals = ("🥇", "🥈", "🥉")
        for index, item in enumerate(data.get("ranking", [])[:20]):
            lines.append(
                f"{medals[index] if index < 3 else '▫️'} "
                f"<b>{html.escape(str(item.get('display_name') or item.get('public_id') or 'Гравець'))}</b> — "
                f"{int(item.get('wins', 0))}–{int(item.get('losses', 0))}–"
                f"{int(item.get('draws', 0))} · {int(item.get('played', 0))} ігор · "
                f"рейтинг {float(item.get('rating', 1000)):g}"
            )
        if not data.get("ranking"):
            lines.append("<i>Завершених мережевих партій ще немає.</i>")
        return "\n".join(lines)

    async def _net_lobby_top(self, call, back_token=""):
        if not await self._network_owner_only(call):
            return
        try:
            await call.edit(
                await self._network_top_text(),
                reply_markup=[
                    [
                        {
                            "text": "↩️ До HikkaNet-ігор",
                            "callback": self._network_lobby_callback,
                            "args": (back_token,),
                        }
                    ]
                ],
            )
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_game_top(self, call, token):
        if not await self._network_owner_only(call):
            return
        view = self._network_view(token)
        if view is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        try:
            await call.edit(
                await self._network_top_text(view["game"].get("kind")),
                reply_markup=[
                    [{"text": "↩️ До партії", "callback": self._net_back_game, "args": (token,)}]
                ],
            )
        except Exception as error:
            await call.answer(str(error)[:180], show_alert=True)

    async def _net_back_game(self, call, token):
        if not await self._network_owner_only(call):
            return
        if self._network_view(token) is None:
            await call.answer("Панель застаріла", show_alert=True)
            return
        await self._edit_network_panel(call, token)

    async def _open_network_game(self, message, game):
        token = self._new_network_view(game)
        try:
            opened = await self.inline.form(
                self._render_network(token),
                message,
                reply_markup=self._markup_network(token),
                disable_security=True,
            )
        except Exception:
            opened = False
        if not opened:
            self._network_views.pop(token, None)
            self._locks.pop(token, None)
            await utils.answer(message, self.strings["inline_failed"])
            return
        if callable(getattr(opened, "edit", None)):
            self._network_views[token]["handle"] = opened
            await self._edit_network_panel(opened, token)

    async def _open_network_lobby(self, message):
        try:
            data = await self._network_lobby_data()
            opened = await self.inline.form(
                self._network_lobby_text(data),
                message,
                reply_markup=self._network_lobby_markup(data),
                disable_security=True,
            )
            if opened:
                return
        except Exception as error:
            await utils.answer(message, f"❌ <code>{html.escape(str(error)[:300])}</code>")
            return
        await utils.answer(message, self.strings["inline_failed"])

    async def _create_network_game(self, message, kind, opponent=None):
        network = self._network()
        if network is None:
            await utils.answer(
                message,
                "❌ Потрібен налаштований <b>HikkaNet 2.0.0</b>. Перевір <code>.hknetstatus</code>.",
            )
            return
        try:
            game = await network.api_game_create(kind, opponent or None)
        except Exception as error:
            await utils.answer(message, f"❌ <code>{html.escape(str(error)[:300])}</code>")
            return
        await self._open_network_game(message, game)

    async def _direct_game(self, message, kind):
        raw = str(utils.get_args_raw(message) or "").strip()
        parts = raw.split()
        if parts and parts[0].lower() in {"net", "network", "global", "мережа"}:
            if kind == "gomenu":
                await utils.answer(
                    message,
                    "Для мережевої партії обери <code>.go9 net [instance-id]</code> "
                    "або <code>.go13 net [instance-id]</code>.",
                )
                return
            await self._create_network_game(
                message, kind, parts[1] if len(parts) > 1 else None
            )
            return
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
        """🏆 .gametop [global [ttt|checkers|chess|go9|go13]]"""
        parts = str(utils.get_args_raw(message) or "").strip().lower().split()
        if parts and parts[0] in {"global", "net", "network", "мережа"}:
            kind = parts[1] if len(parts) > 1 else None
            if kind and kind not in NETWORK_KINDS:
                await utils.answer(
                    message,
                    "❌ Гра: <code>ttt</code>, <code>checkers</code>, <code>chess</code>, "
                    "<code>go9</code> або <code>go13</code>.",
                )
                return
            if self._network() is None:
                await utils.answer(message, "❌ HikkaNet 2.0.0 недоступна.")
                return
            try:
                await utils.answer(message, await self._network_top_text(kind))
            except Exception as error:
                await utils.answer(message, f"❌ <code>{html.escape(str(error)[:300])}</code>")
            return
        await utils.answer(message, self._top_text(self._chat_id(message)))

    @loader.command(ru_doc="Відкрити лобі глобальних HikkaNet-ігор")
    async def netgames(self, message):
        """🌐 Відкриті партії, запрошення та власні матчі"""
        await self._open_network_lobby(message)

    @loader.command(ru_doc="Створити або відкрити HikkaNet-партію")
    async def netgame(self, message):
        """🌐 .netgame <game|game-id> [instance-id]"""
        parts = str(utils.get_args_raw(message) or "").strip().split()
        if not parts:
            await self._open_network_lobby(message)
            return
        value = parts[0].lower()
        aliases = {
            "хрестики": "ttt",
            "шашки": "checkers",
            "шахи": "chess",
            "го9": "go9",
            "ґо9": "go9",
            "го13": "go13",
            "ґо13": "go13",
        }
        value = aliases.get(value, value)
        network = self._network()
        if network is None:
            await utils.answer(message, "❌ HikkaNet 2.0.0 недоступна.")
            return
        if value in NETWORK_KINDS:
            await self._create_network_game(
                message, value, parts[1] if len(parts) > 1 else None
            )
            return
        try:
            game = await network.api_game_get(value)
        except Exception as error:
            await utils.answer(
                message,
                "❌ Вкажи тип гри або чинний ID: "
                f"<code>{html.escape(str(error)[:240])}</code>",
            )
            return
        await self._open_network_game(message, game)

    @loader.command(ru_doc="Прийняти HikkaNet-партію за ID")
    async def netjoin(self, message):
        """✅ .netjoin <game-id>"""
        game_id = str(utils.get_args_raw(message) or "").strip().lower()
        if not game_id:
            await utils.answer(message, "Використання: <code>.netjoin ng_…</code>")
            return
        network = self._network()
        if network is None:
            await utils.answer(message, "❌ HikkaNet 2.0.0 недоступна.")
            return
        try:
            game = await network.api_game_join(game_id)
        except Exception as error:
            await utils.answer(message, f"❌ <code>{html.escape(str(error)[:300])}</code>")
            return
        await self._open_network_game(message, game)

    @loader.command(ru_doc="Власна глобальна статистика HikkaNet-ігор")
    async def netprofile(self, message):
        """📊 Перемоги, поразки, нічиї та рейтинг за іграми"""
        network = self._network()
        if network is None:
            await utils.answer(message, "❌ HikkaNet 2.0.0 недоступна.")
            return
        try:
            profile = await network.api_game_profile()
        except Exception as error:
            await utils.answer(message, f"❌ <code>{html.escape(str(error)[:300])}</code>")
            return
        totals = profile.get("totals", {})
        lines = [
            "📊 <b>Мій профіль HikkaNet Games</b>",
            "",
            f"Ігор: <b>{int(totals.get('played', 0))}</b>",
            f"Перемог: <b>{int(totals.get('wins', 0))}</b>",
            f"Поразок: <b>{int(totals.get('losses', 0))}</b>",
            f"Нічиїх: <b>{int(totals.get('draws', 0))}</b>",
        ]
        for item in profile.get("by_game", []):
            lines.extend(
                [
                    "",
                    f"{NETWORK_LABELS.get(item.get('kind'), item.get('kind'))}: "
                    f"{int(item.get('wins', 0))}–{int(item.get('losses', 0))}–"
                    f"{int(item.get('draws', 0))} · Elo <b>{float(item.get('rating', 1000)):g}</b>",
                ]
            )
        await utils.answer(message, "\n".join(lines))
