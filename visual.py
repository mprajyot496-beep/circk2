# -*- coding: utf-8 -*-
"""
visual.py  -  the broadcast window: a real 3D ground (stadium3d.py) with a
TV-style live scoreboard over it, moving in step with the commentary.

    ┌───────────────────────────────────────────────────────────────┐
    │ [flag] AUS-A  75-3 (13.0)   │  SIX  │   IND-A  BOWL  [flag]    │  top band
    │ CRR 5.77          P'SHIP 45 (52)          AUS-A OPT TO BAT     │  green
    │ RECENT  OVER 12 1 1 0 1 1 1 = 5   OVER 13 0 0 1 1 1 4 = 7     │  red
    │        3D: the delivery being described, broadcast cameras     │
    │ [photo] L Scott   │ [photo] N McSweeney │ [photo] A Roy        │  cards
    │ 4s 1  6s 0  SR 66 │ 4s 4  6s 0  SR 90   │ Econ 4.67            │
    │ LAST WICKET  JOSH PHILIPPE 2(6)                                │
    └───────────────────────────────────────────────────────────────┘

In step with the voice
----------------------
The feed is polled every few seconds and the voice is a few seconds behind
it, so nothing changes when data arrives. Each delivery is staged with two
cues the Speaker fires when the lines become audible:

    start   the commentator begins the ball      -> 3D run-up and delivery
    result  he says what happened ("छक्का!!")     -> the 3D shot, the score,
            the RECENT chip and the centre box, together

The 3D world hurries the delivery if the voice reaches the result first, so
the picture never trails the words and the board never runs ahead of the
picture.

Panda3D owns the window; the scoreboard is drawn with pygame into a
transparent surface and shown as a full-screen layer over the 3D. The poll
loop and the Speaker run on other threads; Scene is the lock-guarded
handover.
"""
import os
import time
import re
import threading
from collections import OrderedDict, deque

import pygame

import faces
import play as PL

W, H = 1280, 720                       # 720p: what a YouTube stream wants

# Your channel name, drawn small in the corner. Leave "" for none.
CHANNEL = os.environ.get("CHANNEL_NAME", "")
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")


def bundled_font(name):
    p = os.path.join(FONT_DIR, name)
    return p if os.path.exists(p) else None

VIEW_Y = 180                           # the 3D shows between the strips...
CARDS_Y = 532                          # ...and the player cards

WHITE = (250, 250, 252)
YELLOW = (255, 214, 10)
BLACK = (8, 8, 12)
TOP_A, TOP_B = (214, 26, 170), (140, 18, 150)     # magenta band
SCORE_A, SCORE_B = (70, 40, 226), (36, 22, 150)   # blue score box
GREEN_A, GREEN_B = (20, 120, 44), (10, 74, 26)
RED_A, RED_B = (196, 22, 34), (120, 10, 20)
CARD_A, CARD_B = (16, 88, 96), (10, 40, 56)       # batter card
BOWL_A, BOWL_B = (26, 52, 104), (12, 24, 56)      # bowler card
STAT_BG = (6, 22, 34)

# What the centre box shows, and in what colour, per ball kind.
EVENT = {
    "six": ("SIX", (190, 120, 255)), "four": ("FOUR", (60, 230, 120)),
    "wicket": ("WICKET", (255, 70, 70)), "dot": ("DOT BALL", WHITE),
    "one": ("1 RUN", WHITE), "two": ("2 RUNS", WHITE),
    "three": ("3 RUNS", WHITE), "runs": ("RUNS", WHITE),
    "wide": ("WIDE", YELLOW), "noball": ("NO BALL", YELLOW),
    "bye": ("BYE", YELLOW), "legbye": ("LEG BYE", YELLOW),
    "other": ("BALL", WHITE),
}
FLASH = {"four": ((60, 230, 120), "FOUR!"), "six": ((190, 120, 255), "SIX!"),
         "wicket": ((255, 70, 70), "WICKET!")}

# Chip in the RECENT strip, per ball kind.
TOKEN = {"dot": "0", "one": "1", "two": "2", "three": "3", "four": "4",
         "six": "6", "wicket": "W", "wide": "Wd", "noball": "Nb",
         "bye": "B", "legbye": "Lb"}

