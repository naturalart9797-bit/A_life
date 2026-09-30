# SPDX-License-Identifier: GPL-3.0-or-later
"""BMesh operations used by Quad Draw.

Everything in this module works in the *local space of the retopo object* and
does not depend on the viewport, so it can be tested headless.
"""

import bmesh
from mathutils import Vector
from mathutils.kdtree import KDTree


# ---------------------------------------------------------------------------
# Component classification
# ---------------------------------------------------------------------------

def is_open_vert(v):
    """A vertex that can still receive new faces (border, wire or loose)."""
    if not v.link_faces:
        return True
    return v.is_boundary


def is_open_edge(e):
    """A border (one face) or wire (no face) edge."""
    return len(e.link_faces) < 2


# ---------------------------------------------------------------------------
# Face creation
# ---------------------------------------------------------------------------

def _winding_vote(face):
    """Return -1 if the face winding disagrees with its neighbours, +1 if it
    agrees, 0 if there are no neighbours."""
    vote = 0
    for loop in face.loops:
        for other in loop.link_loops:
            if other.face is face:
                continue
            # Neighbouring faces must traverse a shared edge in opposite directions.
            vote += -1 if other.vert is loop.vert else 1
    return vote


def create_face(bm, verts, ref_normal=None):
    """Create a face from ``verts`` with a winding consistent with its
    neighbours (or ``ref_normal`` when it has none). Returns the face or None."""
    verts = list(verts)
    if len(verts) < 3 or len(set(verts)) != len(verts):
        return None
    if bm.faces.get(verts) is not None:
        return None
    # Refuse to make non-manifold edges.
    for i, v in enumerate(verts):
        e = bm.edges.get((v, verts[(i + 1) % len(verts)]))
        if e is not None and len(e.link_faces) >= 2:
            return None
    try:
        face = bm.faces.new(verts)
    except ValueError:
        return None
    face.normal_update()
    vote = _winding_vote(face)
    if vote < 0:
        face.normal_flip()
    elif vote == 0 and ref_normal is not None and face.normal.dot(ref_normal) < 0.0:
        face.normal_flip()
    face.normal_update()
    return face


# ---------------------------------------------------------------------------
# Edge ring / edge loop walking
# ---------------------------------------------------------------------------

def _ring_step(edge, s, came_from):
    """Cross the quad adjacent to ``edge`` (not ``came_from``).

    ``s`` is the vertex of ``edge`` that defines the "start" side of the ring.
    Returns (face, opposite_edge, opposite_start_vert) or None.
    """
    for face in edge.link_faces:
        if face is came_from or len(face.verts) != 4:
            continue
        for loop in face.loops:
            if loop.edge is edge:
                break
        else:
            continue
        v0 = loop.vert
        l_opp = loop.link_loop_next.link_loop_next
        v2 = l_opp.vert
        v3 = l_opp.link_loop_next.vert
        # Quad v0 v1 v2 v3: side edges are v1-v2 and v3-v0.
        ns = v3 if s is v0 else v2
        return face, l_opp.edge, ns
    return None


def edge_ring(edge):
    """Walk the edge ring through quads.

    Returns ``(ring, faces, closed)`` where ``ring`` is a list of
    ``(edge, start_vert)`` with consistent orientation and ``faces[i]`` is the
    quad between ``ring[i]`` and ``ring[i + 1]`` (wrapping when closed).
    """
    s0 = edge.verts[0]
    ring_fwd = [(edge, s0)]
    faces_fwd = []
    visited = set()
    closed = False
    e, s, prev = edge, s0, None
    while True:
        step = _ring_step(e, s, prev)
        if step is None:
            break
        f, ne, ns = step
        if f in visited:
            break
        visited.add(f)
        faces_fwd.append(f)
        if ne is edge:
            closed = True
            break
        ring_fwd.append((ne, ns))
        e, s, prev = ne, ns, f
    if closed:
        return ring_fwd, faces_fwd, True

    ring_bwd = []
    faces_bwd = []
    e, s, prev = edge, s0, (faces_fwd[0] if faces_fwd else None)
    if prev is not None:
        while True:
            step = _ring_step(e, s, prev)
            if step is None:
                break
            f, ne, ns = step
            if f in visited:
                break
            visited.add(f)
            faces_bwd.append(f)
            ring_bwd.append((ne, ns))
            e, s, prev = ne, ns, f
    ring = list(reversed(ring_bwd)) + ring_fwd
    faces = list(reversed(faces_bwd)) + faces_fwd
    return ring, faces, False


def edge_ring_preview(edge, t):
    """Points of the edge loop that ``insert_edge_loop(edge, t)`` would make.

    Returns (points, closed)."""
    ring, _faces, closed = edge_ring(edge)
    pts = [s.co.lerp(e.other_vert(s).co, t) for e, s in ring]
    return pts, closed


