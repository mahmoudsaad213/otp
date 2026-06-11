# -*- coding: utf-8 -*-
"""Telegram admin control panel."""
import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import database as db

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "5895491379").split(",")
    if x.strip()
]

USERS_PER_PAGE = 8
MAX_CB = 60  # telegram callback_data limit


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _btn(text: str, callback: str = "noop", style: str | None = None) -> InlineKeyboardButton:
    cb = callback[:MAX_CB]
    kwargs: dict = {"text": text, "callback_data": cb}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(**kwargs)


def _user_display(uid: int) -> str:
    user = db.get_user(uid)
    if not user:
        return str(uid)
    return db.format_user_label(user)


# ── Keyboards ──────────────────────────────────────────────

def admin_main_keyboard() -> InlineKeyboardMarkup:
    s = db.get_settings()
    maint = s["maintenance_mode"] == "1"
    total = db.count_users()
    return InlineKeyboardMarkup([
        [_btn("📊 إحصائيات عامة", "adm:st", "primary")],
        [_btn(f"👥 المستخدمين ({total})", "adm:us:0", "primary")],
        [_btn("🟢 الفحوصات النشطة", "adm:act", "success")],
        [_btn("⚙️ الإعدادات العامة", "adm:set", "primary")],
        [
            _btn(
                "🔧 صيانة: ON" if maint else "🔧 صيانة: OFF",
                "adm:mt:0" if maint else "adm:mt:1",
                "danger" if maint else "success",
            )
        ],
        [_btn("📢 رسالة جماعية", "adm:bc", "primary")],
        [_btn("💾 نسخة احتياطية للبيانات", "adm:bak", "primary")],
        [_btn("🛑 إيقاف كل الفحوصات", "adm:kill", "danger")],
        [_btn("🔄 تحديث اللوحة", "adm:main", "primary")],
    ])


def admin_stats_text(active_count: int) -> str:
    g = db.get_global_stats()
    s = db.get_settings()
    return (
        "📊 *إحصائيات عامة*\n\n"
        f"👥 المستخدمين: `{g['total_users']}`\n"
        f"🟢 فحوصات نشطة: `{active_count}`\n"
        f"🚫 محظورين: `{g['banned']}`\n"
        f"⏸ معلّقين: `{g['suspended']}`\n\n"
        f"📦 إجمالي الجلسات: `{g['total_sessions']}`\n"
        f"💳 إجمالي الكروت: `{g['total_cards']}`\n"
        f"✅ 3DS: `{g['total_3ds']}`\n"
        f"❌ Decline: `{g['total_failed']}`\n"
        f"🚫 Errors: `{g['total_errors']}`\n\n"
        f"🔢 حد الكروت: `{s['global_max_cards']}`\n"
        f"⏱ OTP: `{s['global_delay']}ث` | LIVE: `{s.get('live_delay', '2.0')}ث`\n"
        f"📅 حد يومي: `{s['default_daily_limit'] or '∞'}`\n"
        f"🔧 صيانة: `{'ON' if s['maintenance_mode'] == '1' else 'OFF'}`"
    )


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    s = db.get_settings()
    mx = int(s["global_max_cards"])
    dy = float(s["global_delay"])
    ldy = float(s.get("live_delay", "2.0"))
    dl = int(s["default_daily_limit"])
    bot_on = s["bot_enabled"] == "1"
    return InlineKeyboardMarkup([
        [_btn(f"🔢 حد الكروت الحالي: {mx}", "noop", "primary")],
        [
            _btn("10", "adm:mx:10", "primary"),
            _btn("25", "adm:mx:25", "primary"),
            _btn("50", "adm:mx:50", "primary"),
            _btn("100", "adm:mx:100", "primary"),
        ],
        [_btn(f"⏱ OTP — {dy}ث", "noop", "primary")],
        [
            _btn("0", "adm:dy:0", "primary"),
            _btn("0.5", "adm:dy:0.5", "primary"),
            _btn("1", "adm:dy:1", "primary"),
            _btn("2", "adm:dy:2", "primary"),
            _btn("5", "adm:dy:5", "primary"),
        ],
        [_btn(f"⏱ LIVE — {ldy}ث", "noop", "primary")],
        [
            _btn("0", "adm:ldy:0", "primary"),
            _btn("0.5", "adm:ldy:0.5", "primary"),
            _btn("1", "adm:ldy:1", "primary"),
            _btn("2", "adm:ldy:2", "primary"),
            _btn("3", "adm:ldy:3", "primary"),
            _btn("5", "adm:ldy:5", "primary"),
        ],
        [_btn(f"📅 الحد اليومي: {dl or '∞'}", "noop", "primary")],
        [
            _btn("∞", "adm:dlm:0", "primary"),
            _btn("50", "adm:dlm:50", "primary"),
            _btn("100", "adm:dlm:100", "primary"),
            _btn("200", "adm:dlm:200", "primary"),
        ],
        [
            _btn(
                "🟢 البوت: شغال" if bot_on else "🔴 البوت: موقوف",
                "adm:bot:0" if bot_on else "adm:bot:1",
                "success" if bot_on else "danger",
            )
        ],
        [_btn("✏️ رسالة الصيانة", "adm:mmsg", "primary")],
        [_btn("🔙 رجوع", "adm:main", "primary")],
    ])


