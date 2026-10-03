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


def refine_mirror_plane(plane, samples, nearest, iters=20):
    """Improve a mirror plane using the symmetry of the scan itself.

    The plane through a few centre-line clicks is fragile (a slightly turned
    head plus a protruding nose tilts it by 10-20 degrees). Here scan points
    are mirrored, matched to their closest scan points, and the plane is
    re-fitted to those pairs (normal along p - q, through the midpoints),
    ignoring the worst-matching 25 %."""
    c, n = (np.asarray(x, float) for x in plane)
    P = np.asarray(samples, float)
    if len(P) < 10:
        return c, n
    for _ in range(iters):
        Q = nearest(reflect(P, (c, n)))
        err = np.linalg.norm(reflect(Q, (c, n)) - P, axis=1)
        keep = err <= np.percentile(err, 75)
        D = P[keep] - Q[keep]
        D *= np.where(D @ n < 0, -1.0, 1.0)[:, None]
        if np.linalg.norm(D.sum(0)) < 1e-12:
            break
        n_new = D.sum(0) / np.linalg.norm(D.sum(0))
        c = ((P[keep] + Q[keep]) * 0.5).mean(0)
        if np.dot(n_new, n) > 0.999999:
            n = n_new
            break
        n = n_new
    return c, n


def reflect(V, plane):
    c, n = plane
    d = (V - c) @ n
    return V - 2.0 * d[:, None] * n


def vertex_normals(V, faces):
    F = np.asarray(faces)
    p = V[F]
    n = np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
    N = np.zeros_like(V)
    for k in range(4):
        np.add.at(N, F[:, k], n)
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


def make_snapper(nearest, ray=None, normal=None, inside=None):
    """Snap function ``snap(V, faces, maxd)``: each vertex goes to the closest
    surface hit along its +/- normal (keeps neighbouring vertices in order on
    curved areas), falling back to the closest surface point."""
    if ray is None:
        return lambda V, faces, maxd: nearest(V)

    def snap(V, faces, maxd):
        N = vertex_normals(V, faces)
        out = nearest(V)
        # A hit much further away than the local edge length is another part
        # of the scan (inside a nostril, the other lip...): use the closest
        # point instead, so no vertex shoots off as a spike.
        F = np.asarray(faces)
        el = np.linalg.norm(V[F] - V[np.roll(F, 1, axis=1)], axis=2)
        acc = np.zeros(len(V))
        cnt = np.zeros(len(V))
        for k in range(F.shape[1]):
            np.add.at(acc, F[:, k], el[:, k])
            np.add.at(cnt, F[:, k], 1)
        local = acc / np.maximum(cnt, 1)
        if inside is not None:
            (a0, a1), zone = inside
            ab = a1 - a0
            # Unclamped at the neck end: rays at the neck bottom stay level
            # instead of slanting down onto the shoulders.
            tt = np.clip(((V - a0) @ ab) / max(ab @ ab, 1e-12), 0.0, 2.0)
            axis_pts = a0 + tt[:, None] * ab
        else:
            zone = None
        for i in range(len(V)):
            if zone is not None and zone[i]:
                # Cast from the head / neck axis outwards: the first skin hit
                # is the scalp or the neck on the vertex's own side, never an
                # ear sticking out of it, even if the vertex sank inside.
                c = axis_pts[i]
                d = V[i] - c
                dist = float(np.linalg.norm(d))
                if dist > 1e-9:
                    hit = ray(c, d / dist, dist * 2.5)
                    if hit is not None:
                        out[i] = hit
                        continue
            best = None
            lim = min(maxd, local[i] * 3.0)
            for sgn in (1.0, -1.0):
                hit = ray(V[i], N[i] * sgn, lim)
                if hit is not None and normal is not None:
                    # Skip surfaces facing the other way (the back of an ear,
                    # the inside of a nostril seen from outside...).
                    hn = normal(hit)
                    if hn is not None and float(np.dot(hn, N[i])) < 0.0:
                        hit = None
                if hit is not None:
                    d = np.linalg.norm(hit - V[i])
                    if best is None or d < best[0]:
                        best = (d, hit)
            if best is not None:
                out[i] = best[1]
        return out
    return snap