def insert_edge_loop(bm, edge, t):
    """Insert an edge loop across the ring of ``edge``.

    ``t`` is measured from ``edge.verts[0]``. Returns the new vertices."""
    ring, faces, closed = edge_ring(edge)
    new_verts = []
    for e, s in ring:
        other = e.other_vert(s)
        co = s.co.lerp(other.co, t)
        _ne, nv = bmesh.utils.edge_split(e, s, t)
        nv.co = co
        new_verts.append(nv)
    n = len(new_verts)
    for i, f in enumerate(faces):
        a = new_verts[i]
        b = new_verts[(i + 1) % n]
        if a is b:
            continue
        try:
            bmesh.utils.face_split(f, a, b)
        except ValueError:
            pass
    return new_verts


def edge_loop(edge):
    """Walk an edge loop through valence-4 vertices."""
    loop = [edge]
    seen = {edge}
    for start in edge.verts:
        e = edge
        cur = start
        while True:
            if len(cur.link_edges) != 4 or len(cur.link_faces) != 4:
                break
            faces = set(e.link_faces)
            cands = [x for x in cur.link_edges
                     if x is not e and not (set(x.link_faces) & faces)]
            if len(cands) != 1:
                break
            nx = cands[0]
            if nx in seen:
                break
            seen.add(nx)
            loop.append(nx)
            cur = nx.other_vert(cur)
            e = nx
    return loop


def border_chain(edge):
    """Ordered vertices of the border edge loop through ``edge`` (Maya rule).

    The loop only passes through *regular* border vertices -- 3 edges: two
    border edges plus one edge going into the mesh. It stops at convex corners
    (2 edges, one face) and at concave corners / poles (4+ edges), i.e. wherever
    the border turns a corner topologically. Wire edges (no faces) continue
    through vertices with 2 edges. Returns (verts, closed, index) where
    ``index`` is the position of ``edge`` (edge = verts[index], verts[index + 1]).
    """
    if not is_open_edge(edge):
        a, b = edge.verts
        return [a, b], False, 0

    def passes(v):
        if v.link_faces:
            return len(v.link_edges) == 3
        return len(v.link_edges) == 2

    def walk(start, prev_edge, stop, taken):
        out = []
        cur = start
        while passes(cur):
            nxt = [e for e in cur.link_edges if e is not prev_edge and is_open_edge(e)]
            if len(nxt) != 1:
                break
            e = nxt[0]
            v = e.other_vert(cur)
            if v is stop:
                return out, True
            if v in taken:
                break
            out.append(v)
            taken.add(v)
            prev_edge, cur = e, v
        return out, False

    a, b = edge.verts
    taken = {a, b}
    fwd, closed = walk(b, edge, a, taken)
    if closed:
        return [a, b] + fwd, True, 0
    bwd, _ = walk(a, edge, None, taken)
    verts = list(reversed(bwd)) + [a, b] + fwd
    return verts, False, len(bwd)


