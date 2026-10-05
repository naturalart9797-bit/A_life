# SPDX-License-Identifier: GPL-3.0-or-later
"""成長する膜としての花びら (numpy のみ)。

花びらの形を数式で描くのではなく、植物の花びらと同じ仕組みで「結果として」出す。

  * 参照形 (原基): 成長前の平らな花びら。格子の (s, v) は
      s = 付け根 0 → 先端 1、 v = 左の縁 -1 → 右の縁 +1
    v が一定の線が平行脈 (単子葉植物の脈は付け根と先端で集まる)。
  * 二層の膜: 表 (向軸側) の表皮と裏 (背軸側) の表皮を、厚みの分だけ離した
    2 枚の格子にして、厚み方向とななめのバネでつなぐ。
  * 成長: 場所ごと・方向ごと・層ごとの伸び率 (成長テンソル)。
      - 縁が中央より伸びる → 余った長さが座屈してフリルになる
      - 脈は伸びにくく硬い → 脈と脈のあいだがわずかにふくらむ
      - 表と裏の伸びの差 → 花びらが曲がる (付け根の表側が伸びると外へ開く)
  * 力学: 成長後の長さを満たそうとするバネのエネルギーを L-BFGS で最小化して、
    釣り合いの形を求める。曲がりも波打ちも、この結果として出てくる。
"""

import math

import numpy as np


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# バネの網と、その釣り合いを解くソルバー
# ---------------------------------------------------------------------------

class SpringNet:
    """頂点 X (N, 3) と、辺 (a, b, 目標長 L, 硬さ k) の網。"""

    def __init__(self, X, a, b, L, k, pinned):
        self.X = np.ascontiguousarray(X, dtype=np.float64)
        self.a = a
        self.b = b
        self.L = np.maximum(L, 1e-9)
        self.k = k
        self.pinned = pinned
        n = len(X)
        kl = k / self.L
        self.mass = np.maximum(np.bincount(a, kl, n) + np.bincount(b, kl, n), 1e-12)
        self.iterations = 0

    def energy_grad(self, X):
        d = X[self.b] - X[self.a]
        ln = np.sqrt(np.einsum("ij,ij->i", d, d))
        dl = ln - self.L
        kl = self.k / self.L
        E = 0.5 * float(np.dot(kl, dl * dl))
        f = (kl * dl / np.maximum(ln, 1e-12))[:, None] * d
        n = len(X)
        G = np.empty_like(X)
        for c in range(3):
            G[:, c] = np.bincount(self.b, f[:, c], n) - np.bincount(self.a, f[:, c], n)
        return E, G

    def solve(self, iters=2000, tol=1e-6, memory=12, verbose=False):
        """前処理つき L-BFGS (固定点は動かさない)。"""
        free = (~self.pinned)[:, None]
        x = self.X.copy()
        P = 1.0 / self.mass[:, None]
        E, G = self.energy_grad(x)
        G = np.where(free, G, 0.0)
        g0 = float(np.max(np.abs(G))) + 1e-300
        S, Y, RHO = [], [], []
        it = 0
        for it in range(iters):
            q = G.copy()
            al = []
            for s_, y_, r_ in reversed(list(zip(S, Y, RHO))):
                a_ = r_ * float(np.sum(s_ * q))
                al.append(a_)
                q -= a_ * y_
            if S:
                gamma = float(np.sum(S[-1] * Y[-1])) / max(float(np.sum(Y[-1] * P * Y[-1])), 1e-300)
                z = gamma * P * q
            else:
                z = P * q
            for (s_, y_, r_), a_ in zip(zip(S, Y, RHO), reversed(al)):
                b_ = r_ * float(np.sum(y_ * z))
                z += s_ * (a_ - b_)
            d = -np.where(free, z, 0.0)
            gd = float(np.sum(G * d))
            if gd >= 0:
                S, Y, RHO = [], [], []
                d = -np.where(free, P * G, 0.0)
                gd = float(np.sum(G * d))
            step = 1.0
            ok = False
            for _ in range(25):
                xn = x + step * d
                En, Gn = self.energy_grad(xn)
                if En <= E + 1e-4 * step * gd:
                    ok = True
                    break
                step *= 0.5
            if not ok:
                break
            Gn = np.where(free, Gn, 0.0)
            s_ = xn - x
            y_ = Gn - G
            sy = float(np.sum(s_ * y_))
            if sy > 1e-300:
                S.append(s_)
                Y.append(y_)
                RHO.append(1.0 / sy)
                if len(S) > memory:
                    S.pop(0)
                    Y.pop(0)
                    RHO.pop(0)
            x, E, G = xn, En, Gn
            if verbose and it % 250 == 0:
                print("  it %d  E %.3e  step %.3g" % (it, E, step))
            if float(np.max(np.abs(G))) < tol * g0:
                break
        self.X = x
        self.iterations = it + 1
        return x

    def strain(self):
        d = self.X[self.b] - self.X[self.a]
        return np.sqrt(np.einsum("ij,ij->i", d, d)) / self.L - 1.0


