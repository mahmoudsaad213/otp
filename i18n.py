# -*- coding: utf-8 -*-
"""Arabic / English translations."""
from __future__ import annotations

import database as db

LANGS = ("ar", "en")
DEFAULT_LANG = "ar"

LINK_CHANNEL = "https://t.me/mstoolvip"
LINK_OWNER = "https://t.me/FastSpeedtest"
LINK_CHAT = "https://t.me/facebook_method_tool"

TEXTS: dict[str, dict[str, str]] = {
    "ar": {
        "choose_language": "🌍 *اختر لغتك*\n\nChoose your language:",
        "language_set_ar": "✅ تم تعيين اللغة: *العربية*",
        "language_set_en": "✅ Language set: *English*",
        "welcome_title": "🚀 *DOBIES CC CHECKER*",
        "welcome_about": "بوت فحص كروت سريع — لوحة حية وإشعار *3D LIVE* فوري.",
        "welcome_body": "أرسل ملف `.txt` أو الصق الكروت مباشرة.\nالحد الأقصى *{max}* كرت — فحص كرت كرت.",
        "links_block": (
            "━━━━━━━━━━━━━━\n"
            "📢 [قناة الأدوات والتحديثات](https://t.me/mstoolvip)\n"
            "👤 [المالك](https://t.me/FastSpeedtest)\n"
            "💬 [الشات الجماعي](https://t.me/facebook_method_tool)"
        ),
        "btn_channel": "📢 الأدوات",
        "btn_owner": "👤 المالك",
        "btn_chat": "💬 الشات",
        "help": (
            "📖 *دليل الاستخدام*\n\n"
            "📄 أرسل ملف `.txt` أو الصق الكروت في رسالة\n"
            "📌 الصيغة: `number|MM|YYYY|CVV`\n\n"
            "🔢 الحد الأقصى: *{max}* كرت في المرة\n"
            "🐢 الفحص كرت كرت لتجنب الليمت\n\n"
            "✅ بيبعت رسالة فقط لو *3D LIVE*\n"
            "❌ الرفض مش بيبعت رسالة\n\n"
            "/start — القائمة\n"
            "/stop — إيقاف\n"
            "/reload — تجديد الجلسة\n"
            "/settings — الإعدادات"
        ),
        "settings_title": "⚙️ *الإعدادات*",
        "settings_lang": "🌍 اللغة الحالية: *{lang}*",
        "lang_ar": "العربية",
        "lang_en": "English",
        "btn_help": "📖 المساعدة",
        "btn_status": "📊 الحالة",
        "btn_reload": "🔑 تجديد الجلسة",
        "btn_settings": "⚙️ الإعدادات",
        "btn_admin": "👑 لوحة الأدمن",
        "btn_back": "🔙 رجوع",
        "btn_lang_ar": "🇸🇦 العربية",
        "btn_lang_en": "🇬🇧 English",
        "btn_stop": "🛑 إيقاف الفحص",
        "btn_refresh": "🔄 تحديث",
        "btn_send_file": "📤 أرسل ملف .txt للبدء",
        "dash_title": "🌸 *DOBIES CHECKER*",
        "status_waiting": "💤 في الانتظار",
        "status_running": "🟢 الفحص شغال",
        "status_completed": "✅ اكتمل الفحص",
        "status_stopped": "🛑 تم الإيقاف",
        "status_stopped_admin": "🛑 أوقفه الأدمن",
        "status_3d": "3D LIVE ✅",
        "btn_total": "📦 الإجمالي: {n}",
        "btn_progress": "📊 التقدم: {done}/{total} ({pct}%)",
        "btn_speed": "🚀 {speed}/د",
        "btn_3ds": "✅ 3DS: {n}",
        "btn_decline": "❌ Decline: {n}",
        "btn_errors": "🚫 Errors: {n}",
        "checking_now": "⚠️ *في فحص شغال دلوقتي!*\nاضغط 🛑 إيقاف من لوحة التحكم أولاً.",
        "max_trimmed": "⚠️ الحد الأقصى `{max}` كرت — تم قص `{skipped}` كرت زيادة.",
        "file_loaded": "✅ تم تحميل `{n}` كرت.{note}",
        "cards_ready": "✅ `{n}` كرت جاهز للفحص{note}",
        "invalid_lines": "\n⚠️ تم تجاهل `{n}` سطر بصيغة خاطئة.",
        "ignore_lines": " (تجاهل {n} سطر)",
        "empty_file": "❌ الملف فاضي أو لا يحتوي كروت بصيغة صحيحة.\nالصيغة: `number|MM|YYYY|CVV`",
        "dash_updated": "📊 تم تحديث لوحة التحكم.",
        "reloading": "🔄 جاري تجديد الجلسة...",
        "reload_ok": "✅ تم تجديد الجلسة بنجاح.",
        "reload_fail": "❌ فشل تجديد الجلسة.",
        "no_active_check": "ℹ️ لا يوجد فحص شغال حالياً.",
        "check_stopped": "🛑 *تم إيقاف الفحص*",
        "summary_stopped": "🛑 تم الإيقاف",
        "summary_done": "✅ اكتمل الفحص",
        "summary_total": "📦 الإجمالي: `{n}`",
        "summary_3ds": "✅ 3DS Live: `{n}`",
        "summary_decline": "❌ Declined: `{n}`",
        "summary_errors": "🚫 Errors: `{n}`",
        "summary_time": "⏱ المدة: `{t}`",
        "result_3d_title": "✅ *3D SECURE LIVE*",
        "result_3d_status": "🟢 Live — 3D Enrolled",
        "result_3d_file": "✅ *3DS Live Cards* — {n} كرت",
        "refresh_ok": "تم التحديث",
        "stopping": "جاري الإيقاف...",
        "reloading_short": "جاري التجديد...",
        "err_bot_disabled": "⛔ البوت متوقف مؤقتاً من الإدارة.",
        "err_maintenance": "🔧 البوت في وضع الصيانة حالياً. حاول لاحقاً.",
        "err_banned": "🚫 أنت محظور من البوت.\nالسبب: {reason}",
        "err_suspended": "⏸ حسابك معلّق مؤقتاً. تواصل مع الإدارة.",
        "err_max_cards": "⚠️ الحد الأقصى `{max}` كرت في المرة.",
        "err_daily_limit": "📅 تجاوزت الحد اليومي `{limit}` كرت.\nالمتبقي: `{remaining}`",
        "ban_default_reason": "مخالفة القواعد",
    },
    "en": {
        "choose_language": "🌍 *Choose your language*\n\nاختر لغتك:",
        "language_set_ar": "✅ Language set: *Arabic*",
        "language_set_en": "✅ Language set: *English*",
        "welcome_title": "🚀 *DOBIES CC CHECKER*",
        "welcome_about": "Fast card checker — live dashboard & instant *3D LIVE* alerts.",
        "welcome_body": "Send a `.txt` file or paste cards directly.\nMax *{max}* cards — one-by-one checking.",
        "links_block": (
            "━━━━━━━━━━━━━━\n"
            "📢 [Tools & Updates](https://t.me/mstoolvip)\n"
            "👤 [Owner](https://t.me/FastSpeedtest)\n"
            "💬 [Group Chat](https://t.me/facebook_method_tool)"
        ),
        "btn_channel": "📢 Tools",
        "btn_owner": "👤 Owner",
        "btn_chat": "💬 Chat",
        "help": (
            "📖 *User Guide*\n\n"
            "📄 Send a `.txt` file or paste cards in a message\n"
            "📌 Format: `number|MM|YYYY|CVV`\n\n"
            "🔢 Max: *{max}* cards per session\n"
            "🐢 Cards are checked one by one to avoid limits\n\n"
            "✅ Notifies only on *3D LIVE*\n"
            "❌ Declines are silent\n\n"
            "/start — Menu\n"
            "/stop — Stop check\n"
            "/reload — Refresh session\n"
            "/settings — Settings"
        ),
        "settings_title": "⚙️ *Settings*",
        "settings_lang": "🌍 Current language: *{lang}*",
        "lang_ar": "Arabic",
        "lang_en": "English",
        "btn_help": "📖 Help",
        "btn_status": "📊 Status",
        "btn_reload": "🔑 Reload Session",
        "btn_settings": "⚙️ Settings",
        "btn_admin": "👑 Admin Panel",
        "btn_back": "🔙 Back",
        "btn_lang_ar": "🇸🇦 Arabic",
        "btn_lang_en": "🇬🇧 English",
        "btn_stop": "🛑 Stop Check",
        "btn_refresh": "🔄 Refresh",
        "btn_send_file": "📤 Send .txt file to start",
        "dash_title": "🌸 *DOBIES CHECKER*",
        "status_waiting": "💤 Waiting",
        "status_running": "🟢 Checking",
        "status_completed": "✅ Completed",
        "status_stopped": "🛑 Stopped",
        "status_stopped_admin": "🛑 Stopped by admin",
        "status_3d": "3D LIVE ✅",
        "btn_total": "📦 Total: {n}",
        "btn_progress": "📊 Progress: {done}/{total} ({pct}%)",
        "btn_speed": "🚀 {speed}/m",
        "btn_3ds": "✅ 3DS: {n}",
        "btn_decline": "❌ Decline: {n}",
        "btn_errors": "🚫 Errors: {n}",
        "checking_now": "⚠️ *A check is already running!*\nPress 🛑 Stop on the dashboard first.",
        "max_trimmed": "⚠️ Max `{max}` cards — trimmed `{skipped}` extra cards.",
        "file_loaded": "✅ Loaded `{n}` cards.{note}",
        "cards_ready": "✅ `{n}` cards ready{note}",
        "invalid_lines": "\n⚠️ Ignored `{n}` invalid lines.",
        "ignore_lines": " (ignored {n} lines)",
        "empty_file": "❌ File is empty or has no valid cards.\nFormat: `number|MM|YYYY|CVV`",
        "dash_updated": "📊 Dashboard updated.",
        "reloading": "🔄 Reloading session...",
        "reload_ok": "✅ Session reloaded.",
        "reload_fail": "❌ Session reload failed.",
        "no_active_check": "ℹ️ No active check right now.",
        "check_stopped": "🛑 *Check stopped*",
        "summary_stopped": "🛑 Stopped",
        "summary_done": "✅ Check completed",
        "summary_total": "📦 Total: `{n}`",
        "summary_3ds": "✅ 3DS Live: `{n}`",
        "summary_decline": "❌ Declined: `{n}`",
        "summary_errors": "🚫 Errors: `{n}`",
        "summary_time": "⏱ Duration: `{t}`",
        "result_3d_title": "✅ *3D SECURE LIVE*",
        "result_3d_status": "🟢 Live — 3D Enrolled",
        "result_3d_file": "✅ *3DS Live Cards* — {n} cards",
        "refresh_ok": "Updated",
        "stopping": "Stopping...",
        "reloading_short": "Reloading...",
        "err_bot_disabled": "⛔ Bot is temporarily disabled by admin.",
        "err_maintenance": "🔧 Bot is under maintenance. Try again later.",
        "err_banned": "🚫 You are banned.\nReason: {reason}",
        "err_suspended": "⏸ Your account is suspended. Contact admin.",
        "err_max_cards": "⚠️ Max `{max}` cards per session.",
        "err_daily_limit": "📅 Daily limit `{limit}` exceeded.\nRemaining: `{remaining}`",
        "ban_default_reason": "Rule violation",
    },
}


