"""Build VinePaths (centre lines with weights) from guide curves."""

import math

from mathutils import Quaternion, Vector

from . import guides
from .growth import VinePath


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


def _tip_taper(radii, closed):
    if closed:
        return radii
    n = len(radii)
    out = list(radii)
    for i in range(n):
        tip = n - 1 - i
        if tip < 4:
            out[i] *= 0.35 + 0.65 * tip / 4.0
    return out


class GuideSpline:
    """One spline of a guide object after resampling."""

    def __init__(self, kind, pts, rad, closed, gs):
        self.kind = kind
        self.points = pts
        self.radii = rad
        self.closed = closed
        self.settings = gs  # GuideSettings of the owning object


def collect(body, P, scale):
    step = P.step_length * scale
    out = []
    for obj in guides.guide_objects(body, only_enabled=True):
        gs = obj.vine_guide
        for pts, rad, closed in guides.read_splines(obj):
            rp, rr = guides.resample(pts, rad, step, closed)
            if len(rp) >= 2:
                out.append(GuideSpline(gs.kind, rp, rr, closed, gs))
    return out


def paths_from_guides(splines, sampler, field, P, rng, scale):
    """Turn guide splines into vine paths (one or more strands per guide + tendrils)."""
    base_r = P.radius * scale
    off = P.surface_offset * scale
    paths = []
    for g in splines:
        gs = g.settings
        if g.kind == guides.KIND_SKIRT:
            r_mult = base_r * P.skirt_radius * gs.radius
        else:
            r_mult = base_r * gs.radius
        radii = _tip_taper([max(r, 0.0) * r_mult for r in g.radii], g.closed)
        if max(radii, default=0.0) <= 0.0:
            continue

        # Centre line, normals, weights.
        centers, normals, weights, ts = [], [], [], []
        if g.kind == guides.KIND_BODY:
            for p, r in zip(g.points, radii):
                hit = sampler.nearest(p)
                if P.snap_on_build:
                    centers.append(hit.loc + hit.normal * (off + r))
                else:
                    centers.append(p.copy())
                normals.append(hit.normal)
                weights.append(sampler.weights_at(hit))
                ts.append(None)
        else:
            for p in g.points:
                centers.append(p.copy())
                if field is not None:
                    normals.append(field.normal(p))
                    weights.append(field.weights(p))
                    ts.append(field.t_of(p))
                else:
                    hit = sampler.nearest(p)
                    d = p - hit.loc
                    normals.append(d.normalized() if d.length_squared > 1e-12 else hit.normal)
                    weights.append(sampler.weights_at(hit))
                    ts.append(None)

        strands = gs.strands or P.strands
        paths += _strands(g, centers, normals, radii, weights, ts, strands, P, scale, rng)
        if P.tendril_density > 0.0:
            paths += _tendrils(g, centers, normals, radii, weights, ts, P, scale, rng)
    return paths


def _strands(g, centers, normals, radii, weights, ts, count, P, scale, rng):
    kind = "body" if g.kind == guides.KIND_BODY else "skirt"
    if count <= 1:
        path = VinePath(kind)
        path.points, path.normals, path.radii = centers, normals, radii
        path.weights, path.tparams, path.closed = weights, ts, g.closed
        path.leaf_scale = g.settings.leaves
        return [path]

    out = []
    n = len(centers)
    lens = [0.0]
    for i in range(1, n):
        lens.append(lens[-1] + (centers[i] - centers[i - 1]).length)
    total = lens[-1] or 1.0
    turns = P.strand_twist * total / scale
    if g.closed:
        turns = round(turns)  # keep the braid continuous on rings
    for k in range(count):
        phase = math.tau * k / count + rng.uniform(-0.2, 0.2)
        path = VinePath(kind)
        for i in range(n):
            t = _tangent(centers, i, g.closed)
            ref, bi = _frame(t, normals[i])
            a = phase + math.tau * turns * lens[i] / total
            spread = radii[i] * P.strand_spread
            if kind == "body":
                # Braid lying on the surface: sideways + slightly up, never into the skin.
                o = bi * (math.sin(a) * spread) + ref * ((1.0 + math.cos(a)) * 0.5 * spread)
            else:
                o = (ref * math.cos(a) + bi * math.sin(a)) * spread
            path.points.append(centers[i] + o)
            path.normals.append(normals[i])
            path.radii.append(radii[i] * P.strand_radius)
        path.weights, path.tparams, path.closed = weights, ts, g.closed
        path.leaf_scale = g.settings.leaves / count
        out.append(path)
    return out


def _tendrils(g, centers, normals, radii, weights, ts, P, scale, rng):
    kind = "body" if g.kind == guides.KIND_BODY else "skirt"
    interval = 1.0 / (P.tendril_density / scale)
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
        # Grow sideways and away from the body, curling into a spiral.
        out_dir = (bi * side + ref * 0.8 + t * rng.uniform(-0.3, 0.6)).normalized()
        if kind == "body":
            axis = ref.copy()  # curl in the tangent plane, so it never dives into the skin
        else:
            axis = out_dir.cross(ref)
            if axis.length_squared < 1e-9:
                axis = t.copy()
        axis.normalize()
        length = P.tendril_size * scale * rng.uniform(0.6, 1.4)
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
            # Curvature increases toward the tip -> spiral.
            ang = coils * math.tau / m * (0.3 + 1.7 * u)
            d = Quaternion(axis, ang * side) @ d
            pos = pos + d * seg
        path.weights = [weights[i]] * len(path.points)
        path.tparams = [ts[i]] * len(path.points)
        path.leaf_scale = 0.0
        out.append(path)
    return out
