"""Render the Claude Code status line: model, effort, context, 5-hour and weekly usage.

Claude Code pipes a JSON blob on stdin and shows the first line printed. The
line fits the terminal: Claude Code passes the width in COLUMNS, meters grow to
fill it, and detail is dropped step by step when the terminal gets narrow.

Usage:
    python statusline.py                          # normal mode (stdin JSON from Claude Code)
    python statusline.py --preview [prefix]       # print themes with sample data
    python statusline.py --preview neon --width 60
    python statusline.py --set <theme>            # choose the theme (~/.claude/statusline-theme)

The theme can also be forced with the STATUSLINE_THEME env var.
Set STATUSLINE_DEBUG=1 to save the last input to ~/.claude/statusline-input.json.
"""

import json
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
THEME_FILE = CLAUDE_DIR / "statusline-theme"
DEFAULT_THEME = "classic"
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
EFFORT_SHORT = {"low": "L", "medium": "M", "high": "H", "xhigh": "XH", "max": "MAX"}
# Columns kept free at the right edge so the line never wraps.
EDGE_MARGIN = 2
ALERT_PCT = 80

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


# --- colour helpers -------------------------------------------------------


def fg(r, g, b):
    """Return a 24-bit foreground colour code."""
    return f"\033[38;2;{r};{g};{b}m"


def bg(r, g, b):
    """Return a 24-bit background colour code."""
    return f"\033[48;2;{r};{g};{b}m"


GREEN = (80, 200, 120)
YELLOW = (240, 190, 60)
RED = (235, 90, 90)
GREY = (110, 118, 129)


def level(pct):
    """Pick green / yellow / red for a usage percentage."""
    if pct is None:
        return GREY
    if pct >= ALERT_PCT:
        return RED
    if pct >= 50:
        return YELLOW
    return GREEN


def lerp(a, b, t):
    """Blend two RGB colours."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def multi_gradient(stops, t):
    """Blend across any number of RGB colour stops for t in 0..1."""
    t = max(0.0, min(1.0, t))
    pos = t * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    return lerp(stops[i], stops[i + 1], pos - i)


def pct_txt(pct):
    """Format a percentage, or a dash when it's unknown."""
    return "--" if pct is None else f"{pct:.0f}%"


def filled(pct, width):
    """Return how many cells of a bar are filled."""
    if pct is None:
        return 0
    return max(0, min(width, round(pct / 100 * width)))


def effort_rank(effort):
    """Return 1..5 for the effort level, 0 if unknown."""
    try:
        return EFFORT_LEVELS.index(effort.lower()) + 1
    except ValueError:
        return 0


def visible_width(text):
    """Count terminal columns, with wide characters (emoji) as two."""
    plain = ANSI.sub("", text).replace("️", "")
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain)


# --- reading the input ----------------------------------------------------


def fmt_reset(resets_at):
    """Turn an epoch timestamp into a short '2h13m' / '3d4h' string."""
    if not resets_at:
        return ""
    try:
        secs = int(float(resets_at) - time.time())
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def fmt_size(size):
    """Format a context window size as 200k / 1M."""
    if not size:
        return ""
    if size >= 1_000_000:
        return f"{size / 1_000_000:g}M"
    return f"{round(size / 1000)}k"


def read_settings_effort(model_id):
    """Fall back to the effort level saved in ~/.claude/settings.json."""
    try:
        settings = json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key, value in settings.get("modelSettings", {}).items():
        if model_id and (key == model_id or model_id.startswith(key)):
            if isinstance(value, dict) and value.get("effortLevel"):
                return value["effortLevel"]
    return settings.get("effortLevel")


def get_effort(data, model_id):
    """Read the effort level from the input, then env, then settings."""
    for candidate in (data.get("effort"), (data.get("model") or {}).get("effort")):
        if isinstance(candidate, dict):
            candidate = candidate.get("level") or candidate.get("name")
        if candidate:
            return str(candidate)
    if data.get("effortLevel"):
        return str(data["effortLevel"])
    env = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    if env:
        return env
    return read_settings_effort(model_id) or "default"


