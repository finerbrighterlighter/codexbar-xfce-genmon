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
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------

MODE = sys.argv[1]
ACTION = sys.argv[2]

# Which sources to show: "codex", "claude", or "both" (default)
SOURCE = os.environ.get("CODEXBAR_XFCE_SOURCE", "both").lower()
if SOURCE not in ("codex", "claude", "both"):
    SOURCE = "both"

SHOW_CODEX = SOURCE in ("codex", "both")
SHOW_CLAUDE = SOURCE in ("claude", "both")

# ---------------------------------------------------------------------------
# Codex auth / API constants
# ---------------------------------------------------------------------------

CODEX_CREDS_PATH = Path.home() / ".codex" / "auth.json"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_API_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# ---------------------------------------------------------------------------
# Claude auth / API constants
# ---------------------------------------------------------------------------

CLAUDE_CREDS_DIR = Path.home() / ".config" / "claude-usage-bar"
CLAUDE_CREDS_FILE = CLAUDE_CREDS_DIR / "credentials.json"
CLAUDE_TOKEN_FILE = CLAUDE_CREDS_DIR / "token"          # legacy plain-text fallback
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_API_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_BETA_HEADER = "oauth-2025-04-20"

# ---------------------------------------------------------------------------
# Shared cache / timing constants
# ---------------------------------------------------------------------------

REFRESH_BUFFER = 300        # seconds before expiry to trigger refresh
CACHE_TTL = 60              # seconds a fresh cache entry is valid
SESSION_WINDOW = 5 * 3600   # fallback if API doesn't supply window_seconds
WEEKLY_WINDOW = 7 * 24 * 3600
REVIEW_WINDOW = WEEKLY_WINDOW   # fallback for code-review window

CODEX_CACHE_DIR = Path.home() / ".cache" / "codexbar-xfce-genmon"
CODEX_CACHE_FILE = CODEX_CACHE_DIR / "usage.json"
CODEX_LOCK_FILE = CODEX_CACHE_DIR / ".fetch.lock"

CLAUDE_CACHE_DIR = Path.home() / ".cache" / "codexbar-xfce-genmon"
CLAUDE_CACHE_FILE = CLAUDE_CACHE_DIR / "claude_usage.json"
CLAUDE_LOCK_FILE = CLAUDE_CACHE_DIR / ".claude_fetch.lock"

# ---------------------------------------------------------------------------
# User-configurable env vars
# ---------------------------------------------------------------------------


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


ROTATE_SECONDS = max(1, env_int("CODEXBAR_XFCE_ROTATE_SECONDS", 15))

# Default rotation includes both sources when both are active
_default_rotate: list[str] = []
if SHOW_CODEX:
    _default_rotate += ["codex:remaining", "codex:weekly"]
if SHOW_CLAUDE:
    _default_rotate += ["claude:5h", "claude:7d"]
if SHOW_CODEX and SHOW_CLAUDE:
    _default_rotate += ["combined"]
elif SHOW_CODEX:
    _default_rotate += ["codex:combined", "codex:credits"]
elif SHOW_CLAUDE:
    _default_rotate += ["claude:extra"]

ROTATE_MODES = [
    m.strip()
    for m in os.environ.get(
        "CODEXBAR_XFCE_ROTATE_MODES",
        ",".join(_default_rotate),
    ).split(",")
    if m.strip()
]

SHOW_ICON = os.environ.get("CODEXBAR_XFCE_SHOW_ICON", "1") != "0"

COLOR_MAP = {
    "low":      os.environ.get("CODEXBAR_XFCE_COLOR_LOW",      "#98c379"),
    "mid":      os.environ.get("CODEXBAR_XFCE_COLOR_MID",      "#e5c07b"),
    "high":     os.environ.get("CODEXBAR_XFCE_COLOR_HIGH",     "#d19a66"),
    "critical": os.environ.get("CODEXBAR_XFCE_COLOR_CRITICAL", "#e06c75"),
}

ICON_MAP = {
    # Codex icons
    "codex:remaining": os.environ.get("CODEXBAR_XFCE_ICON_REMAINING", "applications-development"),
    "codex:used":      os.environ.get("CODEXBAR_XFCE_ICON_USED",      "go-down"),
    "codex:weekly":    os.environ.get("CODEXBAR_XFCE_ICON_WEEKLY",    "x-office-calendar"),
    "codex:combined":  os.environ.get("CODEXBAR_XFCE_ICON_COMBINED",  "view-dual"),
    "codex:credits":   os.environ.get("CODEXBAR_XFCE_ICON_CREDITS",   "wallet"),
    # Claude icons
    "claude:5h":       os.environ.get("CODEXBAR_XFCE_ICON_CLAUDE_5H",      "appointment-new"),
    "claude:7d":       os.environ.get("CODEXBAR_XFCE_ICON_CLAUDE_7D",      "x-office-calendar"),
    "claude:opus":     os.environ.get("CODEXBAR_XFCE_ICON_CLAUDE_OPUS",    "applications-science"),
    "claude:sonnet":   os.environ.get("CODEXBAR_XFCE_ICON_CLAUDE_SONNET",  "text-editor"),
    "claude:extra":    os.environ.get("CODEXBAR_XFCE_ICON_CLAUDE_EXTRA",   "wallet"),
    # Cross-source
    "combined":        os.environ.get("CODEXBAR_XFCE_ICON_BOTH_COMBINED",  "view-dual"),
    "rotate":          os.environ.get("CODEXBAR_XFCE_ICON_ROTATE",         "view-refresh"),
}


