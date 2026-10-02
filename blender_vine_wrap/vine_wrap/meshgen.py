"""Vine paths -> stems, leaf stalks, leaves, aerial rootlets, and a Blender mesh.

Per-vertex attributes written for the materials:
  vine_color : per-stem / per-leaf colour variation (linear RGBA)
  vine_age   : 1 at the old woody base of a stem, 0 at the young green tip
  vine_rand  : one random value per stem / leaf (texture variation)
"""

import colorsys
import math

import bpy
from mathutils import Matrix, Vector, noise

UP = Vector((0.0, 0.0, 1.0))


class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.vweights = []
        self.vcolor = []
        self.vage = []
        self.vrand = []
        self.faces = []
        self.face_uvs = []
        self.face_mat = []
        self.leaf_count = 0

    def vert(self, co, weights, color, age=0.0, rand=0.0):
        self.verts.append(co)
        self.vweights.append(weights)
        self.vcolor.append(color)
        self.vage.append(age)
        self.vrand.append(rand)
        return len(self.verts) - 1

    def face(self, idx, uvs, mat):
        self.faces.append(idx)
        self.face_uvs.append(uvs)
        self.face_mat.append(mat)


def _any_perp(t):
    a = Vector((1.0, 0.0, 0.0)) if abs(t.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return t.cross(a).normalized()


def _tangents(pts, closed):
    n = len(pts)
    out = []
    for i in range(n):
        if closed:
            d = pts[(i + 1) % n] - pts[i - 1]
        else:
            d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        if d.length_squared < 1e-16:
            d = out[-1] if out else Vector((0.0, 0.0, 1.0))
        out.append(d.normalized())
    return out


def _frame(t, n):
    ref = n - t * n.dot(t)
    if ref.length_squared < 1e-10:
        ref = _any_perp(t)
    ref.normalize()
    return ref, t.cross(ref)


def _vary(rgb, var, rng, sat_var=0.25, val_var=0.45):
    h, sat, val = colorsys.rgb_to_hsv(*rgb)
    h = (h + rng.uniform(-0.05, 0.05) * var) % 1.0
    sat = min(1.0, max(0.0, sat * (1.0 + rng.uniform(-sat_var, sat_var) * var)))
    val = max(0.0, val * (1.0 + rng.uniform(-val_var, val_var) * var))
    return (*colorsys.hsv_to_rgb(h, sat, val), 1.0)


def _lengths(pts):
    acc = [0.0]
    for i in range(1, len(pts)):
        acc.append(acc[-1] + (pts[i] - pts[i - 1]).length)
    return acc


# ----------------------------------------------------------------------
# Generic tube
# ----------------------------------------------------------------------
def _tube(b, pts, normals, radii, weights, res, mat, col, ages, rand, closed=False, cap=True, v_scale=1.0):
    n = len(pts)
    if n < 2:
        return
    tans = _tangents(pts, closed)
    lens = _lengths(pts)
    r0 = max(radii[0], 1e-6)
    rings = []
    for i in range(n):
        ref, bi = _frame(tans[i], normals[i])
        ring = []
        for j in range(res):
            a = math.tau * j / res
            co = pts[i] + (ref * math.cos(a) + bi * math.sin(a)) * radii[i]
            ring.append(b.vert(co, weights[i], col, ages[i], rand))
        rings.append(ring)
    vs = [s / (math.tau * r0 * 2.0) * v_scale for s in lens]
    segs = n if closed else n - 1
    v_end = (lens[-1] + (pts[0] - pts[-1]).length) / (math.tau * r0 * 2.0) * v_scale
    for i in range(segs):
        i2 = (i + 1) % n
        v1, v2 = vs[i], (vs[i2] if i2 else v_end)
        for j in range(res):
            j2 = (j + 1) % res
            u1, u2 = j / res, (j + 1) / res
            b.face((rings[i][j], rings[i][j2], rings[i2][j2], rings[i2][j]),
                   ((u1, v1), (u2, v1), (u2, v2), (u1, v2)), mat)
    if closed:
        return
    if cap:
        b.face(tuple(reversed(rings[0])),
               tuple((0.5 + 0.5 * math.cos(-math.tau * j / res), 0.5 + 0.5 * math.sin(-math.tau * j / res))
                     for j in range(res)), mat)
    tip = b.vert(pts[-1] + tans[-1] * radii[-1] * 1.5, weights[-1], col, ages[-1], rand)
    last = rings[-1]
    for j in range(res):
        j2 = (j + 1) % res
        b.face((last[j], last[j2], tip),
               ((j / res, vs[-1]), ((j + 1) / res, vs[-1]), ((j + 0.5) / res, vs[-1] + 0.05)), mat)


def add_tube(b, path, res, rng, scale=1.0):
    """The main stem: age gradient and slightly irregular thickness."""
    A = path.attrs
    pts = path.points
    n = len(pts)
    if n < 2:
        return
    lens = _lengths(pts)
    total = lens[-1] or 1.0
    irr = getattr(A, "stem_irregular", 0.0)
    seed = Vector((rng.random() * 100.0, rng.random() * 100.0, rng.random() * 100.0))
    freq = 25.0 / max(scale, 1e-6)
    radii = []
    for p, r in zip(pts, path.radii):
        radii.append(r * max(0.3, 1.0 + irr * noise.noise(p * freq + seed)))
    # Thin side shoots / tendrils are young; the main stem gets older toward its root.
    old = 1.0 if path.leaf_scale > 0.0 else 0.15
    ages = [old * (1.0 - s / total) for s in lens] if not path.closed else [old * 0.6] * n
    col = _vary(A.stem_color, A.color_var, rng)
    _tube(b, pts, path.normals, radii, path.weights, res, A.mat_stem, col, ages, rng.random(),
          closed=path.closed)


# ----------------------------------------------------------------------
# Leaf shapes: half width w(v) along the blade (v: 0 base -> 1 tip) and an
# extra backward offset for lobed bases (heart / ivy).
# ----------------------------------------------------------------------
def _shape(kind, v):
    if kind == "HEART":
        w = math.sin(math.pi * min(1.0, 0.18 + 0.82 * v) ** 0.9) * (1.0 - v) ** 0.25 * 1.15
        return max(w, 0.0), 0.45
    if kind == "IVY":
        env = math.sin(math.pi * min(1.0, 0.12 + 0.88 * v) ** 0.8)
        lobes = 0.62 + 0.38 * abs(math.cos(math.pi * v * 2.35)) ** 2.5
        return env * lobes * 1.25, 0.35
    if kind == "LANCE":
        return math.sin(math.pi * v ** 0.6) ** 1.3 * 0.55, 0.0
    return math.sin(math.pi * v ** 0.85) ** 0.9, 0.05  # OVAL


def add_leaves(b, path, rng, scale):
    A = path.attrs
    density = A.leaf_density * path.leaf_scale
    if not A.use_leaves or density <= 0.0:
        return
    pts = path.points
    n = len(pts)
    if n < 3:
        return
    tans = _tangents(pts, path.closed)
    lens = _lengths(pts)
    total = lens[-1] or 1.0
    interval = 1.0 / (density / scale)
    next_at = interval * rng.uniform(0.3, 1.0)
    side = 1.0 if rng.random() < 0.5 else -1.0
    for i in range(1, n):
        if lens[i] < next_at:
            continue
        next_at = lens[i] + interval * rng.uniform(0.5, 1.5)
        side = -side
        t_along = lens[i] / total
        _leaf(b, pts[i], tans[i], path.normals[i], path.radii[i], side, path.weights[i], t_along,
              A, rng, scale)


def _leaf(b, p, t, n, stem_r, side, weights, t_along, A, rng, scale):
    b.leaf_count += 1
    ref, bi = _frame(t, n)  # ref ~ outward (surface normal), bi ~ sideways
    tip_scale = getattr(A, "leaf_tip_scale", 1.0)
    grow = 1.0 + (tip_scale - 1.0) * (t_along ** 0.8)
    L = A.leaf_size * scale * grow * max(0.2, 1.0 + rng.uniform(-1.0, 1.0) * A.leaf_size_var)
    col = _vary(A.leaf_color, A.color_var, rng, 0.3, 0.6)
    rnd = rng.random()

    # --- leaf stalk (petiole) -------------------------------------------
    pd = (bi * (side * 0.85) + ref * (0.45 + A.leaf_tilt) + t * rng.uniform(0.1, 0.5)
          + Vector((rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2))))
    pd.normalize()
    start = p + (bi * side * 0.8 + ref * 0.4).normalized() * stem_r * 0.8
    pl = getattr(A, "petiole", 0.0) * L
    if pl > 1e-6:
        mid = start + pd * (pl * 0.5) + ref * (pl * 0.12)
        end = start + pd * pl + ref * (pl * 0.05)
        pr = max(stem_r * 0.35, L * 0.025)
        stalk = [start, mid, end]
        _tube(b, stalk, [ref] * 3, [pr, pr * 0.8, pr * 0.6], [weights] * 3, 4, A.mat_stem, col,
              [0.0] * 3, rnd, cap=False, v_scale=0.3)
        base = end
    else:
        base = start

    # --- blade orientation: faces the light, continues the stalk ---------
    light = getattr(A, "leaf_light", 0.0)
    nl = (ref * (1.0 - light) + UP * light)
    if nl.length_squared < 1e-8:
        nl = ref.copy()
    nl.normalize()
    d = pd - nl * pd.dot(nl)
    if d.length_squared < 1e-8:
        d = t - nl * t.dot(nl)
    d.normalize()
    rot = Matrix.Rotation(rng.uniform(-0.45, 0.45), 3, nl)
    d = rot @ d
    nl = Matrix.Rotation(rng.uniform(-0.35, 0.35), 3, d) @ nl
    across = d.cross(nl).normalized()

    kind = getattr(A, "leaf_shape", "OVAL")
    maxw = 0.5 * A.leaf_width
    cup = A.leaf_curl
    wave = getattr(A, "leaf_wave", 0.0)
    phase = rng.uniform(0.0, math.tau)
    NV, NU = 9, 3
    rows = []
    rows_uv = []
    y_min, y_max = 0.0, 1.0
    raw = []
    for iv in range(NV + 1):
        v = iv / NV
        w, lobe = _shape(kind, v)
        hw = max(w * maxw, 0.004)
        row = []
        for iu in range(-NU, NU + 1):
            u = iu / NU
            x = u * hw
            y = v - lobe * (u * u) * hw * (1.0 - v) ** 3
            fold = cup * 0.35 * abs(x)
            droop = -cup * 0.25 * v * v
            ripple = wave * 0.06 * math.sin(v * 13.0 + phase + u * 2.0) * abs(u)
            row.append((x, y, fold + droop + ripple))
            y_min = min(y_min, y)
            y_max = max(y_max, y)
        raw.append(row)
    span = max(y_max - y_min, 1e-6)
    for row in raw:
        ids, uvs = [], []
        for x, y, z in row:
            co = base + d * (y * L) + across * (x * L) + nl * (z * L)
            ids.append(b.vert(co, weights, col, 0.0, rnd))
            uvs.append((0.5 + x / (2.0 * maxw * 1.3), (y - y_min) / span))
        rows.append(ids)
        rows_uv.append(uvs)
    mat = A.mat_leaf
    for iv in range(NV):
        r0, r1 = rows[iv], rows[iv + 1]
        u0, u1 = rows_uv[iv], rows_uv[iv + 1]
        for k in range(2 * NU):
            b.face((r0[k], r0[k + 1], r1[k + 1], r1[k]), (u0[k], u0[k + 1], u1[k + 1], u1[k]), mat)


