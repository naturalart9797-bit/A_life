# SPDX-License-Identifier: GPL-3.0-or-later
"""
Water Flower Petals — 水をためる花の花びらジェネレーター

花びらを何層も重ね、うねり (weave)・ゆらぎ (sway)・ねじれ (twist) で
互いに上下に絡ませながら、中央に風呂のような水たまり (basin) を作る
Blender アドオン。

機能
  * Add > Mesh > 水をためる花 (Water Flower)
      花びら・受け皿 (receptacle)・茎を 1 つのメッシュとして生成。
      厚み (Solidify) と、重なった花びらを一体化して水密にする
      ボクセル Remesh をモディファイアとして付ける。
      F9 / 左下の「最後の操作を調整」パネルでパラメータを調整可能。
  * 水をためる (Fill Water)
      花をボクセル化して「どの高さまで水が漏れずにたまるか」を計算し、
      水のオブジェクトを作って水位と容量をレポートする。
  * 流体シミュレーション準備 (Fluid Setup)
      Mantaflow のドメイン・注水口・花のコリジョンを自動設定する。

インストール: 編集 > プリファレンス > アドオン > ディスクからインストール で
このファイル (water_flower_petals.py) を選択。
"""

bl_info = {
    "name": "Water Flower Petals (水をためる花)",
    "author": "A_life",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Add > Mesh > 水をためる花 / サイドバー > WaterFlower",
    "description": "絡み合う花びらで水をためる花を生成し、たまる水位を計算する",
    "category": "Add Mesh",
}

import math
import random
from collections import deque

import bpy
import bmesh
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
)
from mathutils import Vector
from mathutils.bvhtree import BVHTree

GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def petal_width(u, width, widest, pointiness, base_width):
    """花びらの輪郭 (u: 0=付け根, 1=先端) における半幅。"""
    alpha = math.log(0.5) / math.log(min(max(widest, 0.05), 0.95))
    s = max(math.sin(math.pi * (u ** alpha)), 0.0)
    w = width * (s ** pointiness) + base_width * (1.0 - u) ** 2
    return max(w, width * 0.02)


def build_petal(p, azimuth, length, tilt, base_r, base_z, weave_phase, sway_phase,
                verts, faces, uvs):
    """1 枚の花びらを verts / faces / uvs に追加する。

    花びらは (r, z) 平面で中心線 (spine) を積分し、断面方向に幅・カップ・
    フリル・うねり・ねじれを与えて作る。カップは花の内側 (上向き) に
    凹むので、花びら 1 枚ずつも小さな器になる。
    """
    nu, nv = p["res_u"], p["res_v"]
    cos_a, sin_a = math.cos(azimuth), math.sin(azimuth)

    # spine の積分
    spine = [(base_r, base_z)]
    angles = []
    ds = length / nu
    for i in range(nu + 1):
        u = i / nu
        angles.append(tilt + p["curl"] * (u ** 1.5))
    for i in range(nu):
        a = 0.5 * (angles[i] + angles[i + 1])
        r, z = spine[-1]
        spine.append((r + math.cos(a) * ds, z + math.sin(a) * ds))

    start = len(verts)
    for i in range(nu + 1):
        u = i / nu
        a = angles[i]
        sr, sz = spine[i]
        # 法線 (内側・上向き) と横方向
        nr, nz = -math.sin(a), math.cos(a)
        w = petal_width(u, p["width"] * length, p["widest"], p["pointiness"],
                        p["base_width"] * length)
        tw = p["twist"] * u
        ct, st = math.cos(tw), math.sin(tw)
        weave = p["weave"] * length * u * math.sin(2 * math.pi * p["weave_freq"] * u + weave_phase)
        sway = p["sway"] * length * u * math.sin(2 * math.pi * p["sway_freq"] * u + sway_phase)
        for j in range(nv + 1):
            v = -1.0 + 2.0 * j / nv
            b = v * w + sway
            n = (p["cup"] * w * v * v
                 + p["ruffle"] * w * u * abs(v) ** 1.5
                 * math.sin(p["ruffle_freq"] * math.pi * v + 3.0 * u)
                 + weave)
            b2 = b * ct - n * st
            n2 = b * st + n * ct
            r = sr + n2 * nr
            z = sz + n2 * nz
            verts.append((r * cos_a - b2 * sin_a, r * sin_a + b2 * cos_a, z))
            uvs.append((j / nv, u))

    row = nv + 1
    for i in range(nu):
        for j in range(nv):
            a = start + i * row + j
            faces.append((a, a + 1, a + row + 1, a + row))


