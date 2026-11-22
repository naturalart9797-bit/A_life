# SPDX-License-Identifier: MIT
"""
ススキの草原ジェネレーター (Susuki Field Generator) for Blender

ススキ (Miscanthus sinensis) の株を、大きさのばらつきを付けて草原状に並べる
Blender アドオン。株ごとに根元から弓なりに垂れる細長い葉 (中央に白い主脈、
一部は枯れ葉)、まっすぐ立つ稈 (かん) と稈の葉、そしてその先に十数本の枝が
開いて垂れる穂を作る。穂の枝には小穂と銀白色の絹毛が密生し、逆光で光る。
足元には下草を敷き、風向き・風の強さで全体がなびく。

葉はメッシュ、稈・穂・絹毛・下草は Blender のヘアカーブ (Curves) で作るため、
数百万本の毛でも軽く扱える。

使い方:
  * ドラッグ&ドロップ: 拡張機能の zip を Blender のウィンドウにドロップ (Blender 4.2 以降)
  * スクリプトとして: テキストエディタで開いて「スクリプト実行」
  * ヘッドレス: blender -b -P susuki_field.py -- --seed 7 --render field.png
    (引数は `blender -b -P susuki_field.py -- --help` を参照)

3D ビューのサイドバー (N キー) に「ススキ」タブが追加される。
"""

bl_info = {
    "name": "Susuki Field Generator (ススキの草原)",
    "author": "A_life",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > ススキ",
    "description": "大きさのばらつきを持ったリアルなススキの草原を生成する",
    "category": "Add Mesh",
}

import math
import random
import sys

import numpy as np

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Matrix, Quaternion, Vector, noise

COLLECTION_NAME = "Susuki Field"
UP = Vector((0.0, 0.0, 1.0))
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
REF_HEIGHT = 1.5  # 各パラメータは高さ 1.5 m の株を基準にしている
PLUME_VARIANTS = 16  # 穂は毛が非常に多いので、この数の型を作ってインスタンスで配置する
HAIRS_PER_SPIKELET = 8


# ---------------------------------------------------------------------------
# 設定値 (PropertyGroup とスクリプト用デフォルトで共有)
# ---------------------------------------------------------------------------

# name: (type, default, min, max, label, description)
SETTINGS = {
    "seed": ("int", 1, 0, 1_000_000, "シード", "乱数シード"),
    "area_size": ("float", 16.0, 1.0, 500.0, "範囲", "草原を生成する正方形の一辺 (m)"),
    "clump_count": ("int", 260, 0, 20000, "株の数", "ススキの株の数"),
    "spacing": ("float", 0.35, 0.0, 3.0, "間隔", "株どうしの間隔の係数。0 で自由に重なり、1 で葉先が接する程度"),
    "min_height": ("float", 0.6, 0.05, 10.0, "最小の高さ", "いちばん小さい株の高さ (m、穂先まで)"),
    "max_height": ("float", 2.0, 0.05, 10.0, "最大の高さ", "いちばん大きい株の高さ (m、穂先まで)"),
    "small_bias": ("float", 1.2, 0.2, 5.0, "小さい株の多さ", "1 で大小が均等、大きいほど小さい株が多く大きい株はまれになる"),
    "leaf_count": ("int", 55, 0, 500, "葉の数", "高さ 1.5 m の株の根元から出る葉の数 (株の大きさに応じて増減)"),
    "leaf_width": ("float", 0.013, 0.002, 0.1, "葉の幅", "葉のいちばん広いところの幅 (m)"),
    "leaf_droop": ("float", 1.0, 0.0, 3.0, "葉の垂れ", "葉が弓なりに垂れる強さ"),
    "dead_leaf_ratio": ("float", 0.25, 0.0, 1.0, "枯れ葉の割合", "茶色く枯れて垂れた葉の割合"),
    "autumn": ("float", 0.5, 0.0, 1.0, "色づき", "葉の黄葉の進み具合 (0 で緑、1 で黄金色)"),
    "flowering_ratio": ("float", 0.85, 0.0, 1.0, "穂の付く株の割合", "穂を付ける株の割合"),
    "culm_count": ("int", 12, 0, 100, "穂の数", "高さ 1.5 m の株から立つ穂 (稈) の数 (株の大きさに応じて増減)"),
    "plume_length": ("float", 0.25, 0.03, 1.0, "穂の長さ", "高さ 1.5 m の株の穂の長さ (m)"),
    "raceme_count": ("int", 16, 1, 60, "穂の枝の数", "1 つの穂を構成する枝 (総) の数"),
    "plume_spread": ("float", 0.5, 0.0, 1.0, "穂の開き", "穂の枝の開き具合 (0 で出たての閉じた穂、1 で開ききった穂)"),
    "plume_nod": ("float", 1.0, 0.0, 3.0, "穂の垂れ", "穂の枝が重さでうなだれる強さ"),
    "hair_density": ("float", 6.0, 0.0, 30.0, "小穂の密度", "穂の枝 1 cm あたりの小穂の数 (小穂ごとに絹毛が生える)"),
    "hair_length": ("float", 0.012, 0.001, 0.05, "絹毛の長さ", "小穂の根元に生える銀白色の毛の長さ (m)"),
    "wind_direction": ("float", 30.0, -360.0, 360.0, "風向き", "風が吹いていく方向 (度、+X から反時計回り)"),
    "wind_strength": ("float", 0.5, 0.0, 3.0, "風の強さ", "穂・稈・葉が風下へなびく強さ"),
    "ground_grass": ("bool", True, None, None, "下草", "株の間の地面に短い草を生やす"),
    "grass_density": ("float", 250.0, 0.0, 5000.0, "下草の密度", "1 m² あたりの下草の本数"),
    "ground": ("bool", True, None, None, "地面", "地面を作成する"),
    "atmosphere": ("bool", True, None, None, "空と光", "空・夕方の低い日差し・霞を設定する"),
    "sun_elevation": ("float", 12.0, 1.0, 90.0, "太陽の高さ", "太陽の高度 (度)。低いほど夕方らしく穂が逆光で光る"),
    "sun_azimuth": ("float", 90.0, -360.0, 360.0, "太陽の方位", "太陽のある方向 (度、+X から反時計回り)"),
    "fog_density": ("float", 0.004, 0.0, 1.0, "霞の濃さ", "地表付近の霞の密度 (上空ほど薄くなる)"),
    "clear_previous": ("bool", True, None, None, "前回分を削除", f"「{COLLECTION_NAME}」コレクションの中身を消してから生成する"),
}


