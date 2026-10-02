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

    def point(self, name, uv):
        self.points[name] = np.asarray(uv, dtype=float)

    def curve(self, a, b, fn):
        """Curve from point ``a`` to point ``b`` parametrised on [0, 1]."""
        self.curves[(a, b)] = fn

    def patch(self, a, b, c, d):
        self.patches.append((a, b, c, d))

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

        return np.array(verts), faces, point_ids