# ---------------------------------------------------------------------------
# 構造格子の二層膜
# ---------------------------------------------------------------------------

class Bilayer:
    """(ns+1, nv+1) の格子を 2 層 (0 = 裏, 1 = 表) 重ねた膜。周期 (筒) も可。"""

    def __init__(self, ns1, nv1, periodic=False):
        self.ns1, self.nv1 = ns1, nv1
        self.periodic = periodic

    def vid(self, i, j, layer):
        jj = j % self.nv1 if self.periodic else j
        return (layer * self.ns1 + i) * self.nv1 + jj

    def build(self, R, gs, gv, curv_s, curv_v, thick, stiff_s, stiff_v, pinned_rows, period=None):
        """
        R:      参照形 (ns1, nv1, 2)  [長さ方向, 横方向]
        gs, gv: 両層の平均の成長率 (長さ方向・横方向)
        curv_s, curv_v: 表裏の成長差で生じさせたい曲がり (表の法線の側を正)
        thick:  厚み (ns1, nv1)
        stiff_s, stiff_v: 伸びの硬さの倍率 (脈など)
        """
        ns1, nv1 = self.ns1, self.nv1
        I, J = np.meshgrid(np.arange(ns1), np.arange(nv1), indexing="ij")
        A, B, L, K = [], [], [], []

        def fld(F, i, j):
            return F[i, j % nv1 if self.periodic else j]

        def ref_vec(i0, j0, i1, j1):
            d = fld(R, i1, j1) - fld(R, i0, j0)
            if self.periodic:
                d = d.copy()
                d[..., 1] += np.where(j1 >= nv1, period, 0.0) - np.where(j1 < 0, period, 0.0)
            return d

        def pairs(di, dj):
            i1, j1 = I + di, J + dj
            m = i1 < ns1
            if not self.periodic:
                m &= (j1 >= 0) & (j1 < nv1)
            return I[m], J[m], i1[m], j1[m]

        offsets = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for di, dj in offsets:
            i0, j0, i1, j1 = pairs(di, dj)
            d = ref_vec(i0, j0, i1, j1)

            def avg(F):
                return 0.5 * (fld(F, i0, j0) + fld(F, i1, j1))

            g_s, g_v = avg(gs), avg(gv)
            c_s, c_v = avg(curv_s), avg(curv_v)
            t = avg(thick)
            ds, dv = d[..., 0], d[..., 1]
            ln0 = np.sqrt(ds * ds + dv * dv)
            ws = (ds / np.maximum(ln0, 1e-12)) ** 2
            wv = 1.0 - ws
            stiff = (ws * avg(stiff_s) + wv * avg(stiff_v)) * (0.6 if (di and dj) else 1.0)
            for layer, sign in ((0, +1.0), (1, -1.0)):
                # 裏 (層 0) が多く伸びると表の側へ曲がる: 層の伸び = 1 ± κ t / 2
                fs = g_s * (1.0 + sign * 0.5 * c_s * t)
                fv = g_v * (1.0 + sign * 0.5 * c_v * t)
                A.append(self.vid(i0, j0, layer))
                B.append(self.vid(i1, j1, layer))
                L.append(np.sqrt((ds * fs) ** 2 + (dv * fv) ** 2))
                K.append(t * stiff)
        # 厚み方向のバネ (表と裏をつなぐ) と、ななめのバネ (層のずれを止める)
        A.append(self.vid(I, J, 0).ravel())
        B.append(self.vid(I, J, 1).ravel())
        L.append(thick.ravel())
        K.append((thick * 2.0).ravel())
        for di, dj in offsets[:2]:
            i0, j0, i1, j1 = pairs(di, dj)
            d = ref_vec(i0, j0, i1, j1)
            ad = np.abs(d)
            g = ((fld(gs, i0, j0) + fld(gs, i1, j1)) * ad[..., 0]
                 + (fld(gv, i0, j0) + fld(gv, i1, j1)) * ad[..., 1]) * 0.5 / np.maximum(ad.sum(-1), 1e-12)
            h = np.sqrt((d ** 2).sum(-1)) * g
            t = 0.5 * (fld(thick, i0, j0) + fld(thick, i1, j1))
            for la, lb in ((0, 1), (1, 0)):
                A.append(self.vid(i0, j0, la))
                B.append(self.vid(i1, j1, lb))
                L.append(np.sqrt(h * h + t * t))
                K.append(t * 0.8)
        a = np.concatenate([x.ravel() for x in A]).astype(np.int64)
        b = np.concatenate([x.ravel() for x in B]).astype(np.int64)
        L = np.concatenate([x.ravel() for x in L])
        K = np.concatenate([x.ravel() for x in K])
        pin = np.zeros(2 * ns1 * nv1, dtype=bool)
        for layer in (0, 1):
            for i in pinned_rows:
                pin[self.vid(i, np.arange(nv1), layer)] = True
        return a, b, L, K, pin

    def layers(self, X):
        return X.reshape(2, self.ns1, self.nv1, 3)


