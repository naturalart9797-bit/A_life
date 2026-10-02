# SPDX-License-Identifier: GPL-3.0-or-later
"""Template fitting: align -> RBF warp -> conform to the scan surface.

Pure numpy; the scan is reached only through ``nearest(points)`` so this can
be tested without Blender's UI.
"""

import numpy as np


def similarity(src, dst, allow_reflection=True):
    """Least-squares similarity (Umeyama). Returns (s, R, t): dst ~ s R src + t."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, S, Vt = np.linalg.svd(cov)
    D = np.eye(3)
    if not allow_reflection and np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    var = (xs ** 2).sum() / len(src)
    s = np.trace(np.diag(S) @ D) / max(var, 1e-12)
    t = mu_d - s * R @ mu_s
    return s, R, t


def rbf_warp(points, src, dst, smooth=1e-6):
    """Biharmonic RBF (phi = r) + affine part mapping ``src`` onto ``dst``."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    k = len(src)
    K = np.linalg.norm(src[:, None, :] - src[None, :, :], axis=2)
    K += np.eye(k) * smooth * max(K.max(), 1e-9)
    P = np.hstack([np.ones((k, 1)), src])
    A = np.zeros((k + 4, k + 4))
    A[:k, :k] = K
    A[:k, k:] = P
    A[k:, :k] = P.T
    b = np.zeros((k + 4, 3))
    b[:k] = dst - src
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    w, a = sol[:k], sol[k:]
    pts = np.asarray(points, float)
    Kp = np.linalg.norm(pts[:, None, :] - src[None, :, :], axis=2)
    return pts + Kp @ w + np.hstack([np.ones((len(pts), 1)), pts]) @ a


class Adjacency:

    def __init__(self, n, faces):
        edges = set()
        for f in faces:
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                edges.add((min(a, b), max(a, b)))
        e = np.array(sorted(edges), dtype=np.int64)
        self.e0, self.e1 = e[:, 0], e[:, 1]
        self.deg = np.bincount(np.concatenate([self.e0, self.e1]), minlength=n).astype(float)
        self.deg[self.deg == 0] = 1.0
        self.n = n

    def average(self, V):
        acc = np.zeros_like(V)
        np.add.at(acc, self.e0, V[self.e1])
        np.add.at(acc, self.e1, V[self.e0])
        return acc / self.deg[:, None]


def mirror_plane(points):
    """Best-fit plane (point, normal) through roughly coplanar centre points."""
    p = np.asarray(points, float)
    c = p.mean(0)
    _u, _s, vt = np.linalg.svd(p - c)
    return c, vt[2]


def reflect(V, plane):
    c, n = plane
    d = (V - c) @ n
    return V - 2.0 * d[:, None] * n


def conform(V, faces, pins, nearest, iters=40, lam=0.5, mirror=None, plane=None):
    """Make the warped template ``V`` lie on the surface while keeping its
    shape: alternate a Laplacian step that preserves the warped template's
    own Laplacian (so the designed spacing/edge flow survives) with snapping
    to the closest surface point. ``pins`` = (indices, targets)."""
    V = np.array(V, float)
    adj = Adjacency(len(V), faces)
    rest = adj.average(V) - V
    pin_idx, pin_co = pins
    for it in range(iters):
        V = V + lam * ((adj.average(V) - V) - rest)
        V = nearest(V)
        V[pin_idx] = pin_co
        if mirror is not None and plane is not None:
            V = 0.5 * (V + reflect(V[mirror], plane))
    V = nearest(V)
    V[pin_idx] = pin_co
    if mirror is not None and plane is not None:
        V = 0.5 * (V + reflect(V[mirror], plane))
        V = nearest(V)
    return V


def fit_template(T0, faces, lm_index, lm_target, nearest, iters=40,
                 mirror=None, plane=None):
    """Full pipeline for a template with vertex positions ``T0``."""
    T0 = np.asarray(T0, float)
    src = T0[lm_index]
    s, R, t = similarity(src, lm_target)
    V = (s * (R @ T0.T)).T + t
    V = rbf_warp(V, V[lm_index], lm_target)
    flipped = np.linalg.det(R) < 0
    V = conform(V, faces, (np.asarray(lm_index), np.asarray(lm_target, float)), nearest,
                iters=iters, mirror=mirror, plane=plane)
    if flipped:
        faces = [tuple(reversed(f)) for f in faces]
    return V, faces


def orient_faces(V, faces, normal_at=None):
    """Make face winding consistent across each connected piece and pointing
    outwards (agreeing with ``normal_at(point)`` of the scan when given)."""
    faces = [list(f) for f in faces]
    edge_faces = {}
    for fi, f in enumerate(faces):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(fi)
    seen = [False] * len(faces)
    for start in range(len(faces)):
        if seen[start]:
            continue
        seen[start] = True
        comp = [start]
        stack = [start]
        while stack:
            fi = stack.pop()
            f = faces[fi]
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                for gi in edge_faces[(min(a, b), max(a, b))]:
                    if seen[gi]:
                        continue
                    g = faces[gi]
                    # A consistent neighbour traverses the shared edge b -> a.
                    same = any(g[m] == a and g[(m + 1) % len(g)] == b for m in range(len(g)))
                    if same:
                        g.reverse()
                    seen[gi] = True
                    comp.append(gi)
                    stack.append(gi)
        if normal_at is not None:
            vote = 0.0
            for fi in comp:
                p = V[faces[fi]]
                n = np.cross(p[1] - p[0], p[2] - p[0]) + np.cross(p[2] - p[0], p[3 % len(p)] - p[0])
                sn = normal_at(p.mean(0))
                if sn is not None:
                    vote += np.sign(n.dot(sn))
            if vote < 0:
                for fi in comp:
                    faces[fi].reverse()
    return [tuple(f) for f in faces]