# Team colours for the drawn avatar / missing flag. First three letters of
# the short name; anything else gets slate.
TEAM_COL = {"IND": (24, 96, 220), "AUS": (230, 180, 20), "ENG": (30, 60, 140),
            "PAK": (20, 120, 60), "SA": (20, 110, 70), "NZ": (30, 30, 34),
            "SL": (30, 60, 160), "WI": (128, 20, 40), "BAN": (0, 106, 78),
            "AFG": (30, 80, 200), "ZIM": (200, 30, 40), "IRE": (40, 150, 70)}


# ── The board: everything the overlay shows, as one snapshot ───────────────
def team_code(t):
    """'AUSA' -> 'AUS-A', 'INDU19' -> 'IND-U19': how a broadcast writes it."""
    s = str(t.get("shortName") or t.get("name") or "").upper()
    nm = str(t.get("name") or "")
    if "U19" in s and "-U19" not in s:
        return s.replace("U19", "-U19")
    if nm.endswith(" A") and s.endswith("A") and len(s) > 3:
        return s[:-1] + "-A"
    if "women" in nm.lower() and s.endswith("W") and len(s) >= 3:
        return s[:-1] + "-W"
    return s


def short_name(nm):
    """'Liam Scott' -> 'L Scott', the way score graphics print names."""
    parts = str(nm or "").split()
    if len(parts) < 2:
        return str(nm or "")
    return "%s %s" % (parts[0][0], " ".join(parts[1:]))


def _last_wicket(lw):
    """'Liam Scott  c X b Y 15(23)  - 77/4 in 13.3 ov.' -> 'LIAM SCOTT 15(23)'"""
    if not isinstance(lw, str) or not lw.strip():
        return ""
    nm = re.split(r"\s+(?:c |b |lbw|run out|st |hit |c&b)", lw.strip())[0]
    m = re.search(r"(\d+\(\d+\))", lw)
    return ("%s %s" % (nm.strip(), m.group(1) if m else "")).strip().upper()


def make_board(sit, header):
    """Snapshot of the live state for the overlay. Taken at poll time, shown
    when the voice reaches the ball it belongs to."""
    mini = sit.mini or {}
    bat_id = (mini.get("batTeam") or {}).get("teamId")
    t1, t2 = header.get("team1") or {}, header.get("team2") or {}
    bat, bowl = (t2, t1) if t2.get("id") == bat_id else (t1, t2)

    bowl_score = ""
    sd = mini.get("matchScoreDetails") or {}
    for inn in sd.get("inningsScoreList") or []:
        if inn.get("batTeamId") == bowl.get("id"):
            bowl_score = "%s-%s" % (inn.get("score", 0), inn.get("wickets", 0))
            break

    if sit.is_chase:
        status = ("NEED %d OFF %d" % (sit.need, sit.balls_left)
                  if sit.balls_left else "NEED %d RUNS" % sit.need)
    else:
        status = str(sd.get("customStatus") or header.get("status") or "")
    ps = mini.get("partnerShip") or {}
    # recent-form figures for the ticker: "Last 5 overs 21/1"
    perf = []
    for p in mini.get("latestPerformance") or []:
        if isinstance(p, dict) and p.get("label"):
            perf.append((str(p["label"]).upper(), p.get("runs"), p.get("wkts")))
    projected = None
    if not sit.is_chase and sit.max_overs and sit.crr and \
            4 <= sit.overs_done <= sit.max_overs - 2:
        projected = int(round((sit.runs + float(sit.crr) * sit.balls_left / 6.0)
                              / 5.0) * 5)
    return {
        "perf": perf, "projected": projected,
        "bat": {"id": bat.get("id"), "code": team_code(bat) or (sit.team or ""),
                "name": bat.get("name") or ""},
        "bowl": {"id": bowl.get("id"), "code": team_code(bowl),
                 "name": bowl.get("name") or "", "score": bowl_score},
        "runs": sit.runs, "wkts": sit.wickets, "overs": sit.overs_raw,
        "crr": sit.crr, "rrr": sit.rrr, "chase": sit.is_chase,
        "target": mini.get("target"),
        "pship": (ps.get("runs"), ps.get("balls")) if ps.get("runs") else None,
        "status": status.upper(),
        "striker": dict(sit.striker or {}),
        "non_striker": dict(sit.non_striker or {}),
        "bowler": dict(sit.bowler or {}),
        "other_bowler": dict(sit.other_bowler or {}),
        "last_wicket": _last_wicket(mini.get("lastWicket")),
        "phase": sit.phase,
        "desc": str(header.get("matchDescription") or "").upper(),
        "fmt": sit.fmt,
    }


