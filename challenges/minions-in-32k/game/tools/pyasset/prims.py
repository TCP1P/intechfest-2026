"""Procedural primitives. All primitives emit flat-shaded geometry (per-face
duplicated verts) so the chunky pixel-art look reads cleanly at 160x100.

Sizes are in meters. Primitives center on origin unless noted.
"""

from __future__ import annotations

import math
import numpy as np

from .builder import MeshBuilder


def box(size=(1.0, 1.0, 1.0), color=(0.8, 0.8, 0.8), center=(0.0, 0.0, 0.0), name="box"):
    sx, sy, sz = size[0] / 2, size[1] / 2, size[2] / 2
    cx, cy, cz = center
    mb = MeshBuilder(name=name)
    # Corners
    p = [
        (cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz),
        (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
        (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz),
        (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz),
    ]
    faces = [
        # (v0,v1,v2,v3, normal)
        ((4, 5, 6, 7), (0, 0, 1)),    # +Z front
        ((1, 0, 3, 2), (0, 0, -1)),   # -Z back
        ((0, 4, 7, 3), (-1, 0, 0)),   # -X left
        ((5, 1, 2, 6), (1, 0, 0)),    # +X right
        ((3, 7, 6, 2), (0, 1, 0)),    # +Y top
        ((0, 1, 5, 4), (0, -1, 0)),   # -Y bottom
    ]
    for (i0, i1, i2, i3), n in faces:
        base = mb.vcount()
        for idx in (i0, i1, i2, i3):
            mb.add_vertex(p[idx], n, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def plane(size=(1.0, 1.0), color=(0.5, 0.5, 0.5), y=0.0, center=(0.0, 0.0), name="plane"):
    sx, sz = size[0] / 2, size[1] / 2
    cx, cz = center
    mb = MeshBuilder(name=name)
    p = [
        (cx - sx, y, cz - sz),
        (cx + sx, y, cz - sz),
        (cx + sx, y, cz + sz),
        (cx - sx, y, cz + sz),
    ]
    n = (0, 1, 0)
    base = mb.vcount()
    for v in p:
        mb.add_vertex(v, n, color)
    mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def cylinder(radius=0.5, height=1.0, segments=16, color=(0.8, 0.8, 0.8),
             center=(0.0, 0.0, 0.0), name="cylinder", caps=True):
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    hy = height / 2
    # Side faces — flat shaded per segment
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        x0, z0 = math.cos(a0) * radius, math.sin(a0) * radius
        x1, z1 = math.cos(a1) * radius, math.sin(a1) * radius
        nx = math.cos((a0 + a1) / 2)
        nz = math.sin((a0 + a1) / 2)
        nrm = (nx, 0, nz)
        base = mb.vcount()
        mb.add_vertex((cx + x0, cy - hy, cz + z0), nrm, color)
        mb.add_vertex((cx + x1, cy - hy, cz + z1), nrm, color)
        mb.add_vertex((cx + x1, cy + hy, cz + z1), nrm, color)
        mb.add_vertex((cx + x0, cy + hy, cz + z0), nrm, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    if caps:
        # Top cap
        base = mb.vcount()
        mb.add_vertex((cx, cy + hy, cz), (0, 1, 0), color)
        for i in range(segments):
            a = (i / segments) * math.tau
            mb.add_vertex((cx + math.cos(a) * radius, cy + hy, cz + math.sin(a) * radius),
                          (0, 1, 0), color)
        for i in range(segments):
            mb.add_tri(base, base + 1 + i, base + 1 + ((i + 1) % segments))
        # Bottom cap
        base = mb.vcount()
        mb.add_vertex((cx, cy - hy, cz), (0, -1, 0), color)
        for i in range(segments):
            a = (i / segments) * math.tau
            mb.add_vertex((cx + math.cos(a) * radius, cy - hy, cz + math.sin(a) * radius),
                          (0, -1, 0), color)
        for i in range(segments):
            mb.add_tri(base, base + 1 + ((i + 1) % segments), base + 1 + i)
    return mb


def cone(radius=0.5, height=1.0, segments=16, color=(0.8, 0.8, 0.8),
         center=(0.0, 0.0, 0.0), name="cone"):
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    hy = height / 2
    apex = (cx, cy + hy, cz)
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        p0 = (cx + math.cos(a0) * radius, cy - hy, cz + math.sin(a0) * radius)
        p1 = (cx + math.cos(a1) * radius, cy - hy, cz + math.sin(a1) * radius)
        # Face normal
        e1 = np.array(p1) - np.array(apex)
        e2 = np.array(p0) - np.array(apex)
        n = np.cross(e1, e2)
        nn = np.linalg.norm(n)
        if nn > 1e-8:
            n = n / nn
        base = mb.vcount()
        mb.add_vertex(apex, n, color)
        mb.add_vertex(p0, n, color)
        mb.add_vertex(p1, n, color)
        mb.add_tri(base, base + 1, base + 2)
    # Bottom cap
    base = mb.vcount()
    mb.add_vertex((cx, cy - hy, cz), (0, -1, 0), color)
    for i in range(segments):
        a = (i / segments) * math.tau
        mb.add_vertex((cx + math.cos(a) * radius, cy - hy, cz + math.sin(a) * radius),
                      (0, -1, 0), color)
    for i in range(segments):
        mb.add_tri(base, base + 1 + ((i + 1) % segments), base + 1 + i)
    return mb


def tube(inner=0.3, outer=0.5, height=1.0, segments=16, color=(0.8, 0.8, 0.8),
         center=(0.0, 0.0, 0.0), name="tube"):
    """Hollow cylinder (ring)."""
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    hy = height / 2
    # Outer side
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        x0, z0 = math.cos(a0) * outer, math.sin(a0) * outer
        x1, z1 = math.cos(a1) * outer, math.sin(a1) * outer
        nrm = (math.cos((a0 + a1) / 2), 0, math.sin((a0 + a1) / 2))
        base = mb.vcount()
        mb.add_vertex((cx + x0, cy - hy, cz + z0), nrm, color)
        mb.add_vertex((cx + x1, cy - hy, cz + z1), nrm, color)
        mb.add_vertex((cx + x1, cy + hy, cz + z1), nrm, color)
        mb.add_vertex((cx + x0, cy + hy, cz + z0), nrm, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    # Inner side (normals flipped inward)
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        x0, z0 = math.cos(a0) * inner, math.sin(a0) * inner
        x1, z1 = math.cos(a1) * inner, math.sin(a1) * inner
        nrm = (-math.cos((a0 + a1) / 2), 0, -math.sin((a0 + a1) / 2))
        base = mb.vcount()
        mb.add_vertex((cx + x1, cy - hy, cz + z1), nrm, color)
        mb.add_vertex((cx + x0, cy - hy, cz + z0), nrm, color)
        mb.add_vertex((cx + x0, cy + hy, cz + z0), nrm, color)
        mb.add_vertex((cx + x1, cy + hy, cz + z1), nrm, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    # Top + bottom ring faces
    for sgn, nrm in ((1, (0, 1, 0)), (-1, (0, -1, 0))):
        for i in range(segments):
            a0 = (i / segments) * math.tau
            a1 = ((i + 1) / segments) * math.tau
            o0 = (cx + math.cos(a0) * outer, cy + sgn * hy, cz + math.sin(a0) * outer)
            o1 = (cx + math.cos(a1) * outer, cy + sgn * hy, cz + math.sin(a1) * outer)
            i0 = (cx + math.cos(a0) * inner, cy + sgn * hy, cz + math.sin(a0) * inner)
            i1 = (cx + math.cos(a1) * inner, cy + sgn * hy, cz + math.sin(a1) * inner)
            base = mb.vcount()
            if sgn > 0:
                mb.add_vertex(i0, nrm, color)
                mb.add_vertex(o0, nrm, color)
                mb.add_vertex(o1, nrm, color)
                mb.add_vertex(i1, nrm, color)
            else:
                mb.add_vertex(o0, nrm, color)
                mb.add_vertex(i0, nrm, color)
                mb.add_vertex(i1, nrm, color)
                mb.add_vertex(o1, nrm, color)
            mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def icosphere(radius=0.5, subdivisions=1, color=(0.8, 0.8, 0.8),
              center=(0.0, 0.0, 0.0), name="sphere"):
    """Low-poly icosphere. subdivisions=0 -> 20 tris (icosahedron)."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    verts = [_norm(v) for v in verts]
    for _ in range(subdivisions):
        midcache = {}
        new_faces = []

        def mid(a, b):
            key = tuple(sorted((a, b)))
            if key not in midcache:
                v = ((verts[a][0] + verts[b][0]) / 2,
                     (verts[a][1] + verts[b][1]) / 2,
                     (verts[a][2] + verts[b][2]) / 2)
                verts.append(_norm(v))
                midcache[key] = len(verts) - 1
            return midcache[key]

        for a, b, c in faces:
            ab = mid(a, b)
            bc = mid(b, c)
            ca = mid(c, a)
            new_faces.extend([(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)])
        faces = new_faces
    cx, cy, cz = center
    mb = MeshBuilder(name=name)
    for a, b, c in faces:
        va = verts[a]
        vb = verts[b]
        vc = verts[c]
        # flat-shade per face
        e1 = (vb[0] - va[0], vb[1] - va[1], vb[2] - va[2])
        e2 = (vc[0] - va[0], vc[1] - va[1], vc[2] - va[2])
        n = (
            e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0],
        )
        nn = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1
        n = (n[0] / nn, n[1] / nn, n[2] / nn)
        base = mb.vcount()
        mb.add_vertex((cx + va[0] * radius, cy + va[1] * radius, cz + va[2] * radius), n, color)
        mb.add_vertex((cx + vb[0] * radius, cy + vb[1] * radius, cz + vb[2] * radius), n, color)
        mb.add_vertex((cx + vc[0] * radius, cy + vc[1] * radius, cz + vc[2] * radius), n, color)
        mb.add_tri(base, base + 1, base + 2)
    return mb


def _norm(v):
    m = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1
    return (v[0] / m, v[1] / m, v[2] / m)


def capsule(radius=0.5, cyl_height=1.0, segments=16, rings=4, color=(0.8, 0.8, 0.8),
            center=(0.0, 0.0, 0.0), name="capsule"):
    """Capsule with smooth normals (minion body reads nicer smooth-shaded)."""
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    hy = cyl_height / 2

    # Top hemisphere
    for r in range(rings + 1):
        phi0 = (math.pi / 2) * (r / rings)      # 0..pi/2
        phi1 = (math.pi / 2) * ((r + 1) / rings) if r < rings else math.pi / 2
        for i in range(segments):
            a0 = (i / segments) * math.tau
            a1 = ((i + 1) / segments) * math.tau
            if r < rings:
                # quad between two rings
                y0 = math.sin(phi0) * radius
                r0 = math.cos(phi0) * radius
                y1 = math.sin(phi1) * radius
                r1 = math.cos(phi1) * radius

                def vv(r_, y_, a_):
                    return (cx + math.cos(a_) * r_, cy + hy + y_, cz + math.sin(a_) * r_)

                def nn(r_, y_, a_):
                    # smooth normal — from center of hemisphere
                    p = (math.cos(a_) * r_, y_, math.sin(a_) * r_)
                    m = math.sqrt(sum(x ** 2 for x in p)) or 1
                    return (p[0] / m, p[1] / m, p[2] / m)

                base = mb.vcount()
                mb.add_vertex(vv(r0, y0, a0), nn(r0, y0, a0), color)
                mb.add_vertex(vv(r0, y0, a1), nn(r0, y0, a1), color)
                mb.add_vertex(vv(r1, y1, a1), nn(r1, y1, a1), color)
                mb.add_vertex(vv(r1, y1, a0), nn(r1, y1, a0), color)
                mb.add_quad(base, base + 1, base + 2, base + 3)

    # Cylinder middle
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        x0, z0 = math.cos(a0) * radius, math.sin(a0) * radius
        x1, z1 = math.cos(a1) * radius, math.sin(a1) * radius
        n0 = (math.cos(a0), 0, math.sin(a0))
        n1 = (math.cos(a1), 0, math.sin(a1))
        base = mb.vcount()
        mb.add_vertex((cx + x0, cy - hy, cz + z0), n0, color)
        mb.add_vertex((cx + x1, cy - hy, cz + z1), n1, color)
        mb.add_vertex((cx + x1, cy + hy, cz + z1), n1, color)
        mb.add_vertex((cx + x0, cy + hy, cz + z0), n0, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)

    # Bottom hemisphere (mirror)
    for r in range(rings):
        phi0 = (math.pi / 2) * (r / rings)
        phi1 = (math.pi / 2) * ((r + 1) / rings)
        for i in range(segments):
            a0 = (i / segments) * math.tau
            a1 = ((i + 1) / segments) * math.tau
            y0 = -math.sin(phi0) * radius
            r0 = math.cos(phi0) * radius
            y1 = -math.sin(phi1) * radius
            r1 = math.cos(phi1) * radius

            def vv(r_, y_, a_):
                return (cx + math.cos(a_) * r_, cy - hy + y_, cz + math.sin(a_) * r_)

            def nn(r_, y_, a_):
                p = (math.cos(a_) * r_, y_, math.sin(a_) * r_)
                m = math.sqrt(sum(x ** 2 for x in p)) or 1
                return (p[0] / m, p[1] / m, p[2] / m)

            base = mb.vcount()
            mb.add_vertex(vv(r0, y0, a0), nn(r0, y0, a0), color)
            mb.add_vertex(vv(r1, y1, a0), nn(r1, y1, a0), color)
            mb.add_vertex(vv(r1, y1, a1), nn(r1, y1, a1), color)
            mb.add_vertex(vv(r0, y0, a1), nn(r0, y0, a1), color)
            mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def torus(radius=1.0, tube_radius=0.25, segments=24, sides=12, color=(0.8, 0.8, 0.8),
          center=(0.0, 0.0, 0.0), name="torus"):
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    for i in range(segments):
        a0 = (i / segments) * math.tau
        a1 = ((i + 1) / segments) * math.tau
        for j in range(sides):
            b0 = (j / sides) * math.tau
            b1 = ((j + 1) / sides) * math.tau

            def v(a, b):
                r_ = radius + tube_radius * math.cos(b)
                return (cx + math.cos(a) * r_, cy + tube_radius * math.sin(b), cz + math.sin(a) * r_)

            def n(a, b):
                return (math.cos(a) * math.cos(b), math.sin(b), math.sin(a) * math.cos(b))

            base = mb.vcount()
            mb.add_vertex(v(a0, b0), n(a0, b0), color)
            mb.add_vertex(v(a1, b0), n(a1, b0), color)
            mb.add_vertex(v(a1, b1), n(a1, b1), color)
            mb.add_vertex(v(a0, b1), n(a0, b1), color)
            mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def extrude_poly(poly_xz, y0=0.0, y1=1.0, color=(0.8, 0.8, 0.8), name="prism"):
    """Extrude a 2D polygon (list of (x, z) CCW) vertically, closed solid."""
    mb = MeshBuilder(name=name)
    n = len(poly_xz)
    for i in range(n):
        x0, z0 = poly_xz[i]
        x1, z1 = poly_xz[(i + 1) % n]
        # Side face
        ex, ez = x1 - x0, z1 - z0
        nrm = (ez, 0, -ex)
        m = math.sqrt(nrm[0] ** 2 + nrm[2] ** 2) or 1
        nrm = (nrm[0] / m, 0, nrm[2] / m)
        base = mb.vcount()
        mb.add_vertex((x0, y0, z0), nrm, color)
        mb.add_vertex((x1, y0, z1), nrm, color)
        mb.add_vertex((x1, y1, z1), nrm, color)
        mb.add_vertex((x0, y1, z0), nrm, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    # Fan top + bottom (assumes convex polygon)
    base = mb.vcount()
    for (x, z) in poly_xz:
        mb.add_vertex((x, y1, z), (0, 1, 0), color)
    for i in range(1, n - 1):
        mb.add_tri(base, base + i, base + i + 1)
    base = mb.vcount()
    for (x, z) in poly_xz:
        mb.add_vertex((x, y0, z), (0, -1, 0), color)
    for i in range(1, n - 1):
        mb.add_tri(base, base + i + 1, base + i)
    return mb
