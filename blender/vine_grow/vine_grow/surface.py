"""Poisson-scattered points on the target surface."""

import bisect
import math


class Node:
    __slots__ = ("co", "normal", "hit", "dist")


def scatter_nodes(sampler, origins, reach, spacing, rng):
    """Poisson-disk-ish points on the triangles within `reach` of an origin."""
    co, tris = sampler.co, sampler.tris
    sel, cdf, acc = [], [], 0.0
    for ti, (a, b, c) in enumerate(tris):
        pa, pb, pc = co[a], co[b], co[c]
        cen = (pa + pb + pc) / 3.0
        rad = max((pa - cen).length, (pb - cen).length, (pc - cen).length)
        lim = reach + spacing * 2.0 + rad
        if not any((cen - o).length_squared <= lim * lim for o in origins):
            continue
        area = (pb - pa).cross(pc - pa).length * 0.5
        if area <= 0.0:
            continue
        acc += area
        sel.append(ti)
        cdf.append(acc)
    if not sel:
        return []
    want = int(acc / (spacing * spacing) * 1.1) + 16
    cell = spacing
    grid = {}
    nodes = []
    s2 = spacing * spacing * 0.81
    tries = want * 12
    for _ in range(tries):
        if len(nodes) >= want:
            break
        k = sel[min(bisect.bisect_left(cdf, rng.random() * acc), len(sel) - 1)]
        u, v = rng.random(), rng.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        a, b, c = tris[k]
        p = co[a] * (1.0 - u - v) + co[b] * u + co[c] * v
        key = (math.floor(p.x / cell), math.floor(p.y / cell), math.floor(p.z / cell))
        ok = True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in grid.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if (q - p).length_squared < s2:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    break
            if not ok:
                break
        if not ok:
            continue
        grid.setdefault(key, []).append(p)
        hit = sampler._hit(p, k)
        n = Node()
        n.co, n.normal, n.hit, n.dist = p, hit.normal, hit, math.inf
        nodes.append(n)
    return nodes
