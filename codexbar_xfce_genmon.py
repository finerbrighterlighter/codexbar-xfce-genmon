#!/usr/bin/env python3

import base64
import fcntl
import html
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.sax.saxutils as saxutils
from pathlib import Path


MODE = sys.argv[1]
ACTION = sys.argv[2]
CREDS_PATH = Path.home() / ".codex" / "auth.json"
TOKEN_URL = "https://auth.openai.com/oauth/token"
API_URL = "https://chatgpt.com/backend-api/wham/usage"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_BUFFER = 300
CACHE_TTL = 60
SESSION_WINDOW = 5 * 3600
WEEKLY_WINDOW = 7 * 24 * 3600
CACHE_DIR = Path.home() / ".cache" / "codexbar-xfce-genmon"
CACHE_FILE = CACHE_DIR / "usage.json"
LOCK_FILE = CACHE_DIR / ".fetch.lock"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


ROTATE_SECONDS = max(1, env_int("CODEXBAR_XFCE_ROTATE_SECONDS", 15))
ROTATE_MODES = [
    mode.strip()
    for mode in os.environ.get(
        "CODEXBAR_XFCE_ROTATE_MODES",
        "remaining,weekly,combined,credits",
    ).split(",")
    if mode.strip()
]
SHOW_ICON = os.environ.get("CODEXBAR_XFCE_SHOW_ICON", "1") != "0"
COLOR_MAP = {
    "low": os.environ.get("CODEXBAR_XFCE_COLOR_LOW", "#98c379"),
    "mid": os.environ.get("CODEXBAR_XFCE_COLOR_MID", "#e5c07b"),
    "high": os.environ.get("CODEXBAR_XFCE_COLOR_HIGH", "#d19a66"),
    "critical": os.environ.get("CODEXBAR_XFCE_COLOR_CRITICAL", "#e06c75"),
}
ICON_MAP = {
    "remaining": os.environ.get("CODEXBAR_XFCE_ICON_REMAINING", "applications-development"),
    "used": os.environ.get("CODEXBAR_XFCE_ICON_USED", "applications-development"),
    "weekly": os.environ.get("CODEXBAR_XFCE_ICON_WEEKLY", "x-office-calendar"),
    "combined": os.environ.get("CODEXBAR_XFCE_ICON_COMBINED", "view-dual"),
    "credits": os.environ.get("CODEXBAR_XFCE_ICON_CREDITS", "wallet"),
    "rotate": os.environ.get("CODEXBAR_XFCE_ICON_ROTATE", "view-refresh"),
}


def strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(text).strip()


def pango(text: str, color: str, weight: str = "Semibold") -> str:
    return f"<span fgcolor='{saxutils.escape(color)}' weight='{saxutils.escape(weight)}'>{saxutils.escape(text)}</span>"


def tooltip_line(label: str, value: str, color: str) -> str:
    return f"<span fgcolor='{saxutils.escape(color)}' weight='Bold'>{saxutils.escape(label)}:</span> {saxutils.escape(value)}"


def pick_rotate_mode() -> str:
    if not ROTATE_MODES:
        return "remaining"
    slot = int(time.time()) // ROTATE_SECONDS
    return ROTATE_MODES[slot % len(ROTATE_MODES)]


def script_path() -> str:
    return os.environ.get(
        "CODEXBAR_XFCE_WRAPPER",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "codexbar-xfce-genmon"),
    )


def default_clicks() -> tuple[str, str]:
    popup_mode = "rotate" if MODE == "rotate" else resolved_mode_for_click
    popup_command = f"{shlex.quote(script_path())} popup {shlex.quote(popup_mode)}"
    icon_click = os.environ.get("CODEXBAR_XFCE_ICON_CLICK", popup_command)
    text_click = os.environ.get("CODEXBAR_XFCE_TEXT_CLICK", "")
    if not text_click:
        text_click = popup_command
    return icon_click, text_click


