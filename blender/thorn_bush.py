# SPDX-License-Identifier: MIT
"""
いばらジェネレーター (Thorn Bush Generator) for Blender

細かく枝分かれした実在のいばら (サンザシ・リンボク・カラタチのような
トゲのある落葉低木〜小高木) を 1 株ずつ、軽いメッシュとして生成する Blender アドオン。
株は 3D カーソルの位置に、根元を原点にした 1 つのオブジェクトとして置かれるので、
そのままコピーして好きな場所に配置できる。

使い方:
  * ドラッグ&ドロップ: 拡張機能の zip を Blender のウィンドウにドロップ (Blender 4.2 以降)
  * スクリプトとして: テキストエディタで開いて「スクリプト実行」
  * ヘッドレス: blender -b -P thorn_bush.py -- --seed 7 --save bush.blend
    (引数は `blender -b -P thorn_bush.py -- --help` を参照)

3D ビューのサイドバー (N キー) に「いばら」タブが追加される。
"""

bl_info = {
    "name": "Thorn Bush Generator (いばら)",
    "author": "A_life",
    "version": (4, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > いばら",
    "description": "リアルないばらの低木・小高木を 1 株ずつ軽いメッシュで生成する",
    "category": "Add Mesh",
}

import math
import random
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Vector, noise

UP = Vector((0.0, 0.0, 1.0))
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
FORMS = (
    ("RANDOM", "ランダム", "高木型か株立ち型を乱数で選ぶ"),
    ("TREE", "高木型", "1 本の幹の上に樹冠を広げる"),
    ("SHRUB", "株立ち型", "根元から何本もの幹が立つ"),
)


# ---------------------------------------------------------------------------
# 設定値 (PropertyGroup とスクリプト用デフォルトで共有)
# ---------------------------------------------------------------------------

# name: (type, default, min, max, label, description)。enum は min の位置に選択肢を置く
SETTINGS = {
    "seed": ("int", 1, 0, 1_000_000, "シード", "乱数シード。同じ値なら同じ形になる"),
    "random_seed": ("bool", True, None, None, "毎回ちがう形", "生成するたびにシードを変える (使ったシードは上の欄に残る)"),
    "height": ("float", 2.5, 0.1, 30.0, "高さ", "株のおおよその高さ (m)"),
    "form": ("enum", "RANDOM", FORMS, None, "樹形", "高木型か株立ち型か"),
    "stems": ("int", 4, 1, 12, "株立ちの幹数", "株立ち型の根元から出る幹の数 (平均)"),
    "trunk_radius": ("float", 0.06, 0.005, 1.0, "幹の太さ", "高さ 4 m のときの幹の半径 (m)。高さに応じて変わる"),
    "branch_levels": ("int", 4, 1, 6, "枝の階層", "枝分かれの深さ (高さ 2 m 未満は自動で浅くなる)。大きいほど小枝が増えて重くなる"),
    "branch_count": ("int", 4, 1, 8, "枝の数", "1 本の枝から出る側枝の数 (先端の延長枝は別)"),
    "branch_angle": ("float", 50.0, 5.0, 90.0, "分岐角", "側枝が親の枝から開く角度 (度)"),
    "length_ratio": ("float", 0.6, 0.2, 1.0, "枝の長さ比", "親の枝に対する子の枝の長さ"),
    "radius_ratio": ("float", 0.6, 0.2, 0.95, "枝の太さ比", "分岐点での親の太さに対する子の太さ"),
    "up_tendency": ("float", 0.35, 0.0, 3.0, "上向き", "枝が光を求めて上へ向かう強さ"),
    "internode": ("float", 0.12, 0.02, 1.0, "節の間隔", "小枝の節と節の距離 (m)。太い枝ほど自動で長くなる。大きいほど軽い"),
    "crookedness": ("float", 1.0, 0.0, 3.0, "節の折れ", "節ごとに枝の向きが変わる大きさ (1 で平均約 10°)"),
    "wiggle": ("float", 0.3, 0.0, 3.0, "うねり", "枝全体のゆるやかな曲がり (小さいほど直線的)"),
    "droop": ("float", 0.4, 0.0, 3.0, "枝垂れ", "細い枝が重さで垂れる強さ"),
    "spur_ratio": ("float", 0.4, 0.0, 1.0, "短枝の多さ", "節から出る、先がトゲ状にとがった短い枝の割合"),
    "dead_ratio": ("float", 0.08, 0.0, 1.0, "枯れ枝の割合", "途中で折れて止まった枝の割合"),
    "thorn_chance": ("float", 0.4, 0.0, 1.0, "トゲの付く割合", "枝の節ごとにトゲが付く確率 (幹には付かない)"),
    "thorn_size": ("float", 0.025, 0.002, 0.5, "トゲの長さ", "トゲの長さ (m)"),
    "thorn_hook": ("float", 0.05, -1.0, 2.0, "トゲの反り", "トゲが根元側へ鉤状に曲がる量 (0 でまっすぐ)"),
    "trunk_sides": ("int", 8, 3, 24, "幹の断面の角数", "幹の断面の角数。細い枝ほど自動で減る。小さいほど軽い"),
    "twig_sides": ("int", 3, 3, 12, "小枝の断面の角数", "小枝・短枝の断面の角数。数が多いので 3 で十分なことが多い"),
}


def default_settings(**overrides):
    """スクリプト/CLI 用: デフォルト値の設定オブジェクトを作る。"""
    unknown = set(overrides) - set(SETTINGS)
    if unknown:
        raise TypeError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    ns = type("ThornBushSettingsNS", (), {})()
    for name, spec in SETTINGS.items():
        setattr(ns, name, overrides.get(name, spec[1]))
    return ns


# ---------------------------------------------------------------------------
# 枝の形
# ---------------------------------------------------------------------------

class Branch:
    """太さが先細りする 1 本の枝 (折れ線)。depth は幹=0 から数えた枝の階層。"""

    __slots__ = ("points", "r0", "r1", "depth", "spur")

    def __init__(self, points, r0, r1, depth, spur=False):
        self.points = points  # 偶数番目が節、奇数番目が節間の中点
        self.r0 = r0
        self.r1 = r1
        self.depth = depth
        self.spur = spur

    def nodes(self):
        """根元と先端を除く節の点番号。"""
        return range(2, len(self.points) - 1, 2)

    def radius(self, i):
        t = i / max(1, len(self.points) - 1)
        return self.r1 + (self.r0 - self.r1) * (1.0 - t) ** 0.8

    def tangent(self, i):
        pts = self.points
        d = pts[min(len(pts) - 1, i + 1)] - pts[max(0, i - 1)]
        return d.normalized() if d.length > 1e-9 else UP.copy()


def any_perpendicular(v):
    other = Vector((1.0, 0.0, 0.0)) if abs(v.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return v.cross(other).normalized()


def random_unit(rng):
    while True:
        v = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        if 1e-4 < v.length_squared <= 1.0:
            return v.normalized()


def perpendicular_jitter(rng, d):
    v = d.cross(random_unit(rng))
    return v.normalized() if v.length > 1e-6 else any_perpendicular(d)


def grow_branch(rng, s, start, direction, length, depth, dmax, up, min_nodes=1):
    """節ごとに少しずつ向きを変えて伸びる、直線的な枝の点列。

    節間 (節と節の間) はほぼまっすぐでわずかに弓なり、節で向きが小さく折れる。
    それに枝全体のゆるやかなうねり・上向きの成長・細い枝ほど強い垂れを重ねる。
    """
    internode = s.internode * (1.0 + 1.5 * (dmax - depth) / max(1, dmax))  # 太い枝ほど節間が長い
    nodes = max(min_nodes, min(40, round(length / internode)))
    seg = length / nodes
    d = direction.normalized()
    p = start.copy()
    off = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))
    sag = s.droop * depth / max(1, dmax)
    kink = math.radians(10.0) * s.crookedness * (0.35 if depth == 0 else 1.0)
    rate = 2.0 / nodes  # 変化の総量が節の数によらないように
    pts = [p.copy()]
    for k in range(nodes):
        t = k / nodes
        if k > 0:
            angle = kink * abs(rng.gauss(0.0, 1.0))
            d = (d + perpendicular_jitter(rng, d) * math.tan(min(angle, 1.2))).normalized()
        wave = noise.noise_vector(Vector((k * seg * 0.7, 0.0, 0.0)) + off) * s.wiggle
        d = (d + (wave + UP * up - UP * (sag * t)) * rate).normalized()
        bow = perpendicular_jitter(rng, d) * (seg * rng.uniform(0.0, 0.04))
        mid = p + d * (seg * 0.5) + bow
        p = p + d * seg
        for q in (mid, p):
            q.z = max(q.z, 0.0)  # 根元より下には伸ばさない
        if p.z == 0.0 and d.z < 0.0:
            d.z = 0.0
            d.normalize()
        pts.extend((mid, p.copy()))
    return pts


