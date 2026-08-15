# meta developer: @Huai_Baike
# meta syntax: .wwhelp | .я | .ти | .чат | .топ | .зв | .профіль | .нік | .переказ | .пет | .друзі | .графік

__version__ = (9, 0, 0)

import asyncio
import html
import io
import json as _json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
from .. import loader, utils

logger = logging.getLogger(__name__)

_DOMAIN = "https://werwolf.pp.ua"


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _esc(v) -> str:
    """Escape untrusted API/Telegram values for Telegram's HTML parser."""
    return html.escape(str(v if v is not None else ""), quote=True)


def _href(url) -> str:
    """Allow only links Telegram can safely render."""
    value = str(url or "").strip()
    if not re.match(r"^https?://", value, re.IGNORECASE):
        return ""
    return _esc(value)

def _bar(value: int, max_val: int, width: int = 8) -> str:
    width = max(1, _n(width, 8))
    if max_val <= 0: return "░" * width
    f = max(0, min(width, round(value / max_val * width)))
    return "█" * f + "░" * (width - f)

def _n(v, d=0) -> int:
    try:
        value = float(v if v is not None and v != "" else d)
        return int(value) if math.isfinite(value) else d
    except (TypeError, ValueError, OverflowError): return d

def _f(v, d=0.0) -> float:
    try:
        value = float(v if v is not None and v != "" else d)
        return value if math.isfinite(value) else d
    except (TypeError, ValueError, OverflowError): return d

def _vip(level: int) -> str:
    return ["", "⭐", "🌟", "💎", "👑"][max(0, min(_n(level), 4))]

def _sep() -> str: return "—" * 16

