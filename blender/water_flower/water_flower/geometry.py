# SPDX-License-Identifier: GPL-3.0-or-later
"""花の形を計算する (bpy に依存しない純粋な計算)。

花の軸は z。部品:
  * 器 (corona)   : 中央の水をためる部分。底が閉じた 1 枚の回転面なので、
                    縁 (rim) のいちばん低い所まで必ず水がたまる。
  * 外側の花びら  : 子房の上のドーム (花托) から筒になって器を包みながら立ち上がり、
                    その先で開く遊びの花びら。重なる所は「巻き込みの順番」の分だけ
                    ずらすので交差しない。
  * がく          : 花托のふちから出る緑の葉。
  * しべ・めしべ  : 器の底に立つ。
  * 子房・茎      : 花全体を支える。

bloom = 1 で満開、0 でつぼみ。つぼみでは器が (底も) 縮み、花びらが立ち上がって
器を包み、横方向が器のまわりに巻き付く (wrap)。
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

    __slots__ = ("name", "kind", "verts", "faces", "uvs", "colors", "outs", "tube_rows")

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind      # 'PETAL' 'SEPAL' 'CORONA' 'STAMEN' 'PISTIL' 'STEM' 'WATER'
        self.verts = []
        self.faces = []
        self.uvs = []         # 頂点ごと
        self.colors = []      # 頂点ごと (r, g, b)
        self.outs = []        # 花びらの「外側 (重なりで下になる側)」の向き
        self.tube_rows = 0


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
        eb = smoothstep(0.0, 1.0, bloom)
        # つぼみでは器全体 (底も) が縮む
        self.R = p.corona_radius * lerp(p.bud_corona_radius, 1.0, eb)
        self.H = p.corona_height * lerp(p.bud_corona_height, 1.0, eb)
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
        # 縁は上下よりも横 (外側) に大きく波打たせる
        frill_z = p.rim_frill * self.H * 0.35
        crimp = p.rim_crimp * self.H * 0.4
        amp = 2.0 * lobe + 2.0 * frill_z + 2.0 * crimp * 2.0
        limit = self.H * 0.15
        s = limit / amp if amp > limit else 1.0
        self.lobe_depth = lobe * s
        self.frill_z = frill_z * s
        self.crimp_z = crimp * s
        self.crimp_r = p.rim_crimp * self.R
        self.crimp_freq = p.rim_crimp_freq
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
        if self.crimp_freq > 0:
            # 縁のごく近くだけの細かいちぢれ
            s2 = max(0.0, (t - 0.8) / 0.2) ** 2
            cr = math.sin(self.crimp_freq * phi + 2.0 * fr + self.ph3)
            r += self.crimp_r * s2 * cr
            z += s2 * self.crimp_z * (math.sin(self.crimp_freq * 1.37 * phi + self.ph1) - 1.0)
        return max(r, 0.0), z

    def floor_z(self, rho):
        """器の底で、軸からの距離 rho の所の高さ (しべを底に立てるため)。"""
        a, b = 0.0, 0.5
        for _ in range(30):
            m = 0.5 * (a + b)
            if self.base_r(m) < rho:
                a = m
            else:
                b = m
        return self.H * a

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
        self.core = None
        self.core_top = 0.0

    def add_obstacles(self, pieces):
        """器の中から上へ出ている部品 (しべ・めしべ) も、花びらがよける対象にする。"""
        verts = [v for pc in pieces for v in pc.verts]
        if not verts:
            return
        top = max(v[2] for v in verts)
        if top <= 0:
            return
        n = 160
        core = [0.0] * n
        for x, y, z in verts:
            b = int(clamp(z / top, 0.0, 1.0) * (n - 1) + 0.5)
            core[b] = max(core[b], math.hypot(x, y))
        self.core = [max(core[max(b - 2, 0):b + 3]) for b in range(n)]
        self.core_top = top

    def _core(self, z):
        if self.core is None or z < 0 or z > self.core_top:
            return None
        n = len(self.core)
        x = z / self.core_top * (n - 1)
        i = min(int(x), n - 2)
        c = lerp(self.core[i], self.core[i + 1], x - i)
        return c if c > 0 else None

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
        """高さ z での器 (と中のしべ) の最大半径。何もない高さなら None。"""
        e = self._sample(self.hi, z)
        c = self._core(z)
        if c is None:
            return e
        return c if e is None else max(e, c)

    def inner(self, z):
        return self._sample(self.lo, z)

    def outer_expanded(self, z, d):
        """器を d だけ太らせた形の、高さ z での半径 (器の上下 d の範囲まで)。"""
        best = None
        n = 6
        for i in range(n + 1):
            zz = z - d + 2.0 * d * i / n
            e = self.outer(zz)
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
    inset = p.water_gap
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
# 花托 (子房の上のふくらみ)。花びらの筒とがくは、この上に付く
# ---------------------------------------------------------------------------

class Receptacle:
    """子房の上面のドーム。dome_z(r) で高さが分かる。"""

    def __init__(self, p, corona, max_offset):
        R = p.corona_radius
        self.tube_r = R * p.tube_radius
        need = self.tube_r + p.clearance + max_offset + p.clearance * 2.0
        self.R = max(R * p.ovary_radius, need)
        self.top = -p.clearance * 2.0
        self.h = self.R * 0.12
        self.length = R * p.ovary_length

    def dome_z(self, r):
        x = clamp(r / self.R, 0.0, 1.0)
        return self.top - self.h * x * x

    def rim(self):
        return self.R, self.dome_z(self.R)


# ---------------------------------------------------------------------------
# 外側の花びら・がく
# ---------------------------------------------------------------------------

def petal_outline(u, width, widest, pointiness):
    """花びらの輪郭 (付け根 0 → 先端 0)。"""
    alpha = math.log(0.5) / math.log(clamp(widest, 0.05, 0.95))
    s = max(math.sin(math.pi * (u ** alpha)), 0.0)
    return width * (s ** pointiness)


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


def spine_curve(r0, z0, tilt, curl, L, L_ref, nu, tip_curl=0.0):
    """花びらの中心線 (r, z 平面)。曲がりは長さ s の関数なので、同じ設定なら
    長さが違っても同じ曲線の上に乗る。"""
    angles = []
    for a in range(nu + 1):
        s = L * a / nu
        angles.append(tilt + curl * (s / L_ref) ** 1.6 + tip_curl * smoothstep(0.7, 1.0, a / nu))
    spine = [(r0, z0)]
    ds = L / nu
    for a in range(nu):
        ang = 0.5 * (angles[a] + angles[a + 1])
        r, z = spine[-1]
        spine.append((r + math.cos(ang) * ds, z + math.sin(ang) * ds))
    return angles, spine


def tube_radius_at(env, rec, z, d):
    """花びらの筒が、器を d だけ太らせた形と花托の筒に沿う半径。"""
    e = env.outer_expanded(z, d)
    base = rec.tube_r + d
    return base if e is None else max(e, base)


def solve_bud_curl(p, corona, env, rec, tilt, L):
    """つぼみで花びらの先が軸の近くまで閉じる反りを二分法で求める。"""
    z0 = p.petal_attach * corona.H
    r0 = tube_radius_at(env, rec, z0, p.clearance)
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


class WhorlCfg:
    """1 層ぶんの設定 (花びら / がく)。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def whorl_configs(p, corona, bud_curl):
    cfgs = []
    nw = max(p.whorls, 1)
    for k in range(nw):
        cfgs.append(WhorlCfg(
            kind='PETAL', k=k, count=max(p.petals_per_whorl, 1),
            az0=k * 2 * math.pi / (max(p.petals_per_whorl, 1) * nw) + p.petal_rotation,
            length=p.petal_length * (1.0 + k * p.whorl_length_step),
            width=p.petal_width * (1.0 + k * p.whorl_width_step),
            widest=p.petal_widest, pointiness=p.petal_pointiness,
            tilt=math.radians(p.petal_tilt + k * p.whorl_tilt_step), curl=p.petal_curl,
            cup=p.petal_cup, fold=p.petal_fold, reflex=p.petal_reflex, sweep=p.petal_sweep,
            tip_curl=p.petal_tip_curl, detail=p.petal_detail, midrib=p.petal_midrib,
            twist=p.petal_twist, alternate=p.twist_alternate, wave=p.petal_wave,
            wave_freq=p.petal_wave_freq, ruffle=p.petal_ruffle, ruffle_freq=p.petal_ruffle_freq,
            jitter=p.petal_jitter, wrap_open=p.petal_wrap_open, tube=True,
            bud_tilt=math.radians(p.bud_tilt), bud_curl=bud_curl, bud_scale=p.bud_scale,
            colors=("petal_base", "petal_tip"),
        ))
    if p.sepal_count > 0:
        n = p.sepal_count
        cfgs.append(WhorlCfg(
            kind='SEPAL', k=nw, count=n,
            az0=math.pi / n + p.petal_rotation,
            length=p.petal_length * p.sepal_length, width=p.sepal_width,
            widest=0.35, pointiness=1.0,
            tilt=math.radians(p.sepal_tilt), curl=p.sepal_curl,
            cup=p.sepal_cup, fold=p.petal_fold * 0.5, reflex=0.0, sweep=0.0,
            tip_curl=0.0, detail=p.petal_detail, midrib=p.petal_midrib,
            twist=p.petal_twist * 0.2, alternate=True, wave=p.petal_wave * 0.5,
            wave_freq=1.0, ruffle=0.0, ruffle_freq=0.0,
            jitter=p.petal_jitter, wrap_open=0.4, tube=False,
            bud_tilt=math.radians(p.sepal_bud_tilt), bud_curl=0.25, bud_scale=1.0,
            colors=("sepal_base", "sepal_tip"),
        ))
    return cfgs