def board_for_ball(board, ball):
    """The board as it should read when the voice reaches this ball.

    On the last ball of an over the feed has already moved on: its current
    bowler is the NEXT over's, and the one who bowled this ball sits in the
    'other bowler' slot. Show the man the commentator is talking about; the
    new bowler appears with the over break."""
    if not board:
        return board
    bw, ob = board.get("bowler") or {}, board.get("other_bowler") or {}
    name = str(ball.get("bowler") or "").lower().split()
    if name and ob.get("name") and \
            str(ob["name"]).lower().split()[-1:] == name[-1:] and \
            str(bw.get("name") or "").lower().split()[-1:] != name[-1:]:
        board = dict(board, bowler=ob)
    return board


def overs_str(v):
    """18 -> '18.0', 13.3 -> '13.3' - a scoreboard always shows the ball."""
    try:
        return "%.1f" % float(v)
    except (TypeError, ValueError):
        return str(v or "0.0")


def _token_runs(tok):
    """Runs a RECENT chip stands for: '4' -> 4, 'Wd' -> 1, 'W' -> 0."""
    digits = re.findall(r"\d+", tok)
    if digits:
        return sum(int(d) for d in digits)
    return 1 if tok.lower() in ("wd", "nb") else 0


class Scene:
    """What the overlay shows. Written by the commentary loop and by speech
    cues; read once a frame by the renderer."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self, title=""):
        with self._lock:
            self.title = title
            self.board = None
            self.overs_log = OrderedDict()     # over number -> [tokens]
            self.cmds = deque()                # commands for the 3D world
            self.flash = None                  # (kind, started_ms)
            self.event = ("LIVE", (255, 70, 70), 0)
            self.focus = None                  # (role, started_ms)
            self._pending = 0                  # staged balls not yet spoken
            self._deferred = None
            self._last_bowler = None           # who bowled the ball last told

    # -- immediate --------------------------------------------------------
    def show(self, board):
        with self._lock:
            self.board = board

    def _fit(self, board):
        """Keep the bowler card on the man who bowled the ball the voice
        last described. The feed's bowler slots are unreliable across an
        over boundary; until the next ball names its bowler, the card
        keeps the last one rather than jumping to a guess."""
        if not board or not self._last_bowler:
            return board
        b = board_for_ball(board, {"bowler": self._last_bowler})
        cur = (self.board or {}).get("bowler") or {}
        if not _same(b["bowler"].get("name"), self._last_bowler) and                 _same(cur.get("name"), self._last_bowler):
            b = dict(b, bowler=cur)
        return b

    def defer(self, board):
        """A board change with no ball behind it (new batter, bowler change):
        held until line_started() sees the voice has caught up, or the board
        would run ahead of the commentary."""
        with self._lock:
            self._deferred = board

    def seed_recent(self, recent, overs_raw):
        """Fill the RECENT strip from the feed's string when we join, e.g.
        '... 1 1 0 1 1 1 | 0 0 1 1 1 4'. After this it is built from the
        balls as they are spoken."""
        if not recent:
            return
        try:
            ov = float(overs_raw or 0)
        except (TypeError, ValueError):
            return
        current = int(ov) + (1 if round((ov - int(ov)) * 10) else 0)
        groups = [g.split() for g in str(recent).replace("...", "").split("|")]
        groups = [g for g in groups if g]
        with self._lock:
            if self.overs_log:
                return
            for k, g in enumerate(reversed(groups)):
                if current - k > 0:
                    self.overs_log[current - k] = g
            self.overs_log = OrderedDict(sorted(self.overs_log.items()))

    def seed_balls(self, balls):
        """Fill RECENT from the deliveries already in the feed when we join.
        Each carries its ballMetric, so this is exact - better than the
        feed's recentOvsStats string, which often holds only this over."""
        log = OrderedDict()
        for b in balls:
            try:
                over = int(float(b.get("metric"))) + 1
            except (TypeError, ValueError):
                continue
            tok = TOKEN.get(b["kind"]) or (str(b.get("runs") or "") or "•")
            log.setdefault(over, []).append(tok)
        if not log:
            return False
        with self._lock:
            self.overs_log = OrderedDict(sorted(log.items())[-3:])
        return True

    # -- staged: fired by the Speaker when the voice gets there ------------
    def stage_ball(self, ball, board=None, log=True, info=None):
        """Three cues for one delivery, (start, shot, land):

        start   the voice begins the ball    -> the 3D bowler runs in
        shot    the voice reaches the stroke  -> the 3D shot is played
                (for a big moment this is the build-up, "ऊपर... और ऊपर...")
        land    the voice announces it       -> the board, the RECENT chip
                and the centre box change ("छक्का!!")

        log=False for a ball the RECENT strip already has (the one we join
        on is in the seeded history)."""
        with self._lock:
            self._pending += 1

        def start():
            with self._lock:
                if info is not None:
                    self.cmds.append(("deliver", info))

        def result():
            with self._lock:
                self._pending = max(0, self._pending - 1)
                self._last_bowler = ball.get("bowler") or self._last_bowler
                if board is not None:
                    self.board = self._fit(board)
                kind = ball["kind"]
                tok = TOKEN.get(kind) or (str(ball.get("runs") or "") or "•")
                over = None
                m = ball.get("metric")
                if m is not None:
                    try:
                        over = int(float(m)) + 1
                    except (TypeError, ValueError):
                        over = None
                if over is None:
                    over = next(reversed(self.overs_log), 1) \
                        if self.overs_log else 1
                if log:
                    self.overs_log.setdefault(over, []).append(tok)
                    self.overs_log = OrderedDict(
                        sorted(self.overs_log.items())[-3:])
                label, col = EVENT.get(kind, EVENT["other"])
                if kind == "runs":
                    label = "%s RUNS" % ball.get("runs")
                self.event = (label, col, pygame.time.get_ticks())
        def shot():
            with self._lock:
                self.cmds.append(("result", info))
        return start, shot, result

    def line_started(self, kind):
        """Called as each spoken line starts. A deferred board (new batter,
        new bowler) waits until the commentator is past the deliveries -
        into small talk or a break - and appears then."""
        if kind in ("filler", "over", "break", "note"):
            with self._lock:
                if self._pending == 0 and self._deferred is not None:
                    self.board = self._fit(self._deferred)
                    self._deferred = None

    def flash_cue(self, kind):
        def cue():
            with self._lock:
                if kind in FLASH:
                    self.flash = (kind, pygame.time.get_ticks())
                    self.cmds.append(("cheer", kind))
        return cue

    def focus_cue(self, role):
        """The voice is talking about someone: the camera cuts to them and
        their card comes up - fired when the line becomes audible."""
        def cue():
            with self._lock:
                self.cmds.append(("focus", role))
                self.focus = (role, pygame.time.get_ticks())
        return cue

    def event_cue(self, label, colour=WHITE, world=None, board=None):
        """Centre-box label (and optionally a 3D camera event, and the board
        as it stands now) on a line."""
        def cue():
            with self._lock:
                self.event = (label, colour, pygame.time.get_ticks())
                if world:
                    self.cmds.append(("event", world))
                if board is not None and not self._pending:
                    self.board = self._fit(board)
        return cue

    def snapshot(self):
        with self._lock:
            cmds = list(self.cmds)
            self.cmds.clear()
            return {"board": self.board, "overs_log": list(
                        (k, list(v)) for k, v in self.overs_log.items()),
                    "event": self.event, "cmds": cmds, "flash": self.flash,
                    "focus": self.focus,
                    "title": self.title}