def notify_popup(title: str, body: str) -> None:
    subprocess.run(["notify-send", title, body], check=False)


def popup_body(title: str, tooltip: str) -> str:
    lines = [line for line in strip_markup(tooltip).splitlines() if line.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    return "\n".join(lines)


def text_for_mode(mode: str, data: dict[str, str]) -> str:
    if mode == "remaining":
        return f"{data['session_remaining']}% left · {data['session_reset']}"
    if mode == "used":
        return f"{data['session_used']}% used · {data['session_reset']}"
    if mode == "weekly":
        return f"W {data['weekly_remaining']}% left · {data['weekly_reset']}"
    if mode == "combined":
        return f"S {data['session_remaining']}% · W {data['weekly_remaining']}%"
    if mode == "credits":
        return f"L {data['credits_local']} · C {data['credits_cloud']}"
    raise ValueError(
        f"unknown mode '{mode}' (expected remaining, used, weekly, combined, credits, rotate)"
    )


def icon_for_mode(mode: str) -> str:
    return ICON_MAP.get(mode, ICON_MAP["remaining"])


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as handle:
        json.dump(data, handle)
        temp_name = handle.name
    os.replace(temp_name, path)


def read_json(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def jwt_decode(token: str) -> dict:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    payload = payload.replace("-", "+").replace("_", "/")
    try:
        return json.loads(base64.b64decode(payload).decode())
    except Exception:
        return {}


def jwt_exp(token: str) -> int:
    payload = jwt_decode(token)
    try:
        return int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return 0


def plan_from_id_token(id_token: str) -> str:
    payload = jwt_decode(id_token)
    auth = payload.get("https://api.openai.com/auth", {})
    plan_type = auth.get("chatgpt_plan_type")
    return str(plan_type).capitalize() if plan_type else "Codex"


def countdown(ts: int | str | None) -> str:
    try:
        if ts in (None, 0, "0", "", "null"):
            return "-"
        diff = int(ts) - int(time.time())
    except (TypeError, ValueError):
        return "-"
    if diff <= 0:
        return "now"
    days, rem = divmod(diff, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    return f"{hours}h {mins:02d}m"


def calc_pacing(usage_pct: int, reset_at: int | str | None, window_s: int) -> tuple[int, str]:
    try:
        reset = int(reset_at or 0)
    except (TypeError, ValueError):
        return 0, "on track"
    if reset <= 0 or window_s <= 0:
        return 0, "on track"
    secs_left = reset - int(time.time())
    elapsed_pct = max(0, min(100, int((window_s - secs_left) * 100 / window_s)))
    if elapsed_pct <= 0:
        return elapsed_pct, "on track"
    pacing_x100 = int(usage_pct * 100 / elapsed_pct)
    if pacing_x100 > 100:
        return elapsed_pct, f"{min(pacing_x100 - 100, 999)}% ahead"
    if pacing_x100 < 100:
        return elapsed_pct, f"{min(100 - pacing_x100, 999)}% under"
    return elapsed_pct, "on track"


def http_json(url: str, *, method: str = "GET", headers: dict | None = None, body: dict | None = None, timeout: int = 10) -> dict:
    data = None
    request_headers = headers.copy() if headers else {}
    if body is not None:
        data = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def refresh_tokens(creds: dict) -> dict:
    tokens = creds.get("tokens", {})
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Missing refresh token. Run codex login.")
    payload = {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "openid profile email",
    }
    refreshed = http_json(TOKEN_URL, method="POST", body=payload, timeout=25)
    new_tokens = creds.setdefault("tokens", {})
    new_tokens["access_token"] = refreshed.get("access_token", new_tokens.get("access_token", ""))
    if refreshed.get("refresh_token"):
        new_tokens["refresh_token"] = refreshed["refresh_token"]
    if refreshed.get("id_token"):
        new_tokens["id_token"] = refreshed["id_token"]
    creds["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    atomic_write_json(CREDS_PATH, creds)
    return creds


def fresh_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    if time.time() - CACHE_FILE.stat().st_mtime >= CACHE_TTL:
        return None
    return read_json(CACHE_FILE)


def stale_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    age = time.time() - CACHE_FILE.stat().st_mtime
    if age > WEEKLY_WINDOW:
        return None
    return read_json(CACHE_FILE)


def fetch_usage_data() -> tuple[dict, str]:
    if not CREDS_PATH.exists():
        raise RuntimeError("No credentials. Run codex login.")
    creds = read_json(CREDS_PATH)
    cached = fresh_cache()
    if cached is not None:
        return cached, plan_from_id_token(creds.get("tokens", {}).get("id_token", ""))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache()
        if cached is not None:
            return cached, plan_from_id_token(creds.get("tokens", {}).get("id_token", ""))

        creds = read_json(CREDS_PATH)
        tokens = creds.get("tokens", {})
        access_token = tokens.get("access_token", "")
        if not access_token:
            raise RuntimeError("No access token. Run codex login.")

        if jwt_exp(access_token) < int(time.time()) + REFRESH_BUFFER:
            try:
                creds = refresh_tokens(creds)
                tokens = creds.get("tokens", {})
                access_token = tokens.get("access_token", access_token)
            except Exception as exc:
                cached = stale_cache()
                if cached is not None:
                    return cached, plan_from_id_token(tokens.get("id_token", ""))
                if is_transient_error(exc):
                    raise RuntimeError("Codex usage is waiting for network.") from exc
                raise RuntimeError("Token refresh failed. Run codex login.") from exc

        headers = {"Authorization": f"Bearer {access_token}"}
        account_id = tokens.get("account_id")
        if account_id:
            headers["chatgpt-account-id"] = str(account_id)

        try:
            usage = http_json(API_URL, headers=headers, timeout=10)
            if "rate_limit" not in usage:
                raise RuntimeError("Usage response missing rate_limit")
            atomic_write_json(CACHE_FILE, usage)
            api_plan = usage.get("plan_type")
            plan = str(api_plan).capitalize() if api_plan else plan_from_id_token(tokens.get("id_token", ""))
            return usage, plan
        except Exception as exc:
            cached = stale_cache()
            if cached is not None:
                return cached, plan_from_id_token(tokens.get("id_token", ""))
            if is_transient_error(exc):
                raise RuntimeError("Codex usage is waiting for network.") from exc
            raise RuntimeError("API request failed.") from exc


def format_message(usage: dict, plan: str) -> tuple[str, str, str, str, str, str]:
    rate_limit = usage.get("rate_limit") or {}
    primary_window = rate_limit.get("primary_window") or {}
    secondary_window = rate_limit.get("secondary_window") or {}
    review_limit = usage.get("code_review_rate_limit") or {}
    review_window = review_limit.get("primary_window") or {}
    credits = usage.get("credits") or {}

    session_used = int(float(primary_window.get("used_percent", 0)))
    session_reset_at = primary_window.get("reset_at", 0)
    weekly_used = int(float(secondary_window.get("used_percent", 0)))
    weekly_reset_at = secondary_window.get("reset_at", 0)

    review_used = int(float(review_window.get("used_percent", 0)))
    review_reset_at = review_window.get("reset_at", 0)

    local_range = credits.get("approx_local_messages") or [0, 0]
    cloud_range = credits.get("approx_cloud_messages") or [0, 0]
    credits_local = str(local_range[0]) if local_range[0] == local_range[-1] else f"{local_range[0]}-{local_range[-1]}"
    credits_cloud = str(cloud_range[0]) if cloud_range[0] == cloud_range[-1] else f"{cloud_range[0]}-{cloud_range[-1]}"

    session_remaining = max(0, 100 - session_used)
    weekly_remaining = max(0, 100 - weekly_used)
    review_remaining = max(0, 100 - review_used)
    session_reset = countdown(session_reset_at)
    weekly_reset = countdown(weekly_reset_at)
    review_reset = countdown(review_reset_at) if review_reset_at else ""
    session_elapsed, _ = calc_pacing(session_used, session_reset_at, SESSION_WINDOW)
    weekly_elapsed, _ = calc_pacing(weekly_used, weekly_reset_at, WEEKLY_WINDOW)
    review_elapsed, _ = calc_pacing(review_used, review_reset_at, WEEKLY_WINDOW)

    max_pct = max(session_used, weekly_used, review_used)
    if max_pct >= 90:
        status_class = "critical"
    elif max_pct >= 75:
        status_class = "high"
    elif max_pct >= 50:
        status_class = "mid"
    else:
        status_class = "low"

    resolved_mode = pick_rotate_mode() if MODE == "rotate" else MODE
    fields = {
        "session_used": str(session_used),
        "session_remaining": str(session_remaining),
        "session_reset": session_reset,
        "session_elapsed": str(session_elapsed),
        "weekly_used": str(weekly_used),
        "weekly_remaining": str(weekly_remaining),
        "weekly_reset": weekly_reset,
        "weekly_elapsed": str(weekly_elapsed),
        "review_used": str(review_used),
        "review_remaining": str(review_remaining),
        "review_reset": review_reset,
        "review_elapsed": str(review_elapsed),
        "credits_local": credits_local,
        "credits_cloud": credits_cloud,
    }

    color = COLOR_MAP.get(status_class, COLOR_MAP["low"])
    text = text_for_mode(resolved_mode, fields)
    icon = icon_for_mode(resolved_mode)
    tooltip_lines = [
        f"<span weight='Bold'>{saxutils.escape(plan or 'Codex')}</span>",
        tooltip_line("Mode", resolved_mode, color),
        tooltip_line(
            "Session",
            f"{session_remaining}% remaining ({session_used}% used, {session_elapsed}% elapsed)",
            color,
        ),
        tooltip_line("Session Reset", session_reset, color),
        tooltip_line(
            "Weekly",
            f"{weekly_remaining}% remaining ({weekly_used}% used, {weekly_elapsed}% elapsed)",
            color,
        ),
        tooltip_line("Weekly Reset", weekly_reset, color),
    ]
    if review_reset:
        tooltip_lines.append(
            tooltip_line(
                "Review",
                f"{review_remaining}% remaining ({review_used}% used, {review_elapsed}% elapsed)",
                color,
            )
        )
        tooltip_lines.append(tooltip_line("Review Reset", review_reset, color))
    tooltip_lines.append(tooltip_line("Credits", f"local {credits_local}, cloud {credits_cloud}", color))
    if MODE == "rotate":
        tooltip_lines.append(tooltip_line("Rotate Every", f"{ROTATE_SECONDS}s", color))
    return text, "\n".join(tooltip_lines), icon, color, resolved_mode, plan or "Codex"


try:
    usage_data, plan = fetch_usage_data()
    text, tooltip, icon, color, resolved_mode_for_click, title = format_message(usage_data, plan)
    icon_click, text_click = default_clicks()
except Exception as exc:
    color = COLOR_MAP["critical"]
    text = "Codex N/A"
    tooltip = saxutils.escape(str(exc))
    icon = ICON_MAP["remaining"]
    title = "Codex"
    resolved_mode_for_click = "remaining"
    icon_click, text_click = default_clicks()

if ACTION == "popup":
    notify_popup(title, popup_body(title, tooltip))
    raise SystemExit(0)

if SHOW_ICON:
    print(f"<icon>{saxutils.escape(icon)}</icon>")
    if icon_click:
        print(f"<click>{saxutils.escape(icon_click)}</click>")
print(f"<txt>{pango(text, color)}</txt>")
if text_click:
    print(f"<txtclick>{saxutils.escape(text_click)}</txtclick>")
print(f"<tool>{tooltip}</tool>")
