# -*- coding: utf-8 -*-
"""
stadium3d.py  -  a real 3D cricket ground (Panda3D) that plays out each
delivery the commentary is talking about.

The ground
    oval outfield with mowing stripes and the painted 30-yard circle, a
    textured pitch with creases and wear, stumps and bails, boundary rope,
    advertising boards, two tiers of crowd, roof, sight screens (black for a
    white ball, white for a red one) and floodlight towers.

The people
    every player is a jointed figure - hips, spine, head, two-bone arms and
    legs solved with IK - in his team's kit (whites in a Test), helmeted at
    the crease, surname on his back. Real data drives them: a left-hander
    stands and hits the other way and the field mirrors; a spinner walks in
    off a short run-up with the keeper up at the stumps, a quick sprints in
    with the keeper standing back; skin tone is sampled from the player's
    actual photo. Two umpires give the real signals.

A delivery (Play)
    run-up -> bound -> release -> pitch at the length the feed says
    (yorker / full / good / short / bouncer) on the line it says -> the shot
    the feed names (drive, pull, cut, sweep, defend, leave, ...) -> the ball
    to the fielding position the feed names -> fielder chases and throws,
    batsmen run their runs, or it clears the rope / hits the stumps / is
    caught -> umpire signal -> celebration.

The cameras
    cut like a broadcast: long lens behind the bowler for the delivery, then
    a follow camera on the ball, boundary / stump / celebration cameras, a
    slow-motion replay from another angle after a four, six or wicket, and
    slow wide, close-up, aerial and crowd shots between balls.

In step with the voice
    deliver() is called when the commentator starts the ball, result() when
    he says what happened. If the voice gets to the result before the
    picture does, the delivery is played faster so the shot lands with the
    words - the picture never trails the voice, and the scoreboard (updated
    on the same cue) never runs ahead of the picture.
"""
import math
import random
import hashlib

import numpy as np
from panda3d.core import (Point2, 
    GeomVertexFormat, GeomVertexData, GeomVertexWriter, Geom, GeomTriangles,
    GeomNode, NodePath, Texture, SamplerState, AmbientLight, DirectionalLight,
    Vec3, Point3, TransparencyAttrib, Fog, LineSegs, TextureStage)

# ── Ground dimensions (metres). x across (off side is -x for a right-hander),
# y along the pitch (bowler's end -y, batsman's end +y), z up.
RX, RY = 68.0, 74.0                     # rope ellipse
STUMPS_Y = 10.06
CREASE_Y = STUMPS_Y - 1.22              # popping crease
G = 9.81

TEAM_COL = {"IND": (0.10, 0.36, 0.86), "AUS": (0.95, 0.75, 0.10),
            "ENG": (0.12, 0.22, 0.56), "PAK": (0.08, 0.48, 0.24),
            "SA": (0.08, 0.44, 0.28), "NZ": (0.12, 0.12, 0.14),
            "SL": (0.12, 0.24, 0.62), "WI": (0.50, 0.08, 0.16),
            "BAN": (0.0, 0.42, 0.31), "AFG": (0.12, 0.32, 0.80),
            "ZIM": (0.80, 0.12, 0.16), "IRE": (0.16, 0.60, 0.28),
            "NEP": (0.10, 0.20, 0.60), "SCO": (0.30, 0.20, 0.50),
            "NED": (0.96, 0.45, 0.05), "UAE": (0.70, 0.10, 0.12)}
SKINS = [(0.86, 0.67, 0.53), (0.76, 0.56, 0.42), (0.62, 0.44, 0.31),
         (0.48, 0.33, 0.23), (0.92, 0.76, 0.64)]


def team_rgb(code):
    c = str(code or "").upper()
    return TEAM_COL.get(c[:3]) or TEAM_COL.get(c[:2]) or (0.36, 0.40, 0.50)


# ── Fielding positions: angle from straight down the ground (towards the
# bowler), positive towards the OFF side, and distance from the batsman's
# stumps. "deep" ones sit just inside the rope.
POS = {
    "keeper": (180, None), "wicketkeeper": (180, None),
    "slip": (165, 9.6), "slips": (165, 9.6), "first slip": (165, 9.6),
    "second slip": (158, 10.0), "third slip": (151, 10.6),
    "leg slip": (-166, 9.6), "gully": (130, 12),
    "silly point": (95, 4.0), "short leg": (-100, 4.0),
    "point": (95, 20), "backward point": (115, 19), "cover point": (82, 21),
    "cover": (70, 22), "short cover": (68, 13), "extra cover": (50, 24),
    "mid-off": (20, 23), "mid off": (20, 23), "mid-on": (-20, 23),
    "mid on": (-20, 23), "mid-wicket": (-62, 22), "midwicket": (-62, 22),
    "mid wicket": (-62, 22), "square leg": (-95, 20),
    "backward square leg": (-118, 21), "third man": (150, "deep"),
    "third-man": (150, "deep"), "short third man": (148, 30),
    "fine leg": (-155, "deep"), "long leg": (-150, "deep"),
    "deep fine leg": (-160, "deep"),
    "long off": (14, "deep"), "long-off": (14, "deep"),
    "wide long off": (28, "deep"), "long on": (-14, "deep"),
    "long-on": (-14, "deep"), "wide long on": (-28, "deep"),
    "straight": (0, "deep"), "down the ground": (0, "deep"),
    "deep extra cover": (52, "deep"), "deep cover": (72, "deep"),
    "sweeper cover": (78, "deep"), "deep point": (100, "deep"),
    "deep backward point": (118, "deep"), "deep mid-wicket": (-62, "deep"),
    "deep midwicket": (-62, "deep"), "cow corner": (-42, "deep"),
    "deep square leg": (-95, "deep"), "deep backward square leg": (-118, "deep"),
    "bowler": (0, 21), "back to the bowler": (0, 21),
}
FIELD_SETS = {
    "powerplay": ["slip", "point", "cover", "mid-off", "mid-on", "mid-wicket",
                  "square leg", "fine leg", "third man"],
    "middle": ["point", "cover", "mid-off", "mid-on", "mid-wicket",
               "deep square leg", "long off", "deep cover", "fine leg"],
    "death": ["deep point", "deep cover", "long off", "long on",
              "deep mid-wicket", "deep square leg", "fine leg", "third man",
              "cover"],
    "test": ["slip", "second slip", "third slip", "gully", "point", "cover",
             "mid-off", "mid-on", "square leg"],
}


def rope_scale(dx, dy):
    """Distance factor to the rope along a direction from the centre."""
    return 1.0 / math.sqrt((dx / RX) ** 2 + (dy / RY) ** 2)


def pos_xy(name, left=False, keeper_y=19.5):
    """World (x, y) of a named fielding position; mirrored for a
    left-hander, whose off side is +x."""
    if name not in POS:
        return None
    ang, dist = POS[name]
    if dist is None:                                # the keeper
        return (0.0, keeper_y)
    a = math.radians(ang)
    dx, dy = -math.sin(a), -math.cos(a)             # off side is -x
    if dist == "deep":
        bx, by = 0.0, STUMPS_Y * 0.5
        # walk out from near the middle to just inside the rope
        lo, hi = 0.0, 200.0
        for _ in range(30):
            mid = (lo + hi) / 2
            x, y = bx + dx * mid, by + dy * mid
            if (x / RX) ** 2 + (y / RY) ** 2 < 0.87:
                lo = mid
            else:
                hi = mid
        x, y = bx + dx * lo, by + dy * lo
    else:
        x, y = dx * dist, STUMPS_Y + dy * dist
    return (-x if left else x, y)


def named_position(detail):
    """The fielding position the feed text names (longest match)."""
    low = " %s " % (detail or "").lower()
    for k in sorted(POS, key=len, reverse=True):
        if k in ("bowler", "keeper") and k not in low:
            continue
        if k in low:
            return k
    return None


# ── Geometry ─────────────────────────────────────────────────────────────────
_FMT = GeomVertexFormat.getV3n3c4t2()


class Mesh:
    """Small procedural mesh builder: quads/triangles with normals, vertex
    colours and UVs, winding fixed up from the intended outward normal."""

    def __init__(self, name="mesh"):
        self.name = name
        self.vd = GeomVertexData(name, _FMT, Geom.UHStatic)
        self.vw = GeomVertexWriter(self.vd, "vertex")
        self.nw = GeomVertexWriter(self.vd, "normal")
        self.cw = GeomVertexWriter(self.vd, "color")
        self.tw = GeomVertexWriter(self.vd, "texcoord")
        self.prim = GeomTriangles(Geom.UHStatic)
        self.n = 0

    def v(self, p, n, c, uv=(0, 0)):
        self.vw.addData3(*p)
        self.nw.addData3(*n)
        self.cw.addData4(c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0)
        self.tw.addData2(*uv)
        self.n += 1
        return self.n - 1

    def quad(self, ps, c, uvs=((0, 0), (1, 0), (1, 1), (0, 1)), n=None):
        p = [np.array(q, float) for q in ps]
        cr = np.cross(p[1] - p[0], p[2] - p[0])
        if n is not None and np.dot(cr, n) < 0:
            ps, uvs = ps[::-1], uvs[::-1]
            cr = -cr
        nn = n if n is not None else cr / (np.linalg.norm(cr) + 1e-9)
        idx = [self.v(q, nn, c, u) for q, u in zip(ps, uvs)]
        self.prim.addVertices(idx[0], idx[1], idx[2])
        self.prim.addVertices(idx[0], idx[2], idx[3])

    def tri_raw(self, a, b, c):
        self.prim.addVertices(a, b, c)

    def node(self):
        g = Geom(self.vd)
        g.addPrimitive(self.prim)
        gn = GeomNode(self.name)
        gn.addGeom(g)
        return NodePath(gn)


def box(sx, sy, sz, c, center=(0, 0, 0)):
    m = Mesh("box")
    cx, cy, cz = center
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    m.quad([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], c, n=(0, 0, 1))
    m.quad([(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)], c, n=(0, 0, -1))
    m.quad([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], c, n=(0, -1, 0))
    m.quad([(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)], c, n=(0, 1, 0))
    m.quad([(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)], c, n=(-1, 0, 0))
    m.quad([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], c, n=(1, 0, 0))
    return m.node()


def limb(r0, r1, length, c, seg=10, sx=1.0):
    """Tapered cylinder hanging down -Z from the joint at the origin."""
    m = Mesh("limb")
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        p = [(r0 * math.cos(a0) * sx, r0 * math.sin(a0), 0),
             (r0 * math.cos(a1) * sx, r0 * math.sin(a1), 0),
             (r1 * math.cos(a1) * sx, r1 * math.sin(a1), -length),
             (r1 * math.cos(a0) * sx, r1 * math.sin(a0), -length)]
        am = (a0 + a1) / 2
        m.quad(p, c, n=(math.cos(am), math.sin(am), 0))
    return m.node()


def sphere(r, c, seg=12, rings=8, scale=(1, 1, 1), z0=-1.0):
    """UV sphere (or the part of it above z0*r, for caps and helmets)."""
    m = Mesh("sphere")
    sx, sy, sz = scale
    for j in range(rings):
        t0, t1 = math.pi * j / rings, math.pi * (j + 1) / rings
        if math.cos(t0) < z0 and math.cos(t1) < z0:
            continue
        for i in range(seg):
            a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
            pts = []
            for t, a in ((t0, a0), (t0, a1), (t1, a1), (t1, a0)):
                pts.append((r * math.sin(t) * math.cos(a) * sx,
                            r * math.sin(t) * math.sin(a) * sy,
                            r * math.cos(t) * sz))
            tm, am = (t0 + t1) / 2, (a0 + a1) / 2
            n = (math.sin(tm) * math.cos(am), math.sin(tm) * math.sin(am),
                 math.cos(tm))
            m.quad(pts, c, n=n)
    return m.node()


def make_tex(arr, name, mip=True, clamp=False):
    """numpy HxWx3/4 uint8 (row 0 = top) -> Panda texture."""
    arr = np.ascontiguousarray(arr[::-1])
    h, w, ch = arr.shape
    t = Texture(name)
    t.setup2dTexture(w, h, Texture.T_unsigned_byte,
                     Texture.F_rgba8 if ch == 4 else Texture.F_rgb8)
    t.setRamImageAs(arr.tobytes(), "RGBA" if ch == 4 else "RGB")
    if mip:
        t.setMinfilter(SamplerState.FT_linear_mipmap_linear)
        t.setAnisotropicDegree(8)
    else:
        t.setMinfilter(SamplerState.FT_linear)
    t.setMagfilter(SamplerState.FT_linear)
    if clamp:
        t.setWrapU(SamplerState.WM_clamp)
        t.setWrapV(SamplerState.WM_clamp)
    return t


