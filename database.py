# -*- coding: utf-8 -*-
"""SQLite persistence for users, settings, and usage stats."""
import os
import sqlite3
import threading
from datetime import date, datetime

DB_PATH = os.getenv("DB_PATH", "bot_data.db")
_lock = threading.Lock()


def _ensure_db_dir() -> None:
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    if folder:
        os.makedirs(folder, exist_ok=True)

DEFAULT_SETTINGS = {
    "maintenance_mode": "0",
    "maintenance_message": "🔧 البوت في وضع الصيانة حالياً. حاول لاحقاً.",
    "global_max_cards": "100",
    "global_delay": "1.0",
    "live_delay": "2.0",
    "default_daily_limit": "0",
    "bot_enabled": "1",
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    _ensure_db_dir()
    with _lock:
        conn = _conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                last_name     TEXT,
                is_banned     INTEGER DEFAULT 0,
                is_suspended  INTEGER DEFAULT 0,
                custom_max_cards INTEGER,
                custom_delay  REAL,
                daily_limit   INTEGER,
                daily_used    INTEGER DEFAULT 0,
                daily_reset   TEXT,
                total_sessions INTEGER DEFAULT 0,
                total_cards   INTEGER DEFAULT 0,
                total_3ds     INTEGER DEFAULT 0,
                total_failed  INTEGER DEFAULT 0,
                total_errors  INTEGER DEFAULT 0,
                last_active   TEXT,
                registered_at TEXT,
                ban_reason    TEXT,
                admin_note    TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
        _migrate(conn)
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "language" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN language TEXT")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "checker_mode" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN checker_mode TEXT")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "otp_advanced" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN otp_advanced INTEGER DEFAULT 0")
    conn.commit()


def _today() -> str:
    return date.today().isoformat()


def get_setting(key: str, default: str = "") -> str:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
        conn.close()


def get_settings() -> dict:
    with _lock:
        conn = _conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
    result = dict(DEFAULT_SETTINGS)
    result.update({r["key"]: r["value"] for r in rows})
    return result


def upsert_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> None:
    now = datetime.now().isoformat()
    with _lock:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, registered_at, last_active, daily_reset)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username    = COALESCE(excluded.username, users.username),
                first_name  = COALESCE(excluded.first_name, users.first_name),
                last_name   = COALESCE(excluded.last_name, users.last_name),
                last_active = excluded.last_active
            """,
            (user_id, username, first_name, last_name, now, now, _today()),
        )
        conn.commit()
        conn.close()


def get_user(user_id: int) -> dict | None:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def get_user_language(user_id: int) -> str | None:
    user = get_user(user_id)
    if not user:
        return None
    return user.get("language")


def set_user_language(user_id: int, language: str) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET language = ? WHERE user_id = ?",
            (language, user_id),
        )
        conn.commit()
        conn.close()


def get_user_checker(user_id: int) -> str | None:
    user = get_user(user_id)
    if not user:
        return None
    mode = user.get("checker_mode")
    return mode if mode in ("otp", "live") else None


def set_user_checker(user_id: int, mode: str) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET checker_mode = ? WHERE user_id = ?",
            (mode, user_id),
        )
        conn.commit()
        conn.close()


def get_user_otp_advanced(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("otp_advanced"))


def set_user_otp_advanced(user_id: int, enabled: bool) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET otp_advanced = ? WHERE user_id = ?",
            (1 if enabled else 0, user_id),
        )
        conn.commit()
        conn.close()


def has_checker(user_id: int) -> bool:
    return get_user_checker(user_id) in ("otp", "live")


def _reset_daily_if_needed(conn: sqlite3.Connection, user_id: int) -> None:
    row = conn.execute(
        "SELECT daily_reset, daily_used FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return
    if row["daily_reset"] != _today():
        conn.execute(
            "UPDATE users SET daily_used = 0, daily_reset = ? WHERE user_id = ?",
            (_today(), user_id),
        )


def list_users(page: int = 0, per_page: int = 8) -> list[dict]:
    offset = page * per_page
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM users ORDER BY last_active DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def count_users() -> int:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        conn.close()
    return row["c"]


def count_banned() -> int:
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1").fetchone()
        conn.close()
    return row["c"]


def get_global_stats() -> dict:
    with _lock:
        conn = _conn()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_users,
                COALESCE(SUM(total_sessions), 0) AS total_sessions,
                COALESCE(SUM(total_cards), 0) AS total_cards,
                COALESCE(SUM(total_3ds), 0) AS total_3ds,
                COALESCE(SUM(total_failed), 0) AS total_failed,
                COALESCE(SUM(total_errors), 0) AS total_errors,
                COALESCE(SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END), 0) AS banned,
                COALESCE(SUM(CASE WHEN is_suspended = 1 THEN 1 ELSE 0 END), 0) AS suspended
            FROM users
            """
        ).fetchone()
        conn.close()
    return dict(row)


def ban_user(user_id: int, reason: str = "") -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?",
            (reason, user_id),
        )
        conn.commit()
        conn.close()


