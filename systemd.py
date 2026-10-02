#             █ █ ▀ █▄▀ ▄▀█ █▀█ ▀
# meta version: 1.1.0
#             █▀█ █ █ █ █▀█ █▀▄ █
#              © Copyright 2022
#           https://t.me/hikariatama
#
# 🔒      Licensed under the GNU AGPLv3
# 🌐 https://www.gnu.org/licenses/agpl-3.0.html

# scope: hikka_min 1.2.10

# meta pic: https://img.icons8.com/plasticine/344/apple-settings--v2.png
# meta banner: https://mods.hikariatama.ru/badges/systemd.jpg
# scope: inline
# scope: hikka_only
# meta developer: @rotkranz

# ⚠️ Please, ensure that userbot has enough rights to control units
# Put these lines in /etc/sudoers using visudo command:
#
# user ALL=(ALL) NOPASSWD: /bin/systemctl
# user ALL=(ALL) NOPASSWD: /bin/journalctl
#
# Where `user` is user on behalf of which the userbot is running

import asyncio
import io
import re
from typing import Union

from telethon.tl.types import Message

from .. import loader, utils
from ..inline.types import InlineCall


def human_readable_size(size: float, decimal_places: int = 2) -> str:
    for unit in ["B", "K", "M", "G", "T", "P"]:
        if size < 1024.0 or unit == "P":
            break
        size /= 1024.0

    return f"{size:.{decimal_places}f} {unit}"


