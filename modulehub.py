# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: 🧭 Єдине inline-меню для основних модулів Hikka.
# scope: inline
# scope: hikka_only

import contextlib
import inspect
import logging

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class ModuleHubMod(loader.Module):
    """🧭 Єдина зручна панель для всіх основних модулів"""

    strings = {
        "name": "ModuleHub",
        "inline_failed": (
            "❌ <b>Не вдалося відкрити inline-меню.</b>\n"
            "<i>Перевірте налаштування inline-бота Hikka.</i>"
        ),
    }

    PAGE_SIZE = 6

    MODULES = {
        "contactstatus": {
            "class": "ContactStatusMod",
            "name": "ContactStatus",
            "icon": "🟢",
            "description": "Online-статистика, watchlist, графіки та експорт.",
        },
        "stats": {
            "class": "DailyStatMod",
            "name": "DailyStat",
            "icon": "📊",
            "description": "Ваша денна і тижнева Telegram-активність.",
        },
        "analysis": {
            "class": "ChatAnalysisMod",
            "name": "ChatAnalysis",
            "icon": "📈",
            "description": "Повний аналіз історії поточного чату.",
        },
        "nekospy": {
            "class": "NekoSpy",
            "name": "NekoSpy",
            "icon": "🐈",
            "description": "Збереження видалених і одноразових повідомлень.",
        },
        "vdlt": {
            "class": "VideoDownloaderMod",
            "name": "VideoDownloader",
            "icon": "🎬",
            "description": "Завантаження відео, музики, транскриптів і діагностика.",
        },
        "dailynews": {
            "class": "DailyNewsMod",
            "name": "DailyNews",
            "icon": "📰",
            "description": "Автоматичні новинні дайджести та порівняння джерел.",
        },
        "mistral": {
            "class": "MistralModule",
            "name": "MistralAI",
            "icon": "🤖",
            "description": "AI-асистент, агенти, OCR, voice, embeddings і моделі.",
        },
        "gemma": {
            "class": "GemmaSelf",
            "name": "GemmaSelf",
            "icon": "💬",
            "description": "AI-відповіді від імені акаунта та керування контекстом.",
        },
        "math": {
            "class": "MathSolverMod",
            "name": "MathSolver",
            "icon": "🧮",
            "description": "Обчислення, конвертація, LaTeX, графіки та анімації.",
        },
        "werwolf": {
            "class": "WerwolfStatsMod",
            "name": "Werwolf / RotKranz",
            "icon": "🐺",
            "description": "Профілі, чати, рейтинги, пети, друзі та економіка.",
        },
        "systemd": {
            "class": "SystemdMod",
            "name": "Systemd",
            "icon": "🖥",
            "description": "Стан, запуск, зупинка і журнали сервісів.",
        },
        "backup": {
            "class": "FullBackupMod",
            "name": "FullBackup",
            "icon": "💾",
            "description": "Повне резервне копіювання і відновлення Hikka.",
        },
        "quiet": {
            "class": "QuietScheduleMod",
            "name": "QuietSchedule",
            "icon": "🌙",
            "description": "Розклад тихого режиму для чатів.",
        },
        "sysinfo": {
            "class": "InfoMod",
            "name": "SysInfo",
            "icon": "🖥️",
            "description": "Стан VPS, CPU, RAM, диска та Hikka.",
        },
        "timeinfo": {
            "class": "TimeInfoMod",
            "name": "TimeInfo",
            "icon": "🕐",
            "description": "Час, часові пояси, timestamp та uptime.",
        },
        "teledocs": {
            "class": "TeledocsMod",
            "name": "TeleDocs",
            "icon": "📖",
            "description": "Пошук в Telegram API-документації.",
        },
        "purge": {
            "class": "PurgeMod",
            "name": "Purge",
            "icon": "🧹",
            "description": "Видалення окремих повідомлень і діапазонів.",
        },
        "eval": {
            "class": "Evaluator",
            "name": "Evaluator",
            "icon": "🧪",
            "description": "Виконання коду в пісочницях різних мов.",
        },
    }

    SECTIONS = (
        ("📊 Статистика", ("contactstatus", "stats", "analysis")),
        ("🤖 AI та медіа", ("vdlt", "dailynews", "mistral", "gemma", "math")),
        ("🐺 RotKranz", ("werwolf",)),
        ("🛠 Система", ("nekospy", "systemd", "backup", "quiet", "sysinfo")),
        ("🧰 Інструменти", ("timeinfo", "teledocs", "purge", "eval")),
    )

    SAFE_EMPTY = {
        "purge",
        "del",
        "contactstatus",
        "contactlist",
        "contactadd",
        "contactremove",
        "contactsync",
        "contactautowatch",
        "contacttimezone",
        "contactstats",
        "contactchart",
        "contactexport",
        "contactinsights",
        "ds",
        "spyinfo",
        "spymode",
        "spyblclear",
        "spywlclear",
        "vdl",
        "vdlaudio",
        "vdlq",
        "vdlchannels",
        "vdlset",
        "vdlqueue",
        "vdlcookies",
        "vdlruntime",
        "vdldiag",
        "vdllist",
        "vdlpmlist",
        "vdlbans",
        "vdlstats",
        "vdlhelp",
        "newsstatus",
        "newsrun",
        "newstoday",
        "newsyesterday",
        "newsweek",
        "newscompare",
        "newsreset",
        "wwhelp",
        "wwtest",
        "wwkey",
        "я",
        "чат",
        "топ",
        "зв",
        "графік",
        "профіль",
        "пет",
        "друзі",
        "курс",
        "чати",
        "рейтинг",
        "нік",
        "чатнік",
        "graphhelp",
        "units",
        "backupall",
        "restoreall",
        "qnow",
        "qhelp",
        "qlist",
        "info",
        "time",
        "timezone",
        "timestamp",
        "uptime",
        "getattrs",
        "mistralhelp",
        "mistralmodels",
        "mistralagents",
        "mistralautolist",
        "mistralstats",
        "mistraldeps",
        "mistralthoughts",
        "mistralmode",
        "mistralclear",
        "mistralauto",
        "gmclear",
        "gmself",
        "vdlupdate",
        "vdlreset",
    }

    MUTATING = {
        "purge",
        "del",
        "contactadd",
        "contactremove",
        "contactautowatch",
        "contacttimezone",
        "contactclear",
        "spymode",
        "spybl",
        "spyblclear",
        "spywl",
        "spywlclear",
        "newsrun",
        "newstoday",
        "newsyesterday",
        "newsweek",
        "newsadd",
        "newsreset",
        "vdl",
        "vdlaudio",
        "vdlq",
        "vdlchannels",
        "vdlset",
        "vdlupdate",
        "vdladd",
        "vdlrm",
        "vdlpm",
        "vdlban",
        "vdlunban",
        "vdlreset",
        "wwkey",
        "wwapi",
        "нік",
        "переказ",
        "чатнік",
        "петдія",
        "друг",
        "addunit",
        "delunit",
        "unit",
        "nameunit",
        "qadd",
        "qdel",
        "backupall",
        "restoreall",
        "gmclear",
        "gmself",
        "mistralcreate",
        "mistraldelete",
        "mistraluse",
        "mistralauto",
        "mistralmode",
        "mistralclear",
        "eval",
        "e",
        "ecpp",
        "ec",
        "enode",
        "ephp",
        "eruby",
        "ebf",
    }

    SECRET_INPUT = {"wwkey"}

    READONLY_WHEN_EMPTY = {
        "contactautowatch",
        "contacttimezone",
        "vdlq",
        "vdlchannels",
        "vdlset",
        "wwkey",
        "нік",
        "чатнік",
        "mistralmode",
    }

    async def client_ready(self, client, db):
        self._client = client

    @staticmethod
    def _chunks(items, size=2):
        return [items[index : index + size] for index in range(0, len(items), size)]

    def _find_module(self, key):
        spec = self.MODULES.get(key)
        if not spec:
            return None
        return next(
            (
                module
                for module in getattr(self.allmodules, "modules", [])
                if module.__class__.__name__ == spec["class"]
            ),
            None,
        )

    def _module_commands(self, key):
        if key == "analysis":
            return [("аналіз", "Повний аналіз поточного чату")]
        module = self._find_module(key)
        if module is None:
            return []
        result = []
        for command, handler in getattr(module, "commands", {}).items():
            doc = inspect.getdoc(handler) or "Без опису"
            result.append((command, doc))
        return result

    def _loaded_count(self):
        return sum(self._find_module(key) is not None for key in self.MODULES)

    def _is_dangerous(self, command, args=""):
        return command in self.MUTATING and not (
            not str(args or "").strip()
            and command in self.READONLY_WHEN_EMPTY
        )

    def _home_text(self):
        return (
            "🧭 <b>ModuleHub · головне меню</b>\n\n"
            f"✅ Активно: <b>{self._loaded_count()}</b> із "
            f"<b>{len(self.MODULES)}</b> основних модулів.\n"
            "🔐 Кнопки доступні лише власнику Hikka.\n\n"
            "<i>Оберіть розділ. Меню автоматично бере актуальні "
            "команди зі всіх завантажених модулів.</i>"
        )

    def _home_markup(self, reply_id=None):
        rows = self._chunks(
            [
                {
                    "text": title,
                    "callback": self._section_page,
                    "args": (index, reply_id),
                }
                for index, (title, _) in enumerate(self.SECTIONS)
            ]
        )
        rows.extend(
            [
                [
                    {
                        "text": "🔎 Знайти команду",
                        "input": "Назва команди або ключове слово",
                        "handler": self._search_input,
                        "args": (reply_id,),
                    }
                ],
                [
                    {
                        "text": "🔄 Оновити",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                    {
                        "text": "🌘 Support chat",
                        "url": "https://t.me/RotKranzUK",
                    },
                ],
                [{"text": "✖️ Закрити", "action": "close"}],
            ]
        )
        return rows

    def _search_results(self, query):
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        results = []
        for key, spec in self.MODULES.items():
            if self._find_module(key) is None:
                continue
            for command, doc in self._module_commands(key):
                haystack = " ".join(
                    (command, spec["name"], spec["description"], str(doc))
                ).casefold()
                if needle not in haystack:
                    continue
                priority = 0 if command.casefold().startswith(needle) else 1
                results.append((priority, key, command, doc))
        results.sort(
            key=lambda item: (
                item[0],
                self.MODULES[item[1]]["name"].casefold(),
                item[2].casefold(),
            )
        )
        return results

    async def _search_input(self, call, query, reply_id=None):
        await self._search_page(call, str(query or "")[:80], 0, reply_id)

    async def _search_page(self, call, query, page=0, reply_id=None):
        results = self._search_results(query)
        page_count = max(1, (len(results) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(int(page), page_count - 1))
        shown = results[page * self.PAGE_SIZE : (page + 1) * self.PAGE_SIZE]
        text = (
            "🔎 <b>Пошук команд</b>\n"
            f"Запит: <code>{utils.escape_html(query)}</code>\n"
            f"Знайдено: <b>{len(results)}</b>"
        )
        if shown:
            for _, key, command, doc in shown:
                spec = self.MODULES[key]
                warning = " ⚠️" if command in self.MUTATING else ""
                text += (
                    f"\n\n{spec['icon']} <b>{spec['name']}</b> · "
                    f"<code>.{utils.escape_html(command)}</code>{warning}\n"
                    f"└ {utils.escape_html(self._short_doc(doc))}"
                )
        else:
            text += "\n\n<i>Нічого не знайдено. Спробуйте коротший запит.</i>"

        markup = self._chunks(
            [
                {
                    "text": ("⚠️ " if command in self.MUTATING else "")
                    + f"{self.MODULES[key]['icon']} .{command}",
                    "callback": self._command_page,
                    "args": (key, command, 0, reply_id),
                }
                for _, key, command, _ in shown
            ]
        )
        if page_count > 1:
            markup.append(
                [
                    {
                        "text": "◀️",
                        "callback": self._search_page,
                        "args": (query, (page - 1) % page_count, reply_id),
                    },
                    {
                        "text": f"{page + 1}/{page_count}",
                        "action": "answer",
                        "message": f"Сторінка {page + 1} з {page_count}",
                    },
                    {
                        "text": "▶️",
                        "callback": self._search_page,
                        "args": (query, (page + 1) % page_count, reply_id),
                    },
                ]
            )
        markup.extend(
            [
                [
                    {
                        "text": "🔎 Новий пошук",
                        "input": "Назва команди або ключове слово",
                        "handler": self._search_input,
                        "args": (reply_id,),
                    }
                ],
                [
                    {
                        "text": "🏠 Головна",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                    {"text": "✖️", "action": "close"},
                ],
            ]
        )
        await call.edit(text, reply_markup=markup)

    async def _home(self, call, reply_id=None):
        await call.edit(
            self._home_text(),
            reply_markup=self._home_markup(reply_id),
        )

    async def _section_page(self, call, section_index, reply_id=None):
        section_index = max(0, min(int(section_index), len(self.SECTIONS) - 1))
        title, keys = self.SECTIONS[section_index]
        module_buttons = []
        lines = []
        for key in keys:
            spec = self.MODULES[key]
            loaded = self._find_module(key) is not None
            state = "✅" if loaded else "⚪️"
            lines.append(f"{state} {spec['icon']} <b>{spec['name']}</b> — {spec['description']}")
            module_buttons.append(
                {
                    "text": f"{state} {spec['icon']} {spec['name']}",
                    "callback": self._module_page,
                    "args": (key, 0, reply_id),
                }
            )
        markup = self._chunks(module_buttons)
        markup.append(
            [
                {
                    "text": "↩️ Головне меню",
                    "callback": self._home,
                    "args": (reply_id,),
                },
                {"text": "✖️", "action": "close"},
            ]
        )
        await call.edit(
            f"{title}\n\n" + "\n".join(lines),
            reply_markup=markup,
        )

    @staticmethod
    def _short_doc(doc, limit=88):
        first = " ".join(str(doc).splitlines()[0].split())
        return first if len(first) <= limit else first[: limit - 1] + "…"

    async def _module_page(
        self,
        call,
        key,
        page=0,
        reply_id=None,
        note=None,
    ):
        spec = self.MODULES.get(key)
        if spec is None:
            await call.answer("Невідомий модул", show_alert=True)
            return
        module = self._find_module(key)
        if module is None:
            await call.answer(
                f"Модуль {spec['name']} не завантажено",
                show_alert=True,
            )
            return
        commands = self._module_commands(key)
        page_count = max(1, (len(commands) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(int(page), page_count - 1))
        shown = commands[page * self.PAGE_SIZE : (page + 1) * self.PAGE_SIZE]
        lines = [
            f"{spec['icon']} <b>{spec['name']}</b>",
            spec["description"],
            f"\n🧩 Команд: <b>{len(commands)}</b> · сторінка "
            f"<b>{page + 1}/{page_count}</b>",
        ]
        if note:
            lines.append(f"\n✅ <i>{utils.escape_html(note)}</i>")
        for command, doc in shown:
            warning = " ⚠️" if command in self.MUTATING else ""
            lines.append(
                f"\n<code>.{utils.escape_html(command)}</code>{warning}\n"
                f"└ {utils.escape_html(self._short_doc(doc))}"
            )
        buttons = [
            {
                "text": ("⚠️ " if command in self.MUTATING else "")
                + f".{command}",
                "callback": self._command_page,
                "args": (key, command, page, reply_id),
            }
            for command, _ in shown
        ]
        markup = self._chunks(buttons)
        if page_count > 1:
            markup.append(
                [
                    {
                        "text": "◀️",
                        "callback": self._module_page,
                        "args": (key, (page - 1) % page_count, reply_id),
                    },
                    {
                        "text": f"{page + 1}/{page_count}",
                        "action": "answer",
                        "message": f"Сторінка {page + 1} з {page_count}",
                    },
                    {
                        "text": "▶️",
                        "callback": self._module_page,
                        "args": (key, (page + 1) % page_count, reply_id),
                    },
                ]
            )
        section_index = next(
            index
            for index, (_, keys) in enumerate(self.SECTIONS)
            if key in keys
        )
        markup.append(
            [
                {
                    "text": "↩️ До розділу",
                    "callback": self._section_page,
                    "args": (section_index, reply_id),
                },
                {
                    "text": "🏠 Головна",
                    "callback": self._home,
                    "args": (reply_id,),
                },
                {"text": "✖️", "action": "close"},
            ]
        )
        await call.edit("\n".join(lines), reply_markup=markup)

    def _command_doc(self, key, command):
        return next(
            (doc for current, doc in self._module_commands(key) if current == command),
            "Опис команди відсутній.",
        )

    async def _command_page(self, call, key, command, page=0, reply_id=None):
        spec = self.MODULES[key]
        doc = self._command_doc(key, command)
        dangerous = self._is_dangerous(command)
        potentially_dangerous = command in self.MUTATING
        warning = (
            "\n\n⚠️ <b>З аргументами команда може змінити дані "
            "або зовнішній стан.</b>"
            if potentially_dangerous
            else ""
        )
        reply_note = (
            "\n💬 Команду буде виконано у відповідь на обране повідомлення."
            if reply_id
            else ""
        )
        text = (
            f"{spec['icon']} <b>{spec['name']} · "
            f".{utils.escape_html(command)}</b>\n\n"
            f"{utils.escape_html(doc)[:2600]}{warning}{reply_note}"
        )
        markup = []
        action_row = []
        if command in self.SAFE_EMPTY or command == "аналіз":
            action_row.append(
                {
                    "text": "▶️ Виконати",
                    "callback": (
                        self._confirm_command
                        if dangerous
                        else self._execute_command
                    ),
                    "args": (key, command, "", page, reply_id),
                }
            )
        action_row.append(
            {
                "text": "⌨️ Ввести аргументи",
                "input": f"Аргументи для .{command}",
                "handler": self._input_command,
                "args": (key, command, page, reply_id),
            }
        )
        markup.append(action_row)
        markup.append(
            [
                {
                    "text": "↩️ До команд",
                    "callback": self._module_page,
                    "args": (key, page, reply_id),
                },
                {"text": "✖️", "action": "close"},
            ]
        )
        await call.edit(text, reply_markup=markup)

    async def _input_command(
        self,
        call,
        query,
        key,
        command,
        page=0,
        reply_id=None,
    ):
        query = str(query or "").strip()[:3000]
        if self._is_dangerous(command, query):
            await self._confirm_command(
                call,
                key,
                command,
                query,
                page,
                reply_id,
            )
            return
        with contextlib.suppress(Exception):
            await call.answer(f"Запускаю .{command}…")
        await self._execute_command(
            call,
            key,
            command,
            query,
            page,
            reply_id,
            answer=False,
        )

    async def _confirm_command(
        self,
        call,
        key,
        command,
        args="",
        page=0,
        reply_id=None,
    ):
        shown_args = "••••••" if command in self.SECRET_INPUT else args or "—"
        text = (
            "⚠️ <b>Підтвердіть дію</b>\n\n"
            f"Команда: <code>.{utils.escape_html(command)}</code>\n"
            f"Аргументи: <code>{utils.escape_html(shown_args)}</code>\n\n"
            "<i>Дія може змінити дані, опублікувати повідомлення "
            "або вплинути на систему.</i>"
        )
        await call.edit(
            text,
            reply_markup=[
                [
                    {
                        "text": "✅ Так, виконати",
                        "callback": self._execute_command,
                        "args": (key, command, args, page, reply_id),
                    },
                    {
                        "text": "❌ Скасувати",
                        "callback": self._command_page,
                        "args": (key, command, page, reply_id),
                    },
                ]
            ],
        )

    @staticmethod
    def _chat_id(call):
        form = getattr(call, "form", {}) or {}
        if isinstance(form, dict):
            return form.get("chat")
        return None

    async def _execute_analysis(self, chat_id, reply_id=None):
        module = self._find_module("analysis")
        if module is None:
            raise RuntimeError("ChatAnalysis не завантажено")
        started = module.strings["started"]
        status = await self._client.send_message(
            chat_id,
            started,
            parse_mode="html",
            reply_to=reply_id,
        )
        text = await module._build_analysis(status, status)
        await utils.answer(status, text)

    async def _execute_command(
        self,
        call,
        key,
        command,
        args="",
        page=0,
        reply_id=None,
        answer=True,
    ):
        chat_id = self._chat_id(call)
        if chat_id is None:
            if answer:
                await call.answer("Не вдалося визначити чат", show_alert=True)
            return
        if answer:
            await call.answer(f"Запускаю .{command}…")
        try:
            if command == "аналіз":
                await self._execute_analysis(chat_id, reply_id)
            else:
                module = self._find_module(key)
                if module is None:
                    raise RuntimeError(
                        f"Модуль {self.MODULES[key]['name']} не завантажено"
                    )
                handler = getattr(module, "commands", {}).get(command)
                if handler is None:
                    raise RuntimeError(f"Команду .{command} не знайдено")
                command_text = f"{self.get_prefix()}{command} {args}".strip()
                send_kwargs = {"reply_to": reply_id} if reply_id else {}
                message = await self._client.send_message(
                    chat_id, command_text, **send_kwargs
                )
                await handler(message)
        except Exception as error:
            logger.exception("ModuleHub: command %s failed", command)
            error_text = utils.escape_html(
                str(error) or type(error).__name__
            )[:500]
            with contextlib.suppress(Exception):
                await call.edit(
                    f"❌ <b>Не вдалося виконати "
                    f"<code>.{utils.escape_html(command)}</code></b>\n\n"
                    f"<code>{error_text}</code>",
                    reply_markup=[
                        [
                            {
                                "text": "↩️ Назад",
                                "callback": self._module_page,
                                "args": (key, page, reply_id),
                            }
                        ]
                    ],
                )
            return
        with contextlib.suppress(Exception):
            await self._module_page(
                call,
                key,
                page,
                reply_id,
                f".{command} виконано в цьому чаті",
            )

    async def _open_menu(self, message):
        reply_id = getattr(message, "reply_to_msg_id", None)
        try:
            result = await self.inline.form(
                self._home_text(),
                message,
                reply_markup=self._home_markup(reply_id),
                force_me=True,
                disable_security=False,
            )
            if result:
                return
        except Exception:
            logger.exception("ModuleHub: inline form failed")
        await utils.answer(message, self.strings["inline_failed"])

    @loader.command(ru_doc="Відкрити меню основних модулів")
    async def modmenu(self, message):
        """🧭 Єдине inline-меню всіх основних модулів"""
        await self._open_menu(message)

    @loader.command(ru_doc="Коротка команда меню модулів")
    async def hub(self, message):
        """🧭 Аліас для .modmenu"""
        await self._open_menu(message)