def default_settings(**overrides):
    """スクリプト/CLI 用: デフォルト値の設定オブジェクトを作る。"""
    unknown = set(overrides) - set(SETTINGS)
    if unknown:
        raise TypeError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    ns = type("SusukiSettingsNS", (), {})()
    for name, spec in SETTINGS.items():
        setattr(ns, name, overrides.get(name, spec[1]))
    return ns


# ---------------------------------------------------------------------------
# 形状の計算
# ---------------------------------------------------------------------------

def any_perpendicular(v):
    other = Vector((1.0, 0.0, 0.0)) if abs(v.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return v.cross(other).normalized()


def random_unit(rng):
    while True:
        v = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        if 1e-4 < v.length_squared <= 1.0:
            return v.normalized()


def wind_vector(s):
    a = math.radians(s.wind_direction)
    return Vector((math.cos(a), math.sin(a), 0.0)) * s.wind_strength


class LeafMesh:
    """葉のメッシュと、シェーダーで使う頂点属性。"""

    def __init__(self):
        self.verts = []
        self.faces = []
        self.t = []      # 葉の根元 0 → 先端 1
        self.u = []      # 葉の幅方向 0 → 1 (0.5 が主脈)
        self.rand = []   # 葉ごとの乱数 (色のばらつき)
        self.dead = []   # 枯れ葉なら 1


class CurveSet:
    """同じ点数のカーブを束ねて追加し、最後に 1 つのヘアカーブにまとめる。"""

    def __init__(self):
        self.pos = []
        self.radius = []
        self.sizes = []
        self.rand = []

    def add(self, pos, radius, rand=None):
        """pos: (カーブ数, 点数, 3)、radius: (カーブ数, 点数)。"""
        pos = np.asarray(pos, dtype=np.float32)
        if pos.ndim == 2:
            pos = pos[None]
            radius = np.asarray(radius, dtype=np.float32)[None]
        n, m, _ = pos.shape
        if n == 0:
            return
        self.pos.append(pos.reshape(-1, 3))
        self.radius.append(np.asarray(radius, dtype=np.float32).reshape(-1))
        self.sizes.append(np.full(n, m, dtype=np.int32))
        self.rand.append(np.asarray(rand if rand is not None else np.random.random(n), dtype=np.float32))

    def __len__(self):
        return int(sum(len(x) for x in self.sizes))


def add_leaf(lm, rng, s, base, d0, length, width, dead, wind):
    """根元から弓なりに垂れる細長い葉。中央の主脈で浅く V 字に折れ、ゆるくねじれる。"""
    segs = 10
    d = d0.normalized()
    p = base.copy()
    side = d.cross(UP)
    side = side.normalized() if side.length > 1e-6 else any_perpendicular(d)
    droop = s.leaf_droop * rng.uniform(0.6, 1.4) * (1.8 if dead else 1.0)
    twist = rng.uniform(-1.2, 1.2)
    rand = rng.random()
    seg = length / segs
    o = len(lm.verts)
    for k in range(segs + 1):
        t = k / segs
        if k:
            # 先へ行くほど自重と風で曲がる
            d = (d + (-UP * (droop * 2.2 * t) + wind * (0.9 * t)) * (1.0 / segs)).normalized()
            p = p + d * seg
            p.z = max(p.z, 0.003)
        side = (side - d * side.dot(d))
        side = side.normalized() if side.length > 1e-6 else any_perpendicular(d)
        side = Matrix.Rotation(twist / segs, 3, d) @ side
        normal = side.cross(d)
        if normal.z < 0:
            normal = -normal
        w = width * (0.35 + 0.65 * min(1.0, t / 0.12)) * max(0.0, 1.0 - t ** 1.6) ** 0.7
        w = max(w, width * 0.02)
        fold = w * 0.28
        for u, v in ((0.0, p - side * (w * 0.5) + normal * fold), (0.5, p), (1.0, p + side * (w * 0.5) + normal * fold)):
            lm.verts.append(tuple(v))
            lm.t.append(t)
            lm.u.append(u)
            lm.rand.append(rand)
            lm.dead.append(1.0 if dead else 0.0)
    for k in range(segs):
        a = o + k * 3
        lm.faces.append((a, a + 1, a + 4, a + 3))
        lm.faces.append((a + 1, a + 2, a + 5, a + 4))


def interp_polyline(P, t):
    """折れ線 P (m,3) 上の弧長パラメータ t (n,) の位置と接線。"""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    total = cum[-1] if cum[-1] > 0 else 1.0
    x = t * total
    pos = np.stack([np.interp(x, cum, P[:, k]) for k in range(3)], axis=1)
    idx = np.clip(np.searchsorted(cum, x, side="right") - 1, 0, len(seg) - 1)
    tan = (P[idx + 1] - P[idx]) / np.maximum(seg[idx], 1e-9)[:, None]
    return pos, tan, total


def random_perpendicular_np(nprng, tan):
    r = nprng.normal(size=tan.shape)
    perp = np.cross(tan, r)
    return perp / np.maximum(np.linalg.norm(perp, axis=1), 1e-9)[:, None]


def add_raceme_spikelets(nprng, s, P, scale, wind, hairs, spikelets):
    """穂の枝 (総) に沿って小穂と、その根元の銀白色の絹毛を付ける。"""
    _, _, total = interp_polyline(P, np.zeros(1))
    n = int(s.hair_density * total * 100 * nprng.uniform(0.8, 1.2))
    if n <= 0:
        return
    t = nprng.uniform(0.04, 1.0, n)
    pos, tan, _ = interp_polyline(P, t)
    perp = random_perpendicular_np(nprng, tan)
    up = np.array([0.0, 0.0, 1.0])
    w = np.array(wind[:])

    # 小穂: 枝にほぼ沿って付く小さな粒
    beta = np.radians(nprng.normal(12.0, 6.0, n))[:, None]
    d = tan * np.cos(beta) + perp * np.sin(beta)
    sl = (0.0045 * scale * nprng.uniform(0.8, 1.2, n))[:, None]
    sp = np.stack([pos, pos + d * sl * 0.5, pos + d * sl], axis=1)
    sr = np.array([0.00035, 0.0004, 0.0001], dtype=np.float32) * scale
    spikelets.add(sp, np.broadcast_to(sr, (n, 3)))

    # 絹毛: 小穂の根元から先端側へ斜めに広がり、枝のまわりをふんわり包む
    k = HAIRS_PER_SPIKELET
    m = k * n
    pos2 = np.repeat(pos, k, axis=0)
    tan2 = np.repeat(tan, k, axis=0)
    perp2 = random_perpendicular_np(nprng, tan2)
    beta2 = np.radians(np.clip(nprng.normal(35.0, 14.0, m), 3.0, 80.0))[:, None]
    hd = tan2 * np.cos(beta2) + perp2 * np.sin(beta2)
    hd += w * 0.25 - up * 0.1
    hd /= np.linalg.norm(hd, axis=1)[:, None]
    hl = (s.hair_length * scale * nprng.uniform(0.6, 1.3, m))[:, None]
    hp = np.stack([pos2, pos2 + hd * hl - up * (hl * 0.1)], axis=1)  # 細く短いので 2 点の直線で十分
    hr = np.array([0.00008, 0.00002], dtype=np.float32) * scale
    hairs.add(hp, np.broadcast_to(hr, (m, 2)))


def build_plume(rng, nprng, s, wind, out):
    """穂の型を原点に作る (稈の向き = +Z、高さ 1.5 m の株の大きさ)。

    短い主軸から十数本の枝 (総) が互生して開き、先へ行くほどうなだれて風下へなびく。
    """
    top = Vector((0.0, 0.0, 0.0))
    axis = UP
    P = s.plume_length * rng.uniform(0.85, 1.15)
    axis_len = 0.45 * P
    axis_pts = [top + axis * (axis_len * k / 4) for k in range(5)]
    out["racemes"].add([tuple(v) for v in axis_pts], [0.0011 * (1 - 0.6 * k / 4) for k in range(5)])
    count = max(1, round(s.raceme_count * rng.uniform(0.7, 1.3)))
    phi = rng.uniform(0, math.tau)
    a = any_perpendicular(axis)
    b = axis.cross(a)
    for j in range(count):
        f = j / max(1, count - 1)
        attach = top + axis * (axis_len * f)
        length = P * rng.uniform(0.75, 1.0) * (1.0 - 0.35 * f)  # 下の枝ほど長い
        phi += GOLDEN_ANGLE + rng.uniform(-0.25, 0.25)
        side = a * math.cos(phi) + b * math.sin(phi)
        spread = math.radians(3.0 + 35.0 * s.plume_spread * rng.uniform(0.6, 1.3))
        d = axis * math.cos(spread) + side * math.sin(spread)
        nod = s.plume_nod * rng.uniform(0.7, 1.3)
        pts = [attach.copy()]
        p = attach.copy()
        segs = 6
        for k in range(segs):
            t = (k + 1) / segs
            # 先へ行くほど重さでうなだれ、風下へ流れる
            d = (d + (-UP * (nod * 3.0 * t) + wind * (2.0 * t) + side * (s.plume_spread * 0.6 * t)) * (1.0 / segs)).normalized()
            p = p + d * (length / segs)
            pts.append(p.copy())
        Pn = np.array([tuple(v) for v in pts])
        out["racemes"].add(Pn, np.linspace(0.0006, 0.00025, len(pts)))
        add_raceme_spikelets(nprng, s, Pn, 1.0, wind, out["hairs"], out["spikelets"])


def build_clump(rng, nprng, s, base, height, out):
    """1 株のススキ: 根元から弓なりに垂れる葉、まっすぐ立つ稈と稈の葉、稈の先の穂。"""
    rel = height / REF_HEIGHT
    wind = wind_vector(s)
    rb = 0.035 + 0.06 * rel  # 株元の半径
    lm = out["leaves"]

    n_leaves = round(s.leaf_count * rel ** 1.2 * rng.uniform(0.7, 1.3))
    for _ in range(n_leaves):
        r = rb * math.sqrt(rng.random())
        a = rng.uniform(0, math.tau)
        start = base + Vector((math.cos(a) * r, math.sin(a) * r, 0.0))
        dead = rng.random() < s.dead_leaf_ratio
        # 株の外側の葉ほど寝て、内側ほど立つ
        elev = math.radians(rng.uniform(62, 88) - 25.0 * (r / rb) - (15.0 if dead else 0.0))
        az = a + rng.uniform(-0.6, 0.6)
        d0 = Vector((math.cos(az) * math.cos(elev), math.sin(az) * math.cos(elev), math.sin(elev)))
        length = height * rng.uniform(0.45, 0.8) * (rng.uniform(0.5, 0.9) if dead else 1.0)
        width = s.leaf_width * rng.uniform(0.7, 1.2) * (0.6 + 0.4 * min(rel, 1.5))
        add_leaf(lm, rng, s, start, d0, length, width, dead, wind)

    if rng.random() >= s.flowering_ratio:
        return
    n_culms = round(s.culm_count * rel * rng.uniform(0.5, 1.4))
    for _ in range(n_culms):
        r = rb * 0.6 * math.sqrt(rng.random())
        a = rng.uniform(0, math.tau)
        start = base + Vector((math.cos(a) * r, math.sin(a) * r, 0.0))
        lean = math.radians(rng.uniform(0.0, 10.0))
        lean_dir = Vector((math.cos(a), math.sin(a), 0.0))
        hc = height * rng.uniform(0.82, 1.0) * (1.0 - 0.15 * s.plume_nod / 3.0)
        wobble = any_perpendicular(UP) if rng.random() < 0.5 else -any_perpendicular(UP)
        wobble = Matrix.Rotation(rng.uniform(0, math.tau), 3, UP) @ wobble
        pts = []
        m = 14
        for k in range(m):
            u = k / (m - 1)
            p = (start + UP * (hc * u) + lean_dir * (hc * math.sin(lean) * u)
                 + wind * (0.15 * hc * u ** 2.2) + wobble * (hc * 0.015 * math.sin(math.pi * u)))
            pts.append(p)
        radius = 0.0028 * max(rel, 0.4) ** 0.5
        out["culms"].add([tuple(v) for v in pts], [radius * (1.0 - 0.55 * k / (m - 1)) for k in range(m)])
        # 稈の葉: 下半分の節から出て弓なりに垂れる
        for _ in range(rng.randint(2, 4)):
            u = rng.uniform(0.08, 0.5)
            i = int(u * (m - 1))
            az = rng.uniform(0, math.tau)
            elev = math.radians(rng.uniform(45, 75))
            d0 = Vector((math.cos(az) * math.cos(elev), math.sin(az) * math.cos(elev), math.sin(elev)))
            add_leaf(lm, rng, s, pts[i], d0, height * rng.uniform(0.25, 0.45),
                     s.leaf_width * rng.uniform(0.6, 0.9), rng.random() < s.dead_leaf_ratio * 0.6, wind)
        top = pts[-1]
        axis = (pts[-1] - pts[-2]).normalized()
        scale = max(0.4, rel) ** 0.5 * rng.uniform(0.85, 1.15)
        # 型は風下へなびいた形で作ってあるので、風があるときは向きのばらつきを小さくする
        yaw = rng.uniform(-1, 1) * (math.pi if s.wind_strength < 0.05 else math.radians(25))
        out["plumes"].append((top, axis, scale, yaw, rng.randrange(PLUME_VARIANTS)))


def place_clumps(rng, s):
    """大きさをばらつかせた株を、大きい順に重なりを避けながら配置する。"""
    lo, hi = sorted((s.min_height, s.max_height))
    heights = sorted((lo + (hi - lo) * rng.random() ** s.small_bias for _ in range(s.clump_count)), reverse=True)
    half = s.area_size * 0.5
    placed = []
    for h in heights:
        for _ in range(30):
            c = Vector((rng.uniform(-half, half), rng.uniform(-half, half), 0.0))
            if all((c - q).length >= s.spacing * 0.3 * (h + qh) for q, qh in placed):
                placed.append((c, h))
                break
    return placed


def build_ground_grass(nprng, s, out):
    """株の間の地面に生える短い下草。"""
    if not s.ground_grass or s.grass_density <= 0:
        return
    half = s.area_size * 0.5 + 1.0
    n = int(s.grass_density * (2 * half) ** 2)
    if n == 0:
        return
    base = np.column_stack([nprng.uniform(-half, half, n), nprng.uniform(-half, half, n), np.zeros(n)])
    h = nprng.uniform(0.04, 0.22, n) * nprng.uniform(0.6, 1.0, n)
    az = nprng.uniform(0, math.tau, n)
    lean = np.radians(nprng.uniform(0, 45, n))
    d = np.column_stack([np.cos(az) * np.sin(lean), np.sin(az) * np.sin(lean), np.cos(lean)])
    w = np.array(wind_vector(s)[:]) * 0.3
    pts = [base]
    for k in range(1, 4):
        t = k / 3
        bend = (d + (w - np.array([0.0, 0.0, 1.0]) * 0.6 * t * np.sin(lean)[:, None]) * t)
        pts.append(base + bend * (h * t)[:, None])
    P = np.stack(pts, axis=1)
    r = np.array([0.0010, 0.0008, 0.0005, 0.0001], dtype=np.float32)
    out["grass"].add(P, np.broadcast_to(r, (n, 4)), nprng.random(n))


def build_field(s):
    rng = random.Random(s.seed)
    nprng = np.random.default_rng(s.seed)
    noise.seed_set(s.seed)
    out = {
        "leaves": LeafMesh(),
        "culms": CurveSet(),
        "racemes": CurveSet(),
        "spikelets": CurveSet(),
        "hairs": CurveSet(),
        "grass": CurveSet(),
        "plumes": [],    # (位置, 稈の向き, 大きさ, 回転, 型の番号)
        "variants": [],  # 穂の型ごとの {racemes, spikelets, hairs}
    }
    for base, height in place_clumps(rng, s):
        build_clump(rng, nprng, s, base, height, out)
    if out["plumes"]:
        wind = wind_vector(s)
        for _ in range(PLUME_VARIANTS):
            sets = {"racemes": CurveSet(), "spikelets": CurveSet(), "hairs": CurveSet()}
            build_plume(rng, nprng, s, wind, sets)
            out["variants"].append(sets)
    build_ground_grass(nprng, s, out)
    return out


# ---------------------------------------------------------------------------
# Blender オブジェクト / マテリアル
# ---------------------------------------------------------------------------

def _clear_collection(col):
    for child in list(col.children):
        _clear_collection(child)
        bpy.data.collections.remove(child)
    for obj in list(col.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curves):
                bpy.data.hair_curves.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)


