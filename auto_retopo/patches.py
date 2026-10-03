# SPDX-License-Identifier: GPL-3.0-or-later
"""Quad patch layouts -> all-quad meshes.

A layout is a set of named corner points, curves between them and 4-sided
patches. Every patch is filled with an n x m grid (Coons interpolation of its
four sides). Opposite sides of a patch must have the same number of segments;
those constraints are propagated along "chords" so the whole layout gets a
consistent, watertight subdivision. This is how the ideal topology (edge loops
around the eyes / mouth, poles where we want them) is designed in code.
"""

import math

import numpy as np


def line(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return lambda t: a + (b - a) * t


def ellipse_arc(center, rx, ry, a0, a1):
    """Arc from angle a0 to a1 (radians) on an axis-aligned ellipse."""
    cx, cy = center

    def fn(t):
        a = a0 + (a1 - a0) * t
        return np.array([cx + rx * math.cos(a), cy + ry * math.sin(a)])
    return fn


def bezier(a, c, b):
    a, c, b = (np.asarray(p, dtype=float) for p in (a, c, b))
    return lambda t: (1 - t) ** 2 * a + 2 * (1 - t) * t * c + t ** 2 * b


class Layout:

    def __init__(self):
        self.points = {}
        self.curves = {}
        self.patches = []
        self.npatches = []

    def point(self, name, uv):
        self.points[name] = np.asarray(uv, dtype=float)

    def curve(self, a, b, fn):
        """Curve from point ``a`` to point ``b`` parametrised on [0, 1]."""
        self.curves[(a, b)] = fn

    def patch(self, a, b, c, d):
        self.patches.append((a, b, c, d))

    def npatch(self, *corners):
        """An odd-sided patch (3 or 5 sides) filled with one quad per corner
        around a centre pole (valence = number of sides). All its sides get
        the same, even segment count and are split at their midpoints."""
        if len(corners) % 2 == 0:
            raise ValueError("npatch needs an odd number of corners")
        self.npatches.append(tuple(corners))

    # ------------------------------------------------------------------

    def _curve(self, a, b):
        if (a, b) in self.curves:
            return self.curves[(a, b)]
        if (b, a) in self.curves:
            fn = self.curves[(b, a)]
            return lambda t: fn(1.0 - t)
        return line(self.points[a], self.points[b])

    def chords(self):
        """Group undirected edges that must share a segment count."""
        parent = {}

        def key(a, b):
            return (a, b) if a <= b else (b, a)

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for a, b, c, d in self.patches:
            union(key(a, b), key(c, d))
            union(key(b, c), key(d, a))
        for cs in self.npatches:
            n = len(cs)
            for i in range(1, n):
                union(key(cs[0], cs[1]), key(cs[i], cs[(i + 1) % n]))
        groups = {}
        for k in list(parent):
            groups.setdefault(find(k), []).append(k)
        return list(groups.values()), key

    def build(self, counts, default=2):
        """Return (uv array (N, 2), faces list of 4-tuples, point index map).

        ``counts`` maps any edge (a, b) of a chord to its segment count."""
        groups, key = self.chords()
        seg = {}
        for g in groups:
            n = None
            for e in g:
                for k in (e, (e[1], e[0])):
                    if k in counts:
                        if n is not None and n != counts[k]:
                            raise ValueError(f"conflicting counts on chord {g}")
                        n = counts[k]
            n = n or default
            for e in g:
                seg[e] = n

        verts = []
        index = {}

        def vid(k, uv):
            if k not in index:
                index[k] = len(verts)
                verts.append(np.asarray(uv, dtype=float))
            return index[k]

        point_ids = {name: vid(("P", name), uv) for name, uv in self.points.items()}

        def edge_vid(a, b, i, n):
            """Vertex i (0..n) along edge a->b, shared with the reverse edge."""
            if i == 0:
                return point_ids[a]
            if i == n:
                return point_ids[b]
            if a <= b:
                k = ("E", a, b, i)
            else:
                k = ("E", b, a, n - i)
            return vid(k, self._curve(a, b)(i / n))

        faces = []
        for pi, (a, b, c, d) in enumerate(self.patches):
            n = seg[key(a, b)]
            m = seg[key(b, c)]
            c0 = self._curve(a, b)   # s, t = 0
            c1 = self._curve(d, c)   # s, t = 1
            d0 = self._curve(a, d)   # s = 0, t
            d1 = self._curve(b, c)   # s = 1, t
            pa, pb, pc, pd = (self.points[x] for x in (a, b, c, d))
            grid = [[None] * (m + 1) for _ in range(n + 1)]
            for i in range(n + 1):
                for j in range(m + 1):
                    if j == 0:
                        grid[i][j] = edge_vid(a, b, i, n)
                    elif j == m:
                        grid[i][j] = edge_vid(d, c, i, n)
                    elif i == 0:
                        grid[i][j] = edge_vid(a, d, j, m)
                    elif i == n:
                        grid[i][j] = edge_vid(b, c, j, m)
                    else:
                        s, t = i / n, j / m
                        uv = ((1 - t) * c0(s) + t * c1(s) + (1 - s) * d0(t) + s * d1(t)
                              - ((1 - s) * (1 - t) * pa + s * (1 - t) * pb
                                 + (1 - s) * t * pd + s * t * pc))
                        grid[i][j] = vid(("F", pi, i, j), uv)
            # Orient the whole patch counter-clockwise in the layout plane
            # (decided from its corners, so folded interior cells cannot flip).
            corners = [pa, pb, pc, pd]
            area = sum(corners[k][0] * corners[(k + 1) % 4][1]
                       - corners[(k + 1) % 4][0] * corners[k][1] for k in range(4))
            for i in range(n):
                for j in range(m):
                    f = (grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1])
                    faces.append(f if area > 0 else tuple(reversed(f)))

        for pi, cs in enumerate(self.npatches):
            n = len(cs)
            sc = seg[key(cs[0], cs[1])]
            if sc % 2:
                raise ValueError(f"odd segment count {sc} on n-sided patch {cs}")
            k = sc // 2
            mids = [edge_vid(cs[i], cs[(i + 1) % n], k, sc) for i in range(n)]
            P = lambda v: verts[v]
            z_pos = np.mean([P(m) for m in mids], axis=0)
            z = vid(("Z", pi), z_pos)

            def spoke(i, j):
                """Vertex j (0..k) from midpoint i towards the centre."""
                if j == 0:
                    return mids[i]
                if j == k:
                    return z
                return vid(("S", pi, i, j), P(mids[i]) + (z_pos - P(mids[i])) * j / k)

            corner_pos = [self.points[c] for c in cs]
            for i in range(n):
                ci, cnext, cprev = cs[i], cs[(i + 1) % n], cs[i - 1]
                g = [[None] * (k + 1) for _ in range(k + 1)]
                for a in range(k + 1):
                    for b in range(k + 1):
                        if b == 0:
                            g[a][b] = edge_vid(ci, cnext, a, sc)
                        elif a == 0:
                            g[a][b] = edge_vid(cprev, ci, sc - b, sc)
                        elif b == k:
                            g[a][b] = spoke(i - 1 if i else n - 1, a)
                        elif a == k:
                            g[a][b] = spoke(i, b)
                for a in range(1, k):
                    for b in range(1, k):
                        s_, t_ = a / k, b / k
                        C0, C1 = P(g[a][0]), P(g[a][k])
                        D0, D1 = P(g[0][b]), P(g[k][b])
                        uv = ((1 - t_) * C0 + t_ * C1 + (1 - s_) * D0 + s_ * D1
                              - ((1 - s_) * (1 - t_) * P(g[0][0]) + s_ * (1 - t_) * P(g[k][0])
                                 + (1 - s_) * t_ * P(g[0][k]) + s_ * t_ * P(g[k][k])))
                        g[a][b] = vid(("NF", pi, i, a, b), uv)
                # Every sub-quad runs c_i -> m_i along the polygon boundary, so
                # the polygon's own orientation decides the winding.
                area = sum(corner_pos[q][0] * corner_pos[(q + 1) % n][1]
                           - corner_pos[(q + 1) % n][0] * corner_pos[q][1] for q in range(n))
                for a in range(k):
                    for b in range(k):
                        f = (g[a][b], g[a + 1][b], g[a + 1][b + 1], g[a][b + 1])
                        faces.append(f if area > 0 else tuple(reversed(f)))

        return np.array(verts), faces, point_ids