def get_lang(user_id: int) -> str:
    lang = db.get_user_language(user_id)
    return lang if lang in LANGS else DEFAULT_LANG


def has_language(user_id: int) -> bool:
    return db.get_user_language(user_id) in LANGS


def t(user_id: int, key: str, **kwargs) -> str:
    lang = get_lang(user_id)
    text = TEXTS.get(lang, TEXTS[DEFAULT_LANG]).get(
        key, TEXTS[DEFAULT_LANG].get(key, key)
    )
    return text.format(**kwargs) if kwargs else text


def lang_label(user_id: int) -> str:
    lang = get_lang(user_id)
    return t(user_id, "lang_ar" if lang == "ar" else "lang_en")


def format_status(user_id: int, code: str) -> str:
    if code.startswith("decline:"):
        return f"DECLINE ({code.split(':', 1)[1]})"
    if code.startswith("error:"):
        return f"Error: {code.split(':', 1)[1]}"
    if code.startswith("guid_retry:"):
        return f"GUID Error — retry ({code.split(':', 1)[1]})"
    known = {
        "waiting": "status_waiting",
        "running": "status_running",
        "completed": "status_completed",
        "stopped": "status_stopped",
        "stopped_admin": "status_stopped_admin",
        "3d_live": "status_3d",
        "guid_error": "status_waiting",
    }
    key = known.get(code)
    if key:
        return t(user_id, key)
    return code[:50]
