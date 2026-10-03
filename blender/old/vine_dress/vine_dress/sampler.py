"""Body surface sampler: BVH queries, smooth normals, weight/mask interpolation."""

import bisect
import math

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc


class SurfaceHit:
    __slots__ = ("loc", "normal", "tri", "bary")

    def __init__(self, loc, normal, tri, bary):
        self.loc = loc
        self.normal = normal
        self.tri = tri
        self.bary = bary


class BodySampler:
    """Snapshot of the body mesh in world space (taken in rest pose)."""

    def __init__(self, context, body, mask_group="", bone_names=None):
        depsgraph = context.evaluated_depsgraph_get()
        body_eval = body.evaluated_get(depsgraph)
        mesh = body_eval.to_mesh()
        from_eval = len(mesh.vertices) == len(body.data.vertices)
        if not from_eval:
            # Topology-changing modifiers: fall back to the original mesh so
            # vertex indices still match the vertex groups.
            body_eval.to_mesh_clear()
            mesh = body.data

        self.matrix_world = body.matrix_world.copy()
        self.matrix_world_inv = self.matrix_world.inverted()
        mw = self.matrix_world
        nmat = mw.to_3x3().inverted().transposed()

        self.co = [mw @ v.co for v in mesh.vertices]
        self.vnormals = [(nmat @ v.normal).normalized() for v in mesh.vertices]
        mesh.calc_loop_triangles()
        self.tris = [tuple(t.vertices) for t in mesh.loop_triangles]

        if from_eval:
            body_eval.to_mesh_clear()

        if not self.tris:
            raise ValueError("ボディメッシュに面がありません")

        self.bvh = BVHTree.FromPolygons(self.co, self.tris)

        zs = [c.z for c in self.co]
        self.zmin = min(zs)
        self.zmax = max(zs)
        self.height = max(self.zmax - self.zmin, 1e-6)

        # Per-vertex weights (only bone groups when bone names are known).
        names = {vg.index: vg.name for vg in body.vertex_groups}
        mask_index = body.vertex_groups[mask_group].index if mask_group in body.vertex_groups else -1
        self.has_mask = mask_index >= 0
        self.vweights = []
        self.vmask = []
        for v in body.data.vertices:
            ws = []
            m = 0.0
            for g in v.groups:
                if g.group == mask_index:
                    m = g.weight
                    continue
                name = names.get(g.group)
                if name is None or g.weight <= 0.0:
                    continue
                if bone_names is not None and name not in bone_names:
                    continue
                ws.append((name, g.weight))
            self.vweights.append(ws)
            self.vmask.append(m)

        # Area-weighted triangle sampling table.
        acc = 0.0
        self.cdf = []
        for a, b, c in self.tris:
            acc += ((self.co[b] - self.co[a]).cross(self.co[c] - self.co[a])).length * 0.5
            self.cdf.append(acc)
        self.total_area = acc

    # ------------------------------------------------------------------
    def _hit(self, loc, tri):
        a, b, c = self.tris[tri]
        pa, pb, pc = self.co[a], self.co[b], self.co[c]
        bary = poly_3d_calc((pa, pb, pc), loc)
        n = (self.vnormals[a] * bary[0] + self.vnormals[b] * bary[1] + self.vnormals[c] * bary[2])
        if n.length_squared < 1e-12:
            n = (pb - pa).cross(pc - pa)
        n.normalize()
        return SurfaceHit(loc, n, tri, bary)

    def nearest(self, p, max_dist=1e10):
        loc, _n, tri, _d = self.bvh.find_nearest(p, max_dist)
        if loc is None:
            return None
        return self._hit(loc, tri)

    def ray_cast(self, origin, direction, max_dist=1e10):
        loc, _n, tri, _d = self.bvh.ray_cast(origin, direction, max_dist)
        if loc is None:
            return None
        return self._hit(loc, tri)

    def sample(self, rng):
        r = rng.random() * self.total_area
        tri = min(bisect.bisect_left(self.cdf, r), len(self.tris) - 1)
        u, v = rng.random(), rng.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        a, b, c = self.tris[tri]
        loc = self.co[a] * (1.0 - u - v) + self.co[b] * u + self.co[c] * v
        return self._hit(loc, tri)

    def mask_at(self, hit):
        if not self.has_mask:
            return 1.0
        a, b, c = self.tris[hit.tri]
        w = hit.bary
        return self.vmask[a] * w[0] + self.vmask[b] * w[1] + self.vmask[c] * w[2]

    def weights_at(self, hit):
        out = {}
        for vi, bw in zip(self.tris[hit.tri], hit.bary):
            if bw <= 0.0:
                continue
            for name, w in self.vweights[vi]:
                out[name] = out.get(name, 0.0) + w * bw
        return out

    def to_local(self, p):
        return self.matrix_world_inv @ p


def blend_weights(a, b, t):
    """Linear blend of two sparse weight dicts: (1-t)*a + t*b."""
    out = {}
    for k, w in a.items():
        out[k] = w * (1.0 - t)
    for k, w in b.items():
        out[k] = out.get(k, 0.0) + w * t
    return out


def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def random_unit(rng):
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, math.tau)
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return Vector((r * math.cos(a), r * math.sin(a), z))


class SubsetView:
    """Same queries as BodySampler, restricted to the faces of one vertex group."""

    def __init__(self, sampler, body, group_name, threshold=0.1):
        self.base = sampler
        vg = body.vertex_groups.get(group_name)
        inside = [False] * len(body.data.vertices)
        if vg is not None:
            gi = vg.index
            for v in body.data.vertices:
                for g in v.groups:
                    if g.group == gi and g.weight >= threshold:
                        inside[v.index] = True
                        break
        self.tri_map = [i for i, t in enumerate(sampler.tris) if all(inside[k] for k in t)]
        self.valid = bool(self.tri_map)
        if self.valid:
            self.bvh = BVHTree.FromPolygons(sampler.co, [sampler.tris[i] for i in self.tri_map])

    def nearest(self, p, max_dist=1e10):
        loc, _n, tri, _d = self.bvh.find_nearest(p, max_dist)
        if loc is None:
            return None
        return self.base._hit(loc, self.tri_map[tri])

    def ray_cast(self, origin, direction, max_dist=1e10):
        loc, _n, tri, _d = self.bvh.ray_cast(origin, direction, max_dist)
        if loc is None:
            return None
        return self.base._hit(loc, self.tri_map[tri])

    def weights_at(self, hit):
        return self.base.weights_at(hit)
