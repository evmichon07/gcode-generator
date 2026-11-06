"""Turn simple part dimensions into GRBL G-code for a hobby CNC router (Bob's CNC, Shapeoko, etc.).

Run:  python gcode_gen.py   -> writes part.nc, preview.svg and showcase.html next to this file.
Origin: front-left corner of the stock, Z0 = top of stock. Units: mm.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    width: float = 120.0        # part size X
    height: float = 80.0        # part size Y
    thickness: float = 12.0     # stock thickness
    tool_d: float = 6.35        # 1/4" flat end mill
    step_down: float = 3.0      # max depth per pass
    stepover: float = 0.4       # pocket stepover, fraction of tool diameter
    pocket: tuple = (30.0, 25.0, 60.0, 30.0, 6.0)  # x, y, w, h, depth
    hole_d: float = 8.0         # corner holes, cut by circular interpolation
    hole_inset: float = 12.0
    skin: float = 0.4           # onion skin left on the outline so the part can't break free
    feed: float = 1200.0        # mm/min
    plunge: float = 300.0
    safe_z: float = 5.0


def passes(depth, step):
    """Equal passes ending exactly at depth: passes(5, 3) -> [-2.5, -5.0]."""
    n = int(-(-depth // step))  # ceil
    return [-round(depth * i / n, 3) for i in range(1, n + 1)]


def generate(job):
    r = job.tool_d / 2
    code = [f"(part {job.width:g} x {job.height:g} x {job.thickness:g} mm, {job.tool_d:g} mm end mill)",
            "G21 G90 G17", "M3 S18000 (router on - manual routers ignore this)", f"G0 Z{job.safe_z:g}"]
    segs, pos = [], [0.0, 0.0]          # preview segments: (kind, x0, y0, x1, y1) or ("hole", cx, cy, rad)

    def rapid(x, y):
        code.extend([f"G0 Z{job.safe_z:g}", f"G0 X{x:.3f} Y{y:.3f}"])
        segs.append(("rapid", *pos, x, y)); pos[:] = [x, y]

    def down(z):
        code.append(f"G1 Z{z:.3f} F{job.plunge:g}")

    def line(x, y, kind):
        code.append(f"G1 X{x:.3f} Y{y:.3f} F{job.feed:g}")
        segs.append((kind, *pos, x, y)); pos[:] = [x, y]

    # 1. Corner holes: full circles (G2) stepping down, cut through into the spoilboard.
    hr = (job.hole_d - job.tool_d) / 2
    assert hr > 0, "hole must be bigger than the tool"
    i = job.hole_inset
    for cx, cy in [(i, i), (job.width - i, i), (job.width - i, job.height - i), (i, job.height - i)]:
        code.append(f"(hole at X{cx:g} Y{cy:g})")
        rapid(cx + hr, cy)
        for z in passes(job.thickness + 0.3, job.step_down):
            down(z)
            code.append(f"G2 X{cx + hr:.3f} Y{cy:.3f} I{-hr:.3f} J0 F{job.feed:g}")
        segs.append(("hole", cx, cy, job.hole_d / 2))

    # 2. Pocket: rings from the center out, each ring one stepover wider.
    px, py, pw, ph, pdepth = job.pocket
    step, rings, k = job.tool_d * job.stepover, [], 0
    while (x1 := px + pw - r - k * step) >= (x0 := px + r + k * step) and \
          (y1 := py + ph - r - k * step) >= (y0 := py + r + k * step):
        rings.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
        k += 1
    assert rings, "pocket is narrower than the tool"
    code.append("(pocket)")
    rapid(*rings[-1][0])
    for z in passes(pdepth, job.step_down):
        down(z)
        for ring in reversed(rings):
            for x, y in ring:
                line(x, y, "pocket")
        rapid(*rings[-1][0])

    # 3. Outline: clockwise outside the part (conventional cut), stopping at the onion skin.
    code.append("(outline)")
    pts = [(-r, -r), (-r, job.height + r), (job.width + r, job.height + r), (job.width + r, -r), (-r, -r)]
    rapid(*pts[0])
    for z in passes(job.thickness - job.skin, job.step_down):
        down(z)
        for x, y in pts[1:]:
            line(x, y, "outline")

    code += [f"G0 Z{job.safe_z:g}", "M5", "G0 X0 Y0", "M30"]
    return code, segs


def preview_svg(job, segs, scale=4.0, pad=12):
    w, h = (job.width + 2 * pad) * scale, (job.height + 2 * pad) * scale
    X = lambda x: (x + pad) * scale
    Y = lambda y: h - (y + pad) * scale             # SVG y points down
    color = {"rapid": "#6e7681", "pocket": "#d29922", "outline": "#58a6ff", "hole": "#3fb950"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}">',
           f'<rect x="{X(0)}" y="{Y(job.height)}" width="{job.width * scale}" height="{job.height * scale}" '
           f'fill="#1c2a35" stroke="#2f3d48"/>']
    for s in segs:
        if s[0] == "hole":
            out.append(f'<circle cx="{X(s[1])}" cy="{Y(s[2])}" r="{s[3] * scale}" fill="none" '
                       f'stroke="{color["hole"]}" stroke-width="2"/>')
        else:
            dash = ' stroke-dasharray="4 4"' if s[0] == "rapid" else ""
            out.append(f'<line x1="{X(s[1])}" y1="{Y(s[2])}" x2="{X(s[3])}" y2="{Y(s[4])}" '
                       f'stroke="{color[s[0]]}" stroke-width="{1 if s[0] == "rapid" else 2}"{dash}/>')
    return "\n".join(out + ["</svg>"])


def showcase(job, code, svg, minutes):
    params = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
        ("Part", f"{job.width:g} x {job.height:g} x {job.thickness:g} mm"), ("Tool", f'{job.tool_d:g} mm (1/4") end mill'),
        ("Pocket", f"{job.pocket[2]:g} x {job.pocket[3]:g} mm, {job.pocket[4]:g} mm deep"),
        ("Holes", f"4 x {job.hole_d:g} mm, circular interpolation"), ("Passes", f"{job.step_down:g} mm max step-down"),
        ("Output", f"{len(code)} lines of GRBL G-code"), ("Est. cut time", f"{minutes:.1f} min at {job.feed:g} mm/min")])
    head = "\n".join(code[:14]) + "\n..."
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>G-code generator</title><style>
body{{margin:0;background:#0f1418;color:#e6edf3;font:14px/1.45 "Segoe UI",system-ui,sans-serif;padding:24px 28px}}
h1{{margin:0;font-size:22px;font-weight:600}} .sub{{color:#8b98a5;margin:2px 0 16px}}
.grid{{display:grid;grid-template-columns:auto 1fr;gap:16px}} .panel{{background:#171e24;border:1px solid #26313a;border-radius:10px;padding:14px 16px}}
td{{padding:3px 12px 3px 0}} td:first-child{{color:#8b98a5}} pre{{margin:10px 0 0;font:12px/1.4 Consolas,monospace;color:#c9d1d9}}
.key span{{margin-right:14px}} .key i{{display:inline-block;width:12px;height:3px;margin-right:5px;vertical-align:middle}}
</style></head><body><h1>G-code Generator for Hobby CNC Routers</h1>
<div class="sub">Part dimensions in, ready-to-run GRBL G-code out &middot; Python, standard library only &middot; sample part</div>
<div class="grid"><div class="panel">{svg}<div class="key"><span><i style="background:#58a6ff"></i>outline</span>
<span><i style="background:#d29922"></i>pocket</span><span><i style="background:#3fb950"></i>holes</span>
<span><i style="background:#6e7681"></i>rapid moves</span></div></div>
<div class="panel"><table>{params}</table><pre>{head}</pre></div></div></body></html>"""