def conform(V, faces, pins, nearest, iters=40, lam=0.5, mirror=None, plane=None, ray=None,
            rest_shape=None, normal=None, inside=None):
    """Make the warped template ``V`` lie on the surface while keeping its
    shape: alternate a Laplacian step that preserves the warped template's
    own Laplacian (so the designed spacing/edge flow survives) with snapping
    to the surface along the vertex normals. ``pins`` = (indices, targets).
    With a mirror map the halves are pulled towards symmetry, but the last
    iterations and the final snap leave every vertex on the real surface."""
    V = np.array(V, float)
    adj = Adjacency(len(V), faces)
    R0 = V if rest_shape is None else np.asarray(rest_shape, float)
    rest = adj.average(R0) - R0
    pin_idx, pin_co = pins
    size = float(np.linalg.norm(V.max(0) - V.min(0)))
    snap = make_snapper(nearest, ray, normal, inside)
    for it in range(iters):
        V = V + lam * ((adj.average(V) - V) - rest)
        V = snap(V, faces, size * 0.15)
        V[pin_idx] = pin_co
        if mirror is not None and plane is not None and it < iters - 5:
            V = 0.5 * (V + reflect(V[mirror], plane))
    V = snap(V, faces, size * 0.15)
    V[pin_idx] = pin_co
    return V


def fit_template(T0, faces, lm_index, lm_target, nearest, iters=40,
                 mirror=None, plane=None, ray=None, normal=None, inside=None):
    """Full pipeline for a template with vertex positions ``T0``."""
    T0 = np.asarray(T0, float)
    src = T0[lm_index]
    s, R, t = similarity(src, lm_target)
    V = (s * (R @ T0.T)).T + t
    V = rbf_warp(V, V[lm_index], lm_target)
    flipped = np.linalg.det(R) < 0
    if flipped:
        faces = [tuple(reversed(f)) for f in faces]
    V = conform(V, faces, (np.asarray(lm_index), np.asarray(lm_target, float)), nearest,
                iters=iters, mirror=mirror, plane=plane, ray=ray, normal=normal,
                inside=inside)
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


def relax_on_surface(V, faces, nearest_one, iters, lam=0.4):
    """Even out stretched quads (thumb base, web strips) by Laplacian
    smoothing followed by snapping back onto the scan. A vertex whose snap
    would jump far (onto a neighbouring finger) keeps its old position."""
    adj = Adjacency(len(V), faces)
    counts = {}
    for f in faces:
        for k in range(4):
            e = (min(f[k], f[(k + 1) % 4]), max(f[k], f[(k + 1) % 4]))
            counts[e] = counts.get(e, 0) + 1
    boundary = {v for e, c in counts.items() if c == 1 for v in e}
    movable = np.array([i not in boundary for i in range(len(V))])
    F = np.asarray(faces)
    for _ in range(iters):
        el = np.linalg.norm(V[F] - V[np.roll(F, 1, axis=1)], axis=2).mean(1)
        local = np.zeros(len(V))
        cnt = np.zeros(len(V))
        for k in range(4):
            np.add.at(local, F[:, k], el)
            np.add.at(cnt, F[:, k], 1)
        local /= np.maximum(cnt, 1)
        target = V + lam * (adj.average(V) - V)
        for i in np.nonzero(movable)[0]:
            p = nearest_one(target[i])
            if np.linalg.norm(p - target[i]) < local[i] * 0.6:
                V[i] = p
    return V


def smooth_border(V, faces, mask, nearest_one, iters=15, lam=0.5):
    """Smooth an open border (e.g. the bottom of the neck) along itself:
    each flagged border vertex moves towards the average of its two border
    neighbours and is put back on the surface."""
    count = {}
    for f in faces:
        for k in range(len(f)):
            e = (min(f[k], f[(k + 1) % len(f)]), max(f[k], f[(k + 1) % len(f)]))
            count[e] = count.get(e, 0) + 1
    nb = {}
    for (a, b), c in count.items():
        if c == 1 and mask[a] and mask[b]:
            nb.setdefault(a, []).append(b)
            nb.setdefault(b, []).append(a)
    ids = [v for v, n in nb.items() if len(n) == 2]
    V = V.copy()
    for _ in range(iters):
        new = {v: V[v] + lam * ((V[nb[v][0]] + V[nb[v][1]]) * 0.5 - V[v]) for v in ids}
        for v, p in new.items():
            V[v] = nearest_one(p)
    return V
