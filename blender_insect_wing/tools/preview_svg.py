"""Render venation patterns to SVG without Blender (for quick checks).

usage: python3 preview_svg.py [PRESET ...]   -> writes wing_<preset>.svg
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "insect_wing_generator"))
import venation  # noqa: E402

WIDTH = {"costa": 3.0, "subcosta": 2.2, "primary": 2.0, "intercalary": 1.3,
         "margin": 1.6, "cross": 0.8}


def to_svg(res, path, scale=900):
    xs = [p[0] for p in res.outline]
    ys = [p[1] for p in res.outline]
    x0, y1 = min(xs), max(ys)
    w = (max(xs) - x0) * scale + 20
    h = (y1 - min(ys)) * scale + 20

    def tr(p):
        return "%.2f,%.2f" % ((p[0] - x0) * scale + 10, (y1 - p[1]) * scale + 10)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (w, h),
           '<rect width="100%" height="100%" fill="white"/>',
           '<polygon points="%s" fill="#e8f1f4" stroke="none"/>' % " ".join(tr(p) for p in res.outline)]
    if res.pterostigma:
        out.append('<polygon points="%s" fill="#3a2a1a"/>' % " ".join(tr(p) for p in res.pterostigma))
    for v in res.veins:
        out.append('<polyline points="%s" fill="none" stroke="#2b2118" stroke-width="%.2f" '
                   'stroke-linecap="round" stroke-linejoin="round"/>'
                   % (" ".join(tr(p) for p in v.pts), WIDTH.get(v.kind, 1)))
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    names = sys.argv[1:] or list(venation.PRESETS)
    for name in names:
        t = time.time()
        res = venation.generate(venation.WingParams(**venation.PRESETS[name]))
        n_cross = sum(1 for v in res.veins if v.kind == "cross")
        print("%-16s %5d cross veins, %3d centres, %.2fs"
              % (name, n_cross, len(res.centres), time.time() - t))
        to_svg(res, "wing_%s.svg" % name.lower())