def add_spur(rng, branch, i, out):
    """節から出る短枝。短く、先が鋭くとがってトゲのように見える。"""
    p = branch.points[i]
    tan = branch.tangent(i)
    tilt = math.radians(rng.uniform(40.0, 80.0))
    d = (tan * math.cos(tilt) + perpendicular_jitter(rng, tan) * math.sin(tilt)).normalized()
    length = rng.uniform(0.02, 0.09)
    bend = perpendicular_jitter(rng, d) * (length * 0.06)
    pts = [p.copy(), p + d * (length * 0.5) + bend, p + d * length]
    out.append(Branch(pts, max(branch.radius(i) * 0.55, 0.001), 0.0, branch.depth + 1, spur=True))


def add_branch(rng, s, start, direction, length, r0, depth, dmax, t0, out, dead=False, crown=1.0):
    """枝を 1 本作り、側枝・短枝・先端の延長枝を再帰的に付ける。"""
    up = s.up_tendency * (1.5 if depth == 0 else 0.6)
    last = dead or depth >= dmax
    # 側枝を出す枝には、側枝の数より多い節を必ず用意する (短い枝でも枝分かれが途切れないように)
    min_nodes = 2 if last else round(s.branch_count * crown / max(1e-3, 1.0 - t0)) + 2
    pts = grow_branch(rng, s, start, direction, length, depth, dmax, up, min_nodes)
    if dead:
        r1 = r0 * 0.7  # 折れた枝は先が太いまま途切れる
    elif last:
        r1 = 0.0  # 小枝の先は鋭くとがる
    else:
        r1 = r0 * 0.45
    branch = Branch(pts, r0, r1, depth)
    out.append(branch)
    n = len(pts)
    nodes = list(branch.nodes())
    # 側枝は 1 つの節から 1 本ずつ、t0 より先の節に互生 (黄金角) で出す
    lateral = []
    if not last:
        cands = [i for i in nodes if i / (n - 1) >= t0] or nodes[-1:]
        count = min(len(cands), max(1, round(s.branch_count * crown * rng.uniform(0.6, 1.4))))
        lateral = sorted(rng.sample(cands, count))
    if depth >= 1 and not dead:
        for i in nodes:
            if i not in lateral and rng.random() < s.spur_ratio * 0.5:
                add_spur(rng, branch, i, out)
    if last:
        return
    phi = rng.uniform(0, math.tau)
    for i in lateral:
        t = i / (n - 1)
        tan = branch.tangent(i)
        phi += GOLDEN_ANGLE + rng.uniform(-0.3, 0.3)
        a = any_perpendicular(tan)
        b = tan.cross(a)
        side = a * math.cos(phi) + b * math.sin(phi)
        tilt = math.radians(s.branch_angle * rng.uniform(0.7, 1.3))
        cd = tan * math.cos(tilt) + side * math.sin(tilt)
        clen = length * s.length_ratio * crown * rng.uniform(0.7, 1.2) * (1.2 - 0.5 * t)  # 根元側ほど長い
        cr = min(branch.radius(i) * 0.9, branch.radius(i) * s.radius_ratio * rng.uniform(0.75, 1.1))
        is_dead = depth >= 1 and rng.random() < s.dead_ratio
        if is_dead:
            clen *= rng.uniform(0.15, 0.45)
        add_branch(rng, s, pts[i], cd, clen, cr, depth + 1, dmax, 0.15, out, dead=is_dead)
    # 先端の延長枝 (頂芽の伸長)
    tip_dir = (branch.tangent(n - 1) + perpendicular_jitter(rng, branch.tangent(n - 1)) * 0.2).normalized()
    add_branch(rng, s, pts[-1], tip_dir, length * s.length_ratio * rng.uniform(0.8, 1.15),
               branch.r1, depth + 1, dmax, 0.15, out)


