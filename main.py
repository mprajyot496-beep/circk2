# -*- coding: utf-8 -*-
"""
main.py  -  the only file you run.

    python main.py

Scans Cricbuzz, shows what is live, you pick a match, and then it opens a 3D
view of the ground and talks continuously in Hindi until you stop it. When the
match finishes it looks for the next live one by itself.

    python main.py --auto            skip the menu, take the best live match
    python main.py --match 151554    go straight to one match id
    python main.py --list            just show what is live and exit
    python main.py --no-visual       commentary only, no window

Threading: pygame wants the display on the main thread, so the render loop owns
it and the Cricbuzz poll loop runs behind it. They meet at visual.Scene, which
is lock-guarded.

    cricbuzz.match_data()        live feed
        -> situation.Situation   phase / chase pressure / over runs / milestones
        -> situation.Director    decides what to say, never a fixed rotation
        -> voice.Speaker         Kokoro/Piper TTS, gapless, self-feeding buffer
        -> visual.Scene          what the 3D view draws
"""
import sys
import time
import argparse
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pygame

import cricbuzz
import atmosphere
import visual
from situation import Situation, Director, parse_ball
import voice
import play as PL

POLL = 8          # seconds between commentary fetches
RESCAN = 45       # seconds between scans when nothing is in play

_stop = threading.Event()


def print_matches(matches):
    print("\n  === Cricbuzz right now ===")
    for i, m in enumerate(matches, 1):
        flag = "LIVE" if m["playing"] else ("DONE" if m["complete"] else "--  ")
        print("   %2d. [%s] %-6s %-50s %s"
              % (i, flag, m["format"], m["title"][:50], m["state"]))
    print()


def choose(matches):
    print_matches(matches)
    live_n = sum(1 for m in matches if m["playing"])
    if live_n:
        print("  %d match(es) in play. Blank = take the top live one." % live_n)
    while True:
        raw = input("  Pick a number (or match id): ").strip()
        if not raw:
            m = cricbuzz.best_live(matches)
            if m:
                return m
            print("  Nothing is in play - pick a number.")
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(matches):
            return matches[int(raw) - 1]
        hit = next((m for m in matches if m["mid"] == raw), None)
        if hit:
            return hit
        print("  Not a valid choice.")


BREAK_LABEL = {"innings": "INNINGS BREAK", "rain": "RAIN", "drinks": "DRINKS",
               "timeout": "TIME OUT", "stumps": "STUMPS", "lunch": "LUNCH",
               "tea": "TEA", "generic": "BREAK"}


def with_cues(lines, cues):
    """Attach callables to lines by index -> (text, emotion, cue) triples.
    The Speaker runs a line's cue the instant that line becomes audible. An
    index past the end rides on the last line, so a cue is never lost."""
    out = [[ln[0], ln[1], None] for ln in lines]
    if not out:
        return out
    for idx, fn in sorted(cues.items()):
        j = min(idx, len(out) - 1)
        prev = out[j][2]
        out[j][2] = fn if prev is None else (lambda a=prev, b=fn: (a(), b()))
    return [tuple(x) for x in out]


def ball_cues(ball, board, board_now, scene, result_at, burst_at,
              log=True):
    """What happens on screen and in the crowd, and on which line.

    line 0         the voice starts on this ball  -> 3D run-up and delivery
    line result_at the voice reaches the stroke   -> the 3D shot (for a big
                   moment, the build-up: "उठाया है... ऊपर... और ऊपर...")
    line burst_at  the voice announces the result -> score, RECENT chip,
                   centre box, and for a four / six / wicket the banner and
                   the crowd roar ("छक्का!!")

    `board` is the scoreboard snapshot to show (None for an older ball in a
    batch, whose score the feed has already moved past); `board_now` is the
    latest one, used only for who is batting and bowling.
    """
    kind = ball["kind"]
    big = kind in ("six", "four", "wicket")
    cues = {}
    land = shot = flash = None
    if scene:
        info = visual.delivery_info(ball, board_now) if board_now else None
        start, shot, land = scene.stage_ball(ball, board, log, info)
        cues[0] = start
        flash = scene.flash_cue(kind) if big else None

    # the crowd reacts to the little moments too: "ooh" at a play-and-miss
    # or a ball in the air, a groan at a dropped catch, applause for 2s and 3s
    play = PL.parse_play(ball.get("detail"))
    small = None
    if play["act"] == "chance":
        small = "drop"
    elif play["act"] in ("beaten", "edge") or (play["air"] and kind != "six"):
        small = "near"
    elif kind in ("two", "three"):
        small = kind

    def on_land():
        if land:
            land()
        if big:
            atmosphere.react(kind)
            if flash:
                flash()
        elif small:
            atmosphere.react(small, volume=0.42)
    r, b = max(1, result_at), max(1, burst_at)
    if shot:
        cues[r] = shot
    if b in cues:
        first = cues[b]
        cues[b] = lambda f=first: (f(), on_land())
    else:
        cues[b] = on_land
    return cues