def cleanup_after_delete(bm, edges, verts):
    """Remove edges that lost all their faces and vertices left without edges,
    so deleting never leaves stray wire edges behind (only ``edges``/``verts``
    that had faces before the delete are considered)."""
    wire = [e for e in edges if e.is_valid and not e.link_faces]
    if wire:
        bmesh.ops.delete(bm, geom=wire, context='EDGES')
    loose = [v for v in verts if v.is_valid and not v.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')


def affected_by(elems):
    """Edges / verts of every face touching ``elems`` (verts, edges or faces)."""
    faces = set()
    for el in elems:
        if isinstance(el, bmesh.types.BMFace):
            faces.add(el)
        else:
            faces.update(el.link_faces)
    edges = {e for f in faces for e in f.edges}
    verts = {v for f in faces for v in f.verts}
    return edges, verts


def delete_edge_loop(bm, edge):
    """Remove the edge loop through ``edge`` (Maya: Ctrl+Shift click edge).

    A border edge removes its face; nothing is left dangling."""
    if not edge.link_faces:
        verts = list(edge.verts)
        bmesh.ops.delete(bm, geom=[edge], context='EDGES')
        cleanup_after_delete(bm, [], verts)
        return
    if is_open_edge(edge):
        edges, verts = affected_by([edge])
        bmesh.ops.delete(bm, geom=list(edge.link_faces), context='FACES_ONLY')
        cleanup_after_delete(bm, edges, verts)
        return
    loop = edge_loop(edge)
    edges, verts = affected_by(loop)
    bmesh.ops.dissolve_edges(bm, edges=loop, use_verts=True, use_face_split=False)
    cleanup_after_delete(bm, edges, verts)


def delete_verts(bm, verts):
    """Delete vertices (and their faces) without leaving stray edges."""
    edges, around = affected_by(verts)
    bmesh.ops.delete(bm, geom=list(verts), context='VERTS')
    cleanup_after_delete(bm, edges, around)


def delete_faces(bm, faces):
    edges, verts = affected_by(faces)
    bmesh.ops.delete(bm, geom=list(faces), context='FACES_ONLY')
    cleanup_after_delete(bm, edges, verts)


# ---------------------------------------------------------------------------
# Relax
# ---------------------------------------------------------------------------

def relax_verts(verts_weights, project=None, relax_boundary=True):
    """Laplacian relax. ``verts_weights`` is a list of (vert, weight 0..1).

    Border vertices only move along the border. ``project`` (optional) maps a
    coordinate back onto the reference surface."""
    new = []
    for v, w in verts_weights:
        if w <= 0.0 or not v.link_faces:
            continue
        if v.is_boundary:
            if not relax_boundary:
                continue
            nbs = [e.other_vert(v) for e in v.link_edges if e.is_boundary]
            if len(nbs) != 2:
                continue
        else:
            nbs = [e.other_vert(v) for e in v.link_edges]
            if len(nbs) < 3:
                continue
        avg = Vector()
        for n in nbs:
            avg += n.co
        avg /= len(nbs)
        new.append((v, v.co.lerp(avg, w)))
    for v, co in new:
        if project is not None:
            p = project(co)
            if p is not None:
                co = p
        v.co = co
    return [v for v, _ in new]


# ---------------------------------------------------------------------------
# Welding
# ---------------------------------------------------------------------------

def merge_vert_into(bm, src, dst):
    """Merge ``src`` into ``dst`` (dst keeps its position)."""
    if src is dst or not src.is_valid or not dst.is_valid:
        return dst
    co = dst.co.copy()
    bmesh.ops.pointmerge(bm, verts=[src, dst], merge_co=co)
    # pointmerge keeps one of the two verts; find the survivor.
    survivor = dst if dst.is_valid else (src if src.is_valid else None)
    degenerate = [f for f in bm.faces if len(f.verts) < 3]
    if degenerate:
        bmesh.ops.delete(bm, geom=degenerate, context='FACES_ONLY')
    return survivor


# ---------------------------------------------------------------------------
# Symmetry (object local X axis)
# ---------------------------------------------------------------------------

def mirror_co(co):
    return Vector((-co.x, co.y, co.z))


class MirrorLookup:
    """Find the mirrored counterpart of vertices by position."""

    def __init__(self, bm, tol):
        self.tol = tol
        self.verts = [v for v in bm.verts]
        self.kd = KDTree(len(self.verts))
        for i, v in enumerate(self.verts):
            self.kd.insert(v.co, i)
        self.kd.balance()
        self.extra = []

    def add(self, v):
        self.extra.append(v)

    def find(self, co):
        target = mirror_co(co)
        if self.verts:
            _co, idx, dist = self.kd.find(target)
            if idx is not None and dist <= self.tol:
                v = self.verts[idx]
                if v.is_valid:
                    return v
        for v in self.extra:
            if v.is_valid and (v.co - target).length <= self.tol:
                return v
        return None

    def mirror_of(self, v):
        """Mirror of vertex ``v`` or None. Centre vertices mirror to themselves."""
        if abs(v.co.x) <= self.tol:
            return v
        m = self.find(v.co)
        return m


def symmetrize_faces(bm, faces, tol, dots=None, ref_normal_fn=None):
    """Create the mirrored copy of ``faces`` (reusing existing vertices and
    consuming dots at mirrored positions). Returns the new faces."""
    lookup = MirrorLookup(bm, tol)
    new_faces = []
    for f in faces:
        if not f.is_valid:
            continue
        src = list(f.verts)
        mverts = []
        for v in src:
            if abs(v.co.x) <= tol:
                v.co.x = 0.0
                mverts.append(v)
                continue
            m = lookup.find(v.co)
            if m is None:
                m = bm.verts.new(mirror_co(v.co))
                lookup.add(m)
                if dots is not None:
                    target = m.co
                    for i in range(len(dots) - 1, -1, -1):
                        if (dots[i] - target).length <= tol:
                            dots.pop(i)
            mverts.append(m)
        if set(mverts) == set(src):
            continue
        mverts.reverse()
        n = None
        if ref_normal_fn is not None:
            n = ref_normal_fn(sum((v.co for v in mverts), Vector()) / len(mverts))
        nf = create_face(bm, mverts, n)
        if nf is not None:
            new_faces.append(nf)
    return new_faces
