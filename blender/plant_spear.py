# SPDX-License-Identifier: GPL-3.0-or-later
"""
Plant Spear Generator
=====================

植物（蔓・葉・巻きひげ）でできた槍を、パラメータで調整しながら生成する Blender アドオン。

使い方:
  1. Edit > Preferences > Add-ons > (右上 ▼) Install from Disk... でこのファイルを選択して有効化
     （または Text Editor に読み込んで Run Script）
  2. 3D ビューで N キー → サイドバーの「Plant Spear」タブ → 「Add Plant Spear」
  3. 槍オブジェクトを選択した状態でパネルのスライダーを動かすと、その場で形が再生成される
"""

bl_info = {
    "name": "Plant Spear Generator",
    "author": "A_life",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Plant Spear / Add > Mesh > Plant Spear",
    "description": "蔓・葉・巻きひげが絡みついた植物性の槍をパラメトリックに生成",
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

MAT_SHAFT, MAT_HEAD, MAT_VINE, MAT_LEAF = range(4)
MAT_NAMES = ("Shaft", "Head", "Vine", "Leaf")


# ---------------------------------------------------------------------------
# small math helpers
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


def leaf_profile(u, base=0.0, peak=0.35, tip_power=0.9):
    """葉の幅プロファイル (u: 0=付け根, 1=先端)。0..1 を返す。"""
    u = clamp(u)
    peak = clamp(peak, 0.05, 0.95)
    if u < peak:
        return base + (1.0 - base) * math.sin(u / peak * math.pi * 0.5)
    k = (u - peak) / (1.0 - peak)
    return math.cos(k * math.pi * 0.5) ** tip_power


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
    """各点で (T, N, B) フレームを返す（ねじれの少ないフレーム）。"""
    T = tangents(pts)
    N = []
    n0 = first_normal if first_normal is not None else perpendicular(T[0])
    n0 = (n0 - T[0] * n0.dot(T[0]))
    n0 = n0.normalized() if n0.length > 1e-9 else perpendicular(T[0])
    N.append(n0)
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
        self.mats = []
        self.uvs = []  # face-corner uv (list per face)

    def add_grid(self, rows, closed, mat, cap_start=None, cap_end=None):
        """rows: 断面リングのリスト（各リングの頂点数は同じ）。"""
        if len(rows) < 2:
            return
        m = len(rows[0])
        if m < 2:
            return
        base = len(self.verts)
        for r in rows:
            self.verts.extend(r)
        n = len(rows)
        span = m if closed else m - 1
        ucols = m if closed else m - 1
        for i in range(n - 1):
            v0 = i / (n - 1)
            v1 = (i + 1) / (n - 1)
            for j in range(span):
                j1 = (j + 1) % m
                a = base + i * m + j
                b = base + i * m + j1
                c = base + (i + 1) * m + j1
                d = base + (i + 1) * m + j
                self.faces.append((a, b, c, d))
                self.mats.append(mat)
                u0 = j / ucols
                u1 = (j + 1) / ucols
                self.uvs.append(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
        if closed and cap_start is not None:
            c = len(self.verts)
            self.verts.append(cap_start)
            for j in range(m):
                self.faces.append((c, base + (j + 1) % m, base + j))
                self.mats.append(mat)
                self.uvs.append(((0.5, 0.0), (0.5, 0.0), (0.5, 0.0)))
        if closed and cap_end is not None:
            c = len(self.verts)
            self.verts.append(cap_end)
            last = base + (n - 1) * m
            for j in range(m):
                self.faces.append((c, last + j, last + (j + 1) % m))
                self.mats.append(mat)
                self.uvs.append(((0.5, 1.0), (0.5, 1.0), (0.5, 1.0)))

    def add_sweep(self, pts, rx, ry=None, sides=8, mat=0, side_vecs=None,
                  roll=None, radial_fn=None, cap_start=False, cap_end=False,
                  tip_start=None, tip_end=None):
        """中心線 pts に沿って楕円断面をスイープしてチューブを作る。"""
        if len(pts) < 2:
            return
        if ry is None:
            ry = rx
        T, N, _B = parallel_transport(pts)
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
            ring = []
            for j in range(sides):
                a = TAU * j / sides
                k = radial_fn(i, a) if radial_fn else 1.0
                ring.append(p + x * (rx[i] * k * math.cos(a)) + y * (ry[i] * k * math.sin(a)))
            rows.append(ring)
        cs = (tip_start if tip_start is not None else pts[0]) if cap_start else None
        ce = (tip_end if tip_end is not None else pts[-1]) if cap_end else None
        self.add_grid(rows, True, mat, cs, ce)


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------

class SpearGenerator:
    def __init__(self, p):
        self.p = p
        self.L = max(p.length, 1e-3)
        self.mb = MeshBuilder()
        self.detail = max(p.detail, 0.1)
        self.head_start = clamp(1.0 - p.head_length / self.L, 0.05, 0.98)
        self.vines = []
        self._build_centerline()

    # ---- resolution helper
    def res(self, n, lo=3):
        return max(lo, int(round(n * self.detail)))

    # ---- centerline -------------------------------------------------------
    def _build_centerline(self):
        p = self.p
        K = 257
        dirv = Vector((math.cos(p.bend_direction), math.sin(p.bend_direction), 0.0))
        off = Vector((p.seed * 7.31, p.seed * 3.17, 0.0))
        pts = []
        for k in range(K):
            t = k / (K - 1)
            pos = Vector((0.0, 0.0, t * self.L))
            pos += dirv * (p.shaft_bend * math.sin(math.pi * t))
            if p.shaft_wobble > 0.0:
                q = off + Vector((t * p.wobble_frequency, 0.0, 0.0))
                w = math.sin(math.pi * t) ** 0.5
                pos.x += noise.noise(q) * p.shaft_wobble * w
                pos.y += noise.noise(q + Vector((0.0, 13.7, 5.1))) * p.shaft_wobble * w
            pts.append(pos)
        self.c_pts = pts
        self.c_T, self.c_N, self.c_B = parallel_transport(pts, Vector((1.0, 0.0, 0.0)))
        self.K = K

    def frame(self, t):
        """中心線上 t(0..1) の (位置, T, N, B)。"""
        f = clamp(t) * (self.K - 1)
        i = min(int(f), self.K - 2)
        a = f - i
        P = self.c_pts[i].lerp(self.c_pts[i + 1], a)
        T = self.c_T[i].lerp(self.c_T[i + 1], a).normalized()
        N = self.c_N[i].lerp(self.c_N[i + 1], a)
        N = (N - T * N.dot(T)).normalized()
        return P, T, N, T.cross(N)

    def extrapolate(self, t):
        """t>1 / t<0 にも対応した中心線上の点。"""
        if 0.0 <= t <= 1.0:
            return self.frame(t)[0]
        if t > 1.0:
            P, T, _, _ = self.frame(1.0)
            return P + T * ((t - 1.0) * self.L)
        P, T, _, _ = self.frame(0.0)
        return P + T * (t * self.L)

    # ---- radii ------------------------------------------------------------
    def shaft_radius(self, t):
        p = self.p
        r = p.shaft_radius * lerp(1.0, p.shaft_taper, t)
        z = t * self.L
        if p.butt_length > 1e-5 and z < p.butt_length:
            k = z / p.butt_length
            r *= lerp(0.04, 1.0, k ** p.butt_sharpness)
        return r

    def head_radius(self, s):
        p = self.p
        r0 = self.shaft_radius(self.head_start)
        b = clamp(p.head_belly, 0.05, 0.95)
        W = max(p.head_width, r0)
        if s < b:
            return r0 + (W - r0) * smoothstep(0.0, 1.0, s / b) ** 0.8
        u = clamp((s - b) / (1.0 - b))
        return W * max(math.cos(u * math.pi * 0.5), 0.0) ** p.head_sharpness

    def body_radius(self, t):
        if t < self.head_start:
            return self.shaft_radius(t)
        s = (t - self.head_start) / (1.0 - self.head_start)
        return max(self.head_radius(s), self.shaft_radius(self.head_start))

    def surface_point(self, t, ang, extra=0.0):
        P, T, N, B = self.frame(t)
        radial = N * math.cos(ang) + B * math.sin(ang)
        return P + radial * (self.body_radius(t) + extra), T, radial

    # ---- parts ------------------------------------------------------------
    def build(self):
        p = self.p
        self.build_shaft()
        self.build_head()
        if p.sheath_count > 0:
            self.build_sheath()
        if p.vine_count > 0:
            self.build_vines()
        if p.wrap_enable:
            self.build_wrap()
        if p.tendril_count > 0:
            self.build_tendrils()
        if p.leaf_count > 0 and self.vines:
            self.build_leaves()
        if p.thorn_count > 0:
            self.build_thorns()
        return self.mb

    def build_shaft(self):
        p = self.p
        segs = self.res(int(p.shaft_segments))
        sides = self.res(int(p.shaft_sides), 4)
        t_end = min(1.0, self.head_start + 0.06 * (1.0 - self.head_start))
        # 石突き（下端）付近を細かく分割
        ts = []
        nb = max(4, segs // 6)
        tb = min(p.butt_length / self.L, t_end * 0.5) if p.butt_length > 0 else 0.0
        if tb > 0:
            for i in range(nb):
                ts.append(tb * (i / nb) ** 1.5 + 1e-4)
        for i in range(segs + 1):
            ts.append(lerp(tb if tb > 0 else 0.0, t_end, i / segs))
        pts = [self.frame(t)[0] for t in ts]
        radii = [self.shaft_radius(t) for t in ts]
        grain = p.shaft_grain
        gfreq = p.grain_frequency
        seedv = Vector((p.seed * 1.7, -p.seed * 2.3, 11.0))

        def radial_fn(i, a):
            if grain <= 0.0:
                return 1.0
            q = seedv + Vector((math.cos(a) * 1.5, math.sin(a) * 1.5, ts[i] * gfreq))
            return 1.0 + grain * noise.noise(q)

        tip = self.frame(0.0)[0]
        self.mb.add_sweep(pts, radii, sides=sides, mat=MAT_SHAFT, radial_fn=radial_fn,
                          cap_start=True, tip_start=tip, cap_end=True)

    def build_head(self):
        p = self.p
        segs = self.res(int(p.head_segments))
        sides = self.res(int(p.head_sides), 6)
        hs = self.head_start
        facets = int(p.head_facets)
        fa = p.head_facet_amount
        rows = []
        for i in range(segs + 1):
            s = (i / segs) ** 0.85 * 0.995
            t = lerp(hs, 1.0, s)
            P, T, N, B = self.frame(t)
            r = self.head_radius(s)
            tw = p.head_twist * s
            ring = []
            for j in range(sides):
                a = TAU * j / sides
                k = 1.0
                if facets >= 3 and fa > 0:
                    seg = TAU / facets
                    local = ((a - tw) % seg) - seg * 0.5
                    poly = math.cos(seg * 0.5) / math.cos(local)
                    k = lerp(1.0, poly, fa)
                # 中央の稜線（葉脈っぽい隆起）
                if p.head_ridge > 0:
                    k *= 1.0 + p.head_ridge * (math.cos(a - tw) ** 8 + math.cos(a - tw + math.pi) ** 8)
                ring.append(P + (N * math.cos(a) + B * math.sin(a)) * (r * k))
            rows.append(ring)
        tip = self.extrapolate(1.0 + p.head_tip_extend / self.L)
        self.mb.add_grid(rows, True, MAT_HEAD, None, tip)

    def build_sheath(self):
        """穂先を包む苞葉（つつみ葉）。"""
        p = self.p
        rng = random.Random(p.seed * 101 + 1)
        n = int(p.sheath_count)
        hs = self.head_start
        hl = 1.0 - hs
        nu = self.res(18, 6)
        nv = self.res(7, 3) | 1
        for k in range(n):
            theta = TAU * k / n + rng.uniform(-0.3, 0.3) * p.sheath_randomness
            length = p.sheath_length * rng.uniform(1.0 - 0.35 * p.sheath_randomness, 1.0)
            s0 = -p.sheath_base
            s1 = length
            half = p.sheath_width * math.pi / max(n, 1) * rng.uniform(0.85, 1.15)
            twist = p.sheath_twist * rng.uniform(0.6, 1.4)
            flare = p.sheath_flare * rng.uniform(0.6, 1.4)
            rows = []
            for i in range(nu + 1):
                u = i / nu
                s = lerp(s0, s1, u)
                t = hs + s * hl
                P, T, N, B = self.frame(clamp(t))
                if t < 0:
                    P = self.extrapolate(t)
                br = self.body_radius(clamp(t))
                w = leaf_profile(u, base=0.4, peak=0.3, tip_power=0.7)
                hug = smoothstep(0.0, 0.25, u)  # 付け根は幹に密着
                row = []
                for j in range(nv):
                    v = j / (nv - 1) * 2.0 - 1.0
                    ang = theta + twist * u + v * half * w
                    rad = br + p.sheath_offset * (0.3 + 0.7 * hug) + flare * p.head_width * u ** 2
                    rad += p.sheath_offset * 1.5 * (1.0 - abs(v)) * hug  # 中肋の膨らみ
                    radial = N * math.cos(ang) + B * math.sin(ang)
                    lift = T * (p.head_length * 0.04 * u * u * (1.0 - abs(v)))
                    row.append(P + radial * rad + lift)
                rows.append(row)
            self.mb.add_grid(rows, False, MAT_LEAF)

    # ---- tendril (curl) path ---------------------------------------------
    def tendril_path(self, origin, a, b, length, turns, power, drift, segs):
        """平面 (a, b) 内で先端ほど強く巻く渦巻きの経路。"""
        c = a.cross(b)
        pts = [origin.copy()]
        pos = origin.copy()
        heading = 0.0
        ds = length / segs
        total = turns * TAU
        for i in range(1, segs + 1):
            u = i / segs
            kappa = total * (power + 1.0) * u ** power / length
            heading += kappa * ds
            d = a * math.cos(heading) + b * math.sin(heading)
            pos = pos + d * ds + c * (drift * ds * u)
            pts.append(pos.copy())
        return pts

    def build_vines(self):
        p = self.p
        rng = random.Random(p.seed * 101 + 2)
        n = int(p.vine_count)
        sides = self.res(int(p.vine_sides), 3)
        for vi in range(n):
            reverse = rng.random() < p.vine_reverse_ratio
            direction = -1.0 if reverse else 1.0
            theta0 = TAU * vi / n + rng.uniform(-0.5, 0.5)
            t0 = clamp(p.vine_start + rng.uniform(-0.05, 0.05) * p.vine_randomness, 0.0, 0.98)
            t1 = clamp(p.vine_end + rng.uniform(-0.08, 0.04) * p.vine_randomness, t0 + 0.02, 1.0)
            turns = p.vine_turns * rng.uniform(1.0 - 0.3 * p.vine_randomness, 1.0 + 0.3 * p.vine_randomness)
            bulge = p.vine_bulge * rng.uniform(0.2, 1.3)
            bulge_pos = p.vine_bulge_position + rng.uniform(-0.08, 0.08) * p.vine_randomness
            thick = p.vine_thickness * rng.uniform(0.75, 1.25)
            ribbon = p.vine_ribbon_width * rng.uniform(0.7, 1.3)
            flat = p.vine_flatness
            nseed = Vector((vi * 17.3 + p.seed, vi * 5.1, 0.0))
            segs = self.res(int(p.vine_segments * max(1.0, turns / 2.0)), 8)

            pts, rx, ry, sidev, roll = [], [], [], [], []
            info = []  # (pos, tangent, radial, thickness) for decoration
            for i in range(segs + 1):
                u = i / segs
                t = lerp(t0, t1, u)
                ang = theta0 + direction * TAU * turns * u
                ang += noise.noise(nseed + Vector((u * 4.0, 0.0, 0.0))) * 0.4 * p.vine_randomness
                taper = smoothstep(0.0, 0.04, u) * 0.6 + 0.4
                taper *= lerp(1.0, 0.45, u ** 2)
                w_thick = thick * taper
                loose = p.vine_looseness * (0.5 + 0.5 * noise.noise(nseed + Vector((0.0, u * 6.0, 3.0))))
                extra = w_thick * (1.0 - 0.6 * flat) + p.vine_gap + loose
                extra += bulge * gauss(t, bulge_pos, p.vine_bulge_width)
                pos, T, radial = self.surface_point(t, ang, extra)
                pts.append(pos)
                rx.append(lerp(w_thick, ribbon * 0.5 * taper, flat))
                ry.append(w_thick * (1.0 - 0.7 * flat))
                sidev.append(radial.cross(T))
                roll.append(p.vine_ribbon_twist * TAU * u + vi)
                info.append((pos, radial, w_thick))

            # 末端の巻きひげ
            if rng.random() < p.vine_end_curl and len(pts) > 2:
                tv = (pts[-1] - pts[-2]).normalized()
                radial = info[-1][1]
                a = (tv + radial * 0.8).normalized()
                b = (a.cross(tv) if rng.random() < 0.5 else tv.cross(a))
                if b.length < 1e-6:
                    b = perpendicular(a)
                b = b.normalized()
                b = (b * 0.7 + UP * 0.3 * (1 if rng.random() < 0.5 else -1)).normalized()
                b = (b - a * b.dot(a)).normalized()
                L = p.vine_curl_length * rng.uniform(0.7, 1.3)
                csegs = self.res(int(28 * max(1.0, p.vine_curl_turns)), 8)
                cpts = self.tendril_path(pts[-1], a, b, L, p.vine_curl_turns, p.tendril_tightness,
                                         0.3, csegs)[1:]
                r_end_x, r_end_y = rx[-1], ry[-1]
                for ci, cp in enumerate(cpts):
                    u = (ci + 1) / len(cpts)
                    k = lerp(1.0, 0.15, u)
                    pts.append(cp)
                    rx.append(lerp(r_end_x, (r_end_x + r_end_y) * 0.5, min(1.0, u * 3)) * k)
                    ry.append(r_end_y * k)
                    sidev.append(sidev[-1])
                    roll.append(roll[-1])

            self.mb.add_sweep(pts, rx, ry, sides=sides, mat=MAT_VINE, side_vecs=sidev,
                              roll=roll, cap_start=True, cap_end=True)
            self.vines.append(info)

    def build_wrap(self):
        """柄と穂先の境目の縛り（きつく巻いた蔓）。"""
        p = self.p
        sides = self.res(6, 3)
        tc = p.wrap_position
        half = p.wrap_length / self.L * 0.5
        strands = 2 if p.wrap_cross else 1
        rng = random.Random(p.seed * 101 + 3)
        for sidx in range(strands):
            direction = 1.0 if sidx == 0 else -1.0
            segs = self.res(int(max(16, p.wrap_turns * 14)), 8)
            pts, radii = [], []
            phase = rng.uniform(0, TAU)
            for i in range(segs + 1):
                u = i / segs
                t = clamp(tc - half + 2 * half * u)
                ang = phase + direction * TAU * p.wrap_turns * u
                bump = 1.0 + 0.6 * math.sin(math.pi * u)  # 中央が膨らむ
                r = p.wrap_thickness * (0.35 + 0.65 * math.sin(math.pi * min(1.0, u * 1.0)) ** 0.3)
                pos, _T, _rad = self.surface_point(t, ang, p.wrap_thickness * bump * (1 + 0.5 * sidx))
                pts.append(pos)
                radii.append(r)
            self.mb.add_sweep(pts, radii, sides=sides, mat=MAT_VINE, cap_start=True, cap_end=True)

    def build_tendrils(self):
        p = self.p
        rng = random.Random(p.seed * 101 + 4)
        sides = self.res(5, 3)
        for _ in range(int(p.tendril_count)):
            t = rng.uniform(p.tendril_zone_start, max(p.tendril_zone_start, p.tendril_zone_end))
            ang = rng.uniform(0, TAU)
            origin, T, radial = self.surface_point(t, ang, p.vine_thickness * 0.5)
            elev = math.radians(p.tendril_angle) * rng.uniform(0.5, 1.5)
            a = (radial * math.cos(elev) + T * math.sin(elev)).normalized()
            side = radial.cross(T).normalized()
            twist = rng.uniform(-0.6, 0.6)
            b = (T * math.cos(twist) + side * math.sin(twist))
            if rng.random() < 0.4:
                b = -b
            b = (b - a * b.dot(a)).normalized()
            L = p.tendril_length * rng.uniform(0.6, 1.4)
            turns = p.tendril_turns * rng.uniform(0.7, 1.3)
            segs = self.res(int(30 * max(1.0, turns)), 8)
            pts = self.tendril_path(origin, a, b, L, turns, p.tendril_tightness,
                                    rng.uniform(-0.4, 0.4), segs)
            radii = [p.tendril_thickness * lerp(1.0, 0.2, (i / segs) ** 0.8) for i in range(segs + 1)]
            self.mb.add_sweep(pts, radii, sides=sides, mat=MAT_VINE, cap_start=True, cap_end=True)

    def leaf_mesh(self, base, main, normal, length, width, curl, fold, nu, nv):
        side = main.cross(normal).normalized()
        rows = []
        for i in range(nu + 1):
            u = i / nu
            w = leaf_profile(u, base=0.12, peak=0.35, tip_power=0.8) * width * 0.5
            # 長さ方向の反り
            bend = curl * u * u * length
            row = []
            for j in range(nv):
                v = j / (nv - 1) * 2.0 - 1.0
                pos = base + main * (u * length) + side * (v * w)
                pos += normal * (bend + fold * abs(v) * w)
                row.append(pos)
            rows.append(row)
        self.mb.add_grid(rows, False, MAT_LEAF)

    def build_leaves(self):
        p = self.p
        rng = random.Random(p.seed * 101 + 5)
        nu = self.res(8, 3)
        nv = self.res(5, 3) | 1
        for _ in range(int(p.leaf_count)):
            info = self.vines[rng.randrange(len(self.vines))]
            lo = int(len(info) * p.leaf_zone_start)
            hi = max(lo + 1, int(len(info) * p.leaf_zone_end))
            idx = clamp(rng.randrange(lo, hi), 1, len(info) - 2)
            idx = int(idx)
            pos, radial, thick = info[idx]
            tv = (info[idx + 1][0] - info[idx - 1][0]).normalized()
            if rng.random() < 0.5:
                tv = -tv
            ang = math.radians(p.leaf_angle) * rng.uniform(0.6, 1.4)
            main = (tv * math.cos(ang) + radial * math.sin(ang)).normalized()
            # 葉の面は「外向き」に近い向きに
            normal = (radial - main * radial.dot(main))
            normal = normal.normalized() if normal.length > 1e-6 else perpendicular(main)
            roll = rng.uniform(-0.8, 0.8)
            side = main.cross(normal)
            normal = (normal * math.cos(roll) + side * math.sin(roll)).normalized()
            size = p.leaf_size * rng.uniform(0.6, 1.3)
            self.leaf_mesh(pos + radial * thick * 0.5, main, normal, size, size * p.leaf_width,
                           p.leaf_curl, 0.35, nu, nv)

    def build_thorns(self):
        p = self.p
        rng = random.Random(p.seed * 101 + 6)
        sides = self.res(5, 3)
        sources = self.vines if (p.thorn_on_vines and self.vines) else None
        for _ in range(int(p.thorn_count)):
            if sources:
                info = sources[rng.randrange(len(sources))]
                idx = rng.randrange(1, len(info) - 1)
                pos, radial, thick = info[idx]
                T = (info[idx + 1][0] - info[idx - 1][0]).normalized()
            else:
                t = rng.uniform(0.08, self.head_start)
                pos, T, radial = self.surface_point(t, rng.uniform(0, TAU))
                thick = 0.0
            ang = math.radians(p.thorn_angle) * rng.uniform(0.7, 1.3)
            up = T if T.dot(UP) >= 0 else -T
            d = (radial * math.cos(ang) + up * math.sin(ang)).normalized()
            size = p.thorn_size * rng.uniform(0.6, 1.4)
            base = pos + radial * (thick * 0.3)
            n = 4
            pts = []
            for i in range(n + 1):
                u = i / n
                # 少し上に反るトゲ
                pts.append(base + d * (u * size) + up * (u * u * size * 0.35))
            radii = [size * 0.28 * (1.0 - i / n) ** 1.2 + 1e-5 for i in range(n + 1)]
            self.mb.add_sweep(pts[:-1], radii[:-1], sides=sides, mat=MAT_VINE,
                              cap_start=True, cap_end=True, tip_end=pts[-1])


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

def _color_pairs(p):
    return (
        (p.color_shaft_dark, p.color_shaft_light, 0.55, (30.0, 30.0, 1.5)),
        (p.color_head_dark, p.color_head_light, 0.45, (6.0, 6.0, 2.0)),
        (p.color_vine_dark, p.color_vine_light, 0.45, (8.0, 8.0, 8.0)),
        (p.color_leaf_dark, p.color_leaf_light, 0.45, (12.0, 12.0, 12.0)),
    )


def _make_material(name, dark, light, rough, scale):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(bsdf.outputs[0], out.inputs[0])
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = scale
    tex = nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 2.0
    tex.inputs["Detail"].default_value = 6.0
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.name = "PS_Ramp"
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[1].position = 0.75
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    links.new(tex.outputs[0], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    coord.location = (-900, 0)
    mapping.location = (-700, 0)
    tex.location = (-500, 0)
    ramp.location = (-300, 0)
    _set_ramp(mat, dark, light)
    return mat


def _set_ramp(mat, dark, light):
    if not mat or not mat.node_tree:
        return
    ramp = mat.node_tree.nodes.get("PS_Ramp")
    if ramp is None:
        return
    ramp.color_ramp.elements[0].color = (*dark, 1.0)
    ramp.color_ramp.elements[-1].color = (*light, 1.0)


def ensure_materials(obj):
    p = obj.plant_spear
    mesh = obj.data
    pairs = _color_pairs(p)
    while len(mesh.materials) < 4:
        mesh.materials.append(None)
    for i, (dark, light, rough, scale) in enumerate(pairs):
        if mesh.materials[i] is None:
            mesh.materials[i] = _make_material(f"PlantSpear_{MAT_NAMES[i]}", dark, light, rough, scale)


def update_colors(obj):
    p = obj.plant_spear
    mesh = obj.data
    for i, (dark, light, _r, _s) in enumerate(_color_pairs(p)):
        if i < len(mesh.materials):
            _set_ramp(mesh.materials[i], dark, light)


# ---------------------------------------------------------------------------
# object update
# ---------------------------------------------------------------------------

_SUSPEND = False


def rebuild(obj):
    if obj is None or obj.type != 'MESH':
        return
    p = obj.plant_spear
    gen = SpearGenerator(p)
    mb = gen.build()
    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata([tuple(v) for v in mb.verts], [], mb.faces)
    mesh.polygons.foreach_set("material_index", mb.mats)
    mesh.polygons.foreach_set("use_smooth", [p.smooth_shading] * len(mb.faces))
    uvl = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    flat = [c for face in mb.uvs for uv in face for c in uv]
    uvl.data.foreach_set("uv", flat)
    mesh.update()
    if p.use_materials:
        ensure_materials(obj)

    mod = obj.modifiers.get("PlantSpear_Subsurf")
    if p.subdivision > 0:
        if mod is None:
            mod = obj.modifiers.new("PlantSpear_Subsurf", 'SUBSURF')
        mod.levels = p.subdivision
        mod.render_levels = p.subdivision
    elif mod is not None:
        obj.modifiers.remove(mod)


def _on_update(self, context):
    if _SUSPEND:
        return
    obj = self.id_data
    if isinstance(obj, bpy.types.Object) and self.is_spear and self.live_update:
        rebuild(obj)


def _on_color(self, context):
    if _SUSPEND:
        return
    obj = self.id_data
    if isinstance(obj, bpy.types.Object) and self.is_spear:
        update_colors(obj)


def F(name, default, lo, hi, desc, length=False, soft=None, upd=_on_update):
    kw = dict(name=name, default=default, min=lo, max=hi, description=desc, update=upd)
    if soft:
        kw["soft_min"], kw["soft_max"] = soft
    if length:
        kw["subtype"] = 'DISTANCE'
        kw["unit"] = 'LENGTH'
        kw["precision"] = 4
    return FloatProperty(**kw)


def A(name, default, lo, hi, desc):
    return FloatProperty(name=name, default=math.radians(default), min=math.radians(lo),
                         max=math.radians(hi), subtype='ANGLE', description=desc, update=_on_update)


def I(name, default, lo, hi, desc, soft=None):
    kw = dict(name=name, default=default, min=lo, max=hi, description=desc, update=_on_update)
    if soft:
        kw["soft_min"], kw["soft_max"] = soft
    return IntProperty(**kw)


def C(name, default, desc):
    return FloatVectorProperty(name=name, default=default, subtype='COLOR', size=3,
                               min=0.0, max=1.0, description=desc, update=_on_color)


class PlantSpearSettings(bpy.types.PropertyGroup):
    is_spear: BoolProperty(default=False)
    live_update: BoolProperty(name="Live Update", default=True,
                              description="パラメータ変更のたびに再生成する（重いときはオフ）",
                              update=_on_update)

    # general
    seed: I("Seed", 1, 0, 100000, "乱数シード。変えると同じパラメータで別個体になる")
    detail: F("Detail", 1.0, 0.2, 4.0, "全体の分割数の倍率（ポリゴン量）")
    length: F("Length", 1.8, 0.05, 100.0, "槍の全長", length=True, soft=(0.2, 5.0))
    smooth_shading: BoolProperty(name="Smooth Shading", default=True, update=_on_update)
    subdivision: I("Subdivision", 1, 0, 4, "サブディビジョンサーフェスのレベル (0で無効)")
    use_materials: BoolProperty(name="Auto Materials", default=True, update=_on_update,
                                description="マテリアルを自動作成する")

    # shaft
    shaft_radius: F("Radius", 0.012, 0.0005, 1.0, "柄の太さ（半径）", length=True, soft=(0.002, 0.08))
    shaft_taper: F("Taper", 0.8, 0.1, 3.0, "上端の太さ比（下端=1）")
    shaft_bend: F("Bend", 0.02, -1.0, 1.0, "全体の反り量", length=True, soft=(-0.2, 0.2))
    bend_direction: A("Bend Direction", 0.0, -360.0, 360.0, "反る方向")
    shaft_wobble: F("Wobble", 0.004, 0.0, 1.0, "幹のうねり", length=True, soft=(0.0, 0.05))
    wobble_frequency: F("Wobble Freq", 3.0, 0.1, 50.0, "うねりの細かさ")
    shaft_grain: F("Grain", 0.08, 0.0, 0.5, "表面の筋・凹凸の強さ")
    grain_frequency: F("Grain Freq", 40.0, 0.0, 500.0, "筋の細かさ（長さ方向）")
    butt_length: F("Butt Spike", 0.12, 0.0, 10.0, "石突き（下端の尖り）の長さ", length=True, soft=(0.0, 0.5))
    butt_sharpness: F("Butt Sharpness", 0.6, 0.1, 3.0, "石突きの尖り方（小=鋭い）")
    shaft_segments: I("Segments", 90, 4, 1000, "柄の長さ方向の分割数", soft=(8, 300))
    shaft_sides: I("Sides", 10, 3, 64, "柄の周方向の分割数")

    # head
    head_length: F("Length", 0.26, 0.01, 10.0, "穂先の長さ", length=True, soft=(0.03, 1.0))
    head_width: F("Width", 0.03, 0.001, 1.0, "穂先の最大半径", length=True, soft=(0.005, 0.15))
    head_belly: F("Belly Position", 0.32, 0.05, 0.95, "最大幅の位置（0=根元, 1=先端）")
    head_sharpness: F("Sharpness", 1.6, 0.3, 5.0, "先端の尖り（大きいほど細く鋭い）")
    head_facets: I("Facets", 6, 0, 16, "結晶状の面の数（0で丸い）")
    head_facet_amount: F("Facet Amount", 0.5, 0.0, 1.0, "面の角張り具合")
    head_twist: A("Twist", 40.0, -720.0, 720.0, "面のねじれ")
    head_ridge: F("Midrib", 0.08, 0.0, 1.0, "葉脈状の稜線の高さ")
    head_tip_extend: F("Tip Extend", 0.01, 0.0, 1.0, "最先端の針状の伸び", length=True, soft=(0.0, 0.1))
    head_segments: I("Segments", 32, 4, 300, "穂先の長さ方向の分割数")
    head_sides: I("Sides", 18, 6, 96, "穂先の周方向の分割数")

    # sheath (bracts around head)
    sheath_count: I("Count", 5, 0, 32, "穂先を包む苞葉の枚数")
    sheath_length: F("Length", 0.75, 0.05, 1.2, "苞葉の長さ（穂先長に対する比）")
    sheath_base: F("Base Offset", 0.3, 0.0, 2.0, "苞葉の付け根を穂先より下へずらす量（穂先長比）")
    sheath_width: F("Width", 1.1, 0.1, 3.0, "苞葉の幅（1で隙間なく一周）")
    sheath_flare: F("Flare", 0.5, -1.0, 5.0, "先端の開き具合")
    sheath_twist: A("Twist", 35.0, -720.0, 720.0, "苞葉のねじれ")
    sheath_offset: F("Offset", 0.0015, 0.0, 0.1, "芯からの浮き", length=True, soft=(0.0, 0.01))
    sheath_randomness: F("Randomness", 0.5, 0.0, 1.0, "苞葉のばらつき")

    # vines
    vine_count: I("Count", 4, 0, 64, "巻きつく蔓の本数")
    vine_start: F("Start", 0.03, 0.0, 1.0, "蔓の始点（全長比）")
    vine_end: F("End", 0.86, 0.0, 1.0, "蔓の終点（全長比）")
    vine_turns: F("Turns", 2.5, 0.0, 50.0, "巻き数", soft=(0.0, 10.0))
    vine_reverse_ratio: F("Reverse Ratio", 0.25, 0.0, 1.0, "逆回りに巻く蔓の割合（交差が生まれる）")
    vine_thickness: F("Thickness", 0.0035, 0.0001, 0.2, "蔓の太さ", length=True, soft=(0.0005, 0.02))
    vine_flatness: F("Flatness", 0.65, 0.0, 1.0, "0=丸い蔓, 1=平たいリボン状の葉")
    vine_ribbon_width: F("Ribbon Width", 0.014, 0.0, 0.5, "リボン状のときの幅", length=True, soft=(0.0, 0.05))
    vine_ribbon_twist: F("Ribbon Twist", 1.2, -20.0, 20.0, "リボンのねじれ回数")
    vine_gap: F("Gap", 0.0015, 0.0, 0.5, "幹からの浮き", length=True, soft=(0.0, 0.02))
    vine_looseness: F("Looseness", 0.006, 0.0, 0.5, "ゆるみ（ランダムな浮き）", length=True, soft=(0.0, 0.05))
    vine_bulge: F("Bulge", 0.03, 0.0, 1.0, "途中で大きく膨らむループの量", length=True, soft=(0.0, 0.1))
    vine_bulge_position: F("Bulge Position", 0.74, 0.0, 1.0, "膨らむ位置（全長比）")
    vine_bulge_width: F("Bulge Width", 0.12, 0.01, 1.0, "膨らむ範囲")
    vine_randomness: F("Randomness", 0.6, 0.0, 1.0, "蔓ごとのばらつき")
    vine_end_curl: F("End Curl Chance", 0.75, 0.0, 1.0, "蔓の先端がくるっと巻く確率")
    vine_curl_length: F("Curl Length", 0.07, 0.0, 2.0, "先端の巻きの長さ", length=True, soft=(0.0, 0.3))
    vine_curl_turns: F("Curl Turns", 1.4, 0.0, 10.0, "先端の巻き数")
    vine_segments: I("Segments", 70, 8, 2000, "蔓の分割数", soft=(16, 400))
    vine_sides: I("Sides", 6, 3, 32, "蔓の周方向の分割数")

    # wrap
    wrap_enable: BoolProperty(name="Binding Wrap", default=True, update=_on_update,
                              description="柄と穂先の境目に蔓をきつく巻く")
    wrap_position: F("Position", 0.64, 0.0, 1.0, "縛りの位置（全長比）")
    wrap_length: F("Length", 0.07, 0.0, 5.0, "縛りの長さ", length=True, soft=(0.0, 0.3))
    wrap_turns: F("Turns", 6.0, 0.5, 100.0, "縛りの巻き数", soft=(1.0, 20.0))
    wrap_thickness: F("Thickness", 0.0025, 0.0001, 0.1, "縛り紐の太さ", length=True, soft=(0.0005, 0.01))
    wrap_cross: BoolProperty(name="Cross", default=True, update=_on_update,
                             description="逆方向にもう一本巻いて交差させる")

    # tendrils
    tendril_count: I("Count", 4, 0, 200, "巻きひげの本数")
    tendril_zone_start: F("Zone Start", 0.68, 0.0, 1.0, "巻きひげが生える範囲の下端（全長比）")
    tendril_zone_end: F("Zone End", 0.86, 0.0, 1.0, "巻きひげが生える範囲の上端（全長比）")
    tendril_length: F("Length", 0.09, 0.0, 2.0, "巻きひげの長さ", length=True, soft=(0.0, 0.3))
    tendril_turns: F("Turns", 1.5, 0.0, 10.0, "巻き数")
    tendril_tightness: F("Tightness", 2.5, 0.0, 8.0, "先端ほど急に巻く度合い")
    tendril_angle: A("Angle", 35.0, -90.0, 90.0, "幹から出る角度（上向き）")
    tendril_thickness: F("Thickness", 0.0015, 0.0001, 0.05, "巻きひげの太さ", length=True, soft=(0.0003, 0.006))

    # leaves
    leaf_count: I("Count", 14, 0, 1000, "蔓につく小葉の枚数")
    leaf_size: F("Size", 0.022, 0.0, 1.0, "小葉の長さ", length=True, soft=(0.0, 0.1))
    leaf_width: F("Width", 0.35, 0.02, 2.0, "小葉の幅（長さ比）")
    leaf_angle: A("Angle", 35.0, -90.0, 90.0, "蔓からの開き角")
    leaf_curl: F("Curl", 0.25, -2.0, 2.0, "小葉の反り")
    leaf_zone_start: F("Zone Start", 0.1, 0.0, 1.0, "蔓上のどこから生えるか")
    leaf_zone_end: F("Zone End", 0.95, 0.0, 1.0, "蔓上のどこまで生えるか")

    # thorns
    thorn_count: I("Count", 16, 0, 2000, "トゲの数")
    thorn_size: F("Size", 0.006, 0.0, 0.5, "トゲの長さ", length=True, soft=(0.0, 0.03))
    thorn_angle: A("Angle", 45.0, -90.0, 90.0, "トゲの上向き角度")
    thorn_on_vines: BoolProperty(name="On Vines", default=True, update=_on_update,
                                 description="オン: 蔓に生やす / オフ: 柄に生やす")

    # colors
    color_shaft_dark: C("Shaft Dark", (0.12, 0.15, 0.08), "柄の暗い色")
    color_shaft_light: C("Shaft Light", (0.42, 0.45, 0.30), "柄の明るい色")
    color_head_dark: C("Head Dark", (0.12, 0.22, 0.10), "穂先の暗い色")
    color_head_light: C("Head Light", (0.55, 0.68, 0.45), "穂先の明るい色")
    color_vine_dark: C("Vine Dark", (0.03, 0.09, 0.04), "蔓の暗い色")
    color_vine_light: C("Vine Light", (0.16, 0.28, 0.14), "蔓の明るい色")
    color_leaf_dark: C("Leaf Dark", (0.05, 0.14, 0.05), "葉の暗い色")
    color_leaf_light: C("Leaf Light", (0.25, 0.42, 0.20), "葉の明るい色")


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------

PRESETS = {
    "REFERENCE": ("Reference", "参考画像風：細い柄に平たい蔓が絡み、苞葉に包まれた穂先", {}),
    "THORNY": ("Thorny", "丸い蔓とトゲだらけの攻撃的な槍", {
        "vine_flatness": 0.0, "vine_thickness": 0.0028, "vine_count": 6, "vine_turns": 4.0,
        "vine_bulge": 0.008, "thorn_count": 80, "thorn_size": 0.009, "thorn_on_vines": True,
        "leaf_count": 4, "head_facets": 4, "head_facet_amount": 0.9, "head_sharpness": 2.4,
        "sheath_count": 3, "sheath_flare": 1.5, "tendril_count": 2,
        "color_vine_dark": (0.06, 0.05, 0.02), "color_vine_light": (0.25, 0.2, 0.1),
    }),
    "LUSH": ("Lush", "葉と巻きひげがたっぷりの生い茂った槍", {
        "vine_count": 7, "vine_bulge": 0.045, "vine_looseness": 0.012, "leaf_count": 70,
        "leaf_size": 0.03, "tendril_count": 14, "tendril_zone_start": 0.3, "tendril_zone_end": 0.85,
        "sheath_count": 7, "sheath_flare": 0.9, "thorn_count": 0, "vine_end": 0.82,
    }),
    "MINIMAL": ("Minimal", "装飾少なめのすっきりした槍", {
        "vine_count": 2, "vine_turns": 1.5, "vine_bulge": 0.0, "leaf_count": 0, "tendril_count": 0,
        "thorn_count": 0, "sheath_count": 3, "wrap_cross": False, "shaft_wobble": 0.0,
    }),
    "WILD": ("Wild", "大きくうねり、ゆるく絡んだ野生的な槍", {
        "shaft_bend": 0.06, "shaft_wobble": 0.015, "vine_count": 5, "vine_bulge": 0.07,
        "vine_bulge_width": 0.2, "vine_looseness": 0.02, "vine_reverse_ratio": 0.5,
        "vine_ribbon_twist": 3.0, "vine_curl_length": 0.12, "vine_curl_turns": 2.0,
        "tendril_count": 9, "tendril_length": 0.14, "sheath_flare": 1.2, "sheath_twist": math.radians(120),
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


class PLANTSPEAR_OT_add(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_add"
    bl_label = "Add Plant Spear"
    bl_description = "植物性の槍を追加"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=[(k, v[0], v[1]) for k, v in PRESETS.items()])
    seed: IntProperty(name="Seed", default=1, min=0)

    def execute(self, context):
        mesh = bpy.data.meshes.new("PlantSpear")
        obj = bpy.data.objects.new("PlantSpear", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        p = obj.plant_spear
        apply_preset(p, self.preset)
        global _SUSPEND
        _SUSPEND = True
        p.seed = self.seed
        p.is_spear = True
        _SUSPEND = False
        rebuild(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_regenerate(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_regenerate"
    bl_label = "Regenerate"
    bl_description = "現在のパラメータで再生成"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None

    def execute(self, context):
        rebuild(_active_spear(context))
        return {'FINISHED'}


class PLANTSPEAR_OT_randomize(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_randomize"
    bl_label = "Random Seed"
    bl_description = "シードをランダムに変えて別個体を生成"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None

    def execute(self, context):
        obj = _active_spear(context)
        obj.plant_spear.seed = random.randint(0, 99999)
        if not obj.plant_spear.live_update:
            rebuild(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_preset(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_preset"
    bl_label = "Apply Preset"
    bl_description = "プリセットを適用（シード以外のパラメータを上書き）"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=[(k, v[0], v[1]) for k, v in PRESETS.items()])

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None

    def execute(self, context):
        obj = _active_spear(context)
        apply_preset(obj.plant_spear, self.preset)
        rebuild(obj)
        update_colors(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_duplicate_variant(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_variant"
    bl_label = "Duplicate as Variant"
    bl_description = "同じパラメータ・別シードで複製して横に並べる（比較用）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None

    def execute(self, context):
        src = _active_spear(context)
        obj = src.copy()
        obj.data = src.data.copy()
        context.collection.objects.link(obj)
        obj.location = src.location + Vector((src.plant_spear.head_width * 6 + 0.1, 0.0, 0.0))
        src.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        global _SUSPEND
        _SUSPEND = True
        obj.plant_spear.seed = random.randint(0, 99999)
        _SUSPEND = False
        rebuild(obj)
        return {'FINISHED'}


class PLANTSPEAR_OT_freeze(bpy.types.Operator):
    bl_idname = "mesh.plant_spear_freeze"
    bl_label = "Convert to Plain Mesh"
    bl_description = "パラメータ編集を終了して通常メッシュにする（手作業で編集できるように）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_spear(context) is not None

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
    bl_label = "Plant Spear"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator_menu_enum(PLANTSPEAR_OT_add.bl_idname, "preset", text="Add Plant Spear", icon='ADD')
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
        col.operator(PLANTSPEAR_OT_duplicate_variant.bl_idname, icon='DUPLICATE')
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


def _sub(label, props, idname, header_prop=None):
    def draw(self, context):
        p = _active_spear(context).plant_spear
        col = self.layout.column(align=True)
        if header_prop:
            col.active = getattr(p, header_prop)
        for item in props:
            if item is None:
                col.separator()
            else:
                col.prop(p, item)

    attrs = dict(
        bl_label=label,
        bl_parent_id="PLANTSPEAR_PT_main",
        bl_options={'DEFAULT_CLOSED'},
        poll=classmethod(lambda cls, context: _active_spear(context) is not None),
        draw=draw,
    )
    if header_prop:
        def draw_header(self, context):
            self.layout.prop(_active_spear(context).plant_spear, header_prop, text="")
        attrs["draw_header"] = draw_header
    return type(idname, (_Base, bpy.types.Panel), attrs)


SUBPANELS = [
    _sub("Shaft (柄)", ["shaft_radius", "shaft_taper", None, "shaft_bend", "bend_direction",
                        "shaft_wobble", "wobble_frequency", None, "shaft_grain", "grain_frequency",
                        None, "butt_length", "butt_sharpness", None, "shaft_segments", "shaft_sides"],
         "PLANTSPEAR_PT_shaft"),
    _sub("Head (穂先)", ["head_length", "head_width", "head_belly", "head_sharpness", "head_tip_extend",
                         None, "head_facets", "head_facet_amount", "head_twist", "head_ridge",
                         None, "head_segments", "head_sides"], "PLANTSPEAR_PT_head"),
    _sub("Sheath Leaves (苞葉)", ["sheath_count", "sheath_length", "sheath_base", "sheath_width",
                                  "sheath_flare", "sheath_twist", "sheath_offset", "sheath_randomness"],
         "PLANTSPEAR_PT_sheath"),
    _sub("Vines (蔓)", ["vine_count", "vine_start", "vine_end", "vine_turns", "vine_reverse_ratio",
                        None, "vine_thickness", "vine_flatness", "vine_ribbon_width", "vine_ribbon_twist",
                        None, "vine_gap", "vine_looseness", "vine_bulge", "vine_bulge_position",
                        "vine_bulge_width", "vine_randomness",
                        None, "vine_end_curl", "vine_curl_length", "vine_curl_turns",
                        None, "vine_segments", "vine_sides"], "PLANTSPEAR_PT_vines"),
    _sub("Binding Wrap (縛り)", ["wrap_position", "wrap_length", "wrap_turns", "wrap_thickness",
                                 "wrap_cross"], "PLANTSPEAR_PT_wrap", header_prop="wrap_enable"),
    _sub("Tendrils (巻きひげ)", ["tendril_count", "tendril_zone_start", "tendril_zone_end",
                                 "tendril_length", "tendril_turns", "tendril_tightness",
                                 "tendril_angle", "tendril_thickness"], "PLANTSPEAR_PT_tendrils"),
    _sub("Leaves (小葉)", ["leaf_count", "leaf_size", "leaf_width", "leaf_angle", "leaf_curl",
                           "leaf_zone_start", "leaf_zone_end"], "PLANTSPEAR_PT_leaves"),
    _sub("Thorns (トゲ)", ["thorn_count", "thorn_size", "thorn_angle", "thorn_on_vines"],
         "PLANTSPEAR_PT_thorns"),
    _sub("Colors (色)", ["use_materials", None, "color_shaft_dark", "color_shaft_light",
                         "color_head_dark", "color_head_light", "color_vine_dark", "color_vine_light",
                         "color_leaf_dark", "color_leaf_light"], "PLANTSPEAR_PT_colors"),
]


def menu_func(self, context):
    self.layout.operator(PLANTSPEAR_OT_add.bl_idname, text="Plant Spear", icon='OUTLINER_OB_FORCE_FIELD')


CLASSES = (
    PlantSpearSettings,
    PLANTSPEAR_OT_add,
    PLANTSPEAR_OT_regenerate,
    PLANTSPEAR_OT_randomize,
    PLANTSPEAR_OT_preset,
    PLANTSPEAR_OT_duplicate_variant,
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