def get_collection(clear):
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    elif clear:
        _clear_collection(col)
    return col


def _find_layer_collection(layer_col, col):
    if layer_col.collection == col:
        return layer_col
    for child in layer_col.children:
        found = _find_layer_collection(child, col)
        if found is not None:
            return found
    return None


def _node_tree(mat):
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return nt


def _attr(nt, name):
    a = nt.nodes.new("ShaderNodeAttribute")
    a.attribute_type = "GEOMETRY"
    a.attribute_name = name
    return a.outputs["Factor"]


def _mix(nt, fac, a, b):
    """色の Mix ノード。a, b は色タプルかソケット、fac は数値かソケット。"""
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    for idx, v in ((6, a), (7, b)):
        if isinstance(v, tuple):
            m.inputs[idx].default_value = (*v, 1.0) if len(v) == 3 else v
        else:
            nt.links.new(v, m.inputs[idx])
    if isinstance(fac, (int, float)):
        m.inputs[0].default_value = fac
    else:
        nt.links.new(fac, m.inputs[0])
    return m.outputs[2]


def _map(nt, value, a, b, c=0.0, d=1.0):
    m = nt.nodes.new("ShaderNodeMapRange")
    m.clamp = True
    m.inputs["From Min"].default_value = a
    m.inputs["From Max"].default_value = b
    m.inputs["To Min"].default_value = c
    m.inputs["To Max"].default_value = d
    nt.links.new(value, m.inputs["Value"])
    return m.outputs["Result"]


