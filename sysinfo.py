import asyncio
import logging
import os
import platform
import shutil
import sys

import telethon

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class InfoMod(loader.Module):
    """Provides system information about the computer hosting this bot"""

    strings = {
        "name": "System Info",
        "info_title": "<b>System Info</b>",
        "kernel": "<b>Kernel:</b> <code>{}</code>",
        "arch": "<b>Arch:</b> <code>{}</code>",
        "os": "<b>OS:</b> <code>{}</code>",
        "heroku": "FTG Installed on <b>Heroku</b>",
        "distro": "<b>Linux Distribution:</b> <code>{}</code>",
        "android_sdk": "<b>Android SDK:</b> <code>{}</code>",
        "android_ver": "<b>Android Version:</b> <code>{}</code>",
        "android_patch": "<b>Android Security Patch:</b> <code>{}</code>",
        "unknown_distro": "<b>Could not determine Linux distribution.</b>",
        "python_version": "<b>Python version:</b> <code>{}</code>",
        "telethon_version": "<b>Telethon version:</b> <code>{}</code>",
        "git_version": "<b>Git version:</b> <code>{}</code>",
        "ftg_type": "<b>FTG Type:</b> <code>{}</code>",
    }

    @staticmethod
    async def _git_version() -> str:
        git = shutil.which("git")
        if not git:
            return "unknown"
        try:
            process = await asyncio.create_subprocess_exec(
                git,
                "-C",
                utils.get_base_dir(),
                "show",
                "-s",
                "--format=%h %cs",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
            if process.returncode == 0 and stdout.strip():
                return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
        except (OSError, ValueError):
            logger.debug("Could not read git version", exc_info=True)
        return "unknown"

    async def infocmd(self, message):
        """Shows system information"""
        ftg_type = "PC/Server"
        reply = self.strings("info_title", message)
        reply += "\n" + self.strings("kernel", message).format(
            utils.escape_html(platform.release())
        )
        reply += "\n" + self.strings("arch", message).format(
            utils.escape_html(platform.machine() or "unknown")
        )
        reply += "\n" + self.strings("os", message).format(
            utils.escape_html(platform.system())
        )

        if platform.system() == "Linux":
            done = False
            try:
                release = platform.freedesktop_os_release()
                reply += "\n" + self.strings("distro", message).format(
                    utils.escape_html(release.get("PRETTY_NAME") or release.get("NAME") or "unknown")
                )
                done = True
            except OSError:
                ftg_type = "Android (Termux)"
                getprop = shutil.which("getprop")
                if getprop is not None:
                    sdk = await asyncio.create_subprocess_exec(
                        getprop, "ro.build.version.sdk", stdout=asyncio.subprocess.PIPE
                    )
                    ver = await asyncio.create_subprocess_exec(
                        getprop,
                        "ro.build.version.release",
                        stdout=asyncio.subprocess.PIPE,
                    )
                    sec = await asyncio.create_subprocess_exec(
                        getprop,
                        "ro.build.version.security_patch",
                        stdout=asyncio.subprocess.PIPE,
                    )
                    (sdks, _), (vers, _), (secs, _) = await asyncio.gather(
                        sdk.communicate(), ver.communicate(), sec.communicate()
                    )
                    if (
                        sdk.returncode == 0
                        and ver.returncode == 0
                        and sec.returncode == 0
                    ):
                        reply += "\n" + self.strings("android_sdk", message).format(
                            utils.escape_html(sdks.decode("utf-8", errors="replace").strip())
                        )
                        reply += "\n" + self.strings("android_ver", message).format(
                            utils.escape_html(vers.decode("utf-8", errors="replace").strip())
                        )
                        reply += "\n" + self.strings("android_patch", message).format(
                            utils.escape_html(secs.decode("utf-8", errors="replace").strip())
                        )
                        done = True
            if not done:
                reply += "\n" + self.strings("unknown_distro", message)
        reply += "\n" + self.strings("python_version", message).format(
            utils.escape_html(sys.version)
        )
        reply += "\n" + self.strings("telethon_version", message).format(
            utils.escape_html(telethon.__version__)
        )
        if "DYNO" in os.environ:
            ftg_type = "Heroku"
        else:
            reply += "\n" + self.strings("git_version", message).format(
                utils.escape_html(await self._git_version())
            )
        if "LAVHOST" in os.environ:
            reply += (
                "\n"
                + "<b>FTG Type:</b> "
                + f"<code>lavHost {utils.escape_html(os.getenv('LAVHOST'))}</code> (@lavHost)"
            )
        else:
            reply += "\n" + self.strings("ftg_type", message).format(
                utils.escape_html(ftg_type)
            )
        await utils.answer(message, reply)