# ----------------------------------------------------------------------
# Aerial rootlets (ivy style) on vines that cling to a surface
# ----------------------------------------------------------------------
def add_rootlets(b, path, rng, scale):
    A = path.attrs
    dens = getattr(A, "rootlet_density", 0.0)
    if dens <= 0.0 or path.kind != "surface" or path.leaf_scale <= 0.0:
        return
    pts = path.points
    n = len(pts)
    if n < 3:
        return
    tans = _tangents(pts, path.closed)
    lens = _lengths(pts)
    interval = 1.0 / (dens / scale)
    next_at = interval * rng.uniform(0.2, 1.0)
    size = A.rootlet_size * scale
    col = _vary(A.stem_color, A.color_var, rng)
    for i in range(1, n - 1):
        if lens[i] < next_at:
            continue
        next_at = lens[i] + interval * rng.uniform(0.5, 1.5)
        ref, bi = _frame(tans[i], path.normals[i])
        base = pts[i] - ref * path.radii[i] * 0.7
        for _ in range(rng.randint(2, 4)):
            a = rng.uniform(0.0, math.tau)
            d = (-ref * 0.6 + (bi * math.cos(a) + tans[i] * math.sin(a)) * 0.8).normalized()
            ln = size * rng.uniform(0.5, 1.3)
            curve = [base, base + d * (ln * 0.5) - ref * (ln * 0.1), base + d * ln - ref * (ln * 0.25)]
            r = max(path.radii[i] * 0.12, 1e-5)
            _tube(b, curve, [ref] * 3, [r, r * 0.7, r * 0.4], [path.weights[i]] * 3, 3, A.mat_stem,
                  col, [0.6] * 3, rng.random(), cap=False, v_scale=0.3)