def grid_normals(X, periodic=False):
    ts = np.gradient(X, axis=0)
    if periodic:
        tv = 0.5 * (np.roll(X, -1, axis=1) - np.roll(X, 1, axis=1))
    else:
        tv = np.gradient(X, axis=1)
    n = np.cross(ts, tv)
    return n / np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)


def upsample(X, shape, periodic=False):
    """粗い格子 (ns1, nv1, 3) の形を細かい格子へ (双線形)。"""
    ns1, nv1 = X.shape[:2]
    ms1, mv1 = shape
    si = np.linspace(0, ns1 - 1, ms1)
    i0 = np.clip(np.floor(si).astype(int), 0, ns1 - 2)
    fi = (si - i0)[:, None, None]
    if periodic:
        vi = np.arange(mv1) / mv1 * nv1
        j0 = np.floor(vi).astype(int) % nv1
        j1 = (j0 + 1) % nv1
        fj = (vi - np.floor(vi))[None, :, None]
    else:
        vi = np.linspace(0, nv1 - 1, mv1)
        j0 = np.clip(np.floor(vi).astype(int), 0, nv1 - 2)
        j1 = j0 + 1
        fj = (vi - j0)[None, :, None]
    A, Bv = X[i0][:, j0], X[i0][:, j1]
    C, D = X[i0 + 1][:, j0], X[i0 + 1][:, j1]
    return A * (1 - fi) * (1 - fj) + Bv * (1 - fi) * fj + C * fi * (1 - fj) + D * fi * fj


