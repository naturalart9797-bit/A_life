"""Turn vine paths into tube + leaf geometry and a Blender mesh."""

import colorsys
import math

import bpy
from mathutils import Vector

MAT_STEM = 0
MAT_LEAF = 1


class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.vweights = []
        self.vsway = []
        self.vcolor = []
        self.faces = []
        self.face_uvs = []
        self.face_mat = []

    def vert(self, co, weights, sway, color):
        self.verts.append(co)
        self.vweights.append(weights)
        self.vsway.append(sway)
        self.vcolor.append(color)
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


def stem_color(attrs, rng):
    return _vary(attrs.stem_color, attrs.color_var, rng)


def leaf_color(attrs, rng):
    return _vary(attrs.leaf_color, attrs.color_var, rng, 0.3, 0.6)


def add_tube(b, path, res, rng):
    pts = path.points
    n = len(pts)
    if n < 2:
        return
    closed = path.closed
    tans = _tangents(pts, closed)
    col = stem_color(path.attrs, rng)
    rings = []
    vs = []
    acc = 0.0
    r0 = max(path.radii[0], 1e-6)
    for i in range(n):
        if i:
            acc += (pts[i] - pts[i - 1]).length
        vs.append(acc / (math.tau * r0 * 2.0))
        ref, bi = _frame(tans[i], path.normals[i])
        r = path.radii[i]
        ring = []
        for j in range(res):
            a = math.tau * j / res
            co = pts[i] + (ref * math.cos(a) + bi * math.sin(a)) * r
            ring.append(b.vert(co, path.weights[i], path.sway[i], col))
        rings.append(ring)

    segs = n if closed else n - 1
    v_end = (acc + (pts[0] - pts[-1]).length) / (math.tau * r0 * 2.0)
    for i in range(segs):
        i2 = (i + 1) % n
        v1 = vs[i]
        v2 = vs[i2] if i2 else v_end
        for j in range(res):
            j2 = (j + 1) % res
            u1, u2 = j / res, (j + 1) / res
            b.face((rings[i][j], rings[i][j2], rings[i2][j2], rings[i2][j]),
                   ((u1, v1), (u2, v1), (u2, v2), (u1, v2)), MAT_STEM)

    if not closed:
        # Start cap and pointed tip.
        b.face(tuple(reversed(rings[0])),
               tuple((0.5 + 0.5 * math.cos(-math.tau * j / res), 0.5 + 0.5 * math.sin(-math.tau * j / res))
                     for j in range(res)), MAT_STEM)
        tip_co = pts[-1] + tans[-1] * path.radii[-1] * 2.0
        tip = b.vert(tip_co, path.weights[-1], path.sway[-1], col)
        last = rings[-1]
        vt = vs[-1] + 0.05
        for j in range(res):
            j2 = (j + 1) % res
            b.face((last[j], last[j2], tip),
                   ((j / res, vs[-1]), ((j + 1) / res, vs[-1]), ((j + 0.5) / res, vt)), MAT_STEM)


# Leaf outline: (v along, half width) for the midrib stations.
_LEAF_STATIONS = ((0.0, 0.0), (0.25, 0.42), (0.55, 0.5), (0.82, 0.32), (1.0, 0.0))


def add_leaves(b, path, rng, scale):
    P = path.attrs
    density = P.leaf_density * path.leaf_scale
    if not P.use_leaves or density <= 0.0:
        return
    pts = path.points
    n = len(pts)
    if n < 3:
        return
    tans = _tangents(pts, path.closed)
    interval = 1.0 / (density / scale)
    next_at = interval * rng.uniform(0.3, 1.0)
    acc = 0.0
    side = 1.0 if rng.random() < 0.5 else -1.0
    for i in range(1, n):
        acc += (pts[i] - pts[i - 1]).length
        if acc < next_at:
            continue
        next_at = acc + interval * rng.uniform(0.5, 1.5)
        side = -side
        _leaf(b, pts[i], tans[i], path.normals[i], path.radii[i], side,
              path.weights[i], path.sway[i], P, rng, scale)


