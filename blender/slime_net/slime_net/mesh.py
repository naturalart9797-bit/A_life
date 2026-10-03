"""Network branches -> tube mesh with per-vertex attributes.

slime_dist  : 0 at an origin -> 1 at the edge of its range (drives the growth animation)
slime_thick : 0 for the thinnest capillaries -> 1 for the thickest veins
"""

import math

import bpy
from mathutils import Vector


class MeshBuilder:
    def __init__(self):
        self.verts, self.weights, self.dist, self.thick = [], [], [], []
        self.faces = []

    def vert(self, co, w, dist, thick):
        self.verts.append(co)
        self.weights.append(w)
        self.dist.append(dist)
        self.thick.append(thick)
        return len(self.verts) - 1


def _perp(t):
    a = Vector((1.0, 0.0, 0.0)) if abs(t.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return t.cross(a).normalized()


def tube(b, pts, normals, radii, weights, dists, thicks, res):
    n = len(pts)
    if n < 2:
        return []
    rings = []
    for i in range(n):
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        t = d.normalized() if d.length_squared > 1e-16 else Vector((0.0, 0.0, 1.0))
        ref = normals[i] - t * normals[i].dot(t)
        ref = ref.normalized() if ref.length_squared > 1e-10 else _perp(t)
        bi = t.cross(ref)
        ring = []
        for j in range(res):
            a = math.tau * j / res
            co = pts[i] + (ref * math.cos(a) + bi * math.sin(a)) * radii[i]
            ring.append(b.vert(co, weights[i], dists[i], thicks[i]))
        rings.append(ring)
    for i in range(n - 1):
        r0, r1 = rings[i], rings[i + 1]
        for j in range(res):
            j2 = (j + 1) % res
            b.faces.append((r0[j], r0[j2], r1[j2], r1[j]))
    return rings


def cap(b, center, normal, tangent, radius, ring, weight, dist, thick):
    """Rounded end for free branch tips."""
    tip = b.vert(center + tangent * radius, weight, dist, thick)
    res = len(ring)
    for j in range(res):
        b.faces.append((ring[j], ring[(j + 1) % res], tip))


def blob(b, center, radius, weight, dist, thick, seg=8, rings=5):
    """Small sphere that hides the seams where branches meet."""
    top = b.vert(center + Vector((0.0, 0.0, radius)), weight, dist, thick)
    bottom = b.vert(center - Vector((0.0, 0.0, radius)), weight, dist, thick)
    grid = []
    for i in range(1, rings):
        th = math.pi * i / rings
        row = []
        for j in range(seg):
            ph = math.tau * j / seg
            d = Vector((math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)))
            row.append(b.vert(center + d * radius, weight, dist, thick))
        grid.append(row)
    for j in range(seg):
        j2 = (j + 1) % seg
        b.faces.append((top, grid[0][j], grid[0][j2]))
        b.faces.append((bottom, grid[-1][j2], grid[-1][j]))
    for i in range(len(grid) - 1):
        for j in range(seg):
            j2 = (j + 1) % seg
            b.faces.append((grid[i][j], grid[i + 1][j], grid[i + 1][j2], grid[i][j2]))


def build_mesh(b, name, to_local):
    me = bpy.data.meshes.new(name)
    me.from_pydata([to_local(v) for v in b.verts], [], b.faces)
    me.update()
    try:
        me.polygons.foreach_set("use_smooth", [True] * len(b.faces))
    except (AttributeError, TypeError, RuntimeError):
        me.shade_smooth()
    for nm, vals in (("slime_dist", b.dist), ("slime_thick", b.thick)):
        a = me.attributes.new(name=nm, type="FLOAT", domain="POINT")
        a.data.foreach_set("value", vals)
    me.update()
    return me


def assign_vertex_groups(obj, b, bone_names):
    buckets = {}
    for vi, ws in enumerate(b.weights):
        total = sum(ws.values())
        if total <= 1e-8:
            continue
        for name, w in ws.items():
            if bone_names is not None and name not in bone_names:
                continue
            q = round(w / total, 3)
            if q > 0.0:
                buckets.setdefault(name, {}).setdefault(q, []).append(vi)
    for name, by_w in buckets.items():
        vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        for w, idx in by_w.items():
            vg.add(idx, w, "REPLACE")