def build_surface_of_revolution(profile, segments, verts, faces, uvs, close_bottom=True):
    """(r, z) のプロファイル列を回転させた面を追加する。"""
    start = len(verts)
    n = len(profile)
    for k, (r, z) in enumerate(profile):
        for s in range(segments):
            t = 2 * math.pi * s / segments
            verts.append((r * math.cos(t), r * math.sin(t), z))
            uvs.append((s / segments, k / max(n - 1, 1)))
    for k in range(n - 1):
        for s in range(segments):
            a = start + k * segments + s
            b = start + k * segments + (s + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    if close_bottom:
        c = len(verts)
        verts.append((0.0, 0.0, profile[0][1]))
        uvs.append((0.5, 0.0))
        for s in range(segments):
            a = start + s
            b = start + (s + 1) % segments
            faces.append((c, b, a))


def build_flower(p):
    verts, faces, uvs = [], [], []
    rng = random.Random(p["seed"])
    layers = p["layers"]
    L = p["length"]
    base_r = p["base_radius"] * L

    for k in range(layers):
        t = k / (layers - 1) if layers > 1 else 0.0
        count = p["petals"] + k * p["petals_increment"]
        tilt_deg = p["tilt_inner"] + (p["tilt_outer"] - p["tilt_inner"]) * t
        length = L * (1.0 + p["length_growth"] * k)
        layer_offset = k * GOLDEN_ANGLE if p["golden"] else (k % 2) * math.pi / count
        for i in range(count):
            j = p["jitter"]
            az = layer_offset + 2 * math.pi * i / count
            az += j * rng.uniform(-0.5, 0.5) * (2 * math.pi / count)
            tilt = math.radians(tilt_deg + j * rng.uniform(-12.0, 12.0))
            ln = length * (1.0 + j * rng.uniform(-0.15, 0.15))
            weave_phase = math.pi * (i % 2) + k * math.pi * 0.5 + j * rng.uniform(-0.6, 0.6)
            sway_phase = math.pi * ((i + k) % 2) + j * rng.uniform(-0.6, 0.6)
            br = base_r * (1.0 + 0.35 * k)
            bz = -0.04 * L * k
            build_petal(p, az, ln, tilt, br, bz, weave_phase, sway_phase, verts, faces, uvs)

    if p["receptacle"]:
        R = base_r * p["receptacle_radius"]
        D = L * p["receptacle_depth"]
        H = L * p["receptacle_height"]
        steps = 10
        prof = []
        for s in range(1, steps + 1):
            tt = s / steps
            prof.append((R * math.sin(tt * math.pi / 2),
                         -D + (D + H) * (1.0 - math.cos(tt * math.pi / 2))))
        build_surface_of_revolution(prof, 32, verts, faces, uvs, close_bottom=True)

    if p["stem"]:
        D = L * p["receptacle_depth"] if p["receptacle"] else 0.0
        sr = L * p["stem_radius"]
        prof = [(sr, -D - L * p["stem_length"]), (sr, -D * 0.5)]
        build_surface_of_revolution(prof, 16, verts, faces, uvs, close_bottom=True)

    return verts, faces, uvs


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _set_input(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return


def get_petal_material(color):
    name = "WaterFlower_Petal"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    if mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf:
            _set_input(bsdf, ["Base Color"], (*color, 1.0))
            _set_input(bsdf, ["Roughness"], 0.45)
            _set_input(bsdf, ["Subsurface Weight", "Subsurface"], 0.2)
            _set_input(bsdf, ["Sheen Weight", "Sheen"], 0.3)
    return mat


def get_water_material():
    name = "WaterFlower_Water"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (0.3, 0.6, 1.0, 0.5)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    if mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf:
            _set_input(bsdf, ["Base Color"], (0.75, 0.9, 1.0, 1.0))
            _set_input(bsdf, ["Roughness"], 0.02)
            _set_input(bsdf, ["IOR"], 1.333)
            _set_input(bsdf, ["Transmission Weight", "Transmission"], 1.0)
    return mat


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESETS = {
    "BATH": dict(  # 絡み合う風呂型 (デフォルト)
        layers=4, petals=5, petals_increment=2, tilt_inner=72.0, tilt_outer=28.0,
        curl=-0.35, cup=0.55, twist=0.6, weave=0.06, weave_freq=1.5, sway=0.05,
        sway_freq=1.0, ruffle=0.08, ruffle_freq=5.0, width=0.42, widest=0.55,
        pointiness=0.55, length_growth=0.12, jitter=0.35, receptacle_height=0.12),
    "BROMELIAD": dict(  # 細長い葉が筒状に重なるタンク型 (アナナス)
        layers=5, petals=4, petals_increment=1, tilt_inner=82.0, tilt_outer=50.0,
        curl=-0.5, cup=0.7, twist=0.25, weave=0.03, weave_freq=1.0, sway=0.02,
        sway_freq=0.5, ruffle=0.02, ruffle_freq=3.0, width=0.3, widest=0.3,
        pointiness=0.9, length_growth=0.2, jitter=0.2, receptacle_height=0.2),
    "LOTUS": dict(  # 浅く広い蓮型の器
        layers=3, petals=8, petals_increment=3, tilt_inner=55.0, tilt_outer=18.0,
        curl=0.25, cup=0.8, twist=0.1, weave=0.02, weave_freq=1.0, sway=0.0,
        sway_freq=1.0, ruffle=0.04, ruffle_freq=4.0, width=0.45, widest=0.6,
        pointiness=0.6, length_growth=0.08, jitter=0.15, receptacle_height=0.08),
    "TANGLE": dict(  # 強く絡まった迷路型
        layers=5, petals=6, petals_increment=2, tilt_inner=75.0, tilt_outer=30.0,
        curl=-0.6, cup=0.5, twist=1.6, weave=0.12, weave_freq=2.5, sway=0.12,
        sway_freq=2.0, ruffle=0.15, ruffle_freq=7.0, width=0.4, widest=0.5,
        pointiness=0.5, length_growth=0.1, jitter=0.6, receptacle_height=0.12),
}


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class MESH_OT_water_flower_add(bpy.types.Operator):
    """絡み合う花びらで水をためる花を追加"""
    bl_idname = "mesh.water_flower_add"
    bl_label = "水をためる花 (Water Flower)"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name="プリセット",
        items=[
            ('CUSTOM', "カスタム", "現在の値をそのまま使う"),
            ('BATH', "風呂型", "絡み合う花びらの風呂"),
            ('BROMELIAD', "アナナス型", "細長い葉が筒状に重なるタンク"),
            ('LOTUS', "蓮型", "浅く広い器"),
            ('TANGLE', "迷路型", "強く絡まった花びら"),
        ],
        default='BATH',
    )
    applied_preset: EnumProperty(
        items=[(k, k, "") for k in ('NONE', 'CUSTOM', 'BATH', 'BROMELIAD', 'LOTUS', 'TANGLE')],
        default='NONE', options={'HIDDEN'},
    )
    seed: IntProperty(name="シード", default=7)

    # 配置
    layers: IntProperty(name="層の数", default=4, min=1, max=12)
    petals: IntProperty(name="内層の枚数", default=5, min=2, max=40)
    petals_increment: IntProperty(name="層ごとの増加", default=2, min=0, max=12)
    golden: BoolProperty(name="黄金角で層をずらす", default=True)
    tilt_inner: FloatProperty(name="内層の立ち上がり角", default=72.0, min=-30.0, max=90.0, subtype='NONE')
    tilt_outer: FloatProperty(name="外層の立ち上がり角", default=28.0, min=-30.0, max=90.0)
    jitter: FloatProperty(name="ばらつき", default=0.35, min=0.0, max=1.0)

    # 花びらの形
    length: FloatProperty(name="花びらの長さ", default=1.0, min=0.01, unit='LENGTH')
    length_growth: FloatProperty(name="層ごとの長さ増加", default=0.12, min=-0.5, max=1.0)
    width: FloatProperty(name="幅", default=0.42, min=0.02, max=2.0)
    widest: FloatProperty(name="最大幅の位置", default=0.55, min=0.05, max=0.95)
    pointiness: FloatProperty(name="先端の丸み↔尖り", default=0.55, min=0.1, max=3.0)
    base_width: FloatProperty(name="付け根の幅", default=0.08, min=0.0, max=1.0)
    base_radius: FloatProperty(name="付け根の半径", default=0.08, min=0.0, max=1.0)
    cup: FloatProperty(name="カップ (くぼみ)", default=0.55, min=-2.0, max=3.0)
    curl: FloatProperty(name="反り (−外 / +内)", default=-0.35, min=-3.0, max=3.0)

    # 絡み
    twist: FloatProperty(name="ねじれ", default=0.6, min=-6.0, max=6.0)
    weave: FloatProperty(name="うねり (上下の編み込み)", default=0.06, min=0.0, max=0.5)
    weave_freq: FloatProperty(name="うねりの回数", default=1.5, min=0.0, max=8.0)
    sway: FloatProperty(name="横ゆらぎ", default=0.05, min=0.0, max=0.5)
    sway_freq: FloatProperty(name="横ゆらぎの回数", default=1.0, min=0.0, max=8.0)
    ruffle: FloatProperty(name="フリル", default=0.08, min=0.0, max=1.0)
    ruffle_freq: FloatProperty(name="フリルの細かさ", default=5.0, min=0.0, max=30.0)

    # 受け皿・茎
    receptacle: BoolProperty(name="受け皿 (底をふさぐ)", default=True)
    receptacle_radius: FloatProperty(name="受け皿の半径倍率", default=2.2, min=0.5, max=10.0)
    receptacle_depth: FloatProperty(name="受け皿の深さ", default=0.12, min=0.0, max=2.0)
    receptacle_height: FloatProperty(name="受け皿の立ち上がり", default=0.12, min=0.0, max=2.0)
    stem: BoolProperty(name="茎", default=True)
    stem_length: FloatProperty(name="茎の長さ", default=1.2, min=0.0, max=10.0)
    stem_radius: FloatProperty(name="茎の太さ", default=0.04, min=0.001, max=0.5)

    # 仕上げ
    res_u: IntProperty(name="解像度 (長さ)", default=28, min=3, max=200)
    res_v: IntProperty(name="解像度 (幅)", default=12, min=2, max=100)
    thickness: FloatProperty(name="厚み", default=0.02, min=0.0, max=0.5, unit='LENGTH')
    watertight: BoolProperty(name="水密化 (ボクセル Remesh)",
                             description="重なった花びらを一体化して隙間をふさぐ",
                             default=True)
    voxel_size: FloatProperty(name="Remesh ボクセル", default=0.012, min=0.001, max=1.0, unit='LENGTH')
    smooth: BoolProperty(name="スムーズシェード", default=True)
    color: FloatVectorProperty(name="花びらの色", subtype='COLOR', size=3, min=0.0, max=1.0,
                               default=(0.95, 0.45, 0.6))
    location: FloatVectorProperty(name="位置", subtype='TRANSLATION')

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "preset")
        col.prop(self, "seed")
        box = col.box()
        box.label(text="配置")
        for n in ("layers", "petals", "petals_increment", "golden",
                  "tilt_inner", "tilt_outer", "jitter"):
            box.prop(self, n)
        box = col.box()
        box.label(text="花びらの形")
        for n in ("length", "length_growth", "width", "widest", "pointiness",
                  "base_width", "base_radius", "cup", "curl"):
            box.prop(self, n)
        box = col.box()
        box.label(text="絡み")
        for n in ("twist", "weave", "weave_freq", "sway", "sway_freq",
                  "ruffle", "ruffle_freq"):
            box.prop(self, n)
        box = col.box()
        box.label(text="受け皿・茎")
        box.prop(self, "receptacle")
        sub = box.column()
        sub.enabled = self.receptacle
        for n in ("receptacle_radius", "receptacle_depth", "receptacle_height"):
            sub.prop(self, n)
        box.prop(self, "stem")
        sub = box.column()
        sub.enabled = self.stem
        sub.prop(self, "stem_length")
        sub.prop(self, "stem_radius")
        box = col.box()
        box.label(text="仕上げ")
        for n in ("res_u", "res_v", "thickness", "watertight"):
            box.prop(self, n)
        sub = box.column()
        sub.enabled = self.watertight
        sub.prop(self, "voxel_size")
        box.prop(self, "smooth")
        box.prop(self, "color")
        col.prop(self, "location")

    def apply_preset(self):
        if self.preset == self.applied_preset:
            return
        for k, v in PRESETS.get(self.preset, {}).items():
            setattr(self, k, v)
        self.applied_preset = self.preset

    def invoke(self, context, event):
        self.location = context.scene.cursor.location
        return self.execute(context)

    def execute(self, context):
        self.apply_preset()
        p = {n: getattr(self, n) for n in (
            "seed", "layers", "petals", "petals_increment", "golden", "tilt_inner",
            "tilt_outer", "jitter", "length", "length_growth", "width", "widest",
            "pointiness", "base_width", "base_radius", "cup", "curl", "twist",
            "weave", "weave_freq", "sway", "sway_freq", "ruffle", "ruffle_freq",
            "receptacle", "receptacle_radius", "receptacle_depth", "receptacle_height",
            "stem", "stem_length", "stem_radius", "res_u", "res_v")}

        verts, faces, uvs = build_flower(p)
        mesh = bpy.data.meshes.new("WaterFlower")
        mesh.from_pydata(verts, [], faces)
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                uv_layer.data[li].uv = uvs[mesh.loops[li].vertex_index]
        mesh.validate()
        mesh.update()
        if self.smooth:
            for poly in mesh.polygons:
                poly.use_smooth = True
        mesh.materials.append(get_petal_material(self.color))

        obj = bpy.data.objects.new("WaterFlower", mesh)
        obj.location = self.location
        context.collection.objects.link(obj)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        obj["water_flower"] = True

        if self.thickness > 0.0:
            sol = obj.modifiers.new("Thickness", 'SOLIDIFY')
            sol.thickness = self.thickness
            sol.offset = 0.0
        if self.watertight:
            rem = obj.modifiers.new("Watertight", 'REMESH')
            rem.mode = 'VOXEL'
            rem.voxel_size = min(self.voxel_size, max(self.thickness * 0.8, 0.001)) \
                if self.thickness > 0 else self.voxel_size
            if hasattr(rem, "use_smooth_shade"):
                rem.use_smooth_shade = self.smooth
        return {'FINISHED'}


