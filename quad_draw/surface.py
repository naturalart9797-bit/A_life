# SPDX-License-Identifier: GPL-3.0-or-later
"""The "live surface": reference meshes that new geometry snaps onto.

All queries are expressed in the local space of the retopo object so the rest
of the add-on never has to juggle matrices.
"""

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def _matrix_np(m):
    return np.array([list(row) for row in m], dtype=np.float64)


class ReferenceSurface:

    def __init__(self, bvh, size):
        self.bvh = bvh
        self.size = max(size, 1e-6)

    @classmethod
    def from_objects(cls, depsgraph, objects, to_local):
        """Build from evaluated ``objects``; ``to_local`` is the inverse world
        matrix of the retopo object."""
        all_co = []
        all_tris = []
        offset = 0
        for ob in objects:
            if ob.type != 'MESH':
                continue
            ob_eval = ob.evaluated_get(depsgraph)
            me = ob_eval.to_mesh()
            try:
                me.calc_loop_triangles()
                nv = len(me.vertices)
                nt = len(me.loop_triangles)
                if nv == 0 or nt == 0:
                    continue
                co = np.empty(nv * 3, dtype=np.float32)
                me.vertices.foreach_get("co", co)
                co = co.reshape(nv, 3).astype(np.float64)
                m = _matrix_np(to_local @ ob.matrix_world)
                co = co @ m[:3, :3].T + m[:3, 3]
                tris = np.empty(nt * 3, dtype=np.int32)
                me.loop_triangles.foreach_get("vertices", tris)
                tris = tris.reshape(nt, 3) + offset
                all_co.append(co)
                all_tris.append(tris)
                offset += nv
            finally:
                ob_eval.to_mesh_clear()
        if not all_co:
            return None
        co = np.concatenate(all_co)
        tris = np.concatenate(all_tris)
        size = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0)))
        bvh = BVHTree.FromPolygons(co.tolist(), tris.tolist(), all_triangles=True)
        return cls(bvh, size)

    # -- queries -----------------------------------------------------------

    def ray_cast(self, origin, direction, distance=1.0e10):
        """Returns (location, normal, distance) or (None, None, None)."""
        loc, nor, _idx, dist = self.bvh.ray_cast(origin, direction, distance)
        if loc is None:
            return None, None, None
        return loc, nor, dist

    def nearest(self, co):
        loc, _nor, _idx, _dist = self.bvh.find_nearest(co)
        return loc

    def nearest_normal(self, co):
        _loc, nor, _idx, _dist = self.bvh.find_nearest(co)
        return nor

    def is_visible(self, co, origin, eps=None):
        """True when nothing on the reference occludes ``co`` from ``origin``."""
        d = co - origin
        dist = d.length
        if dist < 1e-9:
            return True
        if eps is None:
            eps = self.size * 0.01
        loc, _nor, _idx, hit = self.bvh.ray_cast(origin, d / dist, dist)
        if loc is None:
            return True
        return hit >= dist - eps


def project_fn(surface):
    """A callable mapping a coordinate onto ``surface`` (identity without one)."""
    if surface is None:
        return lambda co: co
    return surface.nearest


def normal_fn(surface):
    if surface is None:
        return lambda co: None
    return surface.nearest_normal


def empty_surface_size(bm):
    if not bm.verts:
        return 1.0
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    for v in bm.verts:
        for i in range(3):
            mn[i] = min(mn[i], v.co[i])
            mx[i] = max(mx[i], v.co[i])
    return max((mx - mn).length, 1.0)