def users_list_keyboard(page: int) -> InlineKeyboardMarkup:
    users = db.list_users(page, USERS_PER_PAGE)
    total = db.count_users()
    pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    rows = []
    for u in users:
        label = db.format_user_label(u)[:40]
        cards = u["total_cards"]
        rows.append([_btn(f"{label} — {cards}💳", f"adm:u:{u['user_id']}", "primary")])

    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"adm:us:{page - 1}", "primary"))
    nav.append(_btn(f"{page + 1}/{pages}", "noop", "primary"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"adm:us:{page + 1}", "primary"))
    rows.append(nav)
    rows.append([_btn("🔍 بحث بالـ ID", "adm:find", "primary")])
    rows.append([_btn("🔙 رجوع", "adm:main", "primary")])
    return InlineKeyboardMarkup(rows)


def user_detail_keyboard(uid: int) -> InlineKeyboardMarkup:
    user = db.get_user(uid) or {}
    banned = user.get("is_banned")
    suspended = user.get("is_suspended")
    rows = [
        [
            _btn("✅ رفع الحظر" if banned else "🚫 حظر", f"adm:ub:{uid}" if banned else f"adm:bn:{uid}", "success" if banned else "danger"),
            _btn("✅ إلغاء التعليق" if suspended else "⏸ تعليق", f"adm:usp:{uid}" if suspended else f"adm:sp:{uid}", "success" if suspended else "danger"),
        ],
        [_btn("🔢 تعديل حد الكروت", f"adm:ulm:{uid}", "primary")],
        [_btn("⏱ تعديل التأخير", f"adm:udl:{uid}", "primary")],
        [_btn("📅 تعديل الحد اليومي", f"adm:udlm:{uid}", "primary")],
        [_btn("🛑 إيقاف فحصه", f"adm:stp:{uid}", "danger")],
        [_btn("📩 رسالة خاصة", f"adm:msg:{uid}", "primary")],
        [_btn("🗑 تصفير إحصائياته", f"adm:rst:{uid}", "danger")],
        [_btn("🔙 قائمة المستخدمين", "adm:us:0", "primary")],
    ]
    return InlineKeyboardMarkup(rows)