def find_flower(context):
    obj = context.active_object
    if obj and obj.type == 'MESH' and obj.get("water_flower"):
        return obj
    for o in context.selected_objects:
        if o.type == 'MESH' and o.get("water_flower"):
            return o
    if obj and obj.type == 'MESH' and not obj.get("water_flower_water"):
        return obj
    return None


def compute_water(context, obj, resolution, fill_fraction):
    """花をボクセル化し、中央の底から水を注いだときにたまる領域を求める。

    戻り値: dict(cells, origin, cell, level_z, floor_z, volume) または
            ("error", message)
    """
    depsgraph = context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(obj, depsgraph)
    mw = obj.matrix_world
    corners = [mw @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    size = hi - lo
    cell = max(size.x, size.y, size.z) / resolution
    if cell <= 0:
        return ("error", "オブジェクトが空です")
    lo -= Vector((cell * 2, cell * 2, cell * 2))
    nx = int(math.ceil(size.x / cell)) + 4
    ny = int(math.ceil(size.y / cell)) + 4
    nz = int(math.ceil(size.z / cell)) + 4
    inv = mw.inverted()
    # BVHTree.FromObject はローカル座標なので、距離閾値もローカルに換算
    scale = max(mw.to_scale())
    thr = cell * 0.87 / max(scale, 1e-9)

    def idx(i, j, k):
        return (k * ny + j) * nx + i

    solid = bytearray(nx * ny * nz)
    for k in range(nz):
        z = lo.z + (k + 0.5) * cell
        for j in range(ny):
            y = lo.y + (j + 0.5) * cell
            for i in range(nx):
                pt = inv @ Vector((lo.x + (i + 0.5) * cell, y, z))
                hit = bvh.find_nearest(pt, thr)
                if hit[0] is not None:
                    solid[idx(i, j, k)] = 1

    # 中心軸 (オブジェクト原点) の列から、底 (最初の固体) の上の空気セルを種にする
    c = mw.translation
    ci = min(max(int((c.x - lo.x) / cell), 0), nx - 1)
    cj = min(max(int((c.y - lo.y) / cell), 0), ny - 1)
    seed_k = None
    k = 0
    while k < nz and not solid[idx(ci, cj, k)]:
        k += 1
    while k < nz and solid[idx(ci, cj, k)]:
        k += 1
    if k < nz:
        seed_k = k
    if seed_k is None:
        return ("error", "中央に底が見つかりません (受け皿をオンにしてください)")

    def flood(level):
        """level 以下の空気セルで種から塗りつぶし。外に漏れたら None。"""
        seen = bytearray(nx * ny * nz)
        start = idx(ci, cj, seed_k)
        seen[start] = 1
        q = deque([(ci, cj, seed_k)])
        cells = []
        while q:
            i, j, k = q.popleft()
            cells.append((i, j, k))
            if i == 0 or j == 0 or k == 0 or i == nx - 1 or j == ny - 1:
                return None
            for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                a, b, cc = i + di, j + dj, k + dk
                if cc > level:
                    continue
                n = idx(a, b, cc)
                if not seen[n] and not solid[n]:
                    seen[n] = 1
                    q.append((a, b, cc))
        return cells

    if flood(seed_k) is None:
        return ("error", "底に穴があいていて水がたまりません")
    lo_k, hi_k = seed_k, nz - 1
    if flood(hi_k) is not None:
        lo_k = hi_k
    while hi_k - lo_k > 1:
        mid = (lo_k + hi_k) // 2
        if flood(mid) is None:
            hi_k = mid
        else:
            lo_k = mid
    level = seed_k + int(round((lo_k - seed_k) * fill_fraction))
    cells = flood(level)
    return dict(cells=cells, origin=lo, cell=cell,
                level_z=lo.z + (level + 1) * cell,
                floor_z=lo.z + seed_k * cell,
                volume=len(cells) * cell ** 3)


def cells_to_mesh(name, cells, origin, cell):
    """ボクセル集合の外側の面だけを持つメッシュを作る。"""
    occ = set(cells)
    vmap, verts, faces = {}, [], []

    def v(i, j, k):
        key = (i, j, k)
        if key not in vmap:
            vmap[key] = len(verts)
            verts.append((origin.x + i * cell, origin.y + j * cell, origin.z + k * cell))
        return vmap[key]

    for (i, j, k) in occ:
        if (i + 1, j, k) not in occ:
            faces.append((v(i + 1, j, k), v(i + 1, j + 1, k), v(i + 1, j + 1, k + 1), v(i + 1, j, k + 1)))
        if (i - 1, j, k) not in occ:
            faces.append((v(i, j, k), v(i, j, k + 1), v(i, j + 1, k + 1), v(i, j + 1, k)))
        if (i, j + 1, k) not in occ:
            faces.append((v(i, j + 1, k), v(i, j + 1, k + 1), v(i + 1, j + 1, k + 1), v(i + 1, j + 1, k)))
        if (i, j - 1, k) not in occ:
            faces.append((v(i, j, k), v(i + 1, j, k), v(i + 1, j, k + 1), v(i, j, k + 1)))
        if (i, j, k + 1) not in occ:
            faces.append((v(i, j, k + 1), v(i + 1, j, k + 1), v(i + 1, j + 1, k + 1), v(i, j + 1, k + 1)))
        if (i, j, k - 1) not in occ:
            faces.append((v(i, j, k), v(i, j + 1, k), v(i + 1, j + 1, k), v(i + 1, j, k)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


class OBJECT_OT_water_flower_fill(bpy.types.Operator):
    """花をボクセル化して、漏れずにたまる水位まで水を入れる"""
    bl_idname = "object.water_flower_fill"
    bl_label = "水をためる (Fill Water)"
    bl_options = {'REGISTER', 'UNDO'}

    resolution: IntProperty(name="解析解像度", default=56, min=16, max=160,
                            description="大きいほど正確だが遅い")
    fill_fraction: FloatProperty(name="満水率", default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    smooth_water: BoolProperty(name="水面をなめらかに", default=True)

    @classmethod
    def poll(cls, context):
        return find_flower(context) is not None

    def execute(self, context):
        obj = find_flower(context)
        res = compute_water(context, obj, self.resolution, self.fill_fraction)
        if isinstance(res, tuple):
            self.report({'WARNING'}, res[1])
            return {'CANCELLED'}

        old = bpy.data.objects.get(obj.name + "_Water")
        if old and old.get("water_flower_water"):
            bpy.data.objects.remove(old, do_unlink=True)

        mesh = cells_to_mesh(obj.name + "_Water", res["cells"], res["origin"], res["cell"])
        mesh.materials.append(get_water_material())
        wobj = bpy.data.objects.new(obj.name + "_Water", mesh)
        wobj["water_flower_water"] = True
        wobj["water_level"] = res["level_z"]
        wobj["water_volume"] = res["volume"]
        for col in obj.users_collection:
            col.objects.link(wobj)
            break
        else:
            context.collection.objects.link(wobj)
        if self.smooth_water:
            rem = wobj.modifiers.new("Smooth", 'REMESH')
            rem.mode = 'VOXEL'
            rem.voxel_size = res["cell"] * 0.5
            sm = wobj.modifiers.new("Relax", 'SMOOTH')
            sm.factor = 0.8
            sm.iterations = 6
            for poly in mesh.polygons:
                poly.use_smooth = True
            if hasattr(rem, "use_smooth_shade"):
                rem.use_smooth_shade = True

        depth = res["level_z"] - res["floor_z"]
        unit = context.scene.unit_settings.scale_length or 1.0
        liters = res["volume"] * unit ** 3 * 1000.0
        obj["water_level"] = res["level_z"]
        obj["water_depth"] = depth
        obj["water_volume"] = res["volume"]
        self.report({'INFO'}, "水位 z=%.3f (深さ %.3f) / 容量 %.4f (≈ %.3f L)"
                    % (res["level_z"], depth, res["volume"], liters))
        return {'FINISHED'}


class OBJECT_OT_water_flower_fluid_setup(bpy.types.Operator):
    """Mantaflow で実際に水を注ぐシミュレーションを準備する (ベイクは手動)"""
    bl_idname = "object.water_flower_fluid_setup"
    bl_label = "流体シミュレーション準備"
    bl_options = {'REGISTER', 'UNDO'}

    domain_resolution: IntProperty(name="ドメイン解像度", default=96, min=24, max=512)
    pour_frames: IntProperty(name="注水フレーム数", default=120, min=1, max=10000)

    @classmethod
    def poll(cls, context):
        return find_flower(context) is not None

    def execute(self, context):
        obj = find_flower(context)
        mw = obj.matrix_world
        corners = [mw @ Vector(c) for c in obj.bound_box]
        lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
        center = (lo + hi) * 0.5
        size = hi - lo
        coll = obj.users_collection[0] if obj.users_collection else context.collection

        # 花 = 衝突物 (Effector)
        fl = next((m for m in obj.modifiers if m.type == 'FLUID'), None) \
            or obj.modifiers.new("Fluid", 'FLUID')
        fl.fluid_type = 'EFFECTOR'
        fl.effector_settings.effector_type = 'COLLISION'
        fl.effector_settings.surface_distance = 0.0
        if hasattr(fl.effector_settings, "use_plane_init"):
            fl.effector_settings.use_plane_init = False

        # ドメイン
        dmesh = bpy.data.meshes.new("WaterFlower_Domain")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(dmesh)
        bm.free()
        dom = bpy.data.objects.new("WaterFlower_Domain", dmesh)
        dom.location = (center.x, center.y, lo.z + size.z * 0.65)
        dom.scale = (size.x * 1.3, size.y * 1.3, size.z * 1.35)
        dom.display_type = 'WIRE'
        coll.objects.link(dom)
        dm = dom.modifiers.new("Fluid", 'FLUID')
        dm.fluid_type = 'DOMAIN'
        ds = dm.domain_settings
        ds.domain_type = 'LIQUID'
        ds.resolution_max = self.domain_resolution
        ds.use_mesh = True
        ds.cache_directory = "//cache_water_flower"
        if hasattr(ds, "cache_type"):
            ds.cache_type = 'ALL'
        ds.cache_frame_end = self.pour_frames + 60

        # 注水口
        imesh = bpy.data.meshes.new("WaterFlower_Inflow")
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8,
                                  radius=max(size.x, size.y) * 0.04)
        bm.to_mesh(imesh)
        bm.free()
        inflow = bpy.data.objects.new("WaterFlower_Inflow", imesh)
        inflow.location = (mw.translation.x, mw.translation.y, hi.z + size.z * 0.1)
        coll.objects.link(inflow)
        im = inflow.modifiers.new("Fluid", 'FLUID')
        im.fluid_type = 'FLOW'
        fs = im.flow_settings
        fs.flow_type = 'LIQUID'
        fs.flow_behavior = 'INFLOW'
        fs.use_initial_velocity = True
        fs.velocity_coord = (0.0, 0.0, -1.0)
        # 注水を止める (use_inflow のキーフレーム)
        fs.use_inflow = True
        fs.keyframe_insert("use_inflow", frame=1)
        fs.use_inflow = False
        fs.keyframe_insert("use_inflow", frame=self.pour_frames)
        fs.use_inflow = True

        context.scene.frame_end = max(context.scene.frame_end, self.pour_frames + 60)
        self.report({'INFO'}, "ドメインを選択して Physics > Fluid > Bake All でベイクしてください")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class VIEW3D_PT_water_flower(bpy.types.Panel):
    bl_label = "水をためる花"
    bl_idname = "VIEW3D_PT_water_flower"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "WaterFlower"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator(MESH_OT_water_flower_add.bl_idname, icon='OUTLINER_OB_MESH')
        col.separator()
        col.operator(OBJECT_OT_water_flower_fill.bl_idname, icon='MOD_FLUIDSIM')
        col.operator(OBJECT_OT_water_flower_fluid_setup.bl_idname, icon='PHYSICS')
        obj = find_flower(context)
        if obj and "water_volume" in obj:
            box = self.layout.box()
            box.label(text="水位 z: %.3f" % obj["water_level"])
            box.label(text="深さ: %.3f" % obj["water_depth"])
            box.label(text="容量: %.4f" % obj["water_volume"])


def menu_func(self, context):
    self.layout.operator(MESH_OT_water_flower_add.bl_idname, icon='OUTLINER_OB_MESH')


classes = (
    MESH_OT_water_flower_add,
    OBJECT_OT_water_flower_fill,
    OBJECT_OT_water_flower_fluid_setup,
    VIEW3D_PT_water_flower,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
