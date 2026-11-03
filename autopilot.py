# -*- coding: utf-8 -*-
"""
autopilot.py  -  the channel on its own: every day, the two most-hyped matches,
each innings as its own live stream (4 streams a day).

    python autopilot.py --slot 1 --forever          a server (Oracle VM): never exits
    python autopilot.py --slot 1 --max-minutes 330  a GitHub runner: hands over
                                                    before the 6 h job limit
    python autopilot.py --slot 1 --check            exit 0 if there is work now
    python autopilot.py --plan                      what it would do today

Two slots so two matches can overlap. Each slot:

  1. still has a live broadcast?  -> its match/part still on: resume into it
                                     (a GitHub job ran out of time); else end it
  2. its match from today has another part coming (2nd innings, next Test
     day)?                         -> wait for it, stream it
  3. fewer than MATCHES_PER_DAY started today? -> take the next pick
     (picker.py: India first, then hype and live YouTube viewers). A pick
     that has not started is waited for - the day's slot is not spent on a
     lesser game that happens to be live earlier.

A part ends at the innings break / stumps / result, after a grace period so
the commentator finishes the break talk, and the broadcast is completed.

Nothing is stored locally: what was streamed is read back from the channel's
own broadcasts (the `ref:` tag ytlive.py writes into each description).
"""
import os
import re
import sys
import time
import argparse
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

import ytlive
ytlive.load_env()

import cricbuzz
import picker

POLL = 20                 # seconds between Cricbuzz checks while streaming
GRACE = 80                # seconds of break / result talk before cutting
MAX_IDLE_LIVE = 90 * 60   # a stoppage this long (rain) ends the broadcast
SOON = 25 * 60            # a pick starting within this is waited for
LIVE_BCAST = ("created", "ready", "testing", "liveStarting", "live")
CHANNEL = os.environ.get("CHANNEL_NAME", "")

