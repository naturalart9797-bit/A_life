# SPDX-License-Identifier: GPL-3.0-or-later
"""花の形を計算する (bpy に依存しない純粋な計算)。

花の軸は z。部品は 3 種類:
  * 器 (corona)   : 中央の水をためる部分。底が閉じた 1 枚の回転面なので、
                    縁 (rim) のいちばん低い所まで必ず水がたまる。
  * 外側の花びら  : 器のまわりの遊びの花びら (tepal)。器の外側に押し出し、
                    重なる所は「巻き込みの順番」の分だけずらすので交差しない。
  * しべ・茎      : 器の中のしべ、器の下のふくらみ (子房) と茎。

bloom = 1 で満開、0 でつぼみ。つぼみでは花びらが立ち上がって器を包み、
横方向が器のまわりに巻き付く (wrap)。
"""

import math
import random


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def clamp(x, a, b):
    return a if x < a else b if x > b else x


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(e0, e1, x):
    t = clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smax(a, b, k):
    """なめらかな max (k が 0 で普通の max)。"""
    return 0.5 * (a + b + math.sqrt((a - b) * (a - b) + k * k)) - 0.5 * k


class Piece:
    """1 つの部品 (花びら 1 枚、器、しべ 1 本など)。"""

    __slots__ = ("name", "kind", "verts", "faces", "uvs", "colors", "outs")

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind      # 'PETAL' 'CORONA' 'STAMEN' 'STEM' 'WATER'
        self.verts = []
        self.faces = []
        self.uvs = []         # 頂点ごと
        self.colors = []      # 頂点ごと (r, g, b)
        self.outs = []        # 花びらの「外側 (重なりで下になる側)」の向き


def grid_faces(piece, start, rows, cols, wrap=False):
    """rows x cols の格子の面を追加。wrap=True なら列方向をつなぐ。"""
    cc = cols if wrap else cols - 1
    for i in range(rows - 1):
        for j in range(cc):
            j2 = (j + 1) % cols
            a = start + i * cols + j
            b = start + i * cols + j2
            piece.faces.append((a, b, b + cols, a + cols))


# ---------------------------------------------------------------------------
# 器 (corona)
# ---------------------------------------------------------------------------

