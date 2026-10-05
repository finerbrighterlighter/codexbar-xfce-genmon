# codexbar-xfce-genmon

XFCE Generic Monitor (genmon) plugin script that shows AI usage limits in the panel. It needs only Python 3 (stdlib) and Bash.

## Features

- **Sources:** OpenAI Codex, Anthropic Claude, GitHub Copilot (AI credits) and OpenRouter (credits). Each one is optional and they can be shown in any combination.
- **Per-window bars:** each rate-limit window has a bar with a `│` cursor at the elapsed-time position, plus `used · elapsed · resets` text.
- **Pace / ETA:** each window shows `↑` / `→` / `↓` (burning faster than, on, or slower than an even pace) and either `lasts` or `empty in 2h 30m`.
- **Claude scoped limits:** Claude data comes from the `limits[]` array, so model-scoped weekly caps (e.g. `Fable (7d)`) get their own row, count toward the panel color, and add `⚠Fable` to the panel text at 90% or more.
- **Codex window labels** come from the API (`5-hour`, `7-day`, `30-day`, …). Windows the API doesn't return are hidden.
- **Backoff:** after HTTP 429 (honoring `Retry-After`) or another API error, the script waits before calling that provider again (60s doubling up to 1h) and serves cached data in the meantime. The wait survives genmon re-running the script.
- **Alerts:** `notify-send` fires once when a window crosses 80 / 90 / 100% (configurable) and re-arms when the window resets.
- **Popup:** clicking the panel shows a usage summary as a desktop notification. Run `--popup` in a terminal to print it instead.

## Files

| File | Purpose |
|---|---|
| `codexbar-xfce-genmon` | Bash entrypoint (flag parsing) for genmon |
| `codexbar_xfce_genmon.py` | Fetch, cache, backoff, formatting, alerts |
| `copilot.conf.example` | Template for `copilot.conf` |
| `openrouter.conf.example` | Template for `openrouter.conf` |

`copilot.conf` and `openrouter.conf` hold secrets and are git-ignored.

## Install

1. Install `xfce4-genmon-plugin` and add a **Generic Monitor** item to the panel.
2. Clone this repo, then optionally symlink the entrypoint into your PATH:
   ```bash
   ln -sf "$(pwd)/codexbar-xfce-genmon" ~/.local/bin/codexbar-xfce-genmon
   ```
