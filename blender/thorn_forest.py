# SPDX-License-Identifier: MIT
"""
茨の森ジェネレーター (Thorn Forest Generator) for Blender

細かく枝分かれした実在のいばら (サンザシ・リンボク・カラタチのような
トゲのある落葉低木〜小高木) の群落を、大きさのばらつきを付けて手続き生成する
Blender アドオン。

使い方:
  * アドオンとして: 編集 > プリファレンス > アドオン > インストール でこのファイルを選択
  * スクリプトとして: テキストエディタで開いて「スクリプト実行」
  * ヘッドレス: blender -b -P thorn_forest.py -- --seed 7 --render forest.png
    (引数は `blender -b -P thorn_forest.py -- --help` を参照)

3D ビューのサイドバー (N キー) に「茨の森」タブが追加される。
"""

bl_info = {
    "name": "Thorn Forest Generator (茨の森)",
    "author": "A_life",
    "version": (2, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > 茨の森",
    "description": "大きさのばらつきを持ったリアルないばらの低木・小高木の群落を生成する",
    "category": "Add Mesh",
}

import math
import random
import sys

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Matrix, Vector, noise

COLLECTION_NAME = "Thorn Forest"
UP = Vector((0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# 設定値 (PropertyGroup とスクリプト用デフォルトで共有)
# ---------------------------------------------------------------------------

# name: (type, default, min, max, label, description)
SETTINGS = {
    "seed": ("int", 1, 0, 1_000_000, "シード", "乱数シード"),
    "area_size": ("float", 16.0, 2.0, 500.0, "範囲", "森を生成する正方形の一辺 (m)"),
    "plant_count": ("int", 45, 0, 2000, "株の数", "生成するいばらの株の数"),
    "spacing": ("float", 0.35, 0.0, 3.0, "間隔", "樹冠どうしの間隔の係数。0 で自由に重なり、1 で樹冠が接する程度"),
    "min_height": ("float", 1.0, 0.1, 50.0, "最小の高さ", "いちばん小さい株の高さ (m)"),
    "max_height": ("float", 6.0, 0.1, 50.0, "最大の高さ", "いちばん大きい株の高さ (m)"),
    "small_bias": ("float", 1.3, 0.2, 5.0, "小さい株の多さ", "1 で大小が均等、大きいほど小さい株が多く大きい株はまれになる"),
    "tree_ratio": ("float", 0.35, 0.0, 1.0, "高木型の割合", "1 本の幹から樹冠を広げる株の割合 (残りは根元から何本も幹が立つ株立ち)"),
    "stems": ("int", 4, 1, 12, "株立ちの幹数", "株立ち型の株の根元から出る幹の数 (平均)"),
    "trunk_radius": ("float", 0.06, 0.005, 1.0, "幹の太さ", "高さ 4 m の株の幹の半径 (m)。株の大きさに応じて変わる"),
    "branch_levels": ("int", 4, 1, 6, "枝の階層", "大きい株の枝分かれの深さ (小さい株は自動で浅くなる)"),
    "branch_count": ("int", 4, 1, 8, "枝の数", "1 本の枝から出る側枝の数 (先端の延長枝は別)"),
    "branch_angle": ("float", 50.0, 5.0, 90.0, "分岐角", "側枝が親の枝から開く角度 (度)"),
    "length_ratio": ("float", 0.6, 0.2, 1.0, "枝の長さ比", "親の枝に対する子の枝の長さ"),
    "radius_ratio": ("float", 0.6, 0.2, 0.95, "枝の太さ比", "分岐点での親の太さに対する子の太さ"),
    "up_tendency": ("float", 0.35, 0.0, 3.0, "上向き", "枝が光を求めて上へ向かう強さ"),
    "wiggle": ("float", 1.0, 0.0, 3.0, "うねり", "枝がうねうねと曲がる強さ"),
    "wave_count": ("float", 3.0, 0.5, 12.0, "うねりの回数", "1 本の枝の中で曲がる回数の目安"),
    "crookedness": ("float", 0.9, 0.0, 3.0, "ジグザグ", "節ごとに枝が折れ曲がる強さ (いばらの小枝らしさ)"),
    "droop": ("float", 0.4, 0.0, 3.0, "枝垂れ", "細い枝が重さで垂れる強さ"),
    "step": ("float", 0.1, 0.02, 1.0, "解像度", "枝のサンプリング間隔の上限 (m)。小さいほど滑らかで重い"),
    "thorn_density": ("float", 5.0, 0.0, 60.0, "トゲ密度", "枝 1 m あたりのトゲの数 (幹には付かない)"),
    "thorn_size": ("float", 0.025, 0.002, 0.5, "トゲの長さ", "トゲの長さ (m)"),
    "thorn_hook": ("float", 0.1, -1.0, 2.0, "トゲの反り", "トゲが根元側へ鉤状に曲がる量 (0 でまっすぐ)"),
    "ground": ("bool", True, None, None, "地面", "地面を作成する"),
    "atmosphere": ("bool", True, None, None, "雰囲気", "霧・月光・暗い空を設定する"),
    "fog_density": ("float", 0.015, 0.0, 1.0, "霧の濃さ", "地表付近の霧の密度 (上空ほど薄くなる)"),
    "convert_to_mesh": ("bool", False, None, None, "メッシュに変換", "枝のカーブをメッシュに変換する (書き出し用)"),
    "clear_previous": ("bool", True, None, None, "前回分を削除", f"「{COLLECTION_NAME}」コレクションの中身を消してから生成する"),
}


def default_settings(**overrides):
    """スクリプト/CLI 用: デフォルト値の設定オブジェクトを作る。"""
    unknown = set(overrides) - set(SETTINGS)
    if unknown:
        raise TypeError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    ns = type("ThornForestSettingsNS", (), {})()
    for name, spec in SETTINGS.items():
        setattr(ns, name, overrides.get(name, spec[1]))
    return ns


# ---------------------------------------------------------------------------
# 形状の計算 (bpy に依存しない部分)
# ---------------------------------------------------------------------------

class Vine:
    """太さが先細りする 1 本の枝 (折れ線)。depth は幹=0 から数えた枝の階層。"""

    __slots__ = ("points", "r0", "r1", "depth")

    def __init__(self, points, r0, r1=None, depth=0):
        self.points = points
        self.r0 = r0
        self.r1 = r0 * 0.12 if r1 is None else r1
        self.depth = depth

    def radius(self, i):
        t = i / max(1, len(self.points) - 1)
        return self.r1 + (self.r0 - self.r1) * (1.0 - t) ** 0.8

    def tangent(self, i):
        pts = self.points
        a = pts[max(0, i - 1)]
        b = pts[min(len(pts) - 1, i + 1)]
        d = b - a
        return d.normalized() if d.length > 1e-9 else UP.copy()

    def length(self):
        return sum((b - a).length for a, b in zip(self.points, self.points[1:]))


def any_perpendicular(v):
    other = Vector((1.0, 0.0, 0.0)) if abs(v.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return v.cross(other).normalized()


def random_unit(rng):
    while True:
        v = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        if 1e-4 < v.length_squared <= 1.0:
            return v.normalized()


GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


def grow_branch(rng, s, start, direction, length, depth, dmax, up):
    """1 本の枝の点列。なめらかな曲がり・節ごとの折れ (ジグザグ)・上向きの成長・
    細い枝ほど強い重力による垂れを合成する。変化量は解像度と長さに依存しない。"""
    # うねりを表現できるよう、短い小枝でも 1 うねりあたり 4 点以上を確保する
    n = max(8, int(math.ceil(s.wave_count * 4)), int(round(length / s.step)))
    step = length / n
    d = direction.normalized()
    p = start.copy()
    off = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))
    freq = s.wave_count / max(length, 0.05)  # 枝の長さあたりのうねりの周波数
    bend = s.wiggle * (0.5 if depth == 0 else 1.0) * 7.0  # 幹は控えめに
    sag = s.droop * depth / max(1, dmax)
    kink = s.crookedness * (0.08 + 0.12 * depth / max(1, dmax))
    next_node = rng.randint(2, 5)
    pts = [p.copy()]
    for i in range(n):
        t = i / n
        # 枝に沿った距離で引くノイズで向きを曲げ続けると、うねうねと蛇行する
        wave = noise.noise_vector(Vector((i * step * freq, 0.0, 0.0)) + off)
        d = (d + wave * (bend / n)).normalized()
        d = (d + (UP * up - UP * (sag * t)) * (2.0 / n)).normalized()
        if i == next_node:  # 節: 小枝がジグザグに折れる
            d = (d + random_unit(rng) * kink).normalized()
            next_node = i + rng.randint(2, 5)
        p = p + d * step
        if p.z < 0.0:  # 地面には潜らない
            p.z = 0.0
            d.z = abs(d.z)
            d.normalize()
        pts.append(p.copy())
    return pts


def add_branch(rng, s, start, direction, length, r0, depth, dmax, t0, out):
    """枝を 1 本作り、側枝と先端の延長枝を再帰的に付ける。"""
    up = s.up_tendency * (1.5 if depth == 0 else 0.6)
    pts = grow_branch(rng, s, start, direction, length, depth, dmax, up)
    last = depth >= dmax
    vine = Vine(pts, r0, max(0.0012, r0 * (0.12 if last else 0.45)), depth)
    out.append(vine)
    if last:
        return
    n = len(pts)
    count = max(1, round(s.branch_count * rng.uniform(0.6, 1.4)))
    phi = rng.uniform(0, math.tau)
    for c in range(count):
        # 側枝は枝に沿って層状に、互生 (黄金角) で配置する
        t = t0 + (1.0 - t0) * (c + rng.uniform(0.15, 0.85)) / count
        i = min(n - 2, int(t * (n - 1)))
        tan = vine.tangent(i)
        phi += GOLDEN_ANGLE + rng.uniform(-0.3, 0.3)
        a = any_perpendicular(tan)
        b = tan.cross(a)
        side = a * math.cos(phi) + b * math.sin(phi)
        tilt = math.radians(s.branch_angle * rng.uniform(0.7, 1.3))
        cd = tan * math.cos(tilt) + side * math.sin(tilt)
        clen = length * s.length_ratio * rng.uniform(0.7, 1.2) * (1.2 - 0.5 * t)  # 根元側ほど長い
        cr = min(vine.radius(i) * 0.9, vine.radius(i) * s.radius_ratio * rng.uniform(0.75, 1.1))
        add_branch(rng, s, pts[i], cd, clen, cr, depth + 1, dmax, 0.15, out)
    # 先端の延長枝 (頂芽の伸長)
    tip_dir = (vine.tangent(n - 1) + random_unit(rng) * 0.25).normalized()
    add_branch(rng, s, pts[-1], tip_dir, length * s.length_ratio * rng.uniform(0.8, 1.15),
               vine.r1, depth + 1, dmax, 0.15, out)


def build_plant(rng, s, base, height, out):
    """1 株のいばら。高木型 (1 本の幹 + 樹冠) か株立ち型 (根元から複数の幹)。"""
    rel = height / max(s.max_height, 1e-6)
    dmax = max(1, s.branch_levels - (0 if rel > 0.6 else 1 if rel > 0.3 else 2))
    r_base = s.trunk_radius * (height / 4.0) ** 1.2
    if rng.random() < s.tree_ratio:
        stems = [(rng.uniform(0, 8), height * rng.uniform(0.35, 0.5), r_base, 0.5)]
    else:
        k = max(1, s.stems + rng.randint(-1, 1))
        r = r_base * math.sqrt(1.6 / k)  # 断面積の合計がおおよそ保たれるように
        stems = [(rng.uniform(8, 35), height * rng.uniform(0.45, 0.8), r * rng.uniform(0.7, 1.15), 0.2)
                 for _ in range(k)]
    for lean, length, r0, t0 in stems:
        az = rng.uniform(0, math.tau)
        tilt = math.radians(lean)
        d = Vector((math.cos(az) * math.sin(tilt), math.sin(az) * math.sin(tilt), math.cos(tilt)))
        off = Vector((math.cos(az), math.sin(az), 0.0)) * (r_base * rng.uniform(0.5, 2.0))
        add_branch(rng, s, base + off, d, length, r0, 0, dmax, t0, out)


def place_plants(rng, s):
    """大きさをばらつかせた株を、大きい順に樹冠の重なりを避けながら配置する。"""
    lo, hi = sorted((s.min_height, s.max_height))
    heights = sorted((lo + (hi - lo) * rng.random() ** s.small_bias for _ in range(s.plant_count)), reverse=True)
    half = s.area_size * 0.5
    placed = []
    for h in heights:
        for _ in range(40):
            c = Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0))
            if all((c - q).length >= s.spacing * 0.35 * (h + qh) for q, qh in placed):
                placed.append((c, h))
                break
    return placed


