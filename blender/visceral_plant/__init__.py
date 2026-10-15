# SPDX-License-Identifier: GPL-3.0-or-later
"""Visceral Plant - 胃や腸のような茎と根を持つ植物モデルを生成する Blender アドオン.

茎は「腸」のようにくびれ (ハウストラ) と縦溝 (結腸ヒモ) を持ちながら蛇行し、
ところどころで「胃」のような袋状に膨らみ、幽門のような括約部でくびれる。
根は小腸のように折り畳まれながら下へ伸び、さらに細い根へ分岐する。
"""

bl_info = {
    "name": "Visceral Plant (内臓植物ジェネレーター)",
    "author": "A_life",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Add > Mesh > Visceral Plant / サイドバー > Visceral",
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
)
from mathutils import Matrix, Vector, noise


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
                        limit = (r + qr) * 1.15
                        if 1e-6 < dist < limit:
                            push += d / dist * ((limit - dist) / limit)
        return push


class Tube:
    """中心線 (points) と各点の半径 (radii) からなるチューブ."""

    def __init__(self, kind):
        self.kind = kind  # "stem", "branch", "root", "bulb"
        self.points = []
        self.radii = []
        self.attached = False


# ---------------------------------------------------------------------------
# 成長シミュレーション
# ---------------------------------------------------------------------------