def get_context(data):
    """Return (used percentage, window size) for the context window."""
    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    size = cw.get("context_window_size")
    if pct is None:
        usage = cw.get("current_usage") or {}
        used = sum(
            usage.get(k) or 0
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        if size and used:
            pct = used / size * 100
    return (None if pct is None else float(pct)), size


def get_limit(window):
    """Return (used percentage, reset text) for one rate-limit window."""
    if not isinstance(window, dict) or window.get("used_percentage") is None:
        return None, ""
    return float(window["used_percentage"]), fmt_reset(window.get("resets_at"))


def collect(data):
    """Pull everything the themes need out of the raw input."""
    model = data.get("model") or {}
    limits = data.get("rate_limits") or {}
    ctx, size = get_context(data)
    five, five_reset = get_limit(limits.get("five_hour"))
    week, week_reset = get_limit(limits.get("seven_day"))
    return {
        "model": model.get("display_name") or model.get("id") or "?",
        "effort": get_effort(data, model.get("id")),
        "ctx": ctx,
        "size": fmt_size(size),
        "five": five,
        "five_reset": five_reset,
        "week": week,
        "week_reset": week_reset,
    }


def terminal_width():
    """Return the terminal width in columns, or None when it can't be known.

    Claude Code runs the status line with piped stdio and passes the width in
    COLUMNS; a console probe would report the hidden console's size instead.
    """
    try:
        cols = int(os.environ.get("COLUMNS", ""))
    except ValueError:
        cols = 0
    if cols <= 0:
        cols = shutil.get_terminal_size((0, 0)).columns
    return cols if cols > 0 else None


# --- render options -------------------------------------------------------
#
# Every theme renders from the same options, so one fitter can shrink any of
# them: meter = bar length (0 hides bars), resets = "all" | "alert" | "none",
# effort_word = show "high" (False shows bars or a short code), short = last
# resort (model's first word, short labels, no effort).

RESETS_ORDER = ["none", "alert", "all"]


def show_reset(o, pct, reset):
    """Decide whether a limit's reset time is shown under these options."""
    if not reset or o["resets"] == "none":
        return False
    if o["resets"] == "alert":
        return pct is not None and pct >= ALERT_PCT
    return True


def model_name(s, o):
    """Return the model name, shortened to its first word in short mode."""
    return s["model"].split()[0] if o["short"] else s["model"]


def effort_txt(s, o):
    """Return the effort as a word or a short code."""
    if o["effort_word"]:
        return s["effort"]
    return EFFORT_SHORT.get(s["effort"].lower(), s["effort"][:1].upper())


# --- themes ---------------------------------------------------------------


def theme_classic(s, o):
    """Coloured text with a block bar for context."""
    sep = f" {DIM}│{RESET} "
    c = fg(*level(s["ctx"]))

    def limit(label, pct, reset):
        r = f" {DIM}↻{reset}{RESET}" if show_reset(o, pct, reset) else ""
        return f"{label} {fg(*level(pct))}{pct_txt(pct)}{RESET}{r}"

    size = f"{DIM}/{s['size']}{RESET}" if s["size"] and not o["short"] else ""
    if o["meter"]:
        n = filled(s["ctx"], o["meter"])
        ctx = f"ctx {c}{'█' * n}{'░' * (o['meter'] - n)} {pct_txt(s['ctx'])}{RESET}{size}"
    else:
        ctx = f"ctx {c}{pct_txt(s['ctx'])}{RESET}{size}"
    parts = [f"{fg(86, 182, 194)}{model_name(s, o)}{RESET}"]
    if not o["short"]:
        label = "effort " if o["effort_word"] else ""
        parts.append(f"{fg(198, 120, 221)}{label}{effort_txt(s, o)}{RESET}")
    parts += [
        ctx,
        limit("5h", s["five"], s["five_reset"]),
        limit("wk" if o["short"] or not o["effort_word"] else "week", s["week"], s["week_reset"]),
    ]
    return sep.join(parts)


def theme_powerline(s, o):
    """Solid segments joined by powerline arrows (needs a powerline-capable font)."""
    slate = (45, 52, 66)
    light = (236, 239, 244)
    muted = fg(160, 168, 180)

    def limit(label, pct, reset):
        r = f" {muted}{reset}" if show_reset(o, pct, reset) else ""
        return (slate, level(pct), f"{label} {pct_txt(pct)}{r}")

    segs = [((94, 92, 230), light, f"{BOLD}{model_name(s, o)}")]
    if not o["short"]:
        segs.append(((150, 90, 220), light, f"⚡ {effort_txt(s, o)}"))
    segs += [
        (slate, level(s["ctx"]), f"ctx {pct_txt(s['ctx'])}"),
        limit("5h", s["five"], s["five_reset"]),
        limit("wk", s["week"], s["week_reset"]),
    ]
    out = []
    for i, (b, f, text) in enumerate(segs):
        out.append(f"{bg(*b)}{fg(*f)} {text} {RESET}")
        nxt = segs[i + 1][0] if i + 1 < len(segs) else None
        if nxt is None:
            out.append(f"{fg(*b)}{RESET}")
        elif nxt == b:
            out.append(f"{bg(*b)}{fg(80, 90, 105)}{RESET}")
        else:
            out.append(f"{bg(*nxt)}{fg(*b)}{RESET}")
    return "".join(out)


def theme_pills(s, o):
    """Rounded-looking coloured badges separated by spaces."""
    dark = (24, 26, 33)

    def pill(b, text, f=dark):
        return f"{bg(*b)}{fg(*f)} {text} {RESET}"

    def limit(label, pct, reset):
        r = f" · {reset}" if show_reset(o, pct, reset) else ""
        return pill(level(pct), f"{label} {pct_txt(pct)}{r}")

    rank = effort_rank(s["effort"])
    dots = "●" * rank + "○" * (5 - rank)
    parts = [pill((122, 162, 247), f"{BOLD}{model_name(s, o)}")]
    if not o["short"]:
        text = f"{s['effort']} {dots}" if o["effort_word"] else dots
        parts.append(pill((187, 154, 247), text))
    parts += [
        pill(level(s["ctx"]), f"ctx {pct_txt(s['ctx'])}"),
        limit("5h", s["five"], s["five_reset"]),
        limit("wk", s["week"], s["week_reset"]),
    ]
    return " ".join(parts)


def theme_minimal(s, o):
    """Quiet grey labels; only the numbers carry colour."""
    d = fg(*GREY)
    sep = f"{d}  ·  {RESET}" if not o["short"] else f"{d} · {RESET}"

    def num(pct):
        return f"{fg(*level(pct))}{pct_txt(pct)}{RESET}"

    def limit(label, pct, reset):
        r = f" {d}{reset}{RESET}" if show_reset(o, pct, reset) else ""
        return f"{d}{label}{RESET} {num(pct)}{r}"

    head = f"{BOLD}{model_name(s, o)}{RESET}"
    if not o["short"]:
        head += f" {d}{effort_txt(s, o)}{RESET}"
    return sep.join(
        [
            head,
            f"{d}ctx{RESET} {num(s['ctx'])}",
            limit("5h", s["five"], s["five_reset"]),
            limit("wk", s["week"], s["week_reset"]),
        ]
    )


def theme_emoji(s, o):
    """Friendly emoji labels with a segmented context bar."""
    sep = f" {DIM}│{RESET} "
    c = fg(*level(s["ctx"]))

    def limit(icon, pct, reset):
        r = f" {DIM}({reset}){RESET}" if show_reset(o, pct, reset) else ""
        return f"{icon} {fg(*level(pct))}{pct_txt(pct)}{RESET}{r}"

    if o["meter"]:
        n = filled(s["ctx"], o["meter"])
        bar = f"{c}{'▰' * n}{DIM}{'▱' * (o['meter'] - n)}{RESET} "
    else:
        bar = ""
    parts = [f"🤖 {BOLD}{model_name(s, o)}{RESET}"]
    if not o["short"]:
        parts.append(f"🧠 {effort_txt(s, o)}")
    parts += [
        f"📚 {bar}{c}{pct_txt(s['ctx'])}{RESET}",
        limit("⏳", s["five"], s["five_reset"]),
        limit("📅", s["week"], s["week_reset"]),
    ]
    return sep.join(parts)


def make_neon(*, icon, accent, label, stops, off=(60, 64, 75), fill="━", empty="━",
              effort="▁▂▃▅▇", sep="  ", alert=RED, bars=True):
    """Build a neon-style theme: glowing labels and gradient meters.

    bars=False is the minimal form: no meters and no effort bars, only the
    effort word and the gradient-coloured percentages.
    """

    def render(s, o):
        dim = fg(*off)
        width = o["meter"]

        def pct_color(pct):
            if pct is None:
                return GREY
            return alert if pct >= ALERT_PCT else multi_gradient(stops, pct / 100)

        def meter(pct):
            if not width:
                return ""
            n = filled(pct, width)
            cells = "".join(
                f"{fg(*multi_gradient(stops, i / max(1, width - 1)))}{fill}" for i in range(n)
            )
            return f"{cells}{dim}{empty * (width - n)}{RESET} "

        def gauge(name, pct, reset=""):
            r = f" {dim}{reset}{RESET}" if show_reset(o, pct, reset) else ""
            return (
                f"{fg(*label)}{name}{RESET} {meter(pct)}"
                f"{fg(*pct_color(pct))}{pct_txt(pct)}{RESET}{r}"
            )

        rank = effort_rank(s["effort"])
        effort_bars = "".join(
            f"{fg(*multi_gradient(stops, i / 4))}{ch}" if i < rank else f"{dim}{ch}"
            for i, ch in enumerate(effort)
        )
        word = f" {fg(*label)}{s['effort']}{RESET}" if o["effort_word"] else ""
        joiner = f"{dim}{sep}{RESET}" if sep.strip() else sep
        if o["short"]:
            joiner = " "
        parts = [f"{fg(*accent)}{BOLD}{icon} {model_name(s, o)}{RESET}"]
        if not o["short"] and bars:
            parts.append(f"{effort_bars}{RESET}{word}")
        elif not o["short"]:
            parts.append(f"{fg(*label)}{effort_txt(s, o)}{RESET}")
        parts += [
            gauge("ctx", s["ctx"]),
            gauge("5h", s["five"], s["five_reset"]),
            gauge("wk", s["week"], s["week_reset"]),
        ]
        return joiner.join(parts)

    return render


NEON = dict(icon="◆", accent=(0, 229, 255), label=(255, 64, 160), stops=[GREEN, YELLOW, RED])
LASER = dict(
    icon="⟡",
    accent=(255, 80, 200),
    label=(120, 255, 240),
    stops=[(120, 255, 240), (255, 80, 200), (255, 240, 250)],
    off=(55, 55, 70),
    fill="─",
    empty="─",
)


# name -> (render, base meter, max meter, richest resets mode)
# base = the meter length when the width is unknown; the fitter grows meters
# up to max to fill the line, and shrinks them below base when space runs out.
THEMES = {
    "classic": (theme_classic, 10, 24, "all"),
    "powerline": (theme_powerline, 0, 0, "all"),
    "pills": (theme_pills, 0, 0, "all"),
    "minimal": (theme_minimal, 0, 0, "all"),
    "emoji": (theme_emoji, 10, 20, "all"),
    "neon": (make_neon(**NEON), 8, 20, "all"),
    "neon-mini": (make_neon(**NEON, bars=False), 0, 0, "alert"),
    "neon-laser": (make_neon(**LASER, sep="   "), 12, 24, "all"),
    "neon-laser-mini": (make_neon(**LASER, bars=False), 0, 0, "alert"),
}


# --- fitting --------------------------------------------------------------


def poorer(a, b):
    """Return the less detailed of two resets modes."""
    return a if RESETS_ORDER.index(a) <= RESETS_ORDER.index(b) else b


def candidates(base, top, resets):
    """List render options from most to least detailed."""

    def opts(meter, r, word=True, short=False):
        return {"meter": meter, "resets": poorer(r, resets), "effort_word": word, "short": short}

    small = min(4, base)
    tiny = min(3, base)
    steps = [opts(m, "all") for m in range(top, base - 1, -1)]
    steps += [opts(m, "all") for m in range(base - 1, small - 1, -1)]
    steps += [opts(small, "alert"), opts(tiny, "alert")]
    steps += [opts(tiny, "alert", word=False), opts(0, "alert", word=False)]
    steps += [opts(0, "none", word=False), opts(0, "none", word=False, short=True)]
    seen, out = set(), []
    for o in steps:
        key = tuple(o.values())
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def render_theme(name, s, cols=None):
    """Render a theme to fit cols columns (None = natural size)."""
    render, base, top, resets = THEMES[name]
    if cols is None:
        return render(s, {"meter": base, "resets": resets, "effort_word": True, "short": False})
    target = cols - EDGE_MARGIN
    line = ""
    for o in candidates(base, top, resets):
        line = render(s, o)
        if visible_width(line) <= target:
            return line
    return line


# --- entry point ----------------------------------------------------------


def chosen_theme():
    """Return the theme name from env, then the theme file, then the default."""
    name = os.environ.get("STATUSLINE_THEME")
    if not name:
        try:
            name = THEME_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            name = ""
    return name if name in THEMES else DEFAULT_THEME


def sample_data():
    """Return example input used by --preview."""
    now = time.time()
    return {
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "effort": {"level": "high"},
        "context_window": {"context_window_size": 1000000, "used_percentage": 42},
        "rate_limits": {
            "five_hour": {"used_percentage": 63, "resets_at": now + 7800},
            "seven_day": {"used_percentage": 88, "resets_at": now + 300000},
        },
    }


def preview(prefix, cols):
    """Print every theme (or those starting with prefix) fitted to cols."""
    s = collect(sample_data())
    current = chosen_theme()
    print(f"{DIM}width {cols} columns{RESET}")
    for name in THEMES:
        if not name.startswith(prefix):
            continue
        mark = "  (current)" if name == current else ""
        print(f"\n{DIM}── {name}{mark}{RESET}")
        print(render_theme(name, s, cols))
    print()


def main():
    """Dispatch on the command line, or render the status line from stdin."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = sys.argv[1:]
    if args[:1] == ["--preview"]:
        cols = shutil.get_terminal_size((100, 24)).columns
        if "--width" in args:
            i = args.index("--width")
            try:
                cols = int(args[i + 1])
            except (IndexError, ValueError):
                print("--width needs a number of columns, e.g. --width 60")
                sys.exit(1)
            del args[i : i + 2]
        preview(args[1] if len(args) > 1 else "", cols)
        return
    if args[:1] == ["--set"]:
        if len(args) < 2 or args[1] not in THEMES:
            print(f"Pick one of: {', '.join(THEMES)}")
            sys.exit(1)
        THEME_FILE.write_text(args[1], encoding="utf-8")
        print(f"Status line theme set to {args[1]}")
        return

    if args or sys.stdin is None or sys.stdin.isatty():
        # Run by hand: show usage instead of waiting for input that never comes.
        print(__doc__.strip())
        return

    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if os.environ.get("STATUSLINE_DEBUG"):
        (CLAUDE_DIR / "statusline-input.json").write_text(raw, encoding="utf-8")
    print(render_theme(chosen_theme(), collect(data), terminal_width()))


if __name__ == "__main__":
    main()
