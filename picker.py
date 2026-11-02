# -*- coding: utf-8 -*-
"""
picker.py  -  which matches are worth a stream today.

The live-scores and upcoming-matches pages embed a `matchInfo` JSON for every
match they list (teams, series, format, start time, state). That is the day's
schedule; no extra request per match.

Each match gets a hype score:

    who is playing     India men 100, India A / Women 50-55, top nations 40 ...
    what competition   IPL 95, WPL 45, big leagues 35, Indian domestic 20 ...
    what is at stake   final +40, semi +25, India v Pakistan +60
    format             T20 +15, ODI +10, Test -25 (not India men)
    live buzz          viewers already watching this fixture on YouTube

The top MATCHES_PER_DAY are today's picks. The streamer waits for a pick to
start rather than spending the day's slot on a lesser game that happens to be
live first.
"""
import os
import re
import json
import math
import time
import urllib.parse
import urllib.request

import cricbuzz

IST = 5.5 * 3600

TOP_NATIONS = ("pakistan", "australia", "england", "south africa",
               "new zealand", "sri lanka", "bangladesh", "afghanistan",
               "west indies")

# (substring of the series name, points) - first hit wins
SERIES = [
    ("indian premier league", 95), ("ipl", 95),
    ("women's premier league", 45), ("wpl", 45),
    ("asia cup", 60), ("world cup", 60), ("champions trophy", 70),
    ("world test championship", 40),
    ("big bash", 35), ("bbl", 35), ("pakistan super league", 35),
    ("sa20", 35), ("caribbean premier league", 30), ("the hundred", 30),
    ("ilt20", 30), ("major league cricket", 30), ("lanka premier league", 25),
    ("bangladesh premier league", 25), ("nepal premier league", 35),
    ("syed mushtaq ali", 30), ("vijay hazare", 25), ("ranji trophy elite", 18),
    ("duleep trophy", 25), ("irani", 25), ("deodhar", 20),
    ("ranji trophy plate", 5),
]

FORMAT = {"T20": 15, "ODI": 10, "TEST": -25}

LIVE = cricbuzz.LIVE_STATES
DONE = ("complete", "abandon", "no result", "cancelled")

MATCHES_PER_DAY = int(os.environ.get("MATCHES_PER_DAY", "2"))
MIN_SCORE = int(os.environ.get("MIN_HYPE", "25"))


# ── schedule ───────────────────────────────────────────────────────────────
def schedule():
    """Every match the two Cricbuzz pages list, as plain dicts."""
    dec = json.JSONDecoder()
    found = {}
    for page in ("/cricket-match/live-scores",
                 "/cricket-match/live-scores/upcoming-matches"):
        txt = cricbuzz._page(page).replace('\\"', '"')
        for m in re.finditer(r'"matchInfo":', txt):
            try:
                o, _ = dec.raw_decode(txt, m.end())
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("matchId"):
                found[str(o["matchId"])] = o
    out = []
    for mid, o in found.items():
        t1, t2 = o.get("team1") or {}, o.get("team2") or {}
        state = str(o.get("state") or "")
        out.append({
            "mid": mid,
            "t1": t1.get("teamName") or "?", "t2": t2.get("teamName") or "?",
            "s1": t1.get("teamSName") or "", "s2": t2.get("teamSName") or "",
            "id1": t1.get("teamId"), "id2": t2.get("teamId"),
            "img1": t1.get("imageId"), "img2": t2.get("imageId"),
            "series": _series_name(o.get("seriesName") or ""),
            "desc": o.get("matchDesc") or "",
            "format": str(o.get("matchFormat") or "?").upper(),
            "start": int(o.get("startDate") or 0) / 1000.0,
            "state": state,
            "status": o.get("status") or "",
            "live": state.lower() in LIVE,
            "done": any(d in state.lower() for d in DONE),
            "venue": ((o.get("venueInfo") or {}).get("city") or ""),
        })
    return out


def _series_name(s):
    # the page sometimes renders a name twice, one inside the other:
    # "India A tour of NeIndia A tour of New Zealand, 2026w Zealand, 2026"
    n = len(s)
    if n % 2 == 0:
        k = n // 2
        for j in range(1, k):
            if s[j:j + k] == s[:j] + s[j + k:]:
                return s[j:j + k]
    return s