def grow(fields, init, levels, iters, periodic=False, period=None, verbose=False):
    """粗い格子から順に解いて細かい格子へ受け渡す。中面 (ns1, nv1, 3) と情報を返す。

    fields(ns, nv) -> dict(R, gs, gv, curv_s, curv_v, thick, stiff_s, stiff_v, pinned_rows)
    init(F) -> 初期の中面 (ns1, nv1, 3)
    """
    mid = None
    info = {}
    for li, (ns, nv) in enumerate(levels):
        F = fields(ns, nv)
        ns1, nv1 = F["R"].shape[:2]
        bl = Bilayer(ns1, nv1, periodic)
        a, b, L, K, pin = bl.build(F["R"], F["gs"], F["gv"], F["curv_s"], F["curv_v"], F["thick"],
                                   F["stiff_s"], F["stiff_v"], F["pinned_rows"], period=period)
        start = init(F)
        if mid is None:
            m0 = start
        else:
            m0 = upsample(mid, (ns1, nv1), periodic)
            for i in F["pinned_rows"]:
                m0[i] = start[i]
        h = 0.5 * F["thick"][..., None]
        nrm = grid_normals(m0, periodic)
        X2 = np.stack([m0 - nrm * h, m0 + nrm * h], axis=0)
        net = SpringNet(X2.reshape(-1, 3), a, b, L, K, pin)
        net.solve(iters=iters[li], verbose=verbose)
        lay = bl.layers(net.X)
        mid = 0.5 * (lay[0] + lay[1])
        info = {"strain99": float(np.percentile(np.abs(net.strain()), 99)),
                "iterations": net.iterations, "fields": F}
        if verbose:
            print("level", ns, nv, "iters", info["iterations"], "strain99 %.4f" % info["strain99"])
    return mid, info


# ---------------------------------------------------------------------------
# スイセンの外花被片
# ---------------------------------------------------------------------------

def tepal_outline(s, width=0.42, widest=0.42, base=0.07, mucro=0.03):
    """外花被片の半幅 (長さ 1 に対して)。卵形で先は鋭く、先端に小さな突起 (微突起)。"""
    a = math.log(0.5) / math.log(widest)
    body = np.sin(np.pi * np.clip(s, 0, 1) ** a) ** 0.9
    w = width * body + base * (1.0 - s) ** 2
    tip = smoothstep(0.92, 1.0, s)
    w = w * (1.0 - tip) + mucro * np.clip(1.0 - s, 0, None) / 0.08 * tip
    return np.maximum(w, 0.003)