# ── What the 3D world needs to know about a delivery ───────────────────────
def _same(a, b):
    a, b = str(a or "").lower().split(), str(b or "").lower().split()
    return bool(a and b and a[-1] == b[-1])


def people(board):
    """Who is where, and how they play - from the board and the players'
    Cricbuzz profiles (fetched by faces.py)."""
    st, ns, bw = board["striker"], board["non_striker"], board["bowler"]
    left, _ = faces.style(st.get("id"))
    nleft, _ = faces.style(ns.get("id"))
    _, bstyle = faces.style(bw.get("id"))
    return {
        "bat_code": board["bat"]["code"], "bowl_code": board["bowl"]["code"],
        "fmt": board.get("fmt") or "ODI", "phase": board.get("phase"),
        "striker": st.get("name"), "nonstriker": ns.get("name"),
        "bowler": bw.get("name"), "bat_left": left,
        "spinner": faces.is_spinner(bstyle),
        "bowl_left": "left" in bstyle,
        "styles": {"striker": {"left": left, "skin": faces.skin(st.get("id")),
                               "hair": faces.hair(st.get("id"))},
                   "nonstriker": {"left": nleft,
                                  "skin": faces.skin(ns.get("id")),
                                  "hair": faces.hair(ns.get("id"))},
                   "bowler": {"left_arm": "left" in bstyle,
                              "skin": faces.skin(bw.get("id")),
                              "hair": faces.hair(bw.get("id"))}},
    }