class Whorl:
    """1 層の花びらの共通の形 (ばらつきを除く)。"""

    def __init__(self, p, cfg, bloom, corona, env, rec):
        self.cfg = cfg
        self.k = cfg.k
        self.count = cfg.count
        self.bloom = bloom
        eb = smoothstep(0.0, 1.0, bloom)
        self.eb = eb
        self.play = smoothstep(0.25, 1.0, bloom)
        self.tilt = lerp(cfg.bud_tilt, cfg.tilt, eb)
        self.L_ref = p.petal_length * lerp(p.bud_scale, 1.0, eb) if cfg.kind == 'PETAL' else cfg.length
        self.curl = lerp(cfg.bud_curl, cfg.curl, eb)
        self.wrap = lerp(1.0, cfg.wrap_open, eb)
        scale = lerp(cfg.bud_scale, 1.0, eb)
        self.L = cfg.length * scale if cfg.kind == 'SEPAL' else \
            self.L_ref * (1.0 + (cfg.length / p.petal_length - 1.0) * eb)
        self.W = cfg.width * cfg.length * scale
        nu = p.petal_res_u
        if cfg.tube:
            self.z0 = p.petal_attach * corona.H
            self.r0 = tube_radius_at(env, rec, self.z0, p.clearance)
            # 筒の所では、1 層の花びらが少し重なって円をおおう幅
            self.w_base = math.pi * self.r0 / self.count * 1.12
        else:
            self.r0, self.z0 = rec.rim()
            self.r0 *= 0.985
            self.z0 = rec.dome_z(self.r0)
            self.w_base = self.W * 0.12
        self.angles, self.spine = spine_curve(self.r0, self.z0, self.tilt, self.curl, self.L, self.L_ref,
                                              nu, cfg.tip_curl * self.play)
        self.w = []
        for a in range(nu + 1):
            u = a / nu
            self.w.append(petal_outline(u, self.W, cfg.widest, cfg.pointiness)
                          + self.w_base * (1.0 - smoothstep(0.0, 0.35, u)) + self.W * 0.004)
        # 隣と重なっている所は完全に巻き付けたまま (らせん状に重なる) にする。
        # 平らにするのは隣から離れた所だけ (setup_whorls で決める)
        self.wrap_u = [1.0] * (nu + 1)
        # 半分の広がり角 (横へ流れる分も含む)
        self.alpha = []
        for a in range(nu + 1):
            u = a / nu
            R = max(self.spine[a][0], 1e-6)
            ext = self.w[a] + abs(cfg.sweep) * self.L * u * u * self.play
            self.alpha.append(angle_of(ext, R, self.wrap_u[a], self.w[a]))
        self.spacing = 2 * math.pi / self.count
        self.spiral_max = p.overlap_gap * max(2 * a for a in self.alpha) / self.spacing
        self.free = [1.0] * (nu + 1)
        self.free_cum = [1.0] * (nu + 1)
        self.layer = 0.0


