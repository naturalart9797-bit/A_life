# SPDX-License-Identifier: GPL-3.0-or-later
"""
Twisted Plant Spear
===================

植物の茎・葉をねじり合わせた構造「だけ」でできた槍を、パラメータで生成する Blender アドオン。

使い方:
  1. Edit > Preferences > Add-ons > (右上 ▼) Install from Disk... で plant_spear.zip を選択して有効化
  2. 3D ビューで N キー → サイドバーの「Plant Spear」タブ → 「Add Twisted Spear」
  3. 槍を選択したままスライダーを動かすと、その場で再生成される
"""

bl_info = {
    "name": "Twisted Plant Spear",
    "author": "A_life",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Plant Spear / Add > Mesh > Twisted Spear",
    "description": "茎や葉をねじり合わせた構造の槍をパラメトリックに生成",
    "category": "Add Mesh",
}

import math
import random

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
)
from mathutils import Matrix, Vector, noise

TAU = math.tau
UP = Vector((0.0, 0.0, 1.0))
ATTR_RAND = "strand_random"
MAT_NAME = "TwistedSpear_Strand"


# ---------------------------------------------------------------------------
# math helpers
# ---------------------------------------------------------------------------

def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def smoothstep(e0, e1, x):
    if e1 == e0:
        return 1.0 if x >= e1 else 0.0
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def gauss(x, mu, sigma):
    sigma = max(sigma, 1e-4)
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def perpendicular(v):
    ref = UP if abs(v.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    return v.cross(ref).normalized()


def tangents(pts):
    n = len(pts)
    out = []
    prev = Vector((0.0, 0.0, 1.0))
    for i in range(n):
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        if d.length > 1e-9:
            prev = d.normalized()
        out.append(prev.copy())
    return out


def parallel_transport(pts, first_normal=None):
    """各点の (T, N, B) フレーム（ねじれの少ないフレーム）。"""
    T = tangents(pts)
    n0 = first_normal if first_normal is not None else perpendicular(T[0])
    n0 = n0 - T[0] * n0.dot(T[0])
    n0 = n0.normalized() if n0.length > 1e-9 else perpendicular(T[0])
    N = [n0]
    for i in range(1, len(pts)):
        a, b = T[i - 1], T[i]
        axis = a.cross(b)
        n = N[-1]
        if axis.length > 1e-9:
            ang = math.acos(clamp(a.dot(b), -1.0, 1.0))
            n = Matrix.Rotation(ang, 3, axis.normalized()) @ n
        n = n - b * n.dot(b)
        n = n.normalized() if n.length > 1e-9 else perpendicular(b)
        N.append(n)
    B = [t.cross(nn) for t, nn in zip(T, N)]
    return T, N, B


# ---------------------------------------------------------------------------
# mesh builder
# ---------------------------------------------------------------------------

class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.faces = []
        self.uvs = []
        self.rand = []  # 面ごとのストランド乱数（色のばらつき用）

    def add_tube(self, rows, value, cap_start=None, cap_end=None):
        if len(rows) < 2 or len(rows[0]) < 3:
            return
        m = len(rows[0])
        n = len(rows)
        base = len(self.verts)
        for r in rows:
            self.verts.extend(r)
        for i in range(n - 1):
            v0, v1 = i / (n - 1), (i + 1) / (n - 1)
            for j in range(m):
                j1 = (j + 1) % m
                self.faces.append((base + i * m + j, base + i * m + j1,
                                   base + (i + 1) * m + j1, base + (i + 1) * m + j))
                u0, u1 = j / m, (j + 1) / m
                self.uvs.append(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
                self.rand.append(value)
        for cap, ring_start, flip, v in ((cap_start, base, True, 0.0),
                                         (cap_end, base + (n - 1) * m, False, 1.0)):
            if cap is None:
                continue
            c = len(self.verts)
            self.verts.append(cap)
            for j in range(m):
                a, b = ring_start + j, ring_start + (j + 1) % m
                self.faces.append((c, b, a) if flip else (c, a, b))
                self.uvs.append(((0.5, v),) * 3)
                self.rand.append(value)

    def add_sweep(self, pts, rx, ry, sides, value, side_vecs=None, roll=None):
        """中心線 pts に沿って楕円断面をスイープ（両端はキャップで閉じる）。"""
        if len(pts) < 2:
            return
        T, N, _ = parallel_transport(pts)
        rows = []
        for i, p in enumerate(pts):
            t = T[i]
            x = N[i]
            if side_vecs is not None:
                s = side_vecs[i] - t * side_vecs[i].dot(t)
                if s.length > 1e-9:
                    x = s.normalized()
            y = t.cross(x)
            if roll is not None and roll[i] != 0.0:
                cr, sr = math.cos(roll[i]), math.sin(roll[i])
                x, y = x * cr + y * sr, y * cr - x * sr
            rows.append([p + x * (rx[i] * math.cos(TAU * j / sides))
                         + y * (ry[i] * math.sin(TAU * j / sides)) for j in range(sides)])
        self.add_tube(rows, value, pts[0] - T[0] * (rx[0] * 0.3), pts[-1] + T[-1] * (rx[-1] * 0.3))


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------

class TwistedSpear:
    K = 513  # 中心線のサンプル数

    def __init__(self, p):
        self.p = p
        self.L = max(p.length, 1e-3)
        self.detail = max(p.detail, 0.1)
        self.mb = MeshBuilder()
        self.knots = self._knot_positions()
        self._centerline()
        self._twist_table()

    def res(self, n, lo=3):
        return max(lo, int(round(n * self.detail)))

    # ---- centerline ---------------------------------------------------------
    def _centerline(self):
        p = self.p
        K = self.K
        dirv = Vector((math.cos(p.bend_direction), math.sin(p.bend_direction), 0.0))
        off = Vector((p.seed * 7.31, p.seed * 3.17, 0.0))
        pts = []
        for k in range(K):
            t = k / (K - 1)
            pos = Vector((0.0, 0.0, t * self.L)) + dirv * (p.bend * math.sin(math.pi * t))
            if p.wobble > 0.0:
                q = off + Vector((t * p.wobble_frequency, 0.0, 0.0))
                w = math.sin(math.pi * t) ** 0.5
                pos.x += noise.noise(q) * p.wobble * w
                pos.y += noise.noise(q + Vector((0.0, 13.7, 5.1))) * p.wobble * w
            pts.append(pos)
        self.c_pts = pts
        self.c_T, self.c_N, self.c_B = parallel_transport(pts, Vector((1.0, 0.0, 0.0)))

    def frame(self, t):
        f = clamp(t) * (self.K - 1)
        i = min(int(f), self.K - 2)
        a = f - i
        P = self.c_pts[i].lerp(self.c_pts[i + 1], a)
        T = self.c_T[i].lerp(self.c_T[i + 1], a).normalized()
        N = self.c_N[i].lerp(self.c_N[i + 1], a)
        N = (N - T * N.dot(T)).normalized()
        return P, T, N, T.cross(N)

    # ---- knots ----------------------------------------------------------------
    def _knot_positions(self):
        p = self.p
        n = int(p.knot_count)
        if n <= 0:
            return []
        rng = random.Random(p.seed * 31 + 5)
        a, b = p.knot_start, max(p.knot_start, p.knot_end)
        if n == 1:
            base = [(a + b) * 0.5]
        else:
            base = [lerp(a, b, i / (n - 1)) for i in range(n)]
        span = (b - a) / max(n, 1)
        return [clamp(x + rng.uniform(-0.5, 0.5) * span * p.knot_randomness) for x in base]

    def knot_field(self, t):
        return sum(gauss(t, k, self.p.knot_width) for k in self.knots)

    # ---- silhouette -----------------------------------------------------------
    def radius(self, t):
        """束全体の半径（槍のシルエット）。"""
        p = self.p
        r = p.radius * lerp(1.0, p.taper, t)
        z = t * self.L
        zt = self.L - z
        if p.butt_length > 1e-5 and z < p.butt_length:
            r *= (z / p.butt_length) ** p.butt_sharpness
        if p.tip_length > 1e-5 and zt < p.tip_length:
            r *= (zt / p.tip_length) ** p.tip_sharpness
        if p.bulge > 0.0 and p.bulge_length > 1e-4:
            u = (t - (p.bulge_position - p.bulge_length * 0.5)) / p.bulge_length
            if 0.0 < u < 1.0:
                # 葉形のふくらみ（穂先っぽい）
                shape = math.sin(math.pi * u ** p.bulge_skew) ** 1.5
                r *= 1.0 + p.bulge * shape
        if self.knots and p.knot_pinch > 0.0:
            r *= 1.0 - p.knot_pinch * min(1.0, self.knot_field(t))
        return max(r, p.radius * 0.02)

    # ---- twist ----------------------------------------------------------------
    def twist_sign(self, t):
        r = int(self.p.twist_reversals)
        if r <= 0:
            return 1.0
        return clamp(math.sin(math.pi * (r + 1) * t) * self.p.reversal_sharpness, -1.0, 1.0)

    def _twist_table(self):
        p = self.p
        K = self.K
        cum = [0.0]
        acc = 0.0
        for k in range(1, K):
            t = (k - 0.5) / (K - 1)
            dens = p.twist * (1.0 + p.twist_gradient * (t - 0.5) * 2.0)
            dens *= 1.0 + p.knot_twist * self.knot_field(t)
            dens *= self.twist_sign(t)
            acc += dens * TAU / (K - 1)
            cum.append(acc)
        self._twist = cum

    def twist_at(self, t):
        f = clamp(t) * (self.K - 1)
        i = min(int(f), self.K - 2)
        return lerp(self._twist[i], self._twist[i + 1], f - i)

    # ---- layers ---------------------------------------------------------------
    def layers(self):
        """[(半径位置の割合, 本数, ねじり倍率), ...] と、単位半径でのストランド半径。"""
        p = self.p
        n0 = max(1, int(p.strand_count))
        if n0 == 1:
            return [(0.0, 1, 1.0)], 1.0 * p.fill
        k = math.sin(math.pi / n0) / (1.0 + math.sin(math.pi / n0))
        rs = k * p.fill
        out = []
        d = 1.0 - k
        for l in range(int(p.layers)):
            if l == 0:
                cnt = n0
            else:
                d -= 2.0 * k * p.layer_spacing
                if d < k * 0.5:
                    out.append((0.0, 1, p.layer_twist_ratio ** l))
                    break
                cnt = max(2, int(round(TAU * d / (2.0 * k) * 0.92)))
            out.append((d, cnt, p.layer_twist_ratio ** l))
        return out, rs

    # ---- strands --------------------------------------------------------------
    def build(self):
        p = self.p
        rng = random.Random(p.seed * 101 + 7)
        layer_list, rs_unit = self.layers()
        sides = self.res(int(p.strand_sides), 3)
        rnd = p.randomness
        nseg_base = p.segments * max(1.0, abs(p.twist) / 4.0)
        for li, (dfrac, cnt, tmul) in enumerate(layer_list):
            # 内側の層は少し細く
            layer_scale = 1.0 if li == 0 else p.inner_thickness
            layer_phase = rng.uniform(0.0, TAU)
            for si in range(cnt):
                theta = layer_phase + TAU * si / cnt + rng.uniform(-0.3, 0.3) * rnd
                thick = layer_scale * rng.uniform(1.0 - 0.4 * p.thickness_randomness,
                                                  1.0 + 0.25 * p.thickness_randomness)
                t0 = rng.uniform(0.0, 1.0) * p.end_jitter * 0.15
                t1 = 1.0 - rng.uniform(0.0, 1.0) * p.end_jitter * 0.15
                fray = li == 0 and rng.random() < p.fray_chance
                if fray:
                    t1 = rng.uniform(p.fray_start, max(p.fray_start, p.fray_end))
                splay = p.splay * rng.uniform(0.3, 1.3) * (1.0 if li == 0 else 0.4)
                splay_pos = p.splay_position + rng.uniform(-0.04, 0.04) * rnd
                flare = p.tip_flare * rng.uniform(0.5, 1.5)
                nseed = Vector((li * 31.1 + si * 9.7 + p.seed, si * 3.3, 7.0 + li))
                value = rng.random()
                span = max(t1 - t0, 1e-3)
                segs = self.res(int(nseg_base * span), 8)

                pts, rx, ry, sidev, roll = [], [], [], [], []
                radial = Vector((1.0, 0.0, 0.0))
                for j in range(segs + 1):
                    u = j / segs
                    t = lerp(t0, t1, u)
                    P, T, N, B = self.frame(t)
                    R = self.radius(t)
                    r = R * rs_unit * thick
                    if p.thickness_noise > 0.0:
                        r *= 1.0 + p.thickness_noise * noise.noise(nseed + Vector((t * p.thickness_noise_frequency, 5.0, 0.0)))
                    d = R * dfrac
                    d += splay * gauss(t, splay_pos, p.splay_width)
                    d += flare * smoothstep(1.0 - p.tip_length / self.L, 1.0, t) ** 2
                    d += flare * p.butt_flare * smoothstep(p.butt_length / self.L, 0.0, t) ** 2
                    d += p.looseness * (0.5 + 0.5 * noise.noise(nseed + Vector((t * 8.0, 0.0, 0.0))))
                    ang = theta + tmul * self.twist_at(t)
                    ang += noise.noise(nseed + Vector((0.0, t * 5.0, 0.0))) * p.angle_noise
                    radial = N * math.cos(ang) + B * math.sin(ang)
                    # 端は細くとがらせる
                    end_taper = smoothstep(0.0, 0.04, u) * 0.7 + 0.3
                    end_taper *= smoothstep(1.0, 0.94, u) * 0.75 + 0.25
                    r *= end_taper
                    pts.append(P + radial * d)
                    rx.append(r * (1.0 + p.flatness))
                    ry.append(r * (1.0 - 0.75 * p.flatness))
                    sidev.append(radial.cross(T))
                    roll.append(p.strand_roll * TAU * u + p.strand_roll_offset)

                if fray and len(pts) > 2:
                    self.append_curl(pts, rx, ry, sidev, roll, radial,
                                     p.fray_length * rng.uniform(0.6, 1.4), p.fray_curl, rng)

                if p.ply_count > 1:
                    self.build_plies(pts, rx, ry, value, rng)
                else:
                    self.mb.add_sweep(pts, rx, ry, sides, value, sidev, roll)
        return self.mb

    def build_plies(self, pts, rx, ry, value, rng):
        """1本のストランドを、さらに細い撚り糸の撚り合わせにする。"""
        p = self.p
        n = int(p.ply_count)
        T, N, B = parallel_transport(pts)
        k = math.sin(math.pi / n) / (1.0 + math.sin(math.pi / n))
        sides = self.res(int(p.strand_sides), 3)
        length = sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))
        turns = p.ply_twist * length / max(p.radius * 20.0, 1e-4)
        phase = rng.uniform(0, TAU)
        for j in range(n):
            sub, sr = [], []
            for i, c in enumerate(pts):
                u = i / (len(pts) - 1)
                r = (rx[i] + ry[i]) * 0.5
                a = phase + TAU * j / n + TAU * turns * u
                sub.append(c + (N[i] * math.cos(a) + B[i] * math.sin(a)) * (r * (1.0 - k)))
                sr.append(r * k * p.ply_fill)
            self.mb.add_sweep(sub, sr, sr, sides, value)

    # ---- fray curl ----------------------------------------------------------------
    def tendril_path(self, origin, a, b, length, turns, segs, power=2.5):
        pts = [origin.copy()]
        pos = origin.copy()
        heading = 0.0
        ds = length / segs
        total = turns * TAU
        for i in range(1, segs + 1):
            u = i / segs
            heading += total * (power + 1.0) * u ** power / length * ds
            pos = pos + (a * math.cos(heading) + b * math.sin(heading)) * ds
            pts.append(pos.copy())
        return pts

    def append_curl(self, pts, rx, ry, sidev, roll, radial, length, turns, rng):
        tv = (pts[-1] - pts[-2]).normalized()
        a = (tv + radial * 1.2).normalized()
        b = a.cross(tv) if rng.random() < 0.5 else tv.cross(a)
        b = b.normalized() if b.length > 1e-6 else perpendicular(a)
        b = (b * 0.7 + UP * (0.3 if rng.random() < 0.5 else -0.3)).normalized()
        b = (b - a * b.dot(a)).normalized()
        segs = self.res(int(28 * max(1.0, turns)), 8)
        cpts = self.tendril_path(pts[-1], a, b, length, turns, segs)[1:]
        r_x, r_y = rx[-1], ry[-1]
        for ci, cp in enumerate(cpts):
            u = (ci + 1) / len(cpts)
            k = lerp(1.0, 0.15, u)
            pts.append(cp)
            rx.append(lerp(r_x, (r_x + r_y) * 0.5, min(1.0, u * 3)) * k)
            ry.append(r_y * k)
            sidev.append(sidev[-1])
            roll.append(roll[-1])