class Corona:
    """器の回転面。point(t, phi) -> (r, z)。t: 0=底の中心, 1=縁。"""

    def __init__(self, p, bloom, rng):
        self.R = p.corona_radius
        self.H = p.corona_height
        self.tb = clamp(p.corona_bottom, 0.05, 0.9)
        self.bulge = p.corona_bulge
        self.flare = p.corona_flare * lerp(p.bud_flare, 1.0, bloom)
        self.pinch = p.bud_pinch * (1.0 - bloom)
        self.lobes = p.corona_lobes
        self.ribs = p.corona_ribs
        self.rib_depth = p.corona_rib_depth
        self.frill_freq = p.rim_frill_freq
        self.frill_r = p.rim_frill * self.R
        # 縁の上下の波は、器の高さに対して t 方向に単調になる範囲に制限
        lobe = p.corona_lobe_depth * self.H
        frill_z = p.rim_frill * self.H
        amp = 2.0 * lobe + 2.0 * frill_z
        limit = self.H * 0.15
        s = limit / amp if amp > limit else 1.0
        self.lobe_depth = lobe * s
        self.frill_z = frill_z * s
        self.ph1 = rng.uniform(0, 2 * math.pi)
        self.ph2 = rng.uniform(0, 2 * math.pi)
        self.ph3 = rng.uniform(0, 2 * math.pi)

    def frill(self, phi):
        f = self.frill_freq
        return math.sin(f * phi + self.ph1 + 1.3 * math.sin(round(f * 0.4) * phi + self.ph2))

    def rim_s(self, t):
        return max(0.0, (t - 0.5) / 0.5) ** 3

    def base_r(self, t):
        rt = min(t / self.tb, 1.0)
        rnd = math.sqrt(max(0.0, 1.0 - (1.0 - rt) ** 2))
        r = self.R * rnd * (1.0 + self.bulge * math.sin(math.pi * t))
        r *= 1.0 - self.pinch * t * t
        r += self.R * self.flare * smoothstep(0.45, 1.0, t) ** 2
        return r

    def point(self, t, phi):
        r = self.base_r(t)
        s = self.rim_s(t)
        fr = self.frill(phi)
        rib = math.cos(self.ribs * phi + self.ph3) if self.ribs > 0 else 0.0
        r += self.R * self.rib_depth * rib * math.sin(math.pi * min(t / 0.85, 1.0)) * smoothstep(0.0, 0.25, t)
        r += self.frill_r * s * math.sin(self.frill_freq * 0.5 * phi + self.ph2 + 0.8 * fr)
        z = self.H * t
        if self.lobes > 0:
            z += s * self.lobe_depth * (math.cos(self.lobes * phi) - 1.0)
        z += s * self.frill_z * (fr - 1.0)
        return max(r, 0.0), z

    def rim_min_z(self, n=720):
        return min(self.point(1.0, 2 * math.pi * i / n)[1] for i in range(n))

    def envelope(self, nt=96, nphi=96, nbins=128):
        """高さ z ごとの器の最大半径・最小半径の表を作る。"""
        zmax = self.H * 1.0 + 1e-6
        zmin = 0.0
        hi = [0.0] * nbins
        lo = [float("inf")] * nbins
        for i in range(nt + 1):
            t = i / nt
            for j in range(nphi):
                r, z = self.point(t, 2 * math.pi * j / nphi)
                b = int((z - zmin) / (zmax - zmin) * (nbins - 1) + 0.5)
                if 0 <= b < nbins:
                    hi[b] = max(hi[b], r)
                    lo[b] = min(lo[b], r)
        # 空のビンを埋め、隣と max を取って安全側に
        for arr, fn in ((hi, max), (lo, min)):
            last = None
            for b in range(nbins):
                if arr[b] in (0.0, float("inf")):
                    if last is not None:
                        arr[b] = arr[last]
                else:
                    last = b
        hi2 = [max(hi[max(b - 1, 0):b + 2]) for b in range(nbins)]
        lo2 = [min(lo[max(b - 1, 0):b + 2]) for b in range(nbins)]
        return CoronaEnvelope(zmin, zmax, hi2, lo2)


class CoronaEnvelope:
    def __init__(self, zmin, zmax, hi, lo):
        self.zmin, self.zmax, self.hi, self.lo = zmin, zmax, hi, lo

    def _sample(self, arr, z):
        n = len(arr)
        x = (z - self.zmin) / (self.zmax - self.zmin) * (n - 1)
        if x < 0 or x > n - 1:
            return None
        i = int(x)
        f = x - i
        if i >= n - 1:
            return arr[-1]
        return lerp(arr[i], arr[i + 1], f)

    def outer(self, z):
        """高さ z での器の最大半径。器の高さの外なら None。"""
        return self._sample(self.hi, z)

    def inner(self, z):
        return self._sample(self.lo, z)

    def outer_expanded(self, z, d):
        """器を d だけ太らせた形の、高さ z での半径 (器の上下 d の範囲まで)。"""
        best = None
        n = 6
        for i in range(n + 1):
            zz = z - d + 2.0 * d * i / n
            e = self._sample(self.hi, zz)
            if e is not None:
                dz = abs(zz - z)
                rr = e + math.sqrt(max(d * d - dz * dz, 0.0))
                best = rr if best is None else max(best, rr)
        return best


def build_corona(p, corona, colors):
    piece = Piece("Corona", 'CORONA')
    nt, nphi = p.corona_res_t, p.corona_res_phi
    c_in, c_rim = colors["corona_base"], colors["corona_rim"]
    # 底の中心
    piece.verts.append((0.0, 0.0, 0.0))
    piece.uvs.append((0.5, 0.0))
    piece.colors.append(c_in)
    start = 1
    for i in range(1, nt + 1):
        t = i / nt
        col = tuple(lerp(a, b, smoothstep(0.2, 1.0, t)) for a, b in zip(c_in, c_rim))
        for j in range(nphi):
            phi = 2 * math.pi * j / nphi
            r, z = corona.point(t, phi)
            piece.verts.append((r * math.cos(phi), r * math.sin(phi), z))
            piece.uvs.append((j / nphi, t))
            piece.colors.append(col)
    for j in range(nphi):
        piece.faces.append((0, start + (j + 1) % nphi, start + j))
    # 法線が外向きになる順で格子
    for i in range(nt - 1):
        for j in range(nphi):
            j2 = (j + 1) % nphi
            a = start + i * nphi + j
            b = start + i * nphi + j2
            piece.faces.append((a, a + nphi, b + nphi, b))
    return piece