AZ_JITTER = 0.12


def setup_whorls(p, bloom, corona, env, rec_probe, bud_curl):
    cfgs = whorl_configs(p, corona, bud_curl)
    n_all = len(cfgs)
    whorls = [Whorl(p, c, whorl_bloom(p, bloom, i, n_all), corona, env, rec_probe)
              for i, c in enumerate(cfgs)]
    nu = p.petal_res_u
    margin = 0.12
    layer = 0.0
    petal_whorls = [w for w in whorls if w.cfg.kind == 'PETAL']
    npw = len(petal_whorls)
    for wh in whorls:
        cfg = wh.cfg
        slack = 2.0 * AZ_JITTER * cfg.jitter * wh.spacing
        if cfg.tube:
            wh.layer = layer
            layer += wh.spiral_max + p.whorl_gap + p.clearance
        else:
            wh.layer = 0.0
        for a in range(nu + 1):
            room = wh.spacing - 2 * wh.alpha[a]
            if cfg.kind == 'PETAL' and npw > 1:
                sp = 2 * math.pi / (wh.count * npw)
                for j in (wh.k - 1, wh.k + 1):
                    if 0 <= j < npw:
                        room = min(room, sp - wh.alpha[a] - petal_whorls[j].alpha[a])
            R = max(wh.spine[a][0], 1e-6)
            room_len = (room - slack) * R
            lo = p.clearance
            wh.free[a] = smoothstep(lo, lo + margin * R, room_len)
        acc, total = [0.0], 0.0
        for a in range(1, nu + 1):
            total += 0.5 * (wh.free[a] + wh.free[a - 1])
            acc.append(total)
        wh.free_cum = [x / nu for x in acc]
        # 巻き付き → 平らへの移り変わりは、隣から離れた所から少しずつ (折れ目ができない)
        wh.wrap_u = [lerp(1.0, wh.wrap, smoothstep(0.0, 1.0, min(1.0, acc[a] / nu / 0.3)))
                     for a in range(nu + 1)]
    return whorls, layer