def build_branches(s):
    """1 株ぶんの枝を、根元を原点にして作る。"""
    rng = random.Random(s.seed)
    noise.seed_set(s.seed)  # ノイズ表はプロセスごとに乱数初期化されるので固定する
    height = s.height
    dmax = max(1, s.branch_levels - (0 if height >= 2.0 else 1 if height >= 1.0 else 2))
    r_base = s.trunk_radius * (height / 4.0) ** 1.2
    form = s.form
    if form == "RANDOM":
        form = "TREE" if rng.random() < 0.35 else "SHRUB"
    if form == "TREE":
        # 高木型は幹の上半分から長い枝を多めに出して樹冠を作る
        stems = [(rng.uniform(0, 8), height * rng.uniform(0.35, 0.5), r_base, 0.45, 1.6)]
    else:
        k = max(1, s.stems + rng.randint(-1, 1))
        r = r_base * math.sqrt(1.6 / k)  # 断面積の合計がおおよそ保たれるように
        stems = [(rng.uniform(8, 35), height * rng.uniform(0.45, 0.8), r * rng.uniform(0.7, 1.15), 0.2, 1.0)
                 for _ in range(k)]
    out = []
    for lean, length, r0, t0, crown in stems:
        az = rng.uniform(0, math.tau)
        tilt = math.radians(lean)
        d = Vector((math.cos(az) * math.sin(tilt), math.sin(az) * math.sin(tilt), math.cos(tilt)))
        off = Vector((math.cos(az), math.sin(az), 0.0)) * (r_base * rng.uniform(0.5, 2.0)) if form == "SHRUB" else Vector()
        add_branch(rng, s, off, d, length, r0, 0, dmax, t0, out, crown=crown)
    return out, rng


