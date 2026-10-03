"""Guide curves -> vine centre lines, driven by the guide's style."""

import copy
import math

from mathutils import Quaternion, Vector, noise

from .growth import VinePath


class Attrs:
    """Look attributes carried by a batch of guides through the node graph.

    Lengths are in metres for a 1.7 m object (multiplied by the target scale)."""

    def __init__(self):
        self.radius = 0.005
        self.radius_mult = 1.0
        self.taper = 0.0
        self.snap = True
        self.surface_offset = 0.002
        self.strands = 1
        self.strand_spread = 2.0
        self.strand_twist = 6.0
        self.strand_radius = 0.7
        self.tendril_density = 0.0
        self.tendril_size = 0.06
        self.use_leaves = False
        self.leaf_density = 40.0
        self.leaf_size = 0.045
        self.leaf_size_var = 0.35
        self.leaf_width = 0.55
        self.leaf_tilt = 0.25
        self.leaf_curl = 0.2
        self.noise_amp = 0.0
        self.noise_freq = 8.0
        self.noise_seed = 0
        self.stem_color = (0.16, 0.11, 0.06)
        self.leaf_color = (0.12, 0.35, 0.08)
        self.color_var = 0.5
        self.leaf_shape = "OVAL"
        self.petiole = 0.0
        self.leaf_tip_scale = 1.0
        self.leaf_light = 0.0
        self.leaf_wave = 0.0
        self.stem_irregular = 0.0
        self.rootlet_density = 0.0
        self.rootlet_size = 0.012
        self.style_uid = 0
        self.mat_stem = 0
        self.mat_leaf = 1

    def copy(self):
        return copy.copy(self)

    @classmethod
    def from_guide(cls, style, gs, P):
        a = cls()
        if style is not None:
            for k in ("radius", "taper", "strands", "strand_spread", "strand_twist", "strand_radius",
                      "noise_amp", "noise_freq", "tendril_density", "tendril_size", "use_leaves",
                      "leaf_density", "leaf_size", "leaf_size_var", "leaf_width", "leaf_tilt", "leaf_curl",
                      "color_var", "leaf_shape", "petiole", "leaf_tip_scale", "leaf_light", "leaf_wave",
                      "stem_irregular", "rootlet_density", "rootlet_size"):
                setattr(a, k, getattr(style, k))
            a.stem_color = tuple(style.stem_color)
            a.leaf_color = tuple(style.leaf_color)
            a.style_uid = style.uid
        a.radius_mult = gs.radius
        a.leaf_density *= gs.leaves
        a.snap = gs.snap
        a.surface_offset = P.surface_offset
        return a


# ----------------------------------------------------------------------
def _tangent(pts, i, closed):
    n = len(pts)
    if closed:
        d = pts[(i + 1) % n] - pts[i - 1]
    else:
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
    if d.length_squared < 1e-16:
        return Vector((0.0, 0.0, 1.0))
    return d.normalized()


def _frame(t, n):
    ref = n - t * n.dot(t)
    if ref.length_squared < 1e-10:
        a = Vector((1.0, 0.0, 0.0)) if abs(t.x) < 0.9 else Vector((0.0, 1.0, 0.0))
        ref = t.cross(a)
    ref.normalize()
    return ref, t.cross(ref)


def _tip_taper(radii, closed, extra):
    n = len(radii)
    out = list(radii)
    for i in range(n):
        if extra > 0.0 and not closed:
            out[i] *= max(0.05, 1.0 - extra * i / max(1, n - 1))
        tip = n - 1 - i
        if not closed and tip < 4:
            out[i] *= 0.35 + 0.65 * tip / 4.0
    return out


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
def paths_from_guide(points, radii, closed, A, sampler, rng, scale):
    """One guide spline (world space, resampled) -> vine paths."""
    base_r = A.radius * scale * A.radius_mult
    off = A.surface_offset * scale
    radii = _tip_taper([max(r, 0.0) * base_r for r in radii], closed, A.taper)
    if max(radii, default=0.0) <= 0.0:
        return []
    kind = "surface" if A.snap else "free"
    centers, normals, weights = [], [], []
    for p, r in zip(points, radii):
        hit = sampler.nearest(p)
        if A.snap:
            centers.append(hit.loc + hit.normal * (off + r))
            normals.append(hit.normal)
        else:
            centers.append(p.copy())
            d = p - hit.loc
            normals.append(d.normalized() if d.length_squared > 1e-12 else hit.normal)
        weights.append(sampler.weights_at(hit))
    ts = [None] * len(centers)
    if A.noise_amp > 0.0:
        _apply_noise(centers, normals, kind, A, scale)
    g = _G(kind, closed)
    new = _strands(g, centers, normals, radii, weights, ts, A, scale, rng)
    if A.tendril_density > 0.0:
        new += _tendrils(g, centers, normals, radii, weights, ts, A, scale, rng)
    for p in new:
        p.attrs = A
    return new


