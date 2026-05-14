# codexbar-xfce-genmon

XFCE Generic Monitor wrapper for OpenAI Codex and Anthropic Claude usage.

Shows usage for Codex, Claude, or both side-by-side in the XFCE panel — no Waybar or external packages required.

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

## XFCE Panel Command

```bash
# Both sources, rotating display (default)
/path/to/codexbar-xfce-genmon

# Codex only
/path/to/codexbar-xfce-genmon --codex-only

# Claude only
/path/to/codexbar-xfce-genmon --claude-only

# Explicit source flag
/path/to/codexbar-xfce-genmon --source=both
/path/to/codexbar-xfce-genmon --source=codex
/path/to/codexbar-xfce-genmon --source=claude

# Hide the icon
/path/to/codexbar-xfce-genmon --no-icon

# Fixed mode instead of rotating
/path/to/codexbar-xfce-genmon combined
/path/to/codexbar-xfce-genmon codex:remaining
/path/to/codexbar-xfce-genmon claude:5h
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

### Cross-source modes

| Mode | Display |
|---|---|
| `combined` | `CX 58% · CL 67% · 2h 14m` |
| `rotate` | cycles through all active modes (default) |

Legacy bare modes (`remaining`, `used`, `weekly`, `credits`) are mapped to their `codex:` equivalents for backwards compatibility.

## Source flags

| Flag | Effect |
|---|---|
| `--source=both` | show both Codex and Claude (default) |
| `--source=codex` | Codex only |
| `--source=claude` | Claude only |
| `--codex-only` | shorthand for `--source=codex` |
| `--claude-only` | shorthand for `--source=claude` |

Flags can appear in any position relative to the mode argument.

## Popup

Run `--popup` to print a usage summary directly in the terminal and return to the prompt:

```bash
codexbar-xfce-genmon --popup
```

Output example:

```
Codex
────────────────────────────────────────────
  Session  ████████████████████  FULL        · resets in 1h 12m
  Weekly   █████████████░░░░░░░   36% left  · resets in 3d 17h
  Credits  local 0  ·  cloud 0

Claude
────────────────────────────────────────────
  5-hour   ████████████████████  FULL        · resets in 3h 44m
  7-day    █░░░░░░░░░░░░░░░░░░░   95% left  · resets in 6d 5h
```

The bars are filled to represent **used** quota. `FULL` appears in place of a percentage when a limit is exhausted. Clicking the panel text also triggers this output via `notify-send` is no longer used — the popup is plain terminal output.

Source flags work with `--popup` too:

```bash
codexbar-xfce-genmon --popup --claude-only
codexbar-xfce-genmon --popup --codex-only
```

## Click behavior

- Clicking the icon or text in the panel triggers `--popup` in a terminal.
- Hovering shows the full tooltip with Pango-formatted detail.

## Environment variables

### Source

- `CODEXBAR_XFCE_SOURCE`: `both` | `codex` | `claude`

### Rotation

- `CODEXBAR_XFCE_ROTATE_SECONDS`: seconds per mode slot (default `15`)
- `CODEXBAR_XFCE_ROTATE_MODES`: comma-separated list of modes

```bash
CODEXBAR_XFCE_ROTATE_SECONDS=10 \
CODEXBAR_XFCE_ROTATE_MODES='codex:remaining,claude:5h,combined' \
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

**Cross-source:**
- `CODEXBAR_XFCE_ICON_BOTH_COMBINED`
- `CODEXBAR_XFCE_ICON_ROTATE`

## Notes

- Codex credentials are read from `~/.codex/auth.json`; token is refreshed automatically when near expiry.
- Claude credentials are read from `~/.claude/.credentials.json` (Claude Code) or the fallback paths above; token is refreshed automatically when a refresh token is available.
- Usage data is cached in `~/.cache/codexbar-xfce-genmon/` (`usage.json` for Codex, `claude_usage.json` for Claude).
- When serving from a stale cache (network/auth error), a `~` is appended to the panel text and a "Data Age" line appears in the tooltip.
- The color threshold is driven by the worst-case usage across all active sources.

## License

This wrapper is a local utility project.

XFCE Generic Monitor wrapper for OpenAI Codex and Anthropic Claude usage.