# ---------------------------------------------------------------------------
# メッシュ化
# ---------------------------------------------------------------------------

MAT_BARK, MAT_TWIG, MAT_THORN = 0, 1, 2


class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.faces = []
        self.mats = []

    def add(self, verts, faces, mat):
        o = len(self.verts)
        self.verts.extend(verts)
        self.faces.extend(tuple(i + o for i in f) for f in faces)
        self.mats.extend([mat] * len(faces))


def add_tube(mb, pts, radii, sides, mat):
    """折れ線に沿った先細りの管。先端の半径が 0 なら 1 点にすぼめる (ふたはしない)。"""
    n = len(pts)
    tangents = []
    for i in range(n):
        d = pts[min(n - 1, i + 1)] - pts[max(0, i - 1)]
        tangents.append(d.normalized() if d.length > 1e-9 else UP.copy())
    normal = any_perpendicular(tangents[0])
    prev = tangents[0]
    verts, faces = [], []
    pointed = radii[-1] <= 1e-6
    rings = n - 1 if pointed else n
    for i in range(rings):
        t = tangents[i]
        normal = prev.rotation_difference(t) @ normal  # ねじれの少ないフレームを受け渡す
        normal = (normal - t * normal.dot(t)).normalized()
        binormal = t.cross(normal)
        prev = t
        for k in range(sides):
            a = math.tau * k / sides
            verts.append(pts[i] + (normal * math.cos(a) + binormal * math.sin(a)) * radii[i])
    for i in range(rings - 1):
        for k in range(sides):
            k2 = (k + 1) % sides
            faces.append((i * sides + k, i * sides + k2, (i + 1) * sides + k2, (i + 1) * sides + k))
    if pointed:
        verts.append(pts[-1].copy())
        tip = len(verts) - 1
        last = (rings - 1) * sides
        for k in range(sides):
            faces.append((last + k, last + (k + 1) % sides, tip))
    mb.add(verts, faces, mat)


