#             █ █ ▀ █▄▀ ▄▀█ █▀█ ▀
# meta version: 1.1.0
#             █▀█ █ █ █ █▀█ █▀▄ █
#              © Copyright 2022
#           https://t.me/hikariatama
#
# 🔒      Licensed under the GNU AGPLv3
# 🌐 https://www.gnu.org/licenses/agpl-3.0.html

# scope: hikka_min 1.2.10

# May be working a lil bit weird, because info was manually
# parsed from telegram schema and official telethon search
# mechanism was used as a base for this search

# meta pic: https://i.imgur.com/jH9i1SW.jpeg
# meta banner: https://mods.hikariatama.ru/badges/teledocs.jpg
# meta developer: @rotkranz
# scope: inline
# scope: hikka_only

import logging
import re

import requests as rqsts
from telethon.tl.types import Message

from .. import loader, utils
from ..inline.types import InlineCall

logger = logging.getLogger(__name__)


def get_message(i: dict) -> str:
    return (
        f"🔧 <a href=\"https://tl.telethon.dev/{i['link']}\">{i['result']}</a>\n\n"
        "🍙 <b>Parameters:</b>\n\n"
        f"ℹ️ <i>{utils.escape_html(re.sub(r'<.*?>', '', i['description'][0]))}</i>\n\n"
        f"{i['description'][1]}\n\n"
        "🦀 <b>Example:</b>\n\n"
        f"<pre>{utils.escape_html(i['example'])}</pre>"
    )


@loader.tds
class TeledocsMod(loader.Module):
    """Telethon docs in your pocket"""

    strings = {
        "name": "Teledocs",
        "usage": "ℹ️ <b>Використання:</b> <code>{}tl SendMessageRequest</code>",
        "not_found": "🔎 <b>Нічого не знайдено за запитом</b> <code>{}</code>",
        "unavailable": "⚠️ <b>Документацію Telethon зараз не вдалося завантажити.</b>",
    }

    _tl = None

    @staticmethod
    def _find(haystack: list, needle: str):
        if needle in haystack:
            return 0
        if not haystack or not needle:
            return -1

        haystack_index, needle_index, penalty, started = 0, 0, 0, False
        while True:
            while needle[needle_index] < "a" or needle[needle_index] > "z":
                needle_index += 1
                if needle_index == len(needle):
                    return penalty

            while haystack[haystack_index] != needle[needle_index]:
                haystack_index += 1
                if started:
                    penalty += 1

                if haystack_index == len(haystack):
                    return -1

            haystack_index += 1
            needle_index += 1
            started = True
            if needle_index == len(needle):
                return penalty

            if haystack_index == len(haystack):
                return -1

    def _get_search_array(self, original: list, original_urls: list, query: str):
        destination, destination_urls = [], []
        for i, (item, itemu) in enumerate(zip(original, original_urls)):
            penalty = self._find(item.lower(), query)
            if penalty > -1 and penalty < len(item) / 3:
                destination += [[item, i]]
                destination_urls += [itemu]

        return destination, destination_urls

    def _build_list(
        self,
        found_elements: list,
        requests: bool = False,
        constructors: bool = False,
    ) -> list:
        return (
            [
                {
                    "link": link,
                    "result": item[0],
                    "description": self._tl[
                        "requests_desc" if requests else "constructors_desc"
                    ][item[1]],
                    **(
                        {"example": self._tl["requests_ex"][item[1]]}
                        if requests
                        else {"example": ""}
                    ),
                }
                for item, link in zip(*found_elements)
            ]
            if requests or constructors
            else [
                {
                    "link": link,
                    "result": item[0],
                    "description": ["", ""],
                    "example": "",
                }
                for item, link in zip(*found_elements)
            ]
        )

    def search(self, query: str):
        if not self._tl:
            return []
        query = (query or "").strip().lower()
        if not query:
            return []
        found_requests = self._get_search_array(
            self._tl["requests"],
            self._tl["requests_urls"],
            query,
        )
        found_types = self._get_search_array(
            self._tl["types"],
            self._tl["types_urls"],
            query,
        )
        found_constructors = self._get_search_array(
            self._tl["constructors"],
            self._tl["constructors_urls"],
            query,
        )
        return (
            self._build_list(found_requests, True)
            + self._build_list(found_types)
            + self._build_list(found_constructors, False, True)
        )

    async def client_ready(self, client, db):
        await self._load_docs()

    async def _load_docs(self):
        """Download the docs index; failures must not break module loading."""
        if self._tl:
            return True
        try:
            response = await utils.run_sync(
                rqsts.get,
                "https://github.com/hikariatama/assets/raw/master/tl_docs.json",
                timeout=30,
            )
            response.raise_for_status()
            self._tl = response.json()
        except Exception:
            logger.exception("Teledocs: cannot download Telethon docs index")
            self._tl = None
        return bool(self._tl)

    @loader.inline_everyone
    async def tl_inline_handler(self, query: InlineCall):
        await self._load_docs()
        return [
            {
                "title": i["result"],
                "description": re.sub("<.*?>", "", i["description"][0]),
                "message": get_message(i),
            }
            for i in self.search(query.args)
            if i["description"][0]
        ][:50]

    @loader.command()
    async def tl(self, message: Message):
        """<ref> - Return telethon reference"""
        query = utils.get_args_raw(message).strip()
        if not query:
            return await utils.answer(
                message,
                self.strings["usage"].format(utils.escape_html(self.get_prefix())),
            )
        if not await self._load_docs():
            return await utils.answer(message, self.strings["unavailable"])
        results = self.search(query)
        if not results:
            return await utils.answer(
                message, self.strings["not_found"].format(utils.escape_html(query))
            )
        await utils.answer(message, get_message(results[0]))
