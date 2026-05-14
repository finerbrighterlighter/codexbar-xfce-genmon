# codexbar-xfce-genmon

XFCE Generic Monitor wrapper for OpenAI Codex usage.

This project lets you show Codex usage in the XFCE panel without depending on the `codexbar` package or Waybar.

## Files

- `codexbar-xfce-genmon`: Bash entrypoint for XFCE GenMon
- `codexbar_xfce_genmon.py`: Python logic for auth refresh, usage fetch, caching, formatting, tooltips, colors, and click actions

## Requirements

- `python3`
- `notify-send`
- XFCE panel with `xfce4-genmon-plugin`
- a valid Codex login in `~/.codex/auth.json`

## Setup

1. Install the XFCE Generic Monitor plugin if it is not already available.
2. Add `Generic Monitor` to your XFCE panel.
3. Set the command to one of the examples below.
4. Set the refresh interval to something like `30` or `60` seconds.

## XFCE Panel Command

Use the full path to the wrapper in your local clone when setting the Generic Monitor plugin command.

Examples:

```bash
/path/to/codexbar-xfce-genmon/codexbar-xfce-genmon remaining
```

```bash
/path/to/codexbar-xfce-genmon/codexbar-xfce-genmon rotate
```

Hide the icon:

```bash
/path/to/codexbar-xfce-genmon/codexbar-xfce-genmon remaining --no-icon
```

## Modes

- `remaining`: session remaining percent and session reset time
- `used`: session used percent and session reset time
- `weekly`: weekly remaining percent and weekly reset time
- `combined`: session and weekly remaining percentages
- `credits`: local and cloud credit estimates
- `rotate`: rotates through a configured list of modes

Examples:

```bash
./codexbar-xfce-genmon remaining
./codexbar-xfce-genmon used
./codexbar-xfce-genmon weekly
./codexbar-xfce-genmon combined
./codexbar-xfce-genmon credits
./codexbar-xfce-genmon rotate
```

## Click Behavior

- Clicking the icon shows the full current status using `notify-send`
- Clicking the text shows the same popup
- Hovering shows the full tooltip in the panel

Popup mode can also be called directly:

```bash
./codexbar-xfce-genmon popup remaining
```

## Environment Variables

### Rotation

- `CODEXBAR_XFCE_ROTATE_SECONDS`: rotate interval in seconds
- `CODEXBAR_XFCE_ROTATE_MODES`: comma-separated list of modes

Example:

```bash
CODEXBAR_XFCE_ROTATE_SECONDS=5 CODEXBAR_XFCE_ROTATE_MODES='remaining,weekly,combined,credits' ./codexbar-xfce-genmon rotate
```

### Display

- `CODEXBAR_XFCE_SHOW_ICON`: set to `0` to hide the icon
- `--no-icon`: command-line shortcut to hide the icon

Example:

```bash
CODEXBAR_XFCE_SHOW_ICON=0 ./codexbar-xfce-genmon combined
```

### Click Actions

- `CODEXBAR_XFCE_ICON_CLICK`: custom command for icon click
- `CODEXBAR_XFCE_TEXT_CLICK`: custom command for text click

By default both run the wrapper in `popup` mode.

### Colors

- `CODEXBAR_XFCE_COLOR_LOW`
- `CODEXBAR_XFCE_COLOR_MID`
- `CODEXBAR_XFCE_COLOR_HIGH`
- `CODEXBAR_XFCE_COLOR_CRITICAL`

### Icons

- `CODEXBAR_XFCE_ICON_REMAINING`
- `CODEXBAR_XFCE_ICON_USED`
- `CODEXBAR_XFCE_ICON_WEEKLY`
- `CODEXBAR_XFCE_ICON_COMBINED`
- `CODEXBAR_XFCE_ICON_CREDITS`
- `CODEXBAR_XFCE_ICON_ROTATE`

## Notes

- This wrapper reads `~/.codex/auth.json`, refreshes the OAuth token if needed, and calls the same usage endpoint that `codexbar` uses
- It caches usage data in `~/.cache/codexbar-xfce-genmon/usage.json`
- This wrapper converts usage to remaining where needed
- Output uses XFCE GenMon XML tags and Pango markup

## License

This wrapper is a local utility project.
