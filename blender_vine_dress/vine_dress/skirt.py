"""Skirt part: vines that flare out like a bell below the waist (the submerged part)."""

import math

from mathutils import Vector

from .growth import VinePath
from .sampler import blend_weights, smoothstep


class SkirtShape:
    def __init__(self, sampler, view, P, scale, z_top, z_bot):
        self.sampler = sampler
        self.view = view  # BodySampler or SubsetView used for hull + weights
        self.P = P
        self.scale = scale
        self.z_top = z_top
        self.z_bot = z_bot
        H = sampler.height
        self.H = H

        band = 0.03 * H
        near = [c for c in sampler.co if abs(c.z - z_top) < band]
        if len(near) < 3:
            near = sampler.co
        cx = sum(c.x for c in near) / len(near)
        cy = sum(c.y for c in near) / len(near)
        self.center = Vector((cx, cy, 0.0))
        self.clearance = (P.surface_offset + P.radius * 2.0) * scale

        # Body radius at the top of the skirt, smoothed around the circle.
        n = 72
        raw = [self.hull(z_top, math.tau * k / n) for k in range(n)]
        fallback = max(raw) if max(raw) > 0 else 0.12 * H
        raw = [r if r > 0 else fallback for r in raw]
        sm = raw
        for _ in range(3):
            sm = [(sm[k - 1] + sm[k] * 2.0 + sm[(k + 1) % n]) * 0.25 for k in range(n)]
        self._top = sm

    def hull(self, z, theta):
        """Outer radius of the body (ray cast from the outside, toward the axis)."""
        far = self.H * 2.0
        d = Vector((math.cos(theta), math.sin(theta), 0.0))
        origin = Vector((self.center.x, self.center.y, z)) + d * far
        hit = self.view.ray_cast(origin, -d, far)
        if hit is None:
            return 0.0
        return far - (hit.loc - origin).length

    def top_radius(self, theta):
        n = len(self._top)
        f = (theta % math.tau) / math.tau * n
        i = int(f)
        a = f - i
        return self._top[i % n] * (1.0 - a) + self._top[(i + 1) % n] * a

    def radius(self, t, theta, phase):
        P = self.P
        z = self.z_top + (self.z_bot - self.z_top) * t
        r = self.top_radius(theta) + self.clearance + P.flare * self.H * (t ** P.flare_power)
        r *= 1.0 + P.ruffle * t * math.sin(P.ruffle_freq * theta + phase)
        if P.skirt_follow_body:
            r = max(r, self.hull(z, theta) + self.clearance)
        return r, z

    def point(self, theta, r, z):
        return Vector((self.center.x + math.cos(theta) * r, self.center.y + math.sin(theta) * r, z))


def build_skirt(sampler, view, P, rng, scale, z_top, z_bot):
    if z_top - z_bot < 1e-4:
        return []
    shape = SkirtShape(sampler, view, P, scale, z_top, z_bot)
    step = P.step_length * scale * 1.5
    steps = max(16, min(400, int((z_top - z_bot) * (1.0 + P.flare) / step)))
    ruffle_phase = rng.uniform(0.0, math.tau)
    paths = []

    n = max(1, P.skirt_vines)
    fams = 2 if P.skirt_weave else 1
    for i in range(n):
        fam = i % fams
        k = i // fams
        nf = (n + fams - 1 - fam) // fams
        theta0 = math.tau * k / max(1, nf) + (math.pi / max(1, nf) if fam else 0.0)
        theta0 += rng.uniform(-0.3, 0.3) * math.tau / max(1, n)
        sgn = (1.0 if fam == 0 else -1.0) if P.skirt_weave else (1.0 if rng.random() < 0.5 else -1.0)
        ph1, ph2, ph3 = (rng.uniform(0.0, math.tau) for _ in range(3))
        wob = P.skirt_wave
        length = rng.uniform(0.85, 1.0) if P.skirt_ragged else 1.0

        path = VinePath("skirt")
        rs, thetas, zs, ts = [], [], [], []
        m = max(4, int(steps * length))
        for j in range(m + 1):
            t = length * j / m
            theta = (theta0 + sgn * P.twist * math.tau * t
                     + wob * 0.6 * math.sin(t * math.tau * 2.0 + ph1) * t)
            r, z = shape.radius(t, theta, ruffle_phase)
            r += wob * shape.H * 0.15 * math.sin(t * math.tau * 3.0 + ph2) * t
            z += wob * shape.H * 0.05 * math.sin(t * math.tau * 2.5 + ph3) * t
            rs.append(r)
            thetas.append(theta)
            zs.append(z)
            ts.append(t)
        _smooth_radii(rs, thetas, zs, shape, P)
        _emit(path, shape, rs, thetas, zs, ts, P, scale, closed=False)
        paths.append(path)

    for k in range(P.skirt_rings):
        t = (k + 1) / (P.skirt_rings + 1)
        path = VinePath("skirt")
        segs = 96
        ph = rng.uniform(0.0, math.tau)
        rs, thetas, zs, ts = [], [], [], []
        for j in range(segs):
            theta = math.tau * j / segs
            r, z = shape.radius(t, theta, ruffle_phase)
            z += P.skirt_wave * shape.H * 0.03 * math.sin(3.0 * theta + ph)
            rs.append(r)
            thetas.append(theta)
            zs.append(z)
            ts.append(t)
        _emit(path, shape, rs, thetas, zs, ts, P, scale, closed=True)
        paths.append(path)

    _assign_skirt_weights(paths, shape, P)
    return paths