def add_thorn(mb, base, axis, back, length, radius, hook):
    """根元 base から axis 方向に伸び、back 方向へわずかに反る三角錐。"""
    verts, faces = [], []
    sides, rings = 3, 2
    for j in range(rings):
        u = j / rings
        c = base + axis * (length * u) + back * (length * hook * u * u)
        d = (axis + back * (2 * hook * u)).normalized()
        a = d.cross(back)
        a = a.normalized() if a.length > 1e-6 else any_perpendicular(d)
        b = d.cross(a)
        r = radius * (1.0 - u) ** 1.2
        for k in range(sides):
            ang = math.tau * k / sides
            verts.append(c + (a * math.cos(ang) + b * math.sin(ang)) * r)
    verts.append(base + axis * length + back * (length * hook))
    for k in range(sides):
        k2 = (k + 1) % sides
        faces.append((k, k2, sides + k2, sides + k))
    t = len(verts) - 1
    for k in range(sides):
        faces.append((sides + k, sides + (k + 1) % sides, t))
    mb.add(verts, faces, MAT_THORN)


def build_mesh(s):
    branches, rng = build_branches(s)
    mb = MeshBuilder()
    r_trunk = max((b.r0 for b in branches), default=0.01)
    for b in branches:
        if b.spur or b.r0 < 0.004:
            sides = s.twig_sides
        else:
            # 太さに応じて断面の角数を減らす
            f = min(1.0, b.r0 / max(r_trunk, 1e-6)) ** 0.5
            sides = max(s.twig_sides, round(s.twig_sides + (s.trunk_sides - s.twig_sides) * f))
        radii = [b.radius(i) for i in range(len(b.points))]
        add_tube(mb, b.points, radii, sides, MAT_BARK if b.depth <= 1 and not b.spur else MAT_TWIG)
    # トゲ: 枝の節に付ける (幹と短枝には付けない)
    if s.thorn_chance > 0:
        for b in branches:
            if b.depth == 0 or b.spur:
                continue
            for i in b.nodes():
                if rng.random() >= s.thorn_chance:
                    continue
                r = b.radius(i)
                tan = b.tangent(i)
                side = perpendicular_jitter(rng, tan)
                length = s.thorn_size * rng.uniform(0.5, 1.4)
                axis = (side + tan * rng.uniform(0.2, 0.7)).normalized()
                add_thorn(mb, b.points[i] + side * (r * 0.6), axis, -tan, length,
                          min(r * 0.9, length * 0.14), s.thorn_hook * rng.uniform(0.5, 1.5))
    return mb


# ---------------------------------------------------------------------------
# Blender オブジェクト / マテリアル
# ---------------------------------------------------------------------------

def _principled(mat):
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return nt, bsdf


def _object_coords(nt):
    """テクスチャ座標をオブジェクト座標 (メートル単位) にして、模様が実寸で出るようにする。"""
    tc = next((n for n in nt.nodes if n.type == "TEX_COORD"), None) or nt.nodes.new("ShaderNodeTexCoord")
    return tc.outputs["Object"]