def delivery_info(ball, board):
    """Everything the 3D needs to play this ball the way the feed says it
    went: length, line, movement, the stroke, where it went, how out."""
    b = dict(board)
    st, ns = board["striker"], board["non_striker"]
    # the feed names the batter on strike for THIS ball - trust it
    if _same(ns.get("name"), ball["bat"]) and not _same(st.get("name"),
                                                        ball["bat"]):
        b["striker"], b["non_striker"] = ns, st
    if not _same(board["bowler"].get("name"), ball["bowler"]):
        b["bowler"] = {"name": ball["bowler"]}
    info = people(b)
    play = PL.parse_play(ball.get("detail"))
    how = None
    if ball["kind"] == "wicket":
        how = PL.parse_dismissal(ball.get("text"))["how"]
    info.update({
        "kind": ball["kind"], "runs": ball.get("runs"),
        "detail": ball.get("detail") or "", "text": ball.get("text") or "",
        "length": play["length"], "line": play["line"], "move": play["move"],
        "act": play["act"], "air": play["air"], "field": play["field"],
        "how": how,
    })
    return info


# ── Drawing helpers ─────────────────────────────────────────────────────────
def _vgrad(size, top, bot, alpha=255):
    """Vertical gradient surface - every panel on a broadcast has one."""
    w, h = size
    s = pygame.Surface((1, max(1, h)), pygame.SRCALPHA)
    for y in range(max(1, h)):
        u = y / max(1, h - 1)
        s.set_at((0, y), tuple(int(top[i] + (bot[i] - top[i]) * u)
                               for i in range(3)) + (alpha,))
    return pygame.transform.scale(s, (max(1, w), max(1, h)))


def _rounded(surf, radius):
    """Clip a surface to a rounded rectangle."""
    mask = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(),
                     border_radius=radius)
    out = surf.copy()
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return out


def _team_col(code):
    return TEAM_COL.get(str(code or "")[:3], TEAM_COL.get(
        str(code or "")[:2], (70, 84, 110)))