Shows usage for Codex, Claude, or both side-by-side in the XFCE panel — no Waybar or external packages required.

## Files

- `codexbar-xfce-genmon`: Bash entrypoint for XFCE GenMon
- `codexbar_xfce_genmon.py`: Python logic for auth refresh, usage fetch, caching, formatting, tooltips, colors, and click actions

## Requirements

- `python3`
- `notify-send`
- XFCE panel with `xfce4-genmon-plugin`
- **Codex:** a valid login in `~/.codex/auth.json` (run `codex login`)
- **Claude:** credentials from any of these sources (checked in order):
  1. `~/.claude/.credentials.json` — written automatically by **Claude Code** (`claude` CLI). No extra setup needed if you already use Claude Code.
  2. `~/.config/claude-usage-bar/credentials.json` — written by the macOS [claude-usage-bar](https://github.com/Blimp-Labs/claude-usage-bar) app.
  3. `~/.config/claude-usage-bar/token` — legacy plain-text access token fallback.

## Setup

1. Install the XFCE Generic Monitor plugin if not already present.
2. Add `Generic Monitor` to your XFCE panel.
3. Set the command to one of the examples below.
4. Set the refresh interval to `30` or `60` seconds.

## XFCE Panel Command

```bash
# Both sources, rotating display (default)
/path/to/codexbar-xfce-genmon

# Codex only
/path/to/codexbar-xfce-genmon --codex-only

# Claude only
/path/to/codexbar-xfce-genmon --claude-only

# Explicit source flag
/path/to/codexbar-xfce-genmon --source=both
/path/to/codexbar-xfce-genmon --source=codex
/path/to/codexbar-xfce-genmon --source=claude

# Hide the icon
/path/to/codexbar-xfce-genmon --no-icon

# Fixed mode instead of rotating
/path/to/codexbar-xfce-genmon combined
/path/to/codexbar-xfce-genmon codex:remaining
/path/to/codexbar-xfce-genmon claude:5h
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

### Cross-source modes

| Mode | Display |
|---|---|
| `combined` | `CX 58% · CL 67%` |
| `rotate` | cycles through all active modes (default) |

Legacy bare modes (`remaining`, `used`, `weekly`, `credits`) are mapped to their `codex:` equivalents for backwards compatibility.

## Source flags

| Flag | Effect |
|---|---|
| `--source=both` | show both Codex and Claude (default) |
| `--source=codex` | Codex only |
| `--source=claude` | Claude only |
| `--codex-only` | shorthand for `--source=codex` |
| `--claude-only` | shorthand for `--source=claude` |

Flags can appear in any position relative to the mode argument.

## Click behavior

- Clicking the icon or text shows the full tooltip as a `notify-send` popup.
- Hovering shows the full tooltip in the panel.
- The popup includes both sources when both are active.

```bash
# Trigger popup manually
./codexbar-xfce-genmon popup combined
./codexbar-xfce-genmon popup claude:5h --claude-only
```

## Environment variables

### Source

- `CODEXBAR_XFCE_SOURCE`: `both` | `codex` | `claude`

### Rotation

- `CODEXBAR_XFCE_ROTATE_SECONDS`: seconds per mode slot (default `15`)
- `CODEXBAR_XFCE_ROTATE_MODES`: comma-separated list of modes

```bash
CODEXBAR_XFCE_ROTATE_SECONDS=10 \
CODEXBAR_XFCE_ROTATE_MODES='codex:remaining,claude:5h,combined' \
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

**Cross-source:**
- `CODEXBAR_XFCE_ICON_BOTH_COMBINED`
- `CODEXBAR_XFCE_ICON_ROTATE`

## Notes

- Codex credentials are read from `~/.codex/auth.json`; token is refreshed automatically when near expiry.
- Claude credentials are read from `~/.config/claude-usage-bar/credentials.json` (or the legacy `token` file); token is refreshed automatically when a refresh token is available.
- Usage data is cached in `~/.cache/codexbar-xfce-genmon/` (`usage.json` for Codex, `claude_usage.json` for Claude).
- When serving from a stale cache (network/auth error), a `~` is appended to the panel text and a "Data Age" line appears in the tooltip.
- The color threshold is driven by the worst-case usage across all active sources.

## License

This wrapper is a local utility project.
