# -*- coding: utf-8 -*-
"""
situation.py  -  what is actually happening, and therefore what to talk about.

This is the part that stops the commentary sounding like a loop. Nothing is
chosen on a fixed rotation. Instead:

  Situation   reads the live miniscore and derives real context - match phase
              (powerplay / middle / death), runs in the over just bowled,
              whether this is a chase and how much pressure it is under,
              and which milestones were just crossed.

  Director    picks the next topic from that context, weighted, and refuses to
              reuse a topic or a sentence it used recently. Over breaks get
              their own scripted-but-varied block, because a 30-40 second gap
              needs real content, not more small talk.

Templates live in phrasing.py; the numbers and names come from the feed.
"""
import re
import time
import random
from collections import deque

import phrasing as P
import play as PL
import talk as TK

MAX_OVERS = {"T20": 20, "T20I": 20, "IT20": 20, "ODI": 50, "ODM": 50,
             "ONE-DAY": 50}


class Pick:
    """Chooses a line from a bank without repeating itself.

    Every exact line has a 15-minute gap before it may be said again. A
    small bank ("मज़ा आने वाला है भाई, बने रहिए" and its 4 siblings) used to
    run out within minutes and then repeat at random - exactly what makes a
    voice sound like a loop. Now:
        pick(pool)   essential lines: if the whole bank is on cooldown, the
                     line said LONGEST ago is reused, never a random one
        maybe(pool)  optional lines: '' when the whole bank is on cooldown,
                     so the line is simply skipped
    """

    def __init__(self, memory=250, min_gap=900.0):
        self._seen = deque(maxlen=memory)
        self._t = {}
        self.min_gap = min_gap

    def _fresh(self, pool):
        now = time.time()
        return [p for p in pool if p not in self._seen
                and now - self._t.get(p, -1e9) >= self.min_gap]

    def _use(self, p):
        self._seen.append(p)
        self._t[p] = time.time()
        return p

    def __call__(self, pool):
        if not pool:
            return ""
        fresh = self._fresh(pool)
        if fresh:
            return self._use(random.choice(fresh))
        return self._use(min(pool, key=lambda q: self._t.get(q, -1e9)))

    def maybe(self, pool):
        fresh = self._fresh(pool or [])
        return self._use(random.choice(fresh)) if fresh else ""


# ── Parsing a Cricbuzz delivery ─────────────────────────────────────────────
_BALL_RE = re.compile(
    r"^(?P<bowler>[A-Z][A-Za-z.'\-]*(?:\s+[A-Za-z.'\-]+){0,2})"
    r"\s+to\s+"
    r"(?P<bat>[A-Z][A-Za-z.'\-]*(?:\s+[A-Za-z.'\-]+){0,2}),\s*(?P<rest>.+)$",
    re.S)

MAX_NOTE = 400


# Cricbuzz is inconsistent about how it announces a dismissal:
#   "THATS OUT!! Caught!!"            (interim line, no apostrophe)
#   "out Caught by K Richardson!!"    (the full line, with a WICKET event)
#   "bowled", "lbw", "stumped", "run out", "hit wicket"
# Missing any one of these announces a wicket as a dot ball, so this checks
# the leading word instead of relying on an escape-prone regex.
_OUT_WORDS = ("out", "thats", "that", "bowled", "lbw", "caught", "stumped",
              "retired", "run")


def _is_out(first_clause):
    """True when this clause is announcing a dismissal."""
    head = first_clause.strip().lower()
    if not head:
        return False
    word = re.split(r'[^a-z]+', head, maxsplit=1)[0]
    if word in ("thats", "that", "run"):
        return "out" in head          # "thats out", "run out"
    return word in _OUT_WORDS


def parse_ball(text, events=(), metric=None):
    """Structured delivery, or None when the line is a match note.

    `metric` is Cricbuzz's ballMetric (13.3 = 14th over, 3rd ball), when the
    feed gives one."""
    if len(text) > MAX_NOTE:
        return None
    m = _BALL_RE.match(text)
    if not m:
        return None
    clauses = [c.strip() for c in m.group("rest").split(",") if c.strip()]
    first = (clauses[0] if clauses else "").lower()
    ev = " ".join(events).lower()

    if "wicket" in ev or _is_out(first):
        kind, runs = "wicket", 0
    elif "six" in ev or first.startswith("six"):
        kind, runs = "six", 6
    elif "four" in ev or first.startswith("four"):
        kind, runs = "four", 4
    elif first.startswith("no ball"):
        kind, runs = "noball", 1
    elif "wide" in first:
        kind, runs = "wide", 1
    elif re.match(r"^\d+\s*lb", first):
        kind, runs = "legbye", 1
    elif re.match(r"^\d+\s*b\b", first):
        kind, runs = "bye", 1
    elif first.startswith("no run"):
        kind, runs = "dot", 0
    else:
        m2 = re.match(r"^(\d+)\s+runs?", first)
        if m2:
            r = int(m2.group(1))
            kind = {1: "one", 2: "two", 3: "three"}.get(r, "runs")
            runs = r
        else:
            kind, runs = "other", 0

    return {"bowler": m.group("bowler").strip(), "bat": m.group("bat").strip(),
            "kind": kind, "runs": runs, "detail": ", ".join(clauses[1:]),
            "text": text, "metric": metric}


def find_position(detail):
    """The fielding position the commentary names, in Hindi, or None.

    Longest key first, so "deep mid-wicket" is not matched as "mid-wicket".
    These come from phrasing.FIELD_HI rather than the transliterator, which
    turns "cover" and "gully" into nonsense.
    """
    low = (detail or "").lower()
    if not low:
        return None
    for eng in sorted(P.FIELD_HI, key=len, reverse=True):
        if eng in low:
            return P.FIELD_HI[eng]
    return None


def dismissed_for(text):
    """Runs the dismissed batsman made, from "... 31(26)" in the feed text."""
    m = re.search(r"(\d+)\s*\((\d+)\)", text or "")
    return (m.group(1), m.group(2)) if m else (None, None)