def resolve_against(piece, others, dist, cols, skip_rows=0, search=None):
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
    if not faces:
        return
    tree = BVHTree.FromPolygons(verts, faces)
    if search is None:
        search = dist * 3.0
    n = len(piece.verts)
    push = [0.0] * n
    for idx in range(skip_rows * cols, n):
        v = piece.verts[idx]
        loc, nrm, fi, d = tree.find_nearest(Vector(v), search)
        if loc is None:
            continue
        o = outs[faces[fi][0]]
        if nrm.x * o[0] + nrm.y * o[1] + nrm.z * o[2] < 0:
            nrm = -nrm
        signed = (Vector(v) - loc).dot(nrm)
        if signed < dist:
            push[idx] = dist - signed
    if not any(push):
        return
    rows = n // cols
    for _ in range(4):
        nxt = list(push)
        for r in range(skip_rows, rows):
            for c in range(cols):
                i = r * cols + c
                acc, cnt = push[i], 1
                for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if skip_rows <= rr < rows and 0 <= cc < cols:
                        acc += push[rr * cols + cc]
                        cnt += 1
                nxt[i] = max(push[i], acc / cnt)
        push = nxt
    for i in range(n):
        if push[i] > 0:
            v, o = piece.verts[i], piece.outs[i]
            piece.verts[i] = (v[0] + o[0] * push[i], v[1] + o[1] * push[i], v[2] + o[2] * push[i])