def unban_user(user_id: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET is_banned = 0, ban_reason = '' WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        conn.close()


def suspend_user(user_id: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute("UPDATE users SET is_suspended = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()


def unsuspend_user(user_id: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute("UPDATE users SET is_suspended = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()


def set_user_max_cards(user_id: int, value: int | None) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET custom_max_cards = ? WHERE user_id = ?",
            (value, user_id),
        )
        conn.commit()
        conn.close()


def set_user_delay(user_id: int, value: float | None) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET custom_delay = ? WHERE user_id = ?",
            (value, user_id),
        )
        conn.commit()
        conn.close()


def set_user_daily_limit(user_id: int, value: int | None) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "UPDATE users SET daily_limit = ? WHERE user_id = ?",
            (value, user_id),
        )
        conn.commit()
        conn.close()


def reset_user_stats_db(user_id: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            """
            UPDATE users SET
                total_sessions = 0, total_cards = 0, total_3ds = 0,
                total_failed = 0, total_errors = 0, daily_used = 0
            WHERE user_id = ?
            """,
            (user_id,),
        )
        conn.commit()
        conn.close()


def record_session(
    user_id: int,
    cards: int,
    success_3ds: int,
    failed: int,
    errors: int,
) -> None:
    with _lock:
        conn = _conn()
        _reset_daily_if_needed(conn, user_id)
        conn.execute(
            """
            UPDATE users SET
                total_sessions = total_sessions + 1,
                total_cards    = total_cards + ?,
                total_3ds      = total_3ds + ?,
                total_failed   = total_failed + ?,
                total_errors   = total_errors + ?,
                daily_used     = daily_used + ?,
                last_active    = ?
            WHERE user_id = ?
            """,
            (cards, success_3ds, failed, errors, cards, datetime.now().isoformat(), user_id),
        )
        conn.commit()
        conn.close()


def get_checker_delay(user_id: int, checker_mode: str | None = None) -> float:
    """Delay between cards for the given checker mode."""
    settings = get_settings()
    user = get_user(user_id)
    if checker_mode is None:
        checker_mode = (user or {}).get("checker_mode") or "otp"

    if user and user["custom_delay"] is not None:
        return float(user["custom_delay"])

    key = "live_delay" if checker_mode == "live" else "global_delay"
    return float(settings.get(key, "1.0"))


def get_user_limits(user_id: int) -> tuple[int, float, int]:
    """max_cards, delay, daily_limit (0 = unlimited)"""
    settings = get_settings()
    global_max = int(settings["global_max_cards"])
    default_daily = int(settings["default_daily_limit"])

    user = get_user(user_id)
    if not user:
        return global_max, get_checker_delay(user_id), default_daily

    max_cards = user["custom_max_cards"] if user["custom_max_cards"] is not None else global_max
    delay = get_checker_delay(user_id, user.get("checker_mode") or "otp")
    daily = user["daily_limit"] if user["daily_limit"] is not None else default_daily
    return max_cards, delay, daily


def check_access(user_id: int, cards_count: int = 0) -> tuple[bool, str | None, dict]:
    """Returns (allowed, error_key, error_kwargs). error_key None if allowed."""
    if get_setting("bot_enabled", "1") != "1":
        return False, "bot_disabled", {}

    if get_setting("maintenance_mode", "0") == "1":
        custom = get_setting("maintenance_message", "")
        if custom and custom != DEFAULT_SETTINGS["maintenance_message"]:
            return False, "custom", {"msg": custom}
        return False, "maintenance", {}

    user = get_user(user_id)
    if not user:
        return True, None, {}

    if user["is_banned"]:
        reason = user["ban_reason"] or ""
        return False, "banned", {"reason": reason}

    if user["is_suspended"]:
        return False, "suspended", {}

    if cards_count > 0:
        max_cards, _, daily_limit = get_user_limits(user_id)
        if cards_count > max_cards:
            return False, "max_cards", {"max": max_cards}

        with _lock:
            conn = _conn()
            _reset_daily_if_needed(conn, user_id)
            row = conn.execute(
                "SELECT daily_used FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            conn.close()

        if daily_limit > 0 and row:
            remaining = daily_limit - row["daily_used"]
            if cards_count > remaining:
                return False, "daily_limit", {
                    "limit": daily_limit,
                    "remaining": max(0, remaining),
                }

    return True, None, {}


def get_broadcast_targets() -> list[int]:
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT user_id FROM users WHERE is_banned = 0 AND is_suspended = 0"
        ).fetchall()
        conn.close()
    return [r["user_id"] for r in rows]


def create_db_backup(dest_path: str) -> bool:
    """Safe SQLite backup while the bot is running."""
    _ensure_db_dir()
    with _lock:
        if not os.path.isfile(DB_PATH):
            return False
        src = _conn()
        try:
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    return True


def format_user_label(user: dict) -> str:
    name = user.get("first_name") or "?"
    uname = f"@{user['username']}" if user.get("username") else f"ID:{user['user_id']}"
    flags = ""
    if user.get("is_banned"):
        flags += "🚫"
    if user.get("is_suspended"):
        flags += "⏸"
    return f"{flags}{name} ({uname})"