def build_vines(s):
    rng = random.Random(s.seed)
    noise.seed_set(s.seed)  # ノイズ表はプロセスごとに乱数初期化されるので固定する
    vines = []
    for base, height in place_plants(rng, s):
        build_plant(rng, s, base, height, vines)
    return [v for v in vines if len(v.points) >= 4]


def sample_along(rng, vine, density):
    """密度 (個/m) に従って枝上の位置 (index, 補間位置, 0..1 の t) を返す。"""
    if density <= 0:
        return
    pts = vine.points
    spacing = 1.0 / density
    dist_to_next = rng.uniform(0.2, 1.0) * spacing
    for i in range(len(pts) - 1):
        seg = (pts[i + 1] - pts[i]).length
        pos = 0.0
        while pos + dist_to_next <= seg:
            pos += dist_to_next
            f = pos / seg if seg > 0 else 0.0
            yield i, pts[i].lerp(pts[i + 1], f), (i + f) / (len(pts) - 1)
            dist_to_next = spacing * rng.uniform(0.5, 1.5)
        dist_to_next -= seg - pos


class MeshBuilder:
    def __init__(self):
        self.verts = []
        self.faces = []

    def add(self, verts, faces):
        o = len(self.verts)
        self.verts.extend(verts)
        self.faces.extend(tuple(i + o for i in f) for f in faces)