def user_detail_text(uid: int) -> str:
    user = db.get_user(uid)
    if not user:
        return f"❌ المستخدم `{uid}` غير موجود."
    mx, dy, dl = db.get_user_limits(uid)
    custom_mx = "افتراضي" if user["custom_max_cards"] is None else str(user["custom_max_cards"])
    custom_dy = "افتراضي" if user["custom_delay"] is None else f"{user['custom_delay']}ث"
    custom_dl = "افتراضي" if user["daily_limit"] is None else (str(user["daily_limit"]) if user["daily_limit"] else "∞")
    la = user["last_active"] or "—"
    if len(la) > 19:
        la = la[:19]
    return (
        f"👤 *{_user_display(uid)}*\n"
        f"🆔 `{uid}`\n\n"
        f"📦 جلسات: `{user['total_sessions']}`\n"
        f"💳 كروت مفحوصة: `{user['total_cards']}`\n"
        f"✅ 3DS: `{user['total_3ds']}` | ❌ Decline: `{user['total_failed']}`\n"
        f"🚫 Errors: `{user['total_errors']}`\n"
        f"📅 مستخدم اليوم: `{user['daily_used']}`\n\n"
        f"🚫 محظور: `{'نعم' if user['is_banned'] else 'لا'}`\n"
        f"⏸ معلّق: `{'نعم' if user['is_suspended'] else 'لا'}`\n"
        f"🔢 حد الكروت: `{custom_mx}` (فعّال: {mx})\n"
        f"⏱ التأخير: `{custom_dy}` (فعّال: {dy}ث)\n"
        f"📅 حد يومي: `{custom_dl}`\n"
        f"🕐 آخر نشاط: `{la}`"
    )


def user_limit_keyboard(uid: int, prefix: str) -> InlineKeyboardMarkup:
    """prefix: ulm | udl | udlm"""
    presets = {
        "ulm": [("10", 10), ("25", 25), ("50", 50), ("100", 100), ("∞", -1)],
        "udl": [("0.5ث", 0.5), ("1ث", 1), ("2ث", 2), ("5ث", 5), ("∞", -1)],
        "udlm": [("∞", 0), ("50", 50), ("100", 100), ("200", 200), ("افتراضي", -1)],
    }
    items = presets[prefix]
    row = [_btn(lbl, f"adm:{prefix}:{uid}:{val}", "primary") for lbl, val in items[:5]]
    return InlineKeyboardMarkup([
        row,
        [_btn("🔙 رجوع", f"adm:u:{uid}", "primary")],
    ])


# ── Handlers ───────────────────────────────────────────────

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, active_sessions_fn) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ أدمن فقط.")
        return
    active = active_sessions_fn()
    await update.message.reply_text(
        "👑 *لوحة تحكم الأدمن*\n\n" + admin_stats_text(active),
        reply_markup=admin_main_keyboard(),
        parse_mode="Markdown",
    )


