# SPDX-License-Identifier: MIT
"""
茨の森ジェネレーター (Thorn Forest Generator) for Blender

ねじれた茨の幹・アーチを描いて垂れ下がる蔓・地を這う藪・鉤状のトゲ・
葉・薔薇をまとめて手続き生成する Blender アドオン。

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
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > 茨の森",
    "description": "ねじれた茨の木・藪・トゲ・薔薇からなる茨の森を手続き生成する",
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
    "tree_count": ("int", 9, 0, 200, "茨の木の数", "ねじれた幹を持つ茨の木の本数"),
    "min_spacing": ("float", 3.0, 0.0, 50.0, "最小間隔", "茨の木どうしの最小距離 (m)"),
    "trunk_height": ("float", 5.0, 0.5, 50.0, "幹の高さ", "幹の長さ (m)"),
    "trunk_strands": ("int", 4, 1, 12, "幹の本数", "1 本の幹を構成するねじれた蔓の数"),
    "trunk_radius": ("float", 0.28, 0.0, 5.0, "幹の太さ", "蔓が巻きつく螺旋の半径 (m)"),
    "strand_radius": ("float", 0.09, 0.005, 2.0, "蔓の太さ", "幹を構成する蔓の根元半径 (m)"),
    "twist": ("float", 1.5, 0.0, 10.0, "ねじれ", "幹全体での巻き数"),
    "crown_length": ("float", 4.0, 0.0, 30.0, "樹冠の蔓の長さ", "幹の先端から垂れ下がる蔓の長さ (m)"),
    "branch_levels": ("int", 2, 0, 4, "枝の階層", "枝分かれの深さ"),
    "branches_per_vine": ("int", 3, 0, 12, "枝の数", "1 本の蔓から出る枝の数"),
    "branch_ratio": ("float", 0.55, 0.05, 1.0, "枝の長さ比", "親の残り長さに対する枝の長さ"),
    "bramble_count": ("int", 35, 0, 2000, "藪の数", "地面を這ってアーチを描く茨の株の数"),
    "bramble_length": ("float", 4.5, 0.2, 30.0, "藪の蔓の長さ", "藪の蔓の長さ (m)"),
    "bramble_canes": ("int", 6, 1, 20, "株あたりの蔓", "藪 1 株から伸びる蔓の数"),
    "wiggle": ("float", 0.6, 0.0, 3.0, "うねり", "蔓のうねりの強さ"),
    "droop": ("float", 0.9, 0.0, 3.0, "垂れ下がり", "蔓が先端に向かって垂れる強さ"),
    "step": ("float", 0.12, 0.02, 1.0, "解像度", "蔓のサンプリング間隔 (m)。小さいほど滑らかで重い"),
    "thorn_density": ("float", 7.0, 0.0, 60.0, "トゲ密度", "1 m あたりのトゲの数"),
    "thorn_size": ("float", 0.09, 0.005, 1.0, "トゲの長さ", "最も太い蔓でのトゲの長さ (m)"),
    "thorn_hook": ("float", 0.5, -1.0, 2.0, "トゲの反り", "トゲが根元側へ鉤状に曲がる量"),
    "leaves": ("bool", True, None, None, "葉", "細い蔓に葉を付ける"),
    "leaf_density": ("float", 3.0, 0.0, 40.0, "葉の密度", "細い蔓 1 m あたりの葉の数"),
    "leaf_size": ("float", 0.09, 0.005, 1.0, "葉の大きさ", "葉の長さ (m)"),
    "roses": ("bool", True, None, None, "薔薇", "蔓の先端に薔薇を咲かせる"),
    "rose_count": ("int", 25, 0, 2000, "薔薇の数", "咲かせる薔薇の数"),
    "rose_size": ("float", 0.12, 0.01, 2.0, "薔薇の大きさ", "薔薇の半径 (m)"),
    "ground": ("bool", True, None, None, "地面", "地面を作成する"),
    "atmosphere": ("bool", True, None, None, "雰囲気", "霧・月光・暗い空を設定する"),
    "fog_density": ("float", 0.025, 0.0, 1.0, "霧の濃さ", "地表付近の霧の密度 (上空ほど薄くなる)"),
    "convert_to_mesh": ("bool", False, None, None, "メッシュに変換", "蔓のカーブをメッシュに変換する (書き出し用)"),
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
    """太さが先細りする 1 本の蔓 (折れ線)。"""

    __slots__ = ("points", "r0", "r1", "center")

    def __init__(self, points, r0, r1=None, center=None):
        self.points = points
        self.r0 = r0
        self.r1 = r0 * 0.12 if r1 is None else r1
        # 幹の蔓のときは螺旋の中心線 (枝を外向きに出すため)
        self.center = center

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
    """方向を少しずつ曲げながら成長する蔓の点列を返す。

    うねり (ノイズ場)・上向きの成長 (前半)・重力による垂れ (後半) を合成し、
    地面に当たったら地表を這わせる。
    """
    droop = s.droop if droop is None else droop
    wiggle = s.wiggle if wiggle is None else wiggle
    step = s.step
    n = max(4, int(length / step))
    d = direction.normalized()
    p = start.copy()
    offset = Vector((rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)))
    pts = [p.copy()]
    for i in range(n):
        t = i / n
        turn = noise.noise_vector(p * 0.7 + offset) * wiggle * 1.6
        turn += UP * (up_bias * (1.0 - t) * 2.0)
        turn -= UP * (droop * t * 2.2)
        d = (d + turn * step).normalized()
        p = p + d * step
        if p.z < 0.02:  # 地面: 這わせる
            p.z = 0.02
            if d.z < 0.0:
                d.z = abs(d.z) * 0.3
                d.normalize()
        pts.append(p.copy())
    return pts


def parallel_transport_frames(pts):
    """点列に沿ってねじれの少ない (tangent, normal, binormal) を返す。"""
    n = len(pts)
    tangents = []
    for i in range(n):
        d = pts[min(n - 1, i + 1)] - pts[max(0, i - 1)]
        tangents.append(d.normalized() if d.length > 1e-9 else UP.copy())
    normal = any_perpendicular(tangents[0])
    frames = []
    prev = tangents[0]
    for t in tangents:
        normal = prev.rotation_difference(t) @ normal
        normal = (normal - t * normal.dot(t)).normalized()
        frames.append((t, normal, t.cross(normal)))
        prev = t
    return frames


def poisson_points(rng, count, half, min_dist, tries=30):
    pts = []
    for _ in range(count):
        for _ in range(tries):
            c = Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0))
            if all((c - q).length >= min_dist for q in pts):
                pts.append(c)
                break
    return pts


def spawn_branches(rng, s, vine, level, out):
    if level >= s.branch_levels or s.branches_per_vine <= 0:
        return
    n = len(vine.points)
    if n < 6:
        return
    total = vine.length()
    for _ in range(s.branches_per_vine):
        t = rng.uniform(0.2, 0.9)
        i = min(n - 2, int(t * (n - 1)))
        p = vine.points[i]
        tan = vine.tangent(i)
        if vine.center is not None:
            side = p - vine.center[i]
            side = (side - tan * side.dot(tan))
            side = side.normalized() if side.length > 1e-6 else any_perpendicular(tan)
        else:
            side = tan.cross(random_unit(rng))
            side = side.normalized() if side.length > 1e-6 else any_perpendicular(tan)
        ang = math.radians(rng.uniform(30.0, 70.0))
        d = tan * math.cos(ang) + side * math.sin(ang)
        length = total * (1.0 - t) * s.branch_ratio * rng.uniform(0.7, 1.2)
        if length < s.step * 4:
            continue
        child = Vine(grow(rng, p.copy(), d, length, s, up_bias=0.15), vine.radius(i) * 0.6)
        out.append(child)
        spawn_branches(rng, s, child, level + 1, out)


def build_tree(rng, s, base):
    """ねじれた複数の蔓からなる幹 + 垂れ下がる樹冠を作る。"""
    vines = []
    lean = Vector((rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25), 1.0))
    height = s.trunk_height * rng.uniform(0.75, 1.25)
    spine = grow(rng, base, lean, height, s, up_bias=0.6, droop=0.0, wiggle=s.wiggle * 0.5)
    frames = parallel_transport_frames(spine)
    m = len(spine)
    strands = max(1, s.trunk_strands)
    phase0 = rng.uniform(0, math.tau)
    for k in range(strands):
        phase = phase0 + math.tau * k / strands
        pts, center = [], []
        for i, (c, (_, nrm, bnm)) in enumerate(zip(spine, frames)):
            u = i / (m - 1)
            R = s.trunk_radius * (1.0 + 0.8 * (1.0 - u) ** 3) * (1.0 - 0.35 * u)
            a = phase + math.tau * s.twist * u
            pts.append(c + (nrm * math.cos(a) + bnm * math.sin(a)) * R)
            center.append(c)
        # 根: 外側の地面へ広がる
        out_dir = pts[0] - spine[0]
        out_dir.z = 0.0
        if out_dir.length > 1e-6 and s.trunk_radius > 0:
            out_dir.normalize()
            root = spine[0] + out_dir * s.trunk_radius * 3.5
            root.z = 0.0
            mid = spine[0] + out_dir * s.trunk_radius * 2.4
            mid.z = s.trunk_radius * 0.25
            pts = [root, mid] + pts
            center = [spine[0], spine[0]] + center
        r0 = s.strand_radius * rng.uniform(0.85, 1.15)
        strand = Vine(pts, r0, r0 * 0.55, center)
        vines.append(strand)
        spawn_branches(rng, s, strand, max(0, s.branch_levels - 1), vines)

        # 樹冠: 幹の先端から外へアーチを描いて垂れる
        if s.crown_length > 0:
            tip = pts[-1]
            outward = tip - spine[-1]
            outward = outward.normalized() if outward.length > 1e-6 else any_perpendicular(frames[-1][0])
            d = frames[-1][0] * 0.6 + outward * 0.8
            crown = Vine(
                [tip.copy()] + grow(rng, tip.copy(), d, s.crown_length * rng.uniform(0.8, 1.3),
                                    s, up_bias=0.35)[1:],
                strand.r1,
            )
            vines.append(crown)
            spawn_branches(rng, s, crown, 0, vines)
    return vines


def build_bramble(rng, s, base):
    """地面から放射状に伸びて弧を描く藪の株。"""
    vines = []
    for _ in range(max(1, s.bramble_canes)):
        az = rng.uniform(0, math.tau)
        elev = math.radians(rng.uniform(50, 80))
        d = Vector((math.cos(az) * math.cos(elev), math.sin(az) * math.cos(elev), math.sin(elev)))
        cane = Vine(
            grow(rng, base + Vector((0, 0, 0.02)), d, s.bramble_length * rng.uniform(0.6, 1.3),
                 s, up_bias=0.2, droop=s.droop * 1.3),
            s.strand_radius * rng.uniform(0.45, 0.75),
        )
        vines.append(cane)
        spawn_branches(rng, s, cane, max(0, s.branch_levels - 1), vines)
    return vines


def build_vines(s):
    rng = random.Random(s.seed)
    noise.seed_set(s.seed)  # ノイズ表はプロセスごとに乱数初期化されるので固定する
    half = s.area_size * 0.5
    vines = []
    for base in poisson_points(rng, s.tree_count, half * 0.9, s.min_spacing):
        vines.extend(build_tree(rng, s, base))
    for _ in range(s.bramble_count):
        base = Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0))
        vines.extend(build_bramble(rng, s, base))
    return [v for v in vines if len(v.points) >= 4]


def sample_along(rng, vine, density):
    """密度 (個/m) に従って蔓上の位置 (index, 補間位置, 0..1 の t) を返す。"""
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


def build_leaves(rng, s, vines):
    mb = MeshBuilder()
    if not s.leaves or s.leaf_density <= 0:
        return mb
    thin = s.strand_radius * 0.45
    for v in vines:
        for i, p, t in sample_along(rng, v, s.leaf_density):
            r = v.radius(i)
            if r > thin:
                continue
            tan = v.tangent(i)
            side = tan.cross(random_unit(rng))
            if side.length < 1e-6:
                continue
            side.normalize()
            fwd = (side + tan * 0.5 + UP * 0.2).normalized()
            wid = fwd.cross(tan)
            wid = wid.normalized() if wid.length > 1e-6 else any_perpendicular(fwd)
            nrm = fwd.cross(wid)
            L = s.leaf_size * rng.uniform(0.6, 1.3)
            W = L * 0.32
            fold = L * 0.06
            sag = -UP * (L * 0.15)
            b = p + side * r
            verts = [
                b,
                b + fwd * (L * 0.12),
                b + fwd * (L * 0.45) + wid * W + nrm * fold,
                b + fwd * (L * 0.55),
                b + fwd * (L * 0.45) - wid * W + nrm * fold,
                b + fwd * L + sag,
            ]
            faces = [(0, 1, 2), (0, 4, 1), (1, 3, 2), (1, 4, 3), (2, 3, 5), (3, 4, 5)]
            mb.add(verts, faces)
    return mb


def add_rose(mb, center, axis, size, rng):
    """層状の花弁で構成した簡易的な薔薇。"""
    z = axis.normalized()
    x = any_perpendicular(z)
    y = z.cross(x)
    to_world = Matrix((
        (x.x, y.x, z.x, center.x),
        (x.y, y.y, z.y, center.y),
        (x.z, y.z, z.z, center.z),
        (0, 0, 0, 1),
    )) @ Matrix.Rotation(rng.uniform(0, math.tau), 4, "Z") @ Matrix.Scale(size, 4)
    layers = 5
    nu, nv = 4, 3
    for L in range(layers):
        count = 3 + 2 * L
        lf = L / (layers - 1)
        tilt = math.radians(-15 + 80 * lf ** 1.6)  # 内側は中心へ巻き込み、外側は開く
        w = 0.5 + 0.3 * lf
        h = 0.6 + 0.25 * lf
        rr = 0.04 + 0.2 * lf
        off = rng.uniform(0, math.tau)
        for k in range(count):
            ang = off + math.tau * k / count
            rot = Matrix.Rotation(ang, 4, "Z") @ Matrix.Translation((rr, 0, 0)) @ Matrix.Rotation(tilt, 4, "Y")
            verts, faces = [], []
            for j in range(nv + 1):
                v = j / nv
                width = w * (0.35 + 0.65 * math.sin(math.pi * (0.25 + 0.6 * v)))
                for i in range(nu + 1):
                    u = i / nu * 2 - 1
                    lx = 0.25 * width * u * u + 0.35 * h * v * v * lf  # 縁と先端が外へ反る
                    ly = u * width * 0.5
                    lz = v * h
                    verts.append(to_world @ (rot @ Vector((lx, ly, lz))))
            for j in range(nv):
                for i in range(nu):
                    a = j * (nu + 1) + i
                    faces.append((a, a + 1, a + nu + 2, a + nu + 1))
            mb.add(verts, faces)


def build_roses(rng, s, vines):
    mb = MeshBuilder()
    if not s.roses or s.rose_count <= 0 or not vines:
        return mb
    # 先端が地面より上にある細い蔓の先に咲かせる
    thin = s.strand_radius * 0.3
    candidates = [v for v in vines if v.points[-1].z > 0.3 and v.r1 < thin] or vines
    rng.shuffle(candidates)
    for v in candidates[: s.rose_count]:
        n = len(v.points)
        i = rng.randint(max(0, n - 4), n - 1)
        axis = (v.tangent(i) * 0.5 + UP).normalized()
        size = s.rose_size * rng.uniform(0.7, 1.3)
        add_rose(mb, v.points[i] + axis * (size * 0.1), axis, size, rng)
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
        "leaf": get_material("TF_Leaf", (0.02, 0.045, 0.015), 0.55, (0.035, 0.06, 0.02), 15.0),
        "rose": get_material("TF_Rose", (0.30, 0.005, 0.02), 0.45, (0.12, 0.0, 0.01), 8.0),
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
        top = max(s.trunk_height * 2.5, 4.0)
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
    for name, mb, mat in (
        ("TF_Thorns", build_thorns(rng, s, vines), mats["thorn"]),
        ("TF_Leaves", build_leaves(rng, s, vines), mats["leaf"]),
        ("TF_Roses", build_roses(rng, s, vines), mats["rose"]),
    ):
        obj = make_mesh_object(col, name, mb, mat)
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
    ("茨の木", ("tree_count", "min_spacing", "trunk_height", "trunk_strands", "trunk_radius",
                "strand_radius", "twist", "crown_length")),
    ("枝・藪", ("branch_levels", "branches_per_vine", "branch_ratio", "bramble_count",
               "bramble_canes", "bramble_length", "wiggle", "droop")),
    ("トゲ", ("thorn_density", "thorn_size", "thorn_hook")),
    ("葉・薔薇", ("leaves", "leaf_density", "leaf_size", "roses", "rose_count", "rose_size")),
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
        cam.location = (r, -r, s.trunk_height * 0.9)
        look = Vector((0, 0, s.trunk_height * 0.35)) - cam.location
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