def _leaf(b, p, t, n, stem_r, side, weights, sway, P, rng, scale):
    ref, bi = _frame(t, n)  # ref ~ surface normal, bi ~ sideways
    a = math.radians(rng.uniform(35.0, 80.0))
    d = t * math.cos(a) + bi * (math.sin(a) * side) + ref * (P.leaf_tilt + rng.uniform(-0.15, 0.15))
    d.normalize()
    nl = ref - d * ref.dot(d)
    if nl.length_squared < 1e-10:
        nl = _any_perp(d)
    nl.normalize()
    across = d.cross(nl)

    length = P.leaf_size * scale * max(0.2, 1.0 + rng.uniform(-1.0, 1.0) * P.leaf_size_var)
    width = P.leaf_width
    cup = P.leaf_curl
    base = p + bi * (side * stem_r * 0.8)
    col = leaf_color(P, rng)

    def V(x, y, lift):
        co = base + d * (y * length) + across * (x * length) + nl * (lift * length)
        return b.vert(co, weights, sway, col)

    def uv(x, y):
        return (0.5 + x / (2.0 * width * 0.5), y)

    mid, left, right = [], [], []
    mid_xy, left_xy, right_xy = [], [], []
    for (y, hw) in _LEAF_STATIONS:
        droop = -cup * 0.4 * y * y
        mid.append(V(0.0, y, droop))
        mid_xy.append((0.0, y))
        if hw > 0.0:
            x = hw * width
            edge = droop + cup * 0.25 * hw
            right.append(V(x, y, edge))
            left.append(V(-x, y, edge))
            right_xy.append((x, y))
            left_xy.append((-x, y))

    m, R, L = mid, right, left
    mx, rx, lx = mid_xy, right_xy, left_xy
    # Base triangles.
    b.face((m[0], R[0], m[1]), (uv(*mx[0]), uv(*rx[0]), uv(*mx[1])), MAT_LEAF)
    b.face((m[0], m[1], L[0]), (uv(*mx[0]), uv(*mx[1]), uv(*lx[0])), MAT_LEAF)
    for k in range(len(R) - 1):
        b.face((m[k + 1], R[k], R[k + 1], m[k + 2]),
               (uv(*mx[k + 1]), uv(*rx[k]), uv(*rx[k + 1]), uv(*mx[k + 2])), MAT_LEAF)
        b.face((m[k + 1], m[k + 2], L[k + 1], L[k]),
               (uv(*mx[k + 1]), uv(*mx[k + 2]), uv(*lx[k + 1]), uv(*lx[k])), MAT_LEAF)
    # Tip triangles.
    b.face((m[-2], R[-1], m[-1]), (uv(*mx[-2]), uv(*rx[-1]), uv(*mx[-1])), MAT_LEAF)
    b.face((m[-2], m[-1], L[-1]), (uv(*mx[-2]), uv(*mx[-1]), uv(*lx[-1])), MAT_LEAF)


# ----------------------------------------------------------------------
def build_mesh(b, name, to_local):
    me = bpy.data.meshes.new(name)
    me.from_pydata([to_local(v) for v in b.verts], [], b.faces)
    me.update()

    uv_layer = me.uv_layers.new(name="UVMap")
    flat = [c for uvs in b.face_uvs for uv in uvs for c in uv]
    uv_layer.data.foreach_set("uv", flat)
    me.polygons.foreach_set("material_index", b.face_mat)
    me.polygons.foreach_set("use_smooth", [True] * len(b.faces))

    attr = me.color_attributes.new(name="vine_color", type="FLOAT_COLOR", domain="POINT")
    attr.data.foreach_set("color", [c for col in b.vcolor for c in col])
    me.update()
    return me


def assign_vertex_groups(obj, b, bone_names, sway_name):
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

    if sway_name:
        vg = obj.vertex_groups.new(name=sway_name)
        by_w = {}
        for vi, s in enumerate(b.vsway):
            q = round(s, 2)
            if q > 0.0:
                by_w.setdefault(q, []).append(vi)
        for w, idx in by_w.items():
            vg.add(idx, w, "REPLACE")
