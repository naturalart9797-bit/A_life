# SPDX-License-Identifier: MIT
"""
茨の森ジェネレーター (Thorn Forest Generator) for Blender

地を這って広がる茨と上へ伸びる茨の 2 種類の茎と、鉤状のトゲを
手続き生成する Blender アドオン。

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
    "version": (1, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > 茨の森",
    "description": "地を這う茨と上へ伸びる茨・トゲを手続き生成する",
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
    "area_size": ("float", 24.0, 2.0, 500.0, "範囲", "森を生成する正方形の一辺 (m)"),
    "creeper_count": ("int", 70, 0, 2000, "這う茨の数", "地面を這って広がる茨の本数"),
    "creeper_length": ("float", 8.0, 0.5, 50.0, "這う茨の長さ", "地を這う茎の長さ (m)"),
    "creeper_radius": ("float", 0.035, 0.003, 1.0, "這う茨の太さ", "地を這う茎の根元の半径 (m)"),
    "creeper_lift": ("float", 0.3, 0.0, 3.0, "浮き上がり", "地を這う茎がところどころ地面から浮く高さ (m)"),
    "upright_count": ("int", 40, 0, 2000, "伸びる茨の数", "上へ伸びる茨の本数"),
    "min_spacing": ("float", 1.5, 0.0, 50.0, "最小間隔", "上へ伸びる茨どうしの最小距離 (m)"),
    "upright_height": ("float", 5.0, 0.5, 50.0, "伸びる茨の高さ", "上へ伸びる茎の長さ (m)"),
    "upright_radius": ("float", 0.05, 0.003, 1.0, "伸びる茨の太さ", "上へ伸びる茎の根元の半径 (m)"),
    "upright_lean": ("float", 10.0, 0.0, 60.0, "傾き", "上へ伸びる茎が鉛直から傾く最大角度 (度)"),
    "branch_levels": ("int", 2, 0, 4, "枝の階層", "枝分かれの深さ"),
    "branches_per_vine": ("float", 1.2, 0.0, 10.0, "枝の数", "茎 1 m あたりの枝の数"),
    "branch_ratio": ("float", 0.5, 0.05, 1.0, "枝の長さ比", "親の残り長さに対する枝の長さ"),
    "wiggle": ("float", 0.45, 0.0, 3.0, "うねり", "茎の曲がりの強さ"),
    "droop": ("float", 1.0, 0.0, 3.0, "垂れ下がり", "茎が先端に向かって垂れる強さ"),
    "step": ("float", 0.1, 0.02, 1.0, "解像度", "茎のサンプリング間隔 (m)。小さいほど滑らかで重い"),
    "thorn_density": ("float", 7.0, 0.0, 60.0, "トゲ密度", "1 m あたりのトゲの数"),
    "thorn_size": ("float", 0.09, 0.005, 1.0, "トゲの長さ", "最も太い茎でのトゲの長さ (m)"),
    "thorn_hook": ("float", 0.5, -1.0, 2.0, "トゲの反り", "トゲが根元側へ鉤状に曲がる量"),
    "ground": ("bool", True, None, None, "地面", "地面を作成する"),
    "atmosphere": ("bool", True, None, None, "雰囲気", "霧・月光・暗い空を設定する"),
    "fog_density": ("float", 0.025, 0.0, 1.0, "霧の濃さ", "地表付近の霧の密度 (上空ほど薄くなる)"),
    "convert_to_mesh": ("bool", False, None, None, "メッシュに変換", "茎のカーブをメッシュに変換する (書き出し用)"),
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
    """太さが先細りする 1 本の茎 (折れ線)。"""

    __slots__ = ("points", "r0", "r1")

    def __init__(self, points, r0, r1=None):
        self.points = points
        self.r0 = r0
        self.r1 = r0 * 0.12 if r1 is None else r1

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


def grow(rng, start, direction, length, s, up_bias=0.0, droop=None, wiggle=None):
    """方向を少しずつ曲げながら成長する茎の点列を返す。

    なめらかなうねり (低周波ノイズ)・節ごとの小さな折れ・前半の上向きの成長・
    後半の重力による垂れを合成する。地面に着いたらそこで止まる。
    """
    droop = s.droop if droop is None else droop
    wiggle = s.wiggle if wiggle is None else wiggle
    step = s.step
    n = max(4, int(length / step))
    d = direction.normalized()
    p = start.copy()
    offset = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))
    node = rng.uniform(0.15, 0.4)
    walked = 0.0
    pts = [p.copy()]
    for i in range(n):
        t = i / n
        turn = noise.noise_vector(p * 0.35 + offset) * wiggle * 1.6
        turn += UP * (up_bias * (1.0 - t) * 2.0)
        turn -= UP * (droop * t * 2.2)
        d = (d + turn * step).normalized()
        walked += step
        if walked >= node:  # 節: 向きがわずかに変わる
            walked = 0.0
            node = rng.uniform(0.15, 0.4)
            d = (d + random_unit(rng) * wiggle * 0.12).normalized()
        p = p + d * step
        if p.z <= 0.0:
            if i < 3:  # 生え際は地面から持ち上げる
                p.z = 0.01
                d.z = abs(d.z)
                d.normalize()
            else:
                p.z = 0.0
                pts.append(p.copy())
                break
        pts.append(p.copy())
    return pts


def poisson_points(rng, count, half, min_dist, tries=30):
    pts = []
    for _ in range(count):
        for _ in range(tries):
            c = Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0))
            if all((c - q).length >= min_dist for q in pts):
                pts.append(c)
                break
    return pts


HUMP_OFFSET = Vector((17.3, 41.7, 5.9))


def creep(rng, start, heading, length, s, z0):
    """地面を這う茎の点列。水平面内で向きを変えながら進み、
    ところどころ (地形のように共通のノイズ場で) 地面から弧状に浮く。"""
    step = s.step
    n = max(4, int(length / step))
    x, y = start.x, start.y
    off = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), 0.0))
    pts = []
    for i in range(n + 1):
        if i:
            # 進行方向は元の向きのまわりで蛇行させる (累積させると渦を巻いてしまう)
            a = heading + noise.noise(Vector((i * step * 0.5, 0.0, 0.0)) + off) * s.wiggle * 2.6
            a += rng.gauss(0.0, s.wiggle * 0.04)
            x += math.cos(a) * step
            y += math.sin(a) * step
        h = noise.noise(Vector((x * 0.45, y * 0.45, 0.0)) + HUMP_OFFSET)
        z = z0 + s.creeper_lift * min(1.0, max(0.0, h) * 1.8) ** 1.5
        pts.append(Vector((x, y, z)))
    return pts


def spawn_branches(rng, s, vine, level, out, creeping):
    if level >= s.branch_levels or s.branches_per_vine <= 0:
        return
    n = len(vine.points)
    if n < 6:
        return
    total = vine.length()
    count = int(s.branches_per_vine * total + rng.random())
    for _ in range(count):
        t = rng.uniform(0.2, 0.9)
        i = min(n - 2, int(t * (n - 1)))
        p = vine.points[i]
        tan = vine.tangent(i)
        length = total * (1.0 - t) * s.branch_ratio * rng.uniform(0.7, 1.2)
        if length < s.step * 4:
            continue
        r = vine.radius(i) * 0.6
        if creeping:
            heading = math.atan2(tan.y, tan.x) + rng.choice((-1, 1)) * math.radians(rng.uniform(30.0, 70.0))
            pts = creep(rng, p, heading, length, s, r)
        else:
            side = tan.cross(random_unit(rng))
            side = side.normalized() if side.length > 1e-6 else any_perpendicular(tan)
            ang = math.radians(rng.uniform(25.0, 60.0))
            d = tan * math.cos(ang) + side * math.sin(ang)
            pts = grow(rng, p.copy(), d, length, s, up_bias=0.0, droop=s.droop * 1.2)
        child = Vine(pts, r)
        out.append(child)
        spawn_branches(rng, s, child, level + 1, out, creeping)


def build_creeper(rng, s, base, out):
    """地を這う茨: 1 本の茎が地面を這って進み、横へ這う枝を出す。"""
    r = s.creeper_radius * rng.uniform(0.7, 1.3)
    pts = creep(rng, base, rng.uniform(0, math.tau), s.creeper_length * rng.uniform(0.6, 1.3), s, r)
    vine = Vine(pts, r)
    out.append(vine)
    spawn_branches(rng, s, vine, 0, out, creeping=True)


def build_upright(rng, s, base, out):
    """上へ伸びる茨: ほぼ鉛直に伸び、先端がわずかにしなる。枝は斜め上へ出て垂れる。"""
    az = rng.uniform(0, math.tau)
    tilt = math.radians(rng.uniform(0.0, s.upright_lean))
    d = Vector((math.cos(az) * math.sin(tilt), math.sin(az) * math.sin(tilt), math.cos(tilt)))
    pts = grow(rng, base, d, s.upright_height * rng.uniform(0.7, 1.3), s,
               up_bias=0.8, droop=s.droop * 0.25)
    vine = Vine(pts, s.upright_radius * rng.uniform(0.75, 1.25))
    out.append(vine)
    spawn_branches(rng, s, vine, 0, out, creeping=False)


def build_vines(s):
    rng = random.Random(s.seed)
    noise.seed_set(s.seed)  # ノイズ表はプロセスごとに乱数初期化されるので固定する
    half = s.area_size * 0.5
    vines = []
    for base in poisson_points(rng, s.upright_count, half * 0.95, s.min_spacing):
        build_upright(rng, s, base, vines)
    for _ in range(s.creeper_count):
        build_creeper(rng, s, Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0)), vines)
    return [v for v in vines if len(v.points) >= 4]


def sample_along(rng, vine, density):
    """密度 (個/m) に従って茎上の位置 (index, 補間位置, 0..1 の t) を返す。"""
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
    mb = MeshBuilder()
    if s.thorn_density <= 0:
        return mb
    max_r = max(v.r0 for v in vines) if vines else 1.0
    for v in vines:
        for i, p, t in sample_along(rng, v, s.thorn_density):
            r = v.radius(i)
            tan = v.tangent(i)
            side = tan.cross(random_unit(rng))
            if side.length < 1e-6:
                continue
            side.normalize()
            rel = r / max_r
            length = s.thorn_size * (0.35 + 0.65 * rel ** 0.5) * rng.uniform(0.7, 1.3)
            if t > 0.97:  # 先端付近は小さく
                length *= 0.5
            base_r = min(r * 0.7, length * 0.35)
            # トゲは少し先端側へ傾き、根元側 (-tan) へ鉤状に反る
            axis = (side + tan * 0.35).normalized()
            add_thorn(mb, p + side * (r * 0.75), axis, -tan, length,
                      base_r, s.thorn_hook * rng.uniform(0.6, 1.2))
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
        "bark": get_material("TF_Bark", (0.035, 0.03, 0.018), 0.75, (0.075, 0.055, 0.03), 40.0, 0.4),
        "thorn": get_material("TF_Thorn", (0.10, 0.03, 0.02), 0.35, (0.03, 0.012, 0.01), 120.0),
        "ground": get_material("TF_Ground", (0.025, 0.02, 0.015), 0.95, (0.05, 0.045, 0.03), 3.0, 0.3),
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


def make_vine_curve(col, vines, mat):
    cu = bpy.data.curves.new("TF_Vines", "CURVE")
    cu.dimensions = "3D"
    cu.fill_mode = "FULL"
    cu.bevel_depth = 1.0  # 半径は各制御点の radius で指定
    cu.bevel_resolution = 2
    cu.resolution_u = 3
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
    return link_object(col, "TF_Vines", cu, mat)


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
    half = s.area_size * 0.5 + 2.0
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
    bg.inputs["Color"].default_value = (0.012, 0.016, 0.03, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    light = bpy.data.lights.new("TF_Moon", "SUN")
    light.energy = 3.0
    light.color = (0.7, 0.8, 1.0)
    light.angle = math.radians(3)
    moon = bpy.data.objects.new("TF_Moon", light)
    moon.rotation_euler = (math.radians(55), 0.0, math.radians(35))
    col.objects.link(moon)

    # 霧: ワールドボリュームは太陽光を完全に遮ってしまうため、森を覆う箱に入れる
    if s.fog_density > 0:
        half = s.area_size * 0.5 + 2.0
        top = max(s.upright_height * 1.5, 4.0)
        mb = MeshBuilder()
        mb.add(
            [Vector((x, y, z)) for z in (0.0, top) for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))],
            [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
        )
        mat = bpy.data.materials.get("TF_Fog") or bpy.data.materials.new("TF_Fog")
        if mat.node_tree is None:
            mat.use_nodes = True
        mnt = mat.node_tree
        mnt.nodes.clear()
        mout = mnt.nodes.new("ShaderNodeOutputMaterial")
        vol = mnt.nodes.new("ShaderNodeVolumePrincipled")
        vol.inputs["Color"].default_value = (0.55, 0.6, 0.7, 1.0)
        vol.inputs["Anisotropy"].default_value = 0.4
        # 地表で濃く、箱の高さ 50% で消える高さ霧
        coord = mnt.nodes.new("ShaderNodeTexCoord")
        xyz = mnt.nodes.new("ShaderNodeSeparateXYZ")
        fade = mnt.nodes.new("ShaderNodeMapRange")
        fade.inputs["From Min"].default_value = 0.0
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
    if vines:
        obj = make_vine_curve(col, vines, mats["bark"])
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
    """Python コンソール等から: build_forest(seed=3, tree_count=12)"""
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
    ("全体", ("seed", "area_size", "step")),
    ("地を這う茨", ("creeper_count", "creeper_length", "creeper_radius", "creeper_lift")),
    ("上へ伸びる茨", ("upright_count", "min_spacing", "upright_height", "upright_radius", "upright_lean")),
    ("茎の形", ("branch_levels", "branches_per_vine", "branch_ratio", "wiggle", "droop")),
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
        r = s.area_size * 0.62
        h = s.upright_height * 0.6
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