def corona_normal(corona, t, phi, eps=1e-3):
    """器の面の内向き (水側) 法線。"""
    def P(tt, pp):
        r, z = corona.point(tt, pp)
        return (r * math.cos(pp), r * math.sin(pp), z)
    t0, t1 = max(t - eps, 0.0), min(t + eps, 1.0)
    a, b = P(t0, phi), P(t1, phi)
    c, d = P(t, phi - eps), P(t, phi + eps)
    dt = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    dp = (d[0] - c[0], d[1] - c[1], d[2] - c[2])
    n = (dp[1] * dt[2] - dp[2] * dt[1], dp[2] * dt[0] - dp[0] * dt[2], dp[0] * dt[1] - dp[1] * dt[0])
    ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
    n = (n[0] / ln, n[1] / ln, n[2] / ln)
    # 内向き = 軸に向かう / 上向き
    px, py, _ = P(t, phi)
    if n[0] * px + n[1] * py > 0 or (t < 0.02 and n[2] < 0):
        n = (-n[0], -n[1], -n[2])
    return n


def build_water(p, corona):
    """器の内側に、縁のいちばん低い所までたまる水。"""
    piece = Piece("Water", 'WATER')
    rim = corona.rim_min_z()
    inset = p.thickness * 0.5 + p.water_gap
    level = inset + (rim - p.water_margin - inset) * p.water_fill
    empty = level <= inset * 1.5
    if empty:
        # 水がたまらない形でも、アニメーション用に頂点数をそろえるため薄い水を作る
        level = inset * 1.5 + 1e-5
    nphi = p.corona_res_phi
    nk = max(6, p.corona_res_t // 2)
    # 各方位で水位の t を二分法で求める (z は t に対して単調)
    t_level = []
    for j in range(nphi):
        phi = 2 * math.pi * j / nphi
        a, b = 0.0, 1.0
        for _ in range(30):
            m = 0.5 * (a + b)
            if corona.point(m, phi)[1] < level:
                a = m
            else:
                b = m
        t_level.append(a)
    piece.verts.append((0.0, 0.0, inset))
    piece.uvs.append((0.5, 0.0))
    for k in range(1, nk + 1):
        f = k / nk
        for j in range(nphi):
            phi = 2 * math.pi * j / nphi
            t = t_level[j] * f
            r, z = corona.point(t, phi)
            n = corona_normal(corona, t, phi)
            x, y = r * math.cos(phi) + n[0] * inset, r * math.sin(phi) + n[1] * inset
            z = z + n[2] * inset
            if k == nk:
                z = level
            piece.verts.append((x, y, max(z, inset)))
            piece.uvs.append((j / nphi, f))
    start = 1
    for j in range(nphi):
        piece.faces.append((0, start + j, start + (j + 1) % nphi))
    for k in range(nk - 1):
        for j in range(nphi):
            j2 = (j + 1) % nphi
            a = start + k * nphi + j
            b = start + k * nphi + j2
            piece.faces.append((a, b, b + nphi, a + nphi))
    # 水面のふた
    c = len(piece.verts)
    piece.verts.append((0.0, 0.0, level))
    piece.uvs.append((0.5, 1.0))
    top = start + (nk - 1) * nphi
    for j in range(nphi):
        piece.faces.append((c, top + (j + 1) % nphi, top + j))
    piece.colors = [(0.7, 0.85, 1.0)] * len(piece.verts)

    # 体積 (水面の円盤 + 側面の発散定理)
    vol = 0.0
    for f in piece.faces:
        v0 = piece.verts[f[0]]
        for i in range(1, len(f) - 1):
            v1, v2 = piece.verts[f[i]], piece.verts[f[i + 1]]
            vol += (v0[0] * (v1[1] * v2[2] - v1[2] * v2[1])
                    - v0[1] * (v1[0] * v2[2] - v1[2] * v2[0])
                    + v0[2] * (v1[0] * v2[1] - v1[1] * v2[0])) / 6.0
    return piece, (0.0 if empty else abs(vol)), level


# ---------------------------------------------------------------------------
# 外側の花びら
# ---------------------------------------------------------------------------

def petal_half_width(u, width, widest, pointiness, claw):
    alpha = math.log(0.5) / math.log(clamp(widest, 0.05, 0.95))
    s = max(math.sin(math.pi * (u ** alpha)), 0.0)
    w = width * (s ** pointiness) * (1.0 - claw * (1.0 - u) ** 6)
    return max(w, width * 0.015)


def whorl_bloom(p, bloom, k, nwhorls):
    """層ごとの時間差。内側の層ほど先に閉じて後から開く。

    内側の層がいつも外側より閉じているので、閉じる途中で外側の花びらが
    内側の花びらを突き抜けることがない。
    """
    if nwhorls <= 1 or p.anim_stagger <= 0:
        return bloom
    d = p.anim_stagger * (1.0 - k / (nwhorls - 1))
    if d >= 1.0:
        return 0.0
    return clamp((bloom - d) / (1.0 - d), 0.0, 1.0)


def spine_curve(r0, z0, tilt, curl, L, L_ref, nu):
    """花びらの中心線 (r, z 平面)。曲がりは長さ s の関数なので、同じ設定なら
    長さが違っても同じ曲線の上に乗る。"""
    angles = []
    for a in range(nu + 1):
        s = L * a / nu
        angles.append(tilt + curl * (s / L_ref) ** 1.6)
    spine = [(r0, z0)]
    ds = L / nu
    for a in range(nu):
        ang = 0.5 * (angles[a] + angles[a + 1])
        r, z = spine[-1]
        spine.append((r + math.cos(ang) * ds, z + math.sin(ang) * ds))
    return angles, spine


def attach_point(p, corona, env):
    z0 = p.petal_attach * corona.H
    e0 = env.outer(z0)
    return (e0 if e0 is not None else corona.R) + p.clearance, z0


def solve_bud_curl(p, corona, env, tilt, L):
    """つぼみで花びらの先が軸の近くまで閉じる反りを二分法で求める。"""
    r0, z0 = attach_point(p, corona, env)
    rim_r = env.outer(corona.H) or corona.R
    target = max(rim_r * (1.0 - p.bud_close), p.clearance * 2.0)
    max_curl = max(math.pi - tilt, 0.0)

    def tip_r(c):
        return spine_curve(r0, z0, tilt, c, L, L, 48)[1][-1][0]

    if tip_r(max_curl) > target:
        return max_curl
    a, b = 0.0, max_curl
    for _ in range(40):
        m = 0.5 * (a + b)
        if tip_r(m) > target:
            a = m
        else:
            b = m
    return a


def angle_of(lat, R, wrap, w):
    """横方向の位置 lat を、軸のまわりの角度にする (平ら ↔ 巻き付き)。"""
    th_f = math.atan2(lat, max(R, 1e-6))
    th_w = lat / max(R, w / (0.92 * math.pi), 1e-6)
    return lerp(th_f, th_w, wrap)


class Whorl:
    """1 層の花びらの共通の形 (ばらつきを除く)。"""

    def __init__(self, p, k, bloom, corona, env, bud_curl):
        self.k = k
        self.count = max(p.petals_per_whorl, 1)
        self.bloom = bloom
        eb = smoothstep(0.0, 1.0, bloom)
        self.eb = eb
        self.play = smoothstep(0.25, 1.0, bloom)
        self.tilt = lerp(math.radians(p.bud_tilt), math.radians(p.petal_tilt + k * p.whorl_tilt_step), eb)
        self.L_ref = p.petal_length * lerp(p.bud_scale, 1.0, eb)
        self.curl = lerp(bud_curl, p.petal_curl, eb)
        self.wrap = lerp(1.0, p.petal_wrap_open, eb)
        self.L = self.L_ref * (1.0 + k * p.whorl_length_step * eb)
        self.W = p.petal_width * self.L_ref * (1.0 + k * p.whorl_width_step)
        self.r0, self.z0 = attach_point(p, corona, env)
        nu = p.petal_res_u
        self.angles, self.spine = spine_curve(self.r0, self.z0, self.tilt, self.curl, self.L, self.L_ref, nu)
        self.w = [petal_half_width(a / nu, self.W, p.petal_widest, p.petal_pointiness, p.petal_claw)
                  for a in range(nu + 1)]
        # 半分の広がり角
        self.alpha = [angle_of(self.w[a], self.spine[a][0], self.wrap, self.w[a]) for a in range(nu + 1)]
        self.spacing = 2 * math.pi / self.count
        self.spiral_max = p.overlap_gap * max(2 * a for a in self.alpha) / self.spacing
        self.free = [1.0] * (nu + 1)
        self.free_cum = [1.0] * (nu + 1)
        self.layer = 0.0


AZ_JITTER = 0.12


def resolve_against(piece, others, dist, cols):
    """外側の層の花びらが、内側の層の花びらから dist 以上「外側」に離れるよう押し出す。

    ねじれ・うねりなどで内側の花びらに近づいた所だけを動かし、押し出し量は
    格子の上でなじませるので、表面に段差はできない。
    """
    try:
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
    except ImportError:
        return
    verts, faces, outs = [], [], []
    for o in others:
        off = len(verts)
        verts.extend(o.verts)
        outs.extend(o.outs)
        faces.extend(tuple(i + off for i in f) for f in o.faces)
    tree = BVHTree.FromPolygons(verts, faces)
    n = len(piece.verts)
    push = [0.0] * n
    for idx, v in enumerate(piece.verts):
        loc, nrm, fi, d = tree.find_nearest(Vector(v), dist * 1.5)
        if loc is None:
            continue
        o = outs[faces[fi][0]]
        # 内側の花びらの「外側」を向いた法線
        if nrm.x * o[0] + nrm.y * o[1] + nrm.z * o[2] < 0:
            nrm = -nrm
        signed = (Vector(v) - loc).dot(nrm)
        if signed < dist:
            push[idx] = dist - signed
    if not any(push):
        return
    rows = n // cols
    for _ in range(3):
        nxt = list(push)
        for r in range(rows):
            for c in range(cols):
                i = r * cols + c
                acc, cnt = push[i], 1
                for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if 0 <= rr < rows and 0 <= cc < cols:
                        acc += push[rr * cols + cc]
                        cnt += 1
                nxt[i] = max(push[i], acc / cnt)
        push = nxt
    for i in range(n):
        if push[i] > 0:
            v, o = piece.verts[i], piece.outs[i]
            piece.verts[i] = (v[0] + o[0] * push[i], v[1] + o[1] * push[i], v[2] + o[2] * push[i])


def setup_whorls(p, bloom, corona, env, bud_curl):
    nw = max(p.whorls, 1)
    whorls = [Whorl(p, k, whorl_bloom(p, bloom, k, nw), corona, env, bud_curl) for k in range(nw)]
    nu = p.petal_res_u
    margin = 0.12
    layer = 0.0
    # 向きのばらつきで隣に近づく分も見込む
    slack = 2.0 * AZ_JITTER * p.petal_jitter * (2 * math.pi / max(p.petals_per_whorl, 1))
    for k, wh in enumerate(whorls):
        wh.layer = layer
        layer += wh.spiral_max + p.whorl_gap + p.clearance
        # 隣の花びらと角度が重なっている所では「あそび」を効かせない
        for a in range(nu + 1):
            room = wh.spacing - 2 * wh.alpha[a]
            if nw > 1:
                sp = 2 * math.pi / (wh.count * nw)
                for j in (k - 1, k + 1):
                    if 0 <= j < nw:
                        room = min(room, sp - wh.alpha[a] - whorls[j].alpha[a])
            # 角度のすき間を長さにして、すき間+厚みより離れてから効かせる
            R = max(wh.spine[a][0], 1e-6)
            room_len = (room - slack) * R
            lo = p.clearance + p.thickness
            wh.free[a] = smoothstep(lo, lo + margin * R, room_len)
        acc, total = [0.0], 0.0
        for a in range(1, nu + 1):
            total += 0.5 * (wh.free[a] + wh.free[a - 1])
            acc.append(total)
        wh.free_cum = [x / nu for x in acc]   # 0..1 (全部自由なら u と同じ)
    return whorls


def build_petal(p, wh, i, corona, env, rng, colors):
    k = wh.k
    piece = Piece("Petal_%d_%d" % (k, i), 'PETAL')
    nu, nv = p.petal_res_u, p.petal_res_v
    eb, play = wh.eb, wh.play
    jit = p.petal_jitter * play
    count, nw = wh.count, max(p.whorls, 1)

    az = 2 * math.pi * i / count + k * 2 * math.pi / (count * nw) + p.petal_rotation
    az += jit * rng.uniform(-AZ_JITTER, AZ_JITTER) * (2 * math.pi / count)
    tilt_j = math.radians(jit * rng.uniform(-10, 10))
    twist_sign = -1.0 if (p.twist_alternate and i % 2) else 1.0
    twist = p.petal_twist * twist_sign * (1.0 + jit * rng.uniform(-0.3, 0.3)) * play
    wave = p.petal_wave * play
    wave_phase = rng.uniform(0, 2 * math.pi)
    ruffle = p.petal_ruffle * play
    cup = p.petal_cup * play
    L = wh.L * (1.0 + jit * rng.uniform(-0.08, 0.08))
    curl_j = wh.curl * jit * rng.uniform(-0.3, 0.3)
    angles, spine = spine_curve(wh.r0, wh.z0, wh.tilt, wh.curl, L, wh.L_ref, nu)
    # 傾きと反りのばらつきは、隣と離れた所から先だけ効かせる (重なった所で交差しない)
    spine2 = [spine[0]]
    for a in range(1, nu + 1):
        r, z = spine[a]
        r0, z0 = spine2[0]
        ang = (tilt_j + curl_j * (a / nu) ** 1.6) * wh.free_cum[a]
        dr, dz = r - r0, z - z0
        spine2.append((r0 + dr * math.cos(ang) - dz * math.sin(ang), z0 + dr * math.sin(ang) + dz * math.cos(ang)))
        angles[a] += ang
    spine = spine2

    c_base, c_tip = colors["petal_base"], colors["petal_tip"]
    for a in range(nu + 1):
        u = a / nu
        fr = wh.free[a]
        ang = angles[a]
        sr, sz = spine[a]
        nr, nz = -math.sin(ang), math.cos(ang)
        w = wh.w[a] * (L / wh.L)
        tw = twist * wh.free_cum[a]
        ct, st = math.cos(tw), math.sin(tw)
        wv = wave * fr * L * u * math.sin(2 * math.pi * p.petal_wave_freq * u + wave_phase)
        spiral = p.bud_spiral * (1.0 - eb) * u
        alpha = wh.alpha[a]
        col = tuple(lerp(x, y, smoothstep(0.0, 0.8, u)) for x, y in zip(c_base, c_tip))
        for c in range(nv + 1):
            v = -1.0 + 2.0 * c / nv
            lat = v * w
            n = (cup * w * v * v + wv) * fr
            n += ruffle * fr * W_RUFFLE * wh.W * (abs(v) ** 3) * u * math.sin(2 * math.pi * p.petal_ruffle_freq * u + 2.0 * v)
            n -= p.petal_midrib * wh.W * (1.0 - abs(v)) ** 4 * play
            lat2 = lat * ct - n * st
            n2 = lat * st + n * ct
            # 重なり順のずれ (外向き / 下向き = -N)。角度に比例させるので、
            # 隣どうしの差はいつも「重なりのすき間」になり、つぼみではらせん状に重なる。
            th0 = angle_of(lat, sr, wh.wrap, w)
            off = wh.layer + p.overlap_gap * (th0 + alpha) / wh.spacing
            n2 -= off
            R = sr + n2 * nr
            z = sz + n2 * nz
            rho_f = math.hypot(R, lat2)
            rho = lerp(rho_f, max(R, 0.0), wh.wrap)
            th = angle_of(lat2, R, wh.wrap, w)
            # 器 (厚みとすき間の分だけ太らせたもの) の外へ押し出す
            fl = env.outer_expanded(z, p.clearance + off)
            if fl is not None:
                rho = smax(rho, fl, p.clearance * 0.5)
            th += az + spiral
            piece.verts.append((rho * math.cos(th), rho * math.sin(th), z))
            piece.outs.append((math.sin(ang) * math.cos(th), math.sin(ang) * math.sin(th), -math.cos(ang)))
            piece.uvs.append((c / nv, u))
            piece.colors.append(col)
    grid_faces(piece, 0, nu + 1, nv + 1)
    return piece


W_RUFFLE = 1.0


# ---------------------------------------------------------------------------
# しべ・茎
# ---------------------------------------------------------------------------

def tube(piece, path, radii, segs, close_start=True, close_end=True, color=(1, 1, 1)):
    """path (点列) に沿った管。"""
    start = len(piece.verts)
    n = len(path)
    prev_side = None
    for i in range(n):
        p0 = path[max(i - 1, 0)]
        p1 = path[min(i + 1, n - 1)]
        tx, ty, tz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        ln = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
        tx, ty, tz = tx / ln, ty / ln, tz / ln
        # 平行移動フレーム
        if prev_side is None:
            ref = (1.0, 0.0, 0.0) if abs(tx) < 0.9 else (0.0, 1.0, 0.0)
        else:
            ref = prev_side
        sx = ref[1] * tz - ref[2] * ty
        sy = ref[2] * tx - ref[0] * tz
        sz = ref[0] * ty - ref[1] * tx
        ls = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        sx, sy, sz = sx / ls, sy / ls, sz / ls
        ux = ty * sz - tz * sy
        uy = tz * sx - tx * sz
        uz = tx * sy - ty * sx
        prev_side = (ux, uy, uz)
        r = radii[i]
        for s in range(segs):
            a = 2 * math.pi * s / segs
            ca, sa = math.cos(a), math.sin(a)
            piece.verts.append((path[i][0] + r * (ca * sx + sa * ux),
                                path[i][1] + r * (ca * sy + sa * uy),
                                path[i][2] + r * (ca * sz + sa * uz)))
            piece.uvs.append((s / segs, i / (n - 1)))
            piece.colors.append(color)
    grid_faces(piece, start, n, segs, wrap=True)
    if close_start:
        c = len(piece.verts)
        piece.verts.append(path[0])
        piece.uvs.append((0.5, 0.0))
        piece.colors.append(color)
        for s in range(segs):
            piece.faces.append((c, start + (s + 1) % segs, start + s))
    if close_end:
        c = len(piece.verts)
        piece.verts.append(path[-1])
        piece.uvs.append((0.5, 1.0))
        piece.colors.append(color)
        last = start + (n - 1) * segs
        for s in range(segs):
            piece.faces.append((c, last + s, last + (s + 1) % segs))


def build_stamens(p, corona, env, rng, colors):
    """器の底から立つしべ。器の壁にも、となりのしべにも触れない位置に置く。"""
    pieces = []
    n = p.stamen_count
    if n <= 0:
        return pieces
    H = corona.H
    base_z = max(H * 0.06, p.thickness + p.stamen_radius * 2.0)
    steps = 24
    gap = p.clearance + p.thickness
    jit = 0.15
    half = math.sin(math.pi / n * (1.0 - 2.0 * jit)) if n > 1 else 1.0

    def profile(f):
        if f < 0.72:
            return p.stamen_radius
        g = (f - 0.72) / 0.28
        return p.stamen_radius + (p.stamen_anther - p.stamen_radius) * math.sin(math.pi * min(g, 0.999)) ** 0.6

    heights = [H * p.stamen_height * (1.0 + rng.uniform(-0.12, 0.12)) for _ in range(n)]
    azs = [2 * math.pi * i / n + rng.uniform(-jit, jit) * 2 * math.pi / n for i in range(n)]
    # 狭い器でも入るように、太さをまとめて縮める
    scale = 1.0
    for hgt in heights:
        for s_ in range(steps + 1):
            f = s_ / steps
            z = base_z + (hgt - base_z) * f
            lim = env.inner(z)
            if lim is None:
                continue
            room = lim - p.thickness * 0.5 - p.clearance - (gap / (2 * half) if n > 1 else 0.0)
            rad = profile(f) * ((1.0 / half + 1.0) if n > 1 else 1.0)
            scale = min(scale, max(room, 0.0) / rad)
    scale = max(scale, 0.15)

    for i in range(n):
        piece = Piece("Stamen_%d" % i, 'STAMEN')
        r0 = corona.R * 0.12
        path, radii = [], []
        for s_ in range(steps + 1):
            f = s_ / steps
            z = base_z + (heights[i] - base_z) * f
            rad = profile(f) * scale
            rho = r0 + corona.R * p.stamen_spread * f * f
            if n > 1:
                rho = max(rho, (2 * rad + gap) / (2 * half))
            lim = env.inner(z)
            if lim is not None:
                rho = min(rho, max(lim - p.thickness * 0.5 - p.clearance - rad, 0.0))
            ang = azs[i] + p.stamen_curve * f * f
            path.append((rho * math.cos(ang), rho * math.sin(ang), z))
            radii.append(rad)
        radii[-1] = p.stamen_radius * scale * 0.3
        tube(piece, path, radii, 8, color=colors["stamen"])
        pieces.append(piece)
    return pieces


def build_stem(p, corona, colors):
    piece = Piece("Stem", 'STEM')
    if not p.stem:
        return None
    gap = p.thickness + p.clearance
    top = -gap
    br = corona.R * p.ovary_radius
    bh = corona.R * p.ovary_length
    sr = corona.R * p.stem_radius
    path, radii = [], []
    steps = 16
    for s in range(steps + 1):
        f = s / steps
        path.append((0.0, 0.0, top - bh * f))
        radii.append(max(sr, br * math.sin(math.pi * (0.08 + 0.84 * f)) ** 0.7))
    sl = p.stem_length
    steps2 = 12
    for s in range(1, steps2 + 1):
        f = s / steps2
        bend = p.stem_bend * sl * f * f
        path.append((bend, 0.0, top - bh - sl * f))
        radii.append(sr)
    tube(piece, path, radii, 12, close_start=True, close_end=True, color=colors["stem"])
    return piece


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------

def build_flower(p, bloom, colors, with_water=True):
    """花全体を作る。戻り値: (pieces, water_piece, water_volume, info)"""
    rng = random.Random(p.seed)
    corona = Corona(p, bloom, rng)
    env = corona.envelope()
    pieces = [build_corona(p, corona, colors)]
    L_bud = p.petal_length * p.bud_scale
    bud_curl = solve_bud_curl(p, corona, env, math.radians(p.bud_tilt), L_bud)
    placed = []
    for wh in setup_whorls(p, bloom, corona, env, bud_curl):
        layer_pieces = []
        for i in range(wh.count):
            prng = random.Random(p.seed * 7919 + wh.k * 131 + i)
            layer_pieces.append(build_petal(p, wh, i, corona, env, prng, colors))
        if placed:
            for pc in layer_pieces:
                resolve_against(pc, placed, p.clearance + p.thickness, p.petal_res_v + 1)
        placed.extend(layer_pieces)
        pieces.extend(layer_pieces)
    pieces.extend(build_stamens(p, corona, env, random.Random(p.seed + 17), colors))
    stem = build_stem(p, corona, colors)
    if stem:
        pieces.append(stem)
    water, vol, level = (None, 0.0, 0.0)
    if with_water:
        water, vol, level = build_water(p, corona)
    info = {"rim_min_z": corona.rim_min_z(), "water_volume": vol, "water_level": level}
    return pieces, water, vol, info
