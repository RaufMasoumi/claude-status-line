# Claude Code Status Line

A status line for [Claude Code](https://claude.com/claude-code) that shows the model, effort level, context use, and your 5-hour and weekly usage limits. It's one Python file with no dependencies, comes with nine themes, and fits itself to the terminal width.

```
⟡ Opus 5.5   ▁▂▃▅▇ high   ctx ──────── 42%   5h ──────── 63% 2h10m   wk ──────── 88% 3d11h
```

**Preview every theme in color:** [raufmasoumi.github.io/claude-status-line](https://raufmasoumi.github.io/claude-status-line/) (the page is [`docs/index.html`](docs/index.html)).

## Install

Requires Python 3.8 or later.

**1. Save the script** to your Claude folder.

macOS / Linux:

```sh
curl -o ~/.claude/statusline.py https://raw.githubusercontent.com/RaufMasoumi/claude-status-line/main/statusline.py
```

Windows (PowerShell):

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/RaufMasoumi/claude-status-line/main/statusline.py -OutFile "$HOME\.claude\statusline.py"
```

**2. Add it to `~/.claude/settings.json`**, keeping the settings already there:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/statusline.py",
  "padding": 0,
  "refreshInterval": 3
}
```

On Windows, use `"command": "python C:/Users/<you>/.claude/statusline.py"`.

**3. Pick a theme.** Run these from your Claude folder:

```sh
python statusline.py --preview              # all themes, in color, at your terminal width
python statusline.py --preview neon --width 60
python statusline.py --set neon-laser
```

## Themes

`classic` · `powerline` · `pills` · `minimal` · `emoji` · `neon` · `neon-mini` · `neon-laser` · `neon-laser-mini`

Usage turns yellow at 50% and red at 80% in every theme. `powerline` needs a font with powerline arrows, such as Cascadia Code or any Nerd Font.

## How it fits the terminal

Claude Code passes the terminal width in `COLUMNS`. The meters grow to fill wide terminals. In narrow ones the line drops detail in this order, so it never wraps:

1. The meters get shorter.
2. Reset times are hidden, except on a limit that's at 80% or more.
3. The effort word is dropped, leaving the bars or a short code like `H`.
4. The meters are removed.
5. The model's version number is removed.

The `-mini` themes stay compact and don't stretch. `refreshInterval` redraws the line every few seconds, so it adjusts soon after a resize.

## Notes

- `5h` and `wk` show `--` until Claude's first reply in a session, and only appear on a Claude.ai plan.
- `STATUSLINE_THEME=<name>` overrides the saved theme, and `STATUSLINE_DEBUG=1` saves the input from Claude Code to `~/.claude/statusline-input.json`.
- The script respects `CLAUDE_CONFIG_DIR` if your Claude folder lives somewhere else.

## Preview page

`docs/index.html` is built from the script's real output by `tools/build_preview.py`, using `tools/preview_template.html`. After changing either the script or the template, rebuild the page:

```sh
python tools/build_preview.py
```