if __name__ == "__main__":
    job = Job()
    code, segs = generate(job)
    cut_mm = sum(((s[3] - s[1]) ** 2 + (s[4] - s[2]) ** 2) ** 0.5 for s in segs if s[0] in ("pocket", "outline"))
    minutes = cut_mm / job.feed

    # Self-check: never cuts below the spoilboard allowance, never leaves the part + tool envelope.
    words = [w for l in code if not l.startswith("(") for w in l.split("(")[0].split()]
    zs = [float(w[1:]) for w in words if w[0] == "Z"]
    xs = [float(w[1:]) for w in words if w[0] == "X"]
    ys = [float(w[1:]) for w in words if w[0] == "Y"]
    r = job.tool_d / 2
    assert passes(6, 3) == [-3.0, -6.0] and passes(5, 3) == [-2.5, -5.0]
    assert min(zs) >= -(job.thickness + 0.3) and max(zs) == job.safe_z
    assert -r - 1e-6 <= min(xs) and max(xs) <= job.width + r + 1e-6
    assert -r - 1e-6 <= min(ys) and max(ys) <= job.height + r + 1e-6
    assert code[1] == "G21 G90 G17" and code[-1] == "M30"

    here = Path(__file__).parent
    (here / "part.nc").write_text("\n".join(code) + "\n")
    svg = preview_svg(job, segs)
    (here / "preview.svg").write_text(svg)
    (here / "showcase.html").write_text(showcase(job, code, svg, minutes), encoding="utf-8")
    print(f"ok: {len(code)} lines, cut length {cut_mm / 1000:.1f} m, ~{minutes:.1f} min")
