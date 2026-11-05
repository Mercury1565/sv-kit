"""Generate the animated README banner (docs/assets/svkit-banner-{light,dark}.svg).

Run from the repo root: python docs/assets/make_banner.py
Plain SVG + CSS animation (GitHub renders README images without scripts); motion stops under
prefers-reduced-motion.
"""
from pathlib import Path

THEMES = {
    "dark": dict(fg="#e3e7ed", muted="#9aa5b4", faint="#6b7685", accent="#5b93f5", accent_fg="#0b0d10",
                 wave="#4cc26a", bad="#ff6b6b", panel="#0f1216", border="#262c35", grid="#1b2028"),
    "light": dict(fg="#1b1f24", muted="#5b6573", faint="#8a94a3", accent="#2f6fde", accent_fg="#ffffff",
                  wave="#1a7f37", bad="#c62828", panel="#f6f8fa", border="#d8dde3", grid="#e8ecf0"),
}

W, H = 940, 200
PX0, PX1 = 548, 912          # waveform panel
TX0 = 618                    # trace area starts here (names to the left)
P = 160                      # scroll period (px); traces repeat every P
REPS = 4                     # drawn periods (covers the visible width + one period of scroll)
ROWS = [62, 100, 138]        # trace centre lines
AMP = 9


def clk(y):
    d = [f"M{TX0} {y + AMP}"]
    for _ in range(REPS * 4):            # 4 clock cycles per period
        d.append(f"h20 v{-2 * AMP} h20 v{2 * AMP}")
    return " ".join(d)


def bus(y, vals, colors, c):
    """Hexagon-edged bus segments, each P/len(vals) wide."""
    seg = P // len(vals)
    out = []
    for r in range(REPS):
        for i, (v, col) in enumerate(zip(vals, colors)):
            x = TX0 + r * P + i * seg
            e = 5
            out.append(
                f'<path d="M{x} {y} L{x + e} {y - AMP} H{x + seg - e} L{x + seg} {y} L{x + seg - e} {y + AMP} '
                f'H{x + e} Z" fill="{col}" fill-opacity="{0.16 if col != c["wave"] else 0}" stroke="{col}" stroke-width="1.6"/>'
                f'<text x="{x + 12}" y="{y + 4}" class="v" fill="{col}">{v}</text>'
            )
    return "".join(out)


def svg(c):
    mark = 112
    mx, my = 40, (H - mark) // 2
    s = mark / 20
    names = "".join(
        f'<text x="{PX0 + 14}" y="{y + 4}" class="n">{n}</text>' for n, y in zip(["clk", "data", "q"], ROWS)
    )
    grid = "".join(f'<line x1="{x}" y1="{36}" x2="{x}" y2="{164}" class="grid"/>' for x in range(TX0 + 40, PX1, 80))
    traces = (
        f'<path d="{clk(ROWS[0])}" fill="none" stroke="{c["wave"]}" stroke-width="1.8" stroke-linejoin="round"/>'
        + bus(ROWS[1], ["3a", "c4"], [c["wave"], c["wave"]], c)
        + bus(ROWS[2], ["5f", "xx"], [c["wave"], c["bad"]], c)
    )
    cursor = PX0 + 250
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="t d">
<title id="t">svkit</title>
<desc id="d">svkit: debug and PPA workbench for SystemVerilog. The logo's square wave draws itself while a waveform scrolls under a cursor.</desc>
<style>
  .word {{ font: 700 66px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; letter-spacing: -1px; fill: {c["fg"]}; }}
  .tag {{ font: 400 17px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: {c["muted"]}; }}
  .chips {{ font: 500 12.5px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; fill: {c["faint"]}; }}
  .n {{ font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; fill: {c["muted"]}; }}
  .v {{ font: 11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  .grid {{ stroke: {c["grid"]}; stroke-width: 1; }}
  .draw {{ stroke-dasharray: 34; stroke-dashoffset: 34; animation: draw 6s cubic-bezier(.65,0,.35,1) infinite; }}
  @keyframes draw {{ 0% {{ stroke-dashoffset: 34; }} 22% {{ stroke-dashoffset: 0; }} 82% {{ stroke-dashoffset: 0; }} 100% {{ stroke-dashoffset: -34; }} }}
  .scroll {{ animation: scroll 3.2s linear infinite; }}
  @keyframes scroll {{ from {{ transform: translateX(0); }} to {{ transform: translateX(-{P}px); }} }}
  .blink {{ animation: blink 1.6s steps(2, jump-none) infinite; }}
  @keyframes blink {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .35; }} }}
  @media (prefers-reduced-motion: reduce) {{
    .draw {{ animation: none; stroke-dashoffset: 0; }}
    .scroll, .blink {{ animation: none; }}
  }}
</style>
<defs><clipPath id="trace"><rect x="{TX0}" y="34" width="{PX1 - TX0 - 2}" height="132"/></clipPath></defs>

<g transform="translate({mx} {my}) scale({s})">
  <rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="{c["accent"]}"/>
  <path d="M3.5 13.5h3v-7h4v7h3v-7h3" class="draw" fill="none" stroke="{c["accent_fg"]}" stroke-width="1.8"
        stroke-linejoin="round" stroke-linecap="round"/>
</g>

<text x="{mx + mark + 26}" y="{H / 2 + 6}" class="word">svkit</text>
<text x="{mx + mark + 28}" y="{H / 2 + 36}" class="tag">Debug &amp; PPA workbench for SystemVerilog</text>
<text x="{mx + mark + 28}" y="{H / 2 + 60}" class="chips">waves · X-trace · QoR · T_min · CPI</text>

<rect x="{PX0}" y="24" width="{PX1 - PX0}" height="152" rx="10" fill="{c["panel"]}" stroke="{c["border"]}"/>
<line x1="{TX0 - 6}" y1="30" x2="{TX0 - 6}" y2="170" stroke="{c["border"]}"/>
{names}
<g clip-path="url(#trace)">{grid}<g class="scroll">{traces}</g></g>
<line x1="{cursor}" y1="30" x2="{cursor}" y2="170" stroke="{c["accent"]}" stroke-width="1.4"/>
<path d="M{cursor - 5} 28 h10 l-5 7 z" fill="{c["accent"]}" class="blink"/>
</svg>
"""


if __name__ == "__main__":
    out = Path(__file__).parent
    for name, c in THEMES.items():
        (out / f"svkit-banner-{name}.svg").write_text(svg(c))
        print("wrote", out / f"svkit-banner-{name}.svg")