def add_thorn(mb, base, axis, back, length, radius, hook, sides=5, rings=4):
    """根元 base から axis 方向に伸び、back 方向へ鉤状に反る円錐。"""
    verts, faces = [], []
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
    tip = base + axis * length + back * (length * hook)
    verts.append(tip)
    for j in range(rings - 1):
        for k in range(sides):
            k2 = (k + 1) % sides
            faces.append((j * sides + k, j * sides + k2, (j + 1) * sides + k2, (j + 1) * sides + k))
    last = (rings - 1) * sides
    t = len(verts) - 1
    for k in range(sides):
        faces.append((last + k, last + (k + 1) % sides, t))
    mb.add(verts, faces)


def build_thorns(rng, s, vines):
    """枝 (幹以外) に細いトゲを付ける。トゲは枝の先端側へ斜めに出る。"""
    mb = MeshBuilder()
    if s.thorn_density <= 0:
        return mb
    for v in vines:
        if v.depth == 0:
            continue
        for i, p, t in sample_along(rng, v, s.thorn_density):
            r = v.radius(i)
            tan = v.tangent(i)
            side = tan.cross(random_unit(rng))
            if side.length < 1e-6:
                continue
            side.normalize()
            length = s.thorn_size * rng.uniform(0.6, 1.4)
            if t > 0.9:  # 先端付近は小さく
                length *= 0.6
            axis = (side + tan * rng.uniform(0.3, 0.8)).normalized()
            add_thorn(mb, p + side * (r * 0.6), axis, -tan, length,
                      min(r * 0.8, length * 0.16), s.thorn_hook * rng.uniform(0.5, 1.5),
                      sides=3, rings=2)
    return mb