def _smooth_radii(rs, thetas, zs, shape, P):
    if not P.skirt_follow_body:
        return
    floor = [shape.hull(z, th) + shape.clearance for z, th in zip(zs, thetas)]
    for _ in range(4):
        new = rs[:]
        for i in range(1, len(rs) - 1):
            new[i] = max(floor[i], (rs[i - 1] + rs[i] * 2.0 + rs[i + 1]) * 0.25)
        rs[:] = new


def _emit(path, shape, rs, thetas, zs, ts, P, scale, closed):
    r0 = P.radius * scale * P.skirt_radius
    path.closed = closed
    count = len(rs)
    for i, (r, th, z, t) in enumerate(zip(rs, thetas, zs, ts)):
        path.points.append(shape.point(th, r, z))
        path.normals.append(Vector((math.cos(th), math.sin(th), 0.0)))
        rad = r0 * (1.0 if closed else max(0.3, 1.0 - P.taper * 0.6 * t))
        if not closed:
            tip = count - 1 - i
            if tip < 4:
                rad *= 0.35 + 0.65 * tip / 4.0
        path.radii.append(rad)
    path.tparams = list(ts)


def _assign_skirt_weights(paths, shape, P):
    """Skirt points follow the nearest body part near the waist and blend toward
    the waist (pelvis) further down, so the hem is not torn apart between the
    legs. Weights are sampled on an (angle, height) grid and blurred around the
    circle so neighbouring vines move coherently."""
    view = shape.view
    na, nt = 48, 12
    grid = []
    anchors = []
    for a in range(na):
        th = math.tau * a / na
        r = shape.top_radius(th)
        hit = view.nearest(shape.point(th, r, shape.z_top))
        anchors.append(view.weights_at(hit) if hit else {})
    for j in range(nt + 1):
        t = j / nt
        z = shape.z_top + (shape.z_bot - shape.z_top) * t
        row = []
        for a in range(na):
            th = math.tau * a / na
            r = max(shape.hull(z, th), shape.top_radius(th) * 0.5)
            hit = view.nearest(shape.point(th, r, z))
            near = view.weights_at(hit) if hit else {}
            row.append(blend_weights(near, anchors[a], P.skirt_stiffness * smoothstep(t * 1.5)))
        for _ in range(2 + j // 3):
            row = [blend_weights(blend_weights(row[a - 1], row[(a + 1) % na], 0.5), row[a], 0.5)
                   for a in range(na)]
        grid.append(row)

    def lookup(p, t):
        th = math.atan2(p.y - shape.center.y, p.x - shape.center.x) % math.tau
        fa = th / math.tau * na
        a0 = int(fa) % na
        a1 = (a0 + 1) % na
        ua = fa - int(fa)
        ft = max(0.0, min(1.0, t)) * nt
        j0 = min(int(ft), nt)
        j1 = min(j0 + 1, nt)
        ut = ft - j0
        lo = blend_weights(grid[j0][a0], grid[j0][a1], ua)
        hi = blend_weights(grid[j1][a0], grid[j1][a1], ua)
        return blend_weights(lo, hi, ut)

    for path in paths:
        path.weights = [lookup(p, t) for p, t in zip(path.points, path.tparams)]
