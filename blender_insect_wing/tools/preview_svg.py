"""Render venation patterns to SVG without Blender (for quick checks).

usage: python3 preview_svg.py [NAME ...]   -> writes wing_<name>.svg
NAME is a key of venation.PRESETS (Odonata model), diptera.PRESETS (atlas
fly model) or DEV_<family> for the developmental fly model (needs numpy).
Pass --labels to annotate Diptera veins.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "insect_wing_generator"))
import venation  # noqa: E402
import diptera  # noqa: E402
try:
    import fly_dev  # noqa: E402  (needs numpy)
except ImportError:
    fly_dev = None

WIDTH = {"costa": 3.0, "subcosta": 2.2, "primary": 2.0, "intercalary": 1.3,
         "margin": 1.6, "cross": 0.8,
         "sc": 2.0, "r1": 2.8, "radial": 2.0, "main": 1.7, "weak": 0.8,
         "weak2": 1.2, "rim": 0.6}


def to_svg(res, path, scale=900, labels=False):
    pts = list(res.outline) + [q for lobe in getattr(res, "lobes", []) for q in lobe]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y1 = min(xs), max(ys)
    w = (max(xs) - x0) * scale + 20
    h = (y1 - min(ys)) * scale + 20

    def tr(p):
        return "%.2f,%.2f" % ((p[0] - x0) * scale + 10, (y1 - p[1]) * scale + 10)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (w, h),
           '<rect width="100%" height="100%" fill="white"/>']
    for lobe in getattr(res, "lobes", []):
        out.append('<polygon points="%s" fill="#f1ece0" stroke="#b49a6a" stroke-width="0.8"/>'
                   % " ".join(tr(p) for p in lobe))
    out.append('<polygon points="%s" fill="#eef3f4" stroke="none"/>'
               % " ".join(tr(p) for p in res.outline))
    if getattr(res, "tint", None):
        # pigment preview: dots coloured by the tint field
        step = (max(xs) - x0) / 120
        y = min(ys)
        while y < y1:
            x = x0
            while x < max(xs):
                if venation.point_in_polygon((x, y), res.outline):
                    t = res.tint(x, y)
                    if t > 0.05:
                        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                                   'fill="#c8902c" fill-opacity="%.2f"/>'
                                   % ((x - x0) * scale + 10, (y1 - y) * scale + 10,
                                      step * scale + 0.6, step * scale + 0.6, 0.35 * t))
                x += step
            y += step
    if res.pterostigma:
        out.append('<polygon points="%s" fill="#3a2a1a"/>' % " ".join(tr(p) for p in res.pterostigma))
    for v in res.veins:
        col = "#8a5a1c" if v.kind in ("sc", "r1", "costa", "radial") else "#2b2118"
        dash = ' stroke-dasharray="4,3"' if v.kind == "weak" else ""
        radii = getattr(v, "radii", None)
        if radii:
            # variable calibre: draw segment by segment
            for k in range(len(v.pts) - 1):
                out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
                           'stroke-width="%.2f" stroke-linecap="round"/>'
                           % tuple(tr(v.pts[k]).split(",") + tr(v.pts[k + 1]).split(",")
                                   + [col, 1.3 * radii[k]]))
        else:
            out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                       'stroke-linecap="round" stroke-linejoin="round"%s/>'
                       % (" ".join(tr(p) for p in v.pts), col, WIDTH.get(v.kind, 1), dash))
        name = getattr(v, "name", None)
        if labels and name and v.kind not in ("cross",):
            q = v.pts[len(v.pts) * 2 // 3]
            out.append('<text x="%s" y="%s" font-size="13" font-family="sans-serif" '
                       'fill="#c0392b">%s</text>' % (tr(q).split(",")[0], tr(q).split(",")[1], name))
        elif labels and name:
            q = v.pts[len(v.pts) // 2]
            out.append('<text x="%s" y="%s" font-size="11" font-family="sans-serif" '
                       'fill="#2c6fbb">%s</text>' % (tr(q).split(",")[0], tr(q).split(",")[1], name))
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    labels = "--labels" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or \
        list(venation.PRESETS) + list(diptera.PRESETS)
    for name in names:
        t = time.time()
        if name.startswith("DEV_"):
            fam = name[4:]
            res = fly_dev.generate(fly_dev.DevParams(family=fam, **fly_dev.PRESETS[fam]))
        elif name in diptera.PRESETS:
            res = diptera.generate(diptera.FlyParams(family=name, **diptera.PRESETS[name]))
        else:
            res = venation.generate(venation.WingParams(**venation.PRESETS[name]))
        print("%-16s %4d veins, %.2fs" % (name, len(res.veins), time.time() - t))
        to_svg(res, "wing_%s.svg" % name.lower(), labels=labels)