# ---------------------------------------------------------------------------
# Blender オブジェクト / マテリアル
# ---------------------------------------------------------------------------

def get_collection(clear):
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    elif clear:
        for obj in list(col.objects):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
                elif isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
    return col


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


def _noise_color(nt, bsdf, c1, c2, scale, bump_strength):
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = scale
    tex.inputs["Detail"].default_value = 8.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*c1, 1.0)
    ramp.color_ramp.elements[1].color = (*c2, 1.0)
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    if bump_strength > 0:
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = bump_strength
        nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def get_material(name, color, roughness=0.7, noise_to=None, noise_scale=30.0, bump=0.0):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    nt, bsdf = _principled(mat)
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    if noise_to is not None:
        _noise_color(nt, bsdf, color, noise_to, noise_scale, bump)
    return mat


def materials():
    return {
        "bark": get_material("TF_Bark", (0.035, 0.03, 0.027), 0.8, (0.08, 0.07, 0.06), 25.0, 0.5),
        "twig": get_material("TF_Twig", (0.10, 0.07, 0.045), 0.6, (0.22, 0.15, 0.09), 60.0),
        "thorn": get_material("TF_Thorn", (0.16, 0.11, 0.07), 0.45, (0.06, 0.04, 0.03), 200.0),
        "ground": get_material("TF_Ground", (0.02, 0.018, 0.016), 0.95, (0.07, 0.05, 0.035), 2.0, 0.3),
    }


