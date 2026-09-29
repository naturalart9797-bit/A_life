# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport overlay drawing (dots, highlights, previews, brush, HUD)."""

import math

import blf
import gpu
from gpu_extras.batch import batch_for_shader

# Maya-like palette
COL_DOT = (0.2, 1.0, 0.45, 1.0)
COL_HOVER = (1.0, 0.85, 0.1, 1.0)
COL_HOVER_FACE = (1.0, 0.85, 0.1, 0.18)
COL_FILL = (0.25, 0.55, 1.0, 0.35)
COL_FILL_EDGE = (0.35, 0.75, 1.0, 1.0)
COL_LOOP = (0.2, 1.0, 0.45, 1.0)
COL_DELETE = (1.0, 0.2, 0.2, 1.0)
COL_DELETE_FACE = (1.0, 0.15, 0.15, 0.3)
COL_BRUSH = (1.0, 1.0, 1.0, 0.8)
COL_RELAX = (0.3, 0.8, 1.0, 0.9)
COL_TEXT = (1.0, 1.0, 1.0, 0.9)
COL_WELD = (0.1, 1.0, 1.0, 1.0)


def _shader(name, fallback=None):
    try:
        return gpu.shader.from_builtin(name)
    except (ValueError, SystemError):
        if fallback:
            return gpu.shader.from_builtin(fallback)
        raise


def points(coords, color, size=8.0):
    if not coords:
        return
    sh = _shader('POINT_UNIFORM_COLOR', 'UNIFORM_COLOR')
    gpu.state.point_size_set(size)
    batch = batch_for_shader(sh, 'POINTS', {"pos": coords})
    sh.uniform_float("color", color)
    batch.draw(sh)
    gpu.state.point_size_set(1.0)


def lines(coords, color, width=2.0):
    """``coords`` are pairs of points (LINES primitive)."""
    if len(coords) < 2:
        return
    sh = _shader('POLYLINE_UNIFORM_COLOR')
    vp = gpu.state.viewport_get()
    sh.uniform_float("viewportSize", (vp[2], vp[3]))
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch = batch_for_shader(sh, 'LINES', {"pos": coords})
    batch.draw(sh)


def polyline(coords, color, width=2.0, closed=False):
    if len(coords) < 2:
        return
    segs = []
    n = len(coords)
    for i in range(n if closed else n - 1):
        segs.append(coords[i])
        segs.append(coords[(i + 1) % n])
    lines(segs, color, width)


def polygon(coords, color):
    """Triangle-fan fill of a (convex-ish) polygon."""
    if len(coords) < 3:
        return
    tris = []
    for i in range(1, len(coords) - 1):
        tris.extend((coords[0], coords[i], coords[i + 1]))
    triangles(tris, color)


def triangles(coords, color):
    if len(coords) < 3:
        return
    sh = _shader('UNIFORM_COLOR')
    sh.uniform_float("color", color)
    batch = batch_for_shader(sh, 'TRIS', {"pos": coords})
    batch.draw(sh)


def circle_2d(center, radius, color, width=1.5, segments=48):
    cx, cy = center
    pts = [(cx + math.cos(i / segments * math.tau) * radius,
            cy + math.sin(i / segments * math.tau) * radius, 0.0)
           for i in range(segments)]
    polyline(pts, color, width, closed=True)


def text(x, y, lines_, size=13, color=COL_TEXT):
    font_id = 0
    try:
        blf.size(font_id, size)
    except TypeError:  # very old signature
        blf.size(font_id, size, 72)
    blf.color(font_id, *color)
    blf.enable(font_id, blf.SHADOW)
    blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
    for i, line in enumerate(lines_):
        blf.position(font_id, x, y + (len(lines_) - 1 - i) * (size + 5), 0)
        blf.draw(font_id, line)
    blf.disable(font_id, blf.SHADOW)


def begin():
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)


def end():
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')
