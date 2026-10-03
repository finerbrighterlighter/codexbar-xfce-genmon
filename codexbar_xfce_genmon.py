#!/usr/bin/env python3

import base64
import email.utils
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

# Which models to show — driven by CODEXBAR_XFCE_MODELS (comma-separated).
# Empty = all configured sources (Copilot only if copilot.conf exists).
_MODELS_ENV = os.environ.get("CODEXBAR_XFCE_MODELS", "").lower()
_REQUESTED_MODELS: set[str] = (
    {m.strip() for m in _MODELS_ENV.split(",") if m.strip()}
    if _MODELS_ENV else set()
)

# Backwards-compat: CODEXBAR_XFCE_SOURCE still honoured if MODELS not set
if not _REQUESTED_MODELS:
    _source = os.environ.get("CODEXBAR_XFCE_SOURCE", "").lower()
    if _source == "codex":
        _REQUESTED_MODELS = {"codex"}
    elif _source == "claude":
        _REQUESTED_MODELS = {"claude"}

# Copilot config path — repo-local file takes priority, then ~/.config fallback
_COPILOT_CONF_REPO = Path(__file__).parent / "copilot.conf"
_COPILOT_CONF_HOME = Path.home() / ".config" / "codexbar-xfce-genmon" / "copilot.conf"
COPILOT_CONF_FILE   = _COPILOT_CONF_REPO if _COPILOT_CONF_REPO.exists() else _COPILOT_CONF_HOME
COPILOT_CONF_DIR    = COPILOT_CONF_FILE.parent
COPILOT_CONF_EXISTS = COPILOT_CONF_FILE.exists()

# OpenRouter config path — same lookup order as Copilot
_OPENROUTER_CONF_REPO = Path(__file__).parent / "openrouter.conf"
_OPENROUTER_CONF_HOME = Path.home() / ".config" / "codexbar-xfce-genmon" / "openrouter.conf"
OPENROUTER_CONF_FILE   = _OPENROUTER_CONF_REPO if _OPENROUTER_CONF_REPO.exists() else _OPENROUTER_CONF_HOME
OPENROUTER_CONF_EXISTS = OPENROUTER_CONF_FILE.exists()

# Resolve which models are actually active
if _REQUESTED_MODELS:
    SHOW_CODEX      = "codex"      in _REQUESTED_MODELS
    SHOW_CLAUDE     = "claude"     in _REQUESTED_MODELS
    SHOW_COPILOT    = "copilot"    in _REQUESTED_MODELS
    SHOW_OPENROUTER = "openrouter" in _REQUESTED_MODELS
    # Explicit --model=copilot with no conf → show error state, not silent skip
    COPILOT_EXPLICIT    = SHOW_COPILOT
    OPENROUTER_EXPLICIT = SHOW_OPENROUTER
else:
    # No explicit model flags → auto-include everything that is configured
    SHOW_CODEX      = True
    SHOW_CLAUDE     = True
    SHOW_COPILOT    = COPILOT_CONF_EXISTS
    SHOW_OPENROUTER = OPENROUTER_CONF_EXISTS
    COPILOT_EXPLICIT    = False
    OPENROUTER_EXPLICIT = False

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
# Copilot auth / API constants
# ---------------------------------------------------------------------------

COPILOT_DEFAULT_QUOTA = int(os.environ.get("CODEXBAR_XFCE_COPILOT_QUOTA", "1500") or "1500")
GITHUB_API_BASE = "https://api.github.com"

COPILOT_CACHE_DIR  = Path.home() / ".cache" / "codexbar-xfce-genmon"
COPILOT_CACHE_FILE = COPILOT_CACHE_DIR / "copilot_usage.json"
COPILOT_USER_CACHE = COPILOT_CACHE_DIR / "copilot_user.json"
COPILOT_LOCK_FILE  = COPILOT_CACHE_DIR / ".copilot_fetch.lock"

# ---------------------------------------------------------------------------
# OpenRouter API constants
# ---------------------------------------------------------------------------

OPENROUTER_API_BASE   = "https://openrouter.ai/api/v1"
OPENROUTER_CACHE_FILE = Path.home() / ".cache" / "codexbar-xfce-genmon" / "openrouter_usage.json"
OPENROUTER_LOCK_FILE  = Path.home() / ".cache" / "codexbar-xfce-genmon" / ".openrouter_fetch.lock"
OPENROUTER_CRIT_BALANCE = 0.5


def env_float(name: str, default: float | None) -> float | None:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


OPENROUTER_MIN_BALANCE = env_float("CODEXBAR_XFCE_OPENROUTER_MIN_BALANCE", 2.0)
# Balance alerts are opt-in: only when the env var is set explicitly
OPENROUTER_ALERT_BALANCE = env_float("CODEXBAR_XFCE_OPENROUTER_MIN_BALANCE", None)

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

# Default rotation includes all active sources
_default_rotate: list[str] = []
if SHOW_CODEX:
    _default_rotate += ["codex:remaining", "codex:weekly"]
if SHOW_CLAUDE:
    _default_rotate += ["claude:5h", "claude:7d"]
if SHOW_COPILOT:
    _default_rotate += ["copilot:usage"]
if SHOW_OPENROUTER:
    _default_rotate += ["openrouter:balance"]
if (SHOW_CODEX or SHOW_CLAUDE or SHOW_COPILOT or SHOW_OPENROUTER):
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

# combined panel text: "gauge" (one block glyph per window) or "text" (percentages)
PANEL_STYLE = "text" if os.environ.get("CODEXBAR_XFCE_PANEL_STYLE", "").strip().lower() == "text" else "gauge"
HIDE_IDLE = os.environ.get("CODEXBAR_XFCE_HIDE_IDLE", "1") != "0"
# A source counts as idle (hidden) unless it was active this recently or a
# window is at least this full
ACTIVE_HOURS = env_float("CODEXBAR_XFCE_ACTIVE_HOURS", 6.0)
SHOW_AT_PCT = env_float("CODEXBAR_XFCE_SHOW_AT_PCT", 80.0)

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
    # Copilot icons
    "copilot:usage":   os.environ.get("CODEXBAR_XFCE_ICON_COPILOT",        "github"),
    # OpenRouter icons
    "openrouter:balance": os.environ.get("CODEXBAR_XFCE_ICON_OPENROUTER",  "wallet"),
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
    padded = label.ljust(_TOOLTIP_LABEL_WIDTH)
    return (
        f"<tt>"
        f"<span fgcolor=\"{saxutils.escape(color)}\" weight=\"Bold\">{saxutils.escape(padded)}</span>"
        f"  {saxutils.escape(value)}"
        f"</tt>"
    )


_TOOLTIP_LABEL_WIDTH = 11  # len("Sonnet (7d)") — longest label across all sources