class _G:
    __slots__ = ("kind", "closed")

    def __init__(self, kind, closed):
        self.kind = kind
        self.closed = closed


def _apply_noise(centers, normals, kind, A, scale):
    amp = A.noise_amp * scale
    freq = A.noise_freq / scale
    seed = Vector((A.noise_seed * 13.17, A.noise_seed * 7.31, A.noise_seed * 3.77))
    for i, (c, n) in enumerate(zip(centers, normals)):
        o = noise.noise_vector(c * freq + seed) * amp
        if kind == "surface":
            o -= n * min(0.0, o.dot(n))  # never push into the skin
        centers[i] = c + o


def _strands(g, centers, normals, radii, weights, ts, A, scale, rng):
    kind = g.kind
    count = max(1, A.strands)
    if count == 1:
        path = VinePath(kind)
        path.points, path.normals, path.radii = centers, normals, radii
        path.weights, path.tparams, path.closed = weights, ts, g.closed
        return [path]

    out = []
    n = len(centers)
    lens = [0.0]
    for i in range(1, n):
        lens.append(lens[-1] + (centers[i] - centers[i - 1]).length)
    total = lens[-1] or 1.0
    turns = A.strand_twist * total / scale
    if g.closed:
        turns = round(turns)  # keep the braid continuous on rings
    for k in range(count):
        phase = math.tau * k / count + rng.uniform(-0.2, 0.2)
        path = VinePath(kind)
        for i in range(n):
            t = _tangent(centers, i, g.closed)
            ref, bi = _frame(t, normals[i])
            a = phase + math.tau * turns * lens[i] / total
            spread = radii[i] * A.strand_spread
            if kind == "surface":
                # Braid lying on the surface: sideways + slightly up, never into the skin.
                o = bi * (math.sin(a) * spread) + ref * ((1.0 + math.cos(a)) * 0.5 * spread)
            else:
                o = (ref * math.cos(a) + bi * math.sin(a)) * spread
            path.points.append(centers[i] + o)
            path.normals.append(normals[i])
            path.radii.append(radii[i] * A.strand_radius)
        path.weights, path.tparams, path.closed = weights, ts, g.closed
        path.leaf_scale = 1.0 / count
        out.append(path)
    return out


def _tendrils(g, centers, normals, radii, weights, ts, A, scale, rng):
    kind = g.kind
    interval = 1.0 / (A.tendril_density / scale)
    out = []
    acc = 0.0
    next_at = interval * rng.uniform(0.3, 1.2)
    for i in range(1, len(centers) - 1):
        acc += (centers[i] - centers[i - 1]).length
        if acc < next_at:
            continue
        next_at = acc + interval * rng.uniform(0.5, 1.5)
        t = _tangent(centers, i, g.closed)
        ref, bi = _frame(t, normals[i])
        side = 1.0 if rng.random() < 0.5 else -1.0
        out_dir = (bi * side + ref * 0.8 + t * rng.uniform(-0.3, 0.6)).normalized()
        if kind == "surface":
            axis = ref.copy()  # curl in the tangent plane, so it never dives into the skin
        else:
            axis = out_dir.cross(ref)
            if axis.length_squared < 1e-9:
                axis = t.copy()
        axis.normalize()
        length = A.tendril_size * scale * rng.uniform(0.6, 1.4)
        coils = rng.uniform(1.5, 3.0)
        m = 28
        path = VinePath(kind)
        r0 = radii[i] * 0.35
        d = out_dir.copy()
        pos = centers[i].copy()
        seg = length / m
        for j in range(m + 1):
            u = j / m
            path.points.append(pos.copy())
            path.normals.append(normals[i])
            path.radii.append(r0 * (1.0 - 0.8 * u))
            ang = coils * math.tau / m * (0.3 + 1.7 * u)
            d = Quaternion(axis, ang * side) @ d
            pos = pos + d * seg
        path.weights = [weights[i]] * len(path.points)
        path.tparams = [ts[i]] * len(path.points)
        path.leaf_scale = 0.0
        out.append(path)
    return out
