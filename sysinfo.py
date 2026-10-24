# meta developer: @Huai_Baike
# meta version: 2.1.0
# meta description: Безпечна системна інформація без конфлікту з вбудованою .info.

"""Show compact runtime and host information for Hikka."""

import asyncio
import contextlib
import os
import platform
import shutil
from pathlib import Path

import telethon

from .. import loader, utils


@loader.tds
class InfoMod(loader.Module):
    """🖥 Показує стан системи, де працює Hikka"""

    strings = {
        "name": "SystemInfo",
        "unavailable": "н/д",
    }

    @staticmethod
    def _human_bytes(value):
        value = max(0, int(value or 0))
        units = ("Б", "КіБ", "МіБ", "ГіБ", "ТіБ")
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.0f} {unit}" if unit == "Б" else f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{amount:.1f} ТіБ"

    @staticmethod
    def _bar(percent, width=10):
        filled = max(0, min(width, round(float(percent or 0) / 100 * width)))
        return "▰" * filled + "▱" * (width - filled)

    @staticmethod
    def _duration(seconds):
        seconds = max(0, int(seconds or 0))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days} д")
        if hours or days:
            parts.append(f"{hours} год")
        parts.append(f"{minutes} хв")
        return " ".join(parts)

    @staticmethod
    def _os_name():
        with contextlib.suppress(Exception):
            info = platform.freedesktop_os_release()
            if info.get("PRETTY_NAME"):
                return info["PRETTY_NAME"]
        return platform.platform(aliased=True, terse=True)

    @staticmethod
    def _cpu_name():
        name = platform.processor().strip()
        if name:
            return name
        with contextlib.suppress(OSError):
            for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        return platform.machine() or "н/д"

    @staticmethod
    def _memory():
        values = {}
        with contextlib.suppress(OSError, ValueError):
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        return max(0, total - available), total

    @staticmethod
    def _uptime():
        with contextlib.suppress(OSError, ValueError):
            return float(Path("/proc/uptime").read_text().split()[0])
        return 0

    @staticmethod
    def _environment():
        if os.path.exists("/.dockerenv"):
            return "Docker"
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            return "Kubernetes"
        if "DYNO" in os.environ:
            return "Heroku"
        if "LAVHOST" in os.environ:
            return f"lavHost {os.environ['LAVHOST']}"
        return "VPS / локальна система"

    @staticmethod
    def _repo_root():
        with contextlib.suppress(Exception):
            start = Path(utils.get_base_dir()).resolve()
            if start.is_file():
                start = start.parent
            for candidate in (start, *start.parents):
                if (candidate / ".git").exists():
                    return candidate
        return None

    async def _git_revision(self):
        root = self._repo_root()
        if root is None or not shutil.which("git"):
            return None
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            "show",
            "-s",
            "--format=%h · %cs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return None
        if process.returncode:
            return None
        return stdout.decode(errors="replace").strip() or None

    async def _report(self):
        used_memory, total_memory = self._memory()
        disk = shutil.disk_usage("/")
        load = None
        with contextlib.suppress(OSError):
            load = os.getloadavg()
        git_revision = await self._git_revision()
        esc = utils.escape_html
        system = [
            f"🐧 <b>Система:</b> <code>{esc(self._os_name())}</code>",
            f"🧩 <b>Ядро:</b> <code>{esc(platform.release())}</code> · "
            f"<code>{esc(platform.machine())}</code>",
            f"📦 <b>Середовище:</b> {esc(self._environment())}",
            f"⏱ <b>Uptime:</b> {self._duration(self._uptime())}",
        ]
        cpu = [
            f"⚙️ <b>CPU:</b> <code>{esc(self._cpu_name())}</code>",
            f"🧵 <b>Потоків:</b> {os.cpu_count() or self.strings['unavailable']}",
        ]
        if load is not None:
            cpu.append(
                f"📈 <b>Load:</b> <code>{load[0]:.2f} · {load[1]:.2f} · {load[2]:.2f}</code>"
            )
        memory_percent = used_memory / total_memory * 100 if total_memory else 0
        disk_percent = disk.used / disk.total * 100 if disk.total else 0
        resources = [
            f"🧠 <b>RAM</b> {self._bar(memory_percent)} {memory_percent:.0f}%\n"
            f"<code>{self._human_bytes(used_memory)} / {self._human_bytes(total_memory)}</code>",
            f"💽 <b>Диск</b> {self._bar(disk_percent)} {disk_percent:.0f}%\n"
            f"<code>{self._human_bytes(disk.used)} / {self._human_bytes(disk.total)}</code>",
        ]
        runtime = [
            f"🐍 <b>Python</b> <code>{esc(platform.python_version())}</code> · "
            f"✈️ <b>Telethon</b> <code>{esc(telethon.__version__)}</code>",
        ]
        if git_revision:
            runtime.append(f"🌿 <b>Hikka git:</b> <code>{esc(git_revision)}</code>")
        lines = ["🖥 <b>SystemInfo · Hikka</b>"]
        for block in (system, cpu, resources, runtime):
            lines.append("<blockquote>" + "\n".join(block) + "</blockquote>")
        return "\n".join(lines)

    @loader.command(ru_doc="Показати інформацію про систему та Hikka")
    async def sysinfo(self, message):
        """🖥 Стан ОС, CPU, RAM, диска та середовища Hikka"""
        await utils.answer(message, await self._report())