@loader.tds
class SystemdMod(loader.Module):
    """Control systemd units easily"""

    strings = {
        "name": "Systemd",
        "panel": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Here you can control"
            " your systemd units</b>\n\n{}"
        ),
        "unit_doesnt_exist": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Unit</b>"
            " <code>{}</code> <b>doesn't exist!</b>"
        ),
        "args": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>No arguments"
            " specified</b>"
        ),
        "unit_added": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Unit"
            " </b><code>{}</code><b> with name </b><code>{}</code><b> added</b>"
        ),
        "unit_removed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Unit"
            " </b><code>{}</code><b> removed</b>"
        ),
        "unit_action_done": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Action"
            " </b><code>{}</code><b> performed on unit </b><code>{}</code>"
        ),
        "unit_control": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Interacting with unit"
            " </b><code>{}</code><b> (</b><code>{}</code><b>)</b>\n{} <b>Unit status:"
            " </b><code>{}</code>"
        ),
        "action_not_found": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Action"
            " </b><code>{}</code><b> not found</b>"
        ),
        "unit_renamed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Unit"
            " </b><code>{}</code><b> renamed to </b><code>{}</code>"
        ),
        "stop_btn": "🍎 Stop",
        "start_btn": "🍏 Start",
        "restart_btn": "🔄 Restart",
        "logs_btn": "📄 Logs",
        "tail_btn": "🚅 Tail",
        "back_btn": "🔙 Back",
        "close_btn": "✖️ Close",
        "refresh_btn": "🔄 Refresh",
        "action_failed": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Action"
            " </b><code>{}</code><b> failed:</b> <code>{}</code>"
        ),
        "bad_unit": "🚫 <b>Invalid unit name:</b> <code>{}</code>",
        "empty_panel": "<i>No units yet. Add one with</i> <code>{}addunit nginx</code>",
    }

    strings_ru = {
        "panel": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Здесь вы можете"
            " управлять своими юнитами systemd</b>\n\n{}"
        ),
        "unit_doesnt_exist": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Юнит</b>"
            " <code>{}</code> <b>не существует!</b>"
        ),
        "args": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Не указаны"
            " аргументы</b>"
        ),
        "unit_added": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Юнит"
            " </b><code>{}</code><b> с именем </b><code>{}</code><b> добавлен</b>"
        ),
        "unit_removed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Юнит"
            " </b><code>{}</code><b> удалён</b>"
        ),
        "unit_action_done": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Действие"
            " </b><code>{}</code><b> выполнено на юните </b><code>{}</code>"
        ),
        "unit_control": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Взаимодействие с"
            " юнитом </b><code>{}</code><b> (</b><code>{}</code><b>)</b>\n{} <b>Статус"
            " юнита: </b><code>{}</code>"
        ),
        "action_not_found": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Действие"
            " </b><code>{}</code><b> не найдено</b>"
        ),
        "unit_renamed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Юнит"
            " </b><code>{}</code><b> переименован в </b><code>{}</code>"
        ),
        "stop_btn": "🍎 Стоп",
        "start_btn": "🍏 Старт",
        "restart_btn": "🔄 Рестарт",
        "logs_btn": "📄 Логи",
        "tail_btn": "🚅 Тейл",
        "back_btn": "🔙 Назад",
        "close_btn": "✖️ Закрыть",
        "refresh_btn": "🔄 Обновить",
        "_cmd_doc_units": "Показать список юнитов",
        "_cmd_doc_addunit": "<unit> - Добавить юнит",
        "_cmd_doc_nameunit": "<unit> - Переименовать юнит",
        "_cmd_doc_delunit": "<unit> - Удалить юнит",
        "_cmd_doc_unit": "<unit> - Управлять юнитом",
        "_cls_doc": "Простое и удобное управление юнитами systemd",
    }

    strings_de = {
        "panel": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Hier kannst du deine"
            " systemd-Einheiten kontrollieren</b>\n\n{}"
        ),
        "unit_doesnt_exist": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Einheit</b>"
            " <code>{}</code> <b>existiert nicht!</b>"
        ),
        "args": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Keine Argumente"
            " angegeben</b>"
        ),
        "unit_added": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Einheit"
            " </b><code>{}</code><b> mit dem Namen </b><code>{}</code><b>"
            " hinzugefügt</b>"
        ),
        "unit_removed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Einheit"
            " </b><code>{}</code><b> entfernt</b>"
        ),
        "unit_action_done": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Aktion"
            " </b><code>{}</code><b> auf Einheit </b><code>{}</code><b> ausgeführt</b>"
        ),
        "unit_control": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Interagiere mit"
            " Einheit </b><code>{}</code><b> (</b><code>{}</code><b>)</b>\n{}"
            " <b>Einheitsstatus: </b><code>{}</code>"
        ),
        "action_not_found": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Aktion"
            " </b><code>{}</code><b> nicht gefunden</b>"
        ),
        "unit_renamed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Einheit"
            " </b><code>{}</code><b> umbenannt zu </b><code>{}</code>"
        ),
        "stop_btn": "🍎 Stop",
        "start_btn": "🍏 Start",
        "restart_btn": "🔄 Neustart",
        "logs_btn": "📄 Logs",
        "tail_btn": "🚅 Tail",
        "back_btn": "🔙 Zurück",
        "close_btn": "✖️ Schließen",
        "refresh_btn": "🔄 Aktualisieren",
        "_cmd_doc_units": "Liste der Einheiten anzeigen",
        "_cmd_doc_addunit": "<unit> - Einheit hinzufügen",
        "_cmd_doc_nameunit": "<unit> - Einheit umbenennen",
        "_cmd_doc_delunit": "<unit> - Einheit entfernen",
        "_cmd_doc_unit": "<unit> - Einheit verwalten",
        "_cls_doc": "Einfache und bequeme Verwaltung von systemd-Einheiten",
    }

    strings_hi = {
        "panel": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>यहाँ आप अपने systemd"
            " इकाइयों का नियंत्रण कर सकते हैं</b>\n\n{}"
        ),
        "unit_doesnt_exist": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>इकाई</b>"
            " <code>{}</code> <b>अस्तित्व में नहीं है!</b>"
        ),
        "args": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>कोई तर्क निर्दिष्ट"
            " नहीं किया गया</b>"
        ),
        "unit_added": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>इकाई"
            " </b><code>{}</code><b> नाम </b><code>{}</code><b> के साथ जोड़ा गया</b>"
        ),
        "unit_removed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>इकाई"
            " </b><code>{}</code><b> हटा दिया गया</b>"
        ),
        "unit_action_done": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>कार्य"
            " </b><code>{}</code><b> इकाई </b><code>{}</code><b> पर किया गया</b>"
        ),
        "unit_control": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>इकाई"
            " </b><code>{}</code><b> के साथ इंटरैक्ट करें"
            " (</b><code>{}</code><b>)</b>\n{} <b>इकाई स्थिति: </b><code>{}</code>"
        ),
        "action_not_found": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>कार्य"
            " </b><code>{}</code><b> नहीं मिला</b>"
        ),
        "unit_renamed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>इकाई"
            " </b><code>{}</code><b> का नाम बदल दिया गया </b><code>{}</code>"
        ),
        "stop_btn": "🍎 रोकें",
        "start_btn": "🍏 शुरू करें",
        "restart_btn": "🔄 पुनः शुरू करें",
        "logs_btn": "📄 लॉग",
        "tail_btn": "🚅 Tail",
        "back_btn": "🔙 पीछे जाएँ",
        "close_btn": "✖️ बंद करें",
        "refresh_btn": "🔄 ताज़ा करें",
        "_cmd_doc_units": "इकाइयों की सूची दिखाएँ",
        "_cmd_doc_addunit": "<unit> - इकाई जोड़ें",
        "_cmd_doc_nameunit": "<unit> - इकाई का नाम बदलें",
        "_cmd_doc_delunit": "<unit> - इकाई हटाएँ",
        "_cmd_doc_unit": "<unit> - इकाई प्रबंधित करें",
        "_cls_doc": "systemd इकाइयों का सरल और सुविधाजनक प्रबंधन",
    }

    strings_uz = {
        "panel": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Bu yerda siz sizning"
            " systemd birliklaringizni boshqarishingiz mumkin</b>\n\n{}"
        ),
        "unit_doesnt_exist": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Birlik</b>"
            " <code>{}</code> <b>mavjud emas!</b>"
        ),
        "args": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Hech qanday"
            " argumentlar ko'rsatilmadi</b>"
        ),
        "unit_added": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Birlik"
            " </b><code>{}</code><b> nomi </b><code>{}</code><b> qo'shildi</b>"
        ),
        "unit_removed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Birlik"
            " </b><code>{}</code><b> o'chirildi</b>"
        ),
        "unit_action_done": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Amal"
            " </b><code>{}</code><b> birlik </b><code>{}</code><b> uchun bajirildi</b>"
        ),
        "unit_control": (
            "<emoji document_id=5771858080664915483>🎛</emoji> <b>Birlik"
            " </b><code>{}</code><b> bilan ishlash (</b><code>{}</code><b>)</b>\n{}"
            " <b>Birlik holati: </b><code>{}</code>"
        ),
        "action_not_found": (
            "<emoji document_id=5312526098750252863>🚫</emoji> <b>Amal"
            " </b><code>{}</code><b> topilmadi</b>"
        ),
        "unit_renamed": (
            "<emoji document_id=5314250708508220914>✅</emoji> <b>Birlik"
            " </b><code>{}</code><b> nomi </b><code>{}</code><b> o'zgartirildi</b>"
        ),
        "stop_btn": "🍎 To'xtatish",
        "start_btn": "🍏 Boshlash",
        "restart_btn": "🔄 Qayta ishga tushirish",
        "logs_btn": "📄 Jurnal",
        "tail_btn": "🚅 Tail",
        "back_btn": "🔙 Orqaga",
        "close_btn": "✖️ Yopish",
        "refresh_btn": "🔄 Yangilash",
        "_cmd_doc_units": "Birliklar ro'yxatini ko'rsatish",
        "_cmd_doc_addunit": "<birlik> - Birlik qo'shish",
        "_cmd_doc_nameunit": "<birlik> - Birlik nomini o'zgartirish",
        "_cmd_doc_delunit": "<birlik> - Birlikni o'chirish",
        "_cmd_doc_unit": "<birlik> - Birlikni boshqarish",
    }

    # Commands run through ``sudo -n`` (never prompt for a password) in a
    # subprocess with a timeout, so a misconfigured sudoers or a slow
    # journalctl can no longer freeze the whole userbot.
    COMMAND_TIMEOUT = 20
    UNIT_RE = re.compile(r"[A-Za-z0-9@._:\\-]+")

    @classmethod
    def _valid_unit(cls, unit: str) -> bool:
        return bool(unit) and not unit.startswith("-") and bool(cls.UNIT_RE.fullmatch(unit))

    async def _run(self, *command: str, sudo: bool = True) -> tuple:
        if sudo:
            command = ("sudo", "-n", *command)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return 127, "", str(error)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.COMMAND_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return 124, "", "timeout"
        return (
            process.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def _get_unit_status_text(self, unit: str) -> str:
        _, stdout, _ = await self._run("systemctl", "is-active", unit)
        return stdout or "unknown"

    async def _is_running(self, unit: str) -> bool:
        return await self._get_unit_status_text(unit) == "active"

    async def _unit_exists(self, unit: str) -> bool:
        if not self._valid_unit(unit):
            return False
        code, _, _ = await self._run("systemctl", "cat", unit)
        return code == 0

    async def _answer_call(self, call, text: str):
        if not isinstance(call, int):
            await call.answer(text, show_alert=True)

    async def _manage_unit(self, call: Union[InlineCall, int], unit: dict, action: str):
        if action in {"start", "stop", "restart"}:
            code, _, stderr = await self._run("systemctl", action, unit["formal"])
            if code:
                await self._answer_call(call, (stderr or f"exit {code}")[:190])
                return stderr or f"exit {code}"
        elif action in {"logs", "tail"}:
            code, logs, stderr = await self._run(
                "journalctl", "-u", unit["formal"], "-n", "1000", "--no-pager",
                "-o", "short-iso",
            )
            if code:
                await self._answer_call(call, (stderr or f"exit {code}")[:190])
                return stderr or f"exit {code}"

            if action == "logs":
                logs = io.BytesIO(logs.encode())
                logs.name = f"{unit['formal']}-logs.txt"

                await self._client.send_file(
                    call.form["chat"] if not isinstance(call, int) else call, logs
                )
            else:
                # Newest lines that fit into one Telegram message.
                actual_logs = ""
                for line in reversed(logs.splitlines()):
                    chunk = f"{line}\n"
                    if len(utils.escape_html(chunk + actual_logs)) >= 4000:
                        break
                    actual_logs = chunk + actual_logs

                text = f"<code>{utils.escape_html(actual_logs or '—')}</code>"
                if isinstance(call, int):
                    await self.inline.form(
                        text, call, reply_markup=self._get_unit_markup(unit)
                    )
                    return None

                await call.edit(text, reply_markup=self._get_unit_markup(unit))
                await call.answer("Action complete")
                return None

        if isinstance(call, int):
            return None

        await call.answer("Action complete")
        await asyncio.sleep(2)
        await self._control_service(call, unit)
        return None

    def _get_unit_markup(self, unit: dict) -> list:
        return [
            [
                {
                    "text": self.strings("start_btn"),
                    "callback": self._manage_unit,
                    "args": (unit, "start"),
                },
                {
                    "text": self.strings("stop_btn"),
                    "callback": self._manage_unit,
                    "args": (unit, "stop"),
                },
                {
                    "text": self.strings("restart_btn"),
                    "callback": self._manage_unit,
                    "args": (unit, "restart"),
                },
            ],
            [
                {
                    "text": self.strings("logs_btn"),
                    "callback": self._manage_unit,
                    "args": (unit, "logs"),
                },
                {
                    "text": self.strings("tail_btn"),
                    "callback": self._manage_unit,
                    "args": (unit, "tail"),
                },
            ],
            [
                {
                    "text": self.strings("refresh_btn"),
                    "callback": self._control_service,
                    "args": (unit,),
                },
                {
                    "text": self.strings("back_btn"),
                    "callback": self._control_services,
                },
            ],
        ]

    async def _control_service(self, call: InlineCall, unit: dict):
        status = await self._get_unit_status_text(unit["formal"])
        await call.edit(
            self.strings("unit_control").format(
                utils.escape_html(unit["name"]),
                utils.escape_html(unit["formal"]),
                self._status_emoji(status),
                utils.escape_html(status),
            ),
            reply_markup=self._get_unit_markup(unit),
        )

    async def _get_unit_pid(self, unit: str) -> str:
        _, stdout, _ = await self._run(
            "systemctl", "show", unit, "--property=MainPID", "--value"
        )
        return stdout

    async def _get_unit_resources_consumption(self, unit: str) -> str:
        pid = await self._get_unit_pid(unit)
        if not pid.isdigit() or pid == "0":
            return ""
        code, stdout, _ = await self._run(
            "ps", "-p", pid, "-o", "rss=", "-o", "%cpu=", sudo=False
        )
        try:
            rss, cpu = stdout.split()[:2]
            ram = human_readable_size(int(rss) * 1024)
        except ValueError:
            return ""
        if code:
            return ""
        return f"📟 <code>{ram}</code> | 🗃 <code>{cpu}%</code>"

    async def _unit_line(self, unit: dict) -> str:
        status = await self._get_unit_status_text(unit["formal"])
        resources = (
            await self._get_unit_resources_consumption(unit["formal"])
            if status == "active"
            else ""
        )
        return (
            f"{self._status_emoji(status)} <b>{utils.escape_html(unit['name'])}</b>"
            f" (<code>{utils.escape_html(unit['formal'])}</code>):"
            f" {utils.escape_html(status)} {resources}"
        )

    async def _get_panel(self):
        services = self.get("services", [])
        if not services:
            return self.strings("panel").format(
                self.strings("empty_panel").format(utils.escape_html(self.get_prefix()))
            )
        lines = await asyncio.gather(*(self._unit_line(unit) for unit in services))
        return self.strings("panel").format("\n".join(lines))

    async def _control_services(self, call: InlineCall, refresh: bool = False):
        await call.edit(
            await self._get_panel(),
            reply_markup=await self._get_services_markup(),
        )

        if refresh:
            await call.answer("Information updated!")

    @staticmethod
    def _status_emoji(status: str) -> str:
        return {
            "active": "🍏",
            "inactive": "🍎",
            "failed": "🚫",
            "activating": "🔄",
            "deactivating": "🔄",
        }.get(status, "❓")

    async def _get_services_markup(self) -> list:
        services = self.get("services", [])
        statuses = await asyncio.gather(
            *(self._get_unit_status_text(service["formal"]) for service in services)
        )
        return utils.chunks(
            [
                {
                    "text": self._status_emoji(status) + " " + service["name"],
                    "callback": self._control_service,
                    "args": (service,),
                }
                for service, status in zip(services, statuses)
            ],
            2,
        ) + [
            [
                {
                    "text": self.strings("refresh_btn"),
                    "callback": self._control_services,
                    "args": (True,),
                },
                {"text": self.strings("close_btn"), "action": "close"},
            ]
        ]

    async def unitscmd(self, message: Message):
        """Open control panel"""
        await self.inline.form(
            await self._get_panel(),
            message,
            reply_markup=await self._get_services_markup(),
        )

    async def _check_unit(self, message, unit: str) -> bool:
        if not self._valid_unit(unit):
            await utils.answer(
                message, self.strings("bad_unit").format(utils.escape_html(unit))
            )
            return False
        if not await self._unit_exists(unit):
            await utils.answer(
                message,
                self.strings("unit_doesnt_exist").format(utils.escape_html(unit)),
            )
            return False
        return True

    async def addunitcmd(self, message: Message):
        """<unit> <name> - Add new unit"""
        args = utils.get_args_raw(message)
        if not args:
            await utils.answer(message, self.strings("args"))
            return

        try:
            unit, name = args.split(maxsplit=1)
        except ValueError:
            unit = args
            name = args

        if not await self._check_unit(message, unit):
            return

        services = [
            service for service in self.get("services", []) if service["formal"] != unit
        ]
        self.set("services", services + [{"name": name, "formal": unit}])
        await utils.answer(
            message,
            self.strings("unit_added").format(
                utils.escape_html(unit), utils.escape_html(name)
            ),
        )

    async def delunitcmd(self, message: Message):
        """<unit> - Delete unit"""
        args = utils.get_args_raw(message).strip()
        if not args:
            await utils.answer(message, self.strings("args"))
            return

        if not any(unit["formal"] == args for unit in self.get("services", [])):
            await utils.answer(
                message, self.strings("unit_doesnt_exist").format(utils.escape_html(args))
            )
            return

        self.set(
            "services",
            [
                service
                for service in self.get("services", [])
                if service["formal"] != args
            ],
        )
        await utils.answer(message, self.strings("unit_removed").format(utils.escape_html(args)))

    async def unitcmd(self, message: Message):
        """<unit> <start|stop|restart|logs|tail> - Perform specific action on unit bypassing main menu"""
        args = utils.get_args_raw(message)
        if not args or len(args.split()) < 2:
            await utils.answer(message, self.strings("args"))
            return

        unit, action = args.split(maxsplit=1)
        action = action.strip().lower()
        if action not in {"start", "stop", "restart", "logs", "tail"}:
            await utils.answer(
                message,
                self.strings("action_not_found").format(utils.escape_html(action)),
            )
            return
        if not await self._check_unit(message, unit):
            return

        error = await self._manage_unit(
            utils.get_chat_id(message),
            {"formal": unit, "name": unit},
            action,
        )
        if error:
            await utils.answer(
                message,
                self.strings("action_failed").format(
                    utils.escape_html(action), utils.escape_html(error[:500])
                ),
            )
            return

        await utils.answer(
            message,
            self.strings("unit_action_done").format(
                utils.escape_html(action), utils.escape_html(unit)
            ),
        )

    async def nameunitcmd(self, message: Message):
        """<unit> <new_name> - Rename unit"""
        args = utils.get_args_raw(message)
        if not args or len(args.split()) < 2:
            await utils.answer(message, self.strings("args"))
            return

        unit, name = args.split(maxsplit=1)
        services = self.get("services", [])
        if not any(unit_["formal"] == unit for unit_ in services):
            await utils.answer(
                message, self.strings("unit_doesnt_exist").format(utils.escape_html(unit))
            )
            return

        # Keep the unit at its position in the panel.
        self.set(
            "services",
            [
                {**service, "name": name} if service["formal"] == unit else service
                for service in services
            ],
        )
        await utils.answer(
            message,
            self.strings("unit_renamed").format(
                utils.escape_html(unit), utils.escape_html(name)
            ),
        )