3. Set the genmon command (see [Panel command](#panel-command)) and a refresh period of 30–60 s.
4. Optional: install `libnotify` (`notify-send`) for alerts and the click popup.

## Provider setup

Codex and Claude are always tried. Copilot and OpenRouter are enabled automatically when their config file exists. Each config file is looked up in the **repo directory first**, then in `~/.config/codexbar-xfce-genmon/`.

### Codex

Run `codex login`. Credentials are read from `~/.codex/auth.json`, read-only. An expired token is never refreshed here (that would rotate the refresh token the codex CLI holds); the last cached data is shown until the next `codex` run refreshes it.

### Claude

Credentials are read from the first of these that exists:

1. `~/.claude/.credentials.json`: written by Claude Code (`claude`). No extra setup.
2. `~/.config/claude-usage-bar/credentials.json`: written by the claude-usage-bar app.
3. `~/.config/claude-usage-bar/token`: legacy plain access token.

Credentials are read-only. An expired token is never refreshed here (that would rotate the refresh token Claude Code holds); the last cached data is shown until the next `claude` run refreshes it.

### GitHub Copilot

1. Create a **fine-grained** token at <https://github.com/settings/personal-access-tokens> with *User permissions → Plan: Read-only* (no repo access needed).
2. Create the config:
   ```bash
   cp copilot.conf.example copilot.conf && chmod 600 copilot.conf
   # set GITHUB_TOKEN=...
   ```
   Usage, quota and reset date come from `copilot_internal/user` (the endpoint Copilot clients use to check your quota; undocumented), so the numbers match what Copilot enforces. If that endpoint fails, the billing usage summary (AI credits, then legacy premium requests) is used with `COPILOT_QUOTA` as the quota.
3. Check: `./codexbar-xfce-genmon --popup --model=copilot`

### OpenRouter

1. Create a key at <https://openrouter.ai/settings/keys>.
2. Create the config:
   ```bash
   cp openrouter.conf.example openrouter.conf && chmod 600 openrouter.conf
   # set OPENROUTER_API_KEY=sk-or-v1-...
   ```
3. Check: `./codexbar-xfce-genmon --popup --model=openrouter`

The script calls `GET /api/v1/key` (spend, limits, free-model requests) and `GET /api/v1/credits` (balance). If `/credits` returns 403, the balance shows as unavailable and the key data is still shown. The key's `label` field is never cached or displayed.

## Panel command

```bash
codexbar-xfce-genmon                         # rotate through all active modes (default)
codexbar-xfce-genmon combined                # all sources in one line
codexbar-xfce-genmon --model=claude          # one source (repeatable: --model=codex --model=claude)
codexbar-xfce-genmon claude:5h --no-icon     # fixed mode, no icon
codexbar-xfce-genmon --popup                 # print the popup summary and exit
```

`--model=` accepts `codex`, `claude`, `copilot`, `openrouter`. The legacy flags `--source=codex|claude|both`, `--codex-only`, `--claude-only` and `--copilot-only` still work.

### Modes

| Mode | Panel text |
|---|---|
| `combined` | `CL ▂▆█  CX ▂  CP ▂  OR $1.4` (gauge, default) / `CX 88% · CL 96% ⚠Fable · CP 1279/1500 · OR $7.86 · 4h 35m` (`CODEXBAR_XFCE_PANEL_STYLE=text`) |
| `codex:remaining` / `codex:used` | `[CX] 58% left · 2h 14m` / `[CX] 42% used · 2h 14m` |
| `codex:weekly` / `codex:combined` / `codex:credits` | `[CX] W 83% left · 4d 6h` / `[CX] S 58% · W 83%` / `[CX] L 120-180 · C 40-60` |
| `claude:5h` / `claude:7d` | `[CL] 96% left · 4h 35m` / `[CL] W 23% left · 13h 25m` |
| `claude:opus` / `claude:sonnet` / `claude:extra` | legacy per-model and extra-usage views |
| `copilot:usage` | `[CP] 93% left · 28d` |
| `openrouter:balance` | `[OR] $7.86` |
| `rotate` | cycles through the modes in `CODEXBAR_XFCE_ROTATE_MODES` |

In `combined` (gauge style), each source is a label followed by one block glyph per window showing the **used** percentage, from `▁` (0%) to `█` (100%); any use shows at least `▂`. `CL` shows 5-hour, 7-day, then each scoped limit (e.g. Fable); `CX` each window the plan has (Go has only the 30-day one); `CP` the monthly quota; `OR` the balance (`$7.9`, whole dollars from $10; `$7.9 · 9d` once the month-average runway drops under 30 days). Each glyph is colored by its own percentage and each label by its worst window. Idle sources are hidden, where idle means no recent **activity**: a source is shown only if any window's used value (or OpenRouter's total usage) rose within the last `CODEXBAR_XFCE_ACTIVE_HOURS` (default 6), or any window is at least `CODEXBAR_XFCE_SHOW_AT_PCT`% used (default 80). OpenRouter is also shown while today's spend (`usage_daily`) is above zero or the balance is below `CODEXBAR_XFCE_OPENROUTER_MIN_BALANCE`. Activity is tracked in `~/.cache/codexbar-xfce-genmon/activity.json` by comparing each panel run's fresh data with the last one; drops (window resets) and stale or backed-off data don't count, and a source seen for the first time starts out idle. If all are hidden the panel shows `AI ✓`. A failed source shows as `CL!` in red and is never hidden. Reset times are in the tooltip, whose first line is a legend of the glyphs shown plus the hidden sources, e.g. `bars = % used · CL 5h 7d Fable · hidden: CX CP OR (idle >6h)`.

In `combined` with `CODEXBAR_XFCE_PANEL_STYLE=text`, `CX`/`CL` show the remaining percentage of the first window, `CP` shows remaining/quota, `OR` shows the balance, and the time at the end is the nearest reset. A trailing `~` means some data is stale. `[CX!]` / `[CL!]` / `[CP!]` / `[OR!]` (or a trailing `OR!` in text-style `combined`) means that source failed to load. `W n/a` in `codex:weekly` / `codex:combined` means the Codex plan has no weekly window (e.g. Go).

Outside the gauge, the panel color is the worse of two signals:

- the highest used percentage across all windows: green < 50 ≤ yellow < 75 ≤ orange < 90 ≤ red
- the OpenRouter balance: orange below `CODEXBAR_XFCE_OPENROUTER_MIN_BALANCE`, red below $0.50

### Tooltip

Real output, with values altered:

```
bars = % used · CL 5h 7d Fable · CX 30d · CP mo
Go (Codex)
30-day       █░│░░░░░░░   12% used ·  21% elapsed · resets 23d 16h · ↓ lasts
Credits      local 0, cloud 0

Claude
5-hour       │░░░░░░░░░    4% used ·   8% elapsed · resets 4h 35m · → lasts
7-day        ████████░│   77% used ·  92% elapsed · resets 13h 25m · ↓ lasts
Fable (7d)   █████████│  100% used ·  92% elapsed · resets 13h 25m
Claude Data Age  cached 1m · retry 21:44 (429)

GitHub Copilot
Monthly      │░░░░░░░░░    7% used ·   8% elapsed · resets 28d · → lasts

OpenRouter
Balance      $7.77 left · ~321 days at $0.02/day (month avg)
Bought       spent $17.23 of $25.00 bought
Spend        today $0.00 · week $0.20 · month $0.02
Free reqs    0/1000 today
Mode         combined
```

Some rows appear only when the API returns data for them: `Key cap` (OpenRouter, the only OpenRouter row with a bar), `Review` (Codex code review), and `Opus (7d)` / `Sonnet (7d)` / `Extra` (Claude). When data is served from cache because of an error or backoff, a `… Data Age` row says how old it is and when the next retry is.

### Popup

Clicking the panel runs `codexbar-xfce-genmon popup <mode>`. Without a terminal (a panel click) it shows the summary as a `notify-send` notification. In a terminal it prints the summary. Real output, with values altered:

```
Codex
────────────────────────────────────────────────────────
  30-day      ██░░│░░░░░░░░░░░░░░░   12% used  ·  21% elapsed · resets 23d 16h · ↓ lasts
  Credits     local 0  ·  cloud 0

Claude
────────────────────────────────────────────────────────
  5-hour      █│░░░░░░░░░░░░░░░░░░    4% used  ·   8% elapsed · resets 4h 35m · → lasts
  7-day       ███████████████░░░│░   77% used  ·  92% elapsed · resets 13h 25m · ↓ lasts
  Fable (7d)  ██████████████████│█  FULL       ·  92% elapsed · resets 13h 25m

GitHub Copilot
────────────────────────────────────────────────────────
  Monthly     █│░░░░░░░░░░░░░░░░░░    7% used  ·   8% elapsed · resets 28d · → lasts

OpenRouter
────────────────────────────────────────────────────────
  Balance     $7.77 left · ~321 days at $0.02/day (month avg)
  Bought      spent $17.23 of $25.00 bought
  Spend       today $0.00 · week $0.20 · month $0.02
  Free reqs   0/1000 today
```

How to read a row: the bar fills to **used**, and `│` marks **elapsed** time in the window. Pace is left out when nothing has been used, when the limit is already full, or when the window has no reset time.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CODEXBAR_XFCE_MODELS` | all configured | Comma list of `codex,claude,copilot,openrouter` (`--model=` sets this) |
| `CODEXBAR_XFCE_SOURCE` | — | Legacy: `codex` / `claude` / `both` |
| `CODEXBAR_XFCE_ROTATE_SECONDS` | `15` | Seconds per mode in `rotate` |
| `CODEXBAR_XFCE_ROTATE_MODES` | all active modes + `combined` | Comma list of modes to rotate through |
| `CODEXBAR_XFCE_SHOW_ICON` | `1` | `0` hides the icon (same as `--no-icon`) |
| `CODEXBAR_XFCE_PANEL_STYLE` | `gauge` | `combined` panel text: `gauge` (block glyph per window) or `text` (percentages and nearest reset) |
| `CODEXBAR_XFCE_HIDE_IDLE` | `1` | `0` keeps idle sources in the gauge |
| `CODEXBAR_XFCE_ACTIVE_HOURS` | `6` | Hours a source stays shown in the gauge after its usage last rose |
| `CODEXBAR_XFCE_SHOW_AT_PCT` | `80` | A source with any window at or above this % used is always shown in the gauge |
| `CODEXBAR_XFCE_ICON_CLICK` / `CODEXBAR_XFCE_TEXT_CLICK` | popup command | Command to run on icon / text click |
| `CODEXBAR_XFCE_COLOR_LOW` / `_MID` / `_HIGH` / `_CRITICAL` | `#98c379` / `#e5c07b` / `#d19a66` / `#e06c75` | Status colors |
| `CODEXBAR_XFCE_COPILOT_TOKEN` | from `copilot.conf` | Overrides the GitHub token |
| `CODEXBAR_XFCE_COPILOT_QUOTA` | `1500` | Fallback quota, used only when Copilot's own quota endpoint is unavailable |
| `CODEXBAR_XFCE_OPENROUTER_MIN_BALANCE` | `2` | USD balance below which `OR` turns orange and is always shown in the gauge. **Setting it explicitly** also enables a once-a-day low-balance alert |
| `CODEXBAR_XFCE_ALERT_THRESHOLDS` | `80,90,100` | Alert thresholds in %; an empty string disables alerts |
| `CODEXBAR_XFCE_ICON_<MODE>` | theme icon names | Per-mode icon. `<MODE>` is one of `REMAINING`, `USED`, `WEEKLY`, `COMBINED`, `CREDITS`, `CLAUDE_5H`, `CLAUDE_7D`, `CLAUDE_OPUS`, `CLAUDE_SONNET`, `CLAUDE_EXTRA`, `COPILOT`, `OPENROUTER`, `BOTH_COMBINED`, `ROTATE` |

## Caching, backoff and alerts

All state lives in `~/.cache/codexbar-xfce-genmon/`:

| File | Contents |
|---|---|
| `usage.json`, `claude_usage.json`, `copilot_usage.json`, `openrouter_usage.json` | Last API responses: fresh for 5 min (`CACHE_TTL`, keeps Claude's oauth/usage clear of 429s), then served as stale for up to 7 days |
| `<provider>_backoff.json` | `{blocked_until, reason, interval}` while a provider is backing off |
| `alerts.json` | Highest alert threshold already sent for each `provider:window:reset` |

- **Backoff:** after an API error the wait starts at 60 s and doubles up to 1 h. On HTTP 429, a longer `Retry-After` is honored (a shorter one, e.g. `0`, doesn't shorten the wait). No request is sent to that provider while it waits. Plain network outages don't start a backoff, and a successful fetch clears it.
- **Alerts** (sent only on panel ticks, never from `popup`): for example `Claude 7-day 90% used · resets 13h 48m`, with urgency `critical` at 100%. A missing or failing `notify-send` is ignored.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `[CX!]` / `Codex Error` | Run `codex login` |
| `[CL!]` / `No Claude credentials found` | Run `claude` once, or sign in with claude-usage-bar |
| `… Data Age  cached 5m · retry 21:43 (429)` | Rate-limited. The script retries by itself at the time shown |
| `GitHub API HTTP 401` / `403` | The token expired or lacks *Plan: Read-only*. Create a new one |
| `OpenRouter … (401)` | Wrong or truncated `OPENROUTER_API_KEY` in `openrouter.conf`. After fixing it, delete `~/.cache/codexbar-xfce-genmon/openrouter_backoff.json` or wait for the retry time shown (up to 1 h) |
| OpenRouter `Balance  unavailable (403)` | The key can't read `/credits`. Spend and limits are still shown |
| Too many notifications, or none | Adjust `CODEXBAR_XFCE_ALERT_THRESHOLDS` (empty = off), or check that `notify-send` is installed |
| Anything else | Run it in a terminal to see the error: `./codexbar-xfce-genmon combined --no-icon` |

To force a refresh, delete the relevant `*_usage.json` and `*_backoff.json` files from `~/.cache/codexbar-xfce-genmon/`.
