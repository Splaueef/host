#             █ █ ▀ █▄▀ ▄▀█ █▀█ ▀
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
# meta version: 1.1.0
# scope: inline
# scope: hikka_only

import json
import logging
import re
from urllib.request import urlopen

from telethon.tl.types import Message

from .. import loader, utils
from ..inline.types import InlineCall

logger = logging.getLogger(__name__)
TL_DOCS_URL = "https://raw.githubusercontent.com/hikariatama/assets/master/tl_docs.json"
MAX_TL_DOCS_BYTES = 10 * 1024 * 1024


def _download_docs():
    with urlopen(TL_DOCS_URL, timeout=20) as response:
        raw = response.read(MAX_TL_DOCS_BYTES + 1)
    if len(raw) > MAX_TL_DOCS_BYTES:
        raise ValueError("documentation index is too large")
    return json.loads(raw.decode("utf-8"))


def get_message(i: dict) -> str:
    link = str(i.get("link") or "").lstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", link):
        link = ""
    result = utils.escape_html(str(i.get("result") or "?"))
    title = (
        f'<a href="https://tl.telethon.dev/{link}">{result}</a>'
        if link
        else result
    )
    description = i.get("description")
    if not isinstance(description, list):
        description = []
    description = (description + ["", ""])[:2]
    example = str(i.get("example") or "")
    return (
        f"🔧 {title}\n\n"
        "🍙 <b>Parameters:</b>\n\n"
        f"ℹ️ <i>{utils.escape_html(re.sub(r'<.*?>', '', str(description[0])))}</i>\n\n"
        f"{utils.escape_html(re.sub(r'<.*?>', '', str(description[1])))}\n\n"
        "🦀 <b>Example:</b>\n\n"
        f"<pre>{utils.escape_html(example)}</pre>"
    )


@loader.tds
class TeledocsMod(loader.Module):
    """Telethon docs in your pocket"""

    strings = {
        "name": "Teledocs",
        "no_query": "<b>Вкажи назву Telethon request/type/constructor.</b>",
        "not_found": "<b>Нічого не знайдено.</b>",
        "unavailable": "<b>Документація Telethon тимчасово недоступна.</b>",
    }

    @staticmethod
    def _find(haystack: str, needle: str):
        haystack = str(haystack).casefold()
        needle = "".join(char for char in str(needle).casefold() if char.isalnum())
        if not needle:
            return -1
        if needle in haystack:
            return 0

        position = -1
        penalty = 0
        for character in needle:
            found = haystack.find(character, position + 1)
            if found < 0:
                return -1
            if position >= 0:
                penalty += found - position - 1
            position = found
        return penalty

    def _get_search_array(self, original: list, original_urls: list, query: str):
        destination, destination_urls = [], []
        for i, (item, itemu) in enumerate(zip(original, original_urls)):
            item = str(item)
            penalty = self._find(item, query)
            if 0 <= penalty < len(item) / 3:
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
        query = str(query or "").strip().casefold()
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
        self._tl = None
        try:
            payload = await utils.run_sync(_download_docs)
            required = {
                "requests", "requests_urls", "requests_desc", "requests_ex",
                "types", "types_urls", "constructors", "constructors_urls",
                "constructors_desc",
            }
            if not isinstance(payload, dict) or not required.issubset(payload):
                raise ValueError("unexpected documentation schema")
            if not all(isinstance(payload[key], list) for key in required):
                raise ValueError("unexpected documentation values")
            for names in ("requests", "types", "constructors"):
                related = [f"{names}_urls"]
                if names != "types":
                    related.append(f"{names}_desc")
                if names == "requests":
                    related.append("requests_ex")
                if any(len(payload[key]) != len(payload[names]) for key in related):
                    raise ValueError("misaligned documentation values")
            self._tl = payload
        except Exception:
            logger.exception("Teledocs: failed to load the documentation index")

    @loader.inline_everyone
    async def tl_inline_handler(self, query: InlineCall):
        if not self._tl:
            return []
        return [
            {
                "title": i["result"],
                "description": re.sub("<.*?>", "", str(i["description"][0])),
                "message": get_message(i),
            }
            for i in self.search(query.args)
            if i["description"][0]
        ][:50]

    async def tlcmd(self, message: Message):
        """<ref> - Return telethon reference"""
        if not self._tl:
            return await utils.answer(message, self.strings("unavailable", message))
        query = utils.get_args_raw(message).strip()
        if not query:
            return await utils.answer(message, self.strings("no_query", message))
        results = self.search(query)
        if not results:
            return await utils.answer(message, self.strings("not_found", message))
        await utils.answer(message, get_message(results[0]))