class VisceralGrower:
    def __init__(self, p):
        self.p = p
        self.rng = random.Random(p.seed)
        self.tubes = []
        self.hash = SpatialHash(p.stem_radius * 2.5)
        self._owner = 0

    # --- 1本のチューブを成長させる -------------------------------------
    def grow(self, kind, start, direction, length, r0, r1, *,
             wander, coil, fold, tropism, tropism_dir, sacs=()):
        p = self.p
        rng = self.rng
        tube = Tube(kind)
        owner = self._owner
        self._owner += 1

        step = max(min(r0, r1) * 0.55, p.stem_radius * 0.12)
        n = max(4, int(length / step))
        pos = start.copy()
        d = direction.normalized()
        axis = _any_perpendicular(d)
        axis.rotate(Matrix.Rotation(rng.uniform(0, math.tau), 3, d))
        noise_off = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))
        freq = 1.0 / max(p.stem_radius * 6.0, 1e-3)
        fold_len = max(2, int(rng.uniform(0.6, 1.4) * (1.0 / max(fold, 1e-3)) / step * r0 * 8))
        fold_counter = 0

        for i in range(n + 1):
            t = i / n
            radius = r0 + (r1 - r0) * t
            coil_gain = 1.0

            # 胃袋 (sac): 半径を膨らませ、J字状に強く曲げる
            for (s0, s1, scale) in sacs:
                if s0 <= t <= s1:
                    u = (t - s0) / (s1 - s0)
                    # 噴門側はなだらか、幽門側で急にすぼまる非対称プロファイル
                    bulge = math.sin(math.pi * (u ** 0.75)) ** 0.8
                    radius *= 1.0 + (scale - 1.0) * bulge
                    coil_gain = 3.0
                elif s1 < t <= s1 + 0.03:
                    # 幽門括約部
                    u = (t - s1) / 0.03
                    radius *= 1.0 - 0.45 * math.sin(math.pi * u)

            tube.points.append(pos.copy())
            tube.radii.append(radius)
            self.hash.insert(pos, radius, owner, i)

            if i == n:
                break

            # 蛇行 (ノイズ場)
            nv = noise.noise_vector(pos * freq + noise_off)
            d = d + nv * wander
            # 腸のような折り畳み: 軸まわりに回転、時々軸が反転して蛇行する
            d = d + axis.cross(d) * (coil * coil_gain)
            fold_counter += 1
            if fold > 0 and fold_counter >= fold_len:
                fold_counter = 0
                axis = -axis
                axis = (axis + _random_unit(rng) * 0.6).normalized()
            axis = (axis + _random_unit(rng) * 0.05).normalized()
            # 屈性 (茎は上へ、根は下へ)
            d = d + tropism_dir * tropism
            # 自己回避
            if p.self_avoid > 0:
                push = self.hash.repulsion(pos + d * step, radius, owner, i, ignore_recent=int(radius * 3 / step) + 3)
                d = d + push * p.self_avoid
            if d.length_squared < 1e-9:
                d = direction.copy()
            d.normalize()
            # 地面より下に潜りすぎない / 上に出ない
            if kind in ("stem", "branch") and pos.z < r0 and d.z < 0:
                d.z = abs(d.z)
                d.normalize()
            if kind == "root" and pos.z > -radius * 0.5 and d.z > 0:
                d.z = -abs(d.z)
                d.normalize()
            pos = pos + d * step

        self.tubes.append(tube)
        return tube

    # --- 植物全体 -----------------------------------------------------
    def build(self):
        p = self.p
        rng = self.rng
        up = Vector((0, 0, 1))

        # 茎の「胃袋」の位置をランダムに決める
        sacs = []
        if p.sac_count > 0:
            slots = sorted(rng.uniform(0.15, 0.85) for _ in range(p.sac_count))
            for s in slots:
                w = p.sac_size * p.stem_radius * rng.uniform(2.5, 3.5) / p.stem_length
                w = max(0.05, min(w, 0.4))
                if sacs and s < sacs[-1][1] + 0.05:
                    continue
                sacs.append((s, min(s + w, 0.95), p.sac_size * rng.uniform(0.8, 1.2)))

        stem = self.grow(
            "stem", Vector((0, 0, 0)), up, p.stem_length,
            p.stem_radius, p.stem_radius * p.stem_taper,
            wander=p.stem_wander, coil=p.stem_coil, fold=p.stem_fold,
            tropism=p.stem_tropism, tropism_dir=up, sacs=sacs,
        )

        # 茎からの枝
        self._branches(stem, p.branch_count, p.branch_depth, p.branch_length, up)

        # 根
        for k in range(p.root_count):
            ang = math.tau * k / max(p.root_count, 1) + rng.uniform(-0.4, 0.4)
            outward = Vector((math.cos(ang), math.sin(ang), 0))
            d = (outward * p.root_spread + Vector((0, 0, -1))).normalized()
            start = Vector((0, 0, p.stem_radius * 0.3)) + outward * p.stem_radius * 0.3
            r0 = p.stem_radius * p.root_radius * rng.uniform(0.8, 1.1)
            root = self.grow(
                "root", start, d, p.root_length * rng.uniform(0.75, 1.2),
                r0, r0 * 0.25,
                wander=p.root_wander, coil=p.root_coil, fold=p.root_fold,
                tropism=p.root_gravity, tropism_dir=Vector((0, 0, -1)),
            )
            root.attached = True
            self._sub_roots(root, p.root_sub_count, depth=1)

        # 地際の膨らみ (胃底のような球根)
        if p.base_bulb > 0:
            r = p.stem_radius * p.base_bulb
            bulb = Tube("bulb")
            n = 9
            for i in range(n + 1):
                t = i / n
                bulb.points.append(Vector((0, 0, -r * 0.4 + r * 1.4 * t)))
                bulb.radii.append(max(r * math.sin(math.pi * (0.08 + 0.84 * t)), p.stem_radius * 0.6))
            self.tubes.append(bulb)

        return self.tubes

    def _branches(self, parent, count, depth, length, up):
        p = self.p
        rng = self.rng
        if depth <= 0 or count <= 0:
            return
        n = len(parent.points)
        for _ in range(count):
            i = rng.randint(int(n * 0.25), max(int(n * 0.92), int(n * 0.25) + 1) - 1)
            base = parent.points[i]
            tangent = (parent.points[min(i + 1, n - 1)] - parent.points[max(i - 1, 0)]).normalized()
            side = _any_perpendicular(tangent)
            side.rotate(Matrix.Rotation(rng.uniform(0, math.tau), 3, tangent))
            d = (side * 1.2 + tangent * 0.5 + up * 0.4).normalized()
            r0 = parent.radii[i] * p.branch_radius
            blen = length * rng.uniform(0.7, 1.2)
            sacs = []
            if rng.random() < p.branch_sac_chance:
                s = rng.uniform(0.4, 0.7)
                sacs.append((s, s + 0.18, p.sac_size * 0.8))
            br = self.grow(
                "branch", base, d, blen, r0, r0 * p.stem_taper,
                wander=p.stem_wander * 1.2, coil=p.stem_coil * 1.2, fold=p.stem_fold,
                tropism=p.stem_tropism * 0.7, tropism_dir=up, sacs=sacs,
            )
            br.attached = True
            if p.tip_bulbs:
                self._tip_bulb(br)
            self._branches(br, max(1, count // 2), depth - 1, length * 0.6, up)
        if p.tip_bulbs and parent.kind == "stem":
            self._tip_bulb(parent)

    def _tip_bulb(self, tube):
        """枝先の果実状の袋 (胃のような球体)."""
        p = self.p
        end = tube.points[-1]
        d = (tube.points[-1] - tube.points[-2]).normalized()
        r = max(tube.radii[-1], p.stem_radius * 0.3) * p.tip_bulb_size
        bulb = Tube("bulb")
        bulb.attached = True
        n = 10
        for i in range(n + 1):
            t = i / n
            bulb.points.append(end + d * (-r * 0.3 + r * 2.0 * t))
            prof = math.sin(math.pi * (0.06 + 0.88 * t)) ** 0.7
            bulb.radii.append(max(r * prof, tube.radii[-1] * 0.8))
        self.tubes.append(bulb)

    def _sub_roots(self, parent, count, depth):
        p = self.p
        rng = self.rng
        if depth > p.root_depth or count <= 0:
            return
        n = len(parent.points)
        for _ in range(count):
            i = rng.randint(int(n * 0.2), max(int(n * 0.9), int(n * 0.2) + 1) - 1)
            base = parent.points[i]
            tangent = (parent.points[min(i + 1, n - 1)] - parent.points[max(i - 1, 0)]).normalized()
            side = _any_perpendicular(tangent)
            side.rotate(Matrix.Rotation(rng.uniform(0, math.tau), 3, tangent))
            d = (side + tangent * 0.6).normalized()
            r0 = parent.radii[i] * 0.6
            if r0 < p.stem_radius * 0.04:
                continue
            sub = self.grow(
                "root", base, d, p.root_length * 0.45 * (0.7 ** (depth - 1)) * rng.uniform(0.7, 1.2),
                r0, r0 * 0.3,
                wander=p.root_wander, coil=p.root_coil * 1.2, fold=p.root_fold,
                tropism=p.root_gravity * 0.8, tropism_dir=Vector((0, 0, -1)),
            )
            sub.attached = True
            self._sub_roots(sub, max(1, count // 2), depth + 1)


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
    """チューブを断面リング列 (頂点座標) と先端点に変換する."""
    pts, radii = tube.points, tube.radii
    if len(pts) < 2:
        return [], None, None
    tangents, normals = _transport_frames(pts)
    res = p.ring_resolution
    is_gut = tube.kind in ("stem", "branch")
    is_root = tube.kind == "root"

    # ハウストラ (腸のくびれ) の位相を弧長から計算
    phase = rng.uniform(0, math.tau)
    twist = rng.uniform(-0.5, 0.5)
    noise_off = Vector((rng.uniform(-900, 900), rng.uniform(-900, 900), rng.uniform(-900, 900)))

    def section(center, t, nrm, r, s_phase, s_len):
        b = t.cross(nrm)
        ring = []
        if is_gut:
            depth = p.haustra_depth
        elif is_root:
            depth = p.haustra_depth * p.root_segmentation
        else:
            depth = 0.0
        # 0..1 の周期関数: くびれ部で 0, 膨らみで 1
        seg = abs(math.sin(s_phase)) ** 0.6
        r_seg = r * (1.0 - depth * (1.0 - seg))
        for k in range(res):
            th = math.tau * k / res + twist * s_len
            c, s = math.cos(th), math.sin(th)
            rr = r_seg
            if is_gut and p.taenia_count > 0:
                groove = max(0.0, math.cos(p.taenia_count * th)) ** 12
                rr *= 1.0 - p.taenia_depth * groove * seg
            dirv = nrm * c + b * s
            if p.surface_noise > 0:
                q = center + dirv * rr
                rr *= 1.0 + p.surface_noise * noise.noise(q * (6.0 / p.stem_radius) + noise_off)
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
        rings.append(section(c, tangents[i], normals[i], r, s_phase, s_len))

    # 先端を半球でふさぐ
    cap_steps = max(2, res // 4)
    t_end, n_end, r_end = tangents[-1], normals[-1], radii[-1]
    end_rings = []
    for k in range(1, cap_steps):
        a = (k / cap_steps) * (math.pi / 2)
        center = pts[-1] + t_end * (r_end * math.sin(a))
        end_rings.append(section(center, t_end, n_end, r_end * math.cos(a), s_phase, s_len))
    tip_end = pts[-1] + t_end * r_end

    t0, n0, r0 = tangents[0], normals[0], radii[0]
    start_rings = []
    for k in range(cap_steps - 1, 0, -1):
        a = (k / cap_steps) * (math.pi / 2)
        center = pts[0] - t0 * (r0 * math.sin(a))
        start_rings.append(section(center, t0, n0, r0 * math.cos(a), phase, 0.0))
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
        # 先端のファン
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
# オペレーター
# ---------------------------------------------------------------------------

class MESH_OT_visceral_plant_add(bpy.types.Operator):
    """胃や腸のような茎・根を持つ植物を追加"""

    bl_idname = "mesh.visceral_plant_add"
    bl_label = "Visceral Plant"
    bl_options = {"REGISTER", "UNDO"}

    seed: IntProperty(name="シード", default=1, min=0)

    # 茎
    stem_length: FloatProperty(name="茎の長さ", default=3.0, min=0.2, max=50, unit="LENGTH")
    stem_radius: FloatProperty(name="茎の太さ", default=0.16, min=0.01, max=5, unit="LENGTH")
    stem_taper: FloatProperty(name="先細り", default=0.45, min=0.05, max=1.5)
    stem_wander: FloatProperty(name="蛇行", default=0.35, min=0, max=3)
    stem_coil: FloatProperty(name="巻き (腸の折り畳み)", default=0.18, min=0, max=1.5)
    stem_fold: FloatProperty(name="折り返し頻度", default=0.6, min=0, max=5,
                             description="巻き方向が反転する頻度。高いほど腸のように細かく蛇行する")
    stem_tropism: FloatProperty(name="上向きの力", default=0.25, min=0, max=2)

    # 腸の表面構造
    haustra_depth: FloatProperty(name="くびれの深さ", default=0.28, min=0, max=0.9,
                                 description="結腸のハウストラのような環状のくびれ")
    haustra_period: FloatProperty(name="くびれの間隔", default=2.2, min=0.3, max=20,
                                  description="半径に対するくびれ間隔の倍率")
    taenia_count: IntProperty(name="縦溝の数", default=3, min=0, max=12,
                              description="結腸ヒモのような縦方向の溝")
    taenia_depth: FloatProperty(name="縦溝の深さ", default=0.18, min=0, max=0.8)
    surface_noise: FloatProperty(name="表面のゆらぎ", default=0.06, min=0, max=0.5)

    # 胃袋
    sac_count: IntProperty(name="胃袋の数 (茎)", default=2, min=0, max=10)
    sac_size: FloatProperty(name="胃袋の大きさ", default=2.6, min=1, max=8)
    branch_sac_chance: FloatProperty(name="枝に胃袋が付く確率", default=0.35, min=0, max=1, subtype="FACTOR")
    base_bulb: FloatProperty(name="地際の膨らみ", default=1.8, min=0, max=6)
    tip_bulbs: BoolProperty(name="枝先の袋状の実", default=True)
    tip_bulb_size: FloatProperty(name="実の大きさ", default=2.2, min=0.5, max=6)

    # 枝
    branch_count: IntProperty(name="枝の数", default=4, min=0, max=30)
    branch_depth: IntProperty(name="枝の階層", default=2, min=0, max=4)
    branch_length: FloatProperty(name="枝の長さ", default=1.5, min=0.1, max=30, unit="LENGTH")
    branch_radius: FloatProperty(name="枝の太さ比", default=0.6, min=0.1, max=1.2)

    # 根
    root_count: IntProperty(name="根の数", default=5, min=0, max=30)
    root_length: FloatProperty(name="根の長さ", default=2.4, min=0.1, max=30, unit="LENGTH")
    root_radius: FloatProperty(name="根の太さ比", default=0.5, min=0.05, max=1.5)
    root_spread: FloatProperty(name="根の広がり", default=1.4, min=0, max=5)
    root_wander: FloatProperty(name="根の蛇行", default=0.5, min=0, max=3)
    root_coil: FloatProperty(name="根の巻き (小腸)", default=0.5, min=0, max=1.5)
    root_fold: FloatProperty(name="根の折り返し頻度", default=1.2, min=0, max=5)
    root_gravity: FloatProperty(name="下向きの力", default=0.08, min=0, max=2)
    root_sub_count: IntProperty(name="側根の数", default=2, min=0, max=10)
    root_depth: IntProperty(name="側根の階層", default=1, min=0, max=3)
    root_segmentation: FloatProperty(name="根のくびれ", default=0.5, min=0, max=2)

    # メッシュ
    ring_resolution: IntProperty(name="断面の分割数", default=16, min=6, max=64)
    self_avoid: FloatProperty(name="自己回避", default=0.6, min=0, max=3,
                              description="チューブ同士がめり込まないよう押し返す強さ")
    fuse: BoolProperty(name="融合 (リメッシュ)", default=True,
                       description="ボクセルリメッシュで分岐部をなめらかにつなげる")
    fuse_detail: FloatProperty(name="融合の細かさ", default=0.5, min=0.1, max=3,
                               description="小さいほど細かい (重い)")
    smooth_iterations: IntProperty(name="スムーズ回数", default=4, min=0, max=30)
    subdivision: IntProperty(name="サブディビジョン", default=0, min=0, max=3)
    apply_modifiers: BoolProperty(name="モディファイアを適用", default=False)

    # マテリアル
    use_material: BoolProperty(name="肉質マテリアル", default=True)
    color_a: FloatVectorProperty(name="色 1", subtype="COLOR", size=3, min=0, max=1,
                                 default=(0.85, 0.42, 0.42))
    color_b: FloatVectorProperty(name="色 2", subtype="COLOR", size=3, min=0, max=1,
                                 default=(0.55, 0.08, 0.12))
    vein_color: FloatVectorProperty(name="血管の色", subtype="COLOR", size=3, min=0, max=1,
                                    default=(0.25, 0.02, 0.12))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "seed")

        def box(title, props):
            b = layout.box()
            b.label(text=title)
            col = b.column(align=True)
            for prop in props:
                col.prop(self, prop)

        box("茎", ["stem_length", "stem_radius", "stem_taper", "stem_wander",
                   "stem_coil", "stem_fold", "stem_tropism"])
        box("腸の表面", ["haustra_depth", "haustra_period", "taenia_count",
                       "taenia_depth", "surface_noise"])
        box("胃袋・実", ["sac_count", "sac_size", "branch_sac_chance", "base_bulb",
                       "tip_bulbs", "tip_bulb_size"])
        box("枝", ["branch_count", "branch_depth", "branch_length", "branch_radius"])
        box("根", ["root_count", "root_length", "root_radius", "root_spread",
                  "root_wander", "root_coil", "root_fold", "root_gravity",
                  "root_sub_count", "root_depth", "root_segmentation"])
        box("メッシュ", ["ring_resolution", "self_avoid", "fuse", "fuse_detail",
                       "smooth_iterations", "subdivision", "apply_modifiers"])
        box("マテリアル", ["use_material", "color_a", "color_b", "vein_color"])

    def execute(self, context):
        grower = VisceralGrower(self)
        tubes = grower.build()
        verts, faces = build_mesh_data(tubes, self)
        if not verts:
            self.report({"WARNING"}, "メッシュを生成できませんでした")
            return {"CANCELLED"}

        mesh = bpy.data.meshes.new("VisceralPlant")
        mesh.from_pydata([tuple(v) for v in verts], [], faces)
        mesh.validate()
        mesh.update()
        for poly in mesh.polygons:
            poly.use_smooth = True

        obj = bpy.data.objects.new("VisceralPlant", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location.copy()

        if self.use_material:
            mat = make_flesh_material("VisceralFlesh_%d" % self.seed,
                                      tuple(self.color_a), tuple(self.color_b), tuple(self.vein_color))
            mesh.materials.append(mat)

        if self.fuse:
            min_r = min(min(t.radii) for t in tubes if t.radii)
            xs = [v.x for v in verts]
            ys = [v.y for v in verts]
            zs = [v.z for v in verts]
            dims = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-3)
            voxel = max(min_r * self.fuse_detail, dims / 400.0)
            rm = obj.modifiers.new("Fuse", "REMESH")
            rm.mode = "VOXEL"
            rm.voxel_size = voxel
            rm.adaptivity = 0.0
            rm.use_smooth_shade = True
        if self.smooth_iterations > 0:
            sm = obj.modifiers.new("Smooth", "SMOOTH")
            sm.factor = 0.6
            sm.iterations = self.smooth_iterations
        if self.subdivision > 0:
            sd = obj.modifiers.new("Subdivision", "SUBSURF")
            sd.levels = self.subdivision
            sd.render_levels = self.subdivision

        if self.apply_modifiers and obj.modifiers:
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = obj.evaluated_get(depsgraph)
            new_mesh = bpy.data.meshes.new_from_object(evaluated)
            obj.modifiers.clear()
            old = obj.data
            obj.data = new_mesh
            new_mesh.name = "VisceralPlant"
            bpy.data.meshes.remove(old)

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class VIEW3D_PT_visceral_plant(bpy.types.Panel):
    bl_label = "Visceral Plant"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Visceral"

    def draw(self, context):
        col = self.layout.column()
        col.label(text="胃や腸のような植物を生成")
        col.operator(MESH_OT_visceral_plant_add.bl_idname, text="内臓植物を追加", icon="OUTLINER_OB_MESH")
        col.label(text="生成後、左下の調整パネルで", icon="INFO")
        col.label(text="パラメーターを変更できます")


def _menu_func(self, context):
    self.layout.operator(MESH_OT_visceral_plant_add.bl_idname, text="Visceral Plant (内臓植物)",
                         icon="OUTLINER_OB_MESH")


_classes = (MESH_OT_visceral_plant_add, VIEW3D_PT_visceral_plant)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(_menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(_menu_func)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
