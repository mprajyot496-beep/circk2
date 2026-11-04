# -*- coding: utf-8 -*-
"""
cricbuzz.py  -  live match data from Cricbuzz's own JSON endpoint.

    /api/mcenter/comm/{matchId}   ball-by-ball commentary + miniscore + header

No API key and no browser. The one catch: Cricbuzz fingerprints TLS, so plain
`requests` is refused. curl_cffi with Chrome impersonation makes the handshake
look like a real browser.

Endpoint discovery credit: github.com/omkarcloud/cricbuzz-scraper
"""
import re
import html

from curl_cffi import requests as creq

SITE = "https://www.cricbuzz.com"

# A T20 in play gives a ball every ~25s, a Test can be 45s+. Both work, but
# rank the busier game first when auto-picking.
FORMAT_RANK = {"T20": 0, "T20I": 0, "IT20": 0, "ODI": 1, "ODM": 1,
               "TEST": 3, "FIRST-CLASS": 3}

LIVE_STATES = ("in progress", "innings break", "rain", "drinks",
               "strategic timeout", "delay", "tea", "lunch", "stumps",
               "wet outfield")


FINAL_STATES = ("complete", "abandon", "no result", "cancel")


def finished(header):
    """Is the match really over? The header's `complete` flag is not enough:
    Cricbuzz sets it during an innings break too (seen live: complete=True,
    state "Innings Break", empty result). Over means a final state or a
    result."""
    state = str(header.get("state") or "").lower()
    return (any(s in state for s in FINAL_STATES)
            or bool((header.get("result") or {}).get("resultType")))


def _json(path, timeout=25):
    try:
        r = creq.get(SITE + path, impersonate="chrome", timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        return r.json()
    except Exception:
        return None


def _page(path, timeout=35):
    try:
        r = creq.get(SITE + path, impersonate="chrome", timeout=timeout)
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def clean(t):
    """Strip Cricbuzz's bold markers and HTML out of commentary text."""
    t = t.replace("\\n", " ").replace("\n", " ")
    t = re.sub(r"B\d+\$", "", t)
    t = html.unescape(t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_data(mid):
    """(entries, miniscore, matchHeader). entries oldest-first, each
    (timestamp, text, events, ballMetric) - ballMetric is 13.3 for the 3rd
    ball of the 14th over, None on notes."""
    d = _json("/api/mcenter/comm/%s" % mid)
    if not d:
        return [], {}, {}
    raw = d.get("matchCommentary") or {}
    items = raw.items() if isinstance(raw, dict) else enumerate(raw)
    entries = []
    for ts, c in items:
        if not isinstance(c, dict):
            continue
        txt = clean(c.get("commText") or "")
        if not txt:
            continue
        ev = c.get("event") or []
        if isinstance(ev, str):
            ev = [ev]
        try:
            tsi = int(ts)
        except (TypeError, ValueError):
            tsi = 0
        entries.append((tsi, txt, [str(x).lower() for x in ev],
                        c.get("ballMetric")))
    entries.sort(key=lambda e: e[0])
    return entries, (d.get("miniscore") or {}), (d.get("matchHeader") or {})


def scan(limit=14):
    """Everything Cricbuzz lists right now, annotated with its real state."""
    txt = _page("/cricket-match/live-scores")
    seen, found = set(), []
    for mid, slug in re.findall(r"/live-cricket-scores/(\d+)/([a-z0-9\-]+)", txt):
        if mid in seen:
            continue
        seen.add(mid)
        found.append((mid, slug))
        if len(found) >= limit:
            break

    out = []
    for mid, slug in found:
        _, mini, header = match_data(mid)
        if not header:
            continue
        state = str(header.get("state") or "").lower()
        out.append({
            "mid": mid,
            "title": slug.replace("-", " ").title(),
            "state": header.get("state") or "?",
            "format": str(header.get("matchFormat") or "?").upper(),
            "status": header.get("status") or "",
            "complete": finished(header),
            "playing": state in LIVE_STATES and not finished(header),
            "has_score": bool((mini.get("batTeam") or {}).get("teamScore")),
        })
    out.sort(key=lambda m: (not m["playing"],
                            FORMAT_RANK.get(m["format"], 2),
                            not m["has_score"]))
    return out


def best_live(matches):
    for m in matches:
        if m["playing"]:
            return m
    return None
