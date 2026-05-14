# codexbar-xfce-genmon

XFCE Generic Monitor wrapper for OpenAI Codex, Anthropic Claude, and GitHub Copilot usage.

Shows usage for any combination of sources side-by-side in the XFCE panel — no Waybar or external packages required.

## Files

- `codexbar-xfce-genmon`: Bash entrypoint for XFCE GenMon
- `codexbar_xfce_genmon.py`: Python logic for auth refresh, usage fetch, caching, formatting, tooltips, colors, and click actions

## Requirements

- `python3`
- XFCE panel with `xfce4-genmon-plugin`
- **Codex:** a valid login in `~/.codex/auth.json` (run `codex login`)
- **Claude:** credentials from any of these sources (checked in order):
  1. `~/.claude/.credentials.json` — written automatically by **Claude Code** (`claude` CLI). No extra setup needed if you already use Claude Code.
  2. `~/.config/claude-usage-bar/credentials.json` — written by the macOS [claude-usage-bar](https://github.com/Blimp-Labs/claude-usage-bar) app.
  3. `~/.config/claude-usage-bar/token` — legacy plain-text access token fallback.
- **Copilot:** a `copilot.conf` config file (see [Copilot setup](#copilot-setup) below). The script looks for it first in the repo directory, then at `~/.config/codexbar-xfce-genmon/copilot.conf`. Copilot is silently skipped if neither exists.

## Setup

1. Install the XFCE Generic Monitor plugin if not already present.
2. Add `Generic Monitor` to your XFCE panel.
3. Set the command to one of the examples below.
4. Set the refresh interval to `30` or `60` seconds.

### Optional: install as a CLI command

Symlink the script into your PATH so you can run it from anywhere:

```bash
ln -sf "$(pwd)/codexbar-xfce-genmon" ~/.local/bin/codexbar-xfce-genmon
```

Verify with:

```bash
ls -la ~/.local/bin/codexbar-xfce-genmon
```

Then use `codexbar-xfce-genmon` directly without specifying a path.

## Copilot setup

Copilot tracks **premium request** usage (the monthly quota of 300 for Copilot Pro, higher for Copilot Pro+).

### 1. Create a GitHub personal access token

Go to <https://github.com/settings/personal-access-tokens> and create a **fine-grained** token with:

- **Resource owner:** your personal account
- **Permissions → User permissions → Plan:** Read-only

No repository access is needed.

### 2. Create the config file

Copy the included example and fill in your token:

```bash
cp copilot.conf.example copilot.conf
# edit copilot.conf and replace the placeholder token
```

Or create it manually at `~/.config/codexbar-xfce-genmon/copilot.conf` if you prefer to keep it outside the repo:

```bash
mkdir -p ~/.config/codexbar-xfce-genmon
cat > ~/.config/codexbar-xfce-genmon/copilot.conf <<'EOF'
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
COPILOT_QUOTA=300
EOF
chmod 600 ~/.config/codexbar-xfce-genmon/copilot.conf
```

`COPILOT_QUOTA` is optional — defaults to `300` (Copilot Pro). Set to `1500` for Copilot Pro+.

### 3. Verify

```bash
codexbar-xfce-genmon --model=copilot --popup
```

Expected output:

```
GitHub Copilot
────────────────────────────────────────────────────────
  Monthly  ███░░░░░░░░░░░░░░░░░   16% used  ·  44% elapsed · resets 18d
```
GitHub Copilot
────────────────────────────────────────────
  Monthly  ███░░░░░░░░░░░░░░░░░   84% left  · resets in 18d
```

If you see `[CP!]` in the panel, run `codexbar-xfce-genmon --model=copilot` in a terminal to read the error from stderr.

### Token troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `GitHub API HTTP 401` | Token invalid or expired | Regenerate the token at GitHub |
| `GitHub API HTTP 403` | Token lacks `Plan: read` permission | Recreate the token with the correct permission |
| `Could not determine GitHub username` | Token has no `User: read` permission | Fine-grained tokens include this by default; check token scopes |

## XFCE Panel Command

```bash
# All configured sources, rotating display (default)
/path/to/codexbar-xfce-genmon

# Specific sources
/path/to/codexbar-xfce-genmon --model=codex
/path/to/codexbar-xfce-genmon --model=claude
/path/to/codexbar-xfce-genmon --model=copilot

# Multiple sources (repeatable)
/path/to/codexbar-xfce-genmon --model=codex --model=claude
/path/to/codexbar-xfce-genmon --model=claude --model=copilot

# Hide the icon
/path/to/codexbar-xfce-genmon --no-icon

# Fixed mode instead of rotating
/path/to/codexbar-xfce-genmon combined
/path/to/codexbar-xfce-genmon codex:remaining
/path/to/codexbar-xfce-genmon claude:5h
/path/to/codexbar-xfce-genmon copilot:usage
```

## Modes

### Codex modes

| Mode | Display |
|---|---|
| `codex:remaining` | `[CX] 58% left · 2h 14m` |
| `codex:used` | `[CX] 42% used · 2h 14m` |
| `codex:weekly` | `[CX] W 83% left · 4d 6h` |
| `codex:combined` | `[CX] S 58% · W 83%` |
| `codex:credits` | `[CX] L 120-180 · C 40-60` |

### Claude modes

| Mode | Display |
|---|---|
| `claude:5h` | `[CL] 67% left · 1h 05m` |
| `claude:7d` | `[CL] W 91% left · 3d 12h` |
| `claude:opus` | `[CL] Opus 95% · 3d 12h` |
| `claude:sonnet` | `[CL] Sonnet 88% · 3d 12h` |
| `claude:extra` | `[CL] Extra 100% · 0/40cr` |

### Copilot modes

| Mode | Display |
|---|---|
| `copilot:usage` | `[CP] 8% left · 17d` |

### Cross-source modes

| Mode | Display |
|---|---|
| `combined` | `CX 0% · CL 0% · CP 8% · 0h 08m` |
| `rotate` | cycles through all active modes (default) |

Legacy bare modes (`remaining`, `used`, `weekly`, `credits`) are mapped to their `codex:` equivalents for backwards compatibility.

## Model flags

The `--model=` flag controls which sources are fetched and displayed. It is repeatable and can appear anywhere in the argument list.

| Flag | Effect |
|---|---|
| `--model=codex` | Codex only |
| `--model=claude` | Claude only |
| `--model=copilot` | Copilot only (requires `copilot.conf`) |
| `--model=codex --model=claude` | Codex + Claude |
| *(no flag)* | all configured sources (Copilot auto-included if `copilot.conf` exists) |

Legacy source flags are still accepted:

| Legacy flag | Equivalent |
|---|---|
| `--source=codex` | `--model=codex` |
| `--source=claude` | `--model=claude` |
| `--source=both` | `--model=codex --model=claude` |
| `--codex-only` | `--model=codex` |
| `--claude-only` | `--model=claude` |
| `--copilot-only` | `--model=copilot` |

## Popup

Run `--popup` to print a usage summary directly in the terminal and return to the prompt:

```bash
codexbar-xfce-genmon --popup
```

Output example:

```
Codex
────────────────────────────────────────────────────────
  5-hour   ████████████████████  FULL       ·  97% elapsed · resets 0h 08m
  7-day    █████████████░░░░░░░   64% used  ·  47% elapsed · resets 3d 16h
  Credits  local 0  ·  cloud 0

Claude
────────────────────────────────────────────────────────
  5-hour   ████████████████████  FULL       ·  46% elapsed · resets 2h 38m
  7-day    █░░░░░░░░░░░░░░░░░░░    5% used  ·  11% elapsed · resets 6d 4h

GitHub Copilot
────────────────────────────────────────────────────────
  Monthly  ███████████████████░   94% used  ·  44% elapsed · resets 17d
```

Each row shows: a 20-block bar filled to **used** quota, then `used% · elapsed% · resets`. `FULL` appears when a limit is exhausted. `elapsed%` is how far through the reset window you are in time — useful for pacing (high used + low elapsed = burning fast).

Model flags work with `--popup` too:

```bash
codexbar-xfce-genmon --popup --model=copilot
codexbar-xfce-genmon --popup --model=claude --model=copilot
```

## Click behavior

- Clicking the icon or text in the panel triggers `--popup` in a terminal.
- Hovering shows a tooltip with a progress bar per window, in the same `used · elapsed · resets` format as the popup.

## Environment variables

### Source

- `CODEXBAR_XFCE_MODELS`: comma-separated list of sources to enable (`codex`, `claude`, `copilot`). Empty = all configured.
- `CODEXBAR_XFCE_SOURCE`: legacy alias (`both` | `codex` | `claude`)

### Copilot

- `CODEXBAR_XFCE_COPILOT_TOKEN`: GitHub token (overrides `copilot.conf`)
- `CODEXBAR_XFCE_COPILOT_QUOTA`: monthly quota integer (overrides `copilot.conf`, default `300`)

### Rotation

- `CODEXBAR_XFCE_ROTATE_SECONDS`: seconds per mode slot (default `15`)
- `CODEXBAR_XFCE_ROTATE_MODES`: comma-separated list of modes

```bash
CODEXBAR_XFCE_ROTATE_SECONDS=10 \
CODEXBAR_XFCE_ROTATE_MODES='codex:remaining,claude:5h,copilot:usage,combined' \
  ./codexbar-xfce-genmon rotate
```

### Display

- `CODEXBAR_XFCE_SHOW_ICON`: set to `0` to hide the icon
- `--no-icon`: command-line shorthand

### Click actions

- `CODEXBAR_XFCE_ICON_CLICK`: custom command on icon click
- `CODEXBAR_XFCE_TEXT_CLICK`: custom command on text click

### Colors

- `CODEXBAR_XFCE_COLOR_LOW` (default `#98c379`)
- `CODEXBAR_XFCE_COLOR_MID` (default `#e5c07b`)
- `CODEXBAR_XFCE_COLOR_HIGH` (default `#d19a66`)
- `CODEXBAR_XFCE_COLOR_CRITICAL` (default `#e06c75`)

### Icons

**Codex:**
- `CODEXBAR_XFCE_ICON_REMAINING`
- `CODEXBAR_XFCE_ICON_USED`
- `CODEXBAR_XFCE_ICON_WEEKLY`
- `CODEXBAR_XFCE_ICON_COMBINED`
- `CODEXBAR_XFCE_ICON_CREDITS`

**Claude:**
- `CODEXBAR_XFCE_ICON_CLAUDE_5H`
- `CODEXBAR_XFCE_ICON_CLAUDE_7D`
- `CODEXBAR_XFCE_ICON_CLAUDE_OPUS`
- `CODEXBAR_XFCE_ICON_CLAUDE_SONNET`
- `CODEXBAR_XFCE_ICON_CLAUDE_EXTRA`

**Copilot:**
- `CODEXBAR_XFCE_ICON_COPILOT`

**Cross-source:**
- `CODEXBAR_XFCE_ICON_BOTH_COMBINED`
- `CODEXBAR_XFCE_ICON_ROTATE`

## Notes

- Codex credentials are read from `~/.codex/auth.json`; token is refreshed automatically when near expiry.
- Claude credentials are read from `~/.claude/.credentials.json` (Claude Code) or the fallback paths above; token is refreshed automatically when a refresh token is available.
- Copilot config is read from `copilot.conf` in the repo directory first, then `~/.config/codexbar-xfce-genmon/copilot.conf`; username is cached for 1 hour in `~/.cache/codexbar-xfce-genmon/copilot_user.json`.
- Usage data is cached in `~/.cache/codexbar-xfce-genmon/` (`usage.json` for Codex, `claude_usage.json` for Claude, `copilot_usage.json` for Copilot).
- When serving from a stale cache (network/auth error), a `~` is appended to the panel text and a "Data Age" line appears in the tooltip.
- The color threshold is driven by the worst-case usage percentage across all active sources.
- `[CX!]` / `[CL!]` / `[CP!]` in the panel text indicates a fetch error for that source. Run the script manually in a terminal to see the error on stderr.

## License

This wrapper is a local utility project.