# ---------------------------------------------------------------------------
# Markup helpers
# ---------------------------------------------------------------------------


def strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(text).strip()


def pango(text: str, color: str, weight: str = "Semibold") -> str:
    return (
        f"<span fgcolor=\"{saxutils.escape(color)}\" weight=\"{saxutils.escape(weight)}\">"
        f"{saxutils.escape(text)}</span>"
    )


def tooltip_line(label: str, value: str, color: str) -> str:
    return (
        f"<span fgcolor=\"{saxutils.escape(color)}\" weight=\"Bold\">"
        f"{saxutils.escape(label)}:</span> {saxutils.escape(value)}"
    )


def tooltip_header(text: str) -> str:
    return f"<span weight=\"Bold\">{saxutils.escape(text)}</span>"


# ---------------------------------------------------------------------------
# Mode / rotation helpers
# ---------------------------------------------------------------------------


def pick_rotate_mode() -> str:
    if not ROTATE_MODES:
        return "codex:remaining" if SHOW_CODEX else "claude:5h"
    slot = int(time.time()) // ROTATE_SECONDS
    return ROTATE_MODES[slot % len(ROTATE_MODES)]


def script_path() -> str:
    return os.environ.get(
        "CODEXBAR_XFCE_WRAPPER",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "codexbar-xfce-genmon"),
    )


def default_clicks(resolved_mode: str) -> tuple[str, str]:
    popup_mode = "rotate" if MODE == "rotate" else resolved_mode
    popup_command = f"{shlex.quote(script_path())} popup {shlex.quote(popup_mode)}"
    icon_click = os.environ.get("CODEXBAR_XFCE_ICON_CLICK", popup_command)
    text_click = os.environ.get("CODEXBAR_XFCE_TEXT_CLICK", "") or popup_command
    return icon_click, text_click


def notify_popup(title: str, body: str) -> None:
    subprocess.run(["notify-send", title, body], check=False)