def commentate(m, sp, sit, director, scene):
    """Talk through one match. Returns when it completes or the feed dies."""
    print("\n" + "=" * 68)
    print("  LIVE: %s" % m["title"])
    print("  %s | %s | %s" % (m["format"], m["state"], m["status"]))
    print("=" * 68 + "\n")

    seen = set()
    first = True
    dead_polls = 0
    last_stoppage = None

    while not _stop.is_set():
        entries, mini, header = cricbuzz.match_data(m["mid"])
        if not entries and not mini:
            dead_polls += 1
            if dead_polls > 8:
                print("  [feed unreachable - moving on]")
                return "lost"
            _stop.wait(POLL)
            continue
        dead_polls = 0
        sit.update(mini, header)
        # Snapshot for the overlay. It is NOT shown now - the voice is a few
        # seconds behind the feed, so it rides on the speech and appears when
        # the commentator reaches the ball it belongs to.
        board = visual.make_board(sit, header) if scene else None

        new = [e for e in entries if e[0] not in seen]
        for e in entries:
            seen.add(e[0])

        joining = first
        if first:
            first = False
            if scene:
                scene.reset(header.get("seriesName") or m["title"])
                scene.show(board)               # nothing is queued yet
                # RECENT from the deliveries already in the feed (exact), or
                # the feed's summary string when they carry no ball numbers
                past = [b for b in (parse_ball(e[1], e[2], e[3])
                                    for e in entries) if b]
                if not scene.seed_balls(past):
                    scene.seed_recent(mini.get("recentOvsStats"),
                                      sit.overs_raw)
            new = new[-1:]                      # start at the live edge
            sp.say_many(director.opening_lines(), "event")

        # Only the newest delivery in this batch matches the miniscore; older
        # ones must not read out a score they did not produce.
        last_ball = max((i for i, e in enumerate(new)
                         if parse_ball(e[1], e[2])), default=-1)

        # -- real deliveries get priority -----------------------------------
        staged = False
        for i, (_ts, text, events, metric) in enumerate(new):
            ball = parse_ball(text, events, metric)
            if ball:
                lines = director.ball_lines(ball, latest=(i == last_ball))
                if not lines:
                    continue                    # duplicate wicket line
                shown = visual.board_for_ball(board, ball) \
                    if (scene and i == last_ball) else None
                cues = ball_cues(ball, shown, board, scene,
                                 director.result_at, director.burst_at,
                                 log=not joining)
                sp.say_many(with_cues(lines, cues), "ball")
                staged = True
            else:
                # English match note: speak it only if we can turn it into
                # proper Hindi, otherwise print it and stay quiet rather than
                # let the Hindi voice mangle English prose.
                note = director.note_line(text)
                if note:
                    sp.say(note[0], "note", note[1])
                else:
                    print("  [skip] %s" % text[:90])

        # A board change with no delivery behind it (new batter walks in,
        # bowler changes) waits until the voice has caught up, then shows.
        if scene and not staged and not joining:
            scene.defer(board)

        # -- milestones ------------------------------------------------------
        milestones = director.milestone_lines()
        if milestones:
            big = any(ms["mark"] >= 100 for ms in sit.new_milestones)
            sp.say_many(with_cues(milestones, {0: lambda: (
                atmosphere.react("milestone"),
                scene and scene.event_cue("CENTURY" if big else "FIFTY",
                                          visual.YELLOW)())}), "event")

        # -- over break: a real block of talk, not more small talk ----------
        if sit.over_just_ended:
            lines = director.over_break_lines()
            if scene:
                # the board moves to the next over's bowler with the break
                lines = with_cues(lines, {0: scene.event_cue(
                    "OVER", world="over", board=board)})
            sp.say_many(lines, "over")

        # -- rain / drinks / innings break ----------------------------------
        if sit.stoppage != last_stoppage:
            last_stoppage = sit.stoppage
            if sit.stoppage:
                lines = director.stoppage_lines()
                if scene:
                    lines = with_cues(lines, {0: scene.event_cue(
                        BREAK_LABEL.get(sit.stoppage, "BREAK"),
                        visual.YELLOW, world="break")})
                sp.say_many(lines, "break")

        # -- finished? -------------------------------------------------------
        if header.get("complete"):
            lines = director.result_lines(header.get("status"))
            if scene:
                lines = with_cues(lines, {0: scene.event_cue("RESULT",
                                                             visual.YELLOW)})
            sp.say_many(lines, "event")
            print("\n  [complete: %s]\n" % header.get("status"))
            sp.drain(25)
            return "done"

        _stop.wait(POLL)
    return "stopped"


