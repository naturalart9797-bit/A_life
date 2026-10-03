"""Surface helpers: Poisson-scattered surface points around the origins, linked by
edges that hug the surface, and geodesic (along-the-surface) distances, used to
decide which part of the body lies within an origin's range."""

import bisect
import heapq
import math

from mathutils.kdtree import KDTree


class Node:
    __slots__ = ("co", "normal", "hit", "dist")


# ----------------------------------------------------------------------
# surface graph
# ----------------------------------------------------------------------
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


def connect(sampler, nodes, spacing, k=8):
    """Edges between neighbours that stay on the surface."""
    kd = KDTree(len(nodes))
    for i, n in enumerate(nodes):
        kd.insert(n.co, i)
    kd.balance()
    edges = set()
    rmax = spacing * 2.3
    for i, n in enumerate(nodes):
        cand = [c for c in kd.find_n(n.co, k + 1) if c[1] != i and c[2] <= rmax]
        for _co, j, d in cand:
            a, b = (i, j) if i < j else (j, i)
            if (a, b) in edges:
                continue
            m = nodes[j]
            if n.normal.dot(m.normal) < 0.2:
                continue  # opposite sides of a thin part
            mid = (n.co + m.co) * 0.5
            hit = sampler.nearest(mid)
            if hit is None or (hit.loc - mid).length > 0.25 * d + 0.1 * spacing:
                continue  # chord leaves the surface (gap between body parts)
            edges.add((a, b))
    return sorted(edges)


def geodesic(nodes, edges, sources):
    adj = [[] for _ in nodes]
    for a, b in edges:
        d = (nodes[a].co - nodes[b].co).length
        adj[a].append((b, d))
        adj[b].append((a, d))
    dist = [math.inf] * len(nodes)
    heap = []
    for s in sources:
        dist[s] = 0.0
        heap.append((0.0, s))
    heapq.heapify(heap)
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        for j, w in adj[i]:
            nd = d + w
            if nd < dist[j]:
                dist[j] = nd
                heapq.heappush(heap, (nd, j))
    for n, d in zip(nodes, dist):
        n.dist = d
    return dist


def nearest_node(nodes, p):
    best, bi = math.inf, -1
    for i, n in enumerate(nodes):
        d = (n.co - p).length_squared
        if d < best:
            best, bi = d, i
    return bi


def restrict(nodes, edges, keep):
    """Keep a subset of nodes (bool list) and re-index the edges."""
    remap = {}
    out = []
    for i, n in enumerate(nodes):
        if keep[i]:
            remap[i] = len(out)
            out.append(n)
    e2 = [(remap[a], remap[b]) for a, b in edges if a in remap and b in remap]
    return out, e2, remap
