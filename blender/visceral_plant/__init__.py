# SPDX-License-Identifier: GPL-3.0-or-later
"""Visceral Plant - 胃や腸のような茎と根を持つ植物モデルを生成する Blender アドオン.

腸のような管が何本もねじれ合いながら垂直に伸びて幹をつくり、
途中で胃袋のような塊を抱え込む。下では管がほどけて根となり、
地面を這いながら先端がくるりと巻く。上では一本の細い茎にまとまる。
"""

bl_info = {
    "name": "Visceral Plant (内臓植物ジェネレーター)",
    "author": "A_life",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > サイドバー (N) > Visceral / Add > Mesh > Visceral Plant",
    "description": "胃や腸のような茎・根の構造を持つ植物メッシュを手続き的に生成します",
    "category": "Add Mesh",
}

import math
import random

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
)
from mathutils import Matrix, Vector, noise

TAG = "visceral_plant"
UP = Vector((0, 0, 1))


# ---------------------------------------------------------------------------
# 幾何ユーティリティ
# ---------------------------------------------------------------------------

def _random_unit(rng):
    while True:
        v = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        if 0.01 < v.length_squared <= 1.0:
            return v.normalized()


def _any_perpendicular(v):
    axis = Vector((0, 0, 1)) if abs(v.z) < 0.9 else Vector((1, 0, 0))
    return v.cross(axis).normalized()


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _rand_offset(rng, s=500.0):
    return Vector((rng.uniform(-s, s), rng.uniform(-s, s), rng.uniform(-s, s)))


class SpatialHash:
    """自己回避用の簡易空間ハッシュ (点 + 半径 + 所属チューブID)."""

    def __init__(self, cell):
        self.cell = max(cell, 1e-4)
        self.grid = {}

    def _key(self, p):
        c = self.cell
        return (int(math.floor(p.x / c)), int(math.floor(p.y / c)), int(math.floor(p.z / c)))

    def insert(self, p, r, owner, index):
        self.grid.setdefault(self._key(p), []).append((p.copy(), r, owner, index))

    def repulsion(self, p, r, owner, index, ignore_recent):
        kx, ky, kz = self._key(p)
        push = Vector((0, 0, 0))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q, qr, qo, qi in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                        if qo == owner and index - qi < ignore_recent:
                            continue
                        d = p - q
                        dist = d.length
                        limit = (r + qr) * 1.2
                        if 1e-6 < dist < limit:
                            push += d / dist * ((limit - dist) / limit)
        return push


class Tube:
    """中心線 (points) と各点の半径 (radii) からなるチューブ."""

    def __init__(self, kind):
        self.kind = kind  # "strand", "sac", "top", "shoot", "root", "bulb"
        self.points = []
        self.radii = []


# ---------------------------------------------------------------------------
# 成長シミュレーション
# ---------------------------------------------------------------------------