def _ts(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%d.%m.%y %H:%M")
    except Exception:
        return "?"

def _cur_icon(currency: str) -> str:
    value = str(currency).lower()
    return {"coins": "⭐", "gold": "🥇"}.get(value, _esc(value))


_SECRET_FIELDS = {"token", "api_key", "initdata", "init_data", "password", "secret"}


def _redact(value):
    """Return a JSON-safe copy with credentials removed from debug output."""
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in _SECRET_FIELDS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _json_object(raw: str) -> dict:
    """Parse a command argument as an object, never as an arbitrary JSON value."""
    try:
        value = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise ValueError(f"невалідний JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON body має бути об'єктом")
    return value


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_user(data: dict) -> str:
    u      = data.get("user", {})
    stats  = data.get("stats", {})
    bdown  = stats.get("message_breakdown", {})
    chats  = data.get("top_chats") or data.get("chats", [])
    ov     = data.get("owner_view", {})        # доступно тільки власнику ключа
    gstats = ov.get("global_stats", {})
    su     = ov.get("storage_user", {})        # повні дані з БД
    mar    = ov.get("marriage", {})

    # Беремо дані з storage_user якщо є (точніші), інакше з user
    uid      = u.get("id") or su.get("id") or data.get("_tg_id", "?")
    name = _esc(
        u.get("display_name")
        or u.get("name")
        or su.get("name")
        or data.get("_tg_name")
        or "?"
    )
    username = u.get("username") or su.get("username")
    nickname = _esc(u.get("nickname") or su.get("nickname") or "")
    vip      = _n(u.get("vip_level") or su.get("vip_level"))
    short_id = _esc(su.get("short_id") or "")

    first_seen = stats.get("first_seen") or gstats.get("first_seen", "")

    total   = _n(bdown.get("total")  or stats.get("total_messages") or gstats.get("total_messages"))
    today   = _n(bdown.get("today"))
    week    = _n(bdown.get("week"))
    month   = _n(bdown.get("month"))
    n_chats = _n(stats.get("chats_count"))

    stars  = _f(u.get("stars") or su.get("stars"))
    bank   = _f(u.get("bank")  or su.get("bank"))
    gold   = _f(u.get("gold")  or su.get("gold"))
    wins   = _n(u.get("wins")  or su.get("wins"))
    losses = _n(u.get("losses") or su.get("losses"))

    # Заголовок
    head = f"{_vip(vip) if vip else ''}👤 <b>{name}</b>"
    if username: head += f"  <code>@{_esc(username)}</code>"

    L = [head]
    if nickname:   L.append(f"    ╰ <i>{nickname}</i>")
    L.append(f"    ╰ <code>id: {uid}</code>")
    if short_id:   L.append(f"    ╰ short_id: <code>{short_id}</code>")
    if first_seen: L.append(f"    ╰ з <b>{_esc(first_seen)}</b>")

    # Активність
    L += ["", "<b>📊 Активність</b>", _sep()]
    L.append(f"Всього:    <b>{total:,}</b>  · Чатів: <b>{n_chats}</b>")
    L.append(f"Сьогодні:  <b>{today:,}</b>")
    L.append(f"Тиждень:   <b>{week:,}</b>  · Місяць: <b>{month:,}</b>")

    # Гра/баланс
    if any([stars, bank, gold, wins, losses]):
        L += ["", "<b>🎮 Гра</b>", _sep()]
        L.append(f"⭐ {stars:.0f}  🏦 {bank:.1f}  🥇 {gold:.1f}")
        if wins or losses:
            tg = wins + losses
            wr = round(wins / tg * 100) if tg else 0
            L.append(f"🏆 {wins}W / {losses}L  · WR <b>{wr}%</b>")

    # Рівень (тільки з owner_view)
    if su:
        wlvl   = _n(su.get("work_level"))
        wlp    = _n(su.get("work_xp"))
        shield = _n(su.get("shield"))
        docs   = _n(su.get("docs"))
        if any([wlvl, shield, docs]):
            L += ["", "<b>🎒 Інвентар</b>", _sep()]
            parts = []
            if shield: parts.append(f"🛡 {shield}")
            if docs:   parts.append(f"📄 {docs}")
            if wlvl:   parts.append(f"💼 Робота lvl{wlvl}")
            L.append("  ".join(parts))

    # Шлюб (з owner_view)
    if mar and mar.get("partner_id"):
        partner_id = mar.get("partner_id")
        hp         = _n(mar.get("hp"))
        L += ["", "<b>💍 Шлюб</b>", _sep()]
        L.append(f"Партнер: <code>{partner_id}</code>  HP: <b>{hp:,}</b>")

    # Топ чати
    if chats:
        max_m = max((_n(c.get("messages")) for c in chats), default=1)
        L += ["", "<b>🏆 Топ чати</b>", _sep()]
        for c in chats[:5]:
            raw_title = c.get("title", "")
            link      = _href(c.get("link"))
            cm        = _n(c.get("messages"))
            if not link or raw_title.startswith("Chat -"):
                t_str = f"🔒 <i>приватний</i>"
            else:
                t_str = f'<a href="{link}">{_esc(raw_title)}</a>'
            L.append(f"{_bar(cm, max_m)}  <b>{cm:,}</b>  {t_str}")

    return "\n".join(L)


def _fmt_profile(data: dict) -> str:
    bal   = data.get("balance", {})
    stats = data.get("stats", {})
    lvl   = data.get("level", {})
    prem  = data.get("premium", {})
    inv   = data.get("inventory", {})

    total_msgs   = _n(stats.get("total_messages"))
    active_chats = _n(stats.get("active_chats"))
    avg_day      = _f(stats.get("avg_per_day"))
    top_chat     = _esc(stats.get("top_chat") or "")

    coins = _f(bal.get("coins"))
    bank  = _f(bal.get("bank"))
    gold  = _f(bal.get("gold"))

    level      = _n(lvl.get("level"))
    work_lvl   = _n(lvl.get("work_level"))
    total_lp   = _f(lvl.get("total_lp"))
    act_lp     = _f(lvl.get("activity_lp"))
    rel_lp     = _f(lvl.get("relationship_lp"))
    work_lp    = _f(lvl.get("work_lp"))
    rel_limit  = _n(lvl.get("relationship_limit"))
    rel_used   = len(lvl.get("relationship_levels_used", []))

    prem_active = prem.get("is_active", False)
    prem_level  = _n(prem.get("level"))
    prem_until  = prem.get("until", "")

    defense = _n(inv.get("defense"))
    docs    = _n(inv.get("docs"))
    roles   = _n(inv.get("active_roles_count"))

    vip_badge = _vip(prem_level) if prem_active and prem_level else ""
    L = [f"{vip_badge}<b>Мій профіль</b>"]

    # Баланс
    L += ["", "<b>💰 Баланс</b>", _sep()]
    L.append(f"⭐ Монети:  <b>{coins:.0f}</b>")
    L.append(f"🏦 Банк:    <b>{bank:.1f}</b>")
    L.append(f"🥇 Золото:  <b>{gold:.1f}</b>")

    # Статистика
    L += ["", "<b>📊 Статистика</b>", _sep()]
    L.append(f"Повідомлень:    <b>{total_msgs:,}</b>")
    L.append(f"Активних чатів: <b>{active_chats}</b>")
    if avg_day:
        L.append(f"Середньо/день:  <b>{avg_day:.0f}</b>")
    if top_chat:
        L.append(f"Топ чат: {top_chat}")

    # Рівень
    L += ["", "<b>📈 Рівень {}</b>".format(level), _sep()]
    L.append(f"LP всього:    <b>{total_lp:.0f}</b>")
    L.append(f"Активність:   <b>{act_lp:.0f}</b>")
    L.append(f"Стосунки:     <b>{rel_lp:.0f}</b>  ({rel_used}/{rel_limit} рівнів)")
    L.append(f"Робота lvl{work_lvl}: <b>{work_lp:.0f} LP</b>")

    # Інвентар
    if any([defense, docs, roles]):
        L += ["", "<b>🎒 Інвентар</b>", _sep()]
        parts = []
        if defense: parts.append(f"🛡 Захист: {defense}")
        if docs:    parts.append(f"📄 Доки: {docs}")
        if roles:   parts.append(f"🎭 Ролі: {roles}")
        L.append("  ·  ".join(parts))

    # Преміум
    if prem_active:
        until_str = ""
        if prem_until and "2100" not in prem_until:
            try:
                dt = datetime.fromisoformat(prem_until.replace("Z", "+00:00"))
                until_str = f" до {dt.strftime('%d.%m.%Y')}"
            except Exception:
                pass
        L += ["", f"{'👑' * min(prem_level,3)} <b>Преміум {prem_level} рівень</b>{until_str}"]

    return "\n".join(L)


def _fmt_pets(data: dict) -> str:
    pets = data.get("pets", [])
    if not pets:
        return "🐾 Немає петів."

    L = ["<b>🐾 Мої пети</b>", _sep()]
    for pet in pets:
        name     = _esc(pet.get("name") or "?")
        stage    = _esc(pet.get("stage_label") or pet.get("stage") or "?")
        status   = _esc(pet.get("status_label") or pet.get("status") or "?")
        level    = _n(pet.get("level"))
        health   = _f(pet.get("health"))
        mood     = _f(pet.get("mood"))
        hunger   = _f(pet.get("hunger"))
        energy   = _f(pet.get("energy"))
        is_pri   = pet.get("is_primary", False)
        owners   = pet.get("owners_preview", [])

        L.append("")
        pri_mark = " ★" if is_pri else ""
        L.append(f"🐣 <b>{name}</b>{pri_mark}")
        L.append(f"    ╰ {stage}  ·  {status}  ·  lvl {level}")

        # Показники тільки якщо не нулі
        bars = []
        if health:  bars.append(f"❤️ {health:.0f}")
        if mood:    bars.append(f"😊 {mood:.0f}")
        if hunger:  bars.append(f"🍖 {hunger:.0f}")
        if energy:  bars.append(f"⚡ {energy:.0f}")
        if bars: L.append(f"    ╰ " + "  ".join(bars))

        # Опікуни
        if len(owners) > 1:
            names = [_esc(o.get("name") or o.get("username") or "?") for o in owners[:3]]
            L.append(f"    ╰ 👥 {', '.join(names)}")

        # Кулдауни
        cds = pet.get("cooldowns", {})
        ready = [k for k, v in cds.items() if v == 0]
        if ready:
            L.append(f"    ╰ ✅ Готово: {', '.join(ready)}")

    return "\n".join(L)


def _fmt_friends(data: dict) -> str:
    friends  = data.get("friends", [])
    incoming = data.get("incoming", [])
    outgoing = data.get("outgoing", [])

    L = ["<b>👥 Друзі</b>", _sep()]

    if friends:
        for f in friends:
            name = _esc(f.get("name") or f.get("username") or "?")
            sid  = _esc(f.get("short_id") or "")
            uname= f.get("username", "")
            line = f"· <b>{name}</b>"
            if uname: line += f"  <code>@{_esc(uname)}</code>"
            if sid:   line += f"  <code>{sid}</code>"
            L.append(line)
    else:
        L.append("Список порожній.")

    if incoming:
        L += ["", f"📩 <b>Вхідні заявки ({len(incoming)})</b>"]
        for f in incoming[:5]:
            L.append(f"· {_esc(f.get('name') or '?')}")

    if outgoing:
        L += ["", f"📤 <b>Вихідні заявки ({len(outgoing)})</b>"]
        for f in outgoing[:5]:
            L.append(f"· {_esc(f.get('name') or '?')}")

    return "\n".join(L)


def _fmt_transfer_resp(data: dict, currency: str) -> str:
    bal = data.get("balance", {})
    rec = data.get("recipient", {})
    sent= _n(data.get("transferred") or data.get("amount"))
    fee = _n(data.get("fee", 0))
    ci  = _cur_icon(currency)

    rname = _esc(rec.get("name") or rec.get("display_name") or rec.get("short_id") or "?")
    rsid  = _esc(rec.get("short_id") or "")

    coins = _f(bal.get("coins")); bk = _f(bal.get("bank")); gd = _f(bal.get("gold"))

    L = ["✅ <b>Переказ виконано</b>", _sep()]
    L.append(f"Отримувач:  <b>{rname}</b>" + (f"  <code>{rsid}</code>" if rsid else ""))
    L.append(f"Переказано: {ci} <b>{sent}</b>" + (f"  (комісія: {fee})" if fee else ""))
    L += ["", "<b>💰 Новий баланс</b>", _sep()]
    L.append(f"⭐ {coins:.0f}  🏦 {bk:.1f}  🥇 {gd:.1f}")
    return "\n".join(L)


def _fmt_group(data: dict) -> str:
    title   = _esc(data.get("title") or "Без назви")
    chat_id = data.get("chat_id", "?")
    link    = _href(data.get("link"))
    members = _n(data.get("members"))
    lang    = _esc(data.get("language", "") or "")
    owner   = data.get("owner", {})
    stats   = data.get("stats", {})
    sett    = data.get("settings", {})

    summary = stats.get("summary", {})
    s_day   = summary.get("day",   {})
    s_month = summary.get("month", {})
    s_all   = summary.get("all",   {})
    top     = stats.get("top_users",      [])
    daily   = stats.get("daily_messages", [])
    total   = _n(stats.get("total_messages") or s_all.get("messages"))
    uniq    = _n(stats.get("unique_users")   or s_all.get("unique_users"))

    t_linked = f'<a href="{link}">{title}</a>' if link else title

    L = [f"👥 <b>{t_linked}</b>"]
    L.append(f"    ╰ <code>{chat_id}</code>{'  🌐 ' + lang if lang else ''}")
    L.append(f"    ╰ 👤 {members:,} учасників")
    if owner and owner.get("id"):
        oname = _esc(owner.get("display_name") or str(owner.get("id", "?")))
        L.append(f"    ╰ 👑 {oname}")

    L += ["", "<b>📊 Повідомлення</b>", _sep()]
    if s_day:
        dm, du = _n(s_day.get("messages")), _n(s_day.get("unique_users"))
        L.append(f"Сьогодні:  <b>{dm:,}</b>  ({du} юзерів)")
    if s_month:
        mm, mu = _n(s_month.get("messages")), _n(s_month.get("unique_users"))
        L.append(f"Місяць:    <b>{mm:,}</b>  ({mu} юзерів)")
    L.append(f"Всього:    <b>{total:,}</b>  ({uniq} унікальних)")

    if top:
        max_m = max((_n(u.get("messages")) for u in top), default=1)
        L += ["", "<b>🏆 Топ сьогодні</b>", _sep()]
        for i, u in enumerate(top[:5], 1):
            # top_users має name/username/user_id, не display_name
            uname = _esc(u.get("name") or u.get("display_name") or u.get("username") or str(u.get("user_id","?")))
            um    = _n(u.get("messages"))
            L.append(f"{i}. {_bar(um, max_m)}  <b>{um}</b>  {uname}")

    if daily:
        # Фільтруємо нулі і показуємо останні 7 днів з даними
        active = [d for d in daily if _n(d.get("count")) > 0][-7:]
        if active:
            max_d = max(_n(d.get("count")) for d in active)
            L += ["", "<b>📈 Останні 7 днів</b>", _sep()]
            for d in active:
                day_str = _esc(str(d.get("day", ""))[-5:])
                cnt     = _n(d.get("count"))
                L.append(f"<code>{day_str}</code>  {_bar(cnt, max_d)}  <b>{cnt:,}</b>")

    flags = []
    if sett.get("greeting_enabled"):     flags.append("👋 Привітання")
    if sett.get("enable_group_ai"):      flags.append("🤖 AI")
    if sett.get("enable_ai_moderation"): flags.append("🛡 Модерація")
    if flags:
        L += ["", "<b>⚙️</b>  " + "  ".join(flags)]

    return "\n".join(L)


def _fmt_rel_chat(data: dict, chat_id) -> str:
    entries = (data if isinstance(data, list)
               else data.get("relationships") or data.get("items") or data.get("leaderboard") or [])
    L = [f"<b>🔗 Зв'язки чату</b>  <code>{chat_id}</code>", _sep()]
    if not entries:
        L.append("Даних немає.")
        return "\n".join(L)
    max_s = max((_n(e.get("score") or e.get("messages") or e.get("count")) for e in entries), default=1)
    for i, e in enumerate(entries[:10], 1):
        n1    = _esc(e.get("user1_name") or e.get("name") or e.get("display_name") or "?")
        n2    = _esc(e.get("user2_name") or "")
        score = _n(e.get("score") or e.get("messages") or e.get("count"))
        pair  = f"{n1} ↔ {n2}" if n2 else n1
        L.append(f"{i:>2}. {_bar(score, max_s, 6)}  <b>{score}</b>  {pair}")
    return "\n".join(L)


def _fmt_rel_user(data: dict, label: str) -> str:
    entries = (data if isinstance(data, list)
               else data.get("relationships") or data.get("items") or [])
    mar = data.get("marriage") if isinstance(data, dict) else None
    L   = [f"<b>🔗 Зв'язки</b>  {_esc(label)}", _sep()]

    if mar and mar.get("partner_id"):
        L.append(f"💍 Одружений(-а)  HP: <b>{_n(mar.get('hp')):,}</b>")
        L.append("")

    if not entries:
        L.append("Зв'язків немає.")
        return "\n".join(L)
    max_s = max((_n(e.get("score") or e.get("messages") or e.get("count")) for e in entries), default=1)
    for i, e in enumerate(entries[:10], 1):
        name  = _esc(e.get("display_name") or e.get("name") or e.get("username") or "?")
        score = _n(e.get("score") or e.get("messages") or e.get("count"))
        L.append(f"{i:>2}. {_bar(score, max_s, 6)}  <b>{score}</b>  {name}")
    return "\n".join(L)


# ── Chart generator ───────────────────────────────────────────────────────────

async def _make_chart(daily: list, title: str) -> bytes | None:
    valid = []
    for item in daily or []:
        try:
            valid.append(
                (datetime.strptime(str(item.get("day")), "%Y-%m-%d"), _n(item.get("count")))
            )
        except (TypeError, ValueError, AttributeError):
            continue
    if not valid:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.ticker import MaxNLocator

        dates, counts = map(list, zip(*valid))

        fig, ax = plt.subplots(figsize=(10, 3.5), facecolor="#111111")
        ax.set_facecolor("#111111")
        ax.fill_between(dates, counts, alpha=0.25, color="#E8593C")
        ax.plot(dates, counts, color="#E8593C", linewidth=1.8, zorder=3)

        max_val = max(counts)
        max_idx = counts.index(max_val)
        ax.plot(dates[max_idx], max_val, "o", color="#F2A623", markersize=6, zorder=4)
        ax.annotate(f" {max_val:,}", (dates[max_idx], max_val),
                    color="#F2A623", fontsize=8, va="bottom", ha="left")

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7, color="#888")
        plt.setp(ax.yaxis.get_majorticklabels(), fontsize=7, color="#888")
        ax.spines[:].set_visible(False)
        ax.tick_params(colors="#555", length=0)
        ax.grid(axis="y", color="#333", linewidth=0.5, linestyle="--")
        ax.set_ylim(bottom=0)
        if dates:
            date_range = f"{dates[0].strftime('%d.%m.%Y')} — {dates[-1].strftime('%d.%m.%Y')}"
            ax.set_title(f"{title}\n{date_range}", color="#ccc", fontsize=9, pad=8)
        ax.set_xlabel("Дні →", color="#666", fontsize=7, labelpad=4)
        ax.set_ylabel("Повідомлення →", color="#666", fontsize=7, labelpad=4)

        plt.tight_layout(pad=1.2)
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, facecolor="#111111")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Werwolf chart: %s", e)
        return None