def get_material(name, color, roughness, noise_to, noise_scale, bump=0.0, cells=0.0):
    """ノイズで色むらを付けたマテリアル。cells > 0 なら細胞状の溝 (樹皮の割れ目) の凹凸も加える。
    同名のマテリアルがあればそれを使うので、生成後に自由に編集してよい。"""
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    nt, bsdf = _principled(mat)
    bsdf.inputs["Roughness"].default_value = roughness
    tex = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(_object_coords(nt), tex.inputs["Vector"])
    tex.inputs["Scale"].default_value = noise_scale
    tex.inputs["Detail"].default_value = 8.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*color, 1.0)
    ramp.color_ramp.elements[1].color = (*noise_to, 1.0)
    ramp.color_ramp.elements[0].position = 0.3  # ノイズの値は中央付近に集まるのでコントラストを広げる
    ramp.color_ramp.elements[1].position = 0.7
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    normal = None
    if bump > 0:
        b1 = nt.nodes.new("ShaderNodeBump")
        b1.inputs["Strength"].default_value = bump
        nt.links.new(tex.outputs["Fac"], b1.inputs["Height"])
        normal = b1.outputs["Normal"]
    if cells > 0:
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        vor.feature = "DISTANCE_TO_EDGE"
        vor.inputs["Scale"].default_value = cells
        nt.links.new(_object_coords(nt), vor.inputs["Vector"])
        b2 = nt.nodes.new("ShaderNodeBump")
        b2.inputs["Strength"].default_value = 0.35
        b2.inputs["Distance"].default_value = 0.002
        nt.links.new(vor.outputs["Distance"], b2.inputs["Height"])
        if normal is not None:
            nt.links.new(normal, b2.inputs["Normal"])
        normal = b2.outputs["Normal"]
    if normal is not None:
        nt.links.new(normal, bsdf.inputs["Normal"])
    return mat


def materials():
    return (
        get_material("Thorn_Bark", (0.028, 0.024, 0.02), 0.85, (0.085, 0.072, 0.06), 30.0, 0.6, cells=45.0),
        get_material("Thorn_Twig", (0.075, 0.045, 0.028), 0.55, (0.19, 0.12, 0.072), 25.0, 0.2),
        get_material("Thorn_Spine", (0.16, 0.10, 0.06), 0.4, (0.04, 0.025, 0.018), 200.0),
    )


def create_bush(context, s):
    """1 株を 1 つのメッシュオブジェクトとして作り、3D カーソルの位置に置いて選択する。"""
    mb = build_mesh(s)
    me = bpy.data.meshes.new("Thorn_Bush")
    me.from_pydata([tuple(v) for v in mb.verts], [], mb.faces)
    me.polygons.foreach_set("material_index", mb.mats)
    me.polygons.foreach_set("use_smooth", [True] * len(mb.faces))
    for mat in materials():
        me.materials.append(mat)
    me.update()
    obj = bpy.data.objects.new("Thorn_Bush", me)
    obj["thorn_seed"] = s.seed  # どのシードで作ったかを残しておく
    collection = context.collection or context.scene.collection
    collection.objects.link(obj)
    obj.location = context.scene.cursor.location
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _make_property(spec):
    kind, default, lo, hi, label, desc = spec
    if kind == "bool":
        return BoolProperty(name=label, description=desc, default=default)
    if kind == "int":
        return IntProperty(name=label, description=desc, default=default, min=lo, max=hi)
    if kind == "enum":
        return EnumProperty(name=label, description=desc, items=lo, default=default)
    return FloatProperty(name=label, description=desc, default=default, min=lo, max=hi)


ThornBushSettings = type(
    "ThornBushSettings",
    (bpy.types.PropertyGroup,),
    {"__annotations__": {name: _make_property(spec) for name, spec in SETTINGS.items()}},
)


class THORNBUSH_OT_add(bpy.types.Operator):
    bl_idname = "object.thorn_bush_add"
    bl_label = "いばらを1株生成"
    bl_description = "3D カーソルの位置にいばらを 1 株生成する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        s = context.scene.thorn_bush
        if s.random_seed:
            s.seed = random.randint(0, 999_999)
        obj = create_bush(context, s)
        self.report({"INFO"}, f"いばらを生成しました (シード {s.seed}、{len(obj.data.vertices):,} 頂点)")
        return {"FINISHED"}


PANEL_LAYOUT = (
    ("形", ("height", "form", "stems", "trunk_radius")),
    ("枝", ("branch_levels", "branch_count", "branch_angle", "length_ratio", "radius_ratio",
           "up_tendency", "internode", "crookedness", "wiggle", "droop")),
    ("リアルさ", ("spur_ratio", "dead_ratio")),
    ("トゲ", ("thorn_chance", "thorn_size", "thorn_hook")),
    ("軽さ", ("trunk_sides", "twig_sides")),
)