class VisceralGrower:
    def __init__(self, p):
        self.p = p
        self.rng = random.Random(p.seed)
        self.tubes = []
        self.hash = SpatialHash(p.strand_radius * 3.0)
        self._owner = 0

    def _new_owner(self):
        self._owner += 1
        return self._owner

    # --- 幹: ねじれ合う腸の束 ----------------------------------------
    def _bundle_envelope(self, u, z):
        """高さ u (0..1) での束の半径."""
        p = self.p
        r = p.bundle_radius
        # 下端で根に向かって大きく広がる
        r *= 1.0 + p.root_flare * (1.0 - _smoothstep(u / 0.22)) ** 2
        # 上端で一本にまとまる
        r *= 1.0 - 0.92 * _smoothstep((u - 0.7) / 0.3)
        # 胃袋を抱え込む
        if p.sac_enable:
            zs = p.height * p.sac_height
            half = p.sac_size * p.sac_elongation
            dz = (z - zs) / half
            if abs(dz) < 1.0:
                wrap = p.sac_size * math.sqrt(1.0 - dz * dz) * 0.8 + p.strand_radius * 0.3
                r = max(r, wrap)
        return r

    def _strand(self, index, count):
        p = self.p
        rng = self.rng
        H = p.height
        z0 = H * p.bundle_bottom
        z1 = H * p.bundle_top
        theta0 = math.tau * index / count + rng.uniform(-0.35, 0.35)
        turns = p.twist * rng.uniform(0.75, 1.25)
        off = _rand_offset(rng)
        loops = []
        for _ in range(p.loop_count):
            if rng.random() < 1.0 / max(count, 1) * 1.5:
                loops.append((rng.uniform(0.2, 0.8), p.loop_size * rng.uniform(0.7, 1.3),
                              rng.uniform(-1.2, 1.2)))
        bulges = []
        for _ in range(p.strand_bulges):
            if rng.random() < 0.5:
                bulges.append((rng.uniform(0.15, 0.85), rng.uniform(0.05, 0.09)))

        est_len = math.hypot(z1 - z0, math.tau * turns * p.bundle_radius * 1.3)
        n = max(12, int(est_len / (p.strand_radius * 0.45)))
        tube = Tube("strand")
        for k in range(n + 1):
            u = k / n
            z = z0 + (z1 - z0) * u
            r = self._bundle_envelope(u, z)
            ang = theta0 + math.tau * turns * u
            ang += p.irregularity * 1.5 * noise.noise(Vector((u * 3.0, 0.0, 0.0)) + off)
            for (uc, amp, swing) in loops:
                g = math.exp(-((u - uc) / 0.07) ** 2)
                r += amp * g
                ang += swing * g
            pos = Vector((r * math.cos(ang), r * math.sin(ang), z))
            jitter = noise.noise_vector(Vector((u * 4.0, 1.7, 0.0)) + off)
            pos += jitter * (p.irregularity * p.bundle_radius * 0.5) * (1.0 - _smoothstep((u - 0.75) / 0.25))
            rad = p.strand_radius * (1.15 - 0.45 * u)
            for (uc, w) in bulges:
                rad *= 1.0 + p.bulge_size * math.exp(-((u - uc) / w) ** 2)
            tube.points.append(pos)
            tube.radii.append(rad)
        return tube, theta0

    # --- 地面を這う根 --------------------------------------------------
    def _crawl(self, start, direction, length, r0, r1, outward):
        p = self.p
        rng = self.rng
        owner = self._new_owner()
        step = max(min(r0, r1) * 0.7, p.strand_radius * 0.18)
        n = max(6, int(length / step))
        pos = start.copy()
        d = direction.normalized()
        curl_sign = rng.choice((-1.0, 1.0))
        off = _rand_offset(rng)
        freq = 1.0 / max(p.strand_radius * 12.0, 1e-3)
        pts, radii = [], []
        for i in range(n + 1):
            t = i / n
            r = r0 + (r1 - r0) * t
            pts.append(pos.copy())
            radii.append(r)
            self.hash.insert(pos, r, owner, i)
            if i == n:
                break
            nv = noise.noise_vector(pos * freq + off)
            if pos.z <= r * 1.1:
                # 地面を這う: 水平に蛇行し、先端でくるりと巻く
                nv.z = 0.0
                d = d + nv * p.root_wander
                curl = p.root_curl * 0.6 * _smoothstep((t - 0.55) / 0.45)
                d = d + Vector((0, 0, curl_sign)).cross(d) * curl
                d = d + outward * 0.12
                d.z = 0.0
            else:
                d = d + nv * (p.root_wander * 0.4) + Vector((0, 0, -p.root_gravity))
            if p.self_avoid > 0:
                push = self.hash.repulsion(pos + d * step, r, owner, i,
                                           ignore_recent=int(r * 3 / step) + 3)
                if pos.z <= r * 1.1:
                    push.z = 0.0
                d = d + push * p.self_avoid
            if d.length_squared < 1e-9:
                d = outward.copy()
            d.normalize()
            pos = pos + d * step
            if pos.z < r:
                pos.z = r
        return pts, radii

    # --- 汎用の伸長 (上部の茎・小枝) -----------------------------------
    def _grow(self, kind, start, direction, length, r0, r1, wander, curl, tropism):
        p = self.p
        rng = self.rng
        step = max(min(r0, r1) * 0.6, p.strand_radius * 0.15)
        n = max(4, int(length / step))
        pos = start.copy()
        d = direction.normalized()
        off = _rand_offset(rng)
        freq = 1.0 / max(p.height * 0.25, 1e-3)
        axis = _random_unit(rng)
        tube = Tube(kind)
        for i in range(n + 1):
            t = i / n
            tube.points.append(pos.copy())
            tube.radii.append(r0 + (r1 - r0) * t)
            if i == n:
                break
            d = d + noise.noise_vector(pos * freq + off) * wander
            d = d + axis.cross(d) * curl
            d = d + UP * tropism
            d.normalize()
            pos = pos + d * step
        self.tubes.append(tube)
        return tube

    # --- 植物全体 -----------------------------------------------------
    def build(self):
        p = self.p
        rng = self.rng
        H = p.height
        count = max(1, p.strand_count)

        # 1. ねじれ合う腸の束 + その下の根
        for i in range(count):
            strand, theta0 = self._strand(i, count)
            base = strand.points[0]
            down = (strand.points[0] - strand.points[1]).normalized()
            outward = Vector((base.x, base.y, 0.0))
            outward = outward.normalized() if outward.length > 1e-6 else Vector((math.cos(theta0), math.sin(theta0), 0))
            r0 = strand.radii[0]
            rpts, rradii = self._crawl(base, down, p.root_reach * rng.uniform(0.7, 1.3),
                                       r0, r0 * p.root_taper, outward)
            tube = Tube("strand")
            tube.points = list(reversed(rpts))[:-1] + strand.points
            tube.radii = list(reversed(rradii))[:-1] + strand.radii
            tube.root_len = len(rpts) - 1
            self.tubes.append(tube)

        # 2. 束から直接出る追加の根
        z0 = H * p.bundle_bottom
        for _ in range(p.extra_roots):
            ang = rng.uniform(0, math.tau)
            outward = Vector((math.cos(ang), math.sin(ang), 0))
            z = z0 + rng.uniform(0.0, 0.12) * H
            start = outward * (p.bundle_radius * 0.8) + Vector((0, 0, z))
            r0 = p.strand_radius * rng.uniform(0.55, 0.85)
            d = (outward + Vector((0, 0, -0.6))).normalized()
            pts, radii = self._crawl(start, d, p.root_reach * rng.uniform(0.5, 1.0),
                                     r0, r0 * p.root_taper, outward)
            tube = Tube("root")
            tube.points, tube.radii = pts, radii
            self.tubes.append(tube)

        # 3. 胃袋 (束に抱え込まれた塊)
        if p.sac_enable:
            zs = H * p.sac_height
            half = p.sac_size * p.sac_elongation
            tilt = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), 0)) * p.sac_size * 0.35
            sac = Tube("sac")
            n = 14
            for k in range(n + 1):
                t = k / n
                # J 字に曲がった中心線
                bend = math.sin(math.pi * t) * tilt + tilt * (t - 0.5) * 0.6
                sac.points.append(Vector((0, 0, zs - half + 2 * half * t)) + bend)
                prof = math.sin(math.pi * (0.04 + 0.92 * t)) ** 0.65
                asym = 1.0 + 0.25 * math.sin(math.pi * t * 0.8)
                sac.radii.append(max(p.sac_size * prof * asym, p.strand_radius))
            self.tubes.append(sac)

        # 4. 上部の細い茎
        z1 = H * p.bundle_top
        top_start = Vector((0, 0, z1 - H * 0.04))
        top = self._grow("top", top_start, UP, max(H - top_start.z, H * 0.05),
                         p.top_radius * 1.3, p.top_radius * 0.75,
                         wander=p.top_wiggle, curl=0.0, tropism=0.35)
        if p.top_bulb:
            end = top.points[-1]
            d = (top.points[-1] - top.points[-2]).normalized()
            r = p.top_radius * p.top_bulb_size
            bulb = Tube("bulb")
            n = 10
            for k in range(n + 1):
                t = k / n
                bulb.points.append(end + d * (-r * 0.4 + r * 2.2 * t))
                prof = math.sin(math.pi * (0.05 + 0.9 * t)) ** 0.6
                bulb.radii.append(max(r * prof, p.top_radius * 0.6))
            self.tubes.append(bulb)

        # 5. 上部の茎から出る小枝
        n_top = len(top.points)
        for _ in range(p.shoot_count):
            i = rng.randint(int(n_top * 0.05), max(int(n_top * 0.6), int(n_top * 0.05) + 1))
            i = min(i, n_top - 2)
            base = top.points[i]
            tangent = (top.points[i + 1] - top.points[max(i - 1, 0)]).normalized()
            side = _any_perpendicular(tangent)
            side.rotate(Matrix.Rotation(rng.uniform(0, math.tau), 3, tangent))
            d = (side + tangent * 0.35).normalized()
            r0 = top.radii[i] * 0.5
            self._grow("shoot", base, d, p.shoot_length * rng.uniform(0.6, 1.3),
                       r0, r0 * 0.25, wander=0.25, curl=0.05, tropism=0.05)

        return self.tubes