def make_tepal(length=1.0, res=(96, 40), seed=1, margin_growth=0.10, vein_count=13,
               vein_relief=0.015, cup=1.5, keel=5.0, reflex=-0.6, hinge=-3.0,
               thickness=0.012, thickness_tip=0.45, iters=(1500, 1200, 1000), levels=None, verbose=False):
    """外花被片 1 枚を成長させる。付け根は x 軸方向、表 (向軸側) は +z。中面を返す。"""

    def fields(ns, nv):
        s = np.linspace(0.0, 1.0, ns + 1)[:, None] * np.ones((1, nv + 1))
        v = np.linspace(-1.0, 1.0, nv + 1)[None, :] * np.ones((ns + 1, 1))
        w = tepal_outline(s[:, 0])[:, None]
        R = np.stack([s * length, v * w * length], axis=2)
        av = np.abs(v)
        edge = smoothstep(0.55, 1.0, av) ** 2
        gs = 1.0 + margin_growth * edge * smoothstep(0.15, 0.9, s)
        gv = np.ones_like(s)
        vein_pos = np.linspace(-1, 1, vein_count)
        dvein = np.min(np.abs(v[..., None] - vein_pos[None, None, :]), axis=2)
        on_vein = np.exp(-(dvein / (0.35 * 2.0 / (vein_count - 1))) ** 2)
        gs = gs * (1.0 + vein_relief * (1.0 - on_vein) * smoothstep(0.05, 0.3, s))
        curv_v = (cup * (1.0 - s) ** 1.5 + keel * np.exp(-(v / 0.07) ** 2) * (0.3 + 0.7 * (1 - s))) / length
        curv_s = (hinge * np.exp(-(s / 0.07) ** 2) + reflex * smoothstep(0.1, 0.8, s)) / length
        thick = thickness * length * (1.0 + (thickness_tip - 1.0) * s) * (1.0 - 0.4 * av ** 2)
        return dict(R=R, gs=gs, gv=gv, curv_s=curv_s, curv_v=curv_v, thick=thick,
                    stiff_s=1.0 + 2.0 * on_vein, stiff_v=np.ones_like(s), pinned_rows=(0, 1), s=s, v=v)

    def init(F):
        R, s = F["R"], F["s"]
        X = np.zeros(R.shape[:2] + (3,))
        X[..., 0] = R[..., 0]
        X[..., 1] = R[..., 1]
        rng = np.random.default_rng(seed + R.shape[0])
        X[..., 2] = rng.normal(0.0, 0.002 * length, size=s.shape) * s
        return X

    levels = levels or [(res[0] // 4, res[1] // 4), (res[0] // 2, res[1] // 2), res]
    return grow(fields, init, levels, iters, verbose=verbose)


# ---------------------------------------------------------------------------
# スイセンの副花冠 (筒)
# ---------------------------------------------------------------------------

def make_corona(radius=0.16, height=0.45, res=(64, 224), seed=2, flare=0.8, rim_growth=0.6,
                rim_width=0.2, rim_lengthen=0.06, vein_count=56, vein_relief=0.012, roll=-6.0,
                thickness=0.012, thickness_rim=0.4, iters=(1500, 1200, 1000), levels=None, verbose=False):
    """副花冠を、付け根の輪を固定した筒として成長させる。

    縁の周方向の成長が大きいほど縁は広がり、広がりきれない余りがフリルになる。
    縁ほど薄いので、フリルは縁ほど細かい。
    """
    def fields(ns, nt):
        s = np.linspace(0.0, 1.0, ns + 1)[:, None] * np.ones((1, nt))
        th = (np.arange(nt) / nt * 2 * math.pi)[None, :] * np.ones((ns + 1, 1))
        R = np.stack([s * height, th * radius], axis=2)
        rim = smoothstep(1.0 - rim_width, 1.0, s)
        gv = (1.0 + flare * s ** 2.2) * (1.0 + rim_growth * rim ** 1.5)
        gs = 1.0 + rim_lengthen * rim
        vein_pos = np.arange(vein_count) / vein_count * 2 * math.pi
        dth = np.abs(((th[..., None] - vein_pos[None, None, :]) + math.pi) % (2 * math.pi) - math.pi)
        on_vein = np.exp(-(np.min(dth, axis=2) / (0.35 * 2 * math.pi / vein_count)) ** 2)
        gs = gs * (1.0 + vein_relief * (1.0 - on_vein))
        curv_s = roll * smoothstep(0.85, 1.0, s) / height
        thick = thickness * height * (1.0 + (thickness_rim - 1.0) * smoothstep(0.3, 1.0, s))
        return dict(R=R, gs=gs, gv=gv, curv_s=curv_s, curv_v=np.zeros_like(s), thick=thick,
                    stiff_s=1.0 + 2.0 * on_vein, stiff_v=np.ones_like(s), pinned_rows=(0, 1), s=s, th=th)

    def init(F):
        s, th = F["s"], F["th"]
        rng = np.random.default_rng(seed + s.shape[0])
        rr = radius * (1.0 + 0.004 * rng.normal(size=s.shape) * s)
        X = np.zeros(s.shape + (3,))
        X[..., 0] = rr * np.cos(th)
        X[..., 1] = rr * np.sin(th)
        X[..., 2] = s * height
        return X

    levels = levels or [(res[0] // 4, res[1] // 4), (res[0] // 2, res[1] // 2), res]
    return grow(fields, init, levels, iters, periodic=True, period=2 * math.pi * radius, verbose=verbose)


def grid_faces(ns1, nv1, periodic=False):
    faces = []
    cols = nv1 if periodic else nv1 - 1
    for i in range(ns1 - 1):
        for j in range(cols):
            j2 = (j + 1) % nv1
            a = i * nv1 + j
            b = i * nv1 + j2
            faces.append((a, b, b + nv1, a + nv1))
    return faces