# ── hype ───────────────────────────────────────────────────────────────────
def _team_points(name):
    n = name.lower().strip()
    women = "women" in n
    if n == "india":
        return 100
    if n.startswith("india"):
        if "women" in n:
            return 50
        if n in ("india a", "india a women"):
            return 55
        return 35                       # India U19, India B ...
    base = n.replace(" women", "")
    if base in TOP_NATIONS:
        return 22 if women else 40
    return 0


def static_score(m):
    """Hype from the fixture alone - known before a ball is bowled."""
    s = max(_team_points(m["t1"]), _team_points(m["t2"]))
    ser = m["series"].lower()
    for key, pts in SERIES:
        if key in ser:
            s = max(s, pts) + min(s, pts) // 4
            break
    if not s:
        s = 12 if "tour of" in ser else 5
    names = (m["t1"] + " " + m["t2"]).lower()
    if "india" in names and "pakistan" in names:
        s += 60
    d = m["desc"].lower()
    if "final" in d and "semi" not in d and "quarter" not in d:
        s += 40
    elif "semi" in d:
        s += 25
    elif any(k in d for k in ("qualifier", "eliminator", "quarter")):
        s += 15
    fmt = FORMAT.get(m["format"], 0)
    if m["format"] == "TEST" and "india" in (m["t1"].lower(), m["t2"].lower()):
        fmt = 0                         # India men in a Test still pull a crowd
    s += fmt
    if "women" in names and "india" not in names:
        s -= 10
    return s


_buzz_cache = {}


def buzz(m):
    """Viewers on YouTube live streams of this fixture right now (0 without
    an API key). One search costs 100 units of the 10,000-a-day quota, so it
    is asked only for live picks and cached for an hour."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key or not m["live"]:
        return 0
    hit = _buzz_cache.get(m["mid"])
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    q = "%s vs %s live" % (m["t1"], m["t2"])
    total = 0
    try:
        u = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
            {"part": "id", "q": q, "type": "video", "eventType": "live",
             "videoCategoryId": "17", "maxResults": 15, "key": key})
        ids = [it["id"]["videoId"] for it in
               json.load(urllib.request.urlopen(u, timeout=20)).get("items", [])]
        if ids:
            u = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
                {"part": "liveStreamingDetails", "id": ",".join(ids), "key": key})
            for it in json.load(urllib.request.urlopen(u, timeout=20)).get("items", []):
                total += int((it.get("liveStreamingDetails") or {})
                             .get("concurrentViewers") or 0)
    except Exception as e:
        print("  [buzz] %s" % e)
        return 0
    _buzz_cache[m["mid"]] = (time.time(), total)
    return total


def hype(m, with_buzz=False):
    s = static_score(m)
    if with_buzz:
        v = buzz(m)
        m["viewers"] = v
        s += min(60, int(15 * math.log10(1 + v)))   # 1k watching -> +45
    m["hype"] = s
    return s


# ── today's picks ──────────────────────────────────────────────────────────
def ist_day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts + IST))


def today_candidates(matches, now=None):
    """Matches that can still be streamed today (IST): in play now, or
    starting before midnight. Multi-day games count on each day they play."""
    now = now or time.time()
    today = ist_day(now)
    out = []
    for m in matches:
        if m["done"]:
            continue
        if m["live"] or (m["start"] > now - 3600 and ist_day(m["start"]) == today):
            out.append(m)
    return out


def rank(matches, with_buzz=False):
    for m in matches:
        hype(m)
    if with_buzz:
        # only spend quota on live games near the top
        for m in sorted(matches, key=lambda x: -x["hype"])[:4]:
            if m["live"]:
                hype(m, with_buzz=True)
    return sorted(matches, key=lambda x: (-x["hype"], x["start"]))


def describe(m):
    when = time.strftime("%d %b %H:%M IST", time.gmtime(m["start"] + IST))
    v = " %d watching" % m["viewers"] if m.get("viewers") else ""
    return "[%3d%s] %-5s %s v %s - %s, %s (%s, %s)" % (
        m.get("hype", 0), v, m["format"], m["t1"], m["t2"], m["desc"],
        m["series"], when, m["state"])


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ms = schedule()
    print("  %d matches on the schedule" % len(ms))
    c = rank(today_candidates(ms), with_buzz="--buzz" in sys.argv)
    print("\n  Today's candidates, best first:")
    for m in c:
        print("   ", describe(m))
    picks = [m for m in c if m["hype"] >= MIN_SCORE][:MATCHES_PER_DAY]
    print("\n  Picks:", ", ".join("%s v %s" % (m["t1"], m["t2"]) for m in picks) or "none")