# ── Module ────────────────────────────────────────────────────────────────────

@loader.tds
class WerwolfStatsMod(loader.Module):
    """📊 Статистика та керування акаунтом RotKranz Werwolf"""

    strings = {
        "name":    "WerwolfStats",
        "loading": "<code>⏳ Завантажую...</code>",
        "no_user": "❌ Відповідай на повідомлення або вкажи <code>.ти @username</code>",
        "no_chat": "❌ Тільки в групах.",
        "no_key":  "❌ API-ключ не вказано.\nОтримай через <code>/api</code> у боті → <code>.wwkey YOUR_KEY</code>",
        "err":     "❌ <b>Помилка:</b> <code>{e}</code>",
        "help":    (
            "<b>🐺 Werwolf API · команди</b>\n\n"
            "<b>Статистика</b>\n"
            "<code>.я</code> · <code>.ти</code> · <code>.чат</code> · "
            "<code>.топ</code> · <code>.зв</code> · <code>.графік</code>\n\n"
            "<b>Акаунт</b>\n"
            "<code>.профіль</code> · <code>.пет</code> · <code>.друзі</code> · "
            "<code>.нік</code> · <code>.переказ</code>\n\n"
            "<b>Нові API</b>\n"
            "<code>.курс</code> · <code>.чати</code> · <code>.рейтинг</code> · "
            "<code>.чатнік</code> · <code>.петдія</code> · <code>.друг</code>\n\n"
            "<b>Сервіс</b>\n"
            "<code>.wwkey</code> · <code>.wwtest</code> · <code>.wwdebug</code> · "
            "<code>.wwapi</code>"
        ),
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("domain", _DOMAIN, "Домен сервісу"),
            loader.ConfigValue(
                "timeout", 20, "Таймаут (секунди)",
                validator=loader.validators.Integer(minimum=3, maximum=120),
            ),
            loader.ConfigValue(
                "api_key", "",
                "Особистий API-ключ (/api у боті → .wwkey YOUR_KEY)",
                validator=loader.validators.String(),
            ),
        )
        self._session = None
        self._me      = None

    async def client_ready(self, client, db):
        self._me      = await client.get_me()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def on_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        key = str(self.config["api_key"]).strip()
        headers = {"Accept": "application/json"}
        if key:
            headers["X-API-Key"] = key
        return headers

    def _url(self, path: str) -> str:
        return f"{str(self.config['domain']).rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _path(value) -> str:
        return quote(str(value), safe="@-_")

    async def _request(self, method: str, path: str, **kwargs):
        path = str(path).lstrip("/")
        route = path.split("?", 1)[0]
        segments = route.replace("\\", "/").split("/")
        if not path.startswith("api/") or any(part in {".", ".."} for part in segments):
            raise RuntimeError("дозволені лише маршрути /api/*")
        url = self._url(path)
        logger.debug("Werwolf %s %s", method, url)
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            timeout = aiohttp.ClientTimeout(total=self.config["timeout"])
            headers = self._headers()
            headers.update(kwargs.pop("headers", {}) or {})
            async with self._session.request(method, url, headers=headers, timeout=timeout, **kwargs) as resp:
                body = await resp.text()
                logger.debug("Werwolf ← %s %.200s", resp.status, body)
                if resp.status == 401:
                    raise RuntimeError("невірний API-ключ (401)")
                if resp.status == 403:
                    try:
                        reason = (_json.loads(body).get("error") or "доступ заборонено")
                    except Exception:
                        reason = "доступ заборонено"
                    raise RuntimeError(f"{reason} (403)")
                if resp.status == 404:
                    raise RuntimeError(f"не знайдено (404): {url}")
                if resp.status >= 500:
                    raise RuntimeError(f"помилка сервера ({resp.status})")
                if resp.status >= 400:
                    try:
                        msg = _json.loads(body)
                        err = msg.get("error") or msg.get("message") or body[:200]
                    except Exception:
                        err = body[:200]
                    raise RuntimeError(f"HTTP {resp.status}: {err}")
                if resp.status == 204 or not body.strip():
                    return {}
                try:
                    payload = _json.loads(body)
                    if not isinstance(payload, (dict, list)):
                        raise ValueError("top-level JSON is not an object or list")
                    return payload
                except (ValueError, _json.JSONDecodeError):
                    raise RuntimeError(f"невалідний JSON: {body[:100]}")
        except asyncio.TimeoutError:
            raise RuntimeError(f"таймаут ({self.config['timeout']}с)")
        except RuntimeError:
            raise
        except aiohttp.ClientError as e:
            raise RuntimeError(f"помилка мережі: {e}")

    async def _get(self, path):    return await self._request("GET", path)
    async def _post(self, path, json): return await self._request("POST", path, json=json)
    async def _put(self, path, json):  return await self._request("PUT", path, json=json)
    async def _delete(self, path):     return await self._request("DELETE", path)

    # ── Utils ─────────────────────────────────────────────────────────────

    def _check_key(self) -> bool:
        return bool(str(self.config["api_key"]).strip())

    async def _guard(self, message) -> bool:
        if not self._check_key():
            await utils.answer(message, self.strings["no_key"])
            return False
        return True

    async def _resolve_to_ww_id(self, message, arg: str) -> int | None:
        if arg.lstrip("-").isdigit():
            return int(arg)
        try:
            entity = await message.client.get_entity(arg.lstrip("@"))
            return entity.id
        except Exception:
            return None

    async def _resolve_uid(self, message) -> tuple[int | None, str | None]:
        """Повертає (uid, tg_name)."""
        if message.is_reply:
            reply = await message.get_reply_message()
            if reply and reply.sender_id:
                uid = reply.sender_id
                name = None
                try:
                    sender = await reply.get_sender()
                    name = getattr(sender, "first_name", None) or getattr(sender, "username", None)
                except Exception:
                    pass
                return uid, name
        args = utils.get_args_raw(message).strip()
        if args:
            uid = await self._resolve_to_ww_id(message, args)
            name = None
            if uid:
                try:
                    entity = await message.client.get_entity(uid)
                    name = getattr(entity, "first_name", None) or getattr(entity, "username", None)
                except Exception:
                    pass
            return uid, name
        return None, None

    async def _err(self, msg, e):
        await utils.answer(msg, self.strings["err"].format(e=_esc(e)))

    # ── Service ───────────────────────────────────────────────────────────

    @loader.command()
    async def wwhelp(self, message):
        """Показати довідку та список команд"""
        await utils.answer(message, self.strings["help"])

    @loader.command()
    async def wwkey(self, message):
        """Встановити API-ключ: .wwkey YOUR_KEY  |  без аргументу — показати"""
        key = utils.get_args_raw(message).strip()
        if not key:
            cur = str(self.config["api_key"]).strip()
            if cur:
                await utils.answer(message, f"🔑 Ключ: <code>{cur[:6]}***{cur[-4:]}</code>")
            else:
                await utils.answer(message, "❌ Ключ не встановлено.\n<code>.wwkey YOUR_API_KEY</code>")
            return
        self.config["api_key"] = key
        masked = f"{key[:6]}***{key[-4:]}" if len(key) > 10 else "***"
        msg = await utils.answer(message, f"✅ Ключ збережено: <code>{masked}</code>")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await (msg[0] if isinstance(msg, list) else msg).delete()
        except Exception:
            pass

    @loader.command()
    async def wwtest(self, message):
        """Перевірити API: .wwtest  |  .wwtest group (в групі)"""
        if not await self._guard(message): return
        args = utils.get_args_raw(message).strip().lower()
        msg  = await utils.answer(message, "<code>⏳ Тестую...</code>")
        try:
            if args == "group":
                if message.is_private:
                    await utils.answer(msg, "❌ Тільки в групах."); return
                path = f"api/public/groups/{message.chat_id}"
            else:
                path = "api/profile"
            data  = await self._get(path)
            label = ""
            if "balance" in data:
                label = f"⭐{_f(data['balance'].get('coins')):.0f}  🥇{_f(data['balance'].get('gold')):.1f}"
            elif "title" in data:
                label = _esc(data["title"])
            await utils.answer(
                msg,
                f"✅ <b>API працює</b>\n<code>{_esc(self._url(path))}</code>\n{label}",
            )
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def wwdebug(self, message):
        """Сирий JSON: .wwdebug [me|group|user ID|@username]"""
        if not await self._guard(message): return
        args = utils.get_args_raw(message).strip().split()
        cmd  = args[0].lower() if args else "me"
        msg  = await utils.answer(message, "<code>⏳...</code>")

        if cmd == "me":
            path = "api/profile"
        elif cmd == "group":
            if message.is_private:
                await utils.answer(msg, "❌ Тільки в групах."); return
            path = f"api/public/groups/{message.chat_id}"
        elif cmd == "user" and len(args) >= 2:
            raw = args[1]
            # API підтримує @username напряму
            if raw.startswith("@"):
                path = f"api/public/users/{self._path(raw)}"
            else:
                uid = await self._resolve_to_ww_id(message, raw)
                if uid is None:
                    await utils.answer(msg, f"❌ Не знайдено: <code>{_esc(raw)}</code>"); return
                path = f"api/public/users/{uid}"
        else:
            await utils.answer(msg, "❌ <code>.wwdebug me | group | user ID/@username</code>"); return

        try:
            data    = await self._get(path)
            raw     = _json.dumps(_redact(data), ensure_ascii=False, indent=2)
            preview = raw[:3500] + "\n…" if len(raw) > 3500 else raw
            await utils.answer(
                msg,
                f"<code>{_esc(self._url(path))}</code>\n\n<pre>{_esc(preview)}</pre>",
            )
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def wwapi(self, message):
        """Універсальний JSON API: .wwapi GET public/users?limit=5 | .wwapi POST pets/create {"name":"Луна"}"""
        if not await self._guard(message): return
        raw = utils.get_args_raw(message).strip()
        parts = raw.split(maxsplit=2)
        if len(parts) < 2 or parts[0].upper() not in {"GET", "POST", "PUT", "DELETE"}:
            await utils.answer(message, "❌ <code>.wwapi METHOD api/path [JSON_OBJECT]</code>")
            return
        method, path = parts[0].upper(), parts[1].lstrip("/")
        if not path.startswith("api/"):
            path = "api/" + path
        body = None
        if len(parts) == 3:
            try:
                body = _json_object(parts[2])
            except ValueError as exc:
                await self._err(message, exc); return
        if method in {"POST", "PUT"} and body is None:
            body = {}
        msg = await utils.answer(message, self.strings["loading"])
        try:
            headers = {}
            if method == "POST" and path.startswith("api/developer/"):
                headers["Idempotency-Key"] = str(uuid.uuid4())
            data = await self._request(method, path, json=body, headers=headers) if body is not None else await self._request(method, path, headers=headers)
            rendered = _json.dumps(_redact(data), ensure_ascii=False, indent=2)
            if len(rendered) > 3500:
                rendered = rendered[:3500] + "\n…"
            await utils.answer(msg, f"✅ <code>{_esc(method)} {_esc(path)}</code>\n<pre>{_esc(rendered)}</pre>")
        except RuntimeError as exc:
            await self._err(msg, exc)

    # ── Stats ─────────────────────────────────────────────────────────────

    @loader.command()
    async def я(self, message):
        """Моя статистика у Werwolf"""
        if not await self._guard(message): return
        tg_name = getattr(self._me, "first_name", None) or getattr(self._me, "username", None)
        await self._show_user(message, self._me.id, tg_name)

    @loader.command()
    async def ти(self, message):
        """Статистика іншого (.ти у відповідь / .ти @username / .ти ID)"""
        if not await self._guard(message): return
        uid, tg_name = await self._resolve_uid(message)
        if uid is None:
            await utils.answer(message, self.strings["no_user"]); return
        await self._show_user(message, uid, tg_name)

    @loader.command()
    async def чат(self, message):
        """Статистика поточної групи"""
        if not await self._guard(message): return
        if message.is_private:
            await utils.answer(message, self.strings["no_chat"]); return
        await self._show_group(message, message.chat_id)

    @loader.command()
    async def топ(self, message):
        """Топ юзерів поточного чату"""
        if not await self._guard(message): return
        if message.is_private:
            await utils.answer(message, self.strings["no_chat"]); return
        await self._show_group(message, message.chat_id)

    @loader.command()
    async def зв(self, message):
        """Зв'язки чату; у відповідь — зв'язки конкретного юзера"""
        if not await self._guard(message): return
        if message.is_private:
            await utils.answer(message, self.strings["no_chat"]); return
        msg     = await utils.answer(message, self.strings["loading"])
        chat_id = message.chat_id
        try:
            if message.is_reply:
                uid, name = await self._resolve_uid(message)
                data = await self._get(f"api/public/relationships/user/{chat_id}/{uid}")
                await utils.answer(msg, _fmt_rel_user(data or {}, name or str(uid)))
            else:
                data = await self._get(f"api/public/relationships/chat/{chat_id}")
                await utils.answer(msg, _fmt_rel_chat(data or {}, chat_id))
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def графік(self, message):
        """Графік активності групи (.графік в групі)"""
        if not await self._guard(message): return
        if message.is_private:
            await utils.answer(message, self.strings["no_chat"]); return
        msg = await utils.answer(message, "<code>⏳ Генерую графік...</code>")
        try:
            data  = await self._get(f"api/public/groups/{message.chat_id}")
            daily = data.get("stats", {}).get("daily_messages", [])
            title = data.get("title") or f"chat {message.chat_id}"

            # Фільтруємо нулі
            daily = [d for d in daily if _n(d.get("count")) > 0]
            if not daily:
                await utils.answer(msg, "❌ Немає даних для графіка."); return

            png = await _make_chart(daily, str(title))
            if not png:
                await utils.answer(msg, "⚠️ Потрібен matplotlib: <code>pip install matplotlib</code>"); return

            image = io.BytesIO(png)
            image.name = "werwolf-activity.png"
            await message.client.send_file(message.chat_id, image,
                caption=f"📈 {_esc(title)}", force_document=False)
            await (msg[0] if isinstance(msg, list) else msg).delete()
        except RuntimeError as e:
            await self._err(msg, e)

    # ── Profile / actions ─────────────────────────────────────────────────

    @loader.command()
    async def профіль(self, message):
        """Мій профіль, баланс, рівень, інвентар, преміум"""
        if not await self._guard(message): return
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get("api/profile")
            await utils.answer(msg, _fmt_profile(data))
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def пет(self, message):
        """Мої пети"""
        if not await self._guard(message): return
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get("api/pets")
            await utils.answer(msg, _fmt_pets(data))
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def друзі(self, message):
        """Мої друзі та заявки"""
        if not await self._guard(message): return
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get("api/friends")
            await utils.answer(msg, _fmt_friends(data))
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def нік(self, message):
        """
        Нікнейм:
        .нік           — показати поточний
        .нік Лисичка   — встановити
        .нік -         — очистити
        """
        if not await self._guard(message): return
        args = utils.get_args_raw(message).strip()
        msg  = await utils.answer(message, self.strings["loading"])
        try:
            if not args:
                data = await self._get("api/profile/nickname")
                nick = _esc(data.get("nickname") or "")
                await utils.answer(msg,
                    f"🏷 Нікнейм: <i>{nick}</i>" if nick else "🏷 Нікнейм не встановлено.")
            elif args == "-":
                await self._delete("api/profile/nickname")
                await utils.answer(msg, "✅ Нікнейм очищено.")
            else:
                resp = await self._put("api/profile/nickname", {"nickname": args})
                nick = _esc((resp or {}).get("nickname") or args)
                await utils.answer(msg, f"✅ Нікнейм: <i>{nick}</i>")
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def переказ(self, message):
        """
        .переказ SHORT_ID СУМА [coins|gold]
        За замовчуванням — coins
        Приклад: .переказ gTtHFi 100 gold
        """
        if not await self._guard(message): return
        args  = utils.get_args_raw(message).strip().split()
        usage = "<code>.переказ SHORT_ID СУМА [coins|gold]</code>"

        if len(args) < 2:
            await utils.answer(message, f"❌ {usage}"); return

        short_id   = args[0]
        amount_str = args[1]
        currency   = args[2].lower() if len(args) >= 3 else "coins"

        if currency not in ("coins", "gold"):
            await utils.answer(message, f"❌ Валюта: <code>coins</code> або <code>gold</code>"); return
        try:
            amount = float(amount_str.replace(",", "."))
        except ValueError:
            await utils.answer(message, "❌ Сума має бути числом."); return
        if not math.isfinite(amount) or amount <= 0:
            await utils.answer(message, "❌ Сума має бути додатним скінченним числом."); return
        if amount > 1_000_000_000:
            await utils.answer(message, "❌ Сума перевищує дозволений ліміт."); return

        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._post("api/profile/transfer", {
                "short_id": short_id,
                "amount":   amount,
                "currency": currency,
            })
            await utils.answer(msg, _fmt_transfer_resp(data or {}, currency))
        except RuntimeError as e:
            await self._err(msg, e)

    @loader.command()
    async def курс(self, message):
        """Поточні курси валют RotKranz (API key не обов'язковий)"""
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get("api/rates")
            def rate(name):
                value = data.get(name)
                return "н/д" if value is None else f"{_f(value):g}"
            await utils.answer(msg, "\n".join((
                "<b>💱 Курси RotKranz</b>", _sep(),
                f"1 🥇 = <b>{rate('coin_per_gold')}</b> ⭐",
                f"🥇 / USD: <b>{rate('gold_usd')}</b>",
                f"USD / UAH: <b>{rate('usd_uah')}</b>",
                f"USD / EUR: <b>{rate('usd_eur')}</b>",
            )))
        except RuntimeError as exc:
            await self._err(msg, exc)

    @loader.command()
    async def чати(self, message):
        """Список доступних чатів поточного користувача"""
        if not await self._guard(message): return
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get("api/chats")
            items = data if isinstance(data, list) else data.get("chats", [])
            lines = ["<b>💬 Мої чати</b>", _sep()]
            for item in items[:20]:
                title = _esc(item.get("title") or item.get("name") or "Без назви")
                cid = item.get("chat_id", item.get("id", "?"))
                count = _n(item.get("messages") or item.get("message_count"))
                lines.append(f"· <b>{title}</b> — {count:,}\n  <code>{cid}</code>")
            if not items: lines.append("Чатів немає.")
            await utils.answer(msg, "\n".join(lines))
        except RuntimeError as exc:
            await self._err(msg, exc)

    @loader.command()
    async def рейтинг(self, message):
        """Рейтинг груп: .рейтинг [current|day|month|all]"""
        if not await self._guard(message): return
        period = utils.get_args_raw(message).strip().lower() or "current"
        if period not in {"current", "day", "month", "all"}:
            await utils.answer(message, "❌ Період: <code>current | day | month | all</code>"); return
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get(f"api/rating?period={period}")
            items = data if isinstance(data, list) else data.get("rating") or data.get("groups") or []
            lines = [f"<b>🏆 Рейтинг · {_esc(period)}</b>", _sep()]
            for index, item in enumerate(items[:15], 1):
                title = _esc(item.get("title") or item.get("name") or item.get("chat_id") or "?")
                score = _n(item.get("messages") or item.get("count") or item.get("score"))
                lines.append(f"{index}. <b>{score:,}</b> · {title}")
            if not items: lines.append("Даних немає.")
            await utils.answer(msg, "\n".join(lines))
        except RuntimeError as exc:
            await self._err(msg, exc)

    @loader.command()
    async def чатнік(self, message):
        """Нік у поточному чаті: .чатнік [новий нік|-]"""
        if not await self._guard(message): return
        if message.is_private:
            await utils.answer(message, self.strings["no_chat"]); return
        value = utils.get_args_raw(message).strip()
        path = f"api/profile/chats/{message.chat_id}/nickname"
        msg = await utils.answer(message, self.strings["loading"])
        try:
            if value == "-":
                await self._delete(path); text = "✅ Нік чату очищено."
            elif value:
                data = await self._put(path, {"nickname": value})
                text = f"✅ Нік чату: <i>{_esc(data.get('nickname') or value)}</i>"
            else:
                data = await self._get(path)
                nick = data.get("nickname")
                text = f"🏷 Нік чату: <i>{_esc(nick)}</i>" if nick else "🏷 Нік чату не встановлено."
            await utils.answer(msg, text)
        except RuntimeError as exc:
            await self._err(msg, exc)

    @loader.command()
    async def петдія(self, message):
        """Дія з петом: .петдія PET_ID ACTION [duration_hours]"""
        if not await self._guard(message): return
        args = utils.get_args_raw(message).split()
        if len(args) < 2 or not args[0].isdigit():
            await utils.answer(message, "❌ <code>.петдія PET_ID ACTION [duration_hours]</code>"); return
        body = {"action": args[1]}
        if len(args) > 2:
            hours = _f(args[2], -1)
            if hours <= 0:
                await utils.answer(message, "❌ duration_hours має бути додатним числом."); return
            body["duration_hours"] = hours
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._post(f"api/pets/{args[0]}/action", body)
            await utils.answer(msg, "✅ Дію виконано.\n\n" + _fmt_pets(data if "pets" in data else {"pets": [data.get("pet", data)]}))
        except RuntimeError as exc:
            await self._err(msg, exc)

    @loader.command()
    async def друг(self, message):
        """Друзі: .друг додати SHORT_ID | прийняти/відхилити/видалити USER_ID"""
        if not await self._guard(message): return
        args = utils.get_args_raw(message).split()
        actions = {"додати": ("request", "short_id"), "прийняти": ("respond", "user_id"),
                   "відхилити": ("respond", "user_id"), "видалити": ("remove", "user_id")}
        if len(args) != 2 or args[0].lower() not in actions:
            await utils.answer(message, "❌ <code>.друг додати SHORT_ID | прийняти/відхилити/видалити USER_ID</code>"); return
        verb = args[0].lower(); endpoint, field = actions[verb]
        if field == "user_id" and not args[1].isdigit():
            await utils.answer(message, "❌ USER_ID має бути числом."); return
        body = {field: int(args[1]) if field == "user_id" else args[1]}
        if endpoint == "respond": body["action"] = "accept" if verb == "прийняти" else "decline"
        msg = await utils.answer(message, self.strings["loading"])
        try:
            await self._post(f"api/friends/{endpoint}", body)
            await utils.answer(msg, "✅ Виконано.")
        except RuntimeError as exc:
            await self._err(msg, exc)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _show_user(self, message, uid: int, tg_name: str | None = None):
        msg = await utils.answer(message, self.strings["loading"])
        try:
            # API підтримує числовий ID напряму
            data = await self._get(f"api/public/users/{self._path(uid)}")
            # Якщо нема user-блоку — інжектуємо ім'я з Telegram
            if not data.get("user") and tg_name:
                data["_tg_name"] = tg_name
                data["_tg_id"]   = uid
            await utils.answer(msg, _fmt_user(data))
        except RuntimeError as e:
            await self._err(msg, e)

    async def _show_group(self, message, chat_id: int):
        msg = await utils.answer(message, self.strings["loading"])
        try:
            data = await self._get(f"api/public/groups/{chat_id}")
            await utils.answer(msg, _fmt_group(data))
        except RuntimeError as e:
            # Fallback: спробуємо без -100 префіксу
            cid_str = str(chat_id)
            if "404" in str(e) and cid_str.startswith("-100"):
                try:
                    data = await self._get(f"api/public/groups/{cid_str[4:]}")
                    await utils.answer(msg, _fmt_group(data))
                    return
                except RuntimeError:
                    pass
            await self._err(msg, e)