# ── The window ──────────────────────────────────────────────────────────────
class Visual:
    """Panda3D window: the 3D world, with the scoreboard layered on top."""

    def __init__(self, scene, caption="Live Hindi Cricket Commentary",
                 headless=False):
        # onnxruntime (the voice, the photo cut-outs) must load before
        # Panda3D: Panda3D ships an older MSVCP140.dll, and once that is in
        # the process onnxruntime's DLL refuses to initialise.
        try:
            import onnxruntime  # noqa: F401
        except Exception:
            pass
        from panda3d.core import (loadPrcFileData, Texture, CardMaker,
                                  TransparencyAttrib)
        cfg = ["win-size %d %d" % (W, H), "window-title %s" % caption,
               "audio-library-name null", "framebuffer-multisample 1",
               "multisamples 4", "sync-video 1", "win-fixed-size 1",
               "notify-level warning", "default-directnotify-level warning"]
        if headless or os.environ.get("CRICK_HEADLESS"):
            cfg.append("window-type offscreen")
        if os.environ.get("CRICK_STREAM"):
            # a virtual display on a GPU-less server, grabbed by ffmpeg:
            # pinned to the corner, no MSAA (software GL), capped frame rate
            # so the renderer leaves CPU for the voice and the encoder
            fps = os.environ.get("CRICK_FPS", "25")
            cfg = [c for c in cfg if not c.startswith(
                ("framebuffer-multisample", "multisamples", "sync-video"))]
            cfg += ["win-origin 0 0", "undecorated 1", "sync-video 0",
                    "framebuffer-multisample 0", "multisamples 0",
                    "clock-mode limited", "clock-frame-rate %s" % fps]
        loadPrcFileData("", "\n".join(cfg))
        from direct.showbase.ShowBase import ShowBase
        import stadium3d

        self.scene = scene
        self.running = True
        self.base = ShowBase()
        self.world = stadium3d.World(self.base, band=(VIEW_Y, CARDS_Y),
                                     size=(W, H))
        self.base.accept("escape", self.close)
        self.base.accept("q", self.close)
        self.base.accept("window-event", self._window_event)

        # the scoreboard layer
        self.screen = pygame.Surface((W, H), pygame.SRCALPHA)
        self.tex = Texture("overlay")
        self.tex.setup2dTexture(W, H, Texture.T_unsigned_byte,
                                Texture.F_rgba8)
        cm = CardMaker("overlay")
        cm.setFrameFullscreenQuad()
        # pygame rows run top-down, a texture's bottom-up: flip in the UVs
        # instead of flipping 3.7 MB of pixels every frame
        cm.setUvRange((0, 1), (1, 0))
        card = self.base.render2d.attachNewNode(cm.generate())
        card.setTexture(self.tex)
        card.setTransparency(TransparencyAttrib.MAlpha)

        if not pygame.font.get_init():
            pygame.font.init()
        import overlay
        self.overlay = overlay.Overlay()
        s = self.overlay.surf
        self._fast_upload = (s.get_masks()[:3] == (0xff0000, 0xff00, 0xff)
                             and s.get_pitch() == W * 4)
        self._last = time.time()
        self._ov_at = 0.0
        self._who = None

    def close(self):
        self.running = False

    def _window_event(self, win):
        props = win.getProperties() if win else None
        if props is not None and not props.getOpen():
            self.running = False

    # -- loop ------------------------------------------------------------
    def _apply(self, st):
        """Hand the speech-timed commands to the 3D world."""
        w = self.world
        for cmd, arg in st["cmds"]:
            if cmd == "deliver" and arg:
                w.deliver(arg)
            elif cmd == "result":
                w.result(arg)
            elif cmd == "cheer":
                w.cheer()
            elif cmd == "event":
                w.event(arg)
            elif cmd == "focus":
                w.focus(arg)
        b = st["board"]
        if b:
            who = (b["bat"]["code"], b["bowl"]["code"],
                   b["striker"].get("name"), b["non_striker"].get("name"),
                   b["bowler"].get("name"), b.get("phase"))
            if who != self._who and w.play is None:
                self._who = who
                p = people(b)
                w.bat_code, w.bowl_code = p["bat_code"], p["bowl_code"]
                w.set_teams(p["bat_code"], p["bowl_code"], p["fmt"])
                w.set_people(p["striker"], p["nonstriker"], p["bowler"],
                             p["styles"])
                w.set_field(p["phase"] or "middle", p["bat_left"],
                            p["spinner"])

    def draw_overlay(self, st, dt):
        w = self.world
        # AR name tags between balls and in replays - not over a live ball
        tags = w.tag_points() if (w.play is None or w.replaying) else []
        surf = self.overlay.draw(st, w.replaying, pygame.time.get_ticks(), dt,
                                 tags)
        # pygame's 32-bit surface is BGRA in memory - Panda's own texture
        # order - so the raw buffer goes up as-is. Measured: 28.6 ms/frame
        # for tobytes(flip)+convert, which held the window to ~24 fps.
        if self._fast_upload:
            self.tex.setRamImage(surf.get_buffer().raw)
        else:
            # unusual pixel layout: convert (the UVs already do the flip)
            self.tex.setRamImageAs(pygame.image.tobytes(surf, "RGBA"),
                                   "RGBA")

    def pump(self):
        """One frame. Returns False when the window is closed."""
        now = time.time()
        dt, self._last = now - self._last, now
        st = self.scene.snapshot()
        self._apply(st)
        self.world.update(dt)
        if now - self._ov_at >= 1 / 30.0:
            self.draw_overlay(st, min(0.1, now - self._ov_at))
            self._ov_at = now
        self.base.taskMgr.step()
        if os.environ.get("CRICK_STREAM"):
            # on a server, say how fast the software renderer keeps up
            if not hasattr(self, "_fps_at"):
                self._fps_at, self._frames = now, 0
            self._frames += 1
            if now - self._fps_at >= 30:
                print("  [fps] %.1f" % (self._frames / (now - self._fps_at)),
                      flush=True)
                self._fps_at, self._frames = now, 0
        return self.running