def _smooth_noise(h, w, cells, rng):
    small = rng.random((cells, cells))
    ys = np.linspace(0, cells - 1.001, h)
    xs = np.linspace(0, cells - 1.001, w)
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    a = small[y0][:, x0]
    b = small[y0][:, x0 + 1]
    c = small[y0 + 1][:, x0]
    d = small[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


# ── Textures, generated ─────────────────────────────────────────────────────
GROUND_EXT = 1.24                       # ground mesh extends past the rope


_DETAIL = None


def detail_texture(n=256):
    """Tileable grass-blade grain, mean ~0.9, for a modulate layer."""
    global _DETAIL
    if _DETAIL is None:
        rng = np.random.default_rng(5)
        a = rng.random((n, n))
        # short vertical-ish blades: smear the noise along one axis
        a = (a + np.roll(a, 1, 0) + np.roll(a, 2, 0) + np.roll(a, 1, 1)) / 4
        v = 0.80 + 0.22 * a
        arr = np.clip(np.stack([v, v, v], -1) * 255, 0, 255).astype(np.uint8)
        _DETAIL = make_tex(arr, "detail")
    return _DETAIL


def grass_texture(size=2048):
    rng = np.random.default_rng(7)
    ex, ey = RX * GROUND_EXT, RY * GROUND_EXT
    xs = np.linspace(-ex, ex, size)[None, :]
    ys = np.linspace(ey, -ey, size)[:, None]          # row 0 = +y (top)
    base = np.array([64, 146, 60], float)   # brighter: the detail layer
                                            # modulates it back down ~10%
    img = np.ones((size, size, 3)) * base
    # mowing stripes across the pitch line, the TV look from the end camera
    stripe = (np.floor((ys + 1000) / 6.0) % 2) * 2 - 1
    img *= (1 + 0.075 * stripe)[..., None]
    # a second, fainter pattern along it, as on a match-day square
    stripe2 = (np.floor((xs + 1000) / 12.0) % 2) * 2 - 1
    img *= (1 + 0.025 * stripe2)[..., None]
    img *= (0.94 + 0.12 * _smooth_noise(size, size, 40, rng))[..., None]
    img += rng.normal(0, 3.0, (size, size, 1))
    # the square: lighter, drier grass around the pitches
    sq = (np.abs(xs) < 11) & (np.abs(ys) < 15)
    img[sq] = img[sq] * 1.05 + np.array([6, 4, -6])
    # outside the rope: a touch darker
    out = (xs / RX) ** 2 + (ys / RY) ** 2 > 1.0
    img[out] *= 0.9
    # 30-yard circle: two 27.43 m semicircles on the stumps, joined straight
    ay = np.abs(ys)
    d = np.where(ay <= STUMPS_Y, np.abs(xs) - 27.43,
                 np.sqrt(xs ** 2 + (ay - STUMPS_Y) ** 2) - 27.43)
    s = np.where(ay <= STUMPS_Y, ys,
                 27.43 * np.arctan2(ay - STUMPS_Y, np.abs(xs)))
    ring = (np.abs(d) < 0.13) & ((np.floor(s / 0.9) % 2) == 0)
    img[ring] = (235, 240, 235)
    return make_tex(np.clip(img, 0, 255).astype(np.uint8), "grass")


PITCH_HW, PITCH_HL = 1.9, 12.6          # pitch mesh half width / half length


def pitch_texture(w=192, h=1280):
    rng = np.random.default_rng(3)
    xs = np.linspace(-PITCH_HW, PITCH_HW, w)[None, :]
    ys = np.linspace(PITCH_HL, -PITCH_HL, h)[:, None]
    img = np.ones((h, w, 3)) * np.array([232, 206, 150], float)
    img *= (0.93 + 0.12 * _smooth_noise(h, w, 24, rng))[..., None]
    img += rng.normal(0, 4.0, (h, w, 1))
    # edges blend into the square's grass
    edge = np.clip((np.abs(xs) - 1.40) / 0.25, 0, 1)
    grass = np.array([64, 138, 58], float)
    img = img * (1 - edge[..., None]) + grass * edge[..., None]
    # worn patches on a good length and the bowlers' footmarks
    for cy in (4.5, -4.5):
        wear = np.exp(-(((ys - cy) / 2.6) ** 2 + (xs / 0.7) ** 2))
        img -= (wear * 22)[..., None]
    for sy in (1, -1):
        foot = np.exp(-(((ys - sy * 9.6) / 0.9) ** 2 + ((xs - 0.55 * sy) / 0.35) ** 2))
        img -= (foot * 30)[..., None]
    white = np.array([246, 246, 242], float)
    lw = 0.05

    def line(mask):
        img[mask] = white

    for sy in (1, -1):
        line((np.abs(ys - sy * CREASE_Y) < lw) & (np.abs(xs) < 1.83))
        line((np.abs(ys - sy * STUMPS_Y) < lw) & (np.abs(xs) < 1.32))
        for sx in (1, -1):
            yy = sy * ys
            line((np.abs(xs - sx * 1.32) < lw) & (yy > CREASE_Y - 0.02)
                 & (yy < STUMPS_Y + 1.22))
    return make_tex(np.clip(img, 0, 255).astype(np.uint8), "pitch")


def crowd_textures(w=1024, h=256, cols=None, seed=11):
    """The same seated crowd drawn three ways, so it can move:
        0 seated   1 shifting (heads turn, a few stand)   2 on their feet,
        arms up, flags out - for a four, a six, a wicket."""
    rng = np.random.default_rng(seed)
    palette = [np.array(c) * 255 for c in (cols or [])] + [
        np.array(c) for c in ((235, 235, 235), (30, 30, 34), (200, 40, 40),
                              (240, 200, 40), (70, 140, 220), (250, 140, 40),
                              (120, 60, 160), (40, 150, 90))]
    team = [np.array(c) * 255 for c in (cols or [])] or palette[:2]
    rows = 16
    rh = h / rows
    people = []
    for r in range(rows):
        y0 = int(r * rh)
        n = int(w / 7)
        xs = (np.arange(n) * 7 + rng.integers(0, 3, n)).clip(1, w - 7)
        for x in xs:
            if (x // 128) % 8 == 7 and x % 128 < 14:    # aisle
                continue
            if rng.random() < 0.06:                     # empty seat
                continue
            shirt = palette[rng.integers(0, len(palette))] \
                if rng.random() < 0.55 or not cols else \
                np.array(cols[rng.integers(0, len(cols))]) * 255
            skin = np.array(SKINS[rng.integers(0, len(SKINS))]) * 255
            people.append((int(x), y0, shirt, skin, rng.random(),
                           team[rng.integers(0, len(team))]))
    noise = rng.normal(0, 6, (h, w, 1))
    frames = []
    for frame in (0, 1, 2):
        img = np.zeros((h, w, 3), float) + np.array([44, 46, 58])
        for r in range(rows):
            y0 = int(r * rh)
            img[y0:y0 + 2] = (28, 28, 36)              # seat-row edge
        for x, y0, shirt, skin, k, flag in people:
            lift, dx = 0, 0
            if frame == 1:
                dx = 1 if k < 0.2 else (-1 if k > 0.8 else 0)
                lift = 3 if 0.45 < k < 0.53 else 0      # a few stand up
            elif frame == 2:
                lift = 3 if k < 0.75 else 1             # on their feet
            yb = y0 + int(rh * 0.42) - lift
            top = max(0, y0 + 3 - lift)
            img[max(0, yb):y0 + int(rh) - 1, x:x + 5] = shirt
            img[top:max(top, yb), x + 1 + dx:x + 4 + dx] = skin
            if frame == 2 and k < 0.7:                  # arms up
                img[max(0, top - 4):yb, x] = skin
                img[max(0, top - 4):yb, x + 4] = skin
                if k < 0.1:                             # waving a flag
                    img[max(0, top - 9):max(0, top - 4), x - 1:x + 6] = flag
        for x in range(0, w, 128):                      # aisle stairs
            img[:, x + 112:x + 126] = (88, 88, 96)
        img += noise
        frames.append(make_tex(np.clip(img, 0, 255).astype(np.uint8),
                               "crowd%d" % frame))
    return frames


def crowd_texture(w=1024, h=256, cols=None, seed=11):
    return crowd_textures(w, h, cols, seed)[0]


def text_texture(text, fg, bg, w=1024, h=64, font_size=44):
    """Text rendered with pygame into a texture (boards, shirt names)."""
    import pygame
    if not pygame.font.get_init():
        pygame.font.init()
    path = (pygame.font.match_font("franklingothicdemicond")
            or pygame.font.match_font("arialblack"))
    f = pygame.font.Font(path, font_size) if path else \
        pygame.font.SysFont("arial", font_size, bold=True)
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill(bg)
    img = f.render(text, True, fg)
    surf.blit(img, ((w - img.get_width()) // 2, (h - img.get_height()) // 2))
    arr = pygame.surfarray.array3d(surf).transpose(1, 0, 2)
    alpha = pygame.surfarray.array_alpha(surf).T[..., None]
    return make_tex(np.concatenate([arr, alpha], 2).astype(np.uint8),
                    "txt", mip=True)


# ── IK helpers ──────────────────────────────────────────────────────────────
def _aim_hp(d):
    """(h, p) that point a node's -Z axis along direction d."""
    x, y, z = d
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / n, y / n, z / n
    p = math.degrees(math.acos(max(-1.0, min(1.0, -z))))
    h = math.degrees(math.atan2(-x, y)) if abs(p) > 1e-3 else 0.0
    return h, p


def _two_bone(a, t, l1, l2, pole):
    """Middle joint position for a two-bone chain from a to target t."""
    a, t, pole = np.array(a), np.array(t), np.array(pole, float)
    d = t - a
    dist = float(np.linalg.norm(d)) or 1e-6
    dist_c = min(max(dist, abs(l1 - l2) + 1e-3), (l1 + l2) * 0.999)
    dn = d / dist
    x = (l1 * l1 + dist_c * dist_c - l2 * l2) / (2 * dist_c)
    hgt = math.sqrt(max(l1 * l1 - x * x, 0.0))
    pp = pole - dn * np.dot(pole, dn)
    pn = float(np.linalg.norm(pp))
    pp = pp / pn if pn > 1e-6 else np.array([0, 1.0, 0])
    return a + dn * x + pp * hgt, a + dn * dist_c


def lerp(a, b, u):
    return tuple(a[i] + (b[i] - a[i]) * u for i in range(len(a)))


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


# ── A player ────────────────────────────────────────────────────────────────
UA, FA, TH, SH = 0.29, 0.30, 0.45, 0.45


class Rig:
    """A jointed cricketer. Posed each frame with hips, spine, head and
    hand / foot targets in his own frame (facing +y); arms and legs are
    solved with two-bone IK so feet stay planted and hands reach."""

    def __init__(self, parent, shirt, trousers, skin, role="field",
                 name="", cap=None, whites=False, hair=None, build=None):
        self.role = role
        self.root = parent.attachNewNode("rig")
        self.hips = self.root.attachNewNode("hips")
        self.spine = self.hips.attachNewNode("spine")
        trousers = (0.93, 0.93, 0.90) if whites else trousers
        shirt = (0.95, 0.95, 0.92) if whites else shirt

        # Geometry hangs off a "geo" child of each joint, so it can be
        # flattened into one draw call per body part at the end.
        geo_nodes = {}
        keep = []       # hold the joint wrappers: a freed one's id() can be
                        # reused by a later joint, which once glued a leg pad
                        # onto a hand

        def G(joint):
            g = geo_nodes.get(id(joint))
            if g is None:
                g = joint.attachNewNode("geo")
                geo_nodes[id(joint)] = g
                keep.append(joint)
            return g

        def put(node, joint, pos=None, hpr=None, scale=None):
            node.reparentTo(G(joint))
            if pos:
                node.setPos(*pos)
            if hpr:
                node.setHpr(*hpr)
            if scale:
                node.setScale(*scale)
            return node

        trim = tuple(min(1.0, c * 0.55 + 0.45) for c in shirt) if not whites \
            else (0.20, 0.30, 0.60)
        dark = (0.10, 0.10, 0.12)
        hair = hair or (0.08, 0.06, 0.05)

        # hips: trousers seat and a belt
        put(box(0.34, 0.20, 0.20, trousers, (0, 0, -0.04)), self.hips)
        put(box(0.35, 0.21, 0.035, dark, (0, 0, 0.055)), self.hips)

        # torso: waist up to broad, rounded shoulders, with a collar
        t = limb(0.20, 0.15, 0.56, shirt, seg=14)
        put(t, self.spine, pos=(0, 0, 0.56), scale=(1, 0.62, 1))
        put(sphere(0.205, shirt, seg=14, scale=(1, 0.62, 0.35)), self.spine,
            pos=(0, 0, 0.56))
        put(limb(0.075, 0.08, 0.035, trim, 12), self.spine, pos=(0, 0, 0.64))

        self.neck = self.spine.attachNewNode("neck")
        self.neck.setZ(0.60)
        put(limb(0.05, 0.05, 0.08, skin, 10), self.neck, pos=(0, 0, 0.06))
        head = self.neck.attachNewNode("head")
        head.setZ(0.13)
        # face: skull, ears, eyes, brows, nose, mouth; hair back and sides
        put(sphere(0.105, skin, seg=14, rings=10, scale=(0.92, 1, 1.12)), head)
        for sx in (-1, 1):
            put(sphere(0.026, skin, seg=8, rings=6, scale=(0.5, 1, 1.3)), head,
                pos=(0.098 * sx, -0.005, 0.0))
            put(sphere(0.012, (0.06, 0.05, 0.05), seg=6, rings=4), head,
                pos=(0.036 * sx, 0.093, 0.022))
            put(box(0.032, 0.008, 0.008, hair), head,
                pos=(0.036 * sx, 0.096, 0.045))
        put(box(0.022, 0.03, 0.038, tuple(c * 0.92 for c in skin)), head,
            pos=(0, 0.105, -0.006))
        put(box(0.04, 0.008, 0.008, (0.45, 0.25, 0.22)), head,
            pos=(0, 0.098, -0.05))
        put(sphere(0.108, hair, seg=14, rings=10, scale=(0.95, 0.98, 1.05),
                   z0=-0.1), head, pos=(0, -0.012, 0.012))

        if role in ("bat",) or (role == "keeper" and cap == "helmet"):
            hc = cap if isinstance(cap, tuple) else (0.10, 0.12, 0.20)
            put(sphere(0.132, hc, seg=16, rings=10, scale=(1, 1.06, 0.95),
                       z0=-0.15), head, pos=(0, -0.005, 0.01))
            put(box(0.21, 0.08, 0.014, hc), head, pos=(0, 0.13, 0.055),
                hpr=(0, -12, 0))                                  # peak
            metal = (0.62, 0.64, 0.68)
            for gz in (-0.015, -0.055, -0.095):                  # grille
                put(box(0.18, 0.012, 0.012, metal), head, pos=(0, 0.135, gz))
            for gx in (-0.07, 0.0, 0.07):
                put(box(0.012, 0.012, 0.10, metal), head,
                    pos=(gx, 0.135, -0.055))
            put(box(0.13, 0.02, 0.06, hc), head, pos=(0, -0.12, -0.09))
        elif role == "ump":
            put(sphere(0.115, (0.95, 0.95, 0.92), scale=(1, 1, 0.7), z0=0.0),
                head, pos=(0, 0, 0.02))
            m = Mesh("brim")
            for i in range(20):
                a0, a1 = 2 * math.pi * i / 20, 2 * math.pi * (i + 1) / 20
                m.quad([(0.11 * math.cos(a0), 0.11 * math.sin(a0), 0.03),
                        (0.21 * math.cos(a0), 0.21 * math.sin(a0), 0.0),
                        (0.21 * math.cos(a1), 0.21 * math.sin(a1), 0.0),
                        (0.11 * math.cos(a1), 0.11 * math.sin(a1), 0.03)],
                       (0.95, 0.95, 0.92), n=(0, 0, 1))
            b = m.node()
            b.setTwoSided(True)
            put(b, head, pos=(0, 0, 0.02))
            put(limb(0.116, 0.116, 0.02, dark, 16), head, pos=(0, 0, 0.05))
        else:
            cc = cap or shirt
            put(sphere(0.113, cc, seg=14, scale=(1, 1, 0.8), z0=0.05), head,
                pos=(0, 0, 0.012))
            put(box(0.15, 0.12, 0.012, cc), head, pos=(0, 0.13, 0.035),
                hpr=(0, -10, 0))
            put(sphere(0.012, trim, seg=6, rings=4), head, pos=(0, 0, 0.105))

        self.sh, self.el, self.hp, self.kn, self.an = [], [], [], [], []
        if role == "bat":
            glove = (0.95, 0.95, 0.95)
        elif role == "keeper":
            glove = (0.92, 0.92, 0.88)
        else:
            glove = None
        for s in (-1, 1):
            sh = self.spine.attachNewNode("sh")
            sh.setPos(0.22 * s, 0, 0.50)
            put(sphere(0.068, shirt, seg=10, rings=8), sh)       # shoulder
            put(limb(0.058, 0.050, UA * 0.62, shirt, 12), sh)     # sleeve
            put(limb(0.052, 0.052, 0.03, trim, 12), sh,
                pos=(0, 0, -UA * 0.58))                          # cuff trim
            put(limb(0.046, 0.044, UA * 0.40,
                     skin if not whites else shirt, 12), sh,
                pos=(0, 0, -UA * 0.6))
            el = sh.attachNewNode("el")
            el.setZ(-UA)
            put(sphere(0.045, skin if not whites else shirt, seg=8, rings=6),
                el)
            put(limb(0.044, 0.034, FA - 0.06, skin if not whites else shirt,
                     12), el)
            hand = el.attachNewNode("hand")
            hand.setZ(-(FA - 0.03))
            if glove and role == "keeper":
                put(box(0.13, 0.07, 0.15, glove), hand, pos=(0, 0.01, -0.02))
                put(box(0.13, 0.02, 0.05, (0.85, 0.35, 0.10)), hand,
                    pos=(0, 0.045, 0.04))
            elif glove:
                put(box(0.09, 0.075, 0.11, glove), hand, pos=(0, 0, -0.01))
                put(limb(0.05, 0.05, 0.04, shirt, 10), hand, pos=(0, 0, 0.06))
                for fz in (-0.03, -0.055):
                    put(box(0.092, 0.08, 0.006, (0.75, 0.75, 0.78)), hand,
                        pos=(0, 0, fz))
            else:
                put(sphere(0.042, skin, seg=8, rings=6, scale=(0.8, 1, 1.15)),
                    hand)
            self.sh.append(sh)
            self.el.append(el)

            hp = self.hips.attachNewNode("hp")
            hp.setPos(0.10 * s, 0, -0.06)
            put(limb(0.088, 0.065, TH, trousers, 12), hp)
            kn = hp.attachNewNode("kn")
            kn.setZ(-TH)
            if role == "bat":
                pad = (0.96, 0.96, 0.94)
                put(box(0.14, 0.11, 0.48, pad, (0, 0.025, -0.25)), kn)
                put(box(0.15, 0.12, 0.07, pad, (0, 0.03, 0.0)), kn)   # roll
                for rx in (-0.042, 0.0, 0.042):                       # ridges
                    put(box(0.02, 0.016, 0.42, (0.88, 0.88, 0.86),
                            (rx, 0.087, -0.25)), kn)
                for sz in (-0.10, -0.32):                             # straps
                    put(box(0.145, 0.115, 0.022, dark, (0, 0.025, sz)), kn)
            elif role == "keeper":
                # keepers wear slim pads - full batting pads stuck out like
                # slabs when he crouched
                pad = (0.94, 0.94, 0.92)
                put(box(0.11, 0.08, 0.40, pad, (0, 0.02, -0.25)), kn)
                put(box(0.115, 0.085, 0.02, dark, (0, 0.02, -0.15)), kn)
            else:
                put(sphere(0.066, trousers, seg=10, rings=6), kn)    # knee
                put(limb(0.064, 0.046, SH, trousers, 12), kn)
            an = kn.attachNewNode("an")
            an.setZ(-SH)
            shoe = (0.94, 0.94, 0.94)
            put(box(0.10, 0.25, 0.07, shoe, (0, 0.05, -0.03)), an)
            put(sphere(0.052, shoe, seg=8, rings=6, scale=(1, 1.1, 0.7)), an,
                pos=(0, 0.17, -0.035))
            put(box(0.108, 0.29, 0.022, dark, (0, 0.06, -0.068)), an)  # sole
            put(box(0.02, 0.12, 0.012, trim, (0.052 * s, 0.05, -0.02)), an)
            self.hp.append(hp)
            self.kn.append(kn)
            self.an.append(an)

        self.bat = None
        if role == "bat":
            self.bat = self.root.attachNewNode("bat")
            wood = (0.88, 0.76, 0.54)
            put(limb(0.017, 0.017, 0.28, (0.12, 0.12, 0.14), 10), self.bat)
            for gz in (-0.04, -0.12, -0.20):                       # grip
                put(limb(0.0185, 0.0185, 0.025, (0.75, 0.12, 0.12), 10),
                    self.bat, pos=(0, 0, gz))
            put(box(0.108, 0.04, 0.56, wood, (0, 0, -0.56)), self.bat)
            put(box(0.05, 0.03, 0.40, wood, (0, -0.032, -0.60)), self.bat)
            put(box(0.11, 0.042, 0.06, (0.12, 0.20, 0.55), (0, 0.001, -0.33)),
                self.bat)                                          # sticker
            put(box(0.11, 0.042, 0.03, dark, (0, 0, -0.83)), self.bat)  # toe

        for g in geo_nodes.values():
            g.flattenStrong()

        # surname across the back of the shirt
        if name:
            surname = name.split()[-1].upper()[:12]
            tx = text_texture(surname, (255, 255, 255) if not whites
                              else (30, 30, 40), (0, 0, 0, 0), 256, 64, 46)
            m = Mesh("nm")
            m.quad([(0.16, -0.135, 0.30), (-0.16, -0.135, 0.30),
                    (-0.16, -0.135, 0.46), (0.16, -0.135, 0.46)],
                   (1, 1, 1, 1), n=(0, -1, 0))
            nm = m.node()
            nm.setTexture(tx)
            nm.setTransparency(TransparencyAttrib.MAlpha)
            nm.setLightOff()
            nm.reparentTo(self.spine)

        # soft contact shadow
        self.shadow = parent.attachNewNode("shadow")
        sh_mesh = Mesh("shadow")
        sh_mesh.quad([(-0.6, -0.6, 0), (0.6, -0.6, 0), (0.6, 0.6, 0),
                      (-0.6, 0.6, 0)], (1, 1, 1, 1), n=(0, 0, 1))
        sn = sh_mesh.node()
        sn.reparentTo(self.shadow)
        sn.setTexture(_shadow_tex())
        sn.setTransparency(TransparencyAttrib.MAlpha)
        sn.setLightOff()
        sn.setDepthWrite(False)
        sn.setBin("fixed", 5)
        self.shadow.setZ(0.03)

        self.pos = [0.0, 0.0]
        self.h = 0.0
        self.left = False               # a left-hander / left-armer mirrors
        # height and build differ player to player, and stay the same for
        # the same player (seeded by his name)
        if build is None:
            seed = int(hashlib.md5((name or str(id(self))).encode())
                       .hexdigest(), 16)
            build = (0.94 + (seed % 1000) / 1000 * 0.12,
                     0.92 + (seed // 1000 % 1000) / 1000 * 0.16)
        self.height, girth = build
        self.root.setScale(girth * self.height, girth * self.height,
                           self.height)

    def place(self, x, y, h):
        self.pos = [x, y]
        self.h = h
        self.root.setPos(x, y, 0)
        self.root.setH(h)
        self.shadow.setPos(x + 0.25, y - 0.2, 0.03)

    def pose(self, P):
        m = -1.0 if self.left else 1.0
        hx, hy, hz = P.get("hips", (0, 0, 0.93))
        self.hips.setPos(hx * m, hy, hz)
        sh_, sp_, sr_ = P.get("spine", (0, 0, 0))
        self.spine.setHpr(sh_ * m, sp_, sr_ * m)
        self.neck.setHpr(P.get("head", 0) * m, P.get("head_p", 0), 0)
        feet = P.get("feet", ((-0.13, 0.0, 0.08), (0.13, 0.0, 0.08)))
        hands = P.get("hands", ((-0.26, 0.05, 0.88), (0.26, 0.05, 0.88)))
        if self.left:                       # mirror: swap sides, flip x
            feet = [(-f[0], f[1], f[2]) for f in feet[::-1]]
            hands = [(-q[0], q[1], q[2]) for q in hands[::-1]]
        R = self.root
        for i in range(2):
            a = self.hp[i].getPos(R)
            k, f = _two_bone((a.x, a.y, a.z), feet[i], TH, SH, (0, 1, 0.2))
            self.hp[i].setHpr(R, *_aim_hp(k - np.array((a.x, a.y, a.z))), 0)
            self.kn[i].setHpr(R, *_aim_hp(f - k), 0)
            self.an[i].setHpr(R, P.get("foot_h", 0), 0, 0)
            a = self.sh[i].getPos(R)
            pole = (0.4 * (1 if i else -1), -1.0, -0.5)
            e, w = _two_bone((a.x, a.y, a.z), hands[i], UA, FA, pole)
            self.sh[i].setHpr(R, *_aim_hp(e - np.array((a.x, a.y, a.z))), 0)
            self.el[i].setHpr(R, *_aim_hp(w - e), 0)
        if self.bat is not None:
            grip = P.get("grip")
            if grip is None:
                grip = hands[1]
            bd = P.get("bat", (0.1, 0.15, -1.0))
            if self.left:
                grip = (-grip[0], grip[1], grip[2])
                bd = (-bd[0], bd[1], bd[2])
            self.bat.setPos(*grip)
            self.bat.setHpr(*_aim_hp(bd), 0)
        sc = 1.0 - 0.25 * max(0.0, 0.93 - hz)
        self.shadow.setScale(sc)


_SHADOW = None


def _shadow_tex():
    global _SHADOW
    if _SHADOW is None:
        n = 64
        yy, xx = np.mgrid[0:n, 0:n]
        d = np.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5) / (n / 2)
        a = (np.clip(1 - d, 0, 1) ** 1.6 * 150).astype(np.uint8)
        arr = np.zeros((n, n, 4), np.uint8)
        arr[..., 3] = a
        _SHADOW = make_tex(arr, "shadow", clamp=True)
    return _SHADOW


# ── Poses (in the player's own frame, facing +y) ───────────────────────────
def pose_stand(t=0.0):
    b = 0.006 * math.sin(t * 2.0)
    return {"hips": (0, 0, 0.93 + b), "spine": (0, -2, 0),
            "feet": ((-0.13, 0.0, 0.08), (0.13, 0.0, 0.08)),
            "hands": ((-0.27, 0.02, 0.86), (0.27, 0.02, 0.86))}


def pose_ready(t=0.0, depth=1.0):
    """Fielder on his toes as the ball is bowled."""
    d = depth
    return {"hips": (0, -0.05 * d, 0.93 - 0.20 * d), "spine": (0, -22 * d, 0),
            "feet": ((-0.24, 0.05, 0.08), (0.24, 0.05, 0.08)),
            "hands": ((-0.22, 0.42 * d + 0.05, 0.62 + 0.25 * (1 - d)),
                      (0.22, 0.42 * d + 0.05, 0.62 + 0.25 * (1 - d))),
            "head_p": 10 * d}


def pose_keeper(u_up=0.0):
    """Crouched behind the stumps; u_up 0 -> 1 as he rises to take it."""
    u = smooth(u_up)
    return {"hips": (0, -0.05, 0.50 + 0.30 * u), "spine": (0, -30 + 15 * u, 0),
            "feet": ((-0.28, 0.05, 0.08), (0.28, 0.05, 0.08)),
            "hands": ((-0.10, 0.45, 0.55 + 0.35 * u),
                      (0.10, 0.45, 0.55 + 0.35 * u)),
            "head_p": 12}


def pose_run(phase, speed=6.0, carry=None):
    """Running cycle. phase in radians, speed m/s."""
    s = min(0.75, 0.22 + 0.075 * speed)
    lift = min(0.32, 0.10 + 0.03 * speed)
    feet = []
    hands = []
    for i, off in enumerate((0.0, math.pi)):
        ph = phase + off
        x = -0.12 if i == 0 else 0.12
        feet.append((x, s * math.sin(ph), 0.08 + lift * max(0.0, math.cos(ph))))
        hands.append((x * 2.1, -0.35 * s / 0.6 * math.sin(ph) + 0.08,
                      1.02 + 0.06 * math.cos(ph)))
    out = {"hips": (0, 0, 0.90 + 0.03 * math.cos(2 * phase)),
           "spine": (0, -6 - 1.2 * speed, 0), "feet": tuple(feet),
           "hands": tuple(hands[::-1])}
    if carry:
        out["hands"] = carry
    return out


def pose_celebrate(t):
    j = max(0.0, math.sin(t * 7)) * 0.15
    return {"hips": (0, 0, 0.93 + j), "spine": (0, 6, 0),
            "feet": ((-0.15, 0, 0.08 + j), (0.15, 0, 0.08 + j)),
            "hands": ((-0.32, 0.12, 2.05 + j), (0.32, 0.12, 2.05 + j)),
            "head_p": -12}


def pose_appeal(t):
    return {"hips": (0, 0.1, 0.93), "spine": (0, 8, 0),
            "feet": ((-0.2, 0.2, 0.08), (0.18, -0.2, 0.08)),
            "hands": ((-0.35, 0.45, 1.95), (0.35, 0.45, 1.95)),
            "head_p": -18}


def pose_dejected(t):
    return {"hips": (0, 0, 0.92), "spine": (0, -14, 0), "head_p": 28,
            "feet": ((-0.13, 0.0, 0.08), (0.13, 0.0, 0.08)),
            "hands": ((-0.2, 0.25, 1.0), (0.26, 0.05, 0.86))}


def pose_catch(u):
    """Arms up, cupped, for a catch at chest/head height."""
    return {"hips": (0, 0, 0.88), "spine": (0, -6, 0),
            "feet": ((-0.2, 0.05, 0.08), (0.2, 0.05, 0.08)),
            "hands": ((-0.08, 0.35, 1.25 + 0.35 * u),
                      (0.08, 0.35, 1.25 + 0.35 * u)), "head_p": -10}


def pose_throw(u):
    """Wind up and throw (right arm)."""
    a = math.radians(-60 + 300 * smooth(u))
    sh = (0.22, 0.0, 1.43)
    r = 0.6
    hand = (sh[0], sh[1] - r * math.sin(a), sh[2] - r * math.cos(a))
    return {"hips": (0, 0, 0.9), "spine": (-20 + 40 * u, -6, 0),
            "feet": ((-0.2, 0.35, 0.08), (0.2, -0.3, 0.08)),
            "hands": ((-0.3, 0.45, 1.5 - 0.4 * u), hand)}


def pose_dive(u, side=-1):
    """Full-length dive to one side."""
    k = smooth(u)
    return {"hips": (0.3 * side * k, 0, 0.93 - 0.75 * k),
            "spine": (0, -10, 70 * side * k),
            "feet": ((-0.2 + 0.3 * side * k, -0.1, 0.08 + 0.1 * k),
                     (0.2 + 0.3 * side * k, -0.1, 0.08 + 0.05 * k)),
            "hands": ((0.9 * side * k - 0.1, 0.3, 0.9 - 0.6 * k),
                      (0.9 * side * k + 0.1, 0.3, 0.9 - 0.6 * k))}


def pose_signal(kind, t):
    """Umpire signals: out (finger), four (wave), six (both arms up),
    wide (arms out), no ball (arm out), bye (hand up)."""
    base = pose_stand(t)
    if kind == "out":
        base["hands"] = ((-0.27, 0.02, 0.86), (0.18, 0.25, 2.05))
    elif kind == "six":
        base["hands"] = ((-0.25, 0.05, 2.1), (0.25, 0.05, 2.1))
    elif kind == "four":
        w = math.sin(t * 6) * 0.35
        base["hands"] = ((-0.27, 0.02, 0.86), (0.25 + w, 0.45, 1.45))
    elif kind == "wide":
        base["hands"] = ((-0.85, 0.0, 1.43), (0.85, 0.0, 1.43))
    elif kind == "noball":
        base["hands"] = ((-0.27, 0.02, 0.86), (0.85, 0.0, 1.43))
    return base


# Batting: stance, then the stroke. Coordinates in the batsman's frame -
# he faces +y (the off side for a right-hander); the bowler is at -x.
STANCE = {"hips": (0, 0, 0.86), "spine": (8, -14, 0), "head": 70,
          "feet": ((-0.22, 0.02, 0.08), (0.20, 0.0, 0.08)),
          "hands": ((0.02, 0.26, 0.80), (0.03, 0.24, 0.70)),
          "grip": (0.03, 0.25, 0.80), "bat": (0.25, 0.15, -1.0)}

# keyframes: (u, hips, spine, front foot, back foot, grip, bat direction)
STROKES = {
    "drive": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
               (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
              (0.30, 0.88, (12, -10, 0), (-0.22, 0.02), (0.20, 0.0),
               (0.18, 0.20, 1.12), (0.75, 0.10, 0.65)),
              (0.55, 0.80, (0, -24, 0), (-0.80, 0.18), (0.25, 0.0),
               (-0.30, 0.32, 0.72), (-0.25, 0.25, -0.95)),
              (1.0, 0.82, (-18, -12, 0), (-0.80, 0.18), (0.22, 0.05),
               (-0.30, 0.10, 1.40), (-0.45, -0.35, 0.82))],
    "defend": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
                (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
               (0.35, 0.87, (10, -12, 0), (-0.22, 0.02), (0.20, 0.0),
                (0.10, 0.22, 0.95), (0.55, 0.15, 0.6)),
               (0.6, 0.80, (0, -26, 0), (-0.62, 0.16), (0.22, 0.0),
                (-0.28, 0.30, 0.78), (-0.18, 0.12, -1.0)),
               (1.0, 0.80, (0, -26, 0), (-0.62, 0.16), (0.22, 0.0),
                (-0.28, 0.30, 0.78), (-0.18, 0.12, -1.0))],
    "pull": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
              (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
             (0.3, 0.90, (20, -6, 0), (-0.15, 0.0), (0.35, -0.10),
              (0.20, 0.15, 1.25), (0.6, 0.45, 0.65)),
             (0.55, 0.88, (-10, -4, 0), (-0.15, 0.0), (0.35, -0.10),
              (-0.05, 0.40, 1.05), (-0.20, 0.97, 0.05)),
             (1.0, 0.88, (-55, -4, 0), (-0.10, -0.05), (0.35, -0.1),
              (-0.05, -0.15, 1.30), (0.40, -0.70, 0.55))],
    "cut": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
             (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
            (0.3, 0.90, (14, -8, 0), (-0.20, 0.02), (0.30, 0.25),
             (0.15, 0.15, 1.30), (0.35, -0.2, 0.9)),
            (0.55, 0.86, (25, -12, 0), (-0.20, 0.02), (0.30, 0.30),
             (0.05, 0.50, 1.00), (0.25, 0.95, -0.15)),
            (1.0, 0.86, (35, -16, 0), (-0.20, 0.02), (0.30, 0.30),
             (-0.10, 0.55, 0.90), (-0.30, 0.85, -0.45))],
    "sweep": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
               (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
              (0.3, 0.62, (10, -18, 0), (-0.70, 0.15), (0.25, -0.05),
               (0.10, 0.25, 0.95), (0.55, 0.6, 0.55)),
              (0.55, 0.50, (-10, -26, 0), (-0.75, 0.18), (0.30, -0.10),
               (-0.30, 0.30, 0.50), (-0.95, 0.25, -0.15)),
              (1.0, 0.52, (-45, -20, 0), (-0.75, 0.18), (0.30, -0.10),
               (-0.20, -0.20, 0.75), (-0.20, -0.95, 0.20))],
    "slog": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
              (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
             (0.3, 0.90, (25, -8, 0), (-0.40, 0.10), (0.25, 0.0),
              (0.30, 0.10, 1.35), (0.65, 0.2, 0.75)),
             (0.55, 0.84, (-10, -16, 0), (-0.55, 0.18), (0.25, 0.0),
              (-0.25, 0.40, 0.85), (-0.80, 0.55, -0.20)),
             (1.0, 0.86, (-60, -4, 0), (-0.55, 0.18), (0.25, 0.0),
              (-0.10, -0.25, 1.55), (0.20, -0.60, 0.80))],
    "leave": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
               (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
              (0.5, 0.88, (10, -12, 0), (-0.40, 0.10), (0.20, 0.0),
               (0.05, 0.05, 1.55), (0.25, -0.25, 0.95)),
              (1.0, 0.88, (10, -12, 0), (-0.40, 0.10), (0.20, 0.0),
               (0.05, 0.05, 1.55), (0.25, -0.25, 0.95))],
    "duck": [(0.0, 0.86, (8, -14, 0), (-0.22, 0.02), (0.20, 0.0),
              (0.03, 0.25, 0.80), (0.25, 0.15, -1.0)),
             (0.5, 0.55, (0, -45, 0), (-0.30, 0.05), (0.25, 0.0),
              (0.10, 0.20, 0.70), (0.6, 0.1, 0.5)),
             (1.0, 0.70, (0, -30, 0), (-0.30, 0.05), (0.25, 0.0),
              (0.10, 0.20, 0.80), (0.5, 0.1, -0.6))],
}
STROKES["flick"] = [STROKES["drive"][0], STROKES["drive"][1],
                    (0.55, 0.82, (-25, -18, 0), (-0.60, 0.05), (0.22, 0.0),
                     (-0.25, 0.05, 0.75), (-0.35, -0.35, -0.85)),
                    (1.0, 0.84, (-50, -10, 0), (-0.60, 0.05), (0.22, 0.0),
                     (-0.05, -0.30, 1.25), (0.2, -0.75, 0.6))]
STROKES["hook"] = STROKES["pull"]
STROKES["push"] = STROKES["defend"]


def pose_stroke(name, u, swing_h=0.0):
    """Interpolated stroke at u in [0,1]; contact is at u = 0.55."""
    keys = STROKES.get(name) or STROKES["defend"]
    for k in range(len(keys) - 1):
        if keys[k][0] <= u <= keys[k + 1][0]:
            a, b = keys[k], keys[k + 1]
            v = smooth((u - a[0]) / max(1e-6, b[0] - a[0]))
            break
    else:
        a = b = keys[-1] if u > 1 else keys[0]
        v = 0.0
    hz = a[1] + (b[1] - a[1]) * v
    sp = lerp(a[2], b[2], v)
    ff = lerp(a[3], b[3], v)
    bf = lerp(a[4], b[4], v)
    grip = lerp(a[5], b[5], v)
    bd = lerp(a[6], b[6], v)
    if swing_h:                     # aim the stroke at the target
        c, s = math.cos(math.radians(swing_h)), math.sin(math.radians(swing_h))
        bd = (bd[0] * c - bd[1] * s, bd[0] * s + bd[1] * c, bd[2])
    hands = ((grip[0], grip[1], grip[2]),
             (grip[0] + bd[0] * 0.10, grip[1] + bd[1] * 0.10,
              grip[2] + bd[2] * 0.10))
    return {"hips": (0, 0, hz), "spine": (sp[0] + 8, sp[1], sp[2]),
            "head": 70 - max(0, -sp[0]) * 0.3,
            "feet": ((ff[0], ff[1], 0.08), (bf[0], bf[1], 0.08)),
            "hands": hands, "grip": grip, "bat": bd}


def pose_bowl(u, spinner=False):
    """Delivery stride: u 0 (gather/bound) -> 1 (follow-through)."""
    k = 0.6 if spinner else 1.0
    jump = (0.20 * k) * max(0.0, math.sin(min(1.0, u / 0.35) * math.pi))
    a = math.radians(70 + 270 * smooth((u - 0.25) / 0.6))
    sh = (0.22, 0.0, 1.43 + jump)
    r = 0.62
    bowl_hand = (sh[0] - 0.05, sh[1] - r * math.sin(a), sh[2] - r * math.cos(a))
    front = (-0.22, -0.05 + 0.5 * smooth((u - 0.1) / 0.4),
             2.05 - 1.1 * smooth((u - 0.3) / 0.4) + jump)
    ff_y = 0.15 + 0.75 * smooth((u - 0.2) / 0.3)
    return {"hips": (0, 0, 0.93 + jump - 0.06 * smooth((u - 0.5) / 0.3)),
            "spine": (25 - 40 * smooth((u - 0.4) / 0.4),
                      8 - 30 * smooth((u - 0.4) / 0.5), 0),
            "feet": ((-0.15, ff_y, 0.08 + jump * 0.8),
                     (0.15, -0.35 + 0.6 * smooth((u - 0.6) / 0.4),
                      0.08 + jump * 0.4)),
            "hands": (front, bowl_hand), "head_p": 0}


# ── The ball in flight ──────────────────────────────────────────────────────
class Arc:
    """Ballistic segment from p0 to p1 over T seconds (gravity in z)."""

    def __init__(self, p0, p1, T, g=G):
        self.p0, self.p1, self.T, self.g = np.array(p0, float), \
            np.array(p1, float), max(1e-3, T), g
        self.vz = (self.p1[2] - self.p0[2] + 0.5 * g * self.T ** 2) / self.T

    def at(self, t):
        u = t / self.T
        x = self.p0[0] + (self.p1[0] - self.p0[0]) * u
        y = self.p0[1] + (self.p1[1] - self.p0[1]) * u
        z = self.p0[2] + self.vz * t - 0.5 * self.g * t * t
        return (x, y, max(0.035, z))


class Roll:
    """Ball along the ground, decelerating, with a couple of small hops."""

    def __init__(self, p0, p1, T):
        self.p0, self.p1, self.T = np.array(p0, float), np.array(p1, float), \
            max(1e-3, T)

    def at(self, t):
        u = min(1.0, t / self.T)
        e = 1 - (1 - u) ** 2                     # decelerating
        x, y, _ = self.p0 + (self.p1 - self.p0) * e
        hop = 0.25 * abs(math.sin(u * math.pi * 3)) * (1 - u)
        return (x, y, 0.035 + hop)


# ── One delivery, start to finish ───────────────────────────────────────────
class Play:
    """Deterministic timeline for a delivery. Every actor's state is a pure
    function of the play clock, which is what makes slow-motion replays and
    'catch up with the voice' speed changes free."""

    def __init__(self, world, info, seed):
        self.w = world
        self.info = info
        rnd = random.Random(seed)
        self.rnd = rnd
        self.left = bool(info.get("bat_left"))
        self.spinner = bool(info.get("spinner"))
        self.bowl_left = bool(info.get("bowl_left"))
        kind, act = info.get("kind"), info.get("act")
        self.kind, self.act = kind, act
        self.runs = int(info.get("runs") or 0)
        m = -1.0 if self.left else 1.0             # off side sign: -x * m

        # run-up
        self.ru_len = 6.5 if self.spinner else 20.0
        self.ru_speed = 4.2 if self.spinner else 7.4
        self.T_ru = self.ru_len / self.ru_speed
        self.T_bound = 0.55
        self.T_rel = self.T_ru + self.T_bound * 0.62
        bx = (-0.45 if self.bowl_left else 0.45)
        self.bowl_x = bx
        release = (bx * 1.05, -CREASE_Y - 0.25, 2.05 if self.spinner else 2.25)

        L = info.get("length") or rnd.choice(["good", "good", "full", "backlen"])
        bounce_y = {"yorker": 8.15, "halfvolley": 6.4, "fulltoss": None,
                    "full": 5.3, "good": 3.2, "backlen": 1.4, "short": -0.6,
                    "bouncer": -2.2}.get(L, 3.2)
        arrive_z = {"yorker": 0.10, "halfvolley": 0.28, "fulltoss": 0.75,
                    "full": 0.38, "good": 0.72, "backlen": 1.0, "short": 1.25,
                    "bouncer": 1.75}.get(L, 0.7)
        line = info.get("line")
        lx = {"wide": -0.95, "outoff": -0.32, "offstump": -0.11, "middle": 0.0,
              "legstump": 0.11, "pads": 0.20}.get(line, -0.18) * m
        if kind == "wide":
            lx = -1.05 * m
        self.contact = (lx + 0.25 * m * 0, CREASE_Y - 0.15, arrive_z)
        speed = 22.0 if self.spinner else 37.0
        segs = []
        if bounce_y is None:
            d = self.contact[1] - release[1]
            segs.append(Arc(release, self.contact, d / speed))
        else:
            bp = (lx * 0.9, bounce_y, 0.035)
            d1 = bp[1] - release[1]
            d2 = self.contact[1] - bp[1]
            segs.append(Arc(release, bp, d1 / speed))
            # spin turns off the pitch; seam nips
            mv = info.get("move")
            dx = {"turn": 0.25, "seam": 0.08, "in": 0.12, "away": -0.12}.get(mv, 0)
            if mv == "turn" and self.bowl_left == (not self.left):
                dx = -dx
            c2 = (self.contact[0] + dx * m, self.contact[1], self.contact[2])
            self.contact = c2
            segs.append(Arc(bp, c2, d2 / (speed * 0.88)))
        self.T_contact = self.T_rel + sum(s.T for s in segs)
        self.ball_segs = [(self.T_rel, s) for s in segs]

        # the stroke
        stroke = {"drive": "drive", "pull": "pull", "hook": "pull",
                  "cut": "cut", "sweep": "sweep", "flick": "flick",
                  "slog": "slog", "defend": "defend", "leave": "leave",
                  "ducks": "duck", "edge": "drive", "beaten": "drive",
                  "dance": "drive", "appeal": "defend"}.get(act)
        tname = named_position(info.get("detail"))
        self.tname = tname
        kp_y = 11.2 if self.spinner else 19.5
        self.keeper_y = kp_y
        target = pos_xy(tname, self.left, kp_y) if tname else None
        if stroke is None:
            stroke = self._stroke_for(kind, target)
        if kind == "six" and stroke in ("defend", "drive", "push"):
            stroke = "slog" if (target and target[0] * m > 0) else "drive"
        self.stroke = stroke
        self.advance = 1.4 if act == "dance" else 0.0

        # where the ball goes after the bat
        self.after = []
        self.events = []                # (t, kind, data)
        self.chaser = None
        self.catcher = None
        T = self.T_contact
        c = self.contact
        how = info.get("how") or "other"
        self.how = how if kind == "wicket" else None
        self.runs_T0 = None

        def add(seg):
            self.after.append((T + sum(s.T for _, s in self.after), seg))

        if kind == "wicket" and how in ("bowled",):
            stumps = (0.0, STUMPS_Y, 0.45)
            add(Arc(c, stumps, 0.12))
            self.events.append((T + 0.12, "stumps", STUMPS_Y))
            add(Roll(stumps, (0.4 * m, STUMPS_Y + 6, 0.035), 1.6))
        elif kind == "wicket" and how == "lbw":
            pad = (c[0] + 0.15 * m, CREASE_Y + 0.1, 0.45)
            add(Arc(c, pad, 0.05))
            add(Roll(pad, (pad[0] - 1.5 * m, pad[1] - 2.5, 0.035), 1.0))
            self.events.append((T + 0.3, "appeal", None))
        elif kind == "wicket" and how in ("caught", "caught_nofield", "other"):
            if target is None:
                # no position named: an edge goes to the cordon (or the
                # keeper), anything else to someone actually in the field
                field = list(self.w.field_names)
                if act in ("edge", None):
                    tname = next((n for n in field if "slip" in n), "keeper")
                else:
                    tname = rnd.choice(field)
                target = pos_xy(tname, self.left, kp_y)
            self.catch_name = tname
            hi = 9.0 if math.hypot(target[0], target[1] - c[1]) > 30 else 2.5
            dist = math.hypot(target[0] - c[0], target[1] - c[1])
            add(Arc(c, (target[0], target[1] + 0.4, 1.55),
                    max(0.35, min(3.2, dist / 18.0 + hi / 6.0))))
            self.catcher = tname
            self.events.append((T + self.after[-1][1].T, "caught", tname))
        elif kind == "wicket" and how == "stumped":
            kp = (0.0, kp_y - 0.4, 0.6)
            add(Arc(c, kp, 0.25 if self.spinner else 0.45))
            self.events.append((T + 0.55, "stumps", STUMPS_Y))
            self.advance = 1.6
        elif kind == "wicket" and how == "runout":
            tgt = target or pos_xy("cover", self.left, kp_y)
            add(Roll(c, (tgt[0], tgt[1], 0.035), 1.4))
            self.chaser = tname or "cover"
            add(Arc((tgt[0], tgt[1], 1.5), (0.0, -STUMPS_Y, 0.4), 0.7))
            self.events.append((T + 2.1, "stumps", -STUMPS_Y))
            self.runs_T0 = T + 0.3
        elif kind in ("six",):
            if target is None:
                ang = rnd.uniform(-60, 60)
                a = math.radians(ang)
                target = (-math.sin(a) * 40 * m, -math.cos(a) * 40 + 5)
            dx, dy = target[0], target[1]
            sc = rope_scale(dx, dy) * 1.22
            land = (dx * sc, dy * sc, 7.5)
            add(Arc(c, land, 3.1, g=G * 0.9))
            self.events.append((T + 3.1, "crowd", land))
        elif kind == "four":
            if target is None:
                ang = rnd.uniform(-110, 120)
                a = math.radians(ang)
                target = (-math.sin(a) * 40 * m, -math.cos(a) * 40 + 8)
            dx, dy = target[0], target[1] - 3
            sc = rope_scale(dx, dy) * 1.01
            rope = (dx * sc, dy * sc + 3, 0.035)
            if info.get("air"):
                add(Arc(c, rope, 2.2))
            else:
                add(Roll(c, rope, 2.4))
            self.chaser = self._nearest(rope)
            self.events.append((T + self.after[-1][1].T, "rope", rope))
        elif kind in ("one", "two", "three", "runs", "bye", "legbye") or \
                (kind == "noball" and self.runs > 1):
            if target is None:
                tname = rnd.choice(["cover", "mid-wicket", "point", "square leg",
                                    "mid-on", "mid-off"])
                target = pos_xy(tname, self.left, kp_y)
            if kind in ("bye", "legbye"):
                target = (rnd.uniform(-6, 6), kp_y + rnd.uniform(4, 14))
            dist = math.hypot(target[0] - c[0], target[1] - c[1])
            stop = (target[0], target[1], 0.035)
            T_roll = max(0.9, dist / 16.0)
            add(Roll(c, stop, T_roll))
            self.chaser = tname or self._nearest(stop)
            throw_to = (0.0, kp_y - 0.5, 1.0) if self.runs % 2 else \
                (0.4, -STUMPS_Y - 0.6, 1.0)
            add(Arc((stop[0], stop[1], 1.6), throw_to,
                    max(0.6, math.hypot(stop[0], stop[1] - throw_to[1]) / 28)))
            self.runs_T0 = T + 0.35
        elif kind == "wide":
            kp = (c[0] * 0.8, kp_y - 0.3, 0.8)
            add(Arc(c, kp, 0.3))
        else:                                       # dot / leave / beaten
            if act in ("leave", "beaten", "ducks") or kind == "wicket":
                kp = (c[0] * 0.6, kp_y - 0.3, 0.9)
                add(Arc(c, kp, 0.28 if self.spinner else 0.42))
            elif act == "edge":
                kp = (-0.8 * m, kp_y - 0.2, 0.7)
                add(Arc(c, kp, 0.4))
            else:
                if target is None:
                    tname = rnd.choice(["cover", "mid-off", "mid-on", "point",
                                        "bowler"])
                    target = pos_xy(tname, self.left, kp_y)
                tx, ty = target
                d = math.hypot(tx - c[0], ty - c[1])
                k = min(1.0, 14.0 / max(d, 1.0))
                stop = (c[0] + (tx - c[0]) * k, c[1] + (ty - c[1]) * k, 0.035)
                add(Roll(c, stop, 1.4))
                if d < 26:
                    self.chaser = tname
        self.target = target
        self.swing_h = self._swing_for(target)
        end = T + sum(s.T for _, s in self.after)
        if self.runs_T0 is not None:
            n = max(1, min(4, self.runs if kind not in ("bye", "legbye") else 1))
            self.n_runs = n
            end = max(end, self.runs_T0 + n * 2.9)
        else:
            self.n_runs = 0
        self.T_end = end + 2.2

    def _stroke_for(self, kind, target):
        if kind in ("dot", "wide"):
            return self.rnd.choice(["defend", "leave", "defend"])
        if target is None:
            return {"six": "slog", "four": "drive"}.get(kind, "defend")
        m = -1.0 if self.left else 1.0
        x, y = target[0] * m, target[1]
        ang = math.degrees(math.atan2(-x, -(y - STUMPS_Y)))   # + off side
        if -35 < ang < 35:
            return "drive"
        if ang >= 35:
            return "cut" if ang > 75 else "drive"
        return "pull" if ang < -70 else "flick"

    def _swing_for(self, target):
        """Rotate the stroke towards the target (degrees, batsman frame)."""
        if not target:
            return 0.0
        m = -1.0 if self.left else 1.0
        x, y = target[0] * m, target[1]
        ang = math.degrees(math.atan2(-x, -(y - STUMPS_Y)))
        if self.stroke in ("drive", "defend"):
            return max(-30, min(45, ang * 0.5))
        return 0.0

    def _nearest(self, p):
        best, bd = None, 1e9
        for name in self.w.field_names:
            q = pos_xy(name, self.left, self.keeper_y)
            if q is None:
                continue
            d = math.hypot(q[0] - p[0], q[1] - p[1])
            if d < bd:
                best, bd = name, d
        return best

    # -- state at time t -------------------------------------------------
    def ball(self, t):
        if t < self.T_rel:
            return None
        for t0, seg in reversed(self.ball_segs):
            if t >= t0 and t < t0 + seg.T:
                return seg.at(t - t0)
        if t < self.T_contact:
            return self.ball_segs[-1][1].at(self.ball_segs[-1][1].T)
        for t0, seg in self.after:
            if t0 <= t < t0 + seg.T:
                return seg.at(t - t0)
        if self.after:
            t0, seg = self.after[-1]
            if self.kind == "six":
                return seg.at(seg.T)
            return None if t > t0 + seg.T + 0.6 else seg.at(seg.T)
        return None


# ── The world ───────────────────────────────────────────────────────────────
class World:
    """Builds the stadium, holds the players, runs plays and cameras."""

    def __init__(self, base, band=(180, 532), size=(1280, 720)):
        self.base = base
        self.render = base.render
        self.size = size
        self.band = band
        self.t = 0.0
        self.play = None
        self.play_t = 0.0
        self.play_speed = 1.0
        self.result_seen = False
        self.replay = None              # (play, t, speed, shot)
        self.replaying = False
        self.phase = "middle"
        self.fmt = "ODI"
        self.field_names = list(FIELD_SETS["middle"])
        self.bat_code = self.bowl_code = ""
        self.names = {}
        self.left_bat = False
        self.spinner = False
        self.stumps_hit = {}            # end y -> time
        self.signal = None              # (kind, t)
        self.celebrate = None           # t
        self.shot = None
        self.shot_t0 = 0.0
        self.idle_next = 0.0
        self._focus = "bowler"
        self.crowd_buzz = 0.0
        self.trail = []
        self._trail_np = None

        self._setup_render()
        self._build_ground()
        self._build_stadium()
        self.ball_np = sphere(0.055, (0.97, 0.97, 0.95), seg=12, rings=8)
        self.ball_np.reparentTo(self.render)
        self.ball_np.setLightOff()
        self.ball_np.hide()
        self.rigs = {}
        self.bails = {}
        self._build_stumps()
        self.set_teams("", "", "ODI")

    # -- setup -----------------------------------------------------------
    def _setup_render(self):
        b = self.base
        b.setBackgroundColor(0.55, 0.72, 0.92, 1)
        b.disableMouse()
        self.render.setShaderAuto()
        amb = AmbientLight("amb")
        amb.setColor((0.52, 0.53, 0.58, 1))
        self.render.setLight(self.render.attachNewNode(amb))
        sun = DirectionalLight("sun")
        sun.setColor((0.88, 0.85, 0.78, 1))
        sn = self.render.attachNewNode(sun)
        sn.setHpr(-35, -58, 0)
        self.render.setLight(sn)
        fill = DirectionalLight("fill")
        fill.setColor((0.22, 0.24, 0.30, 1))
        fn = self.render.attachNewNode(fill)
        fn.setHpr(150, -30, 0)
        self.render.setLight(fn)
        fog = Fog("haze")
        fog.setColor(0.62, 0.74, 0.90)
        fog.setExpDensity(0.0011)
        self.render.setFog(fog)
        self.lens = b.camLens
        self.lens.setNearFar(0.3, 4000)
        # sky dome
        m = Mesh("sky")
        rings, seg, R = 12, 32, 2200.0
        for j in range(rings):
            t0, t1 = math.pi / 2 * j / rings, math.pi / 2 * (j + 1) / rings
            for i in range(seg):
                a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
                pts, cols = [], []
                for t, a in ((t0, a0), (t0, a1), (t1, a1), (t1, a0)):
                    pts.append((R * math.cos(t) * math.cos(a),
                                R * math.cos(t) * math.sin(a),
                                R * math.sin(t) - 200))
                u = (t0 + t1) / math.pi
                col = lerp((0.78, 0.86, 0.95), (0.30, 0.52, 0.86), u)
                m.quad(pts, col, n=(0, 0, -1))
        sky = m.node()
        sky.setTwoSided(True)
        sky.setLightOff()
        sky.setFogOff()
        sky.setBin("background", 0)
        sky.setDepthWrite(False)
        sky.reparentTo(self.render)

    def _ellipse_ring(self, s0, z0, s1, z1, c, seg=120, tex=None, tiles=1.0,
                      cut=None):
        """Band between two scaled rope ellipses - stands, roof, boards."""
        m = Mesh("ring")
        for i in range(seg):
            a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
            am = (a0 + a1) / 2
            if cut and cut(am):
                continue
            p = [(RX * s0 * math.cos(a0), RY * s0 * math.sin(a0), z0),
                 (RX * s0 * math.cos(a1), RY * s0 * math.sin(a1), z0),
                 (RX * s1 * math.cos(a1), RY * s1 * math.sin(a1), z1),
                 (RX * s1 * math.cos(a0), RY * s1 * math.sin(a0), z1)]
            n = (-math.cos(am) * (z1 - z0), -math.sin(am) * (z1 - z0),
                 (s1 - s0) * RX + 1e-3)
            u0, u1 = i / seg * tiles, (i + 1) / seg * tiles
            m.quad(p, c, uvs=((u0, 0), (u1, 0), (u1, 1), (u0, 1)), n=n)
        np_ = m.node()
        np_.setTwoSided(True)
        if tex:
            np_.setTexture(tex)
        np_.reparentTo(self.render)
        return np_

    def _build_ground(self):
        m = Mesh("ground")
        seg = 160
        ex, ey = RX * GROUND_EXT, RY * GROUND_EXT
        c = m.v((0, 0, 0), (0, 0, 1), (1, 1, 1), (0.5, 0.5))
        ring = []
        for i in range(seg + 1):
            a = 2 * math.pi * i / seg
            x, y = ex * math.cos(a), ey * math.sin(a)
            ring.append(m.v((x, y, 0), (0, 0, 1), (1, 1, 1),
                            (x / (2 * ex) + 0.5, y / (2 * ey) + 0.5)))
        for i in range(seg):
            m.tri_raw(c, ring[i], ring[i + 1])
        g = m.node()
        g.setTexture(grass_texture())
        g.reparentTo(self.render)
        # fine detail for close-ups: the ground texture is ~8 cm a pixel,
        # so a tiled grass-blade layer modulates it at ~5 mm. Mip-mapped,
        # it averages to flat at distance - no shimmer on wide shots.
        det = detail_texture()
        ts = TextureStage("detail")
        ts.setMode(TextureStage.MModulate)
        g.setTexture(ts, det)
        g.setTexScale(ts, 140, 140)

        pm = Mesh("pitch")
        pm.quad([(-PITCH_HW, -PITCH_HL, 0.012), (PITCH_HW, -PITCH_HL, 0.012),
                 (PITCH_HW, PITCH_HL, 0.012), (-PITCH_HW, PITCH_HL, 0.012)],
                (1, 1, 1), uvs=((0, 0), (1, 0), (1, 1), (0, 1)), n=(0, 0, 1))
        p = pm.node()
        p.setTexture(pitch_texture())
        p.reparentTo(self.render)
        pts = TextureStage("pdetail")
        pts.setMode(TextureStage.MModulate)
        p.setTexture(pts, detail_texture())
        p.setTexScale(pts, 8, 50)

        # boundary rope: a white tube on the ellipse
        rm = Mesh("rope")
        seg, r = 240, 0.07
        for i in range(seg):
            for k in range(6):
                pts = []
                for (ii, kk) in ((i, k), (i + 1, k), (i + 1, k + 1), (i, k + 1)):
                    a = 2 * math.pi * ii / seg
                    b = 2 * math.pi * kk / 6
                    cx, cy = RX * math.cos(a), RY * math.sin(a)
                    nx, ny = math.cos(a), math.sin(a)
                    pts.append((cx + nx * r * math.cos(b),
                                cy + ny * r * math.cos(b),
                                0.07 + r * math.sin(b)))
                rm.quad(pts, (0.97, 0.97, 0.97))
        rope = rm.node()
        rope.setTwoSided(True)
        rope.reparentTo(self.render)

    def _build_stadium(self):
        cols = [team_rgb("IND"), team_rgb("AUS")]
        self.crowd_frames = crowd_textures(cols=cols)
        self.crowd_tex = self.crowd_frames[0]
        self._crowd_frame = 0
        self._crowd_next = 0.0
        self.crowd_ts = TextureStage.getDefault()
        # advertising boards just beyond the rope
        boards = text_texture("LIVE CRICKET   •   HINDI COMMENTARY   •   "
                              "LIVE CRICKET   •   HINDI COMMENTARY",
                              (255, 255, 255), (20, 60, 160, 255), 2048, 64, 40)
        # negative tiles: the ring runs anticlockwise, which reads as mirror
        # writing from inside the ground
        self._ellipse_ring(1.04, 0.0, 1.045, 1.0, (1, 1, 1), tex=boards,
                           tiles=-8).setLightOff()
        # tier 1: front wall, then seating rising back
        self._ellipse_ring(1.10, 0.0, 1.10, 1.6, (0.20, 0.22, 0.30))
        self.tier1 = self._ellipse_ring(1.10, 1.6, 1.33, 13.0, (1, 1, 1),
                                        tex=self.crowd_tex, tiles=36)
        # concourse band with lights
        self._ellipse_ring(1.33, 13.0, 1.33, 16.0, (0.16, 0.17, 0.24))
        self._ellipse_ring(1.33, 15.6, 1.335, 15.9, (1.0, 0.95, 0.75)
                           ).setLightOff()
        self.tier2 = self._ellipse_ring(1.33, 16.0, 1.58, 33.0, (1, 1, 1),
                                        tex=self.crowd_tex, tiles=44)
        self._ellipse_ring(1.58, 33.0, 1.58, 38.0, (0.30, 0.32, 0.40))
        # roof canopy
        self._ellipse_ring(1.42, 39.5, 1.62, 38.0, (0.82, 0.84, 0.88))
        # sight screens: black behind a white ball, white behind a red one
        self.screens = []
        for sy in (-1, 1):
            s = box(26, 1.2, 9.5, (1, 1, 1), (0, sy * RY * 1.085, 4.75))
            s.reparentTo(self.render)
            self.screens.append(s)
        # floodlight towers
        for ang in (40, 140, 220, 320):
            a = math.radians(ang)
            x, y = RX * 1.75 * math.cos(a), RY * 1.75 * math.sin(a)
            t = limb(0.9, 1.3, 62, (0.55, 0.56, 0.60), 10)
            t.setPos(x, y, 62)
            t.reparentTo(self.render)
            head = box(14, 2.0, 9, (0.2, 0.2, 0.24), (0, 0, 0))
            head.setPos(x, y, 66)
            head.lookAt(0, 0, 0)
            head.reparentTo(self.render)
            lamps = box(13, 0.3, 8, (1.0, 0.98, 0.88), (0, 1.1, 0))
            lamps.reparentTo(head)
            lamps.setLightOff()

    def _build_stumps(self):
        self.stumps = {}
        for ey in (STUMPS_Y, -STUMPS_Y):
            grp = self.render.attachNewNode("stumps")
            sticks = []
            for sx in (-0.114, 0.0, 0.114):
                s = limb(0.019, 0.019, 0.71, (0.96, 0.95, 0.90), 8)
                s.setPos(sx, ey, 0.71)
                s.reparentTo(grp)
                sticks.append(s)
            bails = []
            for bx in (-0.057, 0.057):
                b = box(0.11, 0.025, 0.025, (0.96, 0.95, 0.9))
                b.setPos(bx, ey, 0.725)
                b.reparentTo(grp)
                bails.append(b)
            self.stumps[ey] = (sticks, bails)

    # -- people ----------------------------------------------------------
    def set_teams(self, bat_code, bowl_code, fmt):
        """(Re)dress everyone: called on a new match or innings."""
        whites = str(fmt).upper() in ("TEST", "FIRST-CLASS")
        if (bat_code, bowl_code, whites) == getattr(self, "_dress", None):
            return
        self._dress = (bat_code, bowl_code, whites)
        self.fmt = fmt
        for r in self.rigs.values():
            r.root.removeNode()
            r.shadow.removeNode()
        self.rigs = {}
        bat = team_rgb(bat_code)
        bowl = team_rgb(bowl_code)
        dark = lambda c: tuple(x * 0.45 for x in c)
        self.whites = whites
        rng = random.Random(hash((bat_code, bowl_code)) & 0xffff)
        for nm in ("striker", "nonstriker"):
            self.rigs[nm] = Rig(self.render, bat, dark(bat), SKINS[1], "bat",
                                cap=dark(bat), whites=whites)
        self.rigs["bowler"] = Rig(self.render, bowl, dark(bowl), SKINS[2],
                                  "field", cap=dark(bowl), whites=whites)
        kskin = SKINS[rng.randrange(len(SKINS))]
        self.rigs["keeper"] = Rig(self.render, bowl, dark(bowl), kskin,
                                  "keeper", cap=dark(bowl), whites=whites)
        self.rigs["keeper"]._skin = kskin
        for i in range(9):
            self.rigs["f%d" % i] = Rig(self.render, bowl, dark(bowl),
                                       SKINS[rng.randrange(len(SKINS))],
                                       "field", cap=dark(bowl), whites=whites)
        for nm in ("ump1", "ump2"):
            self.rigs[nm] = Rig(self.render, (0.95, 0.95, 0.95),
                                (0.12, 0.12, 0.15), SKINS[rng.randrange(5)],
                                "ump")
        for s in self.screens:
            s.setColor((0.05, 0.05, 0.06, 1) if not whites else
                       (0.97, 0.97, 0.97, 1))
        self.ball_np.setColor((0.97, 0.97, 0.95, 1) if not whites else
                              (0.70, 0.08, 0.08, 1))
        self._named = {}

    def set_people(self, striker, nonstriker, bowler, styles):
        """Give the three named players their real look: handedness,
        bowling style, skin tone from their photo, surname on the back."""
        key = (striker, nonstriker, bowler)
        if key == getattr(self, "_people", None):
            return
        self._people = key
        for role, nm in (("striker", striker), ("nonstriker", nonstriker),
                         ("bowler", bowler)):
            st = styles.get(role) or {}
            r = self.rigs[role]
            if r is None:
                continue
            if getattr(r, "_who", None) != nm and nm:
                skin = st.get("skin") or SKINS[int(hashlib.md5(
                    nm.encode()).hexdigest(), 16) % len(SKINS)]
                bat = team_rgb(self.bat_code if role != "bowler"
                               else self.bowl_code)
                dark = tuple(x * 0.45 for x in bat)
                r.root.removeNode()
                r.shadow.removeNode()
                r = Rig(self.render, bat, dark, skin,
                        "bat" if role != "bowler" else "field", name=nm,
                        cap=dark, whites=self.whites, hair=st.get("hair"))
                r._who = nm
                self.rigs[role] = r
            r.left = bool(st.get("left")) if role != "bowler" else \
                bool(st.get("left_arm"))

    def set_field(self, phase, left, spinner):
        self.phase = phase
        key = "test" if self.whites else phase
        names = list(FIELD_SETS.get(key, FIELD_SETS["middle"]))
        if spinner and "slip" in names and phase != "powerplay":
            names[names.index("slip")] = "short leg" if self.whites else \
                "mid-wicket"
        self.field_names = names
        self.left_bat = left
        self.spinner = bool(spinner)
        # up at the stumps to a spinner, a keeper wears a helmet
        k = self.rigs.get("keeper")
        if k is not None and getattr(k, "_helmet", False) != self.spinner:
            bowl = team_rgb(self.bowl_code)
            dark = tuple(x * 0.45 for x in bowl)
            skin = getattr(k, "_skin", SKINS[0])
            k.root.removeNode()
            k.shadow.removeNode()
            k = Rig(self.render, bowl, dark, skin, "keeper",
                    cap="helmet" if self.spinner else dark,
                    whites=self.whites)
            k._helmet, k._skin = self.spinner, skin
            self.rigs["keeper"] = k

    # -- commands from the commentary ------------------------------------
    def deliver(self, info):
        """The voice has started on this ball: run it."""
        self.bat_code = info.get("bat_code") or self.bat_code
        self.bowl_code = info.get("bowl_code") or self.bowl_code
        self.set_teams(self.bat_code, self.bowl_code, info.get("fmt") or "ODI")
        self.set_people(info.get("striker"), info.get("nonstriker"),
                        info.get("bowler"), info.get("styles") or {})
        self.set_field(info.get("phase") or "middle", info.get("bat_left"),
                       info.get("spinner"))
        # the named fielder must be standing there
        tname = named_position(info.get("detail"))
        if tname and tname not in self.field_names and tname not in (
                "keeper", "wicketkeeper", "bowler", "back to the bowler"):
            tp = pos_xy(tname, info.get("bat_left"), 19.5)
            deep = POS[tname][1] == "deep"
            best = min(range(len(self.field_names)), key=lambda i: (
                (POS[self.field_names[i]][1] == "deep") != deep,
                math.hypot(*(np.array(pos_xy(self.field_names[i],
                             info.get("bat_left"), 19.5)) - tp))))
            self.field_names[best] = tname
        self.play = Play(self, info, seed=hash(info.get("text", "")) & 0xffff)
        self.play_t = 0.0
        self.play_speed = 1.0
        self.result_seen = False
        self.replay = None
        self.replaying = False
        self.stumps_hit = {}
        self.signal = None
        self.celebrate = None
        self.trail = []
        self._cut("broadcast")

    def result(self, info=None):
        """The voice is saying what happened. If the picture has not got to
        the shot yet, hurry it so the shot lands with the words."""
        self.result_seen = True
        p = self.play
        if not p:
            return
        left = p.T_contact - self.play_t
        if left > 0.45:
            self.play_speed = max(1.0, left / 0.45)

    def event(self, name):
        if name == "over":
            self._cut("aerial")
        elif name in ("break", "innings"):
            self._cut("wide")

    # -- per frame -------------------------------------------------------
    def update(self, dt):
        dt = min(dt, 0.1)
        self.t += dt
        if self.replay:
            rp = self.replay
            rp["t"] += dt * rp["speed"]
            self._animate(rp["play"], rp["t"])
            if rp["t"] > rp["end"]:
                self.replay = None
                self.replaying = False
                self._cut("wide")
        elif self.play:
            p = self.play
            self.play_t += dt * self.play_speed
            if self.play_t >= p.T_contact and self.play_speed > 1.0:
                self.play_speed = 1.0
            self._animate(p, self.play_t)
            self._direct(p, self.play_t)
            if self.play_t > p.T_end:
                big = p.kind in ("six", "four", "wicket")
                if big and not getattr(p, "_replayed", False):
                    p._replayed = True
                    self._start_replay(p)
                else:
                    self.play = None
                    self._idle_reset()
        else:
            self._idle(self.t)
        self._update_camera(dt)
        self._update_crowd(dt)

    # -- animation ----------------------------------------------------------
    def _field_xy(self, name):
        return pos_xy(name, self.left_bat, 11.2 if self.spinner else 19.5)

    def _animate(self, p, t):
        R = self.rigs
        m = -1.0 if p.left else 1.0
        # bowler
        b = R["bowler"]
        start_y = -CREASE_Y - p.ru_len - 1.0
        if t < p.T_ru:
            u = t / p.T_ru
            y = start_y + p.ru_len * u
            b.place(p.bowl_x * 1.6, y, 0)
            b.pose(pose_run(t * (6.5 if not p.spinner else 5.0),
                            p.ru_speed * min(1.0, 0.4 + u)))
        elif t < p.T_ru + p.T_bound:
            u = (t - p.T_ru) / p.T_bound
            b.place(p.bowl_x * 1.6 - p.bowl_x * 0.6 * u,
                    -CREASE_Y - 1.0 + 1.0 * u, 0)
            b.pose(pose_bowl(u * 0.75, p.spinner))
        else:
            k = t - p.T_ru - p.T_bound
            if p.kind == "wicket" and t > p.T_contact + 0.6:
                b.place(p.bowl_x, -CREASE_Y + 2.0, 0)
                b.pose(pose_celebrate(t) if p.how != "lbw" or
                       t > p.T_contact + 1.6 else pose_appeal(t))
            elif k < 1.2:
                b.place(p.bowl_x + 0.8 * k, -CREASE_Y + 2.5 * k, 0)
                b.pose(pose_bowl(0.75 + 0.25 * min(1, k / 0.4), p.spinner)
                       if k < 0.4 else pose_run(t * 5, 3.0 * (1.2 - k)))
            else:
                b.place(p.bowl_x + 0.96, -CREASE_Y + 3.0, 15 * m)
                b.pose(pose_stand(t))

        # striker
        s = R["striker"]
        s.left = p.left
        bx = 0.28 * m
        bh = 90.0 if not p.left else -90.0
        T0 = p.T_contact - 0.42
        if p.runs_T0 is not None and t > p.runs_T0:
            self._run_between(p, t)
        elif t < T0:
            adv = p.advance * smooth((t - (T0 - 0.8)) / 0.8)
            s.place(bx, CREASE_Y + 0.15 - adv, bh)
            st = dict(STANCE)
            if t > p.T_rel - 0.3:              # pick up the bat
                st = pose_stroke(p.stroke, 0.3 * smooth(
                    (t - p.T_rel + 0.3) / 0.3), p.swing_h)
            s.pose(st)
        else:
            u = 0.55 * (t - T0) / 0.42 if t < p.T_contact else \
                0.55 + 0.45 * min(1.0, (t - p.T_contact) / 0.5)
            s.place(bx, CREASE_Y + 0.15 - p.advance, bh)
            s.pose(pose_stroke(p.stroke, u, p.swing_h))
            if p.kind == "wicket" and t > p.T_contact + 1.4:
                s.pose(pose_dejected(t))

        # non-striker backs up as the ball is bowled
        n = R["nonstriker"]
        if not (p.runs_T0 is not None and t > p.runs_T0):
            back = 1.2 * smooth((t - p.T_ru + 0.4) / 0.8)
            n.place(-0.9 * (1 if p.bowl_x > 0 else -1), -CREASE_Y - 0.6 + back,
                    0)
            n.pose(pose_run(t * 4, 1.5) if 0 < t - p.T_ru + 0.4 < 0.8
                   else pose_stand(t))

        # keeper
        k = R["keeper"]
        kx = p.contact[0] * 0.5
        k.place(kx, p.keeper_y, 180)
        up = smooth((t - p.T_rel) / 0.35)
        if p.how == "stumped" and t > p.T_contact:
            k.place(0, STUMPS_Y + 0.6, 180)
        if p.kind == "wicket" and t > p.T_contact + 0.8 and p.how != "lbw":
            k.pose(pose_celebrate(t))
        else:
            k.pose(pose_keeper(up if t < p.T_contact + 0.8 else 1.0))

        # fielders: walk in with the bowler, then chase / catch / watch
        ball = p.ball(t)
        for i, name in enumerate(self.field_names[:9]):
            f = R["f%d" % i]
            home = self._field_xy(name)
            walk_in = 2.2 * smooth(t / max(0.5, p.T_ru))
            dx, dy = -home[0], STUMPS_Y - home[1]
            dl = math.hypot(dx, dy) or 1
            hx, hy = home[0] + dx / dl * walk_in, home[1] + dy / dl * walk_in
            face = math.degrees(math.atan2(-dx, dy))
            if name == p.chaser and t > p.T_contact:
                tgt = p.after[0][1].p1 if p.after else (hx, hy, 0)
                tt = p.T_contact + (p.after[0][1].T if p.after else 1)
                u = smooth((t - p.T_contact) / max(0.4, tt - p.T_contact))
                x = hx + (tgt[0] - hx) * u
                y = hy + (tgt[1] - hy) * u
                spd = math.hypot(tgt[0] - hx, tgt[1] - hy) / max(
                    0.4, tt - p.T_contact)
                f.place(x, y, math.degrees(math.atan2(-(tgt[0] - hx),
                                                      tgt[1] - hy)))
                if u < 1.0:
                    if p.info.get("field") == "dive" and u > 0.75:
                        f.pose(pose_dive((u - 0.75) / 0.25, -1))
                    else:
                        f.pose(pose_run(t * 9, min(8, spd)))
                elif len(p.after) > 1 and t < p.after[1][0] + 0.2:
                    f.pose(pose_throw((t - p.after[0][0] - p.after[0][1].T)
                                      / 0.35))
                else:
                    f.pose(pose_stand(t))
            elif name == p.catcher and t > p.T_contact:
                f.place(hx, hy, face)
                tc = p.after[0][0] + p.after[0][1].T
                if t < tc + 0.5:
                    f.pose(pose_catch(smooth((t - tc + 0.6) / 0.6)))
                else:
                    f.pose(pose_celebrate(t))
            elif p.kind == "wicket" and t > p.T_contact + 1.2:
                # everyone runs to the bowler
                bxy = (R["bowler"].pos[0], R["bowler"].pos[1])
                u = smooth((t - p.T_contact - 1.2) / 2.5)
                ang = i * 0.7
                gx = bxy[0] + 1.6 * math.cos(ang)
                gy = bxy[1] + 1.6 * math.sin(ang)
                f.place(hx + (gx - hx) * u, hy + (gy - hy) * u,
                        math.degrees(math.atan2(-(gx - hx), gy - hy)))
                f.pose(pose_run(t * 8, 6) if u < 1 else pose_celebrate(t))
            else:
                if ball is not None and t > p.T_contact:
                    face = math.degrees(math.atan2(-(ball[0] - hx),
                                                   ball[1] - hy))
                f.place(hx, hy, face)
                f.pose(pose_ready(t, smooth((t - p.T_ru + 0.6) / 0.6))
                       if t < p.T_contact + 1.0 else pose_stand(t))

        # umpires
        u1 = R["ump1"]
        u1.place(-0.25 if p.bowl_x > 0 else 0.25, -STUMPS_Y - 1.4, 0)
        sig = None
        if t > p.T_contact + 1.0:
            sig = {"six": "six", "four": "four", "wicket": "out",
                   "wide": "wide", "noball": "noball"}.get(p.kind)
            if p.kind == "wicket" and p.how in ("lbw",) and \
                    t < p.T_contact + 1.6:
                sig = None
        u1.pose(pose_signal(sig, t) if sig else pose_stand(t))
        u2 = R["ump2"]
        u2.place(24 * m, STUMPS_Y - 1.0, -90 * m)
        u2.pose(pose_stand(t))

        # ball and trail
        if ball is not None:
            self.ball_np.show()
            self.ball_np.setPos(*ball)
            self.trail.append(ball)
            self.trail = self.trail[-40:]
        else:
            self.ball_np.hide()
        self._draw_trail(p, t)

        # stumps
        for ev_t, kind, data in p.events:
            if kind == "stumps" and t >= ev_t:
                self._break_stumps(data, t - ev_t)
            if kind in ("stumps",) and t < ev_t:
                self._reset_stumps(data)

    def _run_between(self, p, t):
        """Both batsmen run their runs, turning at each end."""
        R = self.rigs
        k = (t - p.runs_T0) / 2.9
        n = p.n_runs
        for role, sgn in (("striker", 1), ("nonstriker", -1)):
            r = R[role]
            if k >= n:
                leg = n
                u = 1.0
            else:
                leg = int(k)
                u = k - leg
            # striker starts at +y; each leg reverses
            going_down = ((leg % 2 == 0) == (sgn == 1))
            y0 = CREASE_Y + 0.4 if going_down else -CREASE_Y - 0.4
            y1 = -y0
            uu = smooth(u)
            y = y0 + (y1 - y0) * uu
            if k >= n:
                y = y1 if n % 2 == 0 else y1
                y = (CREASE_Y + 0.4) * (1 if ((n % 2 == 0) == (sgn == 1))
                                        else -1)
            x = 0.9 * sgn
            r.place(x, y, 180 if going_down else 0)
            if k < n:
                r.pose(pose_run(t * 9, 6.5, carry=((-0.1, 0.35, 1.0),
                                                    (0.1, 0.35, 0.95))))
                if r.bat is not None:
                    r.bat.setPos(0.15, 0.45, 0.95)
                    r.bat.setHpr(*_aim_hp((0, 1, -0.4)), 0)
            else:
                r.pose(pose_stand(t))

    def _draw_trail(self, p, t):
        if self._trail_np:
            self._trail_np.removeNode()
            self._trail_np = None
        if len(self.trail) < 2 or (t > p.T_contact + 1.2 and
                                   p.kind not in ("six", "four")):
            return
        ls = LineSegs()
        ls.setThickness(3)
        n = len(self.trail)
        for i, q in enumerate(self.trail):
            a = i / n
            ls.setColor(1.0, 0.85, 0.3, a * 0.8)
            if i == 0:
                ls.moveTo(*q)
            else:
                ls.drawTo(*q)
        self._trail_np = self.render.attachNewNode(ls.create())
        self._trail_np.setTransparency(TransparencyAttrib.MAlpha)
        self._trail_np.setLightOff()

    def _break_stumps(self, ey, dt):
        sticks, bails = self.stumps[ey]
        sgn = 1 if ey > 0 else -1
        for i, s in enumerate(sticks):
            s.setP(-sgn * min(25, dt * 160) * (1 if i == 1 else 0.6))
        for i, b in enumerate(bails):
            vx, vy, vz = (-1.2 + 2.4 * i), 2.5 * sgn, 4.0
            z = 0.725 + vz * dt - 0.5 * G * dt * dt
            if z < 0.02:
                z = 0.02
                dt_ = (vz + math.sqrt(vz * vz + 2 * G * 0.7)) / G
            else:
                dt_ = dt
            b.setPos(-0.057 + 0.114 * i + vx * dt_ * 0.5,
                     ey + vy * dt_ * 0.5, z)
            b.setHpr(dt_ * 700, dt_ * 500, 0)
            # LED bails and stumps flash when broken
            on = int(dt * 8) % 2 == 0 and dt < 3
            b.setColorScale((1.6, 0.25, 0.2, 1) if on else (1, 1, 1, 1))
        for s in sticks:
            on = int(dt * 8) % 2 == 0 and dt < 3
            s.setColorScale((1.5, 0.3, 0.25, 1) if on else (1, 1, 1, 1))

    def _reset_stumps(self, ey):
        sticks, bails = self.stumps[ey]
        for s in sticks:
            s.setP(0)
            s.setColorScale(1, 1, 1, 1)
        for i, b in enumerate(bails):
            b.setPos(-0.057 + 0.114 * i, ey, 0.725)
            b.setHpr(0, 0, 0)
            b.setColorScale(1, 1, 1, 1)

    def _idle_reset(self):
        for ey in self.stumps:
            self._reset_stumps(ey)
        self.ball_np.hide()
        if self._trail_np:
            self._trail_np.removeNode()
            self._trail_np = None
        self.trail = []

    def _idle(self, t):
        """Between balls: bowler walks back, fielders stand, batsmen take
        guard, umpires wait."""
        R = self.rigs
        m = -1.0 if self.left_bat else 1.0
        spinner = self.spinner
        b = R["bowler"]
        ru = 6.5 if spinner else 20.0
        cyc = (t % 14.0) / 14.0
        y = -CREASE_Y - 1 - ru * min(1.0, cyc * 1.6)
        if cyc < 0.62:
            b.place(0.6, y, 180)
            b.pose(pose_run(t * 4.5, 1.6))
        else:
            b.place(0.6, -CREASE_Y - 1 - ru, 0)
            b.pose(pose_stand(t))
        s = R["striker"]
        s.left = self.left_bat
        s.place(0.28 * m, CREASE_Y + 0.15, 90.0 if not self.left_bat else -90)
        s.pose(STANCE if (t % 9) > 3 else pose_stand(t))
        n = R["nonstriker"]
        n.place(-0.9, -CREASE_Y - 0.6, 0)
        n.pose(pose_stand(t))
        k = R["keeper"]
        ky = 11.2 if spinner else 19.5
        k.place(0, ky, 180)
        k.pose(pose_stand(t))
        for i, name in enumerate(self.field_names[:9]):
            f = R["f%d" % i]
            x, yy = self._field_xy(name)
            f.place(x, yy, math.degrees(math.atan2(x, STUMPS_Y - yy)))
            f.pose(pose_stand(t + i))
        R["ump1"].place(0.25, -STUMPS_Y - 1.4, 0)
        R["ump1"].pose(pose_stand(t))
        R["ump2"].place(24 * m, STUMPS_Y - 1.0, -90 * m)
        R["ump2"].pose(pose_stand(t))
        self.ball_np.hide()
        if self.shot is None or t > self.idle_next:
            self._cut(random.choice(["wide", "batsman", "aerial", "bowler",
                                     "crowd", "wide", "side", "spider",
                                     "umpire", "helicopter", "nonstriker"]))
            self.idle_next = t + random.uniform(6.0, 9.0)

    # -- replays ---------------------------------------------------------
    def _start_replay(self, p):
        start = max(0.0, p.T_contact - (1.6 if p.kind != "six" else 1.0))
        end = p.T_contact + (2.6 if p.kind != "six" else 3.4)
        self.replay = {"play": p, "t": start, "end": end, "speed": 0.4}
        self.replaying = True
        self._cut(random.choice(["side", "umpire", "spider"])
                  if p.kind != "wicket" else
                  random.choice(["endon", "side", "umpire"]))

    # -- cameras -------------------------------------------------------
    def _cut(self, shot):
        self.shot = shot
        self.shot_t0 = self.t
        self._cam_pos = None

    def _direct(self, p, t):
        """Broadcast cutting during a live delivery."""
        if t < p.T_contact + 0.25:
            if self.shot != "broadcast":
                self._cut("broadcast")
            return
        after = t - p.T_contact
        if p.kind in ("six", "four") and self.shot == "broadcast":
            self._cut("follow")
        elif p.kind in ("one", "two", "three", "runs", "bye", "legbye") \
                and self.shot == "broadcast" and after > 0.4:
            self._cut("follow")
        elif p.kind == "wicket" and self.shot == "broadcast" and after > 0.5:
            self._cut("stumpcam" if p.how in ("bowled", "stumped", "runout")
                      else "follow" if p.how in ("caught", "caught_nofield",
                                                 "other") else "bowler")
        elif p.kind == "wicket" and self.shot == "follow" and p.catcher \
                and p.after and after > p.after[0][1].T - 0.5:
            self._focus = self._rig_for(p.catcher)
            self._cut("fielder")            # the man taking the catch
        elif p.kind == "wicket" and self.shot in ("stumpcam", "follow",
                                                  "fielder") \
                and after > (p.after[0][1].T + 1.6 if p.catcher and p.after
                             else 2.4):
            self._cut("celebrate")
        elif p.runs_T0 is not None and self.shot == "follow" and p.after \
                and after > p.after[0][1].T + 0.2:
            self._cut("side")               # the batsmen running
        elif p.kind == "six" and self.shot == "follow" and after > 3.0:
            self._cut("crowd")
        elif p.kind == "four" and self.shot == "follow" and after > 1.6 \
                and p.after and random.random() < 0.5 and \
                not getattr(p, "_rope_cut", False):
            p._rope_cut = True
            tgt = p.after[-1][1].p1
            self._rope_at = (float(tgt[0]), float(tgt[1]))
            self._cut("rope")
        elif p.kind == "four" and self.shot in ("follow", "rope") and \
                after > 2.8:
            self._cut("batsman")
        elif p.kind not in ("six", "four", "wicket") and after > 2.2 and \
                self.shot == "broadcast":
            self._cut("batsman")

    def _rig_for(self, field_name):
        if field_name in self.field_names[:9]:
            return "f%d" % self.field_names.index(field_name)
        return "keeper"

    def _shot_params(self, shot, t):
        """(camera position, look-at, metres of height the band should
        frame at the look-at point)."""
        R = self.rigs
        m = -1.0 if self.left_bat else 1.0
        age = t - self.shot_t0
        ball = self.ball_np.getPos() if not self.ball_np.isHidden() else None
        if shot == "broadcast":
            return (0.0, -79.0, 12.0), (0.0, 6.5, 0.9), 4.6
        if shot == "follow":
            look = ball if ball is not None else Point3(0, 20, 0)
            # look at the ground under a high ball, or the frame is all sky
            return (0.0, -70.0, 30.0), (look[0], look[1],
                                        min(look[2], 3.0) * 0.5), 16.0
        if shot == "fielder":
            f = R.get(self._focus) or R["bowler"]
            fx, fy = f.pos
            d = math.hypot(fx, fy) or 1.0
            ux, uy = -fx / d, -fy / d
            return (fx + ux * 9.0, fy + uy * 9.0, 2.4), (fx, fy, 1.3), 3.4
        if shot == "batsman":
            s = R["striker"].pos
            return (s[0] - 7.5 * m, s[1] - 3.5, 1.7), (s[0], s[1] + 0.4, 1.15), \
                2.6
        if shot == "bowler":
            b = R["bowler"].pos
            return (b[0] + 4.0, b[1] + 7.0, 2.0), (b[0], b[1], 1.2), 2.8
        if shot == "celebrate":
            b = R["bowler"].pos
            return (b[0] + 7.0, b[1] + 6.0, 2.6), (b[0], b[1], 1.3), 4.5
        if shot == "stumpcam":
            return (1.6, STUMPS_Y + 4.5, 0.7), (0.0, STUMPS_Y, 0.45), 1.6
        if shot == "crowd":
            a = math.radians(30 + age * 3)
            look = (RX * 1.3 * math.cos(a), RY * 1.3 * math.sin(a), 10)
            return (0.0, 0.0, 6.0), look, 26.0
        if shot == "aerial":
            a = math.radians(age * 4)
            return (22 * math.cos(a), 22 * math.sin(a) - 4, 30.0), \
                (0, 2, 0), 12.0
        if shot == "nonstriker":
            n = R["nonstriker"].pos
            return (n[0] + 5.5, n[1] + 4.0, 1.8), (n[0], n[1], 1.2), 2.7
        if shot == "umpire":
            # over the bowler's-end umpire's shoulder, down the pitch
            return (0.55, -STUMPS_Y - 4.6, 1.85), (0.0, STUMPS_Y, 1.0), 3.6
        if shot == "spider":
            # spider-cam gliding along the pitch on its wires
            y = -24 + (age * 2.2) % 48
            return (6.0 * math.sin(age * 0.2), y, 15.0), (0.0, y + 9, 0.0), 9.0
        if shot == "helicopter":
            a = math.radians(age * 2.5)
            return (170 * math.cos(a), 170 * math.sin(a), 95.0), (0, 0, 0), \
                55.0
        if shot == "rope":
            tx, ty = getattr(self, "_rope_at", (0.0, -RY))
            d = math.hypot(tx, ty) or 1.0
            return (tx * 1.06, ty * 1.06, 1.3), (tx * 0.8, ty * 0.8, 0.5), 5.0
        if shot == "side":
            return (30.0 * m, 0.5, 3.2), (0.0, 1.5, 1.0), 5.5
        if shot == "endon":
            return (0.6, 34.0, 4.0), (0.0, -2.0, 1.2), 5.0
        # wide: high behind the bowler, slowly drifting, the field and the
        # stands - the shot the reference stream sits on between balls
        a = math.radians(-90 + 10 * math.sin(age * 0.08))
        return (100 * math.cos(a), 100 * math.sin(a), 36.0), (0, 18, 0), 30.0

    def _update_camera(self, dt):
        pos, look, frame_h = self._shot_params(self.shot or "wide", self.t)
        pos = Point3(*pos)
        look = Point3(*look)
        if self.shot == "follow" and self._cam_pos is not None:
            look = self._cam_look + (look - self._cam_look) * min(1, dt * 6)
        self._cam_pos, self._cam_look = pos, look
        cam = self.base.camera
        cam.setPos(pos)
        cam.lookAt(look)
        # frame frame_h metres inside the visible band, centred on it
        W, H = self.size
        b0, b1 = self.band
        dist = (look - pos).length()
        band_ang = 2 * math.atan(frame_h / 2 / max(dist, 0.1))
        tan_v = math.tan(band_ang / 2) * H / (b1 - b0)
        fv = math.degrees(2 * math.atan(tan_v))
        fh = math.degrees(2 * math.atan(tan_v * W / H))
        self.lens.setFov(min(fh, 150), min(fv, 120))
        fs = self.lens.getFilmSize()
        centre = (b0 + b1) / 2.0
        ndc_y = 1 - 2 * centre / H
        self.lens.setFilmOffset(0, -ndc_y * fs[1] / 2)

    def _update_crowd(self, dt):
        """A crowd that is alive: between balls people shift in their seats
        now and then; on a four, six or wicket they are on their feet,
        bouncing, arms up, flags out."""
        if self.crowd_buzz > 0:
            self.crowd_buzz -= dt
            frame = 2 if int(self.t * 6) % 2 == 0 else 1
            j = 0.004 * math.sin(self.t * 40)
        else:
            if self.t >= self._crowd_next:
                self._idle_frame = 1 if getattr(self, "_idle_frame", 0) == 0 \
                    else 0
                self._crowd_next = self.t + random.uniform(0.6, 1.8)
            frame = getattr(self, "_idle_frame", 0)
            j = 0.0
        if frame != self._crowd_frame:
            self._crowd_frame = frame
            for t in (self.tier1, self.tier2):
                t.setTexture(self.crowd_frames[frame], 1)
        for t in (self.tier1, self.tier2):
            t.setTexOffset(self.crowd_ts, j, abs(j))

    def tag_points(self):
        """(role, name, x px, y px) above the heads of the striker,
        non-striker and bowler, for AR name tags - only for those in view."""
        out = []
        names = dict(zip(("striker", "nonstriker", "bowler"),
                         getattr(self, "_people", (None, None, None))))
        W, H = self.size
        cam = self.base.cam
        for role in ("striker", "nonstriker", "bowler"):
            r = self.rigs.get(role)
            nm = names.get(role)
            if r is None or not nm:
                continue
            p = r.root.getPos(self.render)
            head = Point3(p.x, p.y, 2.15 * getattr(r, "height", 1.0))
            pc = cam.getRelativePoint(self.render, head)
            if pc.y <= 0.5:
                continue
            ndc = Point2()
            if not self.lens.project(pc, ndc):
                continue
            out.append((role, nm, (ndc.x + 1) / 2 * W, (1 - ndc.y) / 2 * H,
                        pc.y))
        return out

    def focus(self, role):
        """The commentator is talking about someone - cut to them, the way
        a director does. Only between balls; a live delivery is never
        interrupted."""
        if self.play is not None or self.replay:
            return
        shot = {"striker": "batsman", "nonstriker": "nonstriker",
                "bowler": "bowler", "pair": "side", "crowd": "crowd",
                "ground": random.choice(["wide", "helicopter", "spider",
                                         "aerial"])}.get(role)
        if shot:
            self._cut(shot)
            self.idle_next = self.t + 6.5

    def cheer(self, secs=3.0):
        self.crowd_buzz = secs