# ---------------------------------------------------------------------------
# material
# ---------------------------------------------------------------------------

def _make_material(p):
    mat = bpy.data.materials.new(MAT_NAME)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(bsdf.outputs[0], out.inputs[0])
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (40.0, 40.0, 3.0)
    tex = nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 2.0
    tex.inputs["Detail"].default_value = 6.0
    attr = nodes.new("ShaderNodeAttribute")
    attr.attribute_name = ATTR_RAND
    sub = nodes.new("ShaderNodeMath")
    sub.operation = 'SUBTRACT'
    sub.inputs[1].default_value = 0.5
    mul = nodes.new("ShaderNodeMath")
    mul.name = "PS_Variation"
    mul.operation = 'MULTIPLY'
    add = nodes.new("ShaderNodeMath")
    add.operation = 'ADD'
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.name = "PS_Ramp"
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[1].position = 0.75
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    links.new(attr.outputs["Fac"], sub.inputs[0])
    links.new(sub.outputs[0], mul.inputs[0])
    links.new(tex.outputs[0], add.inputs[0])
    links.new(mul.outputs[0], add.inputs[1])
    links.new(add.outputs[0], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.5
    for node, x, y in ((coord, -1100, 0), (mapping, -900, 0), (tex, -700, 0), (attr, -900, -300),
                       (sub, -700, -300), (mul, -500, -300), (add, -400, 0), (ramp, -250, 0)):
        node.location = (x, y)
    _sync_material(mat, p)
    return mat


def _sync_material(mat, p):
    if not mat or not mat.node_tree:
        return
    nodes = mat.node_tree.nodes
    ramp = nodes.get("PS_Ramp")
    if ramp:
        ramp.color_ramp.elements[0].color = (*p.color_dark, 1.0)
        ramp.color_ramp.elements[-1].color = (*p.color_light, 1.0)
    mul = nodes.get("PS_Variation")
    if mul:
        mul.inputs[1].default_value = p.color_variation


# ---------------------------------------------------------------------------
# object update
# ---------------------------------------------------------------------------

_SUSPEND = False


def rebuild(obj):
    if obj is None or obj.type != 'MESH':
        return
    p = obj.plant_spear
    mb = TwistedSpear(p).build()
    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata([tuple(v) for v in mb.verts], [], mb.faces)
    mesh.polygons.foreach_set("use_smooth", [p.smooth_shading] * len(mb.faces))
    uvl = mesh.uv_layers[0] if mesh.uv_layers else mesh.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", [c for face in mb.uvs for uv in face for c in uv])
    attr = mesh.attributes.get(ATTR_RAND)
    if attr is None:
        attr = mesh.attributes.new(ATTR_RAND, 'FLOAT', 'FACE')
    attr.data.foreach_set("value", mb.rand)
    mesh.update()

    if p.use_material:
        if not mesh.materials or mesh.materials[0] is None:
            mat = _make_material(p)
            if mesh.materials:
                mesh.materials[0] = mat
            else:
                mesh.materials.append(mat)

    mod = obj.modifiers.get("PlantSpear_Subsurf")
    if p.subdivision > 0:
        if mod is None:
            mod = obj.modifiers.new("PlantSpear_Subsurf", 'SUBSURF')
        mod.levels = p.subdivision
        mod.render_levels = p.subdivision
    elif mod is not None:
        obj.modifiers.remove(mod)


def _owner(self):
    obj = self.id_data
    return obj if isinstance(obj, bpy.types.Object) and self.is_spear else None


def _on_update(self, context):
    if not _SUSPEND and self.live_update:
        rebuild(_owner(self))


def _on_color(self, context):
    obj = None if _SUSPEND else _owner(self)
    if obj and obj.data.materials:
        _sync_material(obj.data.materials[0], self)


def F(name, default, lo, hi, desc, length=False, soft=None, upd=_on_update):
    kw = dict(name=name, default=default, min=lo, max=hi, description=desc, update=upd)
    if soft:
        kw["soft_min"], kw["soft_max"] = soft
    if length:
        kw.update(subtype='DISTANCE', unit='LENGTH', precision=4)
    return FloatProperty(**kw)


def I(name, default, lo, hi, desc, soft=None):
    kw = dict(name=name, default=default, min=lo, max=hi, description=desc, update=_on_update)
    if soft:
        kw["soft_min"], kw["soft_max"] = soft
    return IntProperty(**kw)


def A(name, default, lo, hi, desc):
    return FloatProperty(name=name, default=math.radians(default), min=math.radians(lo),
                         max=math.radians(hi), subtype='ANGLE', description=desc, update=_on_update)


def C(name, default, desc):
    return FloatVectorProperty(name=name, default=default, subtype='COLOR', size=3,
                               min=0.0, max=1.0, description=desc, update=_on_color)


class PlantSpearSettings(bpy.types.PropertyGroup):
    is_spear: BoolProperty(default=False)
    live_update: BoolProperty(name="Live Update", default=True, update=_on_update,
                              description="パラメータ変更のたびに再生成する（重いときはオフ）")

    # general
    seed: I("Seed", 1, 0, 100000, "乱数シード。変えると同じパラメータで別個体になる")
    detail: F("Detail", 1.0, 0.2, 4.0, "全体の分割数の倍率（ポリゴン量）")
    length: F("Length", 1.8, 0.05, 100.0, "全長", length=True, soft=(0.2, 5.0))
    smooth_shading: BoolProperty(name="Smooth Shading", default=True, update=_on_update)
    subdivision: I("Subdivision", 1, 0, 4, "サブディビジョンサーフェスのレベル（0で無効）")

    # silhouette
    radius: F("Radius", 0.016, 0.0005, 2.0, "束全体の太さ（半径）", length=True, soft=(0.003, 0.1))
    taper: F("Taper", 0.85, 0.05, 4.0, "上端側の太さ比（下端=1）")
    tip_length: F("Tip Length", 0.3, 0.0, 50.0, "上の穂先（すぼまる部分）の長さ", length=True, soft=(0.0, 1.0))
    tip_sharpness: F("Tip Sharpness", 0.8, 0.1, 4.0, "穂先のすぼまり方（大=細長く鋭い / 小=ずんぐり）")
    butt_length: F("Butt Length", 0.15, 0.0, 50.0, "下端（石突き）のすぼまる長さ", length=True, soft=(0.0, 1.0))
    butt_sharpness: F("Butt Sharpness", 0.6, 0.1, 4.0, "石突きのすぼまり方")
    bulge: F("Head Bulge", 1.0, 0.0, 10.0, "穂先部分の葉形のふくらみ（0でなし）", soft=(0.0, 4.0))
    bulge_position: F("Bulge Position", 0.9, 0.0, 1.0, "ふくらみの中心（全長比）")
    bulge_length: F("Bulge Length", 0.16, 0.01, 1.0, "ふくらみの長さ（全長比）")
    bulge_skew: F("Bulge Skew", 0.7, 0.2, 3.0, "ふくらみの形（<1 根元寄り / >1 先寄り）")
    bend: F("Bend", 0.02, -2.0, 2.0, "全体の反り", length=True, soft=(-0.3, 0.3))
    bend_direction: A("Bend Direction", 0.0, -360.0, 360.0, "反る方向")
    wobble: F("Wobble", 0.004, 0.0, 1.0, "全体のうねり", length=True, soft=(0.0, 0.05))
    wobble_frequency: F("Wobble Freq", 3.0, 0.1, 50.0, "うねりの細かさ")

    # strands
    strand_count: I("Strands", 7, 1, 64, "外周のストランド（茎・葉）の本数")
    layers: I("Layers", 2, 1, 6, "同心円状の層の数（内側ほど本数が減る）")
    layer_spacing: F("Layer Spacing", 0.9, 0.3, 2.0, "層と層の間隔")
    inner_thickness: F("Inner Thickness", 0.85, 0.1, 2.0, "内側の層のストランドの太さ比")
    fill: F("Fill", 1.12, 0.2, 2.5, "ストランドの太さ（1で隣と接する）")
    flatness: F("Flatness", 0.4, 0.0, 1.0, "断面の平たさ（0=丸い茎 / 1=平たい葉）")
    strand_roll: F("Strand Roll", 0.0, -40.0, 40.0, "各ストランド自体の断面のねじれ回数")
    strand_roll_offset: A("Roll Offset", 0.0, -180.0, 180.0, "平たい断面の向き")
    thickness_randomness: F("Thickness Random", 0.5, 0.0, 1.0, "ストランドごとの太さのばらつき")
    thickness_noise: F("Thickness Noise", 0.15, 0.0, 1.0, "長さ方向の太さのムラ")
    thickness_noise_frequency: F("Noise Freq", 12.0, 0.0, 200.0, "太さのムラの細かさ")
    end_jitter: F("End Jitter", 0.3, 0.0, 1.0, "端の長さのバラつき（ギザギザした先端）")
    randomness: F("Randomness", 0.5, 0.0, 1.0, "配置のばらつき")
    segments: I("Segments", 160, 8, 4000, "長さ方向の分割数", soft=(16, 600))
    strand_sides: I("Sides", 7, 3, 32, "周方向の分割数")

    # twist
    twist: F("Twist", 4.0, -60.0, 60.0, "全長でのねじり回数（マイナスで逆回り）", soft=(-15.0, 15.0))
    twist_gradient: F("Twist Gradient", 0.0, -1.0, 1.0, "+で上ほどきつく / -で下ほどきつく")
    layer_twist_ratio: F("Layer Twist Ratio", -0.7, -3.0, 3.0, "内側の層のねじり倍率（マイナスで逆撚り＝ロープ構造）")
    twist_reversals: I("Reversals", 0, 0, 20, "ねじり方向が反転する回数（S撚り↔Z撚り）")
    reversal_sharpness: F("Reversal Sharpness", 3.0, 1.0, 20.0, "反転の切り替わりの急さ")
    angle_noise: F("Angle Noise", 0.15, 0.0, 3.0, "ねじりの乱れ")
    looseness: F("Looseness", 0.0012, 0.0, 0.5, "ストランドの浮き・ゆるみ", length=True, soft=(0.0, 0.02))

    # knots
    knot_count: I("Knots", 1, 0, 30, "きつく締まる箇所の数")
    knot_start: F("Knot Start", 0.62, 0.0, 1.0, "締まる範囲の下端（全長比）")
    knot_end: F("Knot End", 0.62, 0.0, 1.0, "締まる範囲の上端（全長比）")
    knot_width: F("Knot Width", 0.03, 0.003, 0.5, "締まる部分の幅")
    knot_twist: F("Knot Twist", 3.0, 0.0, 30.0, "締まる部分でねじりを増やす量")
    knot_pinch: F("Knot Pinch", 0.25, 0.0, 0.95, "締まる部分で細くくびれる量")
    knot_randomness: F("Knot Randomness", 0.3, 0.0, 1.0, "締まり位置のばらつき")

    # splay / flare
    splay: F("Splay", 0.02, 0.0, 2.0, "途中で束がほどけて籠状に広がる量", length=True, soft=(0.0, 0.15))
    splay_position: F("Splay Position", 0.74, 0.0, 1.0, "広がる位置（全長比）")
    splay_width: F("Splay Width", 0.06, 0.005, 1.0, "広がる範囲")
    tip_flare: F("Tip Flare", 0.0, 0.0, 2.0, "穂先でストランドが外へ開く量（炎・つぼみ状）", length=True, soft=(0.0, 0.1))
    butt_flare: F("Butt Flare Ratio", 0.0, 0.0, 3.0, "下端でも開く量（Tip Flare に対する比）")

    # ply
    ply_count: I("Ply", 1, 1, 6, "各ストランドを何本の撚り糸で作るか（1で無効）")
    ply_twist: F("Ply Twist", 1.0, -20.0, 20.0, "撚り糸のねじりの強さ")
    ply_fill: F("Ply Fill", 1.1, 0.3, 2.0, "撚り糸の太さ")

    # fray
    fray_chance: F("Fray Chance", 0.0, 0.0, 1.0, "外周ストランドが途中で抜け出して巻く確率")
    fray_start: F("Fray Start", 0.45, 0.0, 1.0, "抜け出す範囲の下端（全長比）")
    fray_end: F("Fray End", 0.85, 0.0, 1.0, "抜け出す範囲の上端（全長比）")
    fray_length: F("Fray Length", 0.08, 0.0, 2.0, "抜け出した先の長さ", length=True, soft=(0.0, 0.3))
    fray_curl: F("Fray Curl", 1.2, 0.0, 10.0, "抜け出した先の巻き数")

    # color
    use_material: BoolProperty(name="Auto Material", default=True, update=_on_update,
                               description="マテリアルを自動作成する")
    color_dark: C("Dark", (0.05, 0.11, 0.04), "暗い色")
    color_light: C("Light", (0.36, 0.48, 0.26), "明るい色")
    color_variation: FloatProperty(name="Strand Variation", default=0.35, min=0.0, max=2.0,
                                   description="ストランドごとの色のばらつき", update=_on_color)


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------

PRESETS = {
    "DEFAULT": ("Twisted", "基本形：葉形にふくらんだ穂先・1か所の締まり・上部でほどける", {}),
    "ROPE": ("Rope", "内層と外層を逆に撚ったロープ構造", {
        "strand_count": 9, "layers": 3, "twist": 6.0, "layer_twist_ratio": -1.0, "flatness": 0.0,
        "fill": 1.05, "splay": 0.0, "bulge": 0.4, "knot_count": 0, "thickness_randomness": 0.15,
        "ply_count": 3, "ply_twist": 1.5, "end_jitter": 0.1, "strand_sides": 5,
    }),
    "BRAID": ("Reversing", "ねじり方向が何度も反転するS/Z撚り", {
        "twist": 9.0, "twist_reversals": 5, "reversal_sharpness": 2.0, "strand_count": 8,
        "flatness": 0.6, "splay": 0.0, "knot_count": 0, "bulge": 0.6,
    }),
    "CAGE": ("Cage", "何か所も籠状にほどけて広がる", {
        "strand_count": 10, "layers": 1, "twist": 4.0, "flatness": 0.2, "fill": 0.9,
        "knot_count": 4, "knot_start": 0.2, "knot_end": 0.8, "knot_pinch": 0.55, "knot_twist": 1.0,
        "splay": 0.03, "splay_position": 0.5, "splay_width": 0.35, "bulge": 1.6,
    }),
    "KNOTTED": ("Knotted", "等間隔に締まった節のある竹のような束", {
        "knot_count": 6, "knot_start": 0.1, "knot_end": 0.78, "knot_pinch": 0.6, "knot_twist": 12.0,
        "knot_width": 0.025, "knot_randomness": 0.1, "twist": 1.0, "splay": 0.0, "flatness": 0.2,
    }),
    "BLADES": ("Leaf Blades", "平たい葉を数枚ゆるくねじった、刃のような槍", {
        "strand_count": 4, "layers": 1, "flatness": 0.95, "fill": 1.6, "twist": 1.5,
        "strand_roll": 0.5, "bulge": 1.8, "bulge_length": 0.25, "bulge_position": 0.85,
        "splay": 0.0, "knot_count": 0, "tip_length": 0.45, "thickness_noise": 0.05,
    }),
    "FLAME": ("Flame", "穂先でストランドが炎のように開く", {
        "strand_count": 9, "twist": 5.0, "twist_gradient": 0.6, "tip_flare": 0.08, "bulge": 0.0,
        "tip_length": 0.5, "tip_sharpness": 1.2, "splay": 0.0, "knot_count": 1, "knot_start": 0.68,
        "knot_end": 0.68, "end_jitter": 0.6, "fray_chance": 0.3,
    }),
}

_SKIP = {"is_spear", "live_update", "seed", "rna_type", "name"}


def apply_preset(p, key):
    global _SUSPEND
    _SUSPEND = True
    try:
        for prop in p.bl_rna.properties:
            ident = prop.identifier
            if ident in _SKIP or prop.is_readonly:
                continue
            if getattr(prop, "is_array", False) and prop.array_length > 0:
                setattr(p, ident, tuple(prop.default_array))
            elif hasattr(prop, "default"):
                setattr(p, ident, prop.default)
        for k, v in PRESETS[key][2].items():
            setattr(p, k, v)
    finally:
        _SUSPEND = False


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------

def _active_spear(context):
    obj = context.active_object
    if obj and obj.type == 'MESH' and obj.plant_spear.is_spear:
        return obj
    return None


def _preset_items():
    return [(k, v[0], v[1]) for k, v in PRESETS.items()]


class PLANTSPEAR_OT_add(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_add"
    bl_label = "Add Twisted Spear"
    bl_description = "ねじり構造の槍を追加"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=_preset_items())
    seed: IntProperty(name="Seed", default=1, min=0)

    def execute(self, context):
        global _SUSPEND
        mesh = bpy.data.meshes.new("TwistedSpear")
        obj = bpy.data.objects.new("TwistedSpear", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        p = obj.plant_spear
        apply_preset(p, self.preset)
        _SUSPEND = True
        p.seed = self.seed
        p.is_spear = True
        _SUSPEND = False
        rebuild(obj)
        return {'FINISHED'}


class _SpearOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None


class PLANTSPEAR_OT_regenerate(_SpearOp, bpy.types.Operator):
    bl_idname = "mesh.plant_spear_regenerate"
    bl_label = "Regenerate"
    bl_description = "現在のパラメータで再生成"

    def execute(self, context):
        rebuild(_active_spear(context))
        return {'FINISHED'}


class PLANTSPEAR_OT_randomize(_SpearOp, bpy.types.Operator):
    bl_idname = "mesh.plant_spear_randomize"
    bl_label = "Random Seed"
    bl_description = "シードをランダムに変えて別個体を生成"

    def execute(self, context):
        obj = _active_spear(context)
        obj.plant_spear.seed = random.randint(0, 99999)
        if not obj.plant_spear.live_update:
            rebuild(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_preset(_SpearOp, bpy.types.Operator):
    bl_idname = "mesh.plant_spear_preset"
    bl_label = "Apply Preset"
    bl_description = "プリセットを適用（シード以外を上書き）"

    preset: EnumProperty(name="Preset", items=_preset_items())

    def execute(self, context):
        obj = _active_spear(context)
        apply_preset(obj.plant_spear, self.preset)
        rebuild(obj)
        if obj.data.materials:
            _sync_material(obj.data.materials[0], obj.plant_spear)
        return {'FINISHED'}


class PLANTSPEAR_OT_variant(_SpearOp, bpy.types.Operator):
    bl_idname = "mesh.plant_spear_variant"
    bl_label = "Duplicate as Variant"
    bl_description = "同じパラメータ・別シードで複製して横に並べる（比較用）"

    def execute(self, context):
        global _SUSPEND
        src = _active_spear(context)
        obj = src.copy()
        obj.data = src.data.copy()
        context.collection.objects.link(obj)
        p = src.plant_spear
        obj.location = src.location + Vector((p.radius * (2.0 + p.bulge) * 4.0 + 0.08, 0.0, 0.0))
        src.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        _SUSPEND = True
        obj.plant_spear.seed = random.randint(0, 99999)
        _SUSPEND = False
        rebuild(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_freeze(_SpearOp, bpy.types.Operator):
    bl_idname = "mesh.plant_spear_freeze"
    bl_label = "Convert to Plain Mesh"
    bl_description = "パラメータ編集を終了して通常メッシュにする"

    def execute(self, context):
        _active_spear(context).plant_spear.is_spear = False
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Plant Spear"


class PLANTSPEAR_PT_main(_Base, bpy.types.Panel):
    bl_label = "Twisted Spear"

    def draw(self, context):
        layout = self.layout
        layout.operator_menu_enum(PLANTSPEAR_OT_add.bl_idname, "preset",
                                  text="Add Twisted Spear", icon='ADD')
        obj = _active_spear(context)
        if obj is None:
            layout.label(text="槍オブジェクトを選択すると編集できます", icon='INFO')
            return
        p = obj.plant_spear
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(p, "seed")
        row.operator(PLANTSPEAR_OT_randomize.bl_idname, text="", icon='FILE_REFRESH')
        col.operator_menu_enum(PLANTSPEAR_OT_preset.bl_idname, "preset", text="Apply Preset", icon='PRESET')
        col.operator(PLANTSPEAR_OT_variant.bl_idname, icon='DUPLICATE')
        col = layout.column(align=True)
        col.prop(p, "length")
        col.prop(p, "detail")
        col.prop(p, "subdivision")
        row = layout.row(align=True)
        row.prop(p, "smooth_shading", toggle=True)
        row.prop(p, "live_update", toggle=True)
        if not p.live_update:
            layout.operator(PLANTSPEAR_OT_regenerate.bl_idname, icon='FILE_REFRESH')
        me = obj.data
        layout.label(text=f"Verts: {len(me.vertices):,}  Faces: {len(me.polygons):,}")
        layout.operator(PLANTSPEAR_OT_freeze.bl_idname, icon='MESH_DATA')


def _sub(label, props, idname):
    def draw(self, context):
        p = _active_spear(context).plant_spear
        col = self.layout.column(align=True)
        for item in props:
            if item is None:
                col.separator()
            else:
                col.prop(p, item)

    return type(idname, (_Base, bpy.types.Panel), dict(
        bl_label=label,
        bl_parent_id="PLANTSPEAR_PT_main",
        bl_options={'DEFAULT_CLOSED'},
        poll=classmethod(lambda cls, context: _active_spear(context) is not None),
        draw=draw,
    ))


SUBPANELS = [
    _sub("Silhouette (全体の形)", [
        "radius", "taper", None, "tip_length", "tip_sharpness", "butt_length", "butt_sharpness",
        None, "bulge", "bulge_position", "bulge_length", "bulge_skew",
        None, "bend", "bend_direction", "wobble", "wobble_frequency"], "PLANTSPEAR_PT_shape"),
    _sub("Strands (ストランド)", [
        "strand_count", "layers", "layer_spacing", "inner_thickness", None,
        "fill", "flatness", "strand_roll", "strand_roll_offset", None,
        "thickness_randomness", "thickness_noise", "thickness_noise_frequency", "end_jitter",
        "randomness", None, "segments", "strand_sides"], "PLANTSPEAR_PT_strands"),
    _sub("Twist (ねじり)", [
        "twist", "twist_gradient", "layer_twist_ratio", None,
        "twist_reversals", "reversal_sharpness", None, "angle_noise", "looseness"], "PLANTSPEAR_PT_twist"),
    _sub("Knots (締まり)", [
        "knot_count", "knot_start", "knot_end", "knot_width", "knot_twist", "knot_pinch",
        "knot_randomness"], "PLANTSPEAR_PT_knots"),
    _sub("Splay / Flare (ほどけ・開き)", [
        "splay", "splay_position", "splay_width", None, "tip_flare", "butt_flare"], "PLANTSPEAR_PT_splay"),
    _sub("Ply (撚り糸)", ["ply_count", "ply_twist", "ply_fill"], "PLANTSPEAR_PT_ply"),
    _sub("Fray (抜け出し)", [
        "fray_chance", "fray_start", "fray_end", "fray_length", "fray_curl"], "PLANTSPEAR_PT_fray"),
    _sub("Color (色)", ["use_material", "color_dark", "color_light", "color_variation"],
         "PLANTSPEAR_PT_color"),
]


def menu_func(self, context):
    self.layout.operator(PLANTSPEAR_OT_add.bl_idname, text="Twisted Spear", icon='MOD_SCREW')


CLASSES = (
    PlantSpearSettings,
    PLANTSPEAR_OT_add,
    PLANTSPEAR_OT_regenerate,
    PLANTSPEAR_OT_randomize,
    PLANTSPEAR_OT_preset,
    PLANTSPEAR_OT_variant,
    PLANTSPEAR_OT_freeze,
    PLANTSPEAR_PT_main,
    *SUBPANELS,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.plant_spear = PointerProperty(type=PlantSpearSettings)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
    del bpy.types.Object.plant_spear
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
