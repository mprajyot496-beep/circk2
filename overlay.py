# -*- coding: utf-8 -*-
"""
overlay.py  -  the broadcast scoreboard, drawn with pygame onto a transparent
layer over the 3D, with motion.

Everything here is plain 2D drawing on the CPU (no GPU features), redrawn ~30
times a second. Text surfaces are cached, so a frame is mostly blits.

    ┌──────────────────────────── top band (112 px) ───────────────────────┐
    │ [flag] AUS-A 171-5 (33.1)      │ SIX │       IND-A  BOWL  [flag]       │
    │ CRR 5.16                P'SHIP 84 (99)            AUS-A OPT TO BAT     │
    │ RECENT  OVER 33 1 1 0 1 0 0 = 3   OVER 34 0 = 0                       │
    │                                                                       │
    │                 3D view  (SIX!/FOUR!/WICKET! banners here)            │
    │  END OF OVER 33  1 1 0 1 0 0 = 3   Y THAKUR 2-21          (lower 3rd) │
    │ [photo] N McSweeney │ [photo] W Sutherland │ [photo] Y Thakur         │
    │ LAST WICKET ...   ◂ ticker: projected 265 • last 5 ov 21/1 ... ▸ LIVE │
    └───────────────────────────────────────────────────────────────────────┘

Motion, and why each is there:
    score       counts up to the new total, "+4" / "+6" floats off it, the
                box flashes - red when a wicket falls
    centre box  pops in with an overshoot, then a shine runs across it
    RECENT      the newest ball chip pops in
    banners     SIX / FOUR / WICKET: a ribbon wipes across the 3D band, the
                word slams in, confetti / sparks / shards; WICKET names the
                batsman and his score
    milestones  FIFTY / CENTURY: gold banner with the player's photo
    over break  END OF OVER lower-third slides in with the over and bowler
    cards       slide up when a new batsman or bowler arrives; the striker
                marker pulses
    ticker      the stats line along the bottom keeps moving
    glint       a light sweep across the top band every few seconds
"""
import math
import random

import pygame

import faces
from visual import (W, H, VIEW_Y, CARDS_Y, CHANNEL, WHITE, YELLOW, BLACK,
                    TOP_A, TOP_B, SCORE_A, SCORE_B, GREEN_A, GREEN_B, RED_A,
                    RED_B, CARD_A, CARD_B, BOWL_A, BOWL_B, STAT_BG,
                    short_name, overs_str, _token_runs, _vgrad, _rounded,
                    _team_col, bundled_font)

# ── Layout (px) ─────────────────────────────────────────────────────────────
TOP_H = 112
GREEN_Y, RED_Y, STRIP_H = 114, 148, 32
TAG_Y = 542                       # role tag above each card
PANEL_Y, PANEL_H = 572, 78        # name / score panel
STAT_Y, STAT_H = 652, 32          # 4s / 6s / SR row
PHOTO_H = 118                     # photo stands up out of the panel
BOTTOM_Y, BOTTOM_H = 690, 28
CARD_W = 412
CARD_X = (10, 434, 858)

GOLD = (255, 196, 40)
KIND = {   # banner: (main colour, dark colour, word, sub-word)
    "six": ((150, 70, 255), (70, 20, 160), "SIX!", "MAXIMUM"),
    "four": ((20, 190, 95), (8, 100, 50), "FOUR!", "BOUNDARY"),
    "wicket": ((235, 40, 50), (120, 8, 18), "WICKET!", ""),
}


def clamp(u):
    return 0.0 if u < 0 else 1.0 if u > 1 else u


def ease_out_cubic(u):
    u = clamp(u)
    return 1 - (1 - u) ** 3


def ease_out_back(u, s=1.9):
    u = clamp(u) - 1
    return 1 + (s + 1) * u ** 3 + s * u ** 2


def ease_in_cubic(u):
    u = clamp(u)
    return u ** 3


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "age", "col", "w", "h",
                 "spin", "g")

    def __init__(self, x, y, vx, vy, life, col, w, h, g=380.0):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life, self.age, self.col = life, 0.0, col
        self.w, self.h, self.g = w, h, g
        self.spin = random.uniform(0, 6.28)