def build_petal(p, wh, i, corona, env, rec, rng, colors):
    """花びら 1 枚。筒の部分 (花托から器に沿って立ち上がる) と、開く部分から成る。"""
    cfg = wh.cfg
    k = wh.k
    piece = Piece(("Petal_%d_%d" if cfg.kind == 'PETAL' else "Sepal_%d_%d") % (k, i), cfg.kind)
    nu, nv = p.petal_res_u, p.petal_res_v
    eb, play = wh.eb, wh.play
    jit = cfg.jitter * play
    count = wh.count

    az = 2 * math.pi * i / count + cfg.az0
    az += jit * rng.uniform(-AZ_JITTER, AZ_JITTER) * wh.spacing
    tilt_j = math.radians(jit * rng.uniform(-10, 10))
    twist_sign = -1.0 if (cfg.alternate and i % 2) else 1.0
    twist = cfg.twist * twist_sign * (1.0 + jit * rng.uniform(-0.3, 0.3)) * play
    wave = cfg.wave * play
    wave_phase = rng.uniform(0, 2 * math.pi)
    ruffle = cfg.ruffle * play
    sweep = cfg.sweep * twist_sign * (1.0 + jit * rng.uniform(-0.4, 0.4)) * play
    ph = [rng.uniform(0, 2 * math.pi) for _ in range(6)]
    L = wh.L * (1.0 + jit * rng.uniform(-0.08, 0.08))
    curl_j = wh.curl * jit * rng.uniform(-0.3, 0.3)
    angles, spine = spine_curve(wh.r0, wh.z0, wh.tilt, wh.curl, L, wh.L_ref, nu, cfg.tip_curl * play)
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

    c_base, c_tip = colors[cfg.colors[0]], colors[cfg.colors[1]]
    cols = nv + 1
    free_rows = []          # [(rho, th, z, out)] 行ごと
    for a in range(nu + 1):
        u = a / nu
        fr = wh.free[a]
        ramp = smoothstep(0.0, 0.2, u)
        ang = angles[a]
        sr, sz = spine[a]
        nr, nz = -math.sin(ang), math.cos(ang)
        w = wh.w[a] * (L / wh.L)
        wrap = wh.wrap_u[a]
        tw = twist * wh.free_cum[a]
        ct, st = math.cos(tw), math.sin(tw)
        wv = wave * L * u * math.sin(2 * math.pi * cfg.wave_freq * u + wave_phase)
        spiral = p.bud_spiral * (1.0 - eb) * u
        alpha = wh.alpha[a]
        sw = sweep * L * u * u * wh.free_cum[a]
        row = []
        for c in range(nv + 1):
            v = -1.0 + 2.0 * c / nv
            av = abs(v)
            lat = v * w
            # 断面: くぼみ・竜骨の折れ・縁の反り返り・うねり・縁の波・細かい起伏
            n = cfg.cup * w * v * v + cfg.fold * w * av
            n -= cfg.reflex * w * smoothstep(0.55, 1.0, av) ** 2 * u
            n += wv
            n += ruffle * wh.W * (av ** 3) * u * math.sin(2 * math.pi * cfg.ruffle_freq * u + 2.0 * v + ph[0])
            n += cfg.detail * wh.W * u * (math.sin(17.0 * u + 5.0 * v + ph[1]) * math.sin(9.0 * u - 7.0 * v + ph[2])
                                          + 0.5 * math.sin(31.0 * u + 13.0 * v + ph[3]))
            n *= fr * play * ramp
            n -= cfg.midrib * wh.W * (1.0 - av) ** 4 * play * ramp
            lat += sw
            lat2 = lat * ct - n * st
            n2 = lat * st + n * ct
            # 重なり順のずれ (外向き / 下向き = -N)。角度に比例させるので、
            # 隣どうしの差はいつも「重なりのすき間」になり、つぼみではらせん状に重なる。
            th0 = angle_of(v * w, sr, wrap, w)
            off = wh.layer + p.overlap_gap * (th0 + alpha) / wh.spacing
            if not cfg.tube:
                off *= smoothstep(0.0, 0.12, u)
            n2 -= off
            R = sr + n2 * nr
            z = sz + n2 * nz
            rho_f = math.hypot(R, lat2)
            rho = lerp(rho_f, max(R, 0.0), wrap)
            th = angle_of(lat2, R, wrap, w)
            fl = env.outer_expanded(z, p.clearance + off)
            if fl is not None:
                rho = smax(rho, fl, p.clearance * 0.5)
            if cfg.tube:
                rho = max(rho, rec.tube_r + p.clearance + off) if z < 0 else rho
            th += az + spiral
            out = (math.sin(ang) * math.cos(th), math.sin(ang) * math.sin(th), -math.cos(ang))
            row.append((rho, th, z, off, v * wh.w_base if cfg.tube else 0.0, out))
        free_rows.append(row)

    # 筒の部分 (花托のドームから、開く部分の付け根まで)
    tube_rows = []
    if cfg.tube:
        nt = max(4, nu // 5)
        for f_i in range(nt):
            f = f_i / nt
            row = []
            for c in range(nv + 1):
                rho_t, th_t, z_t, off, latb, out_t = free_rows[0][c]
                rb = rec.tube_r + p.clearance + off
                zb = rec.dome_z(rb)
                thb = az + latb / rb
                z = lerp(zb, z_t, f)
                rho = tube_radius_at(env, rec, z, p.clearance + off)
                rho = lerp(rho, rho_t, smoothstep(0.6, 1.0, f))
                th = lerp(thb, th_t, smoothstep(0.0, 1.0, f))
                out = (math.cos(th), math.sin(th), 0.0)
                row.append((rho, th, z, off, latb, out))
            tube_rows.append(row)

    rows = tube_rows + free_rows
    nrows = len(rows)
    nt = len(tube_rows)
    green = colors["stem"]
    for ri, row in enumerate(rows):
        if ri < nt:
            u = -0.2 * (1.0 - ri / max(nt, 1))
            g = ri / max(nt, 1)
            col = tuple(lerp(x, y, 0.35 + 0.65 * g) for x, y in zip(green, c_base))
        else:
            u = (ri - nt) / nu
            col = tuple(lerp(x, y, smoothstep(0.0, 0.85, u)) for x, y in zip(c_base, c_tip))
        for c, (rho, th, z, off, latb, out) in enumerate(row):
            piece.verts.append((rho * math.cos(th), rho * math.sin(th), z))
            piece.outs.append(out)
            piece.uvs.append((c / nv, u))
            piece.colors.append(col)
    grid_faces(piece, 0, nrows, cols)
    piece.tube_rows = nt
    return piece


# ---------------------------------------------------------------------------
# しべ・茎
# ---------------------------------------------------------------------------

def tube(piece, path, radii, segs, close_start=True, close_end=True, color=(1, 1, 1), colors=None):
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
        col = colors[i] if colors else color
        for s in range(segs):
            a = 2 * math.pi * s / segs
            ca, sa = math.cos(a), math.sin(a)
            piece.verts.append((path[i][0] + r * (ca * sx + sa * ux),
                                path[i][1] + r * (ca * sy + sa * uy),
                                path[i][2] + r * (ca * sz + sa * uz)))
            piece.uvs.append((s / segs, i / (n - 1)))
            piece.colors.append(col)
    grid_faces(piece, start, n, segs, wrap=True)
    if close_start:
        c = len(piece.verts)
        piece.verts.append(path[0])
        piece.uvs.append((0.5, 0.0))
        piece.colors.append(colors[0] if colors else color)
        for s in range(segs):
            piece.faces.append((c, start + (s + 1) % segs, start + s))
    if close_end:
        c = len(piece.verts)
        piece.verts.append(path[-1])
        piece.uvs.append((0.5, 1.0))
        piece.colors.append(colors[-1] if colors else color)
        last = start + (n - 1) * segs
        for s in range(segs):
            piece.faces.append((c, last + s, last + (s + 1) % segs))


def build_collar(p, corona, env, colors):
    """器と花びらの筒のすき間を上からふさぐ、器のつけ根の細い輪。"""
    z = p.petal_attach * corona.H
    if z <= 0 or z >= corona.H * 0.5:
        return None
    e = env.outer_expanded(z, p.clearance * 0.6)
    if e is None:
        return None
    piece = Piece("Collar", 'CORONA')
    t = z / corona.H
    nphi = p.corona_res_phi
    col = colors["corona_base"]
    for ring in range(2):
        for j in range(nphi):
            phi = 2 * math.pi * j / nphi
            r = corona.point(t, phi)[0] if ring == 0 else e
            piece.verts.append((r * math.cos(phi), r * math.sin(phi), z))
            piece.uvs.append((j / nphi, ring))
            piece.colors.append(col)
    grid_faces(piece, 0, 2, nphi, wrap=True)
    return piece


def build_pistil(p, corona, colors):
    """めしべ: 器の底の中心から立つ花柱と、先の 3 つに分かれた柱頭。"""
    if not p.pistil:
        return None
    piece = Piece("Pistil", 'PISTIL')
    H = corona.H
    r_style = p.stamen_radius * 1.3
    z0 = corona.floor_z(r_style * 1.5)
    top = H * p.pistil_height
    steps = 28
    path, radii, cols = [], [], []
    for s_ in range(steps + 1):
        f = s_ / steps
        z = z0 + (top - z0) * f
        path.append((0.0, 0.0, z))
        rad = r_style * (1.35 - 0.35 * f)
        rad += r_style * 0.9 * smoothstep(0.9, 1.0, f)
        radii.append(rad)
        cols.append(tuple(lerp(a, b, f) for a, b in zip(colors["pistil_base"], colors["pistil"])))
    tube(piece, path, radii, 10, close_start=False, close_end=True, colors=cols)
    # 柱頭の 3 つの裂片
    for j in range(3):
        a0 = 2 * math.pi * j / 3
        lp, lr = [], []
        for s_ in range(9):
            f = s_ / 8
            rr = r_style * (0.6 + 3.2 * f)
            zz = top + r_style * (0.4 + 1.2 * math.sin(f * 2.2))
            lp.append((rr * math.cos(a0), rr * math.sin(a0), zz))
            lr.append(r_style * 0.75 * (1.0 - 0.6 * f))
        tube(piece, lp, lr, 8, color=colors["pistil"])
    return piece


def build_stamens(p, corona, env, rng, colors):
    """器の底から立つしべ。器の壁にも、となりのしべにも、めしべにも触れない位置に置く。"""
    pieces = []
    n = p.stamen_count
    if n <= 0:
        return pieces
    H = corona.H
    steps = 28
    gap = p.clearance
    jit = 0.15
    half = math.sin(math.pi / n * (1.0 - 2.0 * jit)) if n > 1 else 1.0
    pistil_r = (p.stamen_radius * 1.3 * 2.4 + p.stamen_radius * 1.3 * 4.0) if p.pistil else 0.0

    def profile(f):
        if f < 0.7:
            return p.stamen_radius * (1.15 - 0.3 * f)
        g = (f - 0.7) / 0.3
        return p.stamen_radius + (p.stamen_anther - p.stamen_radius) * math.sin(math.pi * min(g, 0.999)) ** 0.5

    heights = [H * p.stamen_height * (1.0 + rng.uniform(-0.1, 0.1)) for _ in range(n)]
    azs = [2 * math.pi * i / n + rng.uniform(-jit, jit) * 2 * math.pi / n for i in range(n)]
    r0 = max(corona.R * 0.14, pistil_r + p.stamen_radius * 1.2 + gap)
    # 付け根は器の底の壁に少しうめこむ (付いて見えるように)
    base_z = corona.floor_z(r0) - p.stamen_radius
    scale = 1.0
    for hgt in heights:
        for s_ in range(steps + 1):
            f = s_ / steps
            z = base_z + (hgt - base_z) * f
            lim = env.inner(z)
            if lim is None or f < 0.15:
                continue
            room = lim - p.clearance - (gap / (2 * half) if n > 1 else 0.0)
            rad = profile(f) * ((1.0 / half + 1.0) if n > 1 else 1.0)
            scale = min(scale, max(room, 0.0) / rad)
    scale = max(scale, 0.15)

    for i in range(n):
        piece = Piece("Stamen_%d" % i, 'STAMEN')
        path, radii, cols = [], [], []
        for s_ in range(steps + 1):
            f = s_ / steps
            z = base_z + (heights[i] - base_z) * f
            rad = profile(f) * scale
            rho = r0 + corona.R * p.stamen_spread * f * f
            if n > 1:
                rho = max(rho, (2 * rad + gap) / (2 * half))
            if p.pistil:
                rho = max(rho, pistil_r + rad + gap)
            lim = env.inner(z)
            if lim is not None and f >= 0.15:
                rho = min(rho, max(lim - p.clearance - rad, 0.0))
            ang = azs[i] + p.stamen_curve * f * f
            path.append((rho * math.cos(ang), rho * math.sin(ang), z))
            radii.append(rad)
            cols.append(colors["stamen"] if f > 0.68 else colors["filament"])
        radii[-1] = p.stamen_radius * scale * 0.3
        # 底にぴったり付ける (付け根は開いたまま器の面に接する)
        tube(piece, path, radii, 8, close_start=False, close_end=True, colors=cols)
        pieces.append(piece)
    return pieces


def build_ovary_stem(p, rec, colors):
    """子房 (花托のドーム + ふくらみ) と茎。花びらの筒とがくはドームの上に付く。"""
    pieces = []
    seg = 32
    ov = Piece("Ovary", 'STEM')
    prof = []
    nd = 8
    for i in range(1, nd + 1):
        r = rec.R * i / nd
        prof.append((r, rec.dome_z(r)))
    sr = p.corona_radius * p.stem_radius
    zr = rec.dome_z(rec.R)
    nb = 16
    for i in range(1, nb + 1):
        f = i / nb
        # 子房: 少しふくらんでから、なめらかに茎へ細くなる
        # 卵の下半分のような、ふっくらした形
        r = sr + (rec.R * (1.0 + 0.1 * math.sin(math.pi * f)) - sr) * math.sqrt(max(0.0, 1.0 - f ** 2.2))
        prof.append((r, zr - rec.length * f))
    # ドームの中心
    ov.verts.append((0.0, 0.0, rec.top))
    ov.uvs.append((0.5, 0.0))
    ov.colors.append(colors["ovary"])
    start = 1
    for k_, (r, z) in enumerate(prof):
        for s in range(seg):
            t = 2 * math.pi * s / seg
            ridge = 1.0 + 0.04 * math.cos(3 * t) * (1.0 if k_ >= nd else 0.0)
            ov.verts.append((r * ridge * math.cos(t), r * ridge * math.sin(t), z))
            ov.uvs.append((s / seg, k_ / len(prof)))
            ov.colors.append(colors["ovary"])
    for s in range(seg):
        ov.faces.append((0, start + s, start + (s + 1) % seg))
    for k_ in range(len(prof) - 1):
        for s in range(seg):
            a = start + k_ * seg + s
            b = start + k_ * seg + (s + 1) % seg
            ov.faces.append((a, b, b + seg, a + seg))
    pieces.append(ov)
    if p.stem and p.stem_length > 0:
        st = Piece("Stem", 'STEM')
        ztop = prof[-1][1]
        path, radii = [], []
        steps = 16
        for s in range(steps + 1):
            f = s / steps
            bend = p.stem_bend * p.stem_length * f * f
            path.append((bend, 0.0, ztop - p.stem_length * f))
            radii.append(prof[-1][0] if s == 0 else sr)
        tube(st, path, radii, seg, close_start=False, close_end=True, color=colors["stem"])
        pieces.append(st)
    return pieces


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------

def build_flower(p, bloom, colors, with_water=True):
    """花全体を作る。戻り値: (pieces, water_piece, water_volume, info)"""
    rng = random.Random(p.seed)
    corona = Corona(p, bloom, rng)
    env = corona.envelope()
    pieces = [build_corona(p, corona, colors)]
    # 花托の大きさは、花びらの層の厚み (重なりのずれ) が決まってから決める
    probe = Receptacle(p, corona, 0.0)
    L_bud = p.petal_length * p.bud_scale
    bud_curl = solve_bud_curl(p, corona, env, probe, math.radians(p.bud_tilt), L_bud)
    inner_parts = build_stamens(p, corona, env, random.Random(p.seed + 17), colors)
    pist = build_pistil(p, corona, colors)
    if pist:
        inner_parts.append(pist)
    env.add_obstacles(inner_parts)
    whorls, total_layer = setup_whorls(p, bloom, corona, env, probe, bud_curl)
    rec = Receptacle(p, corona, total_layer)
    if rec.R != probe.R:
        whorls, total_layer = setup_whorls(p, bloom, corona, env, rec, bud_curl)
    placed = []
    for wh in whorls:
        layer_pieces = []
        for i in range(wh.count):
            prng = random.Random(p.seed * 7919 + wh.k * 131 + i)
            layer_pieces.append(build_petal(p, wh, i, corona, env, rec, prng, colors))
        if placed:
            for pc in layer_pieces:
                for _ in range(2):
                    resolve_against(pc, placed, p.clearance, p.petal_res_v + 1,
                                    skip_rows=pc.tube_rows + 2, search=p.clearance * 3.0 + 0.04 * p.petal_length)
        placed.extend(layer_pieces)
        pieces.extend(layer_pieces)
    collar = build_collar(p, corona, env, colors)
    if collar:
        pieces.append(collar)
    pieces.extend(inner_parts)
    pieces.extend(build_ovary_stem(p, rec, colors))
    water, vol, level = (None, 0.0, 0.0)
    if with_water:
        water, vol, level = build_water(p, corona)
    info = {"rim_min_z": corona.rim_min_z(), "water_volume": vol, "water_level": level}
    return pieces, water, vol, info