SOON_STATES = ("innings break", "drinks", "strategic timeout", "lunch", "tea",
               "rain", "delay", "wet outfield", "toss")


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ── where a match is ───────────────────────────────────────────────────────
def where(mid):
    """The match right now: which part is on, and whether play is live."""
    _, mini, h = cricbuzz.match_data(mid)
    if not h:
        return None
    fmt = str(h.get("matchFormat") or "").upper()
    state = str(h.get("state") or "").lower()
    inn = int(mini.get("inningsId") or 1)
    test = fmt in ("TEST", "FIRST-CLASS")
    if test:
        m = re.search(r"Day (\d+)", h.get("status") or "")
        start = (h.get("matchStartTimestamp") or 0) / 1000.0
        day = int(m.group(1)) if m else 1 + int(max(0, time.time() - start) // 86400)
        key, label = "d%d" % day, "Day %d" % day
    else:
        key = "1" if inn <= 1 else "2"
        label = "1st Innings" if key == "1" else "2nd Innings"
    return {"key": key, "label": label, "state": state, "test": test,
            "playing": state == "in progress",
            "complete": bool(h.get("complete")), "header": h}


def part_over(w, part):
    if w["complete"] or w["key"] != part:
        return True
    if w["test"]:
        return w["state"] == "stumps"
    return part == "1" and w["state"] == "innings break"


# ── titles ─────────────────────────────────────────────────────────────────
def metadata(m, label):
    t1, t2 = m["t1"], m["t2"]
    desc, series = m["desc"], m["series"]
    s1, s2 = m["s1"] or t1, m["s2"] or t2
    # longest that fits YouTube's 100 characters
    options = [
        "🔴 LIVE: %s vs %s, %s | %s | Live Score & Hindi Commentary" % (t1, t2, desc, label),
        "🔴 LIVE: %s vs %s, %s | %s | Hindi Commentary" % (t1, t2, desc, label),
        "🔴 LIVE: %s vs %s | %s | Live Score & Hindi Commentary" % (t1, t2, label),
        "🔴 LIVE: %s vs %s | %s | Hindi Commentary" % (t1, t2, label),
        "🔴 LIVE: %s vs %s, %s | %s | Hindi Commentary" % (s1, s2, desc, label),
    ]
    title = next((t for t in options if len(t) <= 100), options[-1][:100])
    tag1, tag2 = re.sub(r"\W", "", t1), re.sub(r"\W", "", t2)
    text = "\n".join([
        "%s vs %s Live | %s, %s" % (t1, t2, desc, series),
        "%s - live score, ball by ball Hindi commentary aur 3D match view." % label,
        "",
        "🏏 Har ball ki Hindi commentary, live scorecard, run rate, partnership aur match analysis.",
        "👍 Like karo, Subscribe karo aur Bell icon dabao - har bade match ka live yahin milega!",
        "",
        "⚠️ Disclaimer: Yeh official match broadcast nahi hai. Video mein animated "
        "scoreboard aur 3D simulation hai jo live score data se chalta hai, aur "
        "commentary AI Hindi voice mein hai. Kisi broadcaster ki footage use nahi hoti.",
        "",
        "#%svs%s #LiveScore #HindiCommentary #CricketLive #%s" % (
            tag1, tag2, re.sub(r"\W", "", m["s1"] + "vs" + m["s2"]) or "Cricket"),
    ])
    tags = [t1, t2, "%s vs %s" % (t1, t2), "%s vs %s live" % (t1, t2),
            "%s vs %s live score" % (m["s1"], m["s2"]), series,
            "live cricket", "live score", "cricket live today",
            "hindi commentary", "live cricket match today", desc]
    return title, text, [t for t in tags if t]


# ── the slot's work ────────────────────────────────────────────────────────
def schedule_entry(mid, sched=None):
    sched = sched if sched is not None else picker.schedule()
    hit = next((m for m in sched if m["mid"] == str(mid)), None)
    if hit:
        return hit
    # off the schedule pages (finished elsewhere): rebuild from the header
    w = where(mid)
    h = (w or {}).get("header") or {}
    t1, t2 = h.get("team1") or {}, h.get("team2") or {}
    return {"mid": str(mid), "t1": t1.get("name", "?"), "t2": t2.get("name", "?"),
            "s1": t1.get("shortName", ""), "s2": t2.get("shortName", ""),
            "id1": t1.get("id"), "id2": t2.get("id"), "img1": None, "img2": None,
            "series": h.get("seriesName", ""), "desc": h.get("matchDescription", ""),
            "format": str(h.get("matchFormat") or "?").upper(),
            "start": (h.get("matchStartTimestamp") or 0) / 1000.0,
            "state": h.get("state", ""), "live": True, "done": False}


def decide(slot, with_buzz=True):
    """What this slot should do now:
         ("resume", broadcast, match)  a broadcast of ours is still live
         ("stream", None, match, w)    start the part that is on now
         ("wait", seconds, match)      a part / pick starts soon
         ("idle", reason)
    """
    bc = ytlive.recent(30)
    now = time.time()

    # 1. our own broadcast still open
    for b in bc:
        if b["slot"] == slot and b["status"] in LIVE_BCAST and b["mid"]:
            w = where(b["mid"])
            if w and not part_over(w, b["part"]):
                return ("resume", b, schedule_entry(b["mid"]), w)
            log("closing %s (%s) - its part is over" % (b["id"], b["title"]))
            ytlive.finish(b["id"])

    bc = [b for b in bc if b["mid"]]
    today = picker.ist_day(now)
    used_today = []
    for b in sorted(bc, key=lambda b: b["created"]):
        if picker.ist_day(b["created"]) == today and b["mid"] not in used_today:
            used_today.append(b["mid"])
    owner = {}                                  # match -> slot, latest wins
    for b in sorted(bc, key=lambda b: b["created"]):
        owner[b["mid"]] = b["slot"]

    # 2. our match has another part coming
    other_busy = False
    for mid, s in owner.items():
        w = where(mid)
        if not w or w["complete"]:
            continue
        if s != slot:
            other_busy = True
            continue
        # play is on and none of our broadcasts is open (step 1): the next
        # innings / day, or the same part again after a long rain stop
        if w["playing"]:
            return ("stream", None, schedule_entry(mid), w)
        if w["state"] in SOON_STATES:
            return ("wait", 60, schedule_entry(mid))

    # 3. a new pick
    if len(used_today) >= picker.MATCHES_PER_DAY:
        return ("idle", "today's %d matches are taken: %s"
                % (picker.MATCHES_PER_DAY, ", ".join(used_today)))
    sched = picker.schedule()
    cands = [c for c in picker.rank(picker.today_candidates(sched, now),
                                    with_buzz=with_buzz)
             if c["hype"] >= picker.MIN_SCORE and c["mid"] not in owner]
    picks = cands[:picker.MATCHES_PER_DAY - len(used_today)]
    idx = 1 if (slot == 2 and not other_busy) else 0
    if idx >= len(picks):
        return ("idle", "no pick for slot %d (candidates: %s)" % (
            slot, "; ".join(picker.describe(c) for c in cands[:3]) or "none"))
    m = picks[idx]
    w = where(m["mid"])
    if w and w["playing"]:
        return ("stream", None, m, w)
    if (w and w["state"] in SOON_STATES) or 0 < m["start"] - now < SOON:
        return ("wait", 60, m)
    return ("idle", "next pick %s" % picker.describe(m))


def go_live(slot, m, w, stream_id):
    bc = ytlive.recent(30)
    n = sum(1 for b in bc if b["mid"] == m["mid"] and b["part"] == w["key"])
    label = w["label"] + (" (Part %d)" % (n + 1) if n else "")
    title, text, tags = metadata(m, label)
    import thumbnail
    try:
        thumb = thumbnail.render(m, label, channel=CHANNEL)
    except Exception as e:
        log("thumbnail failed: %s" % e)
        thumb = None
    ref = "ref: cb%s p%s s%d" % (m["mid"], w["key"], slot)
    bid = ytlive.create(title, text, ref, stream_id, thumb, tags,
                        os.environ.get("YT_PRIVACY", "public"))
    log("broadcast %s created: %s" % (bid, title))
    return {"id": bid, "mid": m["mid"], "part": w["key"], "title": title}


def stream_part(slot, b, rtmp, deadline):
    """Stream one part into broadcast b until it ends (True) or the job's
    time is up (False - the broadcast is left open for the next run)."""
    from streamer import Streamer
    s = Streamer(slot, b["mid"], rtmp)
    restarts, quiet_since, over_since = 0, None, None
    try:
        s.start()
        log("on air: %s" % b["title"])
        while True:
            if time.time() > deadline:
                log("job time is up - leaving %s open for the next run" % b["id"])
                return False
            if not s.engine_alive():
                restarts += 1
                log("engine stopped (restart %d):\n%s" % (restarts, s.engine_tail(800)))
                if restarts > 6:
                    return True
                s.stop()
                time.sleep(5)
                s = Streamer(slot, b["mid"], rtmp)
                s.start()
            elif not s.ffmpeg_alive():
                log("ffmpeg dropped - reconnecting:\n%s" % s._tail(s.ffmpeg_log, 600))
                s.restart_ffmpeg()
            w = where(b["mid"])
            if w:
                if part_over(w, b["part"]):
                    over_since = over_since or time.time()
                    if time.time() - over_since > GRACE:
                        log("part over (%s)" % w["state"])
                        return True
                else:
                    over_since = None
                if w["playing"]:
                    quiet_since = None
                else:
                    quiet_since = quiet_since or time.time()
                    if time.time() - quiet_since > MAX_IDLE_LIVE:
                        log("no play for %d min (%s) - ending this broadcast"
                            % (MAX_IDLE_LIVE // 60, w["state"]))
                        return True
            time.sleep(POLL)
    finally:
        s.stop()


def run(slot, deadline, forever, check=False):
    stream_id = rtmp = None
    while time.time() < deadline:
        try:
            if slot == 2 and not check:
                time.sleep(45)          # let slot 1 claim its pick first
            d = decide(slot, with_buzz=not check)
            kind = d[0]
            if check:
                what = kind if kind != "idle" else "idle - " + d[1]
                log("slot %d: %s" % (slot, what))
                if os.environ.get("GITHUB_ACTIONS"):
                    # an annotation: readable on the run page without logs
                    print("::notice title=slot %d::%s" % (slot, what))
                return kind != "idle"
            if kind == "idle":
                log("slot %d idle: %s" % (slot, d[1]))
                if not forever:
                    return
                time.sleep(180)
                continue
            if kind == "wait":
                log("slot %d waiting for %s v %s (%s)" % (slot, d[2]["t1"], d[2]["t2"],
                                                         d[2].get("state")))
                time.sleep(d[1])
                continue
            if stream_id is None:
                stream_id, rtmp = ytlive.stream_for_slot(slot)
            if kind == "resume":
                b = d[1]
                log("resuming %s" % b["title"])
            else:
                b = go_live(slot, d[2], d[3], stream_id)
            if stream_part(slot, b, rtmp, deadline):
                ytlive.finish(b["id"])
                log("broadcast %s finished" % b["id"])
        except KeyboardInterrupt:
            raise
        except Exception:
            log("error:\n" + traceback.format_exc())
            time.sleep(60)


def plan():
    sched = picker.schedule()
    c = picker.rank(picker.today_candidates(sched), with_buzz=True)
    print("\n  Today's candidates (hype >= %d is streamable):" % picker.MIN_SCORE)
    for m in c[:12]:
        print("   ", picker.describe(m))
    try:
        for b in ytlive.recent(30):
            print("  ours: %-12s %-9s slot %s  %s" % (b["id"], b["status"],
                                                      b["slot"], b["title"]))
    except SystemExit as e:
        print("  (no YouTube login yet: %s)" % e)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--slot", type=int, default=1, choices=(1, 2))
    ap.add_argument("--forever", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=330)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()
    if a.plan:
        return plan()
    deadline = float("inf") if a.forever else time.time() + a.max_minutes * 60
    if a.check:
        if not os.environ.get("YT_REFRESH_TOKEN"):
            log("YouTube is not connected yet (secret YT_REFRESH_TOKEN) - nothing to do")
            if os.environ.get("GITHUB_ACTIONS"):
                print("::warning title=not connected::secret YT_REFRESH_TOKEN is missing")
            work = False
        else:
            work = run(a.slot, deadline, False, check=True)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("work=%s\n" % ("1" if work else "0"))
        return
    run(a.slot, deadline, a.forever)


if __name__ == "__main__":
    main()