def popup_body(title: str, tooltip: str) -> str:
    lines = [ln for ln in strip_markup(tooltip).splitlines() if ln.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    return "\n".join(lines)


def _pbar(used_pct: int, width: int = 20) -> str:
    """Return a Unicode block progress bar filled to used_pct."""
    filled = round(min(100, max(0, used_pct)) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _popup_label(remaining: int, reset: str) -> str:
    if remaining <= 0:
        return f"{'FULL':<9} · resets in {reset}"
    return f"{remaining:3d}% left · resets in {reset}"


def build_popup_body(cx: dict | None, cl: dict | None) -> str:
    """Build a structured plain-text popup body with progress bars."""
    sep = "─" * 44
    sections: list[str] = []

    if cx is not None:
        rows = [
            f"  Session  {_pbar(cx['session_used'])}  {_popup_label(cx['session_remaining'], cx['session_reset'])}",
            f"  Weekly   {_pbar(cx['weekly_used'])}  {_popup_label(cx['weekly_remaining'], cx['weekly_reset'])}",
        ]
        if cx.get("review_reset"):
            rows.append(
                f"  Review   {_pbar(cx['review_used'])}  {_popup_label(cx['review_remaining'], cx['review_reset'])}"
            )
        rows.append(f"  Credits  local {cx['credits_local']}  ·  cloud {cx['credits_cloud']}")
        sections.append("Codex\n" + sep + "\n" + "\n".join(rows))

    if cl is not None:
        rows = [
            f"  5-hour   {_pbar(cl['fh_used'])}  {_popup_label(cl['fh_remaining'], cl['fh_reset'])}",
            f"  7-day    {_pbar(cl['sd_used'])}  {_popup_label(cl['sd_remaining'], cl['sd_reset'])}",
        ]
        if cl["op_used"] or cl["so_used"]:
            rows += [
                f"  Opus     {_pbar(cl['op_used'])}  {_popup_label(cl['op_remaining'], cl['op_reset'])}",
                f"  Sonnet   {_pbar(cl['so_used'])}  {_popup_label(cl['so_remaining'], cl['so_reset'])}",
            ]
        if cl["extra_enabled"]:
            rows.append(
                f"  Extra    {_pbar(cl['extra_util'])}  {cl['extra_remaining']:3d}% left "
                f"· {cl['extra_used_cr']}/{cl['extra_limit_cr']} cr"
            )
        sections.append("Claude\n" + sep + "\n" + "\n".join(rows))

    return "\n" + ("\n\n").join(sections) + "\n" if sections else "No data\n"

def icon_for_mode(mode: str) -> str:
    if MODE == "rotate":
        return ICON_MAP["rotate"]
    return ICON_MAP.get(mode, ICON_MAP.get("codex:remaining", "applications-development"))


# ---------------------------------------------------------------------------
# Time / pacing helpers
# ---------------------------------------------------------------------------


def format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


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


def iso8601_to_unix(value: str | None) -> int:
    """Parse an ISO8601 datetime string to a Unix timestamp. Returns 0 on failure."""
    if not value:
        return 0
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(value.replace("Z", "+00:00") if value.endswith("Z") else value, fmt.replace("Z", "+00:00") if "Z" in fmt else fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    # last-resort: fromisoformat (Python 3.11+)
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


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


def window_seconds_from_response(window_dict: dict, fallback: int) -> int:
    try:
        v = window_dict.get("window_seconds") or window_dict.get("window_size_seconds")
        if v:
            return int(v)
    except (TypeError, ValueError):
        pass
    return fallback


def status_class_for_pct(pct: int) -> str:
    if pct >= 90:
        return "critical"
    if pct >= 75:
        return "high"
    if pct >= 50:
        return "mid"
    return "low"


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    timeout: int = 10,
) -> dict:
    data = None
    request_headers = headers.copy() if headers else {}
    if body is not None:
        data = json.dumps(body).encode()
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


# ---------------------------------------------------------------------------
# Generic cache helpers
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as fh:
        json.dump(data, fh)
        tmp = fh.name
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    with path.open() as fh:
        return json.load(fh)


def fresh_cache(cache_file: Path) -> dict | None:
    if not cache_file.exists():
        return None
    if time.time() - cache_file.stat().st_mtime >= CACHE_TTL:
        return None
    return read_json(cache_file)


def stale_cache(cache_file: Path) -> tuple[dict, int] | None:
    if not cache_file.exists():
        return None
    age = time.time() - cache_file.stat().st_mtime
    if age > WEEKLY_WINDOW:
        return None
    return read_json(cache_file), int(age)


# ---------------------------------------------------------------------------
# JWT helpers (Codex only)
# ---------------------------------------------------------------------------


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
    try:
        return int(jwt_decode(token).get("exp", 0))
    except (TypeError, ValueError):
        return 0


def plan_from_id_token(id_token: str) -> str:
    auth = jwt_decode(id_token).get("https://api.openai.com/auth", {})
    plan_type = auth.get("chatgpt_plan_type")
    return str(plan_type).capitalize() if plan_type else "Codex"


# ===========================================================================
# CODEX — fetch
# ===========================================================================


def codex_refresh_tokens(creds: dict) -> dict:
    tokens = creds.get("tokens", {})
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Missing Codex refresh token. Run: codex login")
    payload = {
        "client_id": CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "openid profile email",
    }
    refreshed = http_json(CODEX_TOKEN_URL, method="POST", body=payload, timeout=25)
    new_tokens = creds.setdefault("tokens", {})
    new_tokens["access_token"] = refreshed.get("access_token", new_tokens.get("access_token", ""))
    if refreshed.get("refresh_token"):
        new_tokens["refresh_token"] = refreshed["refresh_token"]
    if refreshed.get("id_token"):
        new_tokens["id_token"] = refreshed["id_token"]
    creds["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())
    atomic_write_json(CODEX_CREDS_PATH, creds)
    return creds


def fetch_codex_usage() -> tuple[dict, str, int]:
    """Return (usage_dict, plan_str, stale_age_seconds)."""
    if not CODEX_CREDS_PATH.exists():
        raise RuntimeError("No Codex credentials. Run: codex login")
    creds = read_json(CODEX_CREDS_PATH)

    cached = fresh_cache(CODEX_CACHE_FILE)
    if cached is not None:
        return cached, plan_from_id_token(creds.get("tokens", {}).get("id_token", "")), 0

    CODEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CODEX_LOCK_FILE.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache(CODEX_CACHE_FILE)
        if cached is not None:
            return cached, plan_from_id_token(creds.get("tokens", {}).get("id_token", "")), 0

        creds = read_json(CODEX_CREDS_PATH)
        tokens = creds.get("tokens", {})
        access_token = tokens.get("access_token", "")
        if not access_token:
            raise RuntimeError("No Codex access token. Run: codex login")

        if jwt_exp(access_token) < int(time.time()) + REFRESH_BUFFER:
            try:
                creds = codex_refresh_tokens(creds)
                tokens = creds.get("tokens", {})
                access_token = tokens.get("access_token", access_token)
            except Exception as exc:
                result = stale_cache(CODEX_CACHE_FILE)
                if result:
                    data, age = result
                    return data, plan_from_id_token(tokens.get("id_token", "")), age
                if is_transient_error(exc):
                    raise RuntimeError("Codex usage is waiting for network.") from exc
                raise RuntimeError(f"Codex token refresh failed: {exc}") from exc

        headers = {"Authorization": f"Bearer {access_token}"}
        account_id = tokens.get("account_id")
        if account_id:
            headers["chatgpt-account-id"] = str(account_id)

        try:
            usage = http_json(CODEX_API_URL, headers=headers, timeout=10)
            if "rate_limit" not in usage:
                raise RuntimeError("Codex usage response missing rate_limit")
            atomic_write_json(CODEX_CACHE_FILE, usage)
            api_plan = usage.get("plan_type")
            plan = str(api_plan).capitalize() if api_plan else plan_from_id_token(tokens.get("id_token", ""))
            return usage, plan, 0
        except Exception as exc:
            result = stale_cache(CODEX_CACHE_FILE)
            if result:
                data, age = result
                return data, plan_from_id_token(tokens.get("id_token", "")), age
            if is_transient_error(exc):
                raise RuntimeError("Codex usage is waiting for network.") from exc
            raise RuntimeError(f"Codex API request failed: {exc}") from exc


# ===========================================================================
# CLAUDE — fetch
# ===========================================================================


def _parse_claude_creds_dict(data: dict) -> dict:
    """Normalise a camelCase Claude credentials dict into our internal format.

    Handles two expiresAt formats:
      - int/float Unix milliseconds  (Claude Code: ~/.claude/.credentials.json)
      - ISO8601 string               (claude-usage-bar app: credentials.json)
    """
    raw_expires = data.get("expiresAt")
    expires_unix: int | None = None
    if isinstance(raw_expires, (int, float)):
        # Claude Code stores milliseconds
        expires_unix = int(raw_expires / 1000)
    elif isinstance(raw_expires, str) and raw_expires:
        expires_unix = iso8601_to_unix(raw_expires) or None
    return {
        "access_token":  data.get("accessToken", ""),
        "refresh_token": data.get("refreshToken"),
        "expires_unix":  expires_unix,   # always a Unix timestamp (seconds) or None
        "scopes":        data.get("scopes", []),
    }


# Credential source constants — used by save to know where to write back
_CRED_SOURCE_CLAUDE_CODE = "claude_code"
_CRED_SOURCE_USAGE_BAR   = "usage_bar"
_CRED_SOURCE_LEGACY      = "legacy"


def claude_load_credentials() -> tuple[dict, str]:
    """Load Claude credentials.

    Returns (creds_dict, source) where source is one of the _CRED_SOURCE_* constants.
    Priority: Claude Code > claude-usage-bar app > legacy token file.
    """
    # 1. Claude Code (~/.claude/.credentials.json) — claudeAiOauth key
    claude_code_file = Path.home() / ".claude" / ".credentials.json"
    if claude_code_file.exists():
        try:
            data = read_json(claude_code_file)
            oauth = data.get("claudeAiOauth") or {}
            if oauth.get("accessToken"):
                return _parse_claude_creds_dict(oauth), _CRED_SOURCE_CLAUDE_CODE
        except Exception:
            pass

    # 2. claude-usage-bar app (~/.config/claude-usage-bar/credentials.json)
    if CLAUDE_CREDS_FILE.exists():
        try:
            data = read_json(CLAUDE_CREDS_FILE)
            if data.get("accessToken"):
                return _parse_claude_creds_dict(data), _CRED_SOURCE_USAGE_BAR
        except Exception:
            pass

    # 3. Legacy plain token file
    if CLAUDE_TOKEN_FILE.exists():
        token = CLAUDE_TOKEN_FILE.read_text().strip()
        if token:
            creds = {"access_token": token, "refresh_token": None, "expires_unix": None, "scopes": []}
            return creds, _CRED_SOURCE_LEGACY

    raise RuntimeError(
        "No Claude credentials found. "
        "Run 'claude' (Claude Code) or sign in via the claude-usage-bar app."
    )


def claude_save_credentials(creds: dict, source: str) -> None:
    """Persist refreshed credentials.

    Never writes back to Claude Code's file — that is managed by the Claude CLI.
    Always writes to the claude-usage-bar path so subsequent reads pick it up.
    """
    if source == _CRED_SOURCE_CLAUDE_CODE:
        # Write a usage-bar-format file so the refreshed token survives
        # without touching ~/.claude/.credentials.json
        target_dir = CLAUDE_CREDS_DIR
    else:
        target_dir = CLAUDE_CREDS_DIR

    target_dir.mkdir(parents=True, exist_ok=True)

    # Store expiresAt as Unix ms int to be consistent with Claude Code format
    expires_at_ms = None
    if creds.get("expires_unix"):
        expires_at_ms = creds["expires_unix"] * 1000

    payload = {
        "accessToken":  creds["access_token"],
        "refreshToken": creds.get("refresh_token"),
        "expiresAt":    expires_at_ms,
        "scopes":       creds.get("scopes", []),
    }
    target = target_dir / "credentials.json"
    with tempfile.NamedTemporaryFile("w", delete=False, dir=target_dir) as fh:
        json.dump(payload, fh)
        tmp = fh.name
    os.replace(tmp, target)
    os.chmod(target, 0o600)


def claude_token_needs_refresh(creds: dict) -> bool:
    exp = creds.get("expires_unix")
    if not exp:
        return False    # no expiry info — assume still valid
    return int(exp) < int(time.time()) + REFRESH_BUFFER


def claude_refresh_tokens(creds: dict, source: str) -> dict:
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No Claude refresh token. Re-authenticate via 'claude' or the claude-usage-bar app.")
    scopes = creds.get("scopes") or ["user:profile", "user:inference"]
    payload = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     CLAUDE_CLIENT_ID,
        "scope":         " ".join(scopes),
    }
    refreshed = http_json(CLAUDE_TOKEN_URL, method="POST", body=payload, timeout=25)
    if "access_token" not in refreshed:
        raise RuntimeError("Claude token refresh returned no access_token.")

    expires_unix: int | None = None
    expires_in = refreshed.get("expires_in")
    if expires_in:
        try:
            expires_unix = int(time.time()) + int(expires_in)
        except (TypeError, ValueError):
            pass

    new_creds = {
        "access_token":  refreshed["access_token"],
        "refresh_token": refreshed.get("refresh_token") or refresh_token,
        "expires_unix":  expires_unix,
        "scopes":        scopes,
    }
    claude_save_credentials(new_creds, source)
    return new_creds


def fetch_claude_usage() -> tuple[dict, int]:
    """Return (usage_dict, stale_age_seconds)."""
    creds, source = claude_load_credentials()

    cached = fresh_cache(CLAUDE_CACHE_FILE)
    if cached is not None:
        return cached, 0

    CLAUDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CLAUDE_LOCK_FILE.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache(CLAUDE_CACHE_FILE)
        if cached is not None:
            return cached, 0

        creds, source = claude_load_credentials()
        if claude_token_needs_refresh(creds):
            try:
                creds = claude_refresh_tokens(creds, source)
            except Exception as exc:
                result = stale_cache(CLAUDE_CACHE_FILE)
                if result:
                    data, age = result
                    return data, age
                if is_transient_error(exc):
                    raise RuntimeError("Claude usage is waiting for network.") from exc
                raise RuntimeError(f"Claude token refresh failed: {exc}") from exc

        headers = {
            "Authorization":  f"Bearer {creds['access_token']}",
            "anthropic-beta": CLAUDE_BETA_HEADER,
        }
        try:
            usage = http_json(CLAUDE_API_URL, headers=headers, timeout=10)
            atomic_write_json(CLAUDE_CACHE_FILE, usage)
            return usage, 0
        except Exception as exc:
            result = stale_cache(CLAUDE_CACHE_FILE)
            if result:
                data, age = result
                return data, age
            if is_transient_error(exc):
                raise RuntimeError("Claude usage is waiting for network.") from exc
            raise RuntimeError(f"Claude API request failed: {exc}") from exc


# ===========================================================================
# Codex — parse & format
# ===========================================================================


def parse_codex(usage: dict) -> dict:
    """Extract normalised fields from a Codex wham/usage response."""
    rate_limit = usage.get("rate_limit") or {}
    primary = rate_limit.get("primary_window") or {}
    secondary = rate_limit.get("secondary_window") or {}
    review = (usage.get("code_review_rate_limit") or {}).get("primary_window") or {}
    credits = usage.get("credits") or {}

    session_used = int(float(primary.get("used_percent", 0)))
    weekly_used  = int(float(secondary.get("used_percent", 0)))
    review_used  = int(float(review.get("used_percent", 0)))

    session_reset_at = primary.get("reset_at", 0)
    weekly_reset_at  = secondary.get("reset_at", 0)
    review_reset_at  = review.get("reset_at", 0)

    local_range  = credits.get("approx_local_messages") or [0, 0]
    cloud_range  = credits.get("approx_cloud_messages") or [0, 0]
    credits_local = (
        str(local_range[0]) if local_range[0] == local_range[-1]
        else f"{local_range[0]}-{local_range[-1]}"
    )
    credits_cloud = (
        str(cloud_range[0]) if cloud_range[0] == cloud_range[-1]
        else f"{cloud_range[0]}-{cloud_range[-1]}"
    )

    session_remaining = max(0, 100 - session_used)
    weekly_remaining  = max(0, 100 - weekly_used)
    review_remaining  = max(0, 100 - review_used)

    session_elapsed, _ = calc_pacing(session_used, session_reset_at, window_seconds_from_response(primary,   SESSION_WINDOW))
    weekly_elapsed, _  = calc_pacing(weekly_used,  weekly_reset_at,  window_seconds_from_response(secondary, WEEKLY_WINDOW))
    review_elapsed, _  = calc_pacing(review_used,  review_reset_at,  window_seconds_from_response(review,    REVIEW_WINDOW))

    return {
        "session_used":       session_used,
        "session_remaining":  session_remaining,
        "session_reset":      countdown(session_reset_at),
        "session_reset_at":   session_reset_at,
        "session_elapsed":    session_elapsed,
        "weekly_used":        weekly_used,
        "weekly_remaining":   weekly_remaining,
        "weekly_reset":       countdown(weekly_reset_at),
        "weekly_elapsed":     weekly_elapsed,
        "review_used":        review_used,
        "review_remaining":   review_remaining,
        "review_reset":       countdown(review_reset_at) if review_reset_at else "",
        "review_elapsed":     review_elapsed,
        "credits_local":      credits_local,
        "credits_cloud":      credits_cloud,
        "max_used":           max(session_used, weekly_used, review_used),
    }


def codex_text_for_mode(mode: str, f: dict) -> str:
    if mode == "codex:remaining":
        return f"[CX] {f['session_remaining']}% left · {f['session_reset']}"
    if mode == "codex:used":
        return f"[CX] {f['session_used']}% used · {f['session_reset']}"
    if mode == "codex:weekly":
        return f"[CX] W {f['weekly_remaining']}% left · {f['weekly_reset']}"
    if mode == "codex:combined":
        return f"[CX] S {f['session_remaining']}% · W {f['weekly_remaining']}%"
    if mode == "codex:credits":
        return f"[CX] L {f['credits_local']} · C {f['credits_cloud']}"
    raise ValueError(f"unknown codex mode '{mode}'")


def codex_tooltip_lines(f: dict, color: str) -> list[str]:
    lines = [
        tooltip_line("Session", f"{f['session_remaining']}% remaining ({f['session_used']}% used, {f['session_elapsed']}% elapsed)", color),
        tooltip_line("Session Reset", f['session_reset'], color),
        tooltip_line("Weekly",  f"{f['weekly_remaining']}% remaining ({f['weekly_used']}% used, {f['weekly_elapsed']}% elapsed)", color),
        tooltip_line("Weekly Reset",  f['weekly_reset'],  color),
    ]
    if f["review_reset"]:
        lines += [
            tooltip_line("Review", f"{f['review_remaining']}% remaining ({f['review_used']}% used, {f['review_elapsed']}% elapsed)", color),
            tooltip_line("Review Reset", f["review_reset"], color),
        ]
    lines.append(tooltip_line("Credits", f"local {f['credits_local']}, cloud {f['credits_cloud']}", color))
    return lines


# ===========================================================================
# Claude — parse & format
# ===========================================================================


def parse_claude(usage: dict) -> dict:
    """Extract normalised fields from a Claude api/oauth/usage response.

    utilization values are integers 0-100 (NOT floats 0.0-1.0 as the Swift
    model implies — confirmed against live API).
    """
    five_hour  = usage.get("five_hour")  or {}
    seven_day  = usage.get("seven_day")  or {}
    opus       = usage.get("seven_day_opus")    or {}
    sonnet     = usage.get("seven_day_sonnet")  or {}
    extra      = usage.get("extra_usage") or {}

    def pct(bucket: dict) -> int:
        try:
            return min(100, max(0, int(bucket.get("utilization") or 0)))
        except (TypeError, ValueError):
            return 0

    def reset_unix(bucket: dict) -> int:
        return iso8601_to_unix(bucket.get("resets_at"))

    fh_used  = pct(five_hour)
    sd_used  = pct(seven_day)
    op_used  = pct(opus)
    so_used  = pct(sonnet)

    fh_reset = reset_unix(five_hour)
    sd_reset = reset_unix(seven_day)
    op_reset = reset_unix(opus)
    so_reset = reset_unix(sonnet)

    fh_remaining = max(0, 100 - fh_used)
    sd_remaining = max(0, 100 - sd_used)
    op_remaining = max(0, 100 - op_used)
    so_remaining = max(0, 100 - so_used)

    fh_elapsed, _ = calc_pacing(fh_used, fh_reset, 5 * 3600)
    sd_elapsed, _ = calc_pacing(sd_used, sd_reset, WEEKLY_WINDOW)
    op_elapsed, _ = calc_pacing(op_used, op_reset, WEEKLY_WINDOW)
    so_elapsed, _ = calc_pacing(so_used, so_reset, WEEKLY_WINDOW)

    extra_enabled  = extra.get("is_enabled", False)
    extra_util     = min(100, max(0, int(extra.get("utilization") or 0)))
    extra_used_cr  = extra.get("used_credits") or 0
    extra_limit_cr = extra.get("monthly_limit") or 0
    extra_remaining = max(0, 100 - extra_util)

    return {
        "fh_used":        fh_used,
        "fh_remaining":   fh_remaining,
        "fh_reset":       countdown(fh_reset) if fh_reset else "-",
        "fh_reset_at":    fh_reset,
        "fh_elapsed":     fh_elapsed,
        "sd_used":        sd_used,
        "sd_remaining":   sd_remaining,
        "sd_reset":       countdown(sd_reset) if sd_reset else "-",
        "sd_elapsed":     sd_elapsed,
        "op_used":        op_used,
        "op_remaining":   op_remaining,
        "op_reset":       countdown(op_reset) if op_reset else "-",
        "op_elapsed":     op_elapsed,
        "so_used":        so_used,
        "so_remaining":   so_remaining,
        "so_reset":       countdown(so_reset) if so_reset else "-",
        "so_elapsed":     so_elapsed,
        "extra_enabled":  extra_enabled,
        "extra_util":     extra_util,
        "extra_remaining": extra_remaining,
        "extra_used_cr":  extra_used_cr,
        "extra_limit_cr": extra_limit_cr,
        "extra_reset":    "-",
        "max_used":       max(fh_used, sd_used),
    }


def claude_text_for_mode(mode: str, f: dict) -> str:
    if mode == "claude:5h":
        return f"[CL] {f['fh_remaining']}% left · {f['fh_reset']}"
    if mode == "claude:7d":
        return f"[CL] W {f['sd_remaining']}% left · {f['sd_reset']}"
    if mode == "claude:opus":
        return f"[CL] Opus {f['op_remaining']}% · {f['op_reset']}"
    if mode == "claude:sonnet":
        return f"[CL] Sonnet {f['so_remaining']}% · {f['so_reset']}"
    if mode == "claude:extra":
        if f["extra_enabled"]:
            return f"[CL] Extra {f['extra_remaining']}% · {f['extra_used_cr']}/{f['extra_limit_cr']}cr"
        return "[CL] Extra: off"
    raise ValueError(f"unknown claude mode '{mode}'")


def claude_tooltip_lines(f: dict, color: str) -> list[str]:
    lines = [
        tooltip_line("5-hour",       f"{f['fh_remaining']}% remaining ({f['fh_used']}% used, {f['fh_elapsed']}% elapsed)", color),
        tooltip_line("5h Reset",     f['fh_reset'],  color),
        tooltip_line("7-day",        f"{f['sd_remaining']}% remaining ({f['sd_used']}% used, {f['sd_elapsed']}% elapsed)", color),
        tooltip_line("7d Reset",     f['sd_reset'],  color),
    ]
    if f["op_used"] or f["so_used"]:
        lines += [
            tooltip_line("Opus (7d)",   f"{f['op_remaining']}% remaining ({f['op_used']}% used)", color),
            tooltip_line("Sonnet (7d)", f"{f['so_remaining']}% remaining ({f['so_used']}% used)", color),
        ]
    if f["extra_enabled"]:
        lines.append(tooltip_line(
            "Extra",
            f"{f['extra_remaining']}% remaining · {f['extra_used_cr']}/{f['extra_limit_cr']} credits",
            color,
        ))
    return lines


# ===========================================================================
# Combined display text
# ===========================================================================


def combined_text(cx: dict | None, cl: dict | None) -> str:
    parts = []
    resets: list[int] = []
    if cx is not None:
        parts.append(f"CX {cx['session_remaining']}%")
        if cx.get("session_reset_at"):
            resets.append(cx["session_reset_at"])
    if cl is not None:
        parts.append(f"CL {cl['fh_remaining']}%")
        if cl.get("fh_reset_at"):
            resets.append(cl["fh_reset_at"])
    if not parts:
        return "N/A"
    text = " · ".join(parts)
    if resets:
        nearest = countdown(min(resets))
        text += f" · {nearest}"
    return text


# ===========================================================================
# Main format_message
# ===========================================================================


def format_message(
    codex_usage: dict | None,
    codex_plan: str,
    codex_stale: int,
    claude_usage: dict | None,
    claude_stale: int,
) -> tuple[str, str, str, str, str, str]:
    """
    Return (text, tooltip, icon, color, resolved_mode_for_click, title).
    resolved_mode is the actual mode string used (after resolving 'rotate').
    """
    resolved_mode = pick_rotate_mode() if MODE == "rotate" else MODE

    cx = parse_codex(codex_usage) if codex_usage else None
    cl = parse_claude(claude_usage) if claude_usage else None

    # Determine worst-case status colour across all active sources
    max_used = max(
        (cx["max_used"] if cx else 0),
        (cl["max_used"] if cl else 0),
    )
    status_class = status_class_for_pct(max_used)
    color = COLOR_MAP[status_class]

    # ------------------------------------------------------------------
    # Panel text
    # ------------------------------------------------------------------
    is_codex_mode  = resolved_mode.startswith("codex:")
    is_claude_mode = resolved_mode.startswith("claude:")

    if resolved_mode == "combined":
        text = combined_text(cx, cl)
    elif is_codex_mode:
        if cx is None:
            text = "[CX] N/A"
        else:
            text = codex_text_for_mode(resolved_mode, cx)
    elif is_claude_mode:
        if cl is None:
            text = "[CL] N/A"
        else:
            text = claude_text_for_mode(resolved_mode, cl)
    else:
        # Legacy bare modes (e.g. someone still passes "remaining") — map to codex
        legacy_map = {
            "remaining": "codex:remaining",
            "used":      "codex:used",
            "weekly":    "codex:weekly",
            "credits":   "codex:credits",
        }
        mapped = legacy_map.get(resolved_mode)
        if mapped and cx is not None:
            text = codex_text_for_mode(mapped, cx)
            resolved_mode = mapped
        elif cl is not None:
            text = claude_text_for_mode("claude:5h", cl)
            resolved_mode = "claude:5h"
        else:
            text = "N/A"

    # Stale indicator
    any_stale = (codex_stale > 0 and is_codex_mode) or (claude_stale > 0 and is_claude_mode) or (
        resolved_mode == "combined" and (codex_stale > 0 or claude_stale > 0)
    )
    if any_stale:
        text = text + " ~"

    icon = icon_for_mode(resolved_mode)

    # ------------------------------------------------------------------
    # Tooltip
    # ------------------------------------------------------------------
    tooltip_parts: list[str] = []

    if cx is not None:
        cx_color = COLOR_MAP[status_class_for_pct(cx["max_used"])]
        tooltip_parts.append(tooltip_header(f"{codex_plan} (Codex)"))
        tooltip_parts += codex_tooltip_lines(cx, cx_color)
        if codex_stale > 0:
            tooltip_parts.append(tooltip_line("Codex Data Age", f"{format_age(codex_stale)} (stale)", COLOR_MAP["high"]))

    if cl is not None:
        cl_color = COLOR_MAP[status_class_for_pct(cl["max_used"])]
        if tooltip_parts:
            tooltip_parts.append("")   # blank separator line
        tooltip_parts.append(tooltip_header("Claude"))
        tooltip_parts += claude_tooltip_lines(cl, cl_color)
        if claude_stale > 0:
            tooltip_parts.append(tooltip_line("Claude Data Age", f"{format_age(claude_stale)} (stale)", COLOR_MAP["high"]))

    tooltip_parts.append(tooltip_line("Mode", resolved_mode, color))
    if MODE == "rotate":
        tooltip_parts.append(tooltip_line("Rotate Every", f"{ROTATE_SECONDS}s", color))

    title_parts = []
    if cx is not None:
        title_parts.append(codex_plan)
    if cl is not None:
        title_parts.append("Claude")
    title = " + ".join(title_parts) if title_parts else "Usage"

    return text, "\n".join(tooltip_parts), icon, color, resolved_mode, title


# ===========================================================================
# Entry point
# ===========================================================================

codex_usage_data: dict | None = None
codex_plan: str = "Codex"
codex_stale: int = 0
codex_error: str | None = None

claude_usage_data: dict | None = None
claude_stale: int = 0
claude_error: str | None = None

if SHOW_CODEX:
    try:
        codex_usage_data, codex_plan, codex_stale = fetch_codex_usage()
    except Exception as exc:
        codex_error = str(exc)

if SHOW_CLAUDE:
    try:
        claude_usage_data, claude_stale = fetch_claude_usage()
    except Exception as exc:
        claude_error = str(exc)

try:
    text, tooltip, icon, color, resolved_mode_for_click, title = format_message(
        codex_usage_data, codex_plan, codex_stale,
        claude_usage_data, claude_stale,
    )
    # Append any per-source errors to the tooltip
    if codex_error:
        tooltip += f"\n{tooltip_line('Codex Error', codex_error, COLOR_MAP['critical'])}"
        if codex_usage_data is None:
            text = text.replace("[CX]", "[CX!]") if "[CX]" in text else text
    if claude_error:
        tooltip += f"\n{tooltip_line('Claude Error', claude_error, COLOR_MAP['critical'])}"
        if claude_usage_data is None:
            text = text.replace("[CL]", "[CL!]") if "[CL]" in text else text
    icon_click, text_click = default_clicks(resolved_mode_for_click)
except Exception as exc:
    color = COLOR_MAP["critical"]
    text = "Usage N/A"
    tooltip = saxutils.escape(str(exc))
    icon = ICON_MAP.get("codex:remaining", "applications-development")
    title = "Usage"
    resolved_mode_for_click = "rotate"
    icon_click, text_click = default_clicks(resolved_mode_for_click)

if ACTION == "popup":
    _cx = parse_codex(codex_usage_data) if codex_usage_data else None
    _cl = parse_claude(claude_usage_data) if claude_usage_data else None
    print(build_popup_body(_cx, _cl))
    raise SystemExit(0)

if SHOW_ICON:
    print(f"<icon>{saxutils.escape(icon)}</icon>")
    if icon_click:
        print(f"<click>{saxutils.escape(icon_click)}</click>")
print(f"<txt>{pango(text, color)}</txt>")
if text_click:
    print(f"<txtclick>{saxutils.escape(text_click)}</txtclick>")
print(f"<tool>{tooltip}</tool>")
