"""Slime-mould (Physarum) transport network on a mesh surface.

1. Poisson-scatter nodes on the surface near the origins and connect close
   neighbours with edges that hug the surface (no jumps across gaps).
2. Keep the nodes within a geodesic range of the origins (Dijkstra).
3. Tero et al. Physarum solver: flow is pushed from a random food source to
   the other food sources through the edge network; tubes that carry flow
   thicken, the rest wither  ->  thick veins, thin capillaries and loops.
4. Surviving edges are chained into smooth branches lying on the surface.
"""

import bisect
import heapq
import math

import numpy as np
from mathutils.kdtree import KDTree


class Node:
    __slots__ = ("co", "normal", "hit", "dist")


# ----------------------------------------------------------------------
# 1. surface graph
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


# ----------------------------------------------------------------------
# 2. Physarum solver
# ----------------------------------------------------------------------
def _cg(cond, ea, eb, n, rhs, x0, iters=400, tol=1e-7):
    diag = np.bincount(ea, cond, n) + np.bincount(eb, cond, n)
    eps = 1e-9 * (diag.mean() if n else 1.0) + 1e-12
    diag = diag + eps

    def mul(x):
        return diag * x - np.bincount(ea, cond * x[eb], n) - np.bincount(eb, cond * x[ea], n)

    minv = 1.0 / diag
    x = x0.copy()
    r = rhs - mul(x)
    z = minv * r
    p = z.copy()
    rz = r @ z
    bnorm = np.linalg.norm(rhs) + 1e-30
    for _ in range(iters):
        ap = mul(p)
        denom = p @ ap
        if denom <= 0.0:
            break
        alpha = rz / denom
        x += alpha * p
        r -= alpha * ap
        if np.linalg.norm(r) < tol * bnorm:
            break
        z = minv * r
        rz_new = r @ z
        p = z + (rz_new / rz) * p
        rz = rz_new
    return x


def physarum(n, edges, lengths, sources, foods, rng, iterations=80, mu=1.0, dt=0.5, origin_bias=0.5):
    """Return per-edge conductivity after `iterations` adaptation steps."""
    if not edges:
        return np.zeros(0)
    ea = np.fromiter((a for a, _ in edges), dtype=np.int64, count=len(edges))
    eb = np.fromiter((b for _, b in edges), dtype=np.int64, count=len(edges))
    L = np.maximum(np.asarray(lengths, dtype=np.float64), 1e-9)
    D = 1.0 + 0.2 * np.array([rng.random() for _ in edges])
    terminals = list(dict.fromkeys(list(sources) + list(foods)))
    if len(terminals) < 2:
        return D
    p = np.zeros(n)
    acc = np.zeros(len(edges))
    tail = max(1, iterations // 4)
    for it in range(iterations):
        if rng.random() < origin_bias:
            inlet = sources[rng.randrange(len(sources))]
        else:
            inlet = terminals[rng.randrange(len(terminals))]
        outs = [t for t in terminals if t != inlet]
        rhs = np.zeros(n)
        rhs[outs] = -1.0 / len(outs)
        rhs[inlet] = 1.0
        cond = np.maximum(D, 1e-7) / L
        p = _cg(cond, ea, eb, n, rhs, p)
        Q = np.abs(cond * (p[ea] - p[eb]))
        D = D + dt * (Q ** mu - D)
        D = np.maximum(D, 1e-12)
        if it >= iterations - tail:
            acc += D
    return acc / tail


# ----------------------------------------------------------------------
# 3. extraction
# ----------------------------------------------------------------------
def extract_branches(nodes, edges, D, sources, keep_decades, r_min, r_max):
    """Edges above the threshold -> list of branches [(node ids, edge radii)] and junction radii."""
    if len(D) == 0:
        return [], {}
    dmax = float(D.max())
    thr = dmax * 10.0 ** (-keep_decades)
    kept = [i for i, d in enumerate(D) if d >= thr]
    radius = {}
    adj = {}
    for ei in kept:
        a, b = edges[ei]
        t = (float(D[ei]) / dmax) ** 0.25
        r = r_min + (r_max - r_min) * t
        radius[ei] = r
        adj.setdefault(a, []).append((b, ei))
        adj.setdefault(b, []).append((a, ei))
    # Keep only what is connected to an origin.
    seen = set()
    stack = [s for s in sources if s in adj]
    seen.update(stack)
    while stack:
        i = stack.pop()
        for j, _e in adj.get(i, ()):
            if j not in seen:
                seen.add(j)
                stack.append(j)
    adj = {i: [(j, e) for j, e in nbrs if j in seen] for i, nbrs in adj.items() if i in seen}
    node_r = {i: max(radius[e] for _j, e in nbrs) for i, nbrs in adj.items() if nbrs}

    used = set()
    branches = []

    def walk(start, first_edge, first_next):
        ids = [start, first_next]
        rad = [radius[first_edge]]
        used.add(first_edge)
        prev, cur = start, first_next
        while len(adj.get(cur, ())) == 2 and cur not in sources:
            nxt = [(j, e) for j, e in adj[cur] if e not in used]
            if not nxt:
                break
            j, e = nxt[0]
            used.add(e)
            ids.append(j)
            rad.append(radius[e])
            prev, cur = cur, j
            if cur == start:
                break
        return ids, rad

    for i, nbrs in adj.items():
        if len(nbrs) != 2 or i in sources:
            for j, e in nbrs:
                if e not in used:
                    branches.append(walk(i, e, j))
    for i, nbrs in adj.items():  # pure loops without junctions
        for j, e in nbrs:
            if e not in used:
                branches.append(walk(i, e, j))
    return branches, node_r
