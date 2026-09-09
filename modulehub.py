# meta developer: @Huai_Baike
# meta version: 2.0.0
# meta description: 🧭 Центр команд, конфігурації та встановлення модулів Hikka.
# scope: inline
# scope: hikka_only

import contextlib
import inspect
import logging
import re
from urllib.parse import urlparse

from .. import loader, utils


logger = logging.getLogger(__name__)


@loader.tds
class ModuleHubMod(loader.Module):
    """🧭 Центр команд, налаштувань і встановлення модулів"""

    strings = {
        "name": "ModuleHub",
        "inline_failed": (
            "❌ <b>Не вдалося відкрити inline-меню.</b>\n"
            "<i>Перевірте налаштування inline-бота Hikka.</i>"
        ),
    }

    PAGE_SIZE = 6
    CONFIG_PAGE_SIZE = 8
    REPO_BASE = "https://github.com/Splaueef/host/raw/main"
    TRUSTED_INSTALL_HOSTS = {
        "github.com",
        "raw.githubusercontent.com",
        "gitlab.com",
    }

    REPO_FILES = {
        "contactstatus": "contactstatus.py",
        "stats": "stats.py",
        "analysis": "analysis.py",
        "nekospy": "nekospy.py",
        "vdlt": "vdlt.py",
        "dailynews": "dailynews.py",
        "mistral": "mistralAI.py",
        "gemma": "gemmaself.py",
        "math": "math.py",
        "werwolf": "rkapi.py",
        "systemd": "systemd.py",
        "backup": "backup.py",
        "quiet": "quietschedule.py",
        "sysinfo": "sysinfo.py",
        "timeinfo": "timeinfo.py",
        "teledocs": "teledocs.py",
        "purge": "purge.py",
        "eval": "eval.py",
    }

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
        return self._find_module_by_class(spec["class"])

    def _find_module_by_class(self, class_name):
        return next(
            (
                module
                for module in getattr(self.allmodules, "modules", [])
                if module.__class__.__name__ == class_name
            ),
            None,
        )

    def _module_key(self, module):
        class_name = module.__class__.__name__
        return next(
            (
                key
                for key, spec in self.MODULES.items()
                if spec["class"] == class_name
            ),
            None,
        )

    def _module_name(self, module):
        key = self._module_key(module)
        if key:
            return self.MODULES[key]["name"]
        strings = getattr(module, "strings", None)
        try:
            value = strings("name") if callable(strings) else strings["name"]
            if value:
                return str(value)
        except Exception:
            pass
        name = module.__class__.__name__
        return name[:-3] if name.endswith("Mod") else name

    def _module_icon(self, module):
        key = self._module_key(module)
        return self.MODULES[key]["icon"] if key else "🧩"

    @staticmethod
    def _is_core_module(module):
        return str(getattr(module, "__origin__", "")).startswith("<core")

    @staticmethod
    def _has_config(module):
        config = getattr(module, "config", None)
        try:
            return config is not None and len(config) > 0
        except TypeError:
            return False

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
            "📦 Каталог дозволяє встановлювати й оновлювати модулі.\n"
            "⚙️ Налаштування працюють і для інших модулів Hikka.\n"
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
                        "text": "📦 Каталог модулів",
                        "callback": self._catalog_page,
                        "args": (0, reply_id),
                    },
                    {
                        "text": "⚙️ Усі налаштування",
                        "callback": self._configs_page,
                        "args": (0, "external", reply_id),
                    },
                ],
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
                        "text": "➕ Встановити за назвою / URL",
                        "input": "Назва з репозиторію або HTTPS-посилання на .py",
                        "handler": self._custom_install_input,
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

    def _catalog_source(self, key):
        filename = self.REPO_FILES[key]
        return f"{self.REPO_BASE}/{filename}"

    async def _catalog_page(self, call, page=0, reply_id=None, note=None):
        keys = list(self.MODULES)
        page_count = max(1, (len(keys) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(int(page), page_count - 1))
        shown = keys[page * self.PAGE_SIZE : (page + 1) * self.PAGE_SIZE]
        lines = [
            "📦 <b>Каталог модулів</b>",
            f"Сторінка <b>{page + 1}/{page_count}</b> · "
            f"встановлено <b>{self._loaded_count()}/{len(keys)}</b>",
        ]
        if note:
            lines.append(f"\n✅ <i>{utils.escape_html(note)}</i>")
        for key in shown:
            spec = self.MODULES[key]
            loaded = self._find_module(key) is not None
            lines.append(
                f"\n{'✅' if loaded else '⬇️'} {spec['icon']} "
                f"<b>{spec['name']}</b>\n"
                f"└ {'завантажено · можна оновити' if loaded else 'не встановлено'}"
            )
        markup = self._chunks(
            [
                {
                    "text": (
                        "✅" if self._find_module(key) is not None else "⬇️"
                    )
                    + f" {self.MODULES[key]['icon']} {self.MODULES[key]['name']}",
                    "callback": self._module_page,
                    "args": (key, 0, reply_id),
                }
                for key in shown
            ]
        )
        if page_count > 1:
            markup.append(
                [
                    {
                        "text": "◀️",
                        "callback": self._catalog_page,
                        "args": ((page - 1) % page_count, reply_id),
                    },
                    {
                        "text": f"{page + 1}/{page_count}",
                        "action": "answer",
                        "message": f"Сторінка {page + 1} з {page_count}",
                    },
                    {
                        "text": "▶️",
                        "callback": self._catalog_page,
                        "args": ((page + 1) % page_count, reply_id),
                    },
                ]
            )
        markup.extend(
            [
                [
                    {
                        "text": "➕ Встановити за назвою / URL",
                        "input": "Назва з репозиторію або HTTPS-посилання на .py",
                        "handler": self._custom_install_input,
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
        await call.edit("\n".join(lines), reply_markup=markup)

    async def _missing_module_page(self, call, key, reply_id=None, note=None):
        spec = self.MODULES[key]
        text = (
            f"⬇️ {spec['icon']} <b>{spec['name']}</b>\n\n"
            f"{spec['description']}\n\n"
            "Модуль зараз <b>не встановлено</b>. Його можна безпосередньо "
            "завантажити з перевіреного репозиторію <code>Splaueef/host</code>."
        )
        if note:
            text += f"\n\n⚠️ <i>{utils.escape_html(note)}</i>"
        await call.edit(
            text,
            reply_markup=[
                [
                    {
                        "text": "⬇️ Встановити",
                        "callback": self._confirm_catalog_action,
                        "args": (key, "install", reply_id),
                    }
                ],
                [
                    {
                        "text": "↩️ До каталогу",
                        "callback": self._catalog_page,
                        "args": (0, reply_id),
                    },
                    {
                        "text": "🏠 Головна",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                ],
            ],
        )

    async def _confirm_catalog_action(self, call, key, action, reply_id=None):
        if key not in self.MODULES or key not in self.REPO_FILES:
            await call.answer("Невідомий модуль", show_alert=True)
            return
        spec = self.MODULES[key]
        verb = "оновити" if action == "update" else "встановити"
        source = self._catalog_source(key)
        await call.edit(
            "⚠️ <b>Підтвердження завантаження коду</b>\n\n"
            f"Ви справді хочете <b>{verb}</b> {spec['icon']} "
            f"<b>{spec['name']}</b>?\n\n"
            f"Джерело: <code>{utils.escape_html(source)}</code>\n"
            "<i>Модулі мають доступ до акаунта Hikka. Встановлюйте код "
            "лише з джерел, яким довіряєте.</i>",
            reply_markup=[
                [
                    {
                        "text": f"✅ Так, {verb}",
                        "callback": self._run_catalog_action,
                        "args": (key, action, reply_id),
                    },
                    {
                        "text": "❌ Скасувати",
                        "callback": self._module_page,
                        "args": (key, 0, reply_id),
                    },
                ]
            ],
        )

    async def _run_catalog_action(self, call, key, action, reply_id=None):
        chat_id = self._chat_id(call)
        if chat_id is None:
            await call.answer("Не вдалося визначити чат", show_alert=True)
            return
        spec = self.MODULES[key]
        await call.answer(
            f"{'Оновлюю' if action == 'update' else 'Встановлюю'} "
            f"{spec['name']}…"
        )
        try:
            await self.invoke("dlmod", self._catalog_source(key), peer=chat_id)
        except Exception as error:
            logger.exception("ModuleHub: catalog action failed for %s", key)
            await self._missing_module_page(
                call,
                key,
                reply_id,
                f"Loader повернув помилку: {str(error)[:300]}",
            )
            return
        if self._find_module(key) is None:
            await self._missing_module_page(
                call,
                key,
                reply_id,
                "Loader не підтвердив завантаження. Деталі є в повідомленні команди.",
            )
            return
        await self._module_page(
            call,
            key,
            0,
            reply_id,
            "Модуль оновлено" if action == "update" else "Модуль встановлено",
        )

    async def _confirm_uninstall(self, call, key, page=0, reply_id=None):
        spec = self.MODULES[key]
        await call.edit(
            "🗑 <b>Видалити модуль?</b>\n\n"
            f"{spec['icon']} <b>{spec['name']}</b> буде вивантажено й "
            "прибрано зі списку автозапуску. Збережені дані модуля "
            "залишаться в базі Hikka.",
            reply_markup=[
                [
                    {
                        "text": "🗑 Так, видалити",
                        "callback": self._run_uninstall,
                        "args": (key, reply_id),
                    },
                    {
                        "text": "❌ Скасувати",
                        "callback": self._module_page,
                        "args": (key, page, reply_id),
                    },
                ]
            ],
        )

    async def _run_uninstall(self, call, key, reply_id=None):
        chat_id = self._chat_id(call)
        module = self._find_module(key)
        if chat_id is None or module is None:
            await call.answer("Модуль уже не завантажено", show_alert=True)
            return
        spec = self.MODULES[key]
        await call.answer(f"Видаляю {spec['name']}…")
        try:
            await self.invoke("unloadmod", spec["class"], peer=chat_id)
        except Exception as error:
            logger.exception("ModuleHub: unable to unload %s", key)
            await call.answer(str(error)[:200], show_alert=True)
            return
        if self._find_module(key) is not None:
            await self._module_page(
                call,
                key,
                0,
                reply_id,
                "Loader не зміг видалити модуль",
            )
            return
        await self._missing_module_page(call, key, reply_id, "Модуль видалено")

    @classmethod
    def _normalize_install_source(cls, value):
        source = str(value or "").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}(?:\.py)?", source):
            return source, None
        parsed = urlparse(source)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or host not in cls.TRUSTED_INSTALL_HOSTS
            or not parsed.path.lower().endswith(".py")
        ):
            return None, (
                "Вкажіть коротку назву модуля або HTTPS-посилання на .py "
                "з GitHub чи GitLab."
            )
        return source, None

    async def _custom_install_input(self, call, query, reply_id=None):
        source, error = self._normalize_install_source(query)
        if error:
            await call.edit(
                f"❌ <b>Некоректне джерело</b>\n\n{utils.escape_html(error)}",
                reply_markup=[
                    [
                        {
                            "text": "🔁 Спробувати ще",
                            "input": "Назва або HTTPS-посилання на .py",
                            "handler": self._custom_install_input,
                            "args": (reply_id,),
                        }
                    ],
                    [
                        {
                            "text": "🏠 Головна",
                            "callback": self._home,
                            "args": (reply_id,),
                        }
                    ],
                ],
            )
            return
        await call.edit(
            "⚠️ <b>Встановлення стороннього модуля</b>\n\n"
            f"Джерело: <code>{utils.escape_html(source)}</code>\n\n"
            "<i>Модуль отримає доступ до Hikka та Telegram-акаунта. "
            "Переконайтеся, що довіряєте автору коду.</i>",
            reply_markup=[
                [
                    {
                        "text": "✅ Встановити",
                        "callback": self._run_custom_install,
                        "args": (source, reply_id),
                    },
                    {
                        "text": "❌ Скасувати",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                ]
            ],
        )

    async def _run_custom_install(self, call, source, reply_id=None):
        chat_id = self._chat_id(call)
        if chat_id is None:
            await call.answer("Не вдалося визначити чат", show_alert=True)
            return
        await call.answer("Встановлюю модуль…")
        try:
            await self.invoke("dlmod", source, peer=chat_id)
        except Exception as error:
            logger.exception("ModuleHub: custom install failed")
            await call.edit(
                "❌ <b>Не вдалося запустити Loader</b>\n\n"
                f"<code>{utils.escape_html(str(error))[:500]}</code>",
                reply_markup=[
                    [
                        {
                            "text": "🏠 Головна",
                            "callback": self._home,
                            "args": (reply_id,),
                        }
                    ]
                ],
            )
            return
        await call.edit(
            "✅ <b>Loader завершив встановлення</b>\n\n"
            "Перевірте службове повідомлення в чаті: там буде точний "
            "результат завантаження та можливі попередження залежностей.",
            reply_markup=[
                [
                    {
                        "text": "📦 Каталог",
                        "callback": self._catalog_page,
                        "args": (0, reply_id),
                    },
                    {
                        "text": "🏠 Головна",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                ]
            ],
        )

    def _config_modules(self, scope="external"):
        result = []
        for module in getattr(self.allmodules, "modules", []):
            config = getattr(module, "config", None)
            try:
                has_options = config is not None and bool(list(config))
            except Exception:
                has_options = False
            if not has_options:
                continue
            is_core = self._is_core_module(module)
            if scope == "core" and not is_core:
                continue
            if scope != "core" and is_core:
                continue
            result.append(module)
        result.sort(key=lambda module: self._module_name(module).casefold())
        return result

    @staticmethod
    def _config_validator(module, option):
        try:
            return module.config._config[option].validator
        except Exception:
            return None

    def _config_is_hidden(self, module, option):
        validator = self._config_validator(module, option)
        return getattr(validator, "internal_id", None) == "Hidden"

    def _config_is_bool(self, module, option):
        if self._config_is_hidden(module, option):
            return False
        validator = self._config_validator(module, option)
        return (
            getattr(validator, "internal_id", None) == "Boolean"
            or isinstance(module.config[option], bool)
        )

    def _format_config_value(self, module, option, value, limit=72):
        if self._config_is_hidden(module, option):
            return "••••••" if value not in (None, "", []) else "не задано"
        if value is None:
            text = "None"
        elif isinstance(value, bool):
            text = "увімкнено" if value else "вимкнено"
        elif isinstance(value, (list, tuple, set)):
            text = "[" + ", ".join(map(str, value)) + "]"
        else:
            text = str(value)
        text = " ".join(text.splitlines())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    async def _configs_page(
        self,
        call,
        page=0,
        scope="external",
        reply_id=None,
    ):
        scope = "core" if scope == "core" else "external"
        modules = self._config_modules(scope)
        page_count = max(
            1,
            (len(modules) + self.CONFIG_PAGE_SIZE - 1) // self.CONFIG_PAGE_SIZE,
        )
        page = max(0, min(int(page), page_count - 1))
        shown = modules[
            page * self.CONFIG_PAGE_SIZE : (page + 1) * self.CONFIG_PAGE_SIZE
        ]
        label = "системні" if scope == "core" else "встановлені"
        text = (
            "⚙️ <b>Налаштування модулів</b>\n"
            f"Розділ: <b>{label}</b> · знайдено <b>{len(modules)}</b> · "
            f"сторінка <b>{page + 1}/{page_count}</b>\n\n"
            "<i>Секретні поля приховані. Нові значення перевіряються "
            "валідаторами самого модуля.</i>"
        )
        if not shown:
            text += "\n\n<i>У цьому розділі немає доступних конфігурацій.</i>"
        markup = self._chunks(
            [
                {
                    "text": (
                        f"{self._module_icon(module)} "
                        f"{self._module_name(module)[:26]} "
                        f"({len(list(module.config))})"
                    ),
                    "callback": self._config_page,
                    "args": (module.__class__.__name__, 0, scope, reply_id),
                }
                for module in shown
            ]
        )
        if page_count > 1:
            markup.append(
                [
                    {
                        "text": "◀️",
                        "callback": self._configs_page,
                        "args": ((page - 1) % page_count, scope, reply_id),
                    },
                    {
                        "text": f"{page + 1}/{page_count}",
                        "action": "answer",
                        "message": f"Сторінка {page + 1} з {page_count}",
                    },
                    {
                        "text": "▶️",
                        "callback": self._configs_page,
                        "args": ((page + 1) % page_count, scope, reply_id),
                    },
                ]
            )
        markup.extend(
            [
                [
                    {
                        "text": "🔌 Встановлені",
                        "callback": self._configs_page,
                        "args": (0, "external", reply_id),
                    },
                    {
                        "text": "🧠 Системні",
                        "callback": self._configs_page,
                        "args": (0, "core", reply_id),
                    },
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

    async def _config_page(
        self,
        call,
        class_name,
        page=0,
        scope="external",
        reply_id=None,
        note=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None or not hasattr(module, "config"):
            await call.answer("Модуль або його конфігурація недоступні", show_alert=True)
            return
        options = list(module.config)
        page_count = max(
            1,
            (len(options) + self.PAGE_SIZE - 1) // self.PAGE_SIZE,
        )
        page = max(0, min(int(page), page_count - 1))
        shown = options[page * self.PAGE_SIZE : (page + 1) * self.PAGE_SIZE]
        lines = [
            f"⚙️ <b>{utils.escape_html(self._module_name(module))}</b>",
            f"Параметрів: <b>{len(options)}</b> · "
            f"сторінка <b>{page + 1}/{page_count}</b>",
        ]
        if note:
            lines.append(f"\n✅ <i>{utils.escape_html(note)}</i>")
        for option in shown:
            value = module.config[option]
            lock = "🔒 " if self._config_is_hidden(module, option) else ""
            lines.append(
                f"\n{lock}<code>{utils.escape_html(option)}</code>\n"
                f"└ {utils.escape_html(self._format_config_value(module, option, value))}"
            )
        buttons = [
            {
                "text": (
                    ("🔒 " if self._config_is_hidden(module, option) else "")
                    + option[:28]
                ),
                "callback": self._config_option_page,
                "args": (class_name, option, page, scope, reply_id),
            }
            for option in shown
        ]
        markup = self._chunks(buttons)
        if page_count > 1:
            markup.append(
                [
                    {
                        "text": "◀️",
                        "callback": self._config_page,
                        "args": (
                            class_name,
                            (page - 1) % page_count,
                            scope,
                            reply_id,
                        ),
                    },
                    {
                        "text": f"{page + 1}/{page_count}",
                        "action": "answer",
                        "message": f"Сторінка {page + 1} з {page_count}",
                    },
                    {
                        "text": "▶️",
                        "callback": self._config_page,
                        "args": (
                            class_name,
                            (page + 1) % page_count,
                            scope,
                            reply_id,
                        ),
                    },
                ]
            )
        key = self._module_key(module)
        back_callback = self._module_page if key else self._configs_page
        back_args = (key, 0, reply_id) if key else (0, scope, reply_id)
        markup.append(
            [
                {
                    "text": "↩️ Назад",
                    "callback": back_callback,
                    "args": back_args,
                },
                {
                    "text": "⚙️ Усі конфіги",
                    "callback": self._configs_page,
                    "args": (0, scope, reply_id),
                },
            ]
        )
        await call.edit("\n".join(lines), reply_markup=markup)

    async def _config_option_page(
        self,
        call,
        class_name,
        option,
        page=0,
        scope="external",
        reply_id=None,
        note=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None or option not in getattr(module, "config", {}):
            await call.answer("Параметр більше недоступний", show_alert=True)
            return
        config = module.config
        try:
            doc = config.getdoc(option)
        except Exception:
            doc = "Опис відсутній"
        try:
            default = config.getdef(option)
        except Exception:
            default = None
        current = config[option]
        validator = self._config_validator(module, option)
        validator_name = getattr(validator, "internal_id", None) or type(current).__name__
        text = (
            f"⚙️ <b>{utils.escape_html(self._module_name(module))}</b>\n"
            f"Параметр: <code>{utils.escape_html(option)}</code>\n\n"
            f"{utils.escape_html(str(doc))[:1800]}\n\n"
            f"Поточне: <code>{utils.escape_html(self._format_config_value(module, option, current, 700))}</code>\n"
            f"Тип: <code>{utils.escape_html(validator_name)}</code>\n"
            f"Типове: <code>{utils.escape_html(self._format_config_value(module, option, default, 500))}</code>"
        )
        if self._config_is_hidden(module, option):
            text += "\n\n🔒 <i>Значення приховано й не може бути показане через меню.</i>"
        if note:
            text += f"\n\n✅ <i>{utils.escape_html(note)}</i>"
        markup = []
        if self._config_is_bool(module, option):
            markup.append(
                [
                    {
                        "text": "🔴 Вимкнути" if current else "🟢 Увімкнути",
                        "callback": self._set_config_bool,
                        "args": (
                            class_name,
                            option,
                            not current,
                            page,
                            scope,
                            reply_id,
                        ),
                    }
                ]
            )
        else:
            markup.append(
                [
                    {
                        "text": "⌨️ Змінити значення",
                        "input": f"Нове значення для {option}",
                        "handler": self._set_config_value,
                        "args": (class_name, option, page, scope, reply_id),
                    }
                ]
            )
        markup.extend(
            [
                [
                    {
                        "text": "♻️ Скинути до типового",
                        "callback": self._confirm_config_reset,
                        "args": (class_name, option, page, scope, reply_id),
                    }
                ],
                [
                    {
                        "text": "↩️ До параметрів",
                        "callback": self._config_page,
                        "args": (class_name, page, scope, reply_id),
                    },
                    {
                        "text": "🏠 Головна",
                        "callback": self._home,
                        "args": (reply_id,),
                    },
                ],
            ]
        )
        await call.edit(text, reply_markup=markup)

    async def _set_config_value(
        self,
        call,
        query,
        class_name,
        option,
        page=0,
        scope="external",
        reply_id=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None or option not in getattr(module, "config", {}):
            await call.answer("Параметр більше недоступний", show_alert=True)
            return
        try:
            module.config[option] = str(query or "").strip()[:4000]
        except Exception as error:
            await call.edit(
                "❌ <b>Значення не пройшло перевірку</b>\n\n"
                f"<code>{utils.escape_html(str(error))[:700]}</code>",
                reply_markup=[
                    [
                        {
                            "text": "🔁 Ввести ще раз",
                            "input": f"Нове значення для {option}",
                            "handler": self._set_config_value,
                            "args": (class_name, option, page, scope, reply_id),
                        }
                    ],
                    [
                        {
                            "text": "↩️ Назад",
                            "callback": self._config_option_page,
                            "args": (class_name, option, page, scope, reply_id),
                        }
                    ],
                ],
            )
            return
        await self._config_option_page(
            call,
            class_name,
            option,
            page,
            scope,
            reply_id,
            "Значення збережено",
        )

    async def _set_config_bool(
        self,
        call,
        class_name,
        option,
        value,
        page=0,
        scope="external",
        reply_id=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None or option not in getattr(module, "config", {}):
            await call.answer("Параметр більше недоступний", show_alert=True)
            return
        try:
            module.config[option] = bool(value)
        except Exception as error:
            await call.answer(str(error)[:200], show_alert=True)
            return
        await self._config_option_page(
            call,
            class_name,
            option,
            page,
            scope,
            reply_id,
            "Налаштування змінено",
        )

    async def _confirm_config_reset(
        self,
        call,
        class_name,
        option,
        page=0,
        scope="external",
        reply_id=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None:
            await call.answer("Модуль більше недоступний", show_alert=True)
            return
        await call.edit(
            "♻️ <b>Скинути параметр?</b>\n\n"
            f"Модуль: <b>{utils.escape_html(self._module_name(module))}</b>\n"
            f"Параметр: <code>{utils.escape_html(option)}</code>\n\n"
            "Поточне значення буде замінено типовим.",
            reply_markup=[
                [
                    {
                        "text": "✅ Скинути",
                        "callback": self._reset_config,
                        "args": (class_name, option, page, scope, reply_id),
                    },
                    {
                        "text": "❌ Скасувати",
                        "callback": self._config_option_page,
                        "args": (class_name, option, page, scope, reply_id),
                    },
                ]
            ],
        )

    async def _reset_config(
        self,
        call,
        class_name,
        option,
        page=0,
        scope="external",
        reply_id=None,
    ):
        module = self._find_module_by_class(class_name)
        if module is None or option not in getattr(module, "config", {}):
            await call.answer("Параметр більше недоступний", show_alert=True)
            return
        try:
            module.config[option] = module.config.getdef(option)
        except Exception as error:
            await call.answer(str(error)[:200], show_alert=True)
            return
        await self._config_option_page(
            call,
            class_name,
            option,
            page,
            scope,
            reply_id,
            "Відновлено типове значення",
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
            await self._missing_module_page(call, key, reply_id, note)
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
        management = []
        try:
            has_config = hasattr(module, "config") and bool(list(module.config))
        except Exception:
            has_config = False
        if has_config:
            management.append(
                {
                    "text": "⚙️ Налаштувати",
                    "callback": self._config_page,
                    "args": (spec["class"], 0, "external", reply_id),
                }
            )
        management.append(
            {
                "text": "🔄 Оновити",
                "callback": self._confirm_catalog_action,
                "args": (key, "update", reply_id),
            }
        )
        markup.append(management)
        markup.append(
            [
                {
                    "text": "🗑 Видалити модуль",
                    "callback": self._confirm_uninstall,
                    "args": (key, page, reply_id),
                }
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