class THORNBUSH_PT_panel(bpy.types.Panel):
    bl_label = "いばら"
    bl_idname = "THORNBUSH_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "いばら"

    def draw(self, context):
        s = context.scene.thorn_bush
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator(THORNBUSH_OT_add.bl_idname, icon="ADD")
        row = layout.row(align=True)
        row.prop(s, "seed")
        row.prop(s, "random_seed", text="", icon="FILE_REFRESH")
        layout.label(text="3D カーソルの位置に置かれます", icon="PIVOT_CURSOR")
        for title, names in PANEL_LAYOUT:
            box = layout.box()
            box.label(text=title)
            colm = box.column(align=True)
            for name in names:
                colm.prop(s, name)


def menu_func(self, context):
    self.layout.operator(THORNBUSH_OT_add.bl_idname, text="いばら", icon="OUTLINER_OB_MESH")


CLASSES = (ThornBushSettings, THORNBUSH_OT_add, THORNBUSH_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.thorn_bush = PointerProperty(type=ThornBushSettings)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)  # 追加 > メッシュ にも出す


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
    del bpy.types.Scene.thorn_bush
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


# ---------------------------------------------------------------------------
# ヘッドレス実行: blender -b -P thorn_bush.py -- [options]
# ---------------------------------------------------------------------------

def _cli(argv):
    import argparse

    parser = argparse.ArgumentParser(prog="thorn_bush.py", description="いばらを 1 株生成する")
    for name, (kind, default, lo, *_rest) in SETTINGS.items():
        flag = "--" + name.replace("_", "-")
        if kind == "bool":
            parser.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes", "on"), default=default)
        elif kind == "enum":
            parser.add_argument(flag, choices=[item[0] for item in lo], default=default)
        else:
            parser.add_argument(flag, type=int if kind == "int" else float, default=default)
    parser.add_argument("--save", help="生成後に .blend として保存するパス")
    parser.add_argument("--render", help="確認用に簡単な光とカメラを置いてレンダリングする画像パス (.png)")
    parser.add_argument("--samples", type=int, default=32)
    args = parser.parse_args(argv)
    args.random_seed = False  # CLI では指定したシードをそのまま使う

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    s = default_settings(**{k: getattr(args, k) for k in SETTINGS})
    obj = create_bush(bpy.context, s)
    print(f"[thorn_bush] {obj.name}: {len(obj.data.vertices):,} 頂点, {len(obj.data.polygons):,} 面")

    if args.render:
        scene = bpy.context.scene
        world = scene.world or bpy.data.worlds.new("World")
        scene.world = world
        if world.node_tree is None:
            world.use_nodes = True
        bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
        bg.inputs["Color"].default_value = (0.35, 0.4, 0.48, 1.0)
        bg.inputs["Strength"].default_value = 0.6
        sun = bpy.data.objects.new("Preview_Sun", bpy.data.lights.new("Preview_Sun", "SUN"))
        sun.data.energy = 4.0
        sun.data.color = (1.0, 0.85, 0.7)
        sun.rotation_euler = (math.radians(55), 0.0, math.radians(30))
        scene.collection.objects.link(sun)
        cam = bpy.data.objects.new("Preview_Camera", bpy.data.cameras.new("Preview_Camera"))
        cam.data.lens = 35
        h = s.height
        cam.location = (h * 0.7, -h * 1.25, h * 0.5)
        cam.rotation_euler = (Vector((0, 0, h * 0.5)) - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(cam)
        scene.camera = cam
        scene.render.engine = "CYCLES"
        scene.cycles.samples = args.samples
        scene.cycles.device = "CPU"
        scene.render.resolution_x = 1000
        scene.render.resolution_y = 1000
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=args.save)


if __name__ == "__main__":
    if "--" in sys.argv or bpy.app.background:
        _cli(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    else:
        register()
