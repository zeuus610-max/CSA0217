"""Local Gmail connector for Pocket. Tokens remain in process memory."""
from __future__ import annotations

import base64
import html
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8765/api/auth/callback")
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"
TOKENS: dict[str, dict] = {}
OAUTH_STATES: dict[str, str] = {}
app = FastAPI(title="Pocket FamPay Gmail connector", docs_url=None, redoc_url=None)


def _session(request: Request) -> str | None:
    return request.cookies.get("pocket_session")


def _token(session: str | None) -> dict:
    if not session or session not in TOKENS:
        raise HTTPException(401, "Connect Gmail first.")
    return TOKENS[session]


def _post_form(url: str, values: dict) -> dict:
    data = urllib.parse.urlencode(values).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read())
    except Exception as exc:
        raise HTTPException(502, "Google OAuth request failed. Check the OAuth setup and try again.") from exc


def _gmail_get(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read())
    except Exception as exc:
        raise HTTPException(502, "Gmail could not be read. Reconnect your Google account and try again.") from exc


def _fresh_access(session: str) -> str:
    token = TOKENS[session]
    if token.get("expires_at", 0) < time.time() + 60:
        if not token.get("refresh_token"):
            raise HTTPException(401, "Gmail authorization expired. Reconnect Gmail.")
        refreshed = _post_form("https://oauth2.googleapis.com/token", {
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
        })
        token["access_token"] = refreshed["access_token"]
        token["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
    return token["access_token"]


def _decode_part(part: dict) -> str:
    body = part.get("body", {}).get("data")
    if body:
        try:
            return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", "ignore")
        except Exception:
            return ""
    return "".join(_decode_part(child) for child in part.get("parts", []))


def _plain_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", value)).replace("\xa0", " ")


def _parse_amount(text: str) -> float | None:
    # Favor amounts explicitly marked as a transaction debit; avoid treating balances as spend.
    patterns = [
        r"(?i)(?:spent|paid|purchase(?:d)?|debited|transaction\s+of|amount)\D{0,24}(?:₹|INR\s?|Rs\.?\s?)([\d,]+(?:\.\d{1,2})?)",
        r"(?:₹|INR\s?|Rs\.?\s?)([\d,]+(?:\.\d{1,2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                value = float(match.group(1).replace(",", ""))
                if 0 < value <= 10_000_000:
                    return value
            except ValueError:
                pass
    return None


def _category(text: str) -> str:
    value = text.lower()
    if any(x in value for x in ("food", "cafe", "coffee", "restaurant", "pizza", "swiggy", "zomato", "grocery")):
        return "Food & drink"
    if any(x in value for x in ("metro", "uber", "ola", "ride", "fuel", "transport")):
        return "Transport"
    if any(x in value for x in ("myntra", "amazon", "flipkart", "shopping", "store")):
        return "Shopping"
    if any(x in value for x in ("game", "playstation", "netflix", "spotify", "entertainment")):
        return "Entertainment"
    return "Other"


@app.get("/")
def home():
    return FileResponse(ROOT / "index.html")


@app.get("/api/status")
def status(request: Request):
    configured = bool(CLIENT_ID and CLIENT_SECRET)
    connected = bool(_session(request) in TOKENS) if _session(request) else False
    return {"configured": configured, "connected": connected}


@app.get("/api/auth/start")
def auth_start(request: Request):
    if not CLIENT_ID or not CLIENT_SECRET:
        return RedirectResponse("/?setup=oauth", status_code=303)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    OAUTH_STATES[state] = nonce
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        "state": state,
    })
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + params, status_code=303)
    response.set_cookie("pocket_oauth_state", state, httponly=True, samesite="lax", max_age=600, path="/")
    response.set_cookie("pocket_oauth_session", nonce, httponly=True, samesite="lax", max_age=600, path="/")
    return response


@app.get("/api/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/?gmail_error=cancelled", status_code=303)
    if not state or state != request.cookies.get("pocket_oauth_state") or state not in OAUTH_STATES:
        raise HTTPException(400, "OAuth state check failed. Restart Gmail connection.")
    nonce = OAUTH_STATES.pop(state)
    if nonce != request.cookies.get("pocket_oauth_session"):
        raise HTTPException(400, "OAuth session check failed. Restart Gmail connection.")
    result = _post_form("https://oauth2.googleapis.com/token", {
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    })
    session = secrets.token_urlsafe(32)
    TOKENS[session] = {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
        "expires_at": time.time() + int(result.get("expires_in", 3600)),
    }
    response = RedirectResponse("/?connected=1", status_code=303)
    response.delete_cookie("pocket_oauth_state", path="/")
    response.delete_cookie("pocket_oauth_session", path="/")
    response.set_cookie("pocket_session", session, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path="/")
    return response


@app.get("/api/sync")
def sync(request: Request):
    session = _session(request)
    token = _token(session)
    access = _fresh_access(session)
    query = urllib.parse.urlencode({"q": 'newer_than:90d {FamPay "Fam Pay"}', "maxResults": "100"})
    result = _gmail_get("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + query, access)
    transactions = []
    for item in result.get("messages", []):
        detail = _gmail_get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}?format=full", access)
        payload = detail.get("payload", {})
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}
        subject = headers.get("subject", "FamPay transaction")
        sender = headers.get("from", "")
        text = _plain_text(_decode_part(payload))
        searchable = " ".join((subject, sender, detail.get("snippet", ""), text))
        if not re.search(r"fampay|fam\s*pay", searchable, re.I):
            continue
        # Do not add top-ups, refunds, or incoming transfers to spend totals.
        if re.search(r"(?i)\b(refund|cashback|received|credited|added money|add money|top.?up)\b", subject + " " + text[:800]):
            continue
        amount = _parse_amount(searchable)
        if amount is None:
            continue
        internal_ms = int(detail.get("internalDate", "0"))
        date = datetime.fromtimestamp(internal_ms / 1000, timezone.utc).astimezone().isoformat() if internal_ms else ""
        merchant = re.sub(r"(?i)\s*[-|].*$", "", subject).strip() or "FamPay purchase"
        transactions.append({"id": item["id"], "name": merchant[:70], "amount": amount, "category": _category(searchable), "date": date, "source": "Gmail"})
    transactions.sort(key=lambda x: x["date"], reverse=True)
    return {"count": len(transactions), "transactions": transactions, "synced_at": datetime.now().astimezone().isoformat()}


@app.post("/api/disconnect")
def disconnect(request: Request):
    session = _session(request)
    if session:
        TOKENS.pop(session, None)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("pocket_session", path="/")
    return response