async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    active_sessions_fn,
    stop_user_fn,
    stop_all_fn,
) -> None:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("أدمن فقط", show_alert=True)
        return

    data = query.data
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    # ── main / stats ──
    if action == "main":
        await query.answer()
        active = active_sessions_fn()
        await query.edit_message_text(
            "👑 *لوحة تحكم الأدمن*\n\n" + admin_stats_text(active),
            reply_markup=admin_main_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "st":
        await query.answer()
        active = active_sessions_fn()
        await query.edit_message_text(
            admin_stats_text(active),
            reply_markup=InlineKeyboardMarkup([[_btn("🔙 رجوع", "adm:main", "primary")]]),
            parse_mode="Markdown",
        )
        return

    # ── users list ──
    if action == "us":
        page = int(parts[2]) if len(parts) > 2 else 0
        await query.answer()
        await query.edit_message_text(
            f"👥 *المستخدمين* — صفحة {page + 1}",
            reply_markup=users_list_keyboard(page),
            parse_mode="Markdown",
        )
        return

    if action == "u":
        uid = int(parts[2])
        await query.answer()
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    # ── ban / suspend ──
    if action == "bn":
        uid = int(parts[2])
        db.ban_user(uid, "من الأدمن")
        stop_user_fn(uid)
        await query.answer("تم الحظر")
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    if action == "ub":
        uid = int(parts[2])
        db.unban_user(uid)
        await query.answer("تم رفع الحظر")
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    if action == "sp":
        uid = int(parts[2])
        db.suspend_user(uid)
        stop_user_fn(uid)
        await query.answer("تم التعليق")
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    if action == "usp":
        uid = int(parts[2])
        db.unsuspend_user(uid)
        await query.answer("تم إلغاء التعليق")
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    if action == "stp":
        uid = int(parts[2])
        stop_user_fn(uid)
        await query.answer("تم إيقاف الفحص")
        return

    if action == "rst":
        uid = int(parts[2])
        db.reset_user_stats_db(uid)
        await query.answer("تم تصفير الإحصائيات")
        await query.edit_message_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return

    # ── user limits ──
    for act, setter in [
        ("ulm", lambda uid, v: db.set_user_max_cards(uid, None if v < 0 else v)),
        ("udl", lambda uid, v: db.set_user_delay(uid, None if v < 0 else v)),
        ("udlm", lambda uid, v: db.set_user_daily_limit(uid, None if v < 0 else v)),
    ]:
        if action == act:
            if len(parts) == 3:
                uid = int(parts[2])
                await query.answer()
                title = {"ulm": "حد الكروت", "udl": "التأخير", "udlm": "الحد اليومي"}[act]
                await query.edit_message_text(
                    f"اختر *{title}* للمستخدم `{uid}`:",
                    reply_markup=user_limit_keyboard(uid, act),
                    parse_mode="Markdown",
                )
                return
            if len(parts) == 4:
                uid, val = int(parts[2]), float(parts[3])
                setter(uid, int(val) if act != "udl" else val)
                await query.answer("تم التحديث")
                await query.edit_message_text(
                    user_detail_text(uid),
                    reply_markup=user_detail_keyboard(uid),
                    parse_mode="Markdown",
                )
                return

    # ── message user ──
    if action == "msg":
        uid = int(parts[2])
        context.user_data["admin_action"] = f"msg:{uid}"
        await query.answer()
        await query.message.reply_text(
            f"📩 اكتب الرسالة اللي عايز تبعتها للمستخدم `{uid}`:",
            parse_mode="Markdown",
        )
        return

    # ── settings ──
    if action == "set":
        await query.answer()
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "mx":
        db.set_setting("global_max_cards", parts[2])
        await query.answer(f"حد الكروت: {parts[2]}")
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "dy":
        db.set_setting("global_delay", parts[2])
        await query.answer(f"OTP: {parts[2]}ث")
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "ldy":
        db.set_setting("live_delay", parts[2])
        await query.answer(f"LIVE: {parts[2]}ث")
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "dlm":
        db.set_setting("default_daily_limit", parts[2])
        await query.answer("تم تحديث الحد اليومي")
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "bot":
        db.set_setting("bot_enabled", parts[2])
        await query.answer("تم التحديث")
        await query.edit_message_text(
            "⚙️ *الإعدادات العامة*",
            reply_markup=admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "mt":
        db.set_setting("maintenance_mode", parts[2])
        state = "تشغيل" if parts[2] == "1" else "إيقاف"
        await query.answer(f"وضع الصيانة: {state}")
        active = active_sessions_fn()
        await query.edit_message_text(
            "👑 *لوحة تحكم الأدمن*\n\n" + admin_stats_text(active),
            reply_markup=admin_main_keyboard(),
            parse_mode="Markdown",
        )
        return

    if action == "mmsg":
        context.user_data["admin_action"] = "mmsg"
        await query.answer()
        await query.message.reply_text("✏️ اكتب *رسالة الصيانة* الجديدة:", parse_mode="Markdown")
        return

    # ── broadcast ──
    if action == "bc":
        context.user_data["admin_action"] = "broadcast"
        await query.answer()
        targets = len(db.get_broadcast_targets())
        await query.message.reply_text(
            f"📢 اكتب الرسالة الجماعية.\nسيتم إرسالها لـ `{targets}` مستخدم.",
            parse_mode="Markdown",
        )
        return

    # ── active sessions ──
    if action == "act":
        active = active_sessions_fn()
        await query.answer()
        if not active:
            text = "🟢 *لا توجد فحوصات نشطة حالياً*"
            kb = InlineKeyboardMarkup([[_btn("🔙 رجوع", "adm:main", "primary")]])
        else:
            lines = ["🟢 *فحوصات نشطة:*\n"]
            rows = []
            for uid, info in active.items():
                lines.append(
                    f"• `{uid}` — {info['checked']}/{info['total']} "
                    f"({info.get('name', '?')})"
                )
                rows.append([_btn(f"🛑 {uid}", f"adm:stp:{uid}", "danger")])
            rows.append([_btn("🔙 رجوع", "adm:main", "primary")])
            text = "\n".join(lines)
            kb = InlineKeyboardMarkup(rows)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if action == "bak":
        await query.answer("جاري إنشاء النسخة...")
        tmp = Path(tempfile.gettempdir()) / f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        if not db.create_db_backup(str(tmp)):
            await query.message.reply_text("❌ ملف قاعدة البيانات غير موجود.")
            return
        try:
            with tmp.open("rb") as fh:
                await context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=fh,
                    filename=tmp.name,
                    caption=f"💾 نسخة احتياطية — {db.count_users()} مستخدم",
                )
            await query.message.reply_text("✅ تم إرسال النسخة الاحتياطية في الخاص.")
        finally:
            tmp.unlink(missing_ok=True)
        return

    # ── stop all ──
    if action == "kill":
        n = stop_all_fn()
        await query.answer(f"تم إيقاف {n} فحص")
        active = active_sessions_fn()
        await query.edit_message_text(
            "👑 *لوحة تحكم الأدمن*\n\n" + admin_stats_text(active),
            reply_markup=admin_main_keyboard(),
            parse_mode="Markdown",
        )
        return

    # ── find user ──
    if action == "find":
        context.user_data["admin_action"] = "find"
        await query.answer()
        await query.message.reply_text("🔍 أرسل *User ID* للبحث:", parse_mode="Markdown")
        return

    await query.answer()


async def handle_admin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    bot_app,
) -> bool:
    """Returns True if message was consumed by admin flow."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return False

    action = context.user_data.pop("admin_action", None)
    if not action:
        return False

    text = update.message.text.strip()

    if action == "broadcast":
        targets = db.get_broadcast_targets()
        msg = await update.message.reply_text(f"📤 جاري الإرسال لـ `{len(targets)}` مستخدم...")
        ok, fail = 0, 0
        for tid in targets:
            try:
                await bot_app.bot.send_message(chat_id=tid, text=f"📢 *إعلان من الإدارة*\n\n{text}", parse_mode="Markdown")
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        await msg.edit_text(f"✅ تم الإرسال: `{ok}` | فشل: `{fail}`", parse_mode="Markdown")
        return True

    if action == "mmsg":
        db.set_setting("maintenance_message", text)
        await update.message.reply_text("✅ تم حفظ رسالة الصيانة.")
        return True

    if action.startswith("msg:"):
        tid = int(action.split(":")[1])
        try:
            await bot_app.bot.send_message(
                chat_id=tid,
                text=f"📩 *رسالة من الإدارة*\n\n{text}",
                parse_mode="Markdown",
            )
            await update.message.reply_text(f"✅ تم الإرسال للمستخدم `{tid}`", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ فشل الإرسال: {exc}")
        return True

    if action == "find":
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ ID غير صالح.")
            return True
        db.upsert_user(uid)
        await update.message.reply_text(
            user_detail_text(uid),
            reply_markup=user_detail_keyboard(uid),
            parse_mode="Markdown",
        )
        return True

    return False
