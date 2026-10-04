"""Density points (Yeti-style): many unnamed points on the surface, each with a
painted density value, kept in one hidden point mesh parented to the target
and drawn in the viewport as small discs coloured by their value."""

import math

import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

POINTS_PROP = "vine_grow_points"
ATTR_VALUE = "vg_density"
ATTR_NORMAL = "vg_normal"

VALUE_MAX = 100.0  # painted values are 0..100 (100 = the overall density)

# 0 purple -> 25 blue -> 50 green -> 75 yellow -> 100 orange
RAMP = ((0.0, (0.55, 0.12, 0.85)), (0.25, (0.15, 0.35, 1.00)), (0.5, (0.20, 0.85, 0.25)),
        (0.75, (1.00, 0.90, 0.10)), (1.0, (1.00, 0.45, 0.05)))
ZERO_COLOR = (0.55, 0.55, 0.58)  # unpainted: close to a grey body so it stays quiet


def ramp(t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(RAMP[:-1], RAMP[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * u for a, b in zip(c0, c1))
    return RAMP[-1][1]


# ----------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------
def points_object(target, context=None, create=False):
    for o in bpy.data.objects:
        if o.get(POINTS_PROP) == target.name and o.type == "MESH":
            return o
    if not create:
        return None
    me = bpy.data.meshes.new(target.name + "_VinePoints")
    o = bpy.data.objects.new(target.name + "_VinePoints", me)
    o[POINTS_PROP] = target.name
    colls = target.users_collection or (context.scene.collection,)
    colls[0].objects.link(o)
    o.parent = target
    o.matrix_parent_inverse.identity()
    o.matrix_basis.identity()
    o.hide_select = True
    o.hide_render = True
    return o


class PointSet:
    """Points in the target's local space: positions, normals, density values."""

    def __init__(self, co=None, nrm=None, val=None):
        self.co = co or []
        self.nrm = nrm or []
        self.val = val or []
        self.version = 0
        self.dirty = False  # changed but not written to the mesh yet
        self._kd = None
        self.mesh_version = -1

    def __len__(self):
        return len(self.co)

    def changed(self, structure=False):
        self.version += 1
        self.dirty = True
        if structure:
            self._kd = None

    def kd(self):
        if self._kd is None:
            kd = KDTree(max(1, len(self.co)))
            for i, c in enumerate(self.co):
                kd.insert(c, i)
            kd.balance()
            self._kd = kd
        return self._kd

    def spacing(self):
        """Typical distance between neighbouring points (local units)."""
        n = len(self.co)
        if n < 2:
            return 0.0
        kd = self.kd()
        step = max(1, n // 400)
        ds = sorted(kd.find_n(self.co[i], 2)[1][2] for i in range(0, n, step))
        return ds[len(ds) // 2]

    @classmethod
    def from_object(cls, obj):
        me = obj.data
        n = len(me.vertices)
        flat = [0.0] * (n * 3)
        me.vertices.foreach_get("co", flat)
        co = [Vector(flat[i * 3:i * 3 + 3]) for i in range(n)]
        val = [0.0] * n
        a = me.attributes.get(ATTR_VALUE)
        if a is not None and n:
            a.data.foreach_get("value", val)
        nrm = [Vector((0.0, 0.0, 1.0)) for _ in range(n)]
        a = me.attributes.get(ATTR_NORMAL)
        if a is not None and n:
            f = [0.0] * (n * 3)
            a.data.foreach_get("vector", f)
            nrm = [Vector(f[i * 3:i * 3 + 3]) for i in range(n)]
        ps = cls(co, nrm, val)
        ps.mesh_version = me.get("vg_version", 0)
        return ps

    def to_object(self, obj):
        me = obj.data
        me.clear_geometry()
        n = len(self.co)
        if n:
            me.vertices.add(n)
            me.vertices.foreach_set("co", [c for v in self.co for c in v])
        for name, kind in ((ATTR_VALUE, "FLOAT"), (ATTR_NORMAL, "FLOAT_VECTOR")):
            a = me.attributes.get(name)
            if a is not None:
                me.attributes.remove(a)
            me.attributes.new(name=name, type=kind, domain="POINT")
        if n:
            me.attributes[ATTR_VALUE].data.foreach_set("value", self.val)
            me.attributes[ATTR_NORMAL].data.foreach_set("vector", [c for v in self.nrm for c in v])
        me["vg_version"] = me.get("vg_version", 0) + 1
        me.update()
        self.mesh_version = me["vg_version"]
        self.dirty = False


_cache = {}


def get(target):
    """Live point set of the target (reloaded when the mesh changed, e.g. after undo)."""
    obj = points_object(target)
    if obj is None:
        _cache.pop(target.name, None)
        return None
    ps = _cache.get(target.name)
    if ps is None or (not ps.dirty and ps.mesh_version != obj.data.get("vg_version", 0)):
        ps = PointSet.from_object(obj)
        _cache[target.name] = ps
    return ps


def commit(context, target, ps):
    obj = points_object(target, context, create=True)
    obj.hide_viewport = True  # the add-on draws the points itself (coloured discs)
    ps.to_object(obj)
    _cache[target.name] = ps


def clear(target):
    obj = points_object(target)
    _cache.pop(target.name, None)
    if obj is not None:
        me = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)


# ----------------------------------------------------------------------
# scattering
# ----------------------------------------------------------------------
def scatter(sampler, target, spacing_world, rng, old=None, default=0.0):
    """Poisson points over the whole surface. Values carry over from `old` (nearest old point)."""
    from . import surface
    nodes = surface.scatter_nodes(sampler, [sampler.co[0]], 1e9, spacing_world, rng)
    inv = target.matrix_world.inverted()
    rot = inv.to_3x3()
    co = [inv @ n.co for n in nodes]
    nrm = [(rot @ n.normal).normalized() for n in nodes]
    if old is not None and len(old):
        kd = old.kd()
        lim = max(old.spacing() * 2.5, 1e-9)
        val = []
        for c in co:
            hits = kd.find_n(c, 3)
            sw = sv = 0.0
            for _co, i, d in hits:
                if d > lim:
                    continue
                w = 1.0 / (d + lim * 0.1)
                sw += w
                sv += w * old.val[i]
            val.append(sv / sw if sw else default)
    else:
        val = [default] * len(co)
    return PointSet(co, nrm, val)


# ----------------------------------------------------------------------
# viewport drawing
# ----------------------------------------------------------------------
_batch = {}
_handle = None


def _shader():
    import gpu
    for name in ("SMOOTH_COLOR", "3D_SMOOTH_COLOR"):
        try:
            return gpu.shader.from_builtin(name)
        except (ValueError, KeyError):
            continue
    return None


def _build(ps, size, vmax):
    from gpu_extras.batch import batch_for_shader
    seg = 6
    pos, col = [], []
    ring = [(math.cos(math.tau * k / seg), math.sin(math.tau * k / seg)) for k in range(seg)]
    for c, n, v in zip(ps.co, ps.nrm, ps.val):
        t1 = n.orthogonal().normalized()
        t2 = n.cross(t1)
        ctr = c + n * (size * 0.15)  # just above the skin (avoids z-fighting)
        rgb = ramp(v / vmax) if v > 1e-6 else ZERO_COLOR
        r = size  # every point the same size; only the colour shows the value
        rgba = (rgb[0], rgb[1], rgb[2], 1.0)
        pts = [ctr + (t1 * x + t2 * y) * r for x, y in ring]
        for k in range(seg):
            pos += [ctr, pts[k], pts[(k + 1) % seg]]
            col += [rgba, rgba, rgba]
    shader = _shader()
    if shader is None or not pos:
        return None, None
    return shader, batch_for_shader(shader, "TRIS", {"pos": pos, "color": col})


def draw():
    try:
        context = bpy.context
        P = context.scene.vine_grow
        target = P.target
        if target is None or not P.show_points:
            return
        ps = get(target)
        if ps is None or not len(ps):
            return
        mw = target.matrix_world
        sc = max(mw.to_scale()) or 1.0
        from .pipeline import target_scale
        size = P.point_size * target_scale(target, P) / sc
        key = (id(ps), ps.version, ps.mesh_version, round(size, 9))
        cached = _batch.get(target.name)
        if cached is None or cached[0] != key:
            shader, batch = _build(ps, size, VALUE_MAX)
            cached = (key, shader, batch)
            _batch[target.name] = cached
        _key, shader, batch = cached
        if batch is None:
            return
        import gpu
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(mw)
        batch.draw(shader)
        gpu.matrix.pop()
        gpu.state.depth_test_set("NONE")
    except ReferenceError:
        pass


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _cache.clear()
    _batch.clear()