def _math(nt, op, a, b):
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = op
    for i, v in enumerate((a, b)):
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = v
        else:
            nt.links.new(v, m.inputs[i])
    return m.outputs[0]


def _new_material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    return mat


def leaf_material(s):
    """緑〜黄金色の葉。主脈は白っぽく、葉先は枯れ色、枯れ葉は茶色。薄い葉なので光が透ける。"""
    mat = _new_material("SS_Leaf")
    mat.diffuse_color = (0.15, 0.17, 0.06, 1.0)
    nt = _node_tree(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    t, u, rand, dead = (_attr(nt, n) for n in ("leaf_t", "leaf_u", "leaf_rand", "leaf_dead"))
    autumn = _math(nt, "ADD", _math(nt, "MULTIPLY", rand, 0.6), s.autumn * 0.8 - 0.2)
    col = _mix(nt, _map(nt, autumn, 0.0, 1.0), (0.035, 0.07, 0.015), (0.20, 0.15, 0.04))
    col = _mix(nt, _map(nt, t, 0.7, 1.0, 0.0, 0.7), col, (0.30, 0.22, 0.11))      # 葉先の枯れ
    col = _mix(nt, dead, col, (0.24, 0.17, 0.09))                                  # 枯れ葉
    mid = _map(nt, _math(nt, "ABSOLUTE", _math(nt, "SUBTRACT", u, 0.5), 0.0), 0.0, 0.06, 0.6, 0.0)
    col = _mix(nt, mid, col, (0.55, 0.52, 0.38))                                   # 白い主脈
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.45
    nt.links.new(col, bsdf.inputs["Base Color"])
    trans = nt.nodes.new("ShaderNodeBsdfTranslucent")
    nt.links.new(col, trans.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 0.3
    nt.links.new(bsdf.outputs[0], mix.inputs[1])
    nt.links.new(trans.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def simple_material(name, color, roughness=0.5, color2=None, rand_attr=None):
    mat = _new_material(name)
    mat.diffuse_color = (*color, 1.0)
    nt = _node_tree(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    if color2 is not None:
        if rand_attr:
            fac = _attr(nt, rand_attr)
        else:
            info = nt.nodes.new("ShaderNodeHairInfo")
            fac = info.outputs["Random"]
        nt.links.new(_mix(nt, fac, color, color2), bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def hair_material():
    """穂の絹毛: メラニンの少ない髪のシェーダーで、逆光で銀白色に光る。"""
    mat = _new_material("SS_PlumeHair")
    mat.diffuse_color = (0.75, 0.7, 0.62, 1.0)
    nt = _node_tree(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    try:
        h = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
        h.parametrization = "MELANIN"
        h.inputs["Melanin"].default_value = 0.12
        h.inputs["Melanin Redness"].default_value = 0.65
        h.inputs["Roughness"].default_value = 0.35
        h.inputs["Radial Roughness"].default_value = 0.4
        h.inputs["Random Color"].default_value = 0.25
        nt.links.new(h.outputs[0], out.inputs["Surface"])
    except (RuntimeError, KeyError):
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        b = nt.nodes.new("ShaderNodeBsdfPrincipled")
        b.inputs["Base Color"].default_value = (0.8, 0.75, 0.66, 1.0)
        nt.links.new(b.outputs[0], out.inputs["Surface"])
    return mat


def ground_material():
    mat = _new_material("SS_Ground")
    mat.diffuse_color = (0.05, 0.04, 0.025, 1.0)
    nt = _node_tree(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.95
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 2.5
    tex.inputs["Detail"].default_value = 10.0
    nt.links.new(tc.outputs["Object"], tex.inputs["Vector"])
    nt.links.new(_mix(nt, _map(nt, tex.outputs["Fac"], 0.3, 0.7), (0.025, 0.02, 0.012), (0.10, 0.08, 0.04)),
                 bsdf.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.4
    nt.links.new(tex.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def make_leaf_object(col, lm, mat):
    if not lm.verts:
        return None
    me = bpy.data.meshes.new("SS_Leaves")
    me.from_pydata(lm.verts, [], lm.faces)
    for name, values in (("leaf_t", lm.t), ("leaf_u", lm.u), ("leaf_rand", lm.rand), ("leaf_dead", lm.dead)):
        a = me.attributes.new(name, "FLOAT", "POINT")
        a.data.foreach_set("value", values)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.materials.append(mat)
    obj = bpy.data.objects.new("SS_Leaves", me)
    col.objects.link(obj)
    return obj


def make_curves_object(col, name, cs, mat):
    if len(cs) == 0:
        return None
    hc = bpy.data.hair_curves.new(name)
    sizes = np.concatenate(cs.sizes)
    hc.add_curves(sizes.tolist())
    hc.position_data.foreach_set("vector", np.concatenate(cs.pos).ravel())
    radius = hc.attributes.get("radius") or hc.attributes.new("radius", "FLOAT", "POINT")
    radius.data.foreach_set("value", np.concatenate(cs.radius))
    rnd = hc.attributes.new("rand", "FLOAT", "CURVE")
    rnd.data.foreach_set("value", np.concatenate(cs.rand))
    hc.materials.append(mat)
    obj = bpy.data.objects.new(name, hc)
    col.objects.link(obj)
    return obj


def make_plumes(col, data, mats):
    """穂の型をビューレイヤーから除外したコレクションに作り、各稈の先にインスタンスで置く。"""
    if not data["plumes"]:
        return []
    holder = bpy.data.collections.new("SS_PlumeVariants")
    col.children.link(holder)
    variants = []
    for i, sets in enumerate(data["variants"]):
        vc = bpy.data.collections.new(f"SS_Plume_{i:02d}")
        holder.children.link(vc)
        for key, mat in (("racemes", mats["raceme"]), ("spikelets", mats["spikelet"]), ("hairs", mats["hair"])):
            make_curves_object(vc, f"SS_Plume_{i:02d}_{key}", sets[key], mat)
        variants.append(vc)
    layer = _find_layer_collection(bpy.context.view_layer.layer_collection, holder)
    if layer is not None:
        layer.exclude = True  # 型そのものは原点に表示・レンダリングしない
    created = []
    for top, axis, scale, yaw, k in data["plumes"]:
        e = bpy.data.objects.new("SS_Plume", None)
        e.instance_type = "COLLECTION"
        e.instance_collection = variants[k]
        e.empty_display_size = 0.05
        e.location = top
        e.rotation_mode = "QUATERNION"
        e.rotation_quaternion = UP.rotation_difference(axis.normalized()) @ Quaternion(UP, yaw)
        e.scale = (scale, scale, scale)
        col.objects.link(e)
        created.append(e)
    return created


def make_ground(col, s, mat):
    half = max(s.area_size * 6.0, 150.0)
    me = bpy.data.meshes.new("SS_Ground")
    me.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    obj = bpy.data.objects.new("SS_Ground", me)
    col.objects.link(obj)
    return obj


def sun_rotation(elevation_deg, azimuth_deg):
    """太陽の高度・方位 (太陽のある方向) からサンライトの回転を求める。"""
    return (math.radians(90.0 - elevation_deg), 0.0, math.radians(azimuth_deg + 90.0))


def setup_atmosphere(col, s):
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    nt = world.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None) or nt.nodes.new("ShaderNodeBackground")
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    sky = next((n for n in nt.nodes if n.type == "TEX_SKY"), None) or nt.nodes.new("ShaderNodeTexSky")
    for sky_type in ("MULTIPLE_SCATTERING", "NISHITA", "HOSEK_WILKIE"):
        try:
            sky.sky_type = sky_type
            break
        except TypeError:
            continue
    if hasattr(sky, "sun_disc"):
        sky.sun_disc = False  # 太陽そのものはサンライトで描く
    if hasattr(sky, "sun_elevation"):
        sky.sun_elevation = math.radians(s.sun_elevation)
        sky.sun_rotation = math.radians(90.0 - s.sun_azimuth)
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.15

    light = bpy.data.lights.new("SS_Sun", "SUN")
    light.energy = 4.0
    # 太陽が低いほど赤みを帯びる
    warm = max(0.0, min(1.0, (30.0 - s.sun_elevation) / 30.0))
    light.color = (1.0, 0.92 - 0.2 * warm, 0.82 - 0.35 * warm)
    light.angle = math.radians(1.0)
    sun = bpy.data.objects.new("SS_Sun", light)
    sun.rotation_euler = sun_rotation(s.sun_elevation, s.sun_azimuth)
    col.objects.link(sun)

    if hasattr(scene, "cycles_curves"):
        scene.cycles_curves.shape = "THICK"  # 毛を丸い断面で描く (近くで見てもリアル)

    # 霞: 地面と重ならないよう底面を下げた箱のボリューム
    if s.fog_density > 0:
        half = max(s.area_size * 1.5, 20.0)
        top = max(s.max_height * 4.0, 6.0)
        verts = [(x, y, z) for z in (-1.0, top) for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))]
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        me = bpy.data.meshes.new("SS_Fog")
        me.from_pydata(verts, [], faces)
        mat = _new_material("SS_Fog")
        mnt = _node_tree(mat)
        mout = mnt.nodes.new("ShaderNodeOutputMaterial")
        vol = mnt.nodes.new("ShaderNodeVolumePrincipled")
        vol.inputs["Color"].default_value = (0.85, 0.8, 0.72, 1.0)
        vol.inputs["Anisotropy"].default_value = 0.6  # 逆光で霞が明るく光る
        coord = mnt.nodes.new("ShaderNodeTexCoord")
        xyz = mnt.nodes.new("ShaderNodeSeparateXYZ")
        mnt.links.new(coord.outputs["Generated"], xyz.inputs["Vector"])
        mnt.links.new(_map(mnt, xyz.outputs["Z"], 1.0 / (top + 1.0), 0.6, s.fog_density, 0.0), vol.inputs["Density"])
        mnt.links.new(vol.outputs["Volume"], mout.inputs["Volume"])
        me.materials.append(mat)
        fog = bpy.data.objects.new("SS_Fog", me)
        fog.display_type = "BOUNDS"
        fog.hide_select = True
        col.objects.link(fog)


def generate(s):
    """設定オブジェクト s からススキの草原を生成し、作成したオブジェクトのリストを返す。"""
    col = get_collection(s.clear_previous)
    data = build_field(s)
    created = []
    parts = (
        (make_leaf_object, "SS_Leaves", "leaves", leaf_material(s)),
        (make_curves_object, "SS_Culms", "culms",
         simple_material("SS_Culm", (0.20, 0.16, 0.07), 0.45, (0.16, 0.09, 0.07))),
        (make_curves_object, "SS_Grass", "grass",
         simple_material("SS_Grass", (0.05, 0.09, 0.02), 0.6, (0.28, 0.22, 0.10), rand_attr="rand")),
    )
    for fn, name, key, mat in parts:
        obj = fn(col, data[key], mat) if fn is make_leaf_object else fn(col, name, data[key], mat)
        if obj is not None:
            created.append(obj)
    plume_mats = {
        "raceme": simple_material("SS_Raceme", (0.30, 0.22, 0.14), 0.5, (0.22, 0.13, 0.12)),
        "spikelet": simple_material("SS_Spikelet", (0.38, 0.28, 0.18), 0.5, (0.30, 0.18, 0.17)),
        "hair": hair_material(),
    }
    plumes = make_plumes(col, data, plume_mats)
    if s.ground:
        created.append(make_ground(col, s, ground_material()))
    if s.atmosphere:
        setup_atmosphere(col, s)
    if plumes:
        hairs = sum(len(v["hairs"]) for v in data["variants"])
        print(f"[susuki_field] 穂 {len(plumes)} 本 (型 {len(data['variants'])} 種、絹毛 計 {hairs} 本)")
    return created


def build_field_objects(**overrides):
    """Python コンソール等から: build_field_objects(seed=3, clump_count=300)"""
    return generate(default_settings(**overrides))


def describe(obj):
    if obj.instance_type == "COLLECTION":
        return f"{obj.name}: 穂のインスタンス"
    d = obj.data
    if isinstance(d, bpy.types.Curves):
        return f"{obj.name}: {len(d.curves)} 本"
    return f"{obj.name}: {len(d.vertices)} 頂点"


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


SusukiSettings = type(
    "SusukiSettings",
    (bpy.types.PropertyGroup,),
    {"__annotations__": {name: _make_property(spec) for name, spec in SETTINGS.items()}},
)


class SUSUKI_OT_generate(bpy.types.Operator):
    bl_idname = "object.susuki_field_generate"
    bl_label = "ススキの草原を生成"
    bl_description = "設定に従ってススキの草原を生成する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        objs = generate(context.scene.susuki_field)
        self.report({"INFO"}, "ススキの草原を生成しました (" + ", ".join(describe(o) for o in objs) + ")")
        return {"FINISHED"}


class SUSUKI_OT_randomize(bpy.types.Operator):
    bl_idname = "object.susuki_field_randomize"
    bl_label = "シードを変えて再生成"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.susuki_field.seed = random.randint(0, 999_999)
        return bpy.ops.object.susuki_field_generate()


PANEL_LAYOUT = (
    ("全体", ("seed", "area_size", "clump_count", "spacing")),
    ("大きさのばらつき", ("min_height", "max_height", "small_bias")),
    ("葉", ("leaf_count", "leaf_width", "leaf_droop", "dead_leaf_ratio", "autumn")),
    ("穂", ("flowering_ratio", "culm_count", "plume_length", "raceme_count", "plume_spread", "plume_nod",
           "hair_density", "hair_length")),
    ("風", ("wind_direction", "wind_strength")),
    ("シーン", ("ground_grass", "grass_density", "ground", "atmosphere", "sun_elevation", "sun_azimuth",
               "fog_density", "clear_previous")),
)


class SUSUKI_PT_panel(bpy.types.Panel):
    bl_label = "ススキの草原"
    bl_idname = "SUSUKI_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ススキ"

    def draw(self, context):
        s = context.scene.susuki_field
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator(SUSUKI_OT_generate.bl_idname, icon="OUTLINER_OB_CURVES")
        layout.operator(SUSUKI_OT_randomize.bl_idname, icon="FILE_REFRESH")
        for title, names in PANEL_LAYOUT:
            box = layout.box()
            box.label(text=title)
            colm = box.column(align=True)
            for name in names:
                colm.prop(s, name)


CLASSES = (SusukiSettings, SUSUKI_OT_generate, SUSUKI_OT_randomize, SUSUKI_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.susuki_field = PointerProperty(type=SusukiSettings)


def unregister():
    del bpy.types.Scene.susuki_field
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


# ---------------------------------------------------------------------------
# ヘッドレス実行: blender -b -P susuki_field.py -- [options]
# ---------------------------------------------------------------------------

def _cli(argv):
    import argparse

    parser = argparse.ArgumentParser(prog="susuki_field.py", description="ススキの草原を生成する")
    for name, (kind, default, *_rest) in SETTINGS.items():
        flag = "--" + name.replace("_", "-")
        if kind == "bool":
            parser.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes", "on"), default=default)
        else:
            parser.add_argument(flag, type=int if kind == "int" else float, default=default)
    parser.add_argument("--save", help="生成後に .blend として保存するパス")
    parser.add_argument("--render", help="カメラを置いてレンダリングする画像パス (.png)")
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--view", choices=("eye", "side", "aerial", "close"), default="eye",
                        help="eye: 人の目の高さから太陽の方へ (逆光) / side: 斜め上から順光 / aerial: 真上 / close: 穂の接写")
    parser.add_argument("--resolution", type=int, nargs=2, default=(1280, 720))
    args = parser.parse_args(argv)

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    s = default_settings(**{k: getattr(args, k) for k in SETTINGS})
    for o in generate(s):
        print(f"[susuki_field] {describe(o)}")

    if args.render:
        scene = bpy.context.scene
        cam_data = bpy.data.cameras.new("SS_Camera")
        cam = bpy.data.objects.new("SS_Camera", cam_data)
        scene.collection.objects.link(cam)
        az = math.radians(s.sun_azimuth)
        to_sun = Vector((math.cos(az), math.sin(az), 0.0))
        half = s.area_size * 0.5
        if args.view == "aerial":
            cam_data.lens = 35
            cam.location = (0.0, 0.0, s.area_size * 1.05)
            cam.rotation_euler = (0.0, 0.0, 0.0)
        else:
            if args.view == "eye":
                cam_data.lens = 35
                loc = -to_sun * (half * 0.85) + Vector((0, 0, 1.55))
                target = to_sun * half + Vector((0, 0, 1.2))
            elif args.view == "close":
                cam_data.lens = 85
                loc = -to_sun * (half * 0.75) + Vector((0, 0, 1.4))
                target = -to_sun * (half * 0.55) + Vector((0, 0, 1.25))
            else:
                cam_data.lens = 30
                loc = to_sun * (half * 1.1) + Vector((0, 0, 3.5))
                target = Vector((0, 0, 0.5))
            cam.location = loc
            cam.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
        scene.camera = cam
        scene.render.engine = "CYCLES"
        scene.cycles.samples = args.samples
        scene.cycles.device = "CPU"
        scene.render.resolution_x, scene.render.resolution_y = args.resolution
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=args.save)


if __name__ == "__main__":
    if "--" in sys.argv or bpy.app.background:
        _cli(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    else:
        register()
