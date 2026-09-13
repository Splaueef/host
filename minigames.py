# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: Інтерактивні мініігри для чатів із кнопками та рейтингом
# scope: inline
# scope: hikka_only

__version__ = (1, 0, 0)

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
        if session["kind"] in {"ttt", "rps", "dice"} and session["players"][1] is not None:
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

    @loader.command(ru_doc="Запустити швидку вікторину для всього чату")
    async def quiz(self, message):
        """🧠 Вікторина на швидкість"""
        await self._open(message, "quiz")

    @loader.command(ru_doc="Показати рейтинг мініігор поточного чату")
    async def gametop(self, message):
        """🏆 Перемоги та кількість зіграних ігор"""
        await utils.answer(message, self._top_text(self._chat_id(message)))
