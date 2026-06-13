# -*- coding: utf-8 -*-
"""OTP advanced verification — Arcot 3DS2 challenge page analysis."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

ARCOT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "ar,en-US;q=0.9,en;q=0.8",
    "cache-control": "max-age=0",
    "content-type": "application/x-www-form-urlencoded",
    "dnt": "1",
    "origin": "https://pay.realexpayments.com",
    "referer": "https://pay.realexpayments.com/",
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "iframe",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "upgrade-insecure-requests": "1",
    "user-agent": UA,
}

FAIL_CHECKS: list[tuple[str, int]] = [
    (r'id=["\']error_form["\']', 8),
    (r'class=["\'][^"\']*txnerrorform', 8),
    (r"authentication failed", 10),
    (r"auth(?:entication)?\s+unsuccessful", 8),
    (r"can(?:'|&#39;)t complete your transaction", 7),
    (r"cannot complete your transaction", 7),
    (r"transaction failed", 6),
    (r"transfailure-arrow-image", 6),
    (r"container-body-error-submit", 6),
    (r"we are unable to authenticate", 7),
    (r"unable to verify", 5),
    (r"not authenticated", 5),
    (r"challenge.?cancelled", 5),
]

PASS_CHECKS: list[tuple[str, int, str]] = [
    (r'class=["\']text_input["\']', 6, "otp_input"),
    (r'name=["\']otp(?:code|value)?["\']', 6, "otp_input"),
    (r'name=["\']challenge(?:dataentry|code)?["\']', 6, "otp_input"),
    (r'name=["\'](?:sms|email)?code["\']', 5, "otp_input"),
    (r"type=['\"]text['\"][^>]*(?:otp|code|verification)", 5, "otp_input"),
    (r"type=['\"]tel['\"]", 4, "otp_input"),
    (r"container-body-header-desc-otp-ext", 5, "otp_input"),
    (r"one.?time password", 5, "otp_input"),
    (r"enter (?:the )?(?:code|otp|verification)", 5, "otp_input"),
    (r"mpn_continue", 6, "oob_push"),
    (r"push notification", 6, "oob_push"),
    (r"approve this payment", 6, "oob_push"),
    (r"log on to .* app to approve", 6, "oob_push"),
    (r"so oob template", 5, "oob_push"),
    (r'transaction_type:\s*["\']oob["\']', 6, "oob_push"),
    (r"cbsurl", 4, "oob_push"),
    (r"id=['\"]verify_form['\"]", 4, "generic_challenge"),
    (r"class=['\"]txninputform", 3, "generic_challenge"),
    (r"container-body-txninput-verify", 5, "otp_input"),
    (r"id=['\"]resend_form['\"]", 4, "sms_email"),
    (r"resend_challenge", 4, "sms_email"),
    (r"channelselection", 3, "sms_email"),
    (r"resend secure code", 4, "sms_email"),
    (r"select the option to which we can resend", 4, "sms_email"),
    (r"challenge-info-header-h1", 3, "generic_challenge"),
]


@dataclass(frozen=True)
class ChallengeAnalysis:
    verdict: str  # FULL_3D | AUTH_FAILED | UNCLEAR | FETCH_ERROR
    method: str = ""
    detail: str = ""
    fail_score: int = 0
    pass_score: int = 0


def _score_patterns(html: str, low: str) -> tuple[int, int, str]:
    fail_score = 0
    for pattern, weight in FAIL_CHECKS:
        if re.search(pattern, html, re.I | re.S):
            fail_score += weight

    pass_score = 0
    method = ""
    method_weight = 0
    for pattern, weight, tag in PASS_CHECKS:
        if re.search(pattern, html, re.I | re.S):
            pass_score += weight
            if weight > method_weight:
                method_weight = weight
                method = tag

    return fail_score, pass_score, method


def classify_challenge_html(html: str) -> ChallengeAnalysis:
    if not html or len(html.strip()) < 80:
        return ChallengeAnalysis("UNCLEAR", detail="empty_response")

    low = html.lower()
    fail_score, pass_score, method = _score_patterns(html, low)

    has_error_form = bool(re.search(r'id=["\']error_form["\']', html, re.I))
    has_verify_form = bool(re.search(r'id=["\']verify_form["\']', html, re.I))
    has_text_input = bool(re.search(r'class=["\']text_input["\']', html, re.I))
    has_oob = "push notification" in low or "mpn_continue" in low or "so oob template" in low

    if fail_score >= 8 or (has_error_form and "authentication failed" in low):
        return ChallengeAnalysis("AUTH_FAILED", detail="auth_failed_page", fail_score=fail_score, pass_score=pass_score)

    if has_error_form and not has_verify_form and pass_score < 4:
        return ChallengeAnalysis("AUTH_FAILED", detail="error_form_only", fail_score=fail_score, pass_score=pass_score)

    if pass_score >= 6 and fail_score == 0:
        return ChallengeAnalysis("FULL_3D", method=method or "generic_challenge", fail_score=fail_score, pass_score=pass_score)

    if has_verify_form and fail_score == 0 and (has_text_input or has_oob or pass_score >= 4):
        return ChallengeAnalysis(
            "FULL_3D",
            method=method or ("oob_push" if has_oob else "generic_challenge"),
            fail_score=fail_score,
            pass_score=pass_score,
        )

    if has_verify_form and fail_score == 0 and pass_score >= 3:
        return ChallengeAnalysis("FULL_3D", method=method or "generic_challenge", fail_score=fail_score, pass_score=pass_score)

    if fail_score > 0 and pass_score > 0:
        if fail_score >= pass_score:
            return ChallengeAnalysis("AUTH_FAILED", detail="mixed_fail_wins", fail_score=fail_score, pass_score=pass_score)
        return ChallengeAnalysis("FULL_3D", method=method or "generic_challenge", fail_score=fail_score, pass_score=pass_score)

    if pass_score >= 4 and fail_score <= 2:
        return ChallengeAnalysis("FULL_3D", method=method or "generic_challenge", fail_score=fail_score, pass_score=pass_score)

    return ChallengeAnalysis("UNCLEAR", detail="insufficient_signals", fail_score=fail_score, pass_score=pass_score)


def fetch_arcot_challenge(verify_result: dict, referer: str) -> tuple[str, ChallengeAnalysis]:
    """POST creq to Arcot and classify the returned HTML."""
    challenge_url = (verify_result or {}).get("challengeRequestUrl") or ""
    encoded_creq = (verify_result or {}).get("encodedCreq") or ""
    session_data = (verify_result or {}).get("threeDSSessionData") or ""

    if not challenge_url or not encoded_creq:
        return "", ChallengeAnalysis("UNCLEAR", detail="missing_creq_data")

    headers = {**ARCOT_HEADERS, "Referer": referer}
    data = {"creq": encoded_creq}
    if session_data:
        data["threeDSSessionData"] = session_data

    try:
        resp = requests.post(challenge_url, headers=headers, data=data, timeout=30)
        html = resp.text or ""
        if resp.status_code >= 400:
            log.warning("Arcot challenge HTTP %s len=%d", resp.status_code, len(html))
            return html, ChallengeAnalysis("FETCH_ERROR", detail=f"http_{resp.status_code}")
        analysis = classify_challenge_html(html)
        log.info(
            "Arcot advanced verdict=%s method=%s fail=%s pass=%s detail=%s",
            analysis.verdict,
            analysis.method,
            analysis.fail_score,
            analysis.pass_score,
            analysis.detail,
        )
        return html, analysis
    except Exception as exc:
        log.warning("Arcot challenge fetch failed: %s", exc)
        return "", ChallengeAnalysis("FETCH_ERROR", detail=str(exc)[:80])


def method_label(method: str) -> str:
    labels = {
        "otp_input": "OTP / Code Input",
        "oob_push": "App Push Notification",
        "sms_email": "SMS / Email OTP",
        "generic_challenge": "3DS Challenge",
    }
    return labels.get(method, method or "3DS Challenge")
