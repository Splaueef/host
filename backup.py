# meta developer: @Huai_Baike
# meta version: 1.0.0
# meta description: Повний локальний бекап Hikka та встановлених модулів.

"""Create and restore portable Hikka backups in the current Telegram chat."""

import contextlib
import datetime
import io
import json
import logging
import zipfile
from pathlib import Path, PurePosixPath

from telethon.tl.types import Message

from .. import loader, utils


logger = logging.getLogger(__name__)

BACKUP_FORMAT = "hikka-full-backup"
BACKUP_VERSION = 1
MAX_ARCHIVE_SIZE = 100 * 1024 * 1024
MAX_MODULE_SIZE = 5 * 1024 * 1024


@loader.tds
class FullBackupMod(loader.Module):
    """Повний бекап даних Hikka та сторонніх модулів"""

    strings = {
        "name": "FullBackup",
        "creating": "⏳ <b>Створюю повний бекап…</b>",
        "caption": (
            "📦 <b>Повний бекап Hikka готовий</b>\n"
            "Модулів у файлі: <b>{}</b>\n\n"
            "⚠️ Файл містить налаштування та приватні дані. Не пересилайте його "
            "стороннім. Для відновлення дайте відповідь на файл командою "
            "<code>{}restoreall</code>."
        ),
        "reply_to_file": (
            "❌ <b>Дайте відповідь командою на файл бекапу, створений "
            "<code>backupall</code>.</b>"
        ),
        "restoring": "⏳ <b>Перевіряю та відновлюю бекап…</b>",
        "invalid": "❌ <b>Це невалідний або пошкоджений бекап:</b> <code>{}</code>",
        "restored": (
            "✅ <b>Бекап відновлено.</b> Hikka перезапускається, щоб завантажити "
            "модулі та налаштування."
        ),
    }

    async def client_ready(self, client, db):
        self._client = client
        self._db = db

    @staticmethod
    def _module_directory() -> Path:
        """Return the custom-module directory across supported Hikka versions."""
        path = getattr(loader, "LOADED_MODULES_PATH", None)
        if path is None:
            path = getattr(loader, "LOADED_MODULES_DIR", None)
        if path is None:
            raise RuntimeError("Hikka не надала шлях до встановлених модулів")
        return Path(path)

    def _module_files(self):
        """Yield user-specific persisted module files only."""
        directory = self._module_directory()
        if not directory.is_dir():
            return

        suffix = f"{self.tg_id}.py"
        for path in sorted(directory.rglob("*.py")):
            if path.is_file() and path.name.endswith(suffix):
                yield path

    def _make_archive(self):
        database = json.loads(json.dumps(self._db, ensure_ascii=False))
        module_links = self.lookup("Loader").get("loaded_modules", {})
        if not isinstance(module_links, dict):
            module_links = {}

        result = io.BytesIO()
        count = len(module_links)
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": BACKUP_FORMAT,
                        "version": BACKUP_VERSION,
                        "created_at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "database.json",
                json.dumps(database, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "module_links.json",
                json.dumps(module_links, ensure_ascii=False, indent=2),
            )
            for path in self._module_files() or ():
                if path.stat().st_size > MAX_MODULE_SIZE:
                    logger.warning("Skipping unusually large module %s", path)
                    continue
                archive.writestr(f"modules/{path.name}", path.read_bytes())
                count += 1

        result.seek(0)
        result.name = f"hikka-full-{datetime.datetime.now():%Y-%m-%d-%H-%M}.zip"
        return result, count

    @loader.command()
    async def backupall(self, message: Message):
        """Створити повний бекап Hikka та надіслати його в поточний чат"""
        status = await utils.answer(message, self.strings("creating"))
        try:
            archive, module_count = self._make_archive()
            await utils.answer_file(
                status,
                archive,
                caption=self.strings("caption").format(
                    module_count,
                    utils.escape_html(self.get_prefix()),
                ),
                force_document=True,
            )
        except Exception as error:
            logger.exception("Unable to create full backup")
            await utils.answer(
                status,
                self.strings("invalid").format(utils.escape_html(str(error))),
            )

    @staticmethod
    def _read_json(archive, name):
        with archive.open(name) as source:
            return json.loads(source.read().decode("utf-8"))

    def _validate_archive(self, payload):
        if len(payload) > MAX_ARCHIVE_SIZE:
            raise ValueError("файл завеликий")

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_ARCHIVE_SIZE:
                raise ValueError("розпаковані дані завеликі")
            names = archive.namelist()
            manifest = self._read_json(archive, "manifest.json")
            database = self._read_json(archive, "database.json")
            links = self._read_json(archive, "module_links.json")

            if manifest.get("format") != BACKUP_FORMAT:
                raise ValueError("невідомий формат")
            if manifest.get("version") != BACKUP_VERSION:
                raise ValueError("непідтримувана версія")
            if not isinstance(database, dict):
                raise ValueError("database.json не є об’єктом")
            if not isinstance(links, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in links.items()
            ):
                raise ValueError("некоректний список модулів")
            if not self._db.process_db_autofix(database):
                raise ValueError("база даних не пройшла перевірку Hikka")

            modules = {}
            for name in names:
                parts = PurePosixPath(name).parts
                if len(parts) != 2 or parts[0] != "modules":
                    continue
                filename = parts[1]
                if Path(filename).name != filename or not filename.endswith(".py"):
                    raise ValueError("небезпечне ім’я модуля")
                info = archive.getinfo(name)
                if info.file_size > MAX_MODULE_SIZE:
                    raise ValueError(f"модуль {filename} завеликий")
                modules[filename] = archive.read(name)

        return database, links, modules

    @loader.command()
    async def restoreall(self, message: Message):
        """Відновити бекап із файла у відповіді та перезапустити Hikka"""
        reply = await message.get_reply_message()
        if not reply or not reply.media:
            await utils.answer(message, self.strings("reply_to_file"))
            return

        status = await utils.answer(message, self.strings("restoring"))
        try:
            payload = await reply.download_media(bytes)
            database, links, modules = self._validate_archive(payload)

            # A token belongs to the current installation and must never be
            # replaced by a backup from another instance. Preserve the current
            # value rather than dropping it when the database is replaced.
            current_token = self._db.get("hikka.inline", {}).get("bot_token")
            with contextlib.suppress(KeyError):
                database["hikka.inline"].pop("bot_token")
            if current_token:
                database.setdefault("hikka.inline", {})["bot_token"] = current_token

            directory = self._module_directory()
            directory.mkdir(parents=True, exist_ok=True)
            for filename, source in modules.items():
                (directory / filename).write_bytes(source)

            loader_module = self.lookup("Loader")
            loader_module.set("loaded_modules", links)
            self._db.clear()
            self._db.update(database)
            # Keep module links authoritative even for backups made by older
            # or customized Loader versions.
            loader_module.set("loaded_modules", links)
            self._db.save()

            await utils.answer(status, self.strings("restored"))
            await self.invoke("restart", "-f", peer=message.peer_id)
        except Exception as error:
            logger.exception("Unable to restore full backup")
            await utils.answer(
                status,
                self.strings("invalid").format(utils.escape_html(str(error))),
            )