class Overlay:
    def __init__(self):
        self.surf = pygame.Surface((W, H), pygame.SRCALPHA)
        # Windows fonts first; on a Linux server, the bundled OFL lookalikes
        bold = (pygame.font.match_font("franklingothicdemicond")
                or pygame.font.match_font("bahnschrift")
                or pygame.font.match_font("arialblack")
                or bundled_font("BarlowCondensed-Bold.ttf"))
        heavy = (pygame.font.match_font("impact")
                 or bundled_font("Anton-Regular.ttf") or bold)

        def fb(sz, path=bold):
            return (pygame.font.Font(path, sz) if path
                    else pygame.font.SysFont("segoeui", sz, bold=True))

        self.F = {"score": fb(52), "event": fb(58), "big": fb(34),
                  "mid": fb(27), "small": fb(21), "tiny": fb(16),
                  "banner": fb(112, heavy), "sub": fb(30), "badge": fb(40)}
        self._tc = {}
        self._img = {}
        self._static = self._build_static()

        self._now = 0
        self._ev_t0 = None
        self._flash_t0 = None
        self.banner = None          # dict(kind, t0, sub)
        self.milestone = None       # dict(label, t0, player)
        self.over_panel = None      # dict(t0, over, toks, bowler)
        self.particles = []
        self._score = None          # (shown runs float, from, to, t0)
        self._wkts = None
        self._plus = None           # (text, t0, colour)
        self._score_flash = None    # (t0, colour)
        self._chips = 0
        self._chip_t0 = -9999
        self._slots = {}            # card slot -> (player name, t0)
        self._ticker_txt = ""
        self._ticker_img = None
        self._ticker_x = 0.0
        self._glint_t0 = 0
        self._tag_t = {}
        self._focus_role = (None, 1e9)

    # -- caches ----------------------------------------------------------
    def _build_static(self):
        s = {}
        top = _vgrad((W, TOP_H), TOP_A, TOP_B)
        sheen = pygame.Surface((W, TOP_H), pygame.SRCALPHA)
        for x in range(-TOP_H, W, 40):
            pygame.draw.polygon(sheen, (255, 255, 255, 13),
                                [(x, 0), (x + 18, 0), (x + 18 + TOP_H, TOP_H),
                                 (x + TOP_H, TOP_H)])
        top.blit(sheen, (0, 0))
        pygame.draw.line(top, (255, 120, 220), (0, TOP_H - 2), (W, TOP_H - 2), 2)
        s["top"] = top
        s["green"] = _vgrad((W, STRIP_H), GREEN_A, GREEN_B)
        s["red"] = _vgrad((W, STRIP_H), RED_A, RED_B)
        s["score"] = _rounded(_vgrad((250, 58), SCORE_A, SCORE_B), 9)
        s["card"] = _rounded(_vgrad((CARD_W, PANEL_H), CARD_A, CARD_B), 8)
        s["bcard"] = _rounded(_vgrad((CARD_W, PANEL_H), BOWL_A, BOWL_B), 8)
        # a soft diagonal light bar for shines and glints
        # additive blending ignores alpha, so the fade lives in the colour
        sh = pygame.Surface((90, 200))
        sh.fill((0, 0, 0))
        for i in range(90):
            a = int(70 * math.sin(math.pi * i / 89) ** 2)
            pygame.draw.line(sh, (a, a, a), (i, 0), (i, 199))
        s["shine"] = pygame.transform.rotate(sh, -18)
        return s

    def T(self, fk, txt, col):
        key = (fk, str(txt), col)
        img = self._tc.get(key)
        if img is None:
            if len(self._tc) > 900:
                self._tc.clear()
            img = self.F[fk].render(str(txt), True, col)
            self._tc[key] = img
        return img

    def text(self, fk, txt, col, pos, anchor="topleft", shadow=True,
             alpha=255, surf=None):
        surf = surf or self.surf
        img = self.T(fk, txt, col)
        r = img.get_rect(**{anchor: pos})
        if shadow:
            sh = self.T(fk, txt, (0, 0, 0))
            if alpha < 255 or True:
                sh = sh.copy()
                sh.set_alpha(int(130 * alpha / 255))
            surf.blit(sh, (r.x + 2, r.y + 2))
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        surf.blit(img, r)
        return r

    def image(self, path, size, fit="height"):
        key = (path, size, fit)
        if key in self._img:
            return self._img[key]
        try:
            im = pygame.image.load(path)
        except Exception:
            self._img[key] = None
            return None
        w, h = im.get_size()
        if fit == "cover":
            sc = max(size[0] / w, size[1] / h)
            im = pygame.transform.smoothscale(im, (int(w * sc) + 1,
                                                   int(h * sc) + 1))
            crop = pygame.Rect(0, 0, *size)
            crop.center = im.get_rect().center
            im = im.subsurface(crop.clip(im.get_rect())).copy()
        else:
            sc = size[1] / h
            im = pygame.transform.smoothscale(im, (max(1, int(w * sc)), size[1]))
        self._img[key] = im
        return im

    def shine(self, rect, u):
        """A light bar sweeping across rect, u 0 -> 1."""
        if not 0 <= u <= 1:
            return
        r = pygame.Rect(rect)
        sub = self.surf.subsurface(r.clip(self.surf.get_rect()))
        img = self._static["shine"]
        x = -img.get_width() + (r.w + img.get_width()) * u
        sub.blit(img, (x, (r.h - img.get_height()) // 2),
                 special_flags=pygame.BLEND_RGB_ADD)

    # -- events from the scene -------------------------------------------
    def _events(self, st, b):
        now = self._now
        label, col, t0 = st["event"]
        if t0 and t0 != self._ev_t0:
            self._ev_t0 = t0
            if label in ("FIFTY", "CENTURY") and b:
                self.milestone = {"label": label, "t0": now,
                                  "player": self._milestone_player(b, label)}
                self._burst("gold", W / 2, (VIEW_Y + CARDS_Y) / 2)
            elif label == "OVER" and b:
                self.over_panel = {"t0": now, "b": b,
                                   "log": list(st["overs_log"])}
        fl = st["flash"]
        if fl and fl[1] != self._flash_t0:
            self._flash_t0 = fl[1]
            kind = fl[0]
            sub = KIND[kind][3]
            if kind == "wicket" and b and b.get("last_wicket"):
                sub = b["last_wicket"]
            self.banner = {"kind": kind, "t0": now, "sub": sub}
            self._burst(kind, W / 2, CARDS_Y - 82)

        if b:
            runs, wkts = int(b["runs"] or 0), int(b["wkts"] or 0)
            if self._score is None:
                self._score = [float(runs), runs, runs, now]
                self._wkts = wkts
            elif runs != self._score[2]:
                cur = self._shown_runs()
                delta = runs - self._score[2]
                self._score = [cur, cur, runs, now]
                if 0 < delta <= 7:
                    c = {6: (200, 150, 255), 4: (90, 240, 140)}.get(
                        delta, YELLOW)
                    self._plus = ("+%d" % delta, now, c)
                self._score_flash = (now, (255, 255, 255))
            if wkts > (self._wkts or 0):
                self._score_flash = (now, (255, 60, 60))
            self._wkts = wkts
        n = sum(len(t) for _, t in st["overs_log"])
        if n > self._chips and self._chips:     # not on the first frame
            self._chip_t0 = now
        self._chips = n

    def _milestone_player(self, b, label):
        mark = 100 if label == "CENTURY" else 50
        cand = [p for p in (b["striker"], b["non_striker"])
                if (p.get("runs") or 0) >= mark]
        return min(cand, key=lambda p: p.get("runs") or 0) if cand \
            else b["striker"]

    def _shown_runs(self):
        cur, frm, to, t0 = self._score
        u = clamp((self._now - t0) / 700.0)
        return frm + (to - frm) * ease_out_cubic(u)

    # -- particles -------------------------------------------------------
    def _burst(self, kind, cx, cy):
        P = self.particles
        if kind == "six":
            cols = [(255, 214, 10), (190, 120, 255), (255, 80, 160),
                    (80, 220, 255), (255, 255, 255), (60, 230, 120)]
            for _ in range(150):
                P.append(Particle(random.uniform(0, W), random.uniform(
                    VIEW_Y - 60, VIEW_Y), random.uniform(-60, 60),
                    random.uniform(40, 220), random.uniform(1.8, 3.2),
                    random.choice(cols), random.randint(5, 9),
                    random.randint(8, 14), g=160))
        elif kind == "four":
            for _ in range(80):
                a = random.uniform(0, 6.28)
                v = random.uniform(220, 620)
                P.append(Particle(cx, cy, math.cos(a) * v, math.sin(a) * v * 0.5,
                                  random.uniform(0.6, 1.3),
                                  random.choice([(90, 255, 150), (255, 255, 255),
                                                 (255, 230, 90)]), 4, 4, g=260))
        elif kind == "wicket":
            for _ in range(70):
                a = random.uniform(0, 6.28)
                v = random.uniform(180, 520)
                P.append(Particle(cx, cy, math.cos(a) * v, math.sin(a) * v * 0.6,
                                  random.uniform(0.7, 1.5),
                                  random.choice([(255, 70, 70), (255, 255, 255),
                                                 (180, 20, 30)]),
                                  random.randint(4, 9), random.randint(3, 6),
                                  g=520))
        else:                                  # gold, milestones
            for _ in range(120):
                a = random.uniform(0, 6.28)
                v = random.uniform(120, 480)
                P.append(Particle(cx, cy, math.cos(a) * v, math.sin(a) * v * 0.6,
                                  random.uniform(1.0, 2.2),
                                  random.choice([GOLD, (255, 240, 150),
                                                 (255, 255, 255)]),
                                  random.randint(4, 8), random.randint(4, 8),
                                  g=200))

    def _particles(self, dt):
        alive = []
        for p in self.particles:
            p.age += dt
            if p.age >= p.life:
                continue
            p.vy += p.g * dt
            p.vx *= 0.985
            p.x += p.vx * dt
            p.y += p.vy * dt
            if p.y > CARDS_Y + 40:
                continue
            alive.append(p)
            fade = 1 - p.age / p.life
            w = max(1, int(p.w * abs(math.cos(p.spin + p.age * 9))))
            c = tuple(int(v * (0.55 + 0.45 * fade)) for v in p.col)
            pygame.draw.rect(self.surf, c, (int(p.x), int(p.y), w, p.h))
        self.particles = alive[-500:]

    # -- top band --------------------------------------------------------
    def _flag(self, rect, team):
        x, y, w, h = rect
        pygame.draw.rect(self.surf, WHITE, rect, border_radius=10)
        path = faces.flag_path(team.get("id"))
        img = self.image(path, (w - 8, h - 8), "cover") if path else None
        if img:
            self.surf.blit(_rounded(img, 7), (x + 4, y + 4))
        else:
            pygame.draw.rect(self.surf, _team_col(team.get("code")),
                             (x + 4, y + 4, w - 8, h - 8), border_radius=7)
            self.text("mid", (team.get("code") or "")[:3], WHITE,
                      (x + w // 2, y + h // 2), "center")

    def _top(self, b, event):
        S = self.surf
        S.blit(self._static["top"], (0, 0))
        if not b:
            self.text("big", "CONNECTING TO LIVE MATCH…", WHITE,
                      (W // 2, TOP_H // 2), "center")
            return
        now = self._now
        bat, bowl = b["bat"], b["bowl"]
        self._flag((8, 8, 96, 96), bat)
        tab = pygame.Rect(112, 8, 250, 38)
        pygame.draw.rect(S, BLACK, tab, border_radius=9)
        self.text("big", bat["code"], WHITE, tab.center, "center")

        box = pygame.Rect(112, 50, 250, 58)
        S.blit(self._static["score"], box.topleft)
        if self._score_flash and now - self._score_flash[0] < 900:
            u = 1 - (now - self._score_flash[0]) / 900.0
            c = self._score_flash[1]
            glow = pygame.Surface(box.size, pygame.SRCALPHA)
            glow.fill(c + (int(120 * u),))
            S.blit(_rounded(glow, 9), box.topleft)
            pygame.draw.rect(S, c, box.inflate(4, 4), 3, border_radius=11)
        runs = int(round(self._shown_runs())) if self._score else b["runs"]
        r = self.text("score", "%s-%s" % (runs, b["wkts"]), WHITE,
                      (box.x + 12, box.centery + 1), "midleft")
        self.text("mid", "(%s)" % overs_str(b["overs"]), YELLOW,
                  (r.right + 8, box.centery + 6), "midleft")
        if self._plus and now - self._plus[1] < 1300:
            u = (now - self._plus[1]) / 1300.0
            a = int(255 * (1 - ease_in_cubic(u)))
            self.text("badge", self._plus[0], self._plus[2],
                      (box.right + 14, box.centery - 6 - 40 * ease_out_cubic(u)),
                      "midleft", alpha=a)

        # centre: what just happened
        label, col, t0 = event
        ev = pygame.Rect(472, 12, 336, 92)
        pygame.draw.rect(S, BLACK, ev, border_radius=13)
        age = now - t0 if t0 else 99999
        if age < 1400:
            pulse = 0.5 + 0.5 * math.sin(age / 90.0)
            pygame.draw.rect(S, col, ev.inflate(6, 6), 3 + int(2 * pulse),
                             border_radius=15)
        else:
            pygame.draw.rect(S, (80, 80, 92), ev, 2, border_radius=13)
        fk = "event" if len(label) <= 8 else "big"
        img = self.T(fk, label, col)
        if age < 420:
            sc = ease_out_back(age / 420.0)
            sc = max(0.05, 0.4 + 0.6 * sc)
            img = pygame.transform.smoothscale(
                img, (max(1, int(img.get_width() * sc)),
                      max(1, int(img.get_height() * sc))))
        S.blit(img, img.get_rect(center=ev.center))
        if 420 <= age < 1100:
            self.shine(ev, (age - 420) / 680.0)
        if b.get("desc"):
            self.text("tiny", b["desc"], WHITE, (W // 2, 0), "midtop")

        self._flag((W - 104, 8, 96, 96), bowl)
        tab = pygame.Rect(W - 362, 8, 250, 38)
        pygame.draw.rect(S, BLACK, tab, border_radius=9)
        self.text("big", bowl["code"], WHITE, tab.center, "center")
        rb = pygame.Rect(W - 362, 50, 250, 58)
        S.blit(self._static["score"], rb.topleft)
        if b["chase"] and b.get("target"):
            right = "TARGET %s" % b["target"]
        elif b["chase"] and bowl.get("score"):
            right = bowl["score"]
        else:
            right = "BOWL"
        self.text("score" if len(right) <= 7 else "big", right, YELLOW,
                  rb.center, "center")

        # a glint across the whole band every ~9 s
        g = (now - self._glint_t0) / 1000.0
        if g > 9.0:
            self._glint_t0 = now
        elif g < 1.1:
            self.shine((0, 0, W, TOP_H), g / 1.1)

    def _strips(self, b, overs_log):
        S = self.surf
        S.blit(self._static["green"], (0, GREEN_Y))
        cy = GREEN_Y + STRIP_H // 2
        if b:
            if b["crr"]:
                r = self.text("small", "CRR:", YELLOW, (12, cy), "midleft")
                r = self.text("small", "%.2f" % float(b["crr"]), WHITE,
                              (r.right + 6, cy), "midleft")
                if b.get("rrr"):
                    r = self.text("small", "RRR:", YELLOW, (r.right + 20, cy),
                                  "midleft")
                    self.text("small", "%.2f" % b["rrr"], WHITE,
                              (r.right + 6, cy), "midleft")
            if b["pship"]:
                pill = pygame.Rect(0, 0, 230, 26)
                pill.center = (W // 2, cy)
                pygame.draw.rect(S, (8, 60, 20), pill, border_radius=7)
                pygame.draw.rect(S, (60, 170, 80), pill, 1, border_radius=7)
                r = self.text("small", "P'SHIP:", YELLOW, (pill.x + 14, cy),
                              "midleft")
                self.text("small", "%s (%s)" % b["pship"], WHITE,
                          (r.right + 8, cy), "midleft")
            if b["status"]:
                self.text("small", b["status"][:44], WHITE, (W - 12, cy),
                          "midright")

        S.blit(self._static["red"], (0, RED_Y))
        cy = RED_Y + STRIP_H // 2
        tag = pygame.Rect(-12, RED_Y + 3, 132, STRIP_H - 6)
        pygame.draw.rect(S, (150, 8, 20), tag, border_radius=14)
        pygame.draw.rect(S, (255, 110, 110), tag, 1, border_radius=14)
        self.text("small", "RECENT", WHITE, (12, cy), "midleft")
        x = 140
        last = len(overs_log) - 1
        for oi, (over, toks) in enumerate(overs_log[-2:]):
            r = self.text("small", "OVER %d" % over, WHITE, (x, cy), "midleft")
            x = r.right + 8
            shown = toks[-8:]
            for ti, t in enumerate(shown):
                bg = {"4": (40, 190, 90), "6": (150, 80, 230),
                      "W": (230, 40, 40)}.get(t, BLACK)
                w = max(28, 12 + 11 * len(t))
                chip = pygame.Rect(x, RED_Y + 4, w, STRIP_H - 8)
                newest = (oi == min(1, last) and ti == len(shown) - 1)
                age = self._now - self._chip_t0
                if newest and age < 500:
                    sc = 0.4 + 0.6 * ease_out_back(age / 500.0)
                    chip = chip.inflate(int(w * (sc - 1)),
                                        int(chip.h * (sc - 1)))
                    pygame.draw.rect(S, YELLOW, chip.inflate(4, 4), 2,
                                     border_radius=4)
                pygame.draw.rect(S, bg, chip, border_radius=3)
                pygame.draw.rect(S, (230, 230, 235), chip, 1, border_radius=3)
                self.text("tiny", t, WHITE, chip.center, "center", shadow=False)
                x += w + 6
            r = self.text("small", "= %d" % sum(map(_token_runs, toks)),
                          YELLOW, (x + 2, cy), "midleft")
            x = r.right + 28

    # -- the 3D band -----------------------------------------------------
    def _banner(self):
        bn = self.banner
        if not bn:
            return
        age = (self._now - bn["t0"]) / 1000.0
        T = 2.9
        if age > T:
            self.banner = None
            return
        main, dark, word, _ = KIND[bn["kind"]]
        # low in the 3D band, like a lower third: the ball's flight up and
        # out stays in view above it
        h = 112
        cy = CARDS_Y - h // 2 - 26
        # ribbon wipes in from the left, out to the right
        if age < 0.28:
            x0, x1 = 0, W * ease_out_cubic(age / 0.28)
        elif age > T - 0.45:
            x0, x1 = W * ease_in_cubic((age - (T - 0.45)) / 0.45), W
        else:
            x0, x1 = 0, W
        if x1 - x0 < 2:
            return
        band = _vgrad((int(x1 - x0), h), main, dark, 225)
        self.surf.blit(band, (x0, cy - h // 2))
        for dy, c in ((-h // 2 - 4, WHITE), (h // 2 + 1, WHITE)):
            pygame.draw.line(self.surf, c, (x0, cy + dy), (x1, cy + dy), 3)
        # speed streaks
        for i in range(7):
            sx = (age * 1900 + i * 210) % (W + 300) - 150
            sy = cy - h // 2 + 12 + i * 15
            pygame.draw.line(self.surf, (255, 255, 255), (sx, sy),
                             (sx + 120, sy), 2)
        # the word slams in
        if age > 0.12:
            u = (age - 0.12) / 0.35
            sc = 2.2 - 1.2 * ease_out_back(u, 1.4)
            a = int(255 * clamp(u * 2.5))
            if age > T - 0.45:
                a = int(a * (1 - clamp((age - (T - 0.45)) / 0.3)))
            img = self.T("banner", word, WHITE)
            sh = self.T("banner", word, (0, 0, 0))
            if abs(sc - 1) > 0.01:
                img = pygame.transform.smoothscale(
                    img, (int(img.get_width() * sc), int(img.get_height() * sc)))
                sh = pygame.transform.smoothscale(
                    sh, (int(sh.get_width() * sc), int(sh.get_height() * sc)))
            sh.set_alpha(int(a * 0.5))
            img.set_alpha(a)
            sub_y = cy - (14 if bn["sub"] else 0)
            self.surf.blit(sh, sh.get_rect(center=(W // 2 + 4, sub_y + 4)))
            self.surf.blit(img, img.get_rect(center=(W // 2, sub_y)))
            if bn["sub"] and age > 0.45:
                self.text("sub", bn["sub"], YELLOW, (W // 2, cy + 40),
                          "center", alpha=a)
        if 0.6 < age < 1.4:
            self.shine((0, cy - h // 2, W, h), (age - 0.6) / 0.8)

    def _milestone(self):
        ms = self.milestone
        if not ms:
            return
        age = (self._now - ms["t0"]) / 1000.0
        T = 3.6
        if age > T:
            self.milestone = None
            return
        p = ms["player"] or {}
        cy = (VIEW_Y + CARDS_Y) // 2
        w, h = 760, 132
        x = W // 2 - w // 2
        off = (1 - ease_out_cubic(age / 0.4)) * -900
        if age > T - 0.4:
            off = ease_in_cubic((age - (T - 0.4)) / 0.4) * 900
        r = pygame.Rect(x + off, cy - h // 2, w, h)
        panel = _rounded(_vgrad(r.size, (255, 214, 60), (190, 120, 10), 240), 14)
        self.surf.blit(panel, r.topleft)
        pygame.draw.rect(self.surf, WHITE, r, 3, border_radius=14)
        ph = faces.photo_path(p.get("id"))
        img = self.image(ph, (0, h + 30)) if ph else None
        if img:
            self.surf.blit(img, (r.x + 20, r.bottom - img.get_height()))
        self.text("banner", ms["label"], (90, 30, 0), (r.x + 200, r.centery - 8),
                  "midleft", shadow=False)
        self.text("big", "%s  %s (%s)" % (short_name(p.get("name")).upper(),
                                          p.get("runs", 0), p.get("balls", 0)),
                  WHITE, (r.right - 24, r.bottom - 22), "midright")
        if 0.4 < age < 1.3:
            self.shine(r, (age - 0.4) / 0.9)

    def _over_panel(self):
        op = self.over_panel
        if not op:
            return
        age = (self._now - op["t0"]) / 1000.0
        T = 6.5
        if age > T:
            self.over_panel = None
            return
        b = op["b"]
        try:
            ov = float(b["overs"] or 0)
            n = int(ov) if round((ov - int(ov)) * 10) == 0 else int(ov) + 1
        except (TypeError, ValueError):
            n = 0
        toks = dict(op["log"]).get(n, [])
        w, h = 700, 56
        y = CARDS_Y - h - 18
        off = (1 - ease_out_cubic(age / 0.45)) * -(w + 40)
        if age > T - 0.45:
            off = ease_in_cubic((age - (T - 0.45)) / 0.45) * -(w + 40)
        r = pygame.Rect(20 + off, y, w, h)
        panel = _rounded(_vgrad(r.size, (16, 22, 44), (6, 10, 22), 235), 10)
        self.surf.blit(panel, r.topleft)
        pygame.draw.rect(self.surf, YELLOW, (r.x, r.y, 8, h),
                         border_top_left_radius=10, border_bottom_left_radius=10)
        cy = r.centery
        t = self.text("mid", "END OF OVER %d" % n if n else "END OF OVER",
                      YELLOW, (r.x + 22, cy), "midleft")
        x = t.right + 16
        for i, tk in enumerate(toks[-8:]):
            bg = {"4": (40, 190, 90), "6": (150, 80, 230),
                  "W": (230, 40, 40)}.get(tk, (40, 44, 60))
            cw = max(28, 12 + 11 * len(tk))
            # chips drop in one after another
            d = clamp((age - 0.35 - i * 0.08) / 0.25)
            if d <= 0:
                continue
            cr = pygame.Rect(x, cy - 13 - int(12 * (1 - ease_out_back(d))), cw, 26)
            pygame.draw.rect(self.surf, bg, cr, border_radius=4)
            self.text("tiny", tk, WHITE, cr.center, "center", shadow=False)
            x += cw + 5
        if toks:
            x = self.text("mid", "= %d" % sum(map(_token_runs, toks)), WHITE,
                          (x + 8, cy), "midleft").right
        bw = b.get("bowler") or {}
        if bw.get("name"):
            self.text("small", "%s  %s-%s" % (short_name(bw["name"]).upper(),
                                              bw.get("wickets", 0),
                                              bw.get("runs", 0)),
                      WHITE, (r.right - 16, cy), "midright")

    def _replay(self, replaying):
        if not replaying:
            return
        r = pygame.Rect(16, VIEW_Y + 10, 146, 34)
        pulse = 0.5 + 0.5 * math.sin(self._now / 200.0)
        pygame.draw.rect(self.surf, (200, 16, 30), r, border_radius=8)
        pygame.draw.rect(self.surf, (255, int(160 + 95 * pulse), 160), r, 2,
                         border_radius=8)
        pygame.draw.circle(self.surf, WHITE, (r.x + 18, r.centery),
                           6 + int(2 * pulse))
        self.text("small", "REPLAY", WHITE, (r.x + 32, r.centery), "midleft")

    # -- cards -----------------------------------------------------------
    def _avatar(self, rect, name, code):
        x, y, w, h = rect
        col = _team_col(code)
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.ellipse(s, col + (255,), (w * 0.1, h * 0.62, w * 0.8,
                                              h * 0.7))
        pygame.draw.circle(s, (232, 196, 160, 255), (w // 2, int(h * 0.38)),
                           int(w * 0.24))
        pygame.draw.circle(s, (40, 30, 26, 255), (w // 2, int(h * 0.30)),
                           int(w * 0.24), draw_top_left=True,
                           draw_top_right=True)
        self.surf.blit(s, (x, y))
        ini = "".join(p[0] for p in str(name or "?").split()[:2]).upper()
        self.text("small", ini, WHITE, (x + w // 2, y + int(h * 0.82)),
                  "center")

    def _card(self, slot, player, team, role, bowler=False, striker=False):
        S = self.surf
        x = CARD_X[slot]
        nm = player.get("name")
        prev = self._slots.get(slot)
        if not prev or prev[0] != nm:
            self._slots[slot] = (nm, self._now if prev else -99999)
        u = (self._now - self._slots[slot][1]) / 550.0
        dy = int(70 * (1 - ease_out_cubic(u))) if u < 1 else 0
        faces.want(player, team.get("id"))

        S.blit(self._static["bcard" if bowler else "card"], (x, PANEL_Y + dy))
        frole, fage = self._focus_role
        if frole is not None and self.SLOT_OF.get(frole) == slot and \
                fage < 5600:
            pulse = 0.5 + 0.5 * math.sin(self._now / 160.0)
            pygame.draw.rect(S, (255, int(190 + 60 * pulse), 40),
                             (x - 3, PANEL_Y + dy - 3, CARD_W + 6,
                              PANEL_H + STAT_H + 8), 3, border_radius=10)
        tag = pygame.Rect(x + 246, TAG_Y + dy, 160, 27)
        pygame.draw.rect(S, (10, 14, 26), tag, border_radius=6)
        pygame.draw.rect(S, (110, 120, 140), tag, 1, border_radius=6)
        self.text("small", role, WHITE, (tag.x + 64, tag.centery), "midleft")
        fp = faces.flag_path(team.get("id"))
        fl = self.image(fp, (42, 27), "cover") if fp else None
        if fl:
            S.blit(fl, (tag.x - 50, tag.y))
        else:
            pygame.draw.rect(S, _team_col(team.get("code")),
                             (tag.x - 50, tag.y, 42, 27), border_radius=4)

        ph = faces.photo_path(player.get("id"))
        img = self.image(ph, (0, PHOTO_H)) if ph else None
        if img:
            S.blit(img, (x + 70 - img.get_width() // 2,
                         PANEL_Y + PANEL_H - PHOTO_H + dy))
        else:
            self._avatar((x + 14, PANEL_Y + PANEL_H - PHOTO_H + 10 + dy, 104,
                          PHOTO_H - 10), nm, team.get("code"))

        name = short_name(nm)
        self.text("big" if len(name) <= 13 else "mid", name, WHITE,
                  (x + 280, PANEL_Y + 22 + dy), "center")
        cy = PANEL_Y + 56 + dy
        if bowler:
            fig = "%s-%s" % (player.get("wickets", 0), player.get("runs", 0))
            r = self.text("big", fig, WHITE, (x + 222, cy), "midleft")
            self.text("big", overs_str(player.get("overs", 0)), YELLOW,
                      (r.right + 14, cy), "midleft")
        else:
            r = self.text("big", "%s" % player.get("runs", 0), WHITE,
                          (x + 232, cy), "midleft")
            self.text("big", "(%s)" % player.get("balls", 0), YELLOW,
                      (r.right + 6, cy), "midleft")
            if striker:
                p = 0.5 + 0.5 * math.sin(self._now / 260.0)
                c = (x + 392, cy)
                pygame.draw.circle(S, (255, 214, 10), c, 8 + int(4 * p), 2)
                pygame.draw.circle(S, YELLOW, c, 7)
                pygame.draw.circle(S, BLACK, c, 7, 2)

        row = pygame.Rect(x, STAT_Y + dy, CARD_W, STAT_H)
        pygame.draw.rect(S, STAT_BG, row, border_radius=6)
        if bowler:
            bits = [("Econ:", player.get("economy", 0)),
                    ("Maidens:", player.get("maidens", 0))]
        else:
            bits = [("4s:", player.get("fours", 0)),
                    ("6s:", player.get("sixes", 0)),
                    ("SR:", player.get("strikeRate", 0))]
        cx = x + 20
        for lab, val in bits:
            r = self.text("small", lab, WHITE, (cx, row.centery), "midleft")
            r = self.text("small", val, YELLOW, (r.right + 6, row.centery),
                          "midleft")
            cx = r.right + 30

    def _cards(self, b):
        if not b:
            return
        if b["striker"].get("name"):
            self._card(0, b["striker"], b["bat"], "BATSMAN", striker=True)
        if b["non_striker"].get("name"):
            self._card(1, b["non_striker"], b["bat"], "BATSMAN")
        if b["bowler"].get("name"):
            self._card(2, b["bowler"], b["bowl"], "BOWLER", bowler=True)

    # -- bottom bar ------------------------------------------------------
    def _ticker_text(self, b):
        bits = []
        if b.get("projected"):
            bits.append("PROJECTED SCORE %d" % b["projected"])
        if b.get("chase") and b.get("rrr"):
            bits.append("REQUIRED RATE %.2f" % b["rrr"])
        for lab, r, w in b.get("perf") or []:
            if r is not None:
                bits.append("%s %s/%s" % (lab, r, w or 0))
        if b["pship"]:
            bits.append("PARTNERSHIP %s (%s)" % b["pship"])
        for p in (b["striker"], b["non_striker"]):
            if p.get("name"):
                bits.append("%s %s (%s) SR %s" % (short_name(p["name"]).upper(),
                                                  p.get("runs", 0),
                                                  p.get("balls", 0),
                                                  p.get("strikeRate", 0)))
        bw = b["bowler"]
        if bw.get("name"):
            bits.append("%s %s-%s ECON %s" % (short_name(bw["name"]).upper(),
                                              bw.get("wickets", 0),
                                              bw.get("runs", 0),
                                              bw.get("economy", 0)))
        if b.get("status"):
            bits.append(b["status"])
        return "     •     ".join(bits) + "     •     "

    def _bottom(self, b, dt):
        S = self.surf
        y = BOTTOM_Y
        bar = pygame.Rect(0, y, W, BOTTOM_H)
        s = pygame.Surface(bar.size, pygame.SRCALPHA)
        s.fill((6, 8, 18, 215))
        S.blit(s, bar.topleft)
        x0 = 0
        if b and b.get("last_wicket"):
            pill = pygame.Rect(-14, y, 470, BOTTOM_H)
            pygame.draw.rect(S, (180, 12, 24), pill, border_radius=14)
            r = self.text("small", "LAST WICKET", WHITE, (12, bar.centery),
                          "midleft")
            self.text("small", b["last_wicket"][:26], YELLOW,
                      (r.right + 10, bar.centery), "midleft")
            x0 = pill.right + 6
        live = pygame.Rect(W - 120, y, 120, BOTTOM_H)
        tk = pygame.Rect(x0 + 6, y, live.x - x0 - 12, BOTTOM_H)
        if b and tk.w > 40:
            txt = self._ticker_text(b)
            if txt != self._ticker_txt:
                self._ticker_txt = txt
                self._ticker_img = self.T("small", txt, (225, 232, 245))
            img = self._ticker_img
            self._ticker_x = (self._ticker_x + 70 * dt) % img.get_width()
            sub = S.subsurface(tk)
            x = -self._ticker_x
            while x < tk.w:
                sub.blit(img, (x, (BOTTOM_H - img.get_height()) // 2))
                x += img.get_width()
        p = 0.5 + 0.5 * math.sin(self._now / 300.0)
        pygame.draw.rect(S, (200, 16, 30), live.inflate(-14, -6),
                         border_radius=6)
        pygame.draw.circle(S, (255, int(120 + 135 * p), int(120 + 135 * p)),
                           (live.x + 22, live.centery), 5)
        self.text("small", "LIVE", WHITE, (live.x + 34, live.centery),
                  "midleft")
        if CHANNEL:
            self.text("small", CHANNEL, WHITE, (W - 130, y - 14), "midright")

    # -- frame -----------------------------------------------------------
    # -- who is who --------------------------------------------------------
    SLOT_OF = {"striker": 0, "nonstriker": 1, "bowler": 2}

    def _focused(self, st):
        """(role, age ms) of the player the commentator is talking about."""
        f = st.get("focus")
        if not f or f[0] not in self.SLOT_OF:
            return None, 1e9
        return f[0], self._now - f[1]

    def _tags(self, tags, b):
        """AR name tags pinned above the players in the 3D picture - the way
        a broadcast labels who is who."""
        if not tags or not b:
            return
        placed = []
        for role, name, x, y, depth in sorted(tags, key=lambda t: -t[4]):
            if not (VIEW_Y + 26 < y < CARDS_Y - 4) or not (40 < x < W - 40):
                continue
            team = b["bowl"] if role == "bowler" else b["bat"]
            label = short_name(name).upper()
            img = self.T("small", label, WHITE)
            w, h = img.get_width() + 26, 26
            r = pygame.Rect(0, 0, w, h)
            r.midbottom = (int(x), int(y) - 8)
            for p in placed:                       # do not stack on a tag
                if r.colliderect(p):
                    r.bottom = p.top - 4
            placed.append(r)
            first = self._tag_t.setdefault(role + name, self._now)
            a = int(225 * clamp((self._now - first) / 350.0))
            box = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.rect(box, (8, 12, 24, a), box.get_rect(),
                             border_radius=6)
            pygame.draw.rect(box, _team_col(team.get("code")) + (a,),
                             (0, 0, 6, h), border_top_left_radius=6,
                             border_bottom_left_radius=6)
            self.surf.blit(box, r.topleft)
            self.surf.blit(img, img.get_rect(midleft=(r.x + 14, r.centery)))
            pygame.draw.polygon(self.surf, (8, 12, 24),
                                [(r.centerx - 6, r.bottom),
                                 (r.centerx + 6, r.bottom),
                                 (r.centerx, r.bottom + 7)])
            if role == "striker":
                pygame.draw.circle(self.surf, YELLOW, (r.right - 9, r.centery),
                                   4)

    def _focus_card(self, st, b):
        """The player being talked about: real photo, name and his numbers,
        sliding in above the cards."""
        role, age = self._focused(st)
        if not role or not b or age > 5600:
            return
        p = {"striker": b["striker"], "nonstriker": b["non_striker"],
             "bowler": b["bowler"]}[role]
        if not p.get("name"):
            return
        team = b["bowl"] if role == "bowler" else b["bat"]
        if role == "bowler":
            line = "BOWLING  %s-%s-%s  ECON %s" % (
                overs_str(p.get("overs", 0)), p.get("runs", 0),
                p.get("wickets", 0), p.get("economy", 0))
        else:
            line = "BATTING  %s (%s)  4s %s  6s %s" % (
                p.get("runs", 0), p.get("balls", 0), p.get("fours", 0),
                p.get("sixes", 0))
        w, h = 480, 86
        slide = 1 - ease_out_cubic(age / 450.0)
        if age > 5150:
            slide = ease_in_cubic((age - 5150) / 450.0)
        r = pygame.Rect(W - w - 18 + int(slide * (w + 40)),
                        CARDS_Y - h - 24, w, h)
        panel = _rounded(_vgrad(r.size, (18, 26, 52), (6, 10, 24), 238), 10)
        self.surf.blit(panel, r.topleft)
        pygame.draw.rect(self.surf, _team_col(team.get("code")),
                         (r.x, r.y, 8, h), border_top_left_radius=10,
                         border_bottom_left_radius=10)
        ph = faces.photo_path(p.get("id"))
        img = self.image(ph, (0, h + 30)) if ph else None
        tx = r.x + 24
        if img:
            self.surf.blit(img, (r.x + 16, r.bottom - img.get_height()))
            tx = r.x + 30 + img.get_width()
        self.text("big", (p.get("name") or "").upper()[:20], WHITE,
                  (tx, r.y + 26), "midleft")
        self.text("small", line, YELLOW, (tx, r.y + 62), "midleft")
        if 300 < age < 1100:
            self.shine(r, (age - 300) / 800.0)

    def draw(self, st, replaying, now_ms, dt, tags=()):
        self._now = now_ms
        b = st["board"]
        self._focus_role = self._focused(st)
        self._events(st, b)
        self.surf.fill((0, 0, 0, 0))
        self._top(b, st["event"])
        self._strips(b, st["overs_log"])
        self._particles(dt)
        self._banner()
        self._milestone()
        self._over_panel()
        self._replay(replaying)
        self._tags(tags, b)
        self._focus_card(st, b)
        self._cards(b)
        self._bottom(b, dt)
        return self.surf