def link_object(col, name, data, mat):
    obj = bpy.data.objects.new(name, data)
    if mat is not None:
        data.materials.append(mat)
    col.objects.link(obj)
    return obj


def make_mesh_object(col, name, mb, mat, smooth=True):
    if not mb.verts:
        return None
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in mb.verts], [], mb.faces)
    me.validate()
    me.update()
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    return link_object(col, name, me, mat)


def make_vine_curve(col, name, vines, mat, bevel_resolution=2, resolution_u=3):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.fill_mode = "FULL"
    cu.bevel_depth = 1.0  # 半径は各制御点の radius で指定
    cu.bevel_resolution = bevel_resolution
    cu.resolution_u = resolution_u
    cu.use_fill_caps = True
    for v in vines:
        sp = cu.splines.new("NURBS")
        sp.points.add(len(v.points) - 1)
        co, radius = [], []
        for i, p in enumerate(v.points):
            co.extend((p.x, p.y, p.z, 1.0))
            radius.append(v.radius(i))
        sp.points.foreach_set("co", co)
        sp.points.foreach_set("radius", radius)
        sp.order_u = 4
        sp.use_endpoint_u = True
        sp.use_smooth = True
    return link_object(col, name, cu, mat)


def curve_to_mesh(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
    name, cu, cols = obj.name, obj.data, list(obj.users_collection)
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.curves.remove(cu)
    me.name = name
    obj_new = bpy.data.objects.new(name, me)
    for col in cols:
        col.objects.link(obj_new)
    return obj_new


def make_ground(col, s, mat):
    half = s.area_size * 1.5
    me = bpy.data.meshes.new("TF_Ground")
    me.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    return link_object(col, "TF_Ground", me, mat)


def setup_atmosphere(col, s):
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    nt = world.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None) or nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.045, 0.05, 0.06, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # 低い角度の暖かい日差し
    light = bpy.data.lights.new("TF_Sun", "SUN")
    light.energy = 4.0
    light.color = (1.0, 0.78, 0.55)
    light.angle = math.radians(2)
    sun = bpy.data.objects.new("TF_Sun", light)
    sun.rotation_euler = (math.radians(65), 0.0, math.radians(35))
    col.objects.link(sun)

    # 霧: ワールドボリュームは太陽光を完全に遮ってしまうため、森を覆う箱に入れる
    if s.fog_density > 0:
        half = s.area_size * 1.5
        top = max(s.max_height * 1.5, 4.0)
        mb = MeshBuilder()
        mb.add(
            # 底面を地面と同じ高さにすると境界が地面と重なって灰色に濁るので少し下げる
            [Vector((x, y, z)) for z in (-1.0, top) for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))],
            [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
        )
        mat = bpy.data.materials.get("TF_Fog") or bpy.data.materials.new("TF_Fog")
        if mat.node_tree is None:
            mat.use_nodes = True
        mnt = mat.node_tree
        mnt.nodes.clear()
        mout = mnt.nodes.new("ShaderNodeOutputMaterial")
        vol = mnt.nodes.new("ShaderNodeVolumePrincipled")
        vol.inputs["Color"].default_value = (0.6, 0.6, 0.62, 1.0)
        vol.inputs["Anisotropy"].default_value = 0.4
        # 地表で濃く、箱の高さ 50% で消える高さ霧
        coord = mnt.nodes.new("ShaderNodeTexCoord")
        xyz = mnt.nodes.new("ShaderNodeSeparateXYZ")
        fade = mnt.nodes.new("ShaderNodeMapRange")
        fade.inputs["From Min"].default_value = 1.0 / (top + 1.0)
        fade.inputs["From Max"].default_value = 0.5
        fade.inputs["To Min"].default_value = s.fog_density
        fade.inputs["To Max"].default_value = 0.0
        mnt.links.new(coord.outputs["Generated"], xyz.inputs["Vector"])
        mnt.links.new(xyz.outputs["Z"], fade.inputs["Value"])
        mnt.links.new(fade.outputs["Result"], vol.inputs["Density"])
        mnt.links.new(vol.outputs["Volume"], mout.inputs["Volume"])
        fog = make_mesh_object(col, "TF_Fog", mb, mat, smooth=False)
        fog.display_type = "BOUNDS"
        fog.hide_select = True