def usage_bar(used_pct: int, width: int, elapsed_pct: int | None = None) -> str:
    """Block bar filled to used_pct; a │ cursor replaces the cell at elapsed_pct."""
    filled = round(min(100, max(0, used_pct)) / 100 * width)
    cells = ["█"] * filled + ["░"] * (width - filled)
    if elapsed_pct is not None:
        idx = min(width - 1, max(0, int(elapsed_pct)) * width // 100)
        cells[idx] = "│"
    return "".join(cells)


def tooltip_bar_line(
    label: str, used_pct: int, detail: str, color: str, width: int = 10,
    elapsed_pct: int | None = None,
) -> str:
    """One tooltip row, fully monospaced so bars and details align across rows."""
    bar = usage_bar(used_pct, width, elapsed_pct)
    # Pad label to fixed width inside the monospace block
    padded = label.ljust(_TOOLTIP_LABEL_WIDTH)
    return (
        f"<tt>"
        f"<span fgcolor=\"{saxutils.escape(color)}\" weight=\"Bold\">{saxutils.escape(padded)}</span>"
        f"  {saxutils.escape(bar)}  {saxutils.escape(detail)}"
        f"</tt>"
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


def notify_send(title: str, body: str, urgency: str = "normal") -> None:
    """Desktop notification via notify-send. Never raises.

    Notification servers parse the body as markup, so it is escaped; a
    model name with '&' or '<' would otherwise blank or mangle it.
    """
    try:
        subprocess.run(
            ["notify-send", "-a", "codexbar", "-u", urgency, title, saxutils.escape(body)],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify_popup(title: str, body: str) -> None:
    notify_send(title, body.strip("\n"))


def show_popup(title: str, body: str, interactive: bool) -> None:
    """Print the popup body. From a panel click there is no terminal (genmon
    discards stdout), so the body is also shown as a desktop notification."""
    print(body)
    if not interactive:
        notify_popup(title, body)


def popup_body(title: str, tooltip: str) -> str:
    lines = [ln for ln in strip_markup(tooltip).splitlines() if ln.strip()]
    if lines and lines[0] == title:
        lines = lines[1:]
    return "\n".join(lines)


def _pbar(used_pct: int, width: int = 20, elapsed_pct: int | None = None) -> str:
    """Return a Unicode block progress bar filled to used_pct."""
    return usage_bar(used_pct, width, elapsed_pct)


def _popup_detail(used_pct: int, elapsed_pct: int, reset: str, reset_at: int = 0, window_s: int = 0) -> str:
    """Standardised detail column: used · elapsed · resets [· pace]."""
    suffix = pace_suffix(used_pct, elapsed_pct, reset_at, window_s)
    if used_pct >= 100:
        return f"FULL       · {elapsed_pct:3d}% elapsed · resets {reset}{suffix}"
    return f"{used_pct:3d}% used  · {elapsed_pct:3d}% elapsed · resets {reset}{suffix}"


_POPUP_LABEL_W = 10  # len("Fable (7d)") — longest popup row label (scoped limits)


def build_popup_body(cx: dict | None, cl: dict | None, cp: dict | None = None, orr: dict | None = None) -> str:
    """Build a structured plain-text popup body with progress bars."""
    sep = "─" * 56
    sections: list[str] = []

    def row(label: str, used_pct: int, elapsed_pct: int, reset: str, reset_at: int = 0, window_s: int = 0) -> str:
        bar = _pbar(used_pct, elapsed_pct=elapsed_pct if reset_at else None)
        detail = _popup_detail(used_pct, elapsed_pct, reset, reset_at, window_s)
        return f"  {label:<{_POPUP_LABEL_W}}  {bar}  {detail}"

    if cx is not None:
        rows = []
        if cx.get("session_present", True):
            rows.append(row(cx.get("session_label", "5-hour"), cx['session_used'], cx['session_elapsed'], cx['session_reset'],
                            cx.get('session_reset_at', 0), cx.get('session_window', 0)))
        if cx.get("weekly_present", True):
            rows.append(row(cx.get("weekly_label", "7-day"), cx['weekly_used'], cx['weekly_elapsed'], cx['weekly_reset'],
                            cx.get('weekly_reset_at', 0), cx.get('weekly_window', 0)))
        if cx.get("review_reset"):
            rows.append(row("Review", cx['review_used'], cx['review_elapsed'], cx['review_reset'],
                            cx.get('review_reset_at', 0), cx.get('review_window', 0)))
        rows.append(f"  {'Credits':<{_POPUP_LABEL_W}}  local {cx['credits_local']}  ·  cloud {cx['credits_cloud']}")
        sections.append("Codex\n" + sep + "\n" + "\n".join(rows))

    if cl is not None:
        rows = [
            row("5-hour", cl['fh_used'], cl['fh_elapsed'], cl['fh_reset'], cl.get('fh_reset_at', 0), SESSION_WINDOW),
            row("7-day",  cl['sd_used'], cl['sd_elapsed'], cl['sd_reset'], cl.get('sd_reset_at', 0), WEEKLY_WINDOW),
        ]
        if cl["op_used"] or cl["so_used"]:
            rows += [
                row("Opus",   cl['op_used'], cl['op_elapsed'], cl['op_reset'], cl.get('op_reset_at', 0), WEEKLY_WINDOW),
                row("Sonnet", cl['so_used'], cl['so_elapsed'], cl['so_reset'], cl.get('so_reset_at', 0), WEEKLY_WINDOW),
            ]
        for s in cl.get("scoped", []):
            rows.append(row(s["label"], s["used"], s["elapsed"], s["reset"], s["reset_at"], WEEKLY_WINDOW))
        if cl["extra_enabled"]:
            rows.append(
                f"  {'Extra':<{_POPUP_LABEL_W}}  {_pbar(cl['extra_util'])}  "
                f"{cl['extra_util']:3d}% used  · {cl['extra_used_cr']}/{cl['extra_limit_cr']} cr"
            )
        sections.append("Claude\n" + sep + "\n" + "\n".join(rows))

    if cp is not None:
        rows = [row("Monthly", cp['pct_used'], cp['elapsed'], cp['reset'], cp['reset_at'], cp.get('window', 0))]
        sections.append("GitHub Copilot\n" + sep + "\n" + "\n".join(rows))

    if orr is not None:
        rows = []
        for label, pct, text in openrouter_detail_rows(orr):
            bar = f"{_pbar(pct)}  " if pct is not None else ""
            rows.append(f"  {label:<{_POPUP_LABEL_W}}  {bar}{text}")
        sections.append("OpenRouter\n" + sep + "\n" + "\n".join(rows))

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


def format_span(seconds: int) -> str:
    """countdown()-style duration: '1d 3h', '2h 30m', '45m'."""
    s = max(0, int(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def pace(
    used_pct: int, elapsed_pct: int, reset_at: int | str | None, now: float,
    window_s: int | None = None,
) -> dict:
    """Burn-rate projection for one window.

    delta  = used - elapsed (percentage points ahead of an even pace)
    eta_s  = seconds until 100% at the current average rate (None if unknown)
    lasts  = True when the quota outlives the window at that rate
    window_s, when known, gives exact elapsed seconds; otherwise they are
    derived from elapsed_pct and the time left.
    """
    delta = used_pct - elapsed_pct
    result = {"delta": delta, "eta_s": None, "lasts": None}
    try:
        reset = int(reset_at or 0)
    except (TypeError, ValueError):
        return result
    time_to_reset = reset - now
    if used_pct <= 0 or elapsed_pct <= 0 or reset <= 0 or time_to_reset <= 0:
        return result
    if window_s:
        elapsed_s = window_s - time_to_reset
    else:
        frac = elapsed_pct / 100
        elapsed_s = time_to_reset * frac / (1 - frac) if frac < 1 else 0
    if elapsed_s <= 0:
        return result
    rate = used_pct / elapsed_s
    eta = max(0, int((100 - used_pct) / rate))
    result["eta_s"] = eta
    result["lasts"] = eta >= time_to_reset
    return result


def pace_suffix(used_pct: int, elapsed_pct: int, reset_at: int | str | None, window_s: int = 0) -> str:
    """' · ↑ empty in 2h 30m' / ' · → lasts'; '' when pace is meaningless."""
    if used_pct <= 0 or used_pct >= 100 or not reset_at:
        return ""
    p = pace(used_pct, elapsed_pct, reset_at, time.time(), window_s or None)
    arrow = "↑" if p["delta"] > 5 else "↓" if p["delta"] < -5 else "→"
    if p["eta_s"] is None:
        return f" · {arrow}"
    if p["lasts"]:
        return f" · {arrow} lasts"
    return f" · {arrow} empty in {format_span(p['eta_s'])}"


def window_bar_line(
    label: str, used: int, elapsed: int, reset: str, reset_at: int, window_s: int, color: str,
) -> str:
    """Tooltip bar row for a timed window: used · elapsed · resets · pace."""
    detail = (
        f"{used:3d}% used · {elapsed:3d}% elapsed · resets {reset}"
        f"{pace_suffix(used, elapsed, reset_at, window_s)}"
    )
    return tooltip_bar_line(label, used, detail, color, elapsed_pct=elapsed if reset_at else None)


def window_seconds_from_response(window_dict: dict, fallback: int) -> int:
    try:
        v = (
            window_dict.get("limit_window_seconds")
            or window_dict.get("window_seconds")
            or window_dict.get("window_size_seconds")
        )
        if v:
            return int(v)
    except (TypeError, ValueError):
        pass
    return fallback


def window_label(seconds: int | None, fallback: str) -> str:
    """Human label for a rate-limit window length, e.g. 18000 -> "5-hour"."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return fallback
    if s <= 0:
        return fallback
    if s == 5 * 3600:
        return "5-hour"
    if s == 7 * 86400:
        return "7-day"
    if 28 * 86400 <= s <= 31 * 86400:
        return "30-day"
    if s >= 86400:
        return f"{round(s / 86400)}d"
    return f"{max(1, round(s / 3600))}h"


_CLASS_ORDER = ["low", "mid", "high", "critical"]


def worst_class(*classes: str) -> str:
    return max(classes, key=_CLASS_ORDER.index, default="low")


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


def _exc_chain(exc: BaseException | None):
    """Yield exc and its __cause__/__context__ chain (wrapped errors included)."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def http_error_info(exc: BaseException) -> tuple[int | None, str | None]:
    """Return (HTTP status, Retry-After header) from an HTTPError anywhere in
    the exception chain, so callers that wrap HTTPError still expose them."""
    for e in _exc_chain(exc):
        if isinstance(e, urllib.error.HTTPError):
            headers = e.headers
            return e.code, (headers.get("Retry-After") if headers is not None else None)
    return None, None


def is_transient_error(exc: Exception) -> bool:
    for e in _exc_chain(exc):
        if isinstance(e, urllib.error.HTTPError):
            return False
        if isinstance(e, (urllib.error.URLError, TimeoutError)):
            return True
    return False


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
# Backoff state (persisted — genmon re-execs the script every tick)
# ---------------------------------------------------------------------------

BACKOFF_MIN = 60
BACKOFF_MAX = 3600
BACKOFF_DIR = Path.home() / ".cache" / "codexbar-xfce-genmon"


def backoff_path(provider: str) -> Path:
    return BACKOFF_DIR / f"{provider}_backoff.json"


def parse_retry_after(value: str | None, now: float) -> int | None:
    """Retry-After is either delta-seconds or an HTTP-date."""
    if value is None:
        return None
    v = str(value).strip()
    if v.isdigit():
        return int(v)
    try:
        dt = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int(dt.timestamp() - now))


def next_backoff(prev: dict | None, status: int | None, retry_after: int | None, now: float) -> dict:
    """Compute the next backoff state: double from 60s up to 1h.

    On 429, Retry-After is honored as a lower bound — some APIs send 0-1s,
    which would otherwise retry on every genmon tick.
    """
    try:
        prev_interval = int((prev or {}).get("interval") or 0)
    except (TypeError, ValueError):
        prev_interval = 0
    interval = min(max(BACKOFF_MIN, 2 * prev_interval), BACKOFF_MAX)
    if status == 429 and retry_after is not None:
        interval = min(max(interval, retry_after), 86400)
    return {
        "blocked_until": int(now) + interval,
        "reason":        str(status) if status else "error",
        "interval":      interval,
    }


def backoff_blocked(state: dict | None, now: float) -> bool:
    try:
        return bool(state) and now < float(state.get("blocked_until") or 0)
    except (TypeError, ValueError):
        return False


def load_backoff(provider: str) -> dict | None:
    try:
        data = read_json(backoff_path(provider))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def record_backoff(provider: str, exc: Exception) -> None:
    """Persist a backoff after a failed fetch. Transient network errors don't count."""
    if is_transient_error(exc):
        return
    now = time.time()
    status, retry_after = http_error_info(exc)
    state = next_backoff(load_backoff(provider), status, parse_retry_after(retry_after, now), now)
    try:
        atomic_write_json(backoff_path(provider), state)
    except OSError:
        pass


def clear_backoff(provider: str) -> None:
    try:
        backoff_path(provider).unlink(missing_ok=True)
    except OSError:
        pass


def backoff_until_text(state: dict) -> str:
    retry = time.strftime("%H:%M", time.localtime(int(state.get("blocked_until") or 0)))
    return f"retry {retry} ({state.get('reason') or 'error'})"


def serve_during_backoff(cache_file: Path, name: str, state: dict) -> tuple[dict, int]:
    """While backed off: stale cache if any, never the network."""
    result = stale_cache(cache_file)
    if result:
        return result
    raise RuntimeError(f"{name} backing off · {backoff_until_text(state)}")


def data_age_text(stale: int, state: dict | None, now: float) -> str:
    if backoff_blocked(state, now):
        return f"cached {format_age(stale)} · {backoff_until_text(state)}"
    return f"{format_age(stale)} (stale)"


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

    state = load_backoff("codex")
    if backoff_blocked(state, time.time()):
        data, age = serve_during_backoff(CODEX_CACHE_FILE, "Codex", state)
        return data, plan_from_id_token(creds.get("tokens", {}).get("id_token", "")), age

    CODEX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CODEX_LOCK_FILE.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache(CODEX_CACHE_FILE)
        if cached is not None:
            return cached, plan_from_id_token(creds.get("tokens", {}).get("id_token", "")), 0

        # A process that held the lock before us may have just started a backoff
        state = load_backoff("codex")
        if backoff_blocked(state, time.time()):
            data, age = serve_during_backoff(CODEX_CACHE_FILE, "Codex", state)
            return data, plan_from_id_token(creds.get("tokens", {}).get("id_token", "")), age

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
                record_backoff("codex", exc)
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
            clear_backoff("codex")
            api_plan = usage.get("plan_type")
            plan = str(api_plan).capitalize() if api_plan else plan_from_id_token(tokens.get("id_token", ""))
            return usage, plan, 0
        except Exception as exc:
            record_backoff("codex", exc)
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

    state = load_backoff("claude")
    if backoff_blocked(state, time.time()):
        return serve_during_backoff(CLAUDE_CACHE_FILE, "Claude", state)

    CLAUDE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CLAUDE_LOCK_FILE.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache(CLAUDE_CACHE_FILE)
        if cached is not None:
            return cached, 0

        # A process that held the lock before us may have just started a backoff
        state = load_backoff("claude")
        if backoff_blocked(state, time.time()):
            return serve_during_backoff(CLAUDE_CACHE_FILE, "Claude", state)

        creds, source = claude_load_credentials()
        if claude_token_needs_refresh(creds):
            try:
                creds = claude_refresh_tokens(creds, source)
            except Exception as exc:
                record_backoff("claude", exc)
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
            clear_backoff("claude")
            return usage, 0
        except Exception as exc:
            record_backoff("claude", exc)
            result = stale_cache(CLAUDE_CACHE_FILE)
            if result:
                data, age = result
                return data, age
            if is_transient_error(exc):
                raise RuntimeError("Claude usage is waiting for network.") from exc
            raise RuntimeError(f"Claude API request failed: {exc}") from exc


# ===========================================================================
# Copilot — config, fetch, parse
# ===========================================================================


def load_copilot_config() -> dict | None:
    """Load ~/.config/codexbar-xfce-genmon/copilot.conf.

    Returns a dict with GITHUB_TOKEN and COPILOT_QUOTA, or None if the file
    does not exist. Env vars CODEXBAR_XFCE_COPILOT_TOKEN and
    CODEXBAR_XFCE_COPILOT_QUOTA override file values.
    """
    token_env = os.environ.get("CODEXBAR_XFCE_COPILOT_TOKEN")
    quota_env = os.environ.get("CODEXBAR_XFCE_COPILOT_QUOTA")

    cfg: dict = {"GITHUB_TOKEN": token_env, "COPILOT_QUOTA": COPILOT_DEFAULT_QUOTA}

    if COPILOT_CONF_FILE.exists():
        for raw in COPILOT_CONF_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k == "GITHUB_TOKEN" and not token_env:
                    cfg["GITHUB_TOKEN"] = v
                elif k == "COPILOT_QUOTA" and not quota_env:
                    try:
                        cfg["COPILOT_QUOTA"] = int(v)
                    except ValueError:
                        pass
    elif not token_env:
        return None  # no config and no env token — Copilot unavailable

    if not cfg["GITHUB_TOKEN"]:
        return None

    if quota_env:
        try:
            cfg["COPILOT_QUOTA"] = int(quota_env)
        except ValueError:
            pass

    return cfg


def _github_get(url: str, token: str) -> dict | list:
    """Authenticated GET to GitHub API. Raises RuntimeError on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codexbar-xfce-genmon/copilot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API HTTP {e.code}: {e.read().decode()[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API network error: {e.reason}") from e


def _copilot_next_reset_unix() -> int:
    """Return Unix timestamp for 00:00 UTC on the 1st of next month."""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return int(reset.timestamp())


def copilot_credits_used(summary: dict | list) -> int | None:
    """Sum Copilot AI credits from a billing usage summary; None if no AI-credit items."""
    items = summary.get("usageItems", []) if isinstance(summary, dict) else []
    credits = [i for i in items
               if "ai_unit" in str(i.get("sku", "")).lower()
               or str(i.get("unitType", "")).lower() in ("ai-units", "aicredits")]
    if not credits:
        return None
    return round(sum(float(i.get("grossQuantity") or 0) for i in credits))


def fetch_copilot_usage() -> tuple[dict, int]:
    """Fetch Copilot AI-credit usage (legacy premium requests as fallback). Returns (raw_data, stale_seconds).

    raw_data contains 'used', 'quota', and 'reset_at' (unix).
    Raises RuntimeError if config is missing or API fails.
    """
    cfg = load_copilot_config()
    if cfg is None:
        if COPILOT_EXPLICIT:
            raise RuntimeError(
                f"copilot.conf not found: {COPILOT_CONF_FILE}\n"
                "Create it with:\n"
                f"  mkdir -p {COPILOT_CONF_DIR}\n"
                f"  echo 'GITHUB_TOKEN=ghp_...' >> {COPILOT_CONF_FILE}\n"
                f"  echo 'COPILOT_QUOTA=1500'   >> {COPILOT_CONF_FILE}\n"
                "Token: https://github.com/settings/personal-access-tokens\n"
                "Required: User permissions → Plan → Read-only"
            )
        raise RuntimeError("Copilot not configured")

    token = cfg["GITHUB_TOKEN"]
    quota = cfg["COPILOT_QUOTA"]

    # Serve from cache if fresh
    stale = 0
    cached = fresh_cache(COPILOT_CACHE_FILE)
    if cached is not None:
        return cached, 0

    state = load_backoff("copilot")
    if backoff_blocked(state, time.time()):
        return serve_during_backoff(COPILOT_CACHE_FILE, "Copilot", state)

    # Try stale cache while fetching
    stale_result = stale_cache(COPILOT_CACHE_FILE)

    COPILOT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(COPILOT_LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if stale_result:
            data, age = stale_result
            return data, age
        raise RuntimeError("Copilot fetch locked and no cache")

    try:
        # Get GitHub username (cached 1h)
        user_cached = None
        if COPILOT_USER_CACHE.exists():
            try:
                uc = json.loads(COPILOT_USER_CACHE.read_text())
                if time.time() - uc.get("_ts", 0) < 3600:
                    user_cached = uc.get("login")
            except Exception:
                pass

        if not user_cached:
            user_data = _github_get(f"{GITHUB_API_BASE}/user", token)
            login = user_data.get("login") if isinstance(user_data, dict) else None
            if not login:
                raise RuntimeError("Could not determine GitHub username")
            COPILOT_USER_CACHE.write_text(json.dumps({"login": login, "_ts": int(time.time())}))
            user_cached = login

        # Fetch usage: AI credits (current billing) first, legacy premium requests as fallback
        now = datetime.now(timezone.utc)
        billing = f"{GITHUB_API_BASE}/users/{user_cached}/settings/billing"
        summary = _github_get(
            f"{billing}/usage/summary?year={now.year}&month={now.month}&product=copilot", token)
        used = copilot_credits_used(summary)
        if used is None:
            usage_data = _github_get(f"{billing}/premium_request/usage", token)
            items = usage_data if isinstance(usage_data, list) else usage_data.get("usageItems", [])
            used = round(sum(item.get("grossQuantity", 0) for item in items))
        result = {
            "used":     used,
            "quota":    quota,
            "reset_at": _copilot_next_reset_unix(),
        }
        atomic_write_json(COPILOT_CACHE_FILE, result)
        clear_backoff("copilot")
        return result, 0

    except Exception as exc:
        record_backoff("copilot", exc)
        if stale_result:
            data, age = stale_result
            return data, age
        raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def parse_copilot(raw: dict) -> dict:
    """Normalise raw Copilot cache data into display fields."""
    used     = int(raw.get("used", 0))
    quota    = int(raw.get("quota", COPILOT_DEFAULT_QUOTA)) or COPILOT_DEFAULT_QUOTA
    reset_at = int(raw.get("reset_at", 0))

    remaining     = max(0, quota - used)
    pct_used      = min(100, round(used / quota * 100))
    pct_remaining = max(0, 100 - pct_used)

    # Reset is always 1st of next month — show in days
    diff = max(0, reset_at - int(time.time()))
    days = diff // 86400
    reset_str = f"{days}d" if days > 0 else countdown(reset_at)

    # Elapsed: how far through the current calendar month we are
    now = time.time()
    import datetime as _dt
    _now = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc)
    month_start = _dt.datetime(_now.year, _now.month, 1, tzinfo=_dt.timezone.utc).timestamp()
    month_len   = reset_at - month_start if reset_at > month_start else (30 * 86400)
    elapsed     = min(100, round((now - month_start) / month_len * 100)) if month_len > 0 else 0

    return {
        "used":          used,
        "remaining":     remaining,
        "quota":         quota,
        "pct_used":      pct_used,
        "pct_remaining": pct_remaining,
        "elapsed":       elapsed,
        "window":        int(month_len),
        "reset":         reset_str,
        "reset_at":      reset_at,
        "max_used":      pct_used,
    }


# ===========================================================================
# Copilot — format
# ===========================================================================


def copilot_text_for_mode(mode: str, f: dict) -> str:
    if mode == "copilot:usage":
        return f"[CP] {f['pct_remaining']}% left · {f['reset']}"
    raise ValueError(f"unknown copilot mode '{mode}'")


def copilot_tooltip_lines(f: dict, color: str) -> list[str]:
    return [
        window_bar_line("Monthly", f['pct_used'], f['elapsed'], f['reset'],
                        f['reset_at'], f.get('window', 0), color),
    ]


# ===========================================================================
# OpenRouter — config, fetch, parse, format
# ===========================================================================

# Fields kept from /api/v1/key. Never the "label" (it embeds a key prefix).
_OPENROUTER_KEY_FIELDS = (
    "usage", "usage_daily", "usage_weekly", "usage_monthly",
    "limit", "limit_remaining", "limit_reset",
    "free_model_daily_requests", "is_free_tier",
)

_CONF_LINE_RE = re.compile(r"(?:^|[\s:])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_conf_value(text: str, name: str) -> str | None:
    """Return NAME's value from KEY=value lines.

    Tolerates `export KEY=...`, quotes, and grep-style `file:line:KEY=...`
    prefixes (a config pasted from `grep KEY other.env`).
    """
    value = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _CONF_LINE_RE.search(line)
        if m and m.group(1) == name:
            value = m.group(2).strip().strip("\"'")
    return value or None


def load_openrouter_key() -> str | None:
    if not OPENROUTER_CONF_FILE.exists():
        return None
    try:
        return parse_conf_value(OPENROUTER_CONF_FILE.read_text(), "OPENROUTER_API_KEY")
    except OSError:
        return None


def _money(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sanitize_openrouter(key_data: dict, credits_data: dict | None, credits_error: str | None) -> dict:
    """Whitelist what is cached/displayed from the two OpenRouter responses."""
    key = {k: key_data.get(k) for k in _OPENROUTER_KEY_FIELDS if k in key_data}
    credits = None
    if credits_data is not None:
        credits = {
            "total_credits": _money(credits_data.get("total_credits")) or 0.0,
            "total_usage":   _money(credits_data.get("total_usage")) or 0.0,
        }
    return {"key": key, "credits": credits, "credits_error": credits_error}


def fetch_openrouter_usage() -> tuple[dict, int]:
    """Return (sanitised_usage, stale_age_seconds)."""
    api_key = load_openrouter_key()
    if not api_key:
        raise RuntimeError(f"OpenRouter not configured: set OPENROUTER_API_KEY in {OPENROUTER_CONF_FILE}")

    cached = fresh_cache(OPENROUTER_CACHE_FILE)
    if cached is not None:
        return cached, 0

    state = load_backoff("openrouter")
    if backoff_blocked(state, time.time()):
        return serve_during_backoff(OPENROUTER_CACHE_FILE, "OpenRouter", state)

    OPENROUTER_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OPENROUTER_LOCK_FILE.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        cached = fresh_cache(OPENROUTER_CACHE_FILE)
        if cached is not None:
            return cached, 0

        # A process that held the lock before us may have just started a backoff
        state = load_backoff("openrouter")
        if backoff_blocked(state, time.time()):
            return serve_during_backoff(OPENROUTER_CACHE_FILE, "OpenRouter", state)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    "codexbar-xfce-genmon/openrouter",
        }
        try:
            key_data = http_json(f"{OPENROUTER_API_BASE}/key", headers=headers, timeout=10).get("data") or {}
            credits_data, credits_error = None, None
            try:
                credits_data = http_json(f"{OPENROUTER_API_BASE}/credits", headers=headers, timeout=10).get("data") or {}
            except urllib.error.HTTPError as exc:
                if exc.code != 403:
                    raise
                credits_error = "403"
            usage = sanitize_openrouter(key_data, credits_data, credits_error)
            atomic_write_json(OPENROUTER_CACHE_FILE, usage)
            clear_backoff("openrouter")
            return usage, 0
        except Exception as exc:
            record_backoff("openrouter", exc)
            result = stale_cache(OPENROUTER_CACHE_FILE)
            if result:
                return result
            if is_transient_error(exc):
                raise RuntimeError("OpenRouter usage is waiting for network.") from exc
            status, _ = http_error_info(exc)
            raise RuntimeError(f"OpenRouter API request failed: {f'HTTP {status}' if status else type(exc).__name__}") from exc


def _next_utc_reset(period: str | None, now: float) -> int:
    """Next reset (unix) for an OpenRouter limit_reset period; 0 if none."""
    dt = datetime.fromtimestamp(now, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "daily":
        return int(dt.timestamp()) + 86400
    if period == "weekly":
        return int(dt.timestamp()) + (7 - dt.weekday()) * 86400
    if period == "monthly":
        nxt = dt.replace(day=1, year=dt.year + 1, month=1) if dt.month == 12 else dt.replace(day=1, month=dt.month + 1)
        return int(nxt.timestamp())
    return 0


def balance_class(balance: float | None) -> str:
    if balance is None:
        return "low"
    if balance < OPENROUTER_CRIT_BALANCE:
        return "critical"
    if OPENROUTER_MIN_BALANCE is not None and balance < OPENROUTER_MIN_BALANCE:
        return "high"
    return "low"


def parse_openrouter(raw: dict, now: float | None = None) -> dict:
    """Normalise cached OpenRouter data into display fields."""
    now = time.time() if now is None else now
    key = raw.get("key") or {}
    credits = raw.get("credits")

    balance = total = spent = None
    pct_used = 0
    if isinstance(credits, dict):
        total = _money(credits.get("total_credits")) or 0.0
        spent = _money(credits.get("total_usage")) or 0.0
        balance = total - spent
        pct_used = clamp_pct(spent / total * 100) if total > 0 else (100 if spent > 0 else 0)

    free = key.get("free_model_daily_requests")
    free_used = free_limit = None
    if isinstance(free, dict):
        free_used, free_limit = free.get("used"), free.get("limit")

    limit = _money(key.get("limit"))
    limit_remaining = _money(key.get("limit_remaining"))
    limit_reset = key.get("limit_reset")
    limit_pct = 0
    if limit:
        remaining = limit_remaining if limit_remaining is not None else limit
        limit_pct = clamp_pct((limit - remaining) / limit * 100) or 0

    return {
        "balance":         balance,
        "balance_error":   raw.get("credits_error"),
        "total":           total,
        "spent":           spent,
        "pct_used":        pct_used or 0,
        "spend_day":       _money(key.get("usage_daily")) or 0.0,
        "spend_week":      _money(key.get("usage_weekly")) or 0.0,
        "spend_month":     _money(key.get("usage_monthly")) or 0.0,
        "free_used":       free_used,
        "free_limit":      free_limit,
        "is_free_tier":    bool(key.get("is_free_tier")),
        "limit":           limit,
        "limit_remaining": limit_remaining,
        "limit_reset":     limit_reset,
        "limit_reset_at":  _next_utc_reset(limit_reset, now),
        "limit_pct":       limit_pct,
        "max_used":        limit_pct if limit else 0,
        "status_class":    worst_class(balance_class(balance),
                                       status_class_for_pct(limit_pct) if limit else "low"),
    }


def openrouter_balance_text(f: dict) -> str:
    return f"${f['balance']:.2f}" if f["balance"] is not None else "$?"


def openrouter_text_for_mode(mode: str, f: dict) -> str:
    if mode == "openrouter:balance":
        return f"[OR] {openrouter_balance_text(f)}"
    raise ValueError(f"unknown openrouter mode '{mode}'")


def openrouter_detail_rows(f: dict) -> list[tuple[str, int | None, str]]:
    """(label, bar_pct or None, text) rows shared by tooltip and popup."""
    rows: list[tuple[str, int | None, str]] = []
    if f["balance"] is not None:
        rows.append(("Balance", f["pct_used"],
                     f"${f['balance']:.2f} left · ${f['spent']:.2f}/${f['total']:.2f} used"))
    else:
        reason = f" ({f['balance_error']})" if f.get("balance_error") else ""
        rows.append(("Balance", None, f"unavailable{reason}"))
    rows.append(("Spend", None,
                 f"today ${f['spend_day']:.2f} · week ${f['spend_week']:.2f} · month ${f['spend_month']:.2f}"))
    if f["free_limit"] is not None:
        rows.append(("Free reqs", None, f"{f['free_used'] or 0}/{f['free_limit']} today"))
    if f["limit"] is not None:
        left = f["limit_remaining"] if f["limit_remaining"] is not None else f["limit"]
        reset = f" · resets {f['limit_reset']}" if f["limit_reset"] else ""
        rows.append(("Key limit", f["limit_pct"], f"${left:.2f}/${f['limit']:.2f} left{reset}"))
    return rows


def openrouter_tooltip_lines(f: dict, color: str) -> list[str]:
    lines = []
    for label, pct, text in openrouter_detail_rows(f):
        if pct is None:
            lines.append(tooltip_line(label, text, color))
        else:
            lines.append(tooltip_bar_line(label, pct, text, color))
    return lines


# ===========================================================================
# Codex — parse & format
# ===========================================================================


def parse_codex(usage: dict) -> dict:
    """Extract normalised fields from a Codex wham/usage response."""
    rate_limit = usage.get("rate_limit") or {}
    has_primary   = bool(rate_limit.get("primary_window"))
    has_secondary = bool(rate_limit.get("secondary_window"))
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

    session_window = window_seconds_from_response(primary,   SESSION_WINDOW)
    weekly_window  = window_seconds_from_response(secondary, WEEKLY_WINDOW)

    session_elapsed, _ = calc_pacing(session_used, session_reset_at, session_window)
    weekly_elapsed, _  = calc_pacing(weekly_used,  weekly_reset_at,  weekly_window)
    review_window  = window_seconds_from_response(review, REVIEW_WINDOW)
    review_elapsed, _  = calc_pacing(review_used,  review_reset_at,  review_window)

    return {
        "session_present":    has_primary,
        "session_label":      window_label(session_window, "5-hour"),
        "weekly_present":     has_secondary,
        "weekly_label":       window_label(weekly_window, "7-day"),
        "session_used":       session_used,
        "session_remaining":  session_remaining,
        "session_reset":      countdown(session_reset_at),
        "session_reset_at":   session_reset_at,
        "session_elapsed":    session_elapsed,
        "session_window":     session_window,
        "weekly_used":        weekly_used,
        "weekly_remaining":   weekly_remaining,
        "weekly_reset":       countdown(weekly_reset_at),
        "weekly_elapsed":     weekly_elapsed,
        "weekly_reset_at":    weekly_reset_at,
        "weekly_window":      weekly_window,
        "review_used":        review_used,
        "review_remaining":   review_remaining,
        "review_reset":       countdown(review_reset_at) if review_reset_at else "",
        "review_elapsed":     review_elapsed,
        "review_reset_at":    review_reset_at,
        "review_window":      review_window,
        "credits_local":      credits_local,
        "credits_cloud":      credits_cloud,
        "max_used":           max(session_used, weekly_used, review_used),
    }


def codex_text_for_mode(mode: str, f: dict) -> str:
    if mode == "codex:remaining":
        return f"[CX] {f['session_remaining']}% left · {f['session_reset']}"
    if mode == "codex:used":
        return f"[CX] {f['session_used']}% used · {f['session_reset']}"
    # Plans without a secondary window (e.g. Go) have no weekly quota to show
    has_weekly = f.get("weekly_present", True)
    if mode == "codex:weekly":
        if not has_weekly:
            return "[CX] W n/a"
        return f"[CX] W {f['weekly_remaining']}% left · {f['weekly_reset']}"
    if mode == "codex:combined":
        weekly = f"{f['weekly_remaining']}%" if has_weekly else "n/a"
        return f"[CX] S {f['session_remaining']}% · W {weekly}"
    if mode == "codex:credits":
        return f"[CX] L {f['credits_local']} · C {f['credits_cloud']}"
    raise ValueError(f"unknown codex mode '{mode}'")


def codex_tooltip_lines(f: dict, color: str) -> list[str]:
    lines = []
    if f.get("session_present", True):
        lines.append(window_bar_line(f.get("session_label", "5-hour"), f['session_used'], f['session_elapsed'],
            f['session_reset'], f.get('session_reset_at', 0), f.get('session_window', 0), color))
    if f.get("weekly_present", True):
        lines.append(window_bar_line(f.get("weekly_label", "7-day"), f['weekly_used'], f['weekly_elapsed'],
            f['weekly_reset'], f.get('weekly_reset_at', 0), f.get('weekly_window', 0), color))
    if f["review_reset"]:
        lines.append(window_bar_line("Review", f['review_used'], f['review_elapsed'],
            f['review_reset'], f.get('review_reset_at', 0), f.get('review_window', 0), color))
    lines.append(tooltip_line("Credits", f"local {f['credits_local']}, cloud {f['credits_cloud']}", color))
    return lines


# ===========================================================================
# Claude — parse & format
# ===========================================================================


def clamp_pct(value) -> int | None:
    """Coerce an API percent to 0-100.

    Returns None for non-numeric values and for absurdly large numbers (an
    epoch timestamp leaking into a percent field must not read as 100%).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v > 1000:   # NaN, or clearly not a percent
        return None
    return int(min(100, max(0, v)))


def claude_windows(usage: dict) -> tuple[dict, dict, list[dict]]:
    """Return (session, weekly, scoped) as {used, reset_at[, name]} buckets.

    Prefers the newer limits[] array; falls back per window to the legacy
    five_hour / seven_day keys when limits[] is absent or lacks that window.
    """
    session: dict | None = None
    weekly: dict | None = None
    scoped: list[dict] = []

    limits = usage.get("limits")
    if isinstance(limits, list):
        for lim in limits:
            if not isinstance(lim, dict):
                continue
            used = clamp_pct(lim.get("percent"))
            if used is None:
                continue
            bucket = {"used": used, "reset_at": iso8601_to_unix(lim.get("resets_at"))}
            kind = lim.get("kind")
            if kind == "session" and session is None:
                session = bucket
            elif kind == "weekly_all" and weekly is None:
                weekly = bucket
            elif kind == "weekly_scoped":
                scope = lim.get("scope") if isinstance(lim.get("scope"), dict) else {}
                model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
                bucket["name"] = str(model.get("display_name") or "")
                scoped.append(bucket)

    def legacy(key: str) -> dict:
        b = usage.get(key) or {}
        return {"used": clamp_pct(b.get("utilization")) or 0,
                "reset_at": iso8601_to_unix(b.get("resets_at"))}

    return session or legacy("five_hour"), weekly or legacy("seven_day"), scoped


def parse_claude(usage: dict) -> dict:
    """Extract normalised fields from a Claude api/oauth/usage response.

    utilization values are integers 0-100 (NOT floats 0.0-1.0 as the Swift
    model implies — confirmed against live API).
    """
    opus       = usage.get("seven_day_opus")    or {}
    sonnet     = usage.get("seven_day_sonnet")  or {}
    extra      = usage.get("extra_usage") or {}

    def pct(bucket: dict) -> int:
        return clamp_pct(bucket.get("utilization")) or 0

    def reset_unix(bucket: dict) -> int:
        return iso8601_to_unix(bucket.get("resets_at"))

    session, weekly, scoped_raw = claude_windows(usage)

    fh_used  = session["used"]
    sd_used  = weekly["used"]
    op_used  = pct(opus)
    so_used  = pct(sonnet)

    fh_reset = session["reset_at"]
    sd_reset = weekly["reset_at"]
    op_reset = reset_unix(opus)
    so_reset = reset_unix(sonnet)

    scoped = []
    for s in scoped_raw:
        name = s["name"] or "Scoped"
        scoped.append({
            "name":      name,
            "label":     f"{name} (7d)",
            "used":      s["used"],
            "remaining": max(0, 100 - s["used"]),
            "reset":     countdown(s["reset_at"]) if s["reset_at"] else "-",
            "reset_at":  s["reset_at"],
            "elapsed":   calc_pacing(s["used"], s["reset_at"], WEEKLY_WINDOW)[0],
        })

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
        "sd_reset_at":    sd_reset,
        "op_used":        op_used,
        "op_remaining":   op_remaining,
        "op_reset":       countdown(op_reset) if op_reset else "-",
        "op_elapsed":     op_elapsed,
        "op_reset_at":    op_reset,
        "so_used":        so_used,
        "so_remaining":   so_remaining,
        "so_reset":       countdown(so_reset) if so_reset else "-",
        "so_elapsed":     so_elapsed,
        "so_reset_at":    so_reset,
        "extra_enabled":  extra_enabled,
        "extra_util":     extra_util,
        "extra_remaining": extra_remaining,
        "extra_used_cr":  extra_used_cr,
        "extra_limit_cr": extra_limit_cr,
        "extra_reset":    "-",
        "scoped":         scoped,
        "max_used":       max([fh_used, sd_used] + [s["used"] for s in scoped]),
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
        window_bar_line("5-hour", f['fh_used'], f['fh_elapsed'], f['fh_reset'],
                        f.get('fh_reset_at', 0), SESSION_WINDOW, color),
        window_bar_line("7-day",  f['sd_used'], f['sd_elapsed'], f['sd_reset'],
                        f.get('sd_reset_at', 0), WEEKLY_WINDOW, color),
    ]
    if f["op_used"] or f["so_used"]:
        lines += [
            window_bar_line("Opus (7d)",   f['op_used'], f['op_elapsed'], f['op_reset'],
                            f.get('op_reset_at', 0), WEEKLY_WINDOW, color),
            window_bar_line("Sonnet (7d)", f['so_used'], f['so_elapsed'], f['so_reset'],
                            f.get('so_reset_at', 0), WEEKLY_WINDOW, color),
        ]
    for s in f.get("scoped", []):
        lines.append(window_bar_line(s["label"], s["used"], s["elapsed"], s["reset"],
                                     s["reset_at"], WEEKLY_WINDOW, color))
    if f["extra_enabled"]:
        lines.append(tooltip_bar_line(
            "Extra", f['extra_util'],
            f"{f['extra_util']:3d}% used · {f['extra_used_cr']}/{f['extra_limit_cr']} credits",
            color,
        ))
    return lines


# ===========================================================================
# Combined display text
# ===========================================================================


def combined_text(cx: dict | None, cl: dict | None, cp: dict | None = None, orr: dict | None = None) -> str:
    parts = []
    resets: list[int] = []
    if cx is not None:
        parts.append(f"CX {cx['session_remaining']}%")
        if cx.get("session_reset_at"):
            resets.append(cx["session_reset_at"])
    if cl is not None:
        token = f"CL {cl['fh_remaining']}%"
        hot = [s["name"] for s in cl.get("scoped", []) if s["used"] >= 90]
        if hot:
            token += " ⚠" + ",".join(hot)
        parts.append(token)
        if cl.get("fh_reset_at"):
            resets.append(cl["fh_reset_at"])
    if cp is not None:
        parts.append(f"CP {cp['remaining']}/{cp['quota']}")
        if cp.get("reset_at"):
            resets.append(cp["reset_at"])
    if orr is not None:
        parts.append(f"OR {openrouter_balance_text(orr)}")
    if not parts:
        return "N/A"
    text = " · ".join(parts)
    if resets:
        nearest = countdown(min(resets))
        text += f" · {nearest}"
    return text


# ===========================================================================
# Combined gauge text
# ===========================================================================

GAUGE_GLYPHS = "▁▂▃▄▅▆▇█"


def gauge_glyph(pct: int) -> str:
    """Block glyph for % used: 0 -> ▁, any use >= ▂, only 100 -> █."""
    if pct <= 0:
        return GAUGE_GLYPHS[0]
    if pct >= 100:
        return GAUGE_GLYPHS[-1]
    return GAUGE_GLYPHS[min(6, max(1, round(pct * 7 / 100)))]


def short_window_label(seconds: int | None, fallback: str) -> str:
    """Compact legend name for a window length, e.g. 18000 -> "5h"."""
    label = window_label(seconds, fallback)
    return {"5-hour": "5h", "7-day": "7d", "30-day": "30d"}.get(label, label)


def gauge_balance_text(balance: float | None) -> str:
    if balance is None:
        return "$?"
    return f"${balance:.1f}" if balance < 10 else f"${balance:.0f}"


def gauge_segments(
    cx: dict | None, cl: dict | None, cp: dict | None, orr: dict | None,
    errors: dict | None = None, activity: dict | None = None, now: float | None = None,
) -> list[dict]:
    """One {label, windows, text, cls, error, idle} segment per active source.

    windows is a list of (legend name, % used); text is the glyph-less value
    (OpenRouter balance). errors maps a label to True when that source failed
    with no data to show. A source is idle unless it was active within
    ACTIVE_HOURS (per the activity state) or a window is >= SHOW_AT_PCT.
    """
    errors = errors or {}
    activity = activity or {}
    now = time.time() if now is None else now
    segs: list[dict] = []

    def recently_active(label: str) -> bool:
        try:
            active_at = float((activity.get(label) or {}).get("active_at") or 0)
        except (AttributeError, TypeError, ValueError):
            return False
        return active_at > 0 and now - active_at < ACTIVE_HOURS * 3600

    def add(label: str, windows: list[tuple[str, int]], text: str | None = None,
            cls: str | None = None, busy: bool = False) -> None:
        if cls is None:
            cls = status_class_for_pct(max((p for _, p in windows), default=0))
        busy = busy or recently_active(label) or any(p >= SHOW_AT_PCT for _, p in windows)
        segs.append({"label": label, "windows": windows, "text": text,
                     "cls": cls, "error": False, "idle": not busy})

    if cl is not None:
        add("CL", [("5h", cl["fh_used"]), ("7d", cl["sd_used"])]
            + [(s["name"], s["used"]) for s in cl.get("scoped", [])])
    elif errors.get("CL"):
        segs.append({"label": "CL", "error": True})
    if cx is not None:
        wins = []
        if cx.get("session_present", True):
            wins.append((short_window_label(cx.get("session_window"), "5h"), cx["session_used"]))
        if cx.get("weekly_present", True):
            wins.append((short_window_label(cx.get("weekly_window"), "7d"), cx["weekly_used"]))
        add("CX", wins)
    elif errors.get("CX"):
        segs.append({"label": "CX", "error": True})
    if cp is not None:
        add("CP", [("mo", cp["pct_used"])])
    elif errors.get("CP"):
        segs.append({"label": "CP", "error": True})
    if orr is not None:
        floor = OPENROUTER_MIN_BALANCE if OPENROUTER_MIN_BALANCE is not None else OPENROUTER_CRIT_BALANCE
        bal = orr["balance"]
        busy = (bal is None or bal < floor or (orr.get("spend_day") or 0) > 0
                or bool(orr["limit"] and orr["limit_pct"] >= SHOW_AT_PCT))
        add("OR", [], gauge_balance_text(bal), orr["status_class"], busy)
    elif errors.get("OR"):
        segs.append({"label": "OR", "error": True})
    return segs


def gauge_text(segments: list[dict], hide_idle: bool = True) -> tuple[str, str, str]:
    """Return (pango markup, plain text, plain legend) for the combined gauge."""
    shown = [s for s in segments if s["error"] or not (hide_idle and s["idle"])]
    hidden = [s["label"] for s in segments if s not in shown]
    hidden_note = [f"hidden: {' '.join(hidden)} (idle >{ACTIVE_HOURS:g}h)"] if hidden else []
    if not shown:
        return ("<span weight=\"Semibold\">AI ✓</span>", "AI ✓",
                " · ".join(["bars = % used", "all sources idle"] + hidden_note))

    markup: list[str] = []
    plain: list[str] = []
    legend: list[str] = []
    for s in shown:
        if s["error"]:
            markup.append(pango(f"{s['label']}!", COLOR_MAP["critical"]))
            plain.append(f"{s['label']}!")
            continue
        body = "".join(
            f"<span fgcolor=\"{saxutils.escape(COLOR_MAP[status_class_for_pct(p)])}\">{gauge_glyph(p)}</span>"
            for _, p in s["windows"]
        )
        value = "".join(gauge_glyph(p) for _, p in s["windows"])
        if s["text"] is not None:
            body += pango(s["text"], COLOR_MAP[s["cls"]], "Normal")
            value += s["text"]
        markup.append(f"{pango(s['label'], COLOR_MAP[s['cls']])} {body}")
        plain.append(f"{s['label']} {value}")
        if s["windows"]:
            legend.append(" ".join([s["label"]] + [n for n, _ in s["windows"]]))
        else:
            legend.append(f"{s['label']} balance")
    return "  ".join(markup), "  ".join(plain), " · ".join(["bars = % used"] + legend + hidden_note)


# ===========================================================================
# Activity tracking (decides which gauge sources are idle)
# ===========================================================================

ACTIVITY_FILE = BACKOFF_DIR / "activity.json"
ACTIVITY_LOCK_FILE = BACKOFF_DIR / ".activity.lock"


def activity_sigs(cx: dict | None, cl: dict | None, cp: dict | None, orr: dict | None) -> dict:
    """Per-source {window: used value}; any increase between runs is activity."""
    sigs: dict = {}
    if cl is not None:
        sig = {"5h": cl["fh_used"], "7d": cl["sd_used"]}
        sig.update({f"scoped:{s['name']}": s["used"] for s in cl.get("scoped", [])})
        sigs["CL"] = sig
    if cx is not None:
        sig = {}
        if cx.get("session_present", True):
            sig["session"] = cx["session_used"]
        if cx.get("weekly_present", True):
            sig["weekly"] = cx["weekly_used"]
        sigs["CX"] = sig
    if cp is not None:
        sigs["CP"] = {"used": cp.get("used", cp.get("pct_used", 0))}
    if orr is not None and orr.get("spent") is not None:
        sigs["OR"] = {"usage": orr["spent"]}
    return sigs


def plan_activity(sigs: dict, fresh: dict, state: dict, now: float) -> dict:
    """Return the new activity state. Pure.

    state maps a source label to {sig, active_at}. Only sources with fresh
    data (fresh[label] true) are updated: a used value that rose since the
    stored sig marks the source active now; drops (window resets) don't.
    A first sighting stores the sig with active_at 0 (unknown = not active).
    """
    new_state = dict(state or {})
    for label, sig in sigs.items():
        if not fresh.get(label):
            continue
        prev = new_state.get(label)
        if not isinstance(prev, dict) or not isinstance(prev.get("sig"), dict):
            new_state[label] = {"sig": sig, "active_at": 0}
            continue
        old = prev["sig"]
        rose = False
        for k, v in sig.items():
            try:
                rose = rose or (k in old and float(v) > float(old[k]))
            except (TypeError, ValueError):
                pass
        try:
            active_at = float(prev.get("active_at") or 0)
        except (TypeError, ValueError):
            active_at = 0
        new_state[label] = {"sig": sig, "active_at": now if rose else active_at}
    return new_state


def read_activity() -> dict:
    try:
        state = read_json(ACTIVITY_FILE)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def update_activity(sigs: dict, fresh: dict) -> dict:
    """read-plan-write activity.json under a lock. Never raises; {} on failure."""
    try:
        ACTIVITY_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_LOCK_FILE.open("a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            state = read_activity()
            new_state = plan_activity(sigs, fresh, state, time.time())
            if new_state != state:
                atomic_write_json(ACTIVITY_FILE, new_state)
            return new_state
    except Exception:
        return {}


# ===========================================================================
# Threshold notifications
# ===========================================================================

ALERTS_FILE = BACKOFF_DIR / "alerts.json"
ALERTS_LOCK_FILE = BACKOFF_DIR / ".alerts.lock"
ALERT_KEY_SLACK = 600   # reset_at jitter (s) still treated as the same window


def alert_thresholds() -> list[int]:
    """CODEXBAR_XFCE_ALERT_THRESHOLDS (comma list); unset = 80,90,100, empty = off."""
    raw = os.environ.get("CODEXBAR_XFCE_ALERT_THRESHOLDS")
    if raw is None:
        raw = "80,90,100"
    out: set[int] = set()
    for part in raw.split(","):
        try:
            out.add(min(100, max(1, int(part.strip()))))
        except ValueError:
            continue
    return sorted(out)


def alert_windows(cx: dict | None, cl: dict | None, cp: dict | None, orr: dict | None = None) -> list[dict]:
    """Flatten parsed sources into {provider, window, used, reset_at, reset} rows."""
    rows: list[dict] = []

    def add(provider: str, window: str, used: int, reset_at, reset: str) -> None:
        rows.append({"provider": provider, "window": window, "used": used,
                     "reset_at": int(reset_at or 0), "reset": reset})

    if cx is not None:
        if cx.get("session_present", True):
            add("Codex", cx.get("session_label", "5-hour"), cx["session_used"],
                cx.get("session_reset_at"), cx["session_reset"])
        if cx.get("weekly_present", True):
            add("Codex", cx.get("weekly_label", "7-day"), cx["weekly_used"],
                cx.get("weekly_reset_at"), cx["weekly_reset"])
        if cx.get("review_reset"):
            add("Codex", "Review", cx["review_used"], cx.get("review_reset_at"), cx["review_reset"])
    if cl is not None:
        add("Claude", "5-hour", cl["fh_used"], cl.get("fh_reset_at"), cl["fh_reset"])
        add("Claude", "7-day", cl["sd_used"], cl.get("sd_reset_at"), cl["sd_reset"])
        if cl["op_used"]:
            add("Claude", "Opus (7d)", cl["op_used"], cl.get("op_reset_at"), cl["op_reset"])
        if cl["so_used"]:
            add("Claude", "Sonnet (7d)", cl["so_used"], cl.get("so_reset_at"), cl["so_reset"])
        for sc in cl.get("scoped", []):
            add("Claude", sc["label"], sc["used"], sc["reset_at"], sc["reset"])
    if cp is not None:
        add("Copilot", "Monthly", cp["pct_used"], cp.get("reset_at"), cp["reset"])
    if orr is not None:
        if orr["limit"]:
            reset_at = orr["limit_reset_at"]
            add("OpenRouter", "Key limit", orr["limit_pct"], reset_at,
                countdown(reset_at) if reset_at else "never")
            if not reset_at:
                rows[-1]["no_reset"] = True
        bal, floor = orr["balance"], OPENROUTER_ALERT_BALANCE
        if bal is not None and floor is not None and bal < floor:
            rows.append({
                "provider": "OpenRouter", "window": "balance", "used": 100,
                "reset_at": _next_utc_reset("daily", time.time()),   # once per day
                "reset": "", "thresholds": [100],
                "title": "OpenRouter balance low",
                "body": f"OpenRouter balance ${bal:.2f} (below ${floor:.2f})",
                "urgency": "critical" if bal < OPENROUTER_CRIT_BALANCE else "normal",
            })
    return rows


def _alert_key_reset(key: str) -> int:
    try:
        return int(key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return 0


def plan_alerts(windows: list[dict], state: dict, thresholds: list[int], now: float) -> tuple[list[dict], dict]:
    """Decide which notifications to send. Pure: returns (to_fire, new_state).

    state maps "provider:window:reset_at" -> highest threshold already sent.
    A new reset_at is a fresh key, so alerts re-arm each window; keys whose
    reset_at has passed are pruned. Windows whose reset_at has passed (stale
    cache served after a reset) are skipped, or the pruned key would re-fire
    on every tick. A window row may override "thresholds"
    and carry its own "title"/"body"/"urgency".
    """
    new_state = {
        k: v for k, v in (state or {}).items()
        if not (0 < _alert_key_reset(k) < now)
    }
    fire: list[dict] = []
    for w in windows:
        reset_at = int(w.get("reset_at") or 0)
        if reset_at <= 0 and not w.get("no_reset"):
            continue
        if 0 < reset_at <= now:
            continue    # data predates the reset; nothing current to alert on
        crossed = [t for t in w.get("thresholds", thresholds) if w["used"] >= t]
        top = max(crossed, default=0)
        prefix = f"{w['provider']}:{w['window']}:"
        key = next(
            (k for k in new_state
             if k.startswith(prefix) and abs(_alert_key_reset(k) - reset_at) <= ALERT_KEY_SLACK),
            f"{prefix}{reset_at}",
        )
        try:
            prev = int(new_state.get(key, 0))
        except (TypeError, ValueError):
            prev = 0
        if w.get("no_reset") and top < prev:
            # No reset time to re-arm on: re-arm once usage falls back below
            # a threshold (e.g. the key limit was raised)
            if top:
                new_state[key] = top
            else:
                new_state.pop(key, None)
            continue
        if top <= prev:
            continue
        new_state[key] = top
        fire.append({
            "title":   w.get("title") or f"{w['provider']} usage at {top}%",
            "body":    w.get("body") or f"{w['provider']} {w['window']} {w['used']}% used · resets {w['reset']}",
            "urgency": w.get("urgency") or ("critical" if top >= 100 else "normal"),
        })
    return fire, new_state


def run_alerts(windows: list[dict]) -> None:
    """Send due notifications via notify-send. Never raises.

    read-plan-write runs under a lock so panel plugins ticking at the same
    moment don't both send an alert; notifications go out only after the
    new state is saved.
    """
    try:
        thresholds = alert_thresholds()
        if not thresholds:
            return
        ALERTS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ALERTS_LOCK_FILE.open("a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                state = read_json(ALERTS_FILE)
                if not isinstance(state, dict):
                    state = {}
            except Exception:
                state = {}
            fire, new_state = plan_alerts(windows, state, thresholds, time.time())
            if new_state != state:
                atomic_write_json(ALERTS_FILE, new_state)
        for alert in fire:
            notify_send(alert["title"], alert["body"], alert["urgency"])
    except Exception:
        pass


# ===========================================================================
# Main format_message
# ===========================================================================


def format_message(
    codex_usage: dict | None,
    codex_plan: str,
    codex_stale: int,
    claude_usage: dict | None,
    claude_stale: int,
    copilot_usage: dict | None = None,
    copilot_stale: int = 0,
    backoffs: dict | None = None,
    openrouter_usage: dict | None = None,
    openrouter_stale: int = 0,
) -> tuple[str, str, str, str, str, str]:
    """
    Return (text, tooltip, icon, color, resolved_mode_for_click, title).
    resolved_mode is the actual mode string used (after resolving 'rotate').
    """
    resolved_mode = pick_rotate_mode() if MODE == "rotate" else MODE
    backoffs = backoffs or {}
    now = time.time()

    cx = parse_codex(codex_usage) if codex_usage else None
    cl = parse_claude(claude_usage) if claude_usage else None
    cp = parse_copilot(copilot_usage) if copilot_usage else None
    orr = parse_openrouter(openrouter_usage) if openrouter_usage else None

    # Determine worst-case status colour across all active sources
    max_used = max(
        (cx["max_used"] if cx else 0),
        (cl["max_used"] if cl else 0),
        (cp["max_used"] if cp else 0),
    )
    status_class = worst_class(status_class_for_pct(max_used), orr["status_class"] if orr else "low")
    color = COLOR_MAP[status_class]

    # ------------------------------------------------------------------
    # Panel text
    # ------------------------------------------------------------------
    is_codex_mode   = resolved_mode.startswith("codex:")
    is_claude_mode  = resolved_mode.startswith("claude:")
    is_copilot_mode = resolved_mode.startswith("copilot:")
    is_openrouter_mode = resolved_mode.startswith("openrouter:")

    if resolved_mode == "combined":
        text = combined_text(cx, cl, cp, orr)
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
    elif is_copilot_mode:
        if cp is None:
            text = "[CP] N/A"
        else:
            text = copilot_text_for_mode(resolved_mode, cp)
    elif is_openrouter_mode:
        if orr is None:
            text = "[OR] N/A"
        else:
            text = openrouter_text_for_mode(resolved_mode, orr)
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
    any_stale = (
        (codex_stale > 0 and is_codex_mode)
        or (claude_stale > 0 and is_claude_mode)
        or (copilot_stale > 0 and is_copilot_mode)
        or (openrouter_stale > 0 and is_openrouter_mode)
        or (resolved_mode == "combined" and (codex_stale > 0 or claude_stale > 0 or copilot_stale > 0
                                             or openrouter_stale > 0))
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
            tooltip_parts.append(tooltip_line("Codex Data Age", data_age_text(codex_stale, backoffs.get("codex"), now), COLOR_MAP["high"]))

    if cl is not None:
        cl_color = COLOR_MAP[status_class_for_pct(cl["max_used"])]
        if tooltip_parts:
            tooltip_parts.append("")   # blank separator line
        tooltip_parts.append(tooltip_header("Claude"))
        tooltip_parts += claude_tooltip_lines(cl, cl_color)
        if claude_stale > 0:
            tooltip_parts.append(tooltip_line("Claude Data Age", data_age_text(claude_stale, backoffs.get("claude"), now), COLOR_MAP["high"]))

    if cp is not None:
        cp_color = COLOR_MAP[status_class_for_pct(cp["max_used"])]
        if tooltip_parts:
            tooltip_parts.append("")   # blank separator line
        tooltip_parts.append(tooltip_header("GitHub Copilot"))
        tooltip_parts += copilot_tooltip_lines(cp, cp_color)
        if copilot_stale > 0:
            tooltip_parts.append(tooltip_line("Copilot Data Age", data_age_text(copilot_stale, backoffs.get("copilot"), now), COLOR_MAP["high"]))

    if orr is not None:
        or_color = COLOR_MAP[orr["status_class"]]
        if tooltip_parts:
            tooltip_parts.append("")   # blank separator line
        tooltip_parts.append(tooltip_header("OpenRouter"))
        tooltip_parts += openrouter_tooltip_lines(orr, or_color)
        if openrouter_stale > 0:
            tooltip_parts.append(tooltip_line("OpenRouter Data Age", data_age_text(openrouter_stale, backoffs.get("openrouter"), now), COLOR_MAP["high"]))

    tooltip_parts.append(tooltip_line("Mode", resolved_mode, color))
    if MODE == "rotate":
        tooltip_parts.append(tooltip_line("Rotate Every", f"{ROTATE_SECONDS}s", color))

    title_parts = []
    if cx is not None:
        title_parts.append(codex_plan)
    if cl is not None:
        title_parts.append("Claude")
    if cp is not None:
        title_parts.append("Copilot")
    if orr is not None:
        title_parts.append("OpenRouter")
    title = " + ".join(title_parts) if title_parts else "Usage"

    return text, "\n".join(tooltip_parts), icon, color, resolved_mode, title


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    codex_usage_data: dict | None = None
    codex_plan: str = "Codex"
    codex_stale: int = 0
    codex_error: str | None = None

    claude_usage_data: dict | None = None
    claude_stale: int = 0
    claude_error: str | None = None

    copilot_usage_data: dict | None = None
    copilot_stale: int = 0
    copilot_error: str | None = None

    openrouter_usage_data: dict | None = None
    openrouter_stale: int = 0
    openrouter_error: str | None = None

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

    if SHOW_COPILOT:
        try:
            copilot_usage_data, copilot_stale = fetch_copilot_usage()
        except Exception as exc:
            copilot_error = str(exc)
    elif COPILOT_EXPLICIT:
        # --model=copilot was explicitly requested but conf file is missing
        copilot_error = (
            "copilot.conf not found. Create ~/.config/codexbar-xfce-genmon/copilot.conf "
            "with GITHUB_TOKEN=<token> to enable Copilot support."
        )
        print(copilot_error, file=sys.stderr)

    if SHOW_OPENROUTER:
        try:
            openrouter_usage_data, openrouter_stale = fetch_openrouter_usage()
        except Exception as exc:
            openrouter_error = str(exc)

    _cx = _cl = _cp = _or = None
    try:
        _cx = parse_codex(codex_usage_data) if codex_usage_data else None
        _cl = parse_claude(claude_usage_data) if claude_usage_data else None
        _cp = parse_copilot(copilot_usage_data) if copilot_usage_data else None
        _or = parse_openrouter(openrouter_usage_data) if openrouter_usage_data else None
    except Exception:
        pass

    # Only panel ticks with fresh (not stale/backoff) data update activity
    activity: dict = {}
    try:
        if ACTION == "panel":
            fresh = {
                "CX": codex_usage_data is not None and codex_stale == 0,
                "CL": claude_usage_data is not None and claude_stale == 0,
                "CP": copilot_usage_data is not None and copilot_stale == 0,
                "OR": openrouter_usage_data is not None and openrouter_stale == 0,
            }
            activity = update_activity(activity_sigs(_cx, _cl, _cp, _or), fresh)
        else:
            activity = read_activity()
    except Exception:
        activity = {}

    try:
        text, tooltip, icon, color, resolved_mode_for_click, title = format_message(
            codex_usage_data, codex_plan, codex_stale,
            claude_usage_data, claude_stale,
            copilot_usage_data, copilot_stale,
            {p: load_backoff(p) for p in ("codex", "claude", "copilot", "openrouter")},
            openrouter_usage_data, openrouter_stale,
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
        if copilot_error:
            tooltip += f"\n{tooltip_line('Copilot Error', copilot_error, COLOR_MAP['critical'])}"
            if copilot_usage_data is None:
                if "[CP]" in text:
                    text = text.replace("[CP]", "[CP!]")
                elif COPILOT_EXPLICIT and "[CP]" not in text:
                    text = "[CP!] " + text if text != "N/A" else "[CP!]"
        if openrouter_error:
            tooltip += f"\n{tooltip_line('OpenRouter Error', openrouter_error, COLOR_MAP['critical'])}"
            if openrouter_usage_data is None:
                if "[OR]" in text:
                    text = text.replace("[OR]", "[OR!]")
                elif resolved_mode_for_click == "combined":
                    text = "OR!" if text == "N/A" else f"{text} · OR!"
        txt_markup = pango(text, color)
        if resolved_mode_for_click == "combined" and PANEL_STYLE == "gauge":
            errors = {
                "CX": bool(codex_error) and codex_usage_data is None,
                "CL": bool(claude_error) and claude_usage_data is None,
                "CP": bool(copilot_error) and copilot_usage_data is None,
                "OR": bool(openrouter_error) and openrouter_usage_data is None,
            }
            txt_markup, text, legend = gauge_text(gauge_segments(_cx, _cl, _cp, _or, errors, activity), HIDE_IDLE)
            if codex_stale > 0 or claude_stale > 0 or copilot_stale > 0 or openrouter_stale > 0:
                txt_markup += pango(" ~", COLOR_MAP["high"])
                text += " ~"
            tooltip = f"<span alpha=\"60%\">{saxutils.escape(legend)}</span>\n{tooltip}"
        icon_click, text_click = default_clicks(resolved_mode_for_click)
    except Exception as exc:
        color = COLOR_MAP["critical"]
        text = "Usage N/A"
        tooltip = saxutils.escape(str(exc))
        icon = ICON_MAP.get("codex:remaining", "applications-development")
        title = "Usage"
        resolved_mode_for_click = "rotate"
        icon_click, text_click = default_clicks(resolved_mode_for_click)
        txt_markup = pango(text, color)

    if ACTION == "popup":
        show_popup(title, build_popup_body(_cx, _cl, _cp, _or), sys.stdout.isatty())
        raise SystemExit(0)

    if ACTION == "panel":
        run_alerts(alert_windows(_cx, _cl, _cp, _or))

    if SHOW_ICON:
        print(f"<icon>{saxutils.escape(icon)}</icon>")
        if icon_click:
            print(f"<click>{saxutils.escape(icon_click)}</click>")
    print(f"<txt>{txt_markup}</txt>")
    if text_click:
        print(f"<txtclick>{saxutils.escape(text_click)}</txtclick>")
    print(f"<tool>{tooltip}</tool>")


if __name__ == "__main__":
    main()