# ---------------------------------------------------------------------------
# チューブ → メッシュ
# ---------------------------------------------------------------------------

def _transport_frames(points):
    n = len(points)
    tangents = []
    for i in range(n):
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, n - 1)]
        t = b - a
        tangents.append(t.normalized() if t.length_squared > 1e-12 else Vector((0, 0, 1)))
    normals = [_any_perpendicular(tangents[0])]
    for i in range(1, n):
        prev_t, t = tangents[i - 1], tangents[i]
        nrm = normals[-1]
        axis = prev_t.cross(t)
        if axis.length_squared > 1e-12:
            ang = prev_t.angle(t, 0.0)
            nrm = nrm.copy()
            nrm.rotate(Matrix.Rotation(ang, 3, axis.normalized()))
        nrm = (nrm - t * nrm.dot(t)).normalized()
        normals.append(nrm)
    return tangents, normals


def tube_to_rings(tube, p, rng):
    """チューブを断面リング列 (頂点座標) と両端の点に変換する."""
    pts, radii = tube.points, tube.radii
    if len(pts) < 2:
        return [], None, None
    tangents, normals = _transport_frames(pts)
    res = p.ring_resolution
    root_len = getattr(tube, "root_len", 0)

    depth_by_kind = {
        "strand": p.haustra_depth,
        "top": p.haustra_depth * 0.6,
        "sac": p.haustra_depth * 0.5,
        "root": p.haustra_depth * p.root_segmentation,
    }
    base_depth = depth_by_kind.get(tube.kind, 0.0)
    use_taenia = tube.kind in ("strand", "top") and p.taenia_count > 0

    phase = rng.uniform(0, math.tau)
    twist = rng.uniform(-0.5, 0.5)
    noise_off = _rand_offset(rng, 900.0)
    nscale = 3.0 / max(p.strand_radius, 1e-3)

    def section(center, t, nrm, r, s_phase, s_len, depth, taenia):
        b = t.cross(nrm)
        ring = []
        # 0..1 の周期関数: くびれ部で 0, 膨らみで 1
        seg = abs(math.sin(s_phase)) ** 0.6
        r_seg = r * (1.0 - depth * (1.0 - seg))
        for k in range(res):
            th = math.tau * k / res + twist * s_len
            c, s = math.cos(th), math.sin(th)
            rr = r_seg
            if taenia:
                groove = max(0.0, math.cos(p.taenia_count * th)) ** 12
                rr *= 1.0 - p.taenia_depth * groove * seg
            dirv = nrm * c + b * s
            if p.surface_noise > 0:
                q = center + dirv * rr
                rr *= 1.0 + p.surface_noise * noise.noise(q * nscale + noise_off)
            ring.append(center + dirv * rr)
        return ring

    rings = []
    s_len = 0.0
    s_phase = phase
    for i, (c, r) in enumerate(zip(pts, radii)):
        if i > 0:
            ds = (pts[i] - pts[i - 1]).length
            s_len += ds
            s_phase += math.pi * ds / max(radii[i] * p.haustra_period, 1e-5)
        if i < root_len:
            # 束から出た根の部分は根のくびれ設定に従う
            depth = p.haustra_depth * p.root_segmentation
            taenia = False
        else:
            depth = base_depth
            taenia = use_taenia
        rings.append(section(c, tangents[i], normals[i], r, s_phase, s_len, depth, taenia))

    # 両端を半球でふさぐ
    cap_steps = max(2, res // 4)
    t_end, n_end, r_end = tangents[-1], normals[-1], radii[-1]
    end_rings = []
    for k in range(1, cap_steps):
        a = (k / cap_steps) * (math.pi / 2)
        center = pts[-1] + t_end * (r_end * math.sin(a))
        end_rings.append(section(center, t_end, n_end, r_end * math.cos(a), s_phase, s_len, 0.0, False))
    tip_end = pts[-1] + t_end * r_end

    t0, n0, r0 = tangents[0], normals[0], radii[0]
    start_rings = []
    for k in range(cap_steps - 1, 0, -1):
        a = (k / cap_steps) * (math.pi / 2)
        center = pts[0] - t0 * (r0 * math.sin(a))
        start_rings.append(section(center, t0, n0, r0 * math.cos(a), phase, 0.0, 0.0, False))
    tip_start = pts[0] - t0 * r0

    return start_rings + rings + end_rings, tip_start, tip_end


def build_mesh_data(tubes, p):
    rng = random.Random(p.seed * 7919 + 13)
    verts, faces = [], []
    res = p.ring_resolution
    for tube in tubes:
        rings, tip_s, tip_e = tube_to_rings(tube, p, rng)
        if not rings:
            continue
        base = len(verts)
        for ring in rings:
            verts.extend(ring)
        nr = len(rings)
        for j in range(nr - 1):
            a0 = base + j * res
            b0 = base + (j + 1) * res
            for k in range(res):
                k1 = (k + 1) % res
                faces.append((a0 + k, a0 + k1, b0 + k1, b0 + k))
        si = len(verts)
        verts.append(tip_s)
        for k in range(res):
            k1 = (k + 1) % res
            faces.append((si, base + k1, base + k))
        ei = len(verts)
        verts.append(tip_e)
        last = base + (nr - 1) * res
        for k in range(res):
            k1 = (k + 1) % res
            faces.append((last + k, last + k1, ei))
    return verts, faces


# ---------------------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------------------

def _set_input(node, names, value):
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                continue
            return sock
    return None


def _mix_color_node(nodes):
    """Blender のバージョン差を吸収した RGB ミックスノード (fac, a, b, out) を返す."""
    try:
        node = nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        ins = {s.identifier: s for s in node.inputs}
        outs = {s.identifier: s for s in node.outputs}
        return node, ins["Factor_Float"], ins["A_Color"], ins["B_Color"], outs["Result_Color"]
    except (RuntimeError, KeyError):
        node = nodes.new("ShaderNodeMixRGB")
        return node, node.inputs["Fac"], node.inputs["Color1"], node.inputs["Color2"], node.outputs["Color"]


def make_flesh_material(name, color_a, color_b, vein_color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color_a, 1.0)
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (600, 0)
    links.new(bsdf.outputs[0], out.inputs["Surface"])

    coord = nodes.new("ShaderNodeTexCoord")
    coord.location = (-1000, 0)

    # 肉の色むら
    n1 = nodes.new("ShaderNodeTexNoise")
    n1.location = (-750, 250)
    _set_input(n1, ["Scale"], 4.0)
    _set_input(n1, ["Detail"], 8.0)
    _set_input(n1, ["Roughness"], 0.6)
    links.new(coord.outputs["Object"], n1.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-500, 250)
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (*color_a, 1.0)
    ramp.color_ramp.elements[1].position = 0.7
    ramp.color_ramp.elements[1].color = (*color_b, 1.0)
    links.new(n1.outputs["Fac"], ramp.inputs["Fac"])

    # 血管 (ボロノイの境界線をノイズで歪ませる)
    warp = nodes.new("ShaderNodeTexNoise")
    warp.location = (-1000, -300)
    _set_input(warp, ["Scale"], 3.0)
    _set_input(warp, ["Detail"], 4.0)
    mixv, fac_v, a_v, b_v, out_v = _mix_color_node(nodes)
    mixv.location = (-750, -300)
    fac_v.default_value = 0.15
    links.new(coord.outputs["Object"], a_v)
    links.new(warp.outputs["Color"], b_v)
    vor = nodes.new("ShaderNodeTexVoronoi")
    vor.location = (-500, -300)
    vor.feature = "DISTANCE_TO_EDGE"
    _set_input(vor, ["Scale"], 7.0)
    links.new(out_v, vor.inputs["Vector"])
    vramp = nodes.new("ShaderNodeValToRGB")
    vramp.location = (-250, -300)
    vramp.color_ramp.elements[0].position = 0.0
    vramp.color_ramp.elements[0].color = (1, 1, 1, 1)
    vramp.color_ramp.elements[1].position = 0.04
    vramp.color_ramp.elements[1].color = (0, 0, 0, 1)
    links.new(vor.outputs["Distance"], vramp.inputs["Fac"])

    mixc, fac_c, a_c, b_c, out_c = _mix_color_node(nodes)
    mixc.location = (150, 150)
    links.new(vramp.outputs["Color"], fac_c)
    links.new(ramp.outputs["Color"], a_c)
    b_c.default_value = (*vein_color, 1.0)
    links.new(out_c, bsdf.inputs["Base Color"])

    # ぬめり感のある凹凸
    n2 = nodes.new("ShaderNodeTexNoise")
    n2.location = (-250, -650)
    _set_input(n2, ["Scale"], 35.0)
    _set_input(n2, ["Detail"], 6.0)
    links.new(coord.outputs["Object"], n2.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.location = (300, -400)
    _set_input(bump, ["Strength"], 0.25)
    links.new(n2.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    _set_input(bsdf, ["Roughness"], 0.32)
    _set_input(bsdf, ["Subsurface Weight", "Subsurface"], 0.35)
    _set_input(bsdf, ["Subsurface Radius"], (1.0, 0.25, 0.15))
    _set_input(bsdf, ["Subsurface Color"], (*color_b, 1.0))
    _set_input(bsdf, ["Coat Weight", "Clearcoat"], 0.6)
    _set_input(bsdf, ["Coat Roughness", "Clearcoat Roughness"], 0.08)
    return mat


# ---------------------------------------------------------------------------
# 生成本体
# ---------------------------------------------------------------------------

def build_plant_mesh(p, name="VisceralPlant"):
    tubes = VisceralGrower(p).build()
    verts, faces = build_mesh_data(tubes, p)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.validate()
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    return mesh, tubes, verts


def setup_object(obj, p, tubes, verts):
    mesh = obj.data
    if p.use_material:
        mat = make_flesh_material("VisceralFlesh", tuple(p.color_a), tuple(p.color_b), tuple(p.vein_color))
        mesh.materials.clear()
        mesh.materials.append(mat)

    obj.modifiers.clear()
    if p.fuse and verts:
        min_r = max(min(min(t.radii) for t in tubes if t.radii), p.strand_radius * 0.35)
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        zs = [v.z for v in verts]
        dims = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-3)
        voxel = max(min_r * p.fuse_detail, dims / 350.0)
        rm = obj.modifiers.new("Fuse", "REMESH")
        rm.mode = "VOXEL"
        rm.voxel_size = voxel
        rm.adaptivity = 0.0
        rm.use_smooth_shade = True
    if p.smooth_iterations > 0:
        sm = obj.modifiers.new("Smooth", "SMOOTH")
        sm.factor = 0.6
        sm.iterations = p.smooth_iterations
    if p.subdivision > 0:
        sd = obj.modifiers.new("Subdivision", "SUBSURF")
        sd.levels = p.subdivision
        sd.render_levels = p.subdivision


def regenerate(obj, p):
    """既存の内臓植物オブジェクトのメッシュを作り直す."""
    mesh, tubes, verts = build_plant_mesh(p, obj.data.name if obj.data else "VisceralPlant")
    old = obj.data
    obj.data = mesh
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    setup_object(obj, p, tubes, verts)


def create_plant(context, p):
    mesh, tubes, verts = build_plant_mesh(p)
    obj = bpy.data.objects.new("VisceralPlant", mesh)
    obj[TAG] = True
    context.collection.objects.link(obj)
    obj.location = context.scene.cursor.location.copy()
    setup_object(obj, p, tubes, verts)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def _active_plant(context):
    obj = context.active_object
    if obj is not None and obj.type == "MESH" and obj.get(TAG):
        return obj
    return None


def _on_update(self, context):
    if not self.auto_update:
        return
    obj = _active_plant(context)
    if obj is not None:
        regenerate(obj, self)


# ---------------------------------------------------------------------------
# 設定 (サイドバーに常時表示)
# ---------------------------------------------------------------------------

def _F(name, default, mn, mx, **kw):
    return FloatProperty(name=name, default=default, min=mn, max=mx, update=_on_update, **kw)


def _I(name, default, mn, mx, **kw):
    return IntProperty(name=name, default=default, min=mn, max=mx, update=_on_update, **kw)


def _B(name, default, **kw):
    return BoolProperty(name=name, default=default, update=_on_update, **kw)


def _C(name, default):
    return FloatVectorProperty(name=name, subtype="COLOR", size=3, min=0, max=1,
                               default=default, update=_on_update)


class VisceralPlantSettings(bpy.types.PropertyGroup):
    auto_update: BoolProperty(name="自動更新", default=True,
                              description="値を変えるたびに選択中の内臓植物を作り直す")
    seed: _I("シード", 1, 0, 100000)

    # 全体・幹 (腸の束)
    height: _F("高さ", 3.0, 0.3, 50, unit="LENGTH")
    strand_count: _I("腸の本数", 5, 1, 16, description="幹を構成する、ねじれ合う管の本数")
    strand_radius: _F("腸の太さ", 0.07, 0.005, 2, unit="LENGTH")
    bundle_radius: _F("束の半径", 0.16, 0.0, 5, unit="LENGTH")
    bundle_bottom: _F("束の下端", 0.12, 0.0, 0.6, subtype="FACTOR",
                      description="高さに対して、管が根へほどける位置")
    bundle_top: _F("束の上端", 0.68, 0.2, 0.98, subtype="FACTOR",
                   description="高さに対して、管が一本の茎にまとまる位置")
    twist: _F("ねじれ (回転数)", 1.3, -6, 6)
    irregularity: _F("不規則さ", 0.45, 0, 2)
    loop_count: _I("ループの数", 3, 0, 12, description="束から外へはみ出す腸のループ")
    loop_size: _F("ループの大きさ", 0.22, 0, 3, unit="LENGTH")
    strand_bulges: _I("管のこぶ", 2, 0, 8, description="管の途中のふくらみ")
    bulge_size: _F("こぶの大きさ", 0.6, 0, 3)

    # 胃袋
    sac_enable: _B("胃袋", True)
    sac_height: _F("胃袋の高さ", 0.4, 0.05, 0.95, subtype="FACTOR")
    sac_size: _F("胃袋の大きさ", 0.24, 0.02, 5, unit="LENGTH")
    sac_elongation: _F("胃袋の縦長さ", 1.3, 0.5, 4)

    # 上部
    top_radius: _F("上部の茎の太さ", 0.04, 0.003, 2, unit="LENGTH")
    top_wiggle: _F("上部の揺らぎ", 0.12, 0, 2)
    top_bulb: _B("先端のふくらみ", True)
    top_bulb_size: _F("ふくらみの大きさ", 1.9, 0.5, 8)
    shoot_count: _I("小枝の数", 3, 0, 20)
    shoot_length: _F("小枝の長さ", 0.3, 0.01, 10, unit="LENGTH")

    # 根
    root_flare: _F("根元の広がり", 1.8, 0, 6)
    root_reach: _F("根の長さ", 1.6, 0.05, 30, unit="LENGTH")
    root_gravity: _F("下向きの力", 0.18, 0, 2)
    root_wander: _F("根の蛇行", 0.35, 0, 3)
    root_curl: _F("先端の巻き", 0.7, 0, 3)
    root_taper: _F("根の先細り", 0.3, 0.05, 1)
    extra_roots: _I("追加の根", 3, 0, 20)
    self_avoid: _F("根の自己回避", 0.5, 0, 3)

    # 腸の表面
    haustra_depth: _F("くびれの深さ", 0.14, 0, 0.9, description="結腸のハウストラのような環状のくびれ")
    haustra_period: _F("くびれの間隔", 2.4, 0.3, 20, description="半径に対するくびれ間隔の倍率")
    taenia_count: _I("縦溝の数", 0, 0, 12, description="結腸ヒモのような縦方向の溝")
    taenia_depth: _F("縦溝の深さ", 0.15, 0, 0.8)
    surface_noise: _F("表面のゆらぎ", 0.04, 0, 0.5)
    root_segmentation: _F("根のくびれ", 0.4, 0, 2)

    # メッシュ
    ring_resolution: _I("断面の分割数", 12, 6, 64)
    fuse: _B("融合 (リメッシュ)", True, description="ボクセルリメッシュで管同士をなめらかにつなげる")
    fuse_detail: _F("融合の細かさ", 0.45, 0.1, 3, description="小さいほど細かい (重い)")
    smooth_iterations: _I("スムーズ回数", 3, 0, 30)
    subdivision: _I("サブディビジョン", 0, 0, 3)

    # マテリアル
    use_material: _B("肉質マテリアル", True)
    color_a: _C("色 1", (0.85, 0.42, 0.42))
    color_b: _C("色 2", (0.55, 0.08, 0.12))
    vein_color: _C("血管の色", (0.25, 0.02, 0.12))


# ---------------------------------------------------------------------------
# オペレーター
# ---------------------------------------------------------------------------

class MESH_OT_visceral_plant_add(bpy.types.Operator):
    """サイドバーの設定で内臓植物を新規追加"""

    bl_idname = "mesh.visceral_plant_add"
    bl_label = "内臓植物を追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        create_plant(context, context.scene.visceral_plant)
        return {"FINISHED"}


class OBJECT_OT_visceral_plant_regenerate(bpy.types.Operator):
    """選択中の内臓植物を現在の設定で作り直す"""

    bl_idname = "object.visceral_plant_regenerate"
    bl_label = "作り直す"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_plant(context) is not None

    def execute(self, context):
        regenerate(_active_plant(context), context.scene.visceral_plant)
        return {"FINISHED"}


class OBJECT_OT_visceral_plant_random_seed(bpy.types.Operator):
    """シードをランダムに変える"""

    bl_idname = "object.visceral_plant_random_seed"
    bl_label = "ランダム"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.visceral_plant.seed = random.randint(0, 99999)
        return {"FINISHED"}


class OBJECT_OT_visceral_plant_apply(bpy.types.Operator):
    """モディファイアを適用して通常のメッシュにする (以後は再生成されない)"""

    bl_idname = "object.visceral_plant_apply"
    bl_label = "確定 (モディファイア適用)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_plant(context) is not None

    def execute(self, context):
        obj = _active_plant(context)
        depsgraph = context.evaluated_depsgraph_get()
        new_mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
        old = obj.data
        obj.modifiers.clear()
        obj.data = new_mesh
        if old.users == 0:
            bpy.data.meshes.remove(old)
        del obj[TAG]
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class _VisceralPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Visceral"


class VIEW3D_PT_visceral_plant(_VisceralPanel, bpy.types.Panel):
    bl_label = "Visceral Plant"

    def draw(self, context):
        s = context.scene.visceral_plant
        layout = self.layout
        col = layout.column(align=True)
        col.operator(MESH_OT_visceral_plant_add.bl_idname, icon="ADD")
        row = col.row(align=True)
        row.operator(OBJECT_OT_visceral_plant_regenerate.bl_idname, icon="FILE_REFRESH")
        row.operator(OBJECT_OT_visceral_plant_apply.bl_idname, icon="CHECKMARK", text="確定")
        layout.prop(s, "auto_update")
        row = layout.row(align=True)
        row.prop(s, "seed")
        row.operator(OBJECT_OT_visceral_plant_random_seed.bl_idname, text="", icon="FILE_REFRESH")
        if _active_plant(context) is None:
            layout.label(text="内臓植物を選択すると値の変更が反映されます", icon="INFO")


def _sub_panel(idname, label, props, closed=True):
    def draw(self, context):
        s = context.scene.visceral_plant
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        for prop in props:
            col.prop(s, prop)

    return type(idname, (_VisceralPanel, bpy.types.Panel), {
        "bl_label": label,
        "bl_parent_id": "VIEW3D_PT_visceral_plant",
        "bl_options": {"DEFAULT_CLOSED"} if closed else set(),
        "draw": draw,
    })


_sub_panels = (
    _sub_panel("VIEW3D_PT_visceral_trunk", "幹 (腸の束)",
               ["height", "strand_count", "strand_radius", "bundle_radius", "bundle_bottom",
                "bundle_top", "twist", "irregularity", "loop_count", "loop_size",
                "strand_bulges", "bulge_size"], closed=False),
    _sub_panel("VIEW3D_PT_visceral_sac", "胃袋",
               ["sac_enable", "sac_height", "sac_size", "sac_elongation"]),
    _sub_panel("VIEW3D_PT_visceral_top", "上部の茎",
               ["top_radius", "top_wiggle", "top_bulb", "top_bulb_size",
                "shoot_count", "shoot_length"]),
    _sub_panel("VIEW3D_PT_visceral_root", "根",
               ["root_flare", "root_reach", "root_gravity", "root_wander", "root_curl",
                "root_taper", "extra_roots", "self_avoid"]),
    _sub_panel("VIEW3D_PT_visceral_surface", "腸の表面",
               ["haustra_depth", "haustra_period", "taenia_count", "taenia_depth",
                "surface_noise", "root_segmentation"]),
    _sub_panel("VIEW3D_PT_visceral_mesh", "メッシュ",
               ["ring_resolution", "fuse", "fuse_detail", "smooth_iterations", "subdivision"]),
    _sub_panel("VIEW3D_PT_visceral_material", "マテリアル",
               ["use_material", "color_a", "color_b", "vein_color"]),
)


def _menu_func(self, context):
    self.layout.operator(MESH_OT_visceral_plant_add.bl_idname, text="Visceral Plant (内臓植物)",
                         icon="OUTLINER_OB_MESH")


_classes = (
    VisceralPlantSettings,
    MESH_OT_visceral_plant_add,
    OBJECT_OT_visceral_plant_regenerate,
    OBJECT_OT_visceral_plant_random_seed,
    OBJECT_OT_visceral_plant_apply,
    VIEW3D_PT_visceral_plant,
    *_sub_panels,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.visceral_plant = PointerProperty(type=VisceralPlantSettings)
    bpy.types.VIEW3D_MT_mesh_add.append(_menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(_menu_func)
    del bpy.types.Scene.visceral_plant
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