# ----------------------------------------------------------------------
def build_mesh(b, name, to_local):
    me = bpy.data.meshes.new(name)
    me.from_pydata([to_local(v) for v in b.verts], [], b.faces)
    me.update()

    uv_layer = me.uv_layers.new(name="UVMap")
    flat = [c for uvs in b.face_uvs for uv in uvs for c in uv]
    uv_layer.data.foreach_set("uv", flat)
    me.polygons.foreach_set("material_index", b.face_mat)
    try:
        me.polygons.foreach_set("use_smooth", [True] * len(b.faces))
    except (AttributeError, TypeError, RuntimeError):
        me.shade_smooth()  # newer Blender versions

    attr = me.color_attributes.new(name="vine_color", type="FLOAT_COLOR", domain="POINT")
    attr.data.foreach_set("color", [c for col in b.vcolor for c in col])
    for name_, values in (("vine_age", b.vage), ("vine_rand", b.vrand)):
        a = me.attributes.new(name=name_, type="FLOAT", domain="POINT")
        a.data.foreach_set("value", values)
    me.update()
    return me


def assign_vertex_groups(obj, b, bone_names):
    """Batch vertex-group assignment (grouped by quantised weight)."""
    buckets = {}
    for vi, ws in enumerate(b.vweights):
        total = sum(ws.values())
        if total <= 1e-8:
            continue
        for name, w in ws.items():
            if bone_names is not None and name not in bone_names:
                continue
            q = round(w / total, 3)
            if q <= 0.0:
                continue
            buckets.setdefault(name, {}).setdefault(q, []).append(vi)
    for name, by_w in buckets.items():
        vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        for w, idx in by_w.items():
            vg.add(idx, w, "REPLACE")