def follow_matches(chosen, sp, sit, director, scene):
    """Commentate `chosen`, then whatever is live after it. Runs in a thread."""
    try:
        while not _stop.is_set():
            commentate(chosen, sp, sit, director, scene)
            if _stop.is_set():
                return
            print("  Looking for the next live match...")
            _stop.wait(5)
            matches = cricbuzz.scan()
            nxt = cricbuzz.best_live(matches)
            while nxt is None and not _stop.is_set():
                print("  Nothing in play. Rescanning in %ds..." % RESCAN)
                _stop.wait(RESCAN)
                matches = cricbuzz.scan()
                nxt = cricbuzz.best_live(matches)
            if _stop.is_set():
                return
            chosen = nxt
            sit.__init__()               # fresh context for the new match
            director.__init__(sit)
    except Exception as e:
        print("  [commentary thread] %s" % e)


def main():
    ap = argparse.ArgumentParser(
        description="Live Hindi cricket commentary with a 3D ground view")
    ap.add_argument("--list", action="store_true", help="show live matches, exit")
    ap.add_argument("--auto", action="store_true", help="skip the menu")
    ap.add_argument("--match", help="Cricbuzz match id")
    ap.add_argument("--no-visual", action="store_true",
                    help="commentary only, no window")
    args = ap.parse_args()

    if not voice.ensure_voice():
        sys.exit(1)

    print("  Scanning Cricbuzz...")
    matches = cricbuzz.scan()
    if not matches:
        sys.exit("  Cricbuzz unreachable - check your connection.")

    if args.list:
        print_matches(matches)
        return

    if args.match:
        chosen = next((m for m in matches if m["mid"] == args.match), None) or {
            "mid": args.match, "title": "match %s" % args.match, "format": "?",
            "state": "?", "status": "", "playing": True, "complete": False}
    elif args.auto:
        chosen = cricbuzz.best_live(matches) or matches[0]
    else:
        chosen = choose(matches)

    # pre_init fixes the mixer format before pygame.init() claims it
    pygame.mixer.pre_init(44100, -16, 2, 1024)
    pygame.init()
    if not pygame.mixer.get_init():
        pygame.mixer.init(44100, -16, 2, 1024)
    atmosphere.init()
    atmosphere.start_ambient()

    sit = Situation()
    director = Director(sit)
    scene = None if args.no_visual else visual.Scene()

    def on_say(text, kind, emotion="neutral"):
        tag = {"ball": "BALL", "filler": "....", "over": "OVER",
               "event": "EVNT", "break": "BRK ", "note": "NOTE"}.get(kind, "    ")
        print("  [%s|%-7s] %s" % (tag, emotion, text), flush=True)
        if scene:
            scene.line_started(kind)

    print("  Loading Hindi voice (%s)..." % (
        "kokoro/" + voice.KOKORO_VOICE if voice.kokoro_available()
        else voice.VOICE_NAME))
    # What the commentator talks about between balls steers the camera:
    # a line about the batsman cuts to the batsman and brings up his card.
    focus_for = {"striker": "striker", "boundaries": "striker",
                 "sr": "striker", "nonstriker": "nonstriker",
                 "bowler": "bowler", "partnership": "pair",
                 "score": "ground", "crr": "ground", "overs": "ground",
                 "recap": "ground", "cta": "crowd", "address": "crowd"}

    def filler():
        text, emotion = director.filler_line()
        role = focus_for.get(director.last_topic)
        if scene and role and text:
            return text, emotion, scene.focus_cue(role)
        return text, emotion

    sp = voice.Speaker(filler_cb=filler, on_say=on_say)
    sp.start()

    worker = threading.Thread(
        target=follow_matches, args=(chosen, sp, sit, director, scene),
        daemon=True)
    worker.start()

    try:
        if scene:
            view = visual.Visual(scene, "%s - Live Hindi Commentary"
                                 % chosen["title"][:48])
            while view.pump():
                pass
        else:
            while worker.is_alive():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  रोक रहे हैं... धन्यवाद!")
        _stop.set()
        sp.stop()
        atmosphere.stop()
        pygame.quit()


if __name__ == "__main__":
    main()