# ── Live match context ──────────────────────────────────────────────────────
class Situation:
    """Derived state. update() every poll; the rest is read-only helpers."""

    def __init__(self):
        self.mini = {}
        self.header = {}
        self.fmt = "?"
        self.max_overs = None

        self.over = -1                  # completed overs
        self.over_runs = 0              # runs in the over just completed
        self.over_wkts = 0
        self._over_mark = None          # (runs, wkts) at last over boundary
        self.over_just_ended = False
        # We usually join mid-over, so the first over we see end was only
        # partly watched - its run count is not "runs in the over".
        self._joined_mid_over = False
        self.over_partial = False
        self._innings = None

        self._bat_seen = {}             # name -> last known runs
        self.new_milestones = []

    # -- ingest ----------------------------------------------------------
    def update(self, mini, header):
        if header:
            self.header = header
            self.fmt = str(header.get("matchFormat") or "?").upper()
            self.max_overs = MAX_OVERS.get(self.fmt)
        if not mini:
            return
        self.mini = mini

        runs, wkts = self.runs, self.wickets
        ov = self.overs_done
        self.over_just_ended = False

        # New innings: overs restart from 0, so the old mark would block
        # every over break of the chase (ov > self.over never comes true).
        inn = mini.get("inningsId")
        if inn != self._innings:
            self._innings = inn
            self._over_mark = None

        if self._over_mark is None:
            self._over_mark = (runs, wkts)
            self.over = ov
            self._joined_mid_over = float(self.overs_raw or 0) != ov
        elif ov > self.over:
            self.over_runs = max(0, runs - self._over_mark[0])
            self.over_wkts = max(0, wkts - self._over_mark[1])
            self.over_partial = self._joined_mid_over
            self._joined_mid_over = False
            self._over_mark = (runs, wkts)
            self.over = ov
            self.over_just_ended = True

        self._scan_milestones()
        self._mark_names()

    # Players of these sides have names romanized from Hindi / Urdu /
    # Bengali / Sinhala / Pashto; translit says them by Indic rules
    # ("Thakur" ठाकुर, not थकुर). Matched on the start of the short name, so
    # INDA, INDU19, PAKA and so on count too.
    INDIC_TEAMS = ("IND", "PAK", "BAN", "SL", "AFG", "NEP", "UAE", "OMA")

    def _mark_names(self):
        import translit
        # a women's match: verbs agree with the players (करती हैं, not करते हैं)
        names = " ".join(str((self.header.get(k) or {}).get("name") or "")
                         for k in ("team1", "team2"))
        translit.FEMININE = "women" in names.lower()
        bat_id = (self.mini.get("batTeam") or {}).get("teamId")
        codes = {}
        for key in ("team1", "team2"):
            t = self.header.get(key) or {}
            codes[t.get("id")] = str(t.get("shortName") or "").upper()
        bat = codes.get(bat_id, "")
        bowl = next((c for i, c in codes.items() if i != bat_id), "")
        for team, players in ((bat, (self.striker, self.non_striker)),
                              (bowl, (self.bowler, self.other_bowler))):
            if team.startswith(self.INDIC_TEAMS):
                for p in players:
                    if p.get("name"):
                        translit.mark_indic(p["name"])

    def _scan_milestones(self):
        """Milestones crossed since the last poll."""
        self.new_milestones = []
        for b in (self.striker, self.non_striker):
            nm, r, balls = b.get("name"), b.get("runs"), b.get("balls")
            if not nm or r is None:
                continue
            prev = self._bat_seen.get(nm)
            self._bat_seen[nm] = r
            if prev is None:
                continue
            for mark in (50, 100, 150):
                if prev < mark <= r:
                    self.new_milestones.append(
                        {"type": "bat", "mark": mark, "name": nm,
                         "balls": balls or 0})

    # -- plain readings --------------------------------------------------
    @property
    def runs(self):
        return (self.mini.get("batTeam") or {}).get("teamScore") or 0

    @property
    def wickets(self):
        return (self.mini.get("batTeam") or {}).get("teamWkts") or 0

    @property
    def overs_done(self):
        try:
            return int(float(self.mini.get("overs") or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def overs_raw(self):
        return self.mini.get("overs") or 0

    @property
    def team(self):
        code = ((self.mini.get("batTeamScoreObj") or {}).get("teamName")
                or "").upper()
        if code in P.TEAM_HI:
            return P.TEAM_HI[code]
        # Not a code we have Hindi for - use the header's full name
        # ("India Champions" reads far better than "INDCH").
        tid = (self.mini.get("batTeam") or {}).get("teamId")
        for key in ("team1", "team2"):
            t = self.header.get(key) or {}
            if tid is not None and t.get("id") == tid:
                short = str(t.get("shortName") or "").upper()
                return P.TEAM_HI.get(short) or t.get("name") or code
        return code

    @property
    def striker(self):
        return self.mini.get("batsmanStriker") or {}

    @property
    def non_striker(self):
        return self.mini.get("batsmanNonStriker") or {}

    @property
    def bowler(self):
        return self.mini.get("bowlerStriker") or {}

    @property
    def other_bowler(self):
        return self.mini.get("bowlerNonStriker") or {}

    @property
    def crr(self):
        return self.mini.get("currentRunRate")

    @property
    def need(self):
        n = self.mini.get("remRunsToWin")
        try:
            n = int(n)
        except (TypeError, ValueError):
            return 0
        return n if n > 0 else 0

    @property
    def is_chase(self):
        return self.need > 0

    @property
    def balls_left(self):
        if not self.max_overs:
            return 0
        try:
            ov = float(self.overs_raw)
        except (TypeError, ValueError):
            return 0
        whole = int(ov)
        balls = int(round((ov - whole) * 10))
        return max(0, self.max_overs * 6 - (whole * 6 + balls))

    @property
    def rrr(self):
        b = self.balls_left
        if not (self.is_chase and b):
            return None
        return self.need * 6.0 / b

    @property
    def overs_left(self):
        if not self.max_overs:
            return 0
        return max(0, self.max_overs - self.overs_done)

    @property
    def phase(self):
        """Powerplay / middle / death, scaled to the format."""
        if not self.max_overs:
            return "middle"           # Tests have no powerplay shape
        ov, mx = self.overs_done, self.max_overs
        if ov < (6 if mx == 20 else 10):
            return "powerplay"
        if ov >= mx - (5 if mx == 20 else 10):
            return "death"
        return "middle"

    @property
    def state(self):
        return str(self.header.get("state") or "").lower()

    @property
    def stoppage(self):
        """Key into phrasing.STOPPAGE if play is paused, else None."""
        s = self.state
        if "innings break" in s:
            return "innings"
        # The feed can say "In Progress" for a while after the last ball of
        # an innings - all overs bowled or ten down is the break already
        # (otherwise the gap talk carried on about "death overs").
        if self.max_overs and not self.is_chase and (
                self.overs_done >= self.max_overs or self.wickets >= 10)                 and "complete" not in s:
            return "innings"
        for k in ("rain", "drinks", "stumps", "lunch", "tea"):
            if k in s:
                return k
        if "timeout" in s:
            return "timeout"
        if "delay" in s or "wet" in s:
            return "generic"
        return None

    @property
    def pressure(self):
        """How the chase is going: 'high', 'even' or 'low'."""
        r = self.rrr
        if r is None:
            return "even"
        c = float(self.crr or 0)
        if r > c + 2.5:
            return "high"
        if r < c - 1.5:
            return "low"
        return "even"


# ── Choosing what to say ────────────────────────────────────────────────────
# Thresholds an analyst would call fast/slow/cheap/expensive, per format.
# Tests have none: a strike rate of 50 means something else there.
LIMITS = {
    20: {"sr_fast": 165, "sr_fast_b": 10, "sr_slow": 105, "sr_slow_b": 15,
         "eco_good": 6.0, "eco_bad": 10.5, "eco_ov": 2,
         "mom_hi": 24, "mom_lo": 8},
    50: {"sr_fast": 115, "sr_fast_b": 20, "sr_slow": 65, "sr_slow_b": 30,
         "eco_good": 4.0, "eco_bad": 7.0, "eco_ov": 3,
         "mom_hi": 18, "mom_lo": 5},
}

# "इस एक रन के साथ ..." - what the score line hangs off.
_AFTER = {1: "इस एक रन", 2: "इन दो रनों", 3: "इन तीन रनों",
          4: "इस चौके", 6: "इस छक्के"}

_DROP_RE = re.compile(r"dropped by|dropped at|catch dropped|dropped catch|"
                      r"drops the|put down|spills|misses the catch")


# Seconds before a topic may come back. A person does not ask for likes
# three times in 45 s, or read the score every 20 s (both measured live).
COOLDOWN = {"think": 40, "cta": 210, "address": 150, "recap": 240, "score": 45,
            "crr": 90, "overs": 150, "sr": 120, "lastwicket": 120,
            "partnership": 60, "forward": 60}

STAT_TOPICS = {"score", "striker", "nonstriker", "partnership", "bowler",
               "crr", "overs", "lastwicket", "boundaries", "sr", "rrr",
               "chase", "recap"}


class Director:
    """Picks the next thing to say from live context, avoiding repetition.

    It also remembers the innings - boundaries in a row, who hit whom, who
    was dropped, how long since the last boundary - because what a ball
    *means* depends on what came before it. That memory is what lets it say
    "लगातार तीसरी बाउंड्री" or "बॉलर ने बदला ले लिया" instead of describing
    every ball as if it were the first.
    """

    def __init__(self, sit):
        self.sit = sit
        self.pick = Pick()
        self._recent_topics = deque(maxlen=6)
        self._last_wicket_sig = None
        self._dots = 0                  # consecutive dot balls
        self._dots_before_wicket = False
        self._latest = True
        self.result_at = 1              # line where the 3D shot is played
        self.burst_at = 1               # line where the result is announced
        self._last_bowler = None        # who bowled the ball last described
        self.namer = TK.Namer()         # full name first, surname after
        self._talk_n = 0                # lines-of-thought counter for namer
        self._prev = None               # the ball before, for callbacks
        self.last_topic = None          # what the last gap line was about
        self._topic_t = {}              # topic -> when it was last said
        self._said_t = {}               # exact sentence -> when said
        self._idea_t = {}               # analyst idea -> when said
        self._cb_prev = None
        self._reset_innings()

    def _reset_innings(self):
        self._innings = self.sit.mini.get("inningsId")
        self._since_boundary = None     # legal balls since last 4/6 (None:
                                        # not seen one since we joined)
        self._streak = 0                # boundaries in a row
        self._over_key = None
        self._over_bounds = 0           # boundaries in the current over
        self._hit_by = set()            # (bowler, bat): bat hit bowler for 4/6
        self._dropped = set()           # batters given a life
        self._ball_no = 0               # legal balls seen
        self._wkt_balls = deque(maxlen=6)
        self._over_hist = deque(maxlen=4)

    def _check_innings(self):
        if self.sit.mini.get("inningsId") != self._innings:
            self._reset_innings()

    # -- small builders ---------------------------------------------------
    def _name(self, nm):
        return self.namer(nm, self._talk_n)

    def score(self):
        self._topic_t["score"] = time.time()
        s = self.sit
        if not s.runs and not s.wickets:
            return ""
        team = s.team or "टीम"
        if random.random() < 0.55 or s.wickets >= 10:
            return TK.score_short(team, s.runs, s.wickets)
        if not s.wickets:
            return self.pick(P.SCORE_NOWKT).format(team=team,
                                                   r=P.num_hi(s.runs))
        return self.pick(P.SCORE_FRAME).format(
            team=team, r=P.num_hi(s.runs), wk=P.wickets_hi(s.wickets),
            wn=P.num_hi(s.wickets))

    def score_after(self, ball):
        """'इस एक रन के साथ भारत का स्कोर पहुँचा ...' - the score hung off
        the run that just came, the commonest shape in the reference."""
        n = {"four": 4, "six": 6}.get(ball["kind"], ball["runs"])
        what = _AFTER.get(n)
        if not what or not self.sit.runs:
            return self.score()
        return self.pick(P.SCORE_AFTER).format(
            what=what, team=self.sit.team or "टीम", r=P.num_hi(self.sit.runs))

    def _rested(self, key, gap):
        """True (and stamps it) if this player's stats may be said again -
        across the over break, the gaps and the ball lines alike."""
        now = time.time()
        if now - self._idea_t.get(key, -1e9) < gap:
            return False
        self._idea_t[key] = now
        return True

    def _bat(self, b):
        if not b.get("name") or b.get("runs") is None:
            return ""
        if not self._rested("bat:" + b["name"], 90):
            return ""
        return TK.batter_line(b, self._name(b.get("name")))

    def _bat_boundaries(self, b):
        """Only mention what the batsman actually has - "zero sixes" is not
        something a commentator says, and "one fours" is wrong Hindi."""
        nm, f, sx = b.get("name"), b.get("fours") or 0, b.get("sixes") or 0
        if not nm or (not f and not sx):
            return ""
        bits = []
        if f:
            bits.append("%s %s" % (P.num_hi(f), "चौका" if f == 1 else "चौके"))
        if sx:
            bits.append("%s %s" % (P.num_hi(sx), "छक्का" if sx == 1 else "छक्के"))
        return self.pick(P.BAT_BOUNDARIES).format(nm=self._name(nm),
                                                  b=" और ".join(bits))

    def _bat_sr(self, b):
        nm, sr = b.get("name"), b.get("strikeRate")
        if not nm or not sr or (b.get("balls") or 0) < 15:
            return ""
        try:
            s_ = P.num_hi(int(round(float(sr))))
        except (TypeError, ValueError):
            return ""
        return self.pick(P.BAT_STRIKE_RATE).format(nm=self._name(nm), s=s_)

    def current_bowler(self):
        """The bowler of the ball we last described, with his figures.

        Cricbuzz's current/other bowler slots are unreliable across an over
        boundary (measured: at the end of Nigam's over the 'current bowler'
        was a third man, Arshad Khan, and the next over went to Roy). The
        feed text of the ball itself names who bowled it, so trust that and
        look his figures up in whichever slot holds him."""
        last = (self._last_bowler or "").lower().split()
        s = self.sit
        for b in (s.bowler, s.other_bowler):
            nm = str(b.get("name") or "").lower().split()
            if last and nm and nm[-1] == last[-1]:
                return b
        return {} if last else s.bowler

    def bowler_figures(self):
        b = self.current_bowler()
        lim = LIMITS.get(self.sit.max_overs) or {}
        if b.get("name") and not self._rested("bowl:" + b["name"], 120):
            return ""
        if b.get("name") and b.get("wickets") is not None:
            return TK.bowler_line(b, self._name(b["name"]),
                                  lim.get("eco_good", 6.0),
                                  lim.get("eco_bad", 10.0))
        nm, ov, w, r = b.get("name"), b.get("overs"), b.get("wickets"), b.get("runs")
        if not nm:
            return ""
        if ov and w is not None and r is not None:
            ov = P.num_hi(int(float(ov)))
            if not w:
                return self.pick(P.BOWLER_FIG_NOWKT).format(
                    nm=nm, ov=ov, r=P.num_hi(r))
            return self.pick(P.BOWLER_FIGURES).format(
                nm=nm, ov=ov, r=P.num_hi(r), w=P.wickets_hi(w))
        if b.get("economy"):
            return self.pick(P.BOWLER_ECONOMY).format(
                nm=nm, e=P.rate_hi(b["economy"]))
        return ""

    def partnership(self):
        ps = self.sit.mini.get("partnerShip") or {}
        r, b = ps.get("runs"), ps.get("balls")
        if not r:
            return ""
        return self.pick(P.PARTNERSHIP).format(
            r=P.num_hi(r), b=P.num_hi(b or 0))

    def last_wicket(self):
        lw = self.sit.mini.get("lastWicket") or ""
        if not isinstance(lw, str) or not lw.strip():
            return ""
        nm = re.split(r"\s+(?:c |b |lbw|run out|st |hit )", lw.strip())[0].strip()
        m = re.search(r"(\d+)\((\d+)\)", lw)
        if nm and m:
            return TK.last_wicket_line(self._name(nm), m.group(1), m.group(2))
        return ""

    def chase(self):
        s = self.sit
        if not s.is_chase:
            return ""
        b = s.balls_left
        if b:
            return self.pick(P.CHASE_NEED_BALLS).format(
                r=P.num_hi(s.need), b=P.num_hi(b))
        return self.pick(P.CHASE_NEED).format(r=P.num_hi(s.need))

    def required_rate(self):
        r = self.sit.rrr
        if r is None:
            return ""
        return self.pick(P.CHASE_RRR).format(v=TK.rate_words(r))

    def run_rate(self):
        if not self.sit.crr:
            return ""
        return self.pick(P.RUN_RATE).format(v=TK.rate_words(self.sit.crr))

    def colour(self, must=False):
        s = self.sit
        pool = P.COLOUR.get(s.phase, []) + P.COLOUR["any"]
        return self.pick(pool) if must else self.pick.maybe(pool)

    def pressure_note(self):
        p = self.sit.pressure
        if p == "high":
            return self.pick(P.CHASE_PRESSURE)
        if p == "low":
            return self.pick(P.CHASE_COMFORT)
        return ""

    # -- the analyst: an opinion, always built on a real number -----------
    def insight(self):
        """One analyst line - strike rate, economy, projection, rate gap,
        partnership, boundary drought - or '' when nothing stands out.

        Candidates are gathered as (bank, values) and only the chosen one is
        picked, so lines that were never said don't count as used."""
        s = self.sit
        lim = LIMITS.get(s.max_overs)
        cands = []                      # (bank, fields, is_opinion)

        if lim:
            for b in (s.striker, s.non_striker):
                nm, balls = b.get("name"), b.get("balls") or 0
                r = b.get("runs") or 0
                if not nm or not balls:
                    continue
                sr = r * 100.0 / balls
                if balls >= lim["sr_fast_b"] and sr >= lim["sr_fast"]:
                    cands.append((P.SR_FAST, {"nm": self._name(nm),
                                  "s": P.num_hi(int(round(sr)))}, True))
                elif balls >= lim["sr_slow_b"] and sr < lim["sr_slow"]:
                    cands.append((P.SR_SLOW, {"nm": self._name(nm),
                                              "b": P.num_hi(balls),
                                              "r": P.num_hi(r)}, True))
            bw = self.current_bowler()
            try:
                eco = float(bw.get("economy"))
                bov = float(bw.get("overs") or 0)
            except (TypeError, ValueError):
                eco, bov = None, 0
            if bw.get("name") and eco is not None and bov >= lim["eco_ov"]:
                if eco <= lim["eco_good"]:
                    cands.append((P.ECON_GOOD, {"nm": bw["name"],
                                  "e": TK.rate_words(eco)}, True))
                elif eco >= lim["eco_bad"]:
                    cands.append((P.ECON_BAD, {"nm": bw["name"],
                                  "e": TK.rate_words(eco)}, True))

            # first-innings projection, once there is enough to project from
            mx = s.max_overs
            if (not s.is_chase and not s.mini.get("target") and s.crr
                    and 4 <= s.overs_done <= mx - 2):
                p = s.runs + float(s.crr) * s.balls_left / 6.0
                p = int(round(p / 5.0) * 5)
                cands.append((P.PROJECTION, {"p": P.num_hi(p)}, True))

        if s.is_chase and s.rrr is not None and s.crr:
            gap = s.rrr - float(s.crr)
            if gap >= 1.5:
                cands.append((P.RATE_GAP, {
                    "c": TK.rate_words(s.crr),
                    "v": TK.rate_words(s.rrr)}, True))

        ps = (s.mini.get("partnerShip") or {}).get("runs") or 0
        if ps >= 50:
            cands.append((P.PARTNERSHIP_READ, {"r": P.num_hi(ps)}, True))

        if self._since_boundary is not None and self._since_boundary >= 12:
            cands.append((P.DROUGHT_NOTE,
                          {"n": P.num_hi(self._since_boundary)}, False))

        now = time.time()
        cands = [c for c in cands
                 if now - self._idea_t.get(id(c[0]), -1e9) >= 300]
        if not cands:
            return ""
        bank, fields, opinion = random.choice(cands)
        self._idea_t[id(bank)] = now
        line = self.pick(bank).format(**fields)
        if opinion and random.random() < 0.25:
            line = "%s, %s" % (self.pick(P.OPINION), line)
        return line

    # -- a real delivery --------------------------------------------------
    def _opener(self, ball):
        """How a ball is started: short, and usually one name or none.
        Where the over stands still opens its first and last balls."""
        pk = self.pick
        pos = P.ball_of_over(ball.get("metric"))
        r = random.random()
        if pos and ball["kind"] not in ("wide", "noball"):
            ov, nth = pos
            if nth in (1, 6) and r < 0.55:
                f = {"ov": P.ordinal_over(ov), "nth": P.ordinal_f(nth),
                     "bowler": self._name(ball["bowler"]),
                     "bat": TK.surname(ball["bat"])}
                return pk(P.OVER_FIRST if nth == 1 else P.OVER_LAST).format(**f)
        if r < 0.30:
            return pk(TK.OPEN_PLAIN)
        if r < 0.65:
            return pk(TK.OPEN_BOWLER).format(bw=self._name(ball["bowler"]))
        if r < 0.87:
            return pk(TK.OPEN_BAT).format(bt=self._name(ball["bat"]))
        return pk(TK.OPEN_BOTH).format(bw=self._name(ball["bowler"]),
                                       bt=self._name(ball["bat"]))

    def _track(self, ball, play):
        """Update the innings memory with this ball; return what it implies."""
        kind, bat, bowler = ball["kind"], ball["bat"], ball["bowler"]
        pos = P.ball_of_over(ball.get("metric"))
        key = pos[0] if pos else self.sit.overs_done
        if key != self._over_key:
            self._over_key, self._over_bounds = key, 0

        mem = {"drought": 0, "revenge": False, "drop_cost": False,
               "collapse": 0}
        legal = kind not in ("wide", "noball")
        if legal:
            self._ball_no += 1

        if kind in ("four", "six"):
            mem["drought"] = self._since_boundary or 0
            self._since_boundary = 0
            self._streak += 1
            self._over_bounds += 1
            self._hit_by.add((bowler, bat))
            if bat in self._dropped:
                self._dropped.discard(bat)
                mem["drop_cost"] = True
        elif legal:
            if self._since_boundary is not None:
                self._since_boundary += 1
            self._streak = 0

        if kind == "wicket":
            mem["revenge"] = (bowler, bat) in self._hit_by
            self._hit_by = {p for p in self._hit_by if p[1] != bat}
            self._dropped.discard(bat)
            self._wkt_balls.append(self._ball_no)
            mem["collapse"] = sum(1 for b in self._wkt_balls
                                  if self._ball_no - b < 30)
        elif play["act"] == "chance" and _DROP_RE.search(play["raw"]):
            self._dropped.add(bat)
        return mem

    def ball_lines(self, ball, latest=True):
        """One delivery -> a few short clauses, bridged into the stream.

        `latest` is False for an older ball in a batch that arrived in one
        poll: the miniscore is already past it, so it gets no score line
        (it would read out a total that ball did not produce).

        Returns [] for a repeat wicket: Cricbuzz posts an interim
        "THATS OUT!!" line and then the full "out Caught by ..." line for the
        same dismissal, and announcing both sounds broken.
        """
        self._check_innings()
        self._latest = latest
        self._last_bowler = ball["bowler"]
        if ball["kind"] == "wicket":
            sig = (ball["bowler"], ball["bat"])
            if sig == self._last_wicket_sig:
                return []
            self._last_wicket_sig = sig
        kind = ball["kind"]
        pos = find_position(ball.get("detail"))
        play = PL.parse_play(ball.get("detail"))
        mem = self._track(ball, play)

        self._talk_n += 1
        self._cb_prev = self._prev
        pv = self._prev or {}
        self._prev = {
            "bat": ball["bat"], "bowler": ball["bowler"], "kind": kind,
            "length": play["length"], "act": play["act"],
            "dots": (pv.get("dots", 0) + 1) if kind == "dot" else 0,
            "singles": (pv.get("singles", 0) + 1) if kind == "one" else 0,
        }

        dots_before = self._dots
        self._dots_before_wicket = (kind == "wicket" and dots_before >= 3)
        if kind == "dot":
            self._dots += 1
        elif kind not in ("wide", "bye", "legbye"):
            self._dots = 0

        # A six, a wicket or a no-ball is not a one-line event. A real
        # commentator erupts, says it again, reacts, and only then settles.
        if kind in ("six", "four", "wicket", "noball"):
            out = self._moment(ball, pos, play, mem)
        else:
            out = self._ordinary(ball, pos, play, dots_before)
        return self._no_echo(out)

    _STOP = {"है", "हैं", "के", "की", "का", "में", "पर", "और", "से", "ये",
             "तो", "को", "ने", "भी", "एक", "बहुत", "था", "थी", "थे", "हो",
             "रही", "रहे", "रहा", "गया", "गई", "दिया"}

    def _words(self, t):
        return {w.strip("!?,.।") for w in str(t).split()} - self._STOP - {""}

    def _no_echo(self, out):
        """Drop a line that mostly repeats an earlier one for the same ball
        ("बहुत बड़ा विकेट!" ... "ये तो बहुत बड़ा विकेट है!"). Said in one
        flow, an echo sounds like the commentator is stuck. Cue positions
        (result_at / burst_at) shift with the lines that go."""
        keep, seen = [], []
        removed_before = {"r": 0, "b": 0}
        for i, (t, e) in enumerate(out):
            w = self._words(t)
            echo = i > 0 and w and any(
                len(w & p) / float(min(len(w), len(p)) or 1) >= 0.5
                for p in seen if p)
            if echo and i not in (0, self.result_at, self.burst_at):
                if i < self.result_at:
                    removed_before["r"] += 1
                if i < self.burst_at:
                    removed_before["b"] += 1
                continue
            keep.append((t, e))
            seen.append(w)
        self.result_at -= removed_before["r"]
        self.burst_at -= removed_before["b"]
        return keep

    def _ordinary(self, ball, pos, play, dots_before):
        """A normal ball the way a person says it: a short opener, then ONE
        sentence with the ball, the stroke, where it went and the result -
        "शॉर्ट गेंद, मिड विकेट की तरफ़ पुल किया, एक रन" - then at most one
        thought. (It used to be 5-6 separate lines on every ball, with the
        same fact said two or three times.)"""
        s, kind, pk = self.sit, ball["kind"], self.pick
        act = play["act"]
        lead = "lively" if s.phase == "death" else "neutral"
        out = [(self._opener(ball), lead)]
        # The 3D shot and the scoreboard ride on the next line.
        self.result_at = self.burst_at = 1
        prev = self._cb_prev
        scoring = kind in ("one", "two", "three", "runs")

        # a close call: the reaction IS the description
        react = None
        if act == "chance":
            react = (pk(PL.CHANCE_REACT), "excited")
        elif act == "beaten" and kind == "dot":
            react = (pk(PL.BEATEN_REACT), "amazed")
        elif act == "edge":
            react = (pk(PL.EDGE_REACT), "amazed")
        elif act == "appeal":
            react = (pk(PL.APPEAL_REACT), "excited")
        elif play["air"] and kind != "dot":
            react = (pk(PL.AIR_REACT), "amazed")
        if react:
            # a dropped catch gets the gasp first: "अरे! ये क्या हुआ!"
            if act == "chance" and random.random() < 0.6:
                out.append((TK.interject(kind, act), "excited"))
            out.append(react)
            if scoring or kind in ("wide", "bye", "legbye"):
                res = TK.ball_sentence({"length": None, "line": None,
                                        "act": None}, kind, ball["runs"], pos)
                out.append((res or self._outcome(ball), "neutral"))
        else:
            again = bool(prev and play["length"] and
                         prev.get("length") == play["length"] and
                         prev.get("bowler") == ball["bowler"] and
                         random.random() < 0.6)
            bat_nm = TK.surname(ball["bat"])
            sent = TK.ball_sentence(play, kind, ball["runs"], pos, bat_nm,
                                    again) or self._outcome(ball)
            ij = TK.interject(kind, act, ball["runs"])
            emo = "neutral"
            if ij:
                sent = "%s %s" % (ij, sent)
                emo = "lively" if kind != "dot" else "neutral"
            out.append((sent, emo))

        # at most one thought: tie it to the ball before if possible
        thought = TK.callback(prev, ball, play)
        if thought and random.random() < 0.7:
            out.append((thought, "calm"))
        else:
            out.extend(self._reading(ball, play, dots_before)[:1])

        if self._latest:
            r = random.random()
            if s.is_chase and r < 0.25:
                out.append((self.chase(),
                            "lively" if s.pressure == "high" else "neutral"))
            elif kind in ("two", "three") and r < 0.35:
                out.append((self.score_after(ball), "neutral"))
            elif r < 0.12:
                out.append((self.score(), "neutral"))
        return [(t, e) for t, e in out if t]

    def _reading(self, ball, play, dots_before):
        """At most one interpretive line, only when it adds something, and
        never a line from a bank that has run dry (that is what made the
        same opinion come back ball after ball)."""
        kind, act, mb = ball["kind"], play["act"], self.pick.maybe
        runs = (self.sit.striker or {}).get("runs") or 0
        if kind == "dot" and self._dots >= 3 and self._dots % 2 == 1:
            line = mb(PL.READ["pressure_dots"])
            return [(line.format(n=P.num_hi(self._dots)), "tense")] if line \
                else []
        if kind in ("one", "two", "three", "runs") and dots_before >= 3:
            line = mb(PL.READ["pressure_broken"])
            return [(line.format(n=P.num_hi(dots_before)), "lively")] if line \
                else []
        if 90 <= runs <= 99 and random.random() < 0.35:
            return [(mb(PL.READ["nervous"]), "tense")]
        if 44 <= runs <= 49 and random.random() < 0.3:
            return [(mb(PL.READ["fifty_close"]), "lively")]
        r = random.random()
        if act in PL.READ and r < 0.3:
            return [(mb(PL.READ[act]), "calm")]
        if kind == "two" and r < 0.3:
            return [(mb(PL.READ["double"]), "lively")]
        if kind == "one" and r < 0.1:
            return [(mb(PL.READ["single"]), "calm")]
        return []

    def _moment(self, ball, pos, play, mem):
        """A big moment, drawn out the way it is on a real broadcast:

            eruption -> say it again -> what made it special (a streak, a
            drought ended, a dropped catch coming back to bite) -> where it
            went -> reaction -> the stat -> the score -> settle back down

        Emotion decays across the sequence, so it peaks on the shout and
        comes back to normal by the end instead of staying shrill.
        """
        s, pk = self.sit, self.pick
        kind = ball["kind"]
        out = [(self._opener(ball), "lively")]
        self.result_at = self.burst_at = 1      # the shout: "छक्का!!"

        # The build-up: a real commentator rides the ball in the air before
        # he erupts - "उठाया है... ऊपर... और ऊपर..." -> "छक्का!!". The 3D
        # shot starts on the build-up, the board and the roar land on the
        # shout, so the voice climbs while the ball is in the air.
        build = None
        how = PL.parse_dismissal(ball.get("text"))["how"] \
            if kind == "wicket" else None
        if kind == "six" and random.random() < 0.8:
            build = P.BUILD_SIX
        elif kind == "four" and random.random() < 0.6:
            build = P.BUILD_FOUR_AIR if play["air"] else P.BUILD_FOUR
        elif kind == "wicket" and how in ("caught", "other") and \
                random.random() < 0.7:
            build = P.BUILD_CATCH
        elif kind == "wicket" and random.random() < 0.4:
            build = P.BUILD_WICKET
        if build:
            out.append((pk(build), "amazed"))
            self.burst_at = 2

        if kind == "six":
            out.append((pk(P.SIX_BURST), "excited"))
            out.append((pk(P.SIX_REPEAT), "excited"))
            out.extend(self._boundary_memory(mem))
            out.extend(self._why_it_went(ball, play, six=True))
            if pos:
                out.append((pk(P.OVER_FIELDER).format(pos=pos), "excited"))
            out.append((pk(P.SIX_REACT), "excited"))
            n = (s.striker or {}).get("sixes") or 0
            if n >= 2 and self._latest:
                out.append((pk(P.MS_SIX_COUNT).format(n=P.ordinal_hi(n)),
                            "lively"))

        elif kind == "four":
            out.append((pk(P.FOUR_BURST), "excited"))
            out.append((pk(P.FOUR_REPEAT), "excited"))
            out.extend(self._boundary_memory(mem))
            out.extend(self._why_it_went(ball, play))
            if pos:
                if pos == "कीपर":
                    out.append((pk(["कीपर के पास से निकल गई, बाउंड्री!",
                                    "कीपर के बगल से, और रस्सी के पार!"]),
                                "excited"))
                else:
                    out.append((pk(P.PAST_FIELDER).format(pos=pos),
                                "excited"))
            n = (s.striker or {}).get("fours") or 0
            if n >= 3 and self._latest and random.random() < 0.6:
                out.append((pk(P.MS_FOUR_COUNT).format(n=P.ordinal_hi(n)),
                            "lively"))

        elif kind == "wicket":
            out.append((pk(P.WICKET_BURST), "excited"))
            if random.random() < 0.5:
                out.append((pk(P.WICKET_REPEAT), "excited"))
            out.extend(self._how_out(ball, pos))
            if mem["revenge"] and random.random() < 0.8:
                out.append((pk(P.REVENGE), "lively"))
            runs, _balls = dismissed_for(ball.get("text"))
            if runs is not None:
                out.append((pk(P.WICKET_OUT_FOR).format(
                    nm=ball["bat"], r=P.num_hi(runs)), "lively"))
            if mem["collapse"] >= 3:
                out.append((pk(P.COLLAPSE).format(
                    n=P.num_hi(mem["collapse"])), "lively"))
            elif self._dots_before_wicket:
                out.append((pk(PL.READ["wicket_pressure"]), "lively"))
            elif random.random() < 0.35:
                out.append((pk(PL.WICKET_THOUGHT),
                            "tense"))
            out.append((pk(P.WICKET_REACT), "lively"))

        else:                                    # no ball
            out.append((pk(P.NOBALL_BURST), "excited"))
            out.append((pk(P.NOBALL_REACT), "amazed"))

        if self._latest:
            if kind == "wicket" and s.wickets >= 2 and random.random() < 0.5:
                out.append((pk(P.WICKET_TEAM).format(team=s.team or "टीम"),
                            "neutral"))
            elif kind in ("four", "six") and random.random() < 0.6:
                out.append((self.score_after(ball), "neutral"))
            else:
                out.append((self.score(), "neutral"))
        if kind == "wicket" and random.random() < 0.7:
            out.append((pk(P.NEW_BAT), "neutral"))
        elif s.is_chase and self._latest:
            out.append((self.chase(),
                        "lively" if s.pressure == "high" else "neutral"))
        if random.random() < 0.2:
            out.append((self.pick.maybe(P.SETTLE), "calm"))
        return [(t, e) for t, e in out if t]

    def _boundary_memory(self, mem):
        """What makes this boundary more than a boundary, from memory."""
        pk, out = self.pick, []
        if 2 <= self._streak <= 6:
            out.append((pk(P.STREAK_BOUNDARY).format(
                n=P.ordinal_f(self._streak)), "excited"))
        elif 2 <= self._over_bounds <= 6:
            out.append((pk(P.OVER_BOUNDARIES).format(
                n=P.ordinal_f(self._over_bounds)), "lively"))
        elif mem["drought"] >= 18:
            out.append((pk(P.DROUGHT_BROKEN).format(
                n=P.num_hi(mem["drought"])), "lively"))
        if mem["drop_cost"]:
            out.append((pk(P.DROP_COST), "lively"))
        return out

    def _why_it_went(self, ball, play, six=False):
        """Boundary follow-up: what the batter did and whether the bowler
        offered it - 'loose ball punished' vs 'good ball, better shot'."""
        pk, out = self.pick, []
        did = PL.batter_did(play, pk, TK.surname(ball["bat"]))
        if did:
            out.append((did, "lively"))
        if PL.loose_ball(play):
            out.append((PL.with_glue(pk(PL.READ["loose"]), pk, 0.5), "lively"))
        elif play["length"] in ("good", "backlen", "yorker") \
                and random.random() < 0.6:
            out.append((pk(PL.READ["good_ball_boundary"]),
                        "amazed" if six else "lively"))
        return out

    def _how_out(self, ball, pos):
        """The dismissal told the way it happened: bowled, caught by whom."""
        d = PL.parse_dismissal(ball.get("text"))
        how = d["how"]
        if how == "caught" and not d["fielder"]:
            how = "caught_nofield"
        line = self.pick(PL.HOW_OUT[how]).format(
            fielder=d["fielder"] or "फ़ील्डर", bowler=ball["bowler"],
            bat=ball["bat"])
        out = [(line, "excited")]
        if how in ("caught", "caught_nofield") and pos:
            out.append((self.pick(P.CAUGHT_AT).format(pos=pos), "excited"))
        return out

    def _outcome(self, ball):
        k = ball["kind"]
        pool = {"six": P.OUT_SIX, "four": P.OUT_FOUR, "wicket": P.OUT_WICKET,
                "dot": P.OUT_DOT, "one": P.OUT_ONE, "two": P.OUT_TWO,
                "three": P.OUT_THREE}.get(k)
        if pool:
            return self.pick(pool)
        if k in P.OUT_EXTRA:
            return self.pick(P.OUT_EXTRA[k])
        if k == "runs":
            return "%s रन इस गेंद पर" % P.num_hi(ball["runs"])
        # Unknown outcome: stay neutral. Saying "no run" here would be a
        # flat-out wrong call on a ball we failed to classify.
        return "देखते हैं इस गेंद पर क्या निकला"

    # -- the over break: a real 30-40s block, not more small talk ---------
    def over_break_lines(self):
        """The 30-40s between overs, paced down from the delivery itself."""
        self._check_innings()
        s, pk = self.sit, self.pick
        if s.overs_done and random.random() < 0.5:
            out = [(pk(P.OVER_END_NUM).format(ov=P.ordinal_over(s.overs_done)),
                    "neutral")]
        else:
            out = [(pk(P.OVER_BREAK_OPEN), "neutral")]

        # Runs "in the over" only mean something if we saw the whole over.
        if not s.over_partial:
            n = s.over_runs
            if n == 0:
                out.append((pk(P.OVER_MAIDEN), "lively"))
            else:
                out.append((pk(P.OVER_RUNS).format(n=P.num_hi(n)), "neutral"))
                if n <= 3:
                    out.append((pk(P.OVER_TIGHT), "calm"))
                elif n >= 12:
                    out.append((pk(P.OVER_EXPENSIVE), "lively"))
            self._over_hist.append(n)
            out.append((self._momentum(), "lively"))
        if s.over_wkts:
            out.append((pk(P.OVER_WICKET_IN), "lively"))

        out.append((self.bowler_figures(), "calm"))
        out.append((self.score(), "neutral"))

        if s.is_chase:
            hot = "lively" if s.pressure == "high" else "neutral"
            out.append((self.chase(), hot))
            out.append((self.required_rate(), hot))
            out.append((self.pressure_note(), hot))
        else:
            out.append((self.partnership(), "calm"))
            if s.overs_left:
                out.append((pk(P.OVERS_LEFT).format(
                    n=P.num_hi(s.overs_left)), "calm"))
        if random.random() < 0.6:
            out.append((self.insight(), "calm"))

        # No "next bowler" guess here: the feed's slots are wrong at an over
        # boundary. Its own "X comes into the attack" note names him, and
        # note_line() says that when it arrives.
        if random.random() < 0.5:
            out.append((pk(P.OVER_BREAK_TALK), "slow"))
        out.append((self._bat(s.striker), "calm"))
        return [(t, e) for t, e in out if t]

    def _momentum(self):
        """Runs across the last two full overs, when that tells a story."""
        lim = LIMITS.get(self.sit.max_overs)
        if not lim or len(self._over_hist) < 2 or random.random() > 0.6:
            return ""
        n = self._over_hist[-1] + self._over_hist[-2]
        if n >= lim["mom_hi"]:
            return self.pick(P.MOMENTUM_BAT).format(n=P.num_hi(n))
        if n <= lim["mom_lo"]:
            return self.pick(P.MOMENTUM_BOWL).format(n=P.num_hi(n))
        return ""

    # -- start / end of a match -------------------------------------------
    def opening_lines(self):
        s, pk = self.sit, self.pick
        out = [(pk(P.OPENING), "lively")]
        if s.team:
            out.append((pk(P.OPENING_MATCH).format(team=s.team), "lively"))
        out.append((self.score() or "खेल शुरू हो चुका है", "neutral"))
        return out

    def _team_hi(self, name):
        """A team named in English in the result -> its Hindi name."""
        for key in ("team1", "team2"):
            t = self.sit.header.get(key) or {}
            if str(t.get("name") or "").lower() == name.lower():
                short = str(t.get("shortName") or "").upper()
                return P.TEAM_HI.get(short) or name
        return P.TEAM_HI.get(name.upper(), name)

    def result_lines(self, status):
        """The final result in Hindi. The feed's status is English prose
        ("India won by 5 wkts"), which the Hindi voice would mangle."""
        pk = self.pick
        out = [(pk(P.CLOSING), "lively")]
        m = re.match(r"^\s*(.+?)\s+won\s+by\s+(\d+)\s*(run|wkt|wicket)",
                     status or "", re.I)
        if m:
            team, n = self._team_hi(m.group(1).strip()), int(m.group(2))
            if m.group(3).lower().startswith("run"):
                line = pk(P.RESULT_RUNS).format(team=team, n=P.num_hi(n))
            else:
                line = pk(P.RESULT_WKTS).format(team=team, n=P.wickets_hi(n))
            out.append((line, "excited"))
        else:
            out.append((pk(P.RESULT_OTHER), "neutral"))
        out.append((pk(P.SIGNOFF), "calm"))
        return out

    # -- milestones -------------------------------------------------------
    def milestone_lines(self):
        out = []
        for m in self.sit.new_milestones:
            if m["type"] != "bat":
                continue
            bank = P.MS_HUNDRED if m["mark"] >= 100 else P.MS_FIFTY
            out.append((self.pick(bank).format(
                nm=m["name"], b=P.num_hi(m["balls"])), "excited"))
        return out

    # -- match notes ------------------------------------------------------
    # The feed mixes English prose in with the deliveries ("Yusuf Pathan,
    # right handed bat, comes to the crease", "Time-out !!"). Reading that
    # raw through a Hindi voice is the other half of the pronunciation
    # problem, so recognised notes become proper Hindi and the rest is
    # printed but never spoken.
    def note_line(self, text):
        """(text, emotion) for a match note worth saying, else None."""
        low = text.lower()

        m = re.search(r"^([A-Z][A-Za-z.'\- ]+?),?\s*(right|left)[\s\-]*hand",
                      text)
        if m and "crease" in low:
            hand = "राइट हैंड" if m.group(2).lower() == "right" else "लेफ्ट हैंड"
            return ("%s क्रीज़ पर आ रहे हैं, %s के बल्लेबाज़"
                    % (m.group(1).strip(), hand), "neutral")
        if "comes to the crease" in low:
            nm = text.split(",")[0].strip()
            return ("%s क्रीज़ पर आ रहे हैं" % nm, "neutral")
        if "time-out" in low or "time out" in low:
            return (self.pick(P.STOPPAGE["timeout"]), "slow")
        if "drinks" in low:
            return (self.pick(P.STOPPAGE["drinks"]), "slow")
        if "innings break" in low:
            return (self.pick(P.INNINGS_BREAK), "slow")
        if "rain" in low or "wet outfield" in low:
            return (self.pick(P.STOPPAGE["rain"]), "slow")
        if "review" in low or "drs" in low:
            return ("रिव्यू लिया गया है, अब थर्ड अंपायर के फ़ैसले का इंतज़ार",
                    "lively")
        if "free hit" in low:
            return ("फ्री हिट मिल गई है", "lively")
        if "powerplay" in low:
            return ("पावरप्ले का दौर है", "neutral")
        m = re.match(r"^([A-Z][A-Za-z.'\- ]+?)\s*\[", text)
        if m and "back into the attack" in low:
            return ("%s फिर से बॉलिंग के लिए आए हैं" % m.group(1).strip(),
                    "neutral")
        if "comes into the attack" in low or "into the attack" in low:
            nm = re.split(r"[\[,]", text)[0].strip()
            if nm:
                return ("बॉलिंग करने के लिए आ रहे हैं %s" % nm, "neutral")
        return None

    # -- stoppages --------------------------------------------------------
    def stoppage_lines(self):
        s = self.sit
        key = s.stoppage
        if not key:
            return []
        if key == "innings":
            out = [(self.pick(P.INNINGS_BREAK), "slow"),
                   (self.score(), "neutral")]
            if s.runs:
                out.append((self.pick(P.TARGET_SET).format(
                    n=P.num_hi(s.runs + 1)), "lively"))
            out.append((self._bat(s.striker), "calm"))
            return [(t, e) for t, e in out if t]
        return [(self.pick(P.STOPPAGE.get(key, P.STOPPAGE["generic"])), "slow"),
                (self.score(), "calm")]

    # -- one filler line, chosen by context -------------------------------
    def filler_line(self):
        """Pulled by the Speaker whenever its buffer dips.

        Topics are weighted by the live situation and the last few topics are
        barred, so the gaps never settle into a recognisable cycle.
        """
        s = self.sit

        if s.stoppage:
            opts = [("stoppage", 6), ("score", 2), ("colour", 2),
                    ("address", 1), ("cta", 2), ("recap", 1)]
        elif s.is_chase:
            opts = [("chase", 4), ("rrr", 3), ("pressure", 3), ("score", 3),
                    ("striker", 3), ("bowler", 2), ("colour", 3),
                    ("insight", 2), ("think", 3), ("partnership", 2), ("crr", 1),
                    ("boundaries", 2), ("nonstriker", 1), ("forward", 1),
                    ("address", 1), ("cta", 1), ("recap", 1)]
        else:
            opts = [("score", 3), ("striker", 3), ("nonstriker", 2),
                    ("partnership", 3), ("bowler", 3), ("colour", 4),
                    ("insight", 2), ("think", 3), ("crr", 2), ("overs", 2),
                    ("lastwicket", 2), ("boundaries", 2), ("sr", 1),
                    ("forward", 1), ("address", 1), ("cta", 2), ("recap", 1)]

        self._talk_n += 1
        now = time.time()
        opts = [(t, w) for t, w in opts
                if now - self._topic_t.get(t, -1e9) >= COOLDOWN.get(t, 0)]
        opts = opts or [("colour", 1)]
        pool = [(t, w) for t, w in opts if t not in self._recent_topics]
        # a person does not read three stats in a row: after a number, say
        # something about the game
        if self._recent_topics and self._recent_topics[-1] in STAT_TOPICS:
            pool = [(t, w) for t, w in pool if t not in STAT_TOPICS] or pool
        if not pool:
            pool = opts
        # Try topics until one gives a sentence not said in the last 10
        # minutes. Between two balls the numbers do not change, so the same
        # stat sentence ("Kohli 64 पर...") used to come straight back.
        tried, text, topic = set(), "", "colour"
        for _ in range(12):
            cand = [(t, w) for t, w in pool if t not in tried] or \
                [(t, w) for t, w in opts if t not in tried]
            if not cand:
                break
            topic = random.choices([t for t, _ in cand],
                                   weights=[w for _, w in cand])[0]
            tried.add(topic)
            text = self._by_topic(topic)
            if text and not self._said_lately(text, now):
                break
            text = ""
        if not text:            # everything rested: talk about the game
            for topic, fn in (("think", lambda: self.pick.maybe(TK.THINK)),
                              ("colour", self.colour),
                              ("insight", self.insight)):
                text = fn()
                if text and not self._said_lately(text, now):
                    break
                text = ""
            if not text:
                topic, text = "colour", self.colour(must=True)
        self._said_t[text] = now
        self.last_topic = topic
        self._recent_topics.append(topic)
        self._topic_t[topic] = now

        # Between-ball talk is relaxed, except when a chase is tightening or
        # it is the death overs - then it keeps some edge.
        if topic in ("chase", "rrr", "pressure") and s.pressure == "high":
            emotion = "lively"
        elif s.stoppage:
            emotion = "slow"
        elif s.phase == "death":
            emotion = "neutral"
        else:
            emotion = "calm"
        return text, emotion

    def _said_lately(self, text, now, gap=600.0):
        return now - self._said_t.get(text, -1e9) < gap

    def _by_topic(self, topic):
        s = self.sit
        if topic == "score":        return self.score()
        if topic == "striker":      return self._bat(s.striker)
        if topic == "nonstriker":   return self._bat(s.non_striker)
        if topic == "partnership":  return self.partnership()
        if topic == "bowler":       return self.bowler_figures()
        if topic == "colour":       return self.colour()
        if topic == "insight":      return self.insight()
        if topic == "crr":          return self.run_rate()
        if topic == "rrr":          return self.required_rate()
        if topic == "chase":        return self.chase()
        if topic == "pressure":     return self.pressure_note()
        if topic == "lastwicket":   return self.last_wicket()
        if topic == "boundaries":   return self._bat_boundaries(s.striker)
        if topic == "sr":           return self._bat_sr(s.striker)
        if topic == "overs":
            return (self.pick(P.OVERS_DONE).format(n=P.num_hi(s.overs_done))
                    if s.overs_done else "")
        if topic == "recap":
            sc = self.score()
            return "%s, %s" % (self.pick(P.RECAP_OPEN), sc) if sc else ""
        if topic == "stoppage":
            key = s.stoppage or "generic"
            if key == "innings":
                return self.pick(P.INNINGS_BREAK)
            return self.pick(P.STOPPAGE.get(key, P.STOPPAGE["generic"]))
        if topic == "forward":      return self.pick.maybe(P.FORWARD)
        if topic == "address":      return self.pick.maybe(P.ADDRESS)
        if topic == "cta":          return self.pick.maybe(P.CTA)
        if topic == "think":
            # half the time an observation about the players on the field
            st, ns, bw = s.striker, s.non_striker, self.current_bowler()
            if random.random() < 0.5 and st.get("name") and ns.get("name")                     and bw.get("name"):
                line = self.pick.maybe(TK.THINK_NAMED)
                if line:
                    return line.format(bat=TK.surname(st["name"]),
                                       bat2=TK.surname(ns["name"]),
                                       bowl=TK.surname(bw["name"]),
                                       team=s.team or "टीम")
            return self.pick.maybe(TK.THINK)
        return ""