def generate(s):
    """設定オブジェクト s から茨の森を生成し、作成したオブジェクトのリストを返す。"""
    col = get_collection(s.clear_previous)
    mats = materials()
    vines = build_vines(s)
    rng = random.Random(s.seed * 7919 + 13)
    created = []
    # 幹と太い枝 / 細い小枝 で分け、小枝は断面を粗くして軽くする
    groups = (
        ("TF_Branches", [v for v in vines if v.depth <= 1], mats["bark"], 2, 3),
        ("TF_Twigs", [v for v in vines if v.depth >= 2], mats["twig"], 0, 2),
    )
    for name, group, mat, bevel_res, res_u in groups:
        if not group:
            continue
        obj = make_vine_curve(col, name, group, mat, bevel_res, res_u)
        if s.convert_to_mesh:
            obj = curve_to_mesh(obj)
        created.append(obj)
    obj = make_mesh_object(col, "TF_Thorns", build_thorns(rng, s, vines), mats["thorn"])
    if obj is not None:
        created.append(obj)
    if s.ground:
        created.append(make_ground(col, s, mats["ground"]))
    if s.atmosphere:
        setup_atmosphere(col, s)
    return created


def build_forest(**overrides):
    """Python コンソール等から: build_forest(seed=3, plant_count=50)"""
    return generate(default_settings(**overrides))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _make_property(spec):
    kind, default, lo, hi, label, desc = spec
    if kind == "bool":
        return BoolProperty(name=label, description=desc, default=default)
    if kind == "int":
        return IntProperty(name=label, description=desc, default=default, min=lo, max=hi)
    return FloatProperty(name=label, description=desc, default=default, min=lo, max=hi)


ThornForestSettings = type(
    "ThornForestSettings",
    (bpy.types.PropertyGroup,),
    {"__annotations__": {name: _make_property(spec) for name, spec in SETTINGS.items()}},
)


class THORNFOREST_OT_generate(bpy.types.Operator):
    bl_idname = "object.thorn_forest_generate"
    bl_label = "茨の森を生成"
    bl_description = "設定に従って茨の森を生成する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        objs = generate(context.scene.thorn_forest)
        info = ", ".join(f"{o.name}: {len(o.data.vertices) if hasattr(o.data, 'vertices') else len(o.data.splines)}"
                         for o in objs)
        self.report({"INFO"}, f"茨の森を生成しました ({info})")
        return {"FINISHED"}


