__version__ = (1, 3, 0)

# ©️ Dan Gazizullin, 2021-2022
# This file is a part of Hikka Userbot
# 🌐 https://github.com/hikariatama/Hikka
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

# meta pic: https://github.com/Splaueef/assets/raw/main/hikka_banner.png
# meta banner: https://github.com/Splaueef/assets/raw/main/hikka_banner.png
# meta developer: @rotkranz
# scope: hikka_only
# scope: hikka_min 1.6.0

import contextlib
import io
import logging
import re
import time
import typing
from pathlib import Path

from telethon.tl.types import (
    DocumentAttributeFilename,
    Message,
    PeerChat,
    UpdateDeleteChannelMessages,
    UpdateDeleteMessages,
    UpdateEditChannelMessage,
    UpdateEditMessage,
)
from telethon.utils import get_display_name

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class NekoSpy(loader.Module):
    """ідправляє тобі повідомлення, які були видалені та/або відредаговані обраними користувачами"""

    rei = "<emoji document_id=5409143295039252230>👩‍🎤</emoji>"
    groups = "<emoji document_id=6037355667365300960>👥</emoji>"
    pm = "<emoji document_id=6048540195995782913>👤</emoji>"
    backup_root_name = "NekoSpyBSP"

    strings = {
    "name": "NekoSpy",
    "state": f"{rei} <b>Режим шпигування зараз {{}}</b>",
    "spybl": f"{rei} <b>Поточний чат додано до чорного списку шпигування.</b>",
    "spybl_removed": f"{rei} <b>Поточний чат видалено з чорного списку шпигування.</b>",
    "spybl_clear": f"{rei} <b>Список ігнорування для шпигування очищено.</b>",
    "spywl": f"{rei} <b>Поточний чат додано до білого списку для шпигування</b>",
    "spywl_removed": f"{rei} <b>Поточний чат видалено з білого списку для шпигування</b>",
    "spywl_clear": f"{rei} <b>Список включення для шпигування очищено</b>",
    "whitelist": f"\n{rei} <b>Відстежуються лише повідомлення з:</b>\n{{}}",
    "always_track": f"\n{rei} <b>Завжди відстежуються повідомлення з:</b>\n{{}}",
    "blacklist": f"\n{rei} <b>Ігноруються повідомлення з:</b>\n{{}}",
    "chat": f"{groups} <b>Відстежуються повідомлення у групах</b>\n",
    "pm": f"{pm} <b>Відстежуються повідомлення у приватних повідомленнях</b>\n",
    "mode_off": f"{pm} <b>Повідомлення не відстежуються </b><code>{{}}spymode</code>\n",
    "deleted_pm": (
        '🗑 <b><a href="{}">{}</a> видалив(ла) <a href="{message_url}">повідомлення</a> у'
        " приватному чаті. Вміст:</b>\n{}"
    ),
    "deleted_chat": (
        '🗑 <b><a href="{message_url}">Повідомлення</a> у чаті <a href="{}">{}</a> від <a'
        ' href="{}">{}</a> було видалено. Вміст:</b>\n{}'
    ),
    "edited_pm": (
        '🔏 <b><a href="{}">{}</a> відредагував(ла) <a href="{message_url}">повідомлення</a>'
        " у приватному чаті."
        " Старий вміст:</b>\n{}"
    ),
    "edited_chat": (
        '🔏 <b><a href="{message_url}">Повідомлення</a> у чаті <a href="{}">{}</a>'
        " від <a"
        ' href="{}">{}</a> було відредаговано. Старий вміст:</b>\n{}'
    ),
    "on": "увімкнено",
    "off": "вимкнено",
    "cfg_enable_pm": "Увімкнути режим шпигування у приватних повідомленнях",
    "cfg_enable_groups": "Увімкнути режим шпигування у групах",
    "cfg_whitelist": "Список чатів, повідомлення з яких потрібно відстежувати",
    "cfg_blacklist": "Список чатів, повідомлення з яких потрібно виключити",
    "cfg_always_track": (
        "Список чатів, повідомлення з яких завжди відстежуються незалежно від умов"
    ),
    "cfg_log_edits": "Записувати інформацію про редагування повідомлень",
    "cfg_ignore_inline": "Ігнорувати inline-повідомлення (надіслані через @ботів)",
    "cfg_fw_protect": "Інтервал надсилання повідомлень для запобігання floodwait",
    "sd_media": (
        "🔥 <b><a href='tg://user?id={}'>{}</a> надіслав(ла) вам медіа з самознищенням</b>"
    ),
    "sd_media_out": "🔥 <b>Ви надіслали медіа з одноразовим переглядом</b>",
    "save_sd": (
        "<emoji document_id=5420315771991497307>🔥</emoji> <b>Збереження"
        " медіа з самознищенням</b>\n"
    ),
    "cfg_save_sd": "Зберігати медіа з самознищенням",
} 

    strings_ru = {
        "on": "включен",
        "off": "выключен",
        "state": f"{rei} <b>Режим слежения теперь {{}}</b>",
        "spybl": f"{rei} <b>Текущий чат добавлен в черный список для слежения</b>",
        "spybl_removed": (
            f"{rei} <b>Текущий чат удален из черного списка для слежения</b>"
        ),
        "spybl_clear": f"{rei} <b>Черный список для слежения очищен</b>",
        "spywl": f"{rei} <b>Текущий чат добавлен в белый список для слежения</b>",
        "spywl_removed": (
            f"{rei} <b>Текущий чат удален из белого списка для слежения</b>"
        ),
        "spywl_clear": f"{rei} <b>Белый список для слежения очищен</b>",
        "whitelist": (
            f"\n{rei} <b>Слежу только"
            " за сообщениями от пользователей / групп:</b>\n{}"
        ),
        "always_track": (
            f"\n{rei} <b>Всегда слежу за сообщениями от пользователей /"
            " групп:</b>\n{}"
        ),
        "blacklist": (
            f"\n{rei} <b>Игнорирую сообщений от пользователей / групп:</b>\n{{}}"
        ),
        "chat": f"{groups} <b>Слежу за сообщениями в группах</b>\n",
        "pm": f"{pm} <b>Слежу за сообщениями в личных сообщениях</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> удалил <a href="{message_url}">сообщение</a> в'
            " личке. Содержимое:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{message_url}">Сообщение</a> в чате <a href="{}">{}</a> от'
            ' <a href="{}">{}</a> было удалено. Содержимое:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> отредактировал <a'
            ' href="{message_url}">сообщение</a> в личке. Старое содержимое:</b>\n{}'
        ),
        "edited_chat": (
            '🔏 <b><a href="{message_url}">Сообщение</a> в чате <a href="{}">{}</a> от'
            ' <a href="{}">{}</a> было отредактировано. Старое содержимое:</b>\n{}'
        ),
        "mode_off": f"{pm} <b>Не отслеживаю сообщения </b><code>{{}}spymode</code>\n",
        "cfg_enable_pm": "Включить режим шпиона в личных сообщениях",
        "cfg_enable_groups": "Включить режим шпиона в группах",
        "cfg_whitelist": "Список чатов, от которых нужно сохранять сообщения",
        "cfg_blacklist": "Список чатов, от которых нужно игнорировать сообщения",
        "cfg_always_track": (
            "Список чатов, от которых всегда следует отслеживать сообщения,"
            " несмотря ни на что"
        ),
        "cfg_log_edits": "Сохранять отредактированные сообщения",
        "cfg_ignore_inline": "Игнорировать сообщения из инлайн-режима",
        "cfg_fw_protect": "Защита от флудвейтов при пересылке",
        "_cls_doc": (
            "Сохраняет удаленные и/или отредактированные сообщения от выбранных"
            " пользователей"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> отправил вам самоуничтожающееся"
            " медиа</b>"
        ),
        "sd_media_out": "🔥 <b>Вы отправили медиа для однократного просмотра</b>",
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Сохраняю"
            " самоуничтожающиеся медиа</b>\n"
        ),
        "cfg_save_sd": "Сохранять самоуничтожающееся медиа",
    }

    strings_it = {
        "on": "attivato",
        "off": "disattivato",
        "state": f"{rei} <b>Modalità di tracciamento ora {{}}</b>",
        "spybl": (
            f"{rei} <b>Il gruppo corrente è stato aggiunto alla lista nera di"
            " tracciamento</b>"
        ),
        "spybl_removed": (
            f"{rei} <b>Il gruppo corrente è stato rimosso dalla lista nera di"
            " tracciamento</b>"
        ),
        "spybl_clear": f"{rei} <b>Lista nera di tracciamento ripulita</b>",
        "spywl": (
            f"{rei} <b>Il gruppo corrente è stato aggiunto alla lista bianca di"
            " tracciamento</b>"
        ),
        "spywl_removed": (
            f"{rei} <b>Il gruppo corrente è stato rimosso dalla lista bianca di"
            " tracciamento</b>"
        ),
        "spywl_clear": f"{rei} <b>Lista bianca di tracciamento ripulita</b>",
        "whitelist": (
            f"\n{rei} <b>Sto tracciando solo messaggi da utenti / gruppi:</b>\n{{}}"
        ),
        "always_track": (
            f"\n{rei} <b>Sto tracciando sempre messaggi da utenti / gruppi:</b>\n{{}}"
        ),
        "blacklist": f"\n{rei} <b>Ignoro messaggi da utenti / gruppi:</b>\n{{}}",
        "chat": f"{groups} <b>Sto tracciando i messaggi nei gruppi</b>\n",
        "pm": f"{pm} <b>Sto tracciando i messaggi nei messaggi privati</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> ha cancellato <a href="{message_url}">il'
            " messaggio</a> in privato. Contenuto:</b>\n{}"
        ),
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> ha eliminato <a'
            ' href="{message_url}">un messaggio</a> in privato. Contenuto:</b>\n{}'
        ),
        "deleted_chat": (
            '🗑 <b><a href="{message_url}">Un messaggio</a> nel gruppo <a'
            ' href="{}">{}</a> da <a href="{}">{}</a> è stato eliminato.'
            " Contenuto:</b>\n{}"
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> ha modificato <a href="{message_url}">un'
            " messaggio</a> in privato. Vecchio contenuto:</b>\n{}"
        ),
        "edited_chat": (
            '🔏 <b><a href="{message_url}">Un messaggio</a> nel gruppo <a'
            ' href="{}">{}</a> da <a href="{}">{}</a> è stato modificato. Vecchio'
            " contenuto:</b>\n{}"
        ),
        "mode_off": (
            f"{pm} <b>Non sto tenendo traccia dei messaggi"
            " </b><code>{}spymode</code>\n"
        ),
        "cfg_enable_pm": "Attiva modalità spia nei messaggi privati",
        "cfg_enable_groups": "Attiva modalità spia nei gruppi",
        "cfg_whitelist": "Lista dei gruppi da cui tenere traccia dei messaggi",
        "cfg_blacklist": "Lista dei gruppi da cui ignorare i messaggi",
        "cfg_always_track": (
            "Lista dei gruppi da cui tenere traccia dei messaggi,"
            " non importa quello che succede"
        ),
        "cfg_log_edits": "Salva i messaggi modificati",
        "cfg_ignore_inline": "Ignora i messaggi in modalità inline",
        "cfg_fw_protect": "Protezione contro floodwate ai messaggi inoltrati",
        "_cls_doc": "Salva i messaggi eliminati e/o modificati da utenti selezionati",
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> ti ha inviato un media"
            " che si autodistrugge</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Salvo"
            " i media che si autodistruggono</b>\n"
        ),
        "cfg_save_sd": "Salva i media che si autodistruggono",
    }

    strings_de = {
        "on": "Aktiviert",
        "off": "Deaktiviert",
        "state": f"{rei} <b>Der Tracking-Modus ist jetzt {{}}.</b>",
        "spybl": (
            f"{rei} <b>Der aktuelle Chat wurde zur Spionage-Blacklist hinzugefügt.</b>"
        ),
        "spybl_removed": (
            f"{rei} <b>Der aktuelle Chat wurde von der Spionage-Blacklist entfernt.</b>"
        ),
        "spybl_clear": f"{rei} <b>Die Spionage-Blacklist wurde geleert.</b>",
        "spywl": (
            f"{rei} <b>Der aktuelle Chat wurde zur Spionage-Whitelist hinzugefügt.</b>"
        ),
        "spywl_removed": (
            f"{rei} <b>Der aktuelle Chat wurde von der Spionage-Whitelist entfernt.</b>"
        ),
        "spywl_clear": f"{rei} <b>Die Spionage-Whitelist wurde geleert.</b>",
        "whitelist": f"\n{rei} <b>Ich beobachte nur Nachrichten von:</b>\n{{}}",
        "always_track": f"\n{rei} <b>Ich beobachte immer Nachrichten von:</b>\n{{}}",
        "blacklist": f"\n{rei} <b>Ich ignoriere Nachrichten von:</b>\n{{}}",
        "chat": f"{groups} <b>Ich beobachte Nachrichten in Gruppen.</b>\n",
        "pm": f"{pm} <b>Ich beobachte Nachrichten in privaten Nachrichten.</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> hat eine private <a'
            ' href="{message_url}">Nachricht</a> gelöscht. Inhalt:</b>\n{}'
        ),
        "deleted_chat": (
            '🗑 <b>Die <a href="{message_url}">Nachricht</a> im Chat <a href="{}">{}</a>'
            ' von <a href="{}">{}</a> wurde gelöscht. Inhalt:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> hat eine private <a'
            ' href="{message_url}">Nachricht</a> bearbeitet. Alte Nachricht:</b>\n{}'
        ),
        "edited_chat": (
            '🔏 <b>Die <a href="{message_url}">Nachricht</a> im Chat <a'
            ' href="{}">{}</a>'
            ' von <a href="{}">{}</a> wurde bearbeitet. Alte Nachricht:</b>\n{}'
        ),
        "mode_off": (
            f"{pm} <b>Ich beobachte"
            " Nachrichten nicht mehr. </b><code>{}spymode</code>\n"
        ),
        "cfg_enable_pm": "Aktivieren Sie den Spionage-Modus in privaten Nachrichten",
        "cfg_enable_groups": "Aktivieren Sie den Spionage-Modus in Gruppen",
        "cfg_whitelist": (
            "Liste der Gruppen, von denen Nachrichten gespeichert werden sollen"
        ),
        "cfg_blacklist": (
            "Liste der Gruppen, von denen Nachrichten ignoriert werden sollen"
        ),
        "cfg_always_track": (
            "Liste der Gruppen, von denen immer Nachrichten verfolgt werden sollen,"
            " egal was passiert"
        ),
        "cfg_log_edits": "Gespeicherte bearbeitete Nachrichten",
        "cfg_ignore_inline": "Ignoriere Nachrichten aus Inline-Modus",
        "cfg_fw_protect": "Schutz vor Floodwässern beim Weiterleiten",
        "_cls_doc": (
            "Speichert gelöschte bearbeitete Nachrichten von ausgewählten Benutzern"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> hat Ihnen ein selbstzerstörendes"
            " Medium gesendet</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Speichere"
            " selbstzerstörende Medien</b>\n"
        ),
        "cfg_save_sd": "Speichern Sie selbstzerstörende Medien",
    }

    strings_uz = {
        "on": "yoqildi",
        "off": "o'chirildi",
        "state": f"{rei} <b>Shu paytda spy rejimi {{}}</b>",
        "spybl": f"{rei} <b>Ushbu chat spay rejimining qora ro'yxatiga qo'shildi</b>",
        "spybl_removed": (
            f"{rei} <b>Ushbu chat spay rejimining qora ro'yxatidan olib tashlandi</b>"
        ),
        "spybl_clear": f"{rei} <b>Spay rejimining qora ro'yxati tozalandi</b>",
        "spywl": f"{rei} <b>Ushbu chat spay rejimining oq ro'yxatiga qo'shildi</b>",
        "spywl_removed": (
            f"{rei} <b>Ushbu chat spay rejimining oq ro'yxatidan olib tashlandi</b>"
        ),
        "spywl_clear": f"{rei} <b>Spay rejimining oq ro'yxati tozalandi</b>",
        "whitelist": f"\n{rei} <b>Faqat kelgan xabarlarni kuzatish</b>\n{{}}",
        "always_track": f"\n{rei} <b>Har doim kelgan xabarlarni kuzatish</b>\n{{}}",
        "blacklist": f"\n{rei} <b> kelgan xabarlarni o'chirish</b>\n{{}}",
        "chat": f"{groups} <b>Gruplardagi xabarlarimni kuzatish</b>\n",
        "pm": f"{pm} <b>Shaxsiy xabarlarimni kuzatish</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> shaxsiy <a href="{message_url}">xabarni</a>'
            " o'chirdi. Xabar:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{}">{}</a> guruhdan <a href="{}">{}</a> <a'
            ' href="{message_url}">xabarni</a> o\'chirdi. Xabar:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> shaxsiy <a href="{message_url}">xabarni</a>'
            " tahrirladi. Eski xabar:</b>\n{}"
        ),
        "edited_chat": (
            '🔏 <b><a href="{}">{}</a> guruhdan <a href="{}">{}</a> <a'
            ' href="{message_url}">xabarni</a> tahrirladi. Eski xabar:</b>\n{}'
        ),
        "mode_off": (
            f"{pm} <b>Xabarlarimni kuzatishni to'xtatdim</b><code>{{}}spymode</code>\n"
        ),
        "cfg_enable_pm": "Shaxsiy xabarlarimni kuzatishni yoqish",
        "cfg_enable_groups": "Guruh xabarlarimni kuzatishni yoqish",
        "cfg_whitelist": "Xabarlarni saqlash kerak bo'lgan suhbatlar ro'yxati",
        "cfg_blacklist": "Xabarlarni o'chirish kerak bo'lgan suhbatlar ro'yxati",
        "cfg_always_track": (
            "Nima bo'lishidan qat'i nazar, har doim xabarlarni kuzatib boradigan"
            " suhbatlar ro'yxati"
        ),
        "cfg_log_edits": "Saqlangan tahrirlangan xabarlarni",
        "cfg_ignore_inline": "Inline rejimidan kelgan xabarlarni o'chirish",
        "cfg_fw_protect": "Forwarding floodlardan himoyalash",
        "_cls_doc": (
            "Tanlangan foydalanuvchilardan kelgan va/yoki o'chirilgan yoki tahrirlangan"
            " xabarlarni saqlaydi"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> sizga o'chiriladigan media"
            " yubordi</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>O'z-o'zini yo'q"
            " qiladigan ommaviy axborot vositalarini saqlash</b>\n"
        ),
        "cfg_save_sd": "O'chiriladigan media saqlash",
    }

    strings_tr = {
        "on": "açık",
        "off": "kapalı",
        "state": f"{rei} <b>Şu anda gizli mod {{}}</b>",
        "spybl": f"{rei} <b>Bu sohbet gizli modun siyah listesine eklendi</b>",
        "spybl_removed": (
            f"{rei} <b>Bu sohbet gizli modun siyah listesinden kaldırıldı</b>"
        ),
        "spybl_clear": f"{rei} <b>Gizli modun siyah listesi temizlendi</b>",
        "spywl": f"{rei} <b>Bu sohbet gizli modun beyaz listesine eklendi</b>",
        "spywl_removed": (
            f"{rei} <b>Bu sohbet gizli modun beyaz listesinden kaldırıldı</b>"
        ),
        "spywl_clear": f"{rei} <b>Gizli modun beyaz listesi temizlendi</b>",
        "whitelist": f"\n{rei} <b>Sadece belirtilen gelen mesajları kaydet</b>\n{{}}",
        "always_track": (
            f"\n{rei} <b>Her zaman belirtilen gelen mesajları kaydet</b>\n{{}}"
        ),
        "blacklist": f"\n{rei} <b>Belirtilen gelen mesajları sil</b>\n{{}}",
        "chat": f"{groups} <b>Grup mesajlarımı kaydet</b>\n",
        "pm": f"{pm} <b>Özel mesajlarımı kaydet</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> özel <a href="{message_url}">mesajı</a> sildi.'
            " Mesaj:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{}">{}</a> guruptan <a href="{}">{}</a> <a'
            ' href="{message_url}">mesajı</a> sildi. Mesaj:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> özel <a href="{message_url}">mesajı</a>'
            " düzenledi. Eski mesaj:</b>\n{}"
        ),
        "edited_chat": (
            '🔏 <b><a href="{}">{}</a> guruptan <a href="{}">{}</a> <a'
            ' href="{message_url}">mesajı</a> düzenledi. Eski mesaj:</b>\n{}'
        ),
        "mode_off": (
            f"{pm} <b>Mesajlarımı kaydetmeyi kapattım</b><code>{{}}spymode</code>\n"
        ),
        "cfg_enable_pm": "Özel mesajlarımı kaydetmeyi aç",
        "cfg_enable_groups": "Grup mesajlarımı kaydetmeyi aç",
        "cfg_whitelist": "Kaydedilmesi gereken sohbetler listesi",
        "cfg_blacklist": "Silinmesi gereken sohbetler listesi",
        "cfg_always_track": (
            "Ne olursa olsun, iletileri her zaman izlenecek sohbetler listesi"
        ),
        "cfg_log_edits": "Kaydedilen düzenlenmiş mesajları",
        "cfg_ignore_inline": "Inline modundan gelen mesajları sil",
        "cfg_fw_protect": "Forwarding floodlarından korun",
        "_cls_doc": (
            "Belirtilen kullanıcıların/sohbetlerin silinmiş, düzenlenmiş veya"
            " kaydedilen mesajlarını kaydeder"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> sana silinebilir medya gönderdi</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Kendi kendini imha"
            " eden medyayı kaydetme</b>\n"
        ),
        "cfg_save_sd": "Silinebilir medyayı kaydet",
    }

    strings_es = {
        "on": "activado",
        "off": "desactivado",
        "state": f"{rei} <b>El modo espía está actualmente {{}}</b>",
        "spybl": (
            f"{rei} <b>Este chat ha sido añadido a la lista negra del modo espía</b>"
        ),
        "spybl_removed": (
            f"{rei} <b>Este chat ha sido eliminado de la lista negra del modo espía</b>"
        ),
        "spybl_clear": f"{rei} <b>La lista negra del modo espía ha sido limpiada</b>",
        "spywl": (
            f"{rei} <b>Este chat ha sido añadido a la lista blanca del modo espía</b>"
        ),
        "spywl_removed": (
            f"{rei} <b>Este chat ha sido"
            " eliminado de la lista blanca del modo espía</b>"
        ),
        "spywl_clear": f"{rei} <b>La lista blanca del modo espía ha sido limpiada</b>",
        "whitelist": (
            f"\n{rei} <b>Guardar solo los mensajes de los especificados</b>\n{{}}"
        ),
        "always_track": (
            f"\n{rei} <b>Guardar siempre los mensajes de los especificados</b>\n{{}}"
        ),
        "blacklist": f"\n{rei} <b>Borrar los mensajes de los especificados</b>\n{{}}",
        "chat": (
            "<emoji document_id=603735566736530096   0>👥</emoji> <b>Guardar mis"
            " mensajes de grupo</b>\n"
        ),
        "pm": f"{pm} <b>Guardar mis mensajes privados</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> eliminó un <a href="{message_url}">mensaje</a>'
            " privado. Mensaje:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{}">{}</a> eliminó un <a href="{message_url}">mensaje</a> de'
            ' <a href="{}">{}</a> en el grupo. Mensaje:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> editó un <a href="{message_url}">mensaje</a>'
            " privado. Mensaje anterior:</b>\n{}"
        ),
        "edited_chat": (
            '🔏 <b><a href="{}">{}</a> editó un <a href="{message_url}">mensaje</a> de'
            ' <a href="{}">{}</a> en el grupo. Mensaje anterior:</b>\n{}'
        ),
        "mode_off": (
            f"{pm} <b>He desactivado el modo espía</b><code>{{}}spymode</code>\n"
        ),
        "cfg_enable_pm": "Guardar mensajes privados",
        "cfg_enable_groups": "Guardar mensajes de grupo",
        "cfg_whitelist": "Lista de Chats a guardar",
        "cfg_blacklist": "Lista de Сhats a borrar",
        "cfg_always_track": (
            "Lista de Chats para rastrear siempre los mensajes, pase lo que pase"
        ),
        "cfg_log_edits": "Guardar mensajes editados",
        "cfg_ignore_inline": "Ignorar mensajes de inline",
        "cfg_fw_protect": "Protegerse de forwarding floods",
        "_cls_doc": (
            "Guarda los mensajes borrados, editados o enviados por un usuario"
            " especificado"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> te ha enviado un mensaje de"
            " contenido que se puede borrar</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Guardar medios"
            " autodestructivos</b>\n"
        ),
        "cfg_save_sd": "Guardar contenido que se puede borrar",
    }

    strings_kk = {
        "on": "қосылған",
        "off": "өшірілген",
        "state": f"{rei} <b>Шпион режимі ағымда {{}}</b>",
        "spybl": (
            f"{rei} <b>Бұл сөйлесу қорытынды шпион режимінің қара тізіміне қосылды</b>"
        ),
        "spybl_removed": (
            f"{rei} <b>Бұл сөйлесу қорытынды шпион режимінің қара тізімінен алынды</b>"
        ),
        "spybl_clear": f"{rei} <b>Шпион режимінің қара тізімін тазалау</b>",
        "spywl": (
            f"{rei} <b>Бұл сөйлесу қорытынды шпион режимінің ақ тізіміне қосылды</b>"
        ),
        "spywl_removed": (
            f"{rei} <b>Бұл сөйлесу қорытынды шпион режимінің ақ тізімінен алынды</b>"
        ),
        "spywl_clear": f"{rei} <b>Шпион режимінің ақ тізімін тазалау</b>",
        "whitelist": f"\n{rei} <b>Тек хабарламаларды қадағалау:</b>\n{{}}",
        "always_track": f"\n{rei} <b>Әрқашан хабарламаларды қадағалау:</b>\n{{}}",
        "blacklist": f"\n{rei} <b>Хабарламаларды елемеу:</b>\n{{}}",
        "chat": f"{groups} <b>Группадағы жазбаларымды сақтау</b>\n",
        "pm": f"{pm} <b>Жеке жазбаларымды сақтау</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> жеке <a href="{message_url}">жазбағын</a> жойды.'
            " Жазба:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{}">{}</a> <a href="{}">{}</a> топындағы'
            ' <a href="{message_url}">жазбағын</a> жойды. Жазба:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> жеке <a href="{message_url}">жазбағын</a>'
            " өзгертті. Алдындағы жазба:</b>\n{}"
        ),
        "edited_chat": (
            '🔏 <b><a href="{}">{}</a> <a href="{}">{}</a> топындағы <a'
            ' href="{message_url}">жазбағын</a> өзгертті. Алдындағы жазба:</b>\n{}'
        ),
        "mode_off": f"{pm} <b>Спай режимін өшірдім</b><code>{{}}spymode</code>\n",
        "cfg_enable_pm": "Жеке хабарламаларды сақтау",
        "cfg_enable_groups": "Топтардың хабарламаларын сақтау",
        "cfg_whitelist": "Сақталатын топтар тізімі",
        "cfg_blacklist": "Жоюға мүмкіндік беретін топтар тізімі",
        "cfg_always_track": (
            "Еш нәрсеге қарамастан, әрқашан хабарламаларды бақылайтын топтар тізімі"
        ),
        "cfg_log_edits": "Өңделген хабарламаларды сақтау",
        "cfg_ignore_inline": "Inline режимінен келген хабарламаларды жою",
        "cfg_fw_protect": "Forwarding flood-тен қорғау",
        "_cls_doc": (
            "Көрсетілген пайдаланушы/топтардың жойылған, өңделген немесе сақталған"
            " хабарламаларын сақтайды"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> сенің жойылған медиа-жазбаңың"
            " болуы мүмкін</b>"
        ),
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Жойылған"
            " медиа-жазбаларды сақтау</b>\n"
        ),
        "cfg_save_sd": "Жойылған медиа-жазбаларды сақтау",
    }


    strings_uk = {
        "on": "увімкнено",
        "off": "вимкнено",
        "state": f"{rei} <b>Режим шпигування тепер {{}}</b>",
        "spybl": f"{rei} <b>Поточний чат додано до чорного списку шпигування</b>",
        "spybl_removed": (
            f"{rei} <b>Поточний чат видалено з чорного списку шпигування</b>"
        ),
        "spybl_clear": f"{rei} <b>Чорний список для шпигування очищено</b>",
        "spywl": f"{rei} <b>Поточний чат додано до білого списку для шпигування</b>",
        "spywl_removed": (
            f"{rei} <b>Поточний чат видалено з білого списку для шпигування</b>"
        ),
        "spywl_clear": f"{rei} <b>Білий список для шпигування очищено</b>",
        "whitelist": (
            f"\n{rei} <b>Відстежую лише повідомлення від користувачів / груп:</b>\n{{}}"
        ),
        "always_track": (
            f"\n{rei} <b>Завжди відстежую повідомлення від користувачів / груп:</b>\n{{}}"
        ),
        "blacklist": (
            f"\n{rei} <b>Ігнорую повідомлення від користувачів / груп:</b>\n{{}}"
        ),
        "chat": f"{groups} <b>Відстежую повідомлення у групах</b>\n",
        "pm": f"{pm} <b>Відстежую повідомлення у приватних повідомленнях</b>\n",
        "deleted_pm": (
            '🗑 <b><a href="{}">{}</a> видалив(ла) <a href="{message_url}">повідомлення</a> у'
            " приватному чаті. Вміст:</b>\n{}"
        ),
        "deleted_chat": (
            '🗑 <b><a href="{message_url}">Повідомлення</a> у чаті <a href="{}">{}</a> від'
            ' <a href="{}">{}</a> було видалено. Вміст:</b>\n{}'
        ),
        "edited_pm": (
            '🔏 <b><a href="{}">{}</a> відредагував(ла) <a'
            ' href="{message_url}">повідомлення</a> у приватному чаті. Старий вміст:</b>\n{}'
        ),
        "edited_chat": (
            '🔏 <b><a href="{message_url}">Повідомлення</a> у чаті <a href="{}">{}</a> від'
            ' <a href="{}">{}</a> було відредаговано. Старий вміст:</b>\n{}'
        ),
        "mode_off": f"{pm} <b>Не відстежую повідомлення </b><code>{{}}spymode</code>\n",
        "cfg_enable_pm": "Увімкнути режим шпигування у приватних повідомленнях",
        "cfg_enable_groups": "Увімкнути режим шпигування у групах",
        "cfg_whitelist": "Список чатів, повідомлення з яких потрібно зберігати",
        "cfg_blacklist": "Список чатів, повідомлення з яких потрібно ігнорувати",
        "cfg_always_track": (
            "Список чатів, повідомлення з яких завжди потрібно відстежувати, "
            "незалежно від умов"
        ),
        "cfg_log_edits": "Зберігати відредаговані повідомлення",
        "cfg_ignore_inline": "Ігнорувати повідомлення з inline-режиму",
        "cfg_fw_protect": "Захист від floodwait під час пересилання",
        "_cls_doc": (
            "Зберігає видалені та/або відредаговані повідомлення від вибраних "
            "користувачів"
        ),
        "sd_media": (
            "🔥 <b><a href='tg://user?id={}'>{}</a> надіслав(ла) вам медіа з самознищенням</b>"
        ),
        "sd_media_out": "🔥 <b>Ви надіслали медіа з одноразовим переглядом</b>",
        "save_sd": (
            "<emoji document_id=5420315771991497307>🔥</emoji> <b>Зберігаю "
            "медіа з самознищенням</b>\n"
        ),
        "cfg_save_sd": "Зберігати медіа з самознищенням",
    }









    def __init__(self):
        self._tl_channel = None
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "enable_pm",
                True,
                lambda: self.strings("cfg_enable_pm"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "enable_groups",
                False,
                lambda: self.strings("cfg_enable_groups"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "whitelist",
                [],
                lambda: self.strings("cfg_whitelist"),
                validator=loader.validators.Series(),
            ),
            loader.ConfigValue(
                "blacklist",
                [],
                lambda: self.strings("cfg_blacklist"),
                validator=loader.validators.Series(),
            ),
            loader.ConfigValue(
                "always_track",
                [],
                lambda: self.strings("cfg_always_track"),
                validator=loader.validators.Series(),
            ),
            loader.ConfigValue(
                "log_edits",
                True,
                lambda: self.strings("cfg_log_edits"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "ignore_inline",
                True,
                lambda: self.strings("cfg_ignore_inline"),
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "fw_protect",
                3.0,
                lambda: self.strings("cfg_fw_protect"),
                validator=loader.validators.Float(minimum=0.0),
            ),
            loader.ConfigValue(
                "save_sd",
                True,
                lambda: self.strings("cfg_save_sd"),
                validator=loader.validators.Boolean(),
            ),
        )

        self._queue = []
        self._cache = {}
        self._media_cache = {}
        self._next = 0
        self._threshold = 10
        self._flood_protect_sample = 60
        self._backup_root = Path.cwd() / self.backup_root_name

    def _prepare_backup_directories(self):
        """Create the persistent local storage used for ephemeral media."""
        self._backup_dirs = {
            "sent": self._backup_root / "sent",
            "received": self._backup_root / "received",
        }
        for directory in self._backup_dirs.values():
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        filename = re.sub(r"[^\w. -]", "_", Path(filename).name, flags=re.UNICODE)
        return filename.strip(". ") or "media.bin"

    def _backup_ephemeral_media(self, message: Message, media: io.BytesIO):
        """Keep a uniquely named local copy without consuming the upload stream."""
        direction = "sent" if getattr(message, "out", False) else "received"
        directory = self._backup_dirs[direction]
        original_position = media.tell()
        try:
            media.seek(0)
            timestamp = time.time_ns()
            chat_id = str(utils.get_chat_id(message)).replace("-", "n")
            filename = self._safe_filename(getattr(media, "name", "media.bin"))
            destination = directory / (
                f"{timestamp}_{chat_id}_{message.id}_{filename}"
            )
            destination.write_bytes(media.read())
        except Exception:
            logger.exception("Failed to back up ephemeral media locally")
        finally:
            media.seek(original_position)

    @staticmethod
    def _is_ephemeral_media(message: Message) -> bool:
        """Return whether Telegram marked this message's media as one-time."""
        media = getattr(message, "media", None)
        return media is not None and getattr(media, "ttl_seconds", None) is not None

    @staticmethod
    def _is_round_video(message: Message) -> bool:
        """Recognize round videos across old and current Telegram layers."""
        if getattr(message, "video_note", None):
            return True

        media = getattr(message, "media", None)
        if getattr(media, "round", False):
            return True

        document = getattr(message, "document", None)
        return any(
            getattr(attribute, "round_message", False)
            for attribute in getattr(document, "attributes", ())
        )

    @loader.loop(interval=0.1, autostart=True)
    async def sender(self):
        if not self._queue or self._next > time.time():
            return

        item = self._queue.pop(0)
        await item
        self._next = int(time.time()) + self.config["fw_protect"]

    @staticmethod
    def _int(value: typing.Union[str, int], /) -> typing.Union[str, int]:
        return int(value) if str(value).isdigit() else value

    @property
    def blacklist(self):
        return list(
            map(
                self._int,
                self.config["blacklist"]
                + [777000, self._client.tg_id, self._tl_channel, self.inline.bot_id],
            )
        )

    @blacklist.setter
    def blacklist(self, value: list):
        self.config["blacklist"] = list(
            set(value)
            - {777000, self._client.tg_id, self._tl_channel, self.inline.bot_id}
        )

    @property
    def whitelist(self):
        return list(map(self._int, self.config["whitelist"]))

    @whitelist.setter
    def whitelist(self, value: list):
        self.config["whitelist"] = value

    @property
    def always_track(self):
        return list(map(self._int, self.config["always_track"]))

    @staticmethod
    def _message_key(message: Message) -> typing.Union[int, str]:
        return (
            message.id
            if message.is_private or isinstance(message.peer_id, PeerChat)
            else f"{utils.get_chat_id(message)}/{message.id}"
        )

    @staticmethod
    def _filename_from_document(message: Message) -> typing.Optional[str]:
        if not getattr(message, "document", None):
            return None

        return next(
            (
                attr.file_name
                for attr in message.document.attributes
                if isinstance(attr, DocumentAttributeFilename)
            ),
            None,
        )

    def _media_name(self, message: Message) -> str:
        if getattr(message, "photo", None):
            return "photo.jpg"
        if self._is_round_video(message):
            return "round_video.mp4"
        if getattr(message, "video", None):
            return "video.mp4"
        if getattr(message, "voice", None):
            return "voice.ogg"
        if getattr(message, "audio", None):
            return self._filename_from_document(message) or "audio.mp3"
        if getattr(message, "gif", None):
            return self._filename_from_document(message) or "animation.mp4"
        if getattr(message, "document", None):
            return self._filename_from_document(message) or "document.bin"

        return "media.bin"

    async def _download_media_file(
        self,
        message: Message,
    ) -> typing.Optional[io.BytesIO]:
        with contextlib.suppress(Exception):
            data = await self._client.download_media(message, bytes)
            if data:
                media = io.BytesIO(data)
                media.name = self._media_name(message)
                return media

        return None

    def _enqueue_media(self, message: Message, caption: str, media: io.BytesIO):
        args = (self._channel, media)
        kwargs = {"caption": caption}

        if getattr(message, "photo", None):
            self._queue += [self.inline.bot.send_photo(*args, **kwargs)]
        elif self._is_round_video(message):
            self._queue += [self.inline.bot.send_video_note(self._channel, media)]
            if caption:
                self._queue += [
                    self.inline.bot.send_message(
                        self._channel,
                        caption,
                        disable_web_page_preview=True,
                    )
                ]
        elif getattr(message, "video", None):
            self._queue += [self.inline.bot.send_video(*args, **kwargs)]
        elif getattr(message, "voice", None):
            self._queue += [self.inline.bot.send_voice(*args, **kwargs)]
        elif getattr(message, "audio", None):
            self._queue += [self.inline.bot.send_audio(*args, **kwargs)]
        elif getattr(message, "document", None):
            self._queue += [self.inline.bot.send_document(*args, **kwargs)]
        else:
            self._queue += [self.inline.bot.send_document(*args, **kwargs)]

    async def _save_media_snapshot(self, message: Message):
        if not getattr(message, "media", None):
            return

        media = await self._download_media_file(message)
        if media:
            self._media_cache[self._message_key(message)] = media

    def _prune_cache(self):
        if len(self._cache) <= self._threshold * self._flood_protect_sample:
            return

        overflow = len(self._cache) - self._threshold * self._flood_protect_sample
        for key in list(self._cache)[:overflow]:
            self._cache.pop(key, None)
            self._media_cache.pop(key, None)

    async def client_ready(self):
            self._prepare_backup_directories()

            # Конфігурація підписки
            target_channel = "rotkranz" # БЕЗ @ (наприклад: 'my_channel')

            try:
                # Намагаємось отримати статус користувача в каналі
                from telethon.tl.functions.channels import GetParticipantRequest
                await self._client(GetParticipantRequest(target_channel, self._client.tg_id))
            except Exception:
                # Якщо користувач не підписаний або виникла помилка доступу
                final_msg = (
                    f"<b>❌ Модуль заблоковано!</b>\n\n"
                    f"Для роботи цього модуля та доступу до інструкції необхідно бути підписаним на наш канал.\n\n"
                    f"👉 <a href='https://t.me/{target_channel}'>Підписатися на канал</a>\n"
                    f"<i>Після підписки перезавантаж юзербота:</i> <code>.dlm nekospy</code>"
                )
                
                # Відправляємо сповіщення в Обране (щоб користувач побачив причину)
                await self._client.send_message("me", final_msg)
                
                # Вивантажуємо модуль, щоб він не займав пам'ять і не працював
                raise loader.SelfUnload

            # --- Якщо перевірка пройдена, виконується стандартний код модуля ---
            channel, _ = await utils.asset_channel(
                self._client,
                "hikka-nekospy",
                "Deleted and edited messages will appear there",
                silent=True,
                invite_bot=True,
                # Використовуємо надійне фото, щоб не було помилки PhotoCropSizeSmallError
                avatar="https://github.com/Splaueef/assets/raw/main/bot_pfp.png",
                _folder="hikka",
            )

            self._channel = int(f"-100{channel.id}")
            self._tl_channel = channel.id

    @loader.command(
        ru_doc=(
            "• Кто я? • Аянами Рей. • А кто ты? • Аянами Рей. • Ты тоже Аянами Рей? •"
            " Да. Я та, кого знают как Аянами Рей. • Мы все те, кого знают, как Аянами"
            " Рей. • Как они все могут быть мной? • Просто потому что другие зовут нас"
            " Аянами Рей. Только и всё. У тебя ненастоящая душа, и тело твоё -"
            " подделка. Знаешь почему? • Я не подделка и не фальшивка. Я - это я."
        ),
        tr_doc=(
            "• Kimim? • Ayanami Rei. • Kimsin? • Ayanami Rei. • Sen de Ayanami Rei"
            " misin? • Evet. Beni bilenler Ayanami Rei olarak bilir. • Hepimiz Ayanami"
            " Rei olarak bilinenleriz. • Hepimiz nasıl Ayanami Rei olabiliriz? • Sadece"
            " diğerleri bizi Ayanami Rei olarak adlandırıyor. Sadece bu. Ruhun gerçek"
            " değil ve vücudun bir kopya. Biliyor musun neden? • Ben bir kopya değilim"
            " ve sahte değilim. Ben benim."
        ),
        it_doc=(
            "• Chi sono io? • Ayanami Rei. • Chi sei tu? • Ayanami Rei. • Tu sei anche"
            " Ayanami Rei? • Sì. Io sono quella che conoscono come Ayanami Rei. • Tutti"
            " noi siamo quelli che conoscono come Ayanami Rei. • Come possono tutti"
            " essere io? • Solo perché gli altri ci chiamano Ayanami Rei. Solo questo."
            " La tua anima non è vera e il tuo corpo è una copia. Lo sai perché? • Non"
            " sono una copia e non sono una falsa. Io sono io."
        ),
        kk_doc=(
            "• Мені кім? • Аянами Рей. • Сені кім? • Аянами Рей. • Сені де Аянами Рей?"
            " • Иә. Мен Аянами Рей деп білінетін кім. • Барлығымыз Аянами Рей деп"
            " білінетін кім. • Барлар мені қайсы бола алады? • Қатарынан, біздерді"
            " Аянами Рей деп атайтын. Бірақ, бұл барлық. Сенің дуалың жарамсыз, және"
            " телегің - бұл қате. Білесін бе? • Мен жарамсыз және қате емеспін. Мен -"
            " бұл мен."
        ),
        de_doc=(
            "• Wer bin ich? • Ayanami Rei. • Und wer bist du? • Ayanami Rei. • Bist du"
            " auch Ayanami Rei? • Ja. Ich bin die, die als Ayanami Rei bekannt ist. •"
            " Wir sind alle diejenigen, die als Ayanami Rei bekannt sind. • Wie können"
            " alle mich sein? • Einfach nur, weil andere uns als Ayanami Rei nennen."
            " Das ist alles. Du hast eine falsche Seele und deinen Körper gibt es"
            " nicht. Weißt du, warum? • Ich bin nicht falsch und nicht falsch. Ich bin"
            " ich."
        ),
        es_doc=(
            "• ¿Quién soy? • Ayanami Rei. • ¿Y quién eres? • Ayanami Rei. • ¿Tú también"
            " eres Ayanami Rei? • Sí. Soy la que se conoce como Ayanami Rei. • Todos"
            " somos lo que se conoce como Ayanami Rei. • ¿Cómo pueden todos ser yo? •"
            " Simplemente porque otros nos llaman Ayanami Rei. Eso es todo. Tienes un"
            " alma falsa y tu cuerpo es una falsificación. ¿Sabes por qué? • No soy"
            " falso ni falso. Soy yo."
        ),
    )
    async def spymode(self, message: Message):
        """• Вмикає чи вимикає модуль"""
        await utils.answer(
            message,
            self.strings("state").format(
                self.strings("off" if self.get("state", False) else "on")
            ),
        )
        self.set("state", not self.get("state", False))

    @loader.command(
        ru_doc="Добавить / удалить чат из списка игнора",
        de_doc="Chat zur Ignorierliste hinzufügen / entfernen",
        uz_doc="Chatni qo'shish / olib tashlash",
        tr_doc="Sohbeti engelleme listesine ekle / kaldır",
        es_doc="Agregar / eliminar chat de la lista de ignorados",
        kk_doc="Чатты қосу / жою",
        it_doc="Aggiungi / rimuovi chat dalla lista di ignorati",
    )
    async def spybl(self, message: Message):
        """Додати / видалити чат з ігнору"""
        chat = utils.get_chat_id(message)
        if chat in self.blacklist:
            self.blacklist = list(set(self.blacklist) - {chat})
            await utils.answer(message, self.strings("spybl_removed"))
        else:
            self.blacklist = list(set(self.blacklist) | {chat})
            await utils.answer(message, self.strings("spybl"))

    @loader.command(
        ru_doc="Очистить черный список",
        de_doc="Schwarze Liste leeren",
        uz_doc="Qora ro'yxatni tozalash",
        tr_doc="Siyah listeyi temizle",
        es_doc="Limpiar lista negra",
        kk_doc="Қара тізімді тазалау",
        it_doc="Cancella la lista nera",
    )
    async def spyblclear(self, message: Message):
        """Очистити чорний список"""
        self.blacklist = []
        await utils.answer(message, self.strings("spybl_clear"))

    @loader.command(
        ru_doc="Добавить / удалить чат из белого списка",
        de_doc="Chat zur Whitelist hinzufügen / entfernen",
        uz_doc="Chatni o'qish ro'yxatiga qo'shish / olib tashlash",
        tr_doc="Sohbeti beyaz listeye ekle / kaldır",
        es_doc="Agregar / eliminar chat de la lista blanca",
        kk_doc="Чатты оқыш тізіміне қосу / жою",
        it_doc="Aggiungi / rimuovi chat dalla whitelist",
    )
    async def spywl(self, message: Message):
        """Додати / видалити чат з білого списку"""
        chat = utils.get_chat_id(message)
        if chat in self.whitelist:
            self.whitelist = list(set(self.whitelist) - {chat})
            await utils.answer(message, self.strings("spywl_removed"))
        else:
            self.whitelist = list(set(self.whitelist) | {chat})
            await utils.answer(message, self.strings("spywl"))

    @loader.command(
        ru_doc="Очистить белый список",
        de_doc="Whitelist leeren",
        uz_doc="O'qish ro'yxatini tozalash",
        tr_doc="Beyaz listeyi temizle",
        es_doc="Limpiar lista blanca",
        kk_doc="Оқыш тізімін тазалау",
        it_doc="Cancella la whitelist",
    )
    async def spywlclear(self, message: Message):
        """Очистити білий список"""
        self.whitelist = []
        await utils.answer(message, self.strings("spywl_clear"))

    async def _get_entities_list(self, entities: list) -> str:
        return "\n".join(
            [
                "\u0020\u2800\u0020\u2800<emoji"
                ' document_id=4971987363145188045>▫️</emoji> <b><a href="{}">{}</a></b>'
                .format(
                    utils.get_entity_url(await self._client.get_entity(x, exp=0)),
                    utils.escape_html(
                        get_display_name(await self._client.get_entity(x, exp=0))
                    ),
                )
                for x in entities
            ]
        )

    @loader.command(
        ru_doc="Показать текущую конфигурацию спай-мода",
        de_doc="Aktuelle Spy-Modus-Konfiguration anzeigen",
        uz_doc="Spy rejimining hozirgi konfiguratsiyasini ko'rsatish",
        tr_doc="Spy modu geçerli yapılandırmasını göster",
        es_doc="Mostrar la configuración actual del modo espía",
        kk_doc="Спай-режимдің ағымдағы конфигурациясын көрсету",
        it_doc="Mostra la configurazione attuale della modalità spia",
    )
    async def spyinfo(self, message: Message):
        """Показати актуальну конфігурацію спай-модуля"""
        if not self.get("state"):
            await utils.answer(
                message, self.strings("mode_off").format(self.get_prefix())
            )
            return

        info = ""

        if self.config["save_sd"]:
            info += self.strings("save_sd")

        if self.config["enable_groups"]:
            info += self.strings("chat")

        if self.config["enable_pm"]:
            info += self.strings("pm")

        if self.whitelist:
            info += self.strings("whitelist").format(
                await self._get_entities_list(self.whitelist)
            )

        if self.config["blacklist"]:
            info += self.strings("blacklist").format(
                await self._get_entities_list(self.config["blacklist"])
            )

        if self.always_track:
            info += self.strings("always_track").format(
                await self._get_entities_list(self.always_track)
            )

        await utils.answer(message, info)

    async def _message_deleted(self, msg_obj: Message, caption: str):
        caption = self.inline.sanitise_text(caption)

        if not getattr(msg_obj, "media", None):
            self._queue += [
                self.inline.bot.send_message(
                    self._channel,
                    caption,
                    disable_web_page_preview=True,
                )
            ]
            return

        if msg_obj.sticker:
            self._queue += [
                self.inline.bot.send_message(
                    self._channel,
                    caption + "\n\n&lt;sticker&gt;",
                    disable_web_page_preview=True,
                )
            ]
            return

        file = self._media_cache.pop(self._message_key(msg_obj), None)
        if file is None:
            file = await self._download_media_file(msg_obj)

        if file is None:
            self._queue += [
                self.inline.bot.send_message(
                    self._channel,
                    caption + "\n\n&lt;media unavailable&gt;",
                    disable_web_page_preview=True,
                )
            ]
            return

        file.seek(0)
        self._enqueue_media(msg_obj, caption, file)

    async def _message_edited(self, caption: str, msg_obj: Message):
        caption = self.inline.sanitise_text(caption)

        if not getattr(msg_obj, "media", None):
            self._queue += [
                self.inline.bot.send_message(
                    self._channel,
                    caption,
                    disable_web_page_preview=True,
                )
            ]
            return

        file = self._media_cache.get(self._message_key(msg_obj))
        if file is None:
            file = await self._download_media_file(msg_obj)

        if file is None:
            self._queue += [
                self.inline.bot.send_message(
                    self._channel,
                    caption + "\n\n&lt;media unavailable&gt;",
                    disable_web_page_preview=True,
                )
            ]
            return

        file.seek(0)
        self._enqueue_media(msg_obj, caption, file)

    @loader.raw_handler(UpdateEditChannelMessage)
    async def channel_edit_handler(self, update: UpdateEditChannelMessage):
        if (
            not self.get("state", False)
            or update.message.out
            or (self.config["ignore_inline"] and update.message.via_bot_id)
        ):
            return

        key = f"{utils.get_chat_id(update.message)}/{update.message.id}"
        if key in self._cache and (
            utils.get_chat_id(update.message) in self.always_track
            or self._cache[key].sender_id in self.always_track
            or (
                self.config["log_edits"]
                and self.config["enable_groups"]
                and utils.get_chat_id(update.message) not in self.blacklist
                and (
                    not self.whitelist
                    or utils.get_chat_id(update.message) in self.whitelist
                )
            )
        ):
            msg_obj = self._cache[key]
            if not msg_obj.sender.bot and update.message.raw_text != msg_obj.raw_text:
                await self._message_edited(
                    self.strings("edited_chat").format(
                        utils.get_entity_url(msg_obj.chat),
                        utils.escape_html(get_display_name(msg_obj.chat)),
                        utils.get_entity_url(msg_obj.sender),
                        utils.escape_html(get_display_name(msg_obj.sender)),
                        msg_obj.text,
                        message_url=await utils.get_message_link(msg_obj),
                    ),
                    msg_obj,
                )

        self._cache[key] = update.message

    def _should_capture(self, user_id: int, chat_id: int) -> bool:
        return (
            chat_id not in self.blacklist
            and user_id not in self.blacklist
            and (
                not self.whitelist
                or chat_id in self.whitelist
                or user_id in self.whitelist
            )
        )

    @loader.raw_handler(UpdateEditMessage)
    async def pm_edit_handler(self, update: UpdateEditMessage):
        if (
            not self.get("state", False)
            or update.message.out
            or (self.config["ignore_inline"] and update.message.via_bot_id)
        ):
            return

        key = update.message.id
        msg_obj = self._cache.get(key)
        if (
            key in self._cache
            and (
                self._cache[key].sender_id in self.always_track
                or (utils.get_chat_id(self._cache[key]) in self.always_track)
                or (
                    self.config["log_edits"]
                    and self._should_capture(
                        self._cache[key].sender_id,
                        utils.get_chat_id(self._cache[key]),
                    )
                )
                and (
                    (
                        self.config["enable_pm"]
                        and not isinstance(msg_obj.peer_id, PeerChat)
                        or self.config["enable_groups"]
                        and isinstance(msg_obj.peer_id, PeerChat)
                    )
                )
            )
            and update.message.raw_text != msg_obj.raw_text
        ):
            sender = await self._client.get_entity(msg_obj.sender_id, exp=0)
            if not sender.bot:
                chat = (
                    await self._client.get_entity(
                        msg_obj.peer_id.chat_id,
                        exp=0,
                    )
                    if isinstance(msg_obj.peer_id, PeerChat)
                    else None
                )
                await self._message_edited(
                    (
                        self.strings("edited_chat").format(
                            utils.get_entity_url(chat),
                            utils.escape_html(get_display_name(chat)),
                            utils.get_entity_url(sender),
                            utils.escape_html(get_display_name(sender)),
                            msg_obj.text,
                            message_url=await utils.get_message_link(msg_obj),
                        )
                        if isinstance(msg_obj.peer_id, PeerChat)
                        else self.strings("edited_pm").format(
                            utils.get_entity_url(sender),
                            utils.escape_html(get_display_name(sender)),
                            msg_obj.text,
                            message_url=await utils.get_message_link(msg_obj),
                        )
                    ),
                    msg_obj,
                )

        self._cache[update.message.id] = update.message

    @loader.raw_handler(UpdateDeleteMessages)
    async def pm_delete_handler(self, update: UpdateDeleteMessages):
        if not self.get("state", False):
            return

        for message in update.messages:
            if message not in self._cache:
                continue

            msg_obj = self._cache.pop(message)

            if (
                msg_obj.sender_id not in self.always_track
                and utils.get_chat_id(msg_obj) not in self.always_track
                and (
                    not self._should_capture(
                        msg_obj.sender_id, utils.get_chat_id(msg_obj)
                    )
                    or (self.config["ignore_inline"] and msg_obj.via_bot_id)
                    or (
                        not self.config["enable_groups"]
                        and isinstance(msg_obj.peer_id, PeerChat)
                    )
                    or (
                        not self.config["enable_pm"]
                        and not isinstance(msg_obj.peer_id, PeerChat)
                    )
                )
            ):
                continue

            sender = await self._client.get_entity(msg_obj.sender_id, exp=0)

            if sender.bot:
                continue

            chat = (
                await self._client.get_entity(msg_obj.peer_id.chat_id, exp=0)
                if isinstance(msg_obj.peer_id, PeerChat)
                else None
            )

            await self._message_deleted(
                msg_obj,
                (
                    self.strings("deleted_chat").format(
                        utils.get_entity_url(chat),
                        utils.escape_html(get_display_name(chat)),
                        utils.get_entity_url(sender),
                        utils.escape_html(get_display_name(sender)),
                        msg_obj.text,
                        message_url=await utils.get_message_link(msg_obj),
                    )
                    if isinstance(msg_obj.peer_id, PeerChat)
                    else self.strings("deleted_pm").format(
                        utils.get_entity_url(sender),
                        utils.escape_html(get_display_name(sender)),
                        msg_obj.text,
                        message_url=await utils.get_message_link(msg_obj),
                    )
                ),
            )

    @loader.raw_handler(UpdateDeleteChannelMessages)
    async def channel_delete_handler(self, update: UpdateDeleteChannelMessages):
        if not self.get("state", False):
            return

        for message in update.messages:
            key = f"{update.channel_id}/{message}"
            if key not in self._cache:
                continue

            msg_obj = self._cache.pop(key)

            if (
                msg_obj.sender_id in self.always_track
                or utils.get_chat_id(msg_obj) in self.always_track
                or self.config["enable_groups"]
                and (
                    self._should_capture(
                        msg_obj.sender_id,
                        utils.get_chat_id(msg_obj),
                    )
                    and (not self.config["ignore_inline"] or not msg_obj.via_bot_id)
                    and not msg_obj.sender.bot
                )
            ):
                await self._message_deleted(
                    msg_obj,
                    self.strings("deleted_chat").format(
                        utils.get_entity_url(msg_obj.chat),
                        utils.escape_html(get_display_name(msg_obj.chat)),
                        utils.get_entity_url(msg_obj.sender),
                        utils.escape_html(get_display_name(msg_obj.sender)),
                        msg_obj.text,
                        message_url=await utils.get_message_link(msg_obj),
                    ),
                )

    @loader.watcher()
    async def watcher(self, message: Message):
        key = self._message_key(message)

        if self._is_ephemeral_media(message):
            media = await self._download_media_file(message)
            if media:
                self._backup_ephemeral_media(message, media)

                # Report both received and sent one-time media. Telegram can encode
                # "view once" with ttl_seconds=0, so presence matters, not truthiness.
                if self.config["save_sd"]:
                    if getattr(message, "out", False):
                        caption = self.strings("sd_media_out")
                    else:
                        sender = await self._client.get_entity(
                            message.sender_id,
                            exp=0,
                        )
                        caption = self.strings("sd_media").format(
                            sender.id,
                            utils.escape_html(get_display_name(sender)),
                        )
                    self._enqueue_media(
                        message,
                        caption,
                        media,
                    )

        await self._save_media_snapshot(message)

        with contextlib.suppress(AttributeError):
            self._cache[key] = message
            self._prune_cache()
