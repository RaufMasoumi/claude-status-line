"""Build the preview page: render every theme to HTML and embed the script.

Usage (from the repository root):
    python tools/build_preview.py                 # writes docs/index.html (standalone page)
    python tools/build_preview.py --fragment out.html
        # writes the page body only, for hosts that add their own <html>/<head>

The previews are the script's real output, with its ANSI colours converted to
styled spans, so the page always matches the version of statusline.py it was
built from. Rebuild after changing the script or the template.
"""

import argparse
import html
import importlib.util
import re
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "statusline.py"
TEMPLATE = ROOT / "tools" / "preview_template.html"
DEFAULT_OUT = ROOT / "docs" / "index.html"

ANSI = re.compile(r"\x1b\[([0-9;]*)m")
NARROW_LIMIT = 80
RULER_MAX = 110
FONT_THEMES = {"powerline"}
HERO_THEME = "neon-mini"

# The template is written as a page body; a standalone page needs the
# document shell around it (charset, viewport, and a body with no margin).
STANDALONE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
  :root { padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); }
  body { margin: 0; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
"""


def load_statusline():
    """Import statusline.py from the repository root."""
    spec = importlib.util.spec_from_file_location("statusline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ansi_to_html(text):
    """Convert the subset of ANSI codes the script emits into styled spans."""
    out = []
    state = {"fg": None, "bg": None, "bold": False, "dim": False}

    def style():
        parts = []
        if state["fg"]:
            parts.append(f"color:rgb({state['fg']})")
        if state["bg"]:
            parts.append(f"background:rgb({state['bg']})")
        if state["bold"]:
            parts.append("font-weight:700")
        if state["dim"]:
            parts.append("opacity:.55")
        return ";".join(parts)

    def emit(chunk):
        buf = []

        def flush():
            if buf:
                s = style()
                t = html.escape("".join(buf))
                out.append(f'<span style="{s}">{t}</span>' if s else t)
                buf.clear()

        for ch in chunk:
            # Powerline arrows are drawn in CSS so they show without a powerline font.
            if ch in ("", ""):
                flush()
                cls = "pl" if ch == "" else "pl thin"
                bgc = f"background:rgb({state['bg']})" if state["bg"] else ""
                fgc = f"rgb({state['fg']})" if state["fg"] else "currentColor"
                out.append(f'<span class="{cls}" style="{bgc}"><i style="background:{fgc}"></i></span>')
            else:
                buf.append(ch)
        flush()

    pos = 0
    for m in ANSI.finditer(text):
        emit(text[pos : m.start()])
        pos = m.end()
        codes = m.group(1).split(";") if m.group(1) else ["0"]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == "0":
                state.update(fg=None, bg=None, bold=False, dim=False)
            elif c == "1":
                state["bold"] = True
            elif c == "2":
                state["dim"] = True
            elif c in ("38", "48") and i + 4 < len(codes) and codes[i + 1] == "2":
                state["fg" if c == "38" else "bg"] = ",".join(codes[i + 2 : i + 5])
                i += 4
            i += 1
    emit(text[pos:])
    return "".join(out)


def visible_width(text):
    """Count terminal columns, with wide characters (emoji) as two."""
    plain = ANSI.sub("", text).replace("️", "")
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain)


def sample(sl):
    """Return the example values every preview is rendered with."""
    now = time.time()
    return sl.collect(
        {
            "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
            "effort": {"level": "high"},
            "context_window": {"context_window_size": 200000, "used_percentage": 42},
            "rate_limits": {
                "five_hour": {"used_percentage": 63, "resets_at": now + 7800},
                "seven_day": {"used_percentage": 88, "resets_at": now + 300000},
            },
        }
    )


def theme_rows(sl, s):
    """Render one gallery row per theme, at the theme's natural size."""
    rows = []
    for name in sl.THEMES:
        line = sl.render_theme(name, s)
        width = visible_width(line)
        chips = []
        if width <= NARROW_LIMIT:
            chips.append('<span class="chip narrow">fits a narrow pane</span>')
        if name in FONT_THEMES:
            chips.append('<span class="chip font">needs powerline font</span>')
        fill = min(100, width / RULER_MAX * 100)
        mark = NARROW_LIMIT / RULER_MAX * 100
        cmd = html.escape(f"python statusline.py --set {name}")
        rows.append(
            f"""<article class="theme">
  <div class="theme-head">
    <span class="theme-name">{name}</span>
    <span class="cols">{width} cols</span>
    {''.join(chips)}
    <button class="copy" type="button" data-text="{cmd}" aria-label="Copy the command that selects {name}">Copy --set {name}</button>
  </div>
  <div class="strip">{ansi_to_html(line)}</div>
  <div class="ruler" aria-hidden="true"><span class="fill" style="width:{fill:.1f}%"></span><span class="mark" style="left:{mark:.1f}%"></span></div>
</article>"""
        )
    return rows


def build(fragment):
    """Return the page HTML, as a body fragment or a standalone document."""
    sl = load_statusline()
    s = sample(sl)
    source = SCRIPT.read_text(encoding="utf-8")
    body = (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("{{HERO}}", ansi_to_html(sl.render_theme(HERO_THEME, s)))
        .replace("{{THEMES}}", "\n".join(theme_rows(sl, s)))
        .replace("{{LINES}}", str(source.count("\n")))
        .replace("{{SCRIPT}}", html.escape(source))
    )
    if fragment:
        return body
    # The template opens with <title>, <meta>, <link> and <style>; they belong
    # in <head>, so the body starts at <main>.
    head, sep, rest = body.partition("<main>")
    return f"{STANDALONE_HEAD}{head}</head>\n<body>\n{sep}{rest}\n</body>\n</html>\n"


def main():
    """Parse arguments and write the page."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fragment", metavar="PATH", help="write the body-only page to PATH")
    args = parser.parse_args()
    out = Path(args.fragment) if args.fragment else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    page = build(fragment=bool(args.fragment))
    out.write_text(page, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(page)} bytes)")


if __name__ == "__main__":
    main()