class THORNFOREST_OT_randomize(bpy.types.Operator):
    bl_idname = "object.thorn_forest_randomize"
    bl_label = "シードを変えて再生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.thorn_forest.seed = random.randint(0, 999_999)
        return bpy.ops.object.thorn_forest_generate()


PANEL_LAYOUT = (
    ("全体", ("seed", "area_size", "plant_count", "spacing", "step")),
    ("大きさのばらつき", ("min_height", "max_height", "small_bias")),
    ("樹形", ("tree_ratio", "stems", "trunk_radius")),
    ("枝", ("branch_levels", "branch_count", "branch_angle", "length_ratio", "radius_ratio",
           "up_tendency", "wiggle", "wave_count", "crookedness", "droop")),
    ("トゲ", ("thorn_density", "thorn_size", "thorn_hook")),
    ("シーン", ("ground", "atmosphere", "fog_density", "convert_to_mesh", "clear_previous")),
)


class THORNFOREST_PT_panel(bpy.types.Panel):
    bl_label = "茨の森"
    bl_idname = "THORNFOREST_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "茨の森"

    def draw(self, context):
        s = context.scene.thorn_forest
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator(THORNFOREST_OT_generate.bl_idname, icon="OUTLINER_OB_CURVE")
        layout.operator(THORNFOREST_OT_randomize.bl_idname, icon="FILE_REFRESH")
        for title, names in PANEL_LAYOUT:
            box = layout.box()
            box.label(text=title)
            colm = box.column(align=True)
            for name in names:
                colm.prop(s, name)


CLASSES = (ThornForestSettings, THORNFOREST_OT_generate, THORNFOREST_OT_randomize, THORNFOREST_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.thorn_forest = PointerProperty(type=ThornForestSettings)


def unregister():
    del bpy.types.Scene.thorn_forest
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


# ---------------------------------------------------------------------------
# ヘッドレス実行: blender -b -P thorn_forest.py -- [options]
# ---------------------------------------------------------------------------

def _cli(argv):
    import argparse

    parser = argparse.ArgumentParser(prog="thorn_forest.py", description="茨の森を生成する")
    for name, (kind, default, *_rest) in SETTINGS.items():
        flag = "--" + name.replace("_", "-")
        if kind == "bool":
            parser.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes", "on"), default=default)
        else:
            parser.add_argument(flag, type=int if kind == "int" else float, default=default)
    parser.add_argument("--save", help="生成後に .blend として保存するパス")
    parser.add_argument("--render", help="カメラを置いてレンダリングする画像パス (.png)")
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--view", choices=("side", "aerial"), default="side", help="レンダリングの視点")
    args = parser.parse_args(argv)

    # 起動シーンの既定オブジェクト (Cube / Light / Camera) を片付ける
    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    s = default_settings(**{k: getattr(args, k) for k in SETTINGS})
    objs = generate(s)
    for o in objs:
        n = len(o.data.vertices) if hasattr(o.data, "vertices") else len(o.data.splines)
        print(f"[thorn_forest] {o.name}: {n}")

    if args.render:
        scene = bpy.context.scene
        cam_data = bpy.data.cameras.new("TF_Camera")
        cam_data.lens = 28
        cam = bpy.data.objects.new("TF_Camera", cam_data)
        scene.collection.objects.link(cam)
        if args.view == "aerial":
            cam_data.lens = 35
            cam.location = (0.0, 0.0, s.area_size * 1.05)
            cam.rotation_euler = (0.0, 0.0, 0.0)
        else:
            r = s.area_size * 0.62
            h = s.max_height * 0.6
            cam.location = (r, -r, h * 0.9)
            look = Vector((0, 0, h * 0.35)) - cam.location
            cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
        scene.camera = cam
        scene.render.engine = "CYCLES"
        scene.cycles.samples = args.samples
        scene.cycles.device = "CPU"
        scene.render.resolution_x = 1280
        scene.render.resolution_y = 720
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=args.save)


if __name__ == "__main__":
    if "--" in sys.argv or bpy.app.background:
        _cli(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    else:
        register()
