# -*- coding: utf-8 -*-
"""
voice.py  -  gap-free Hindi speech with Piper TTS (fully offline)

Piper measures at 0.26x realtime on this machine: ~1.1s of CPU to produce
~4.2s of audio. One synth worker therefore stays comfortably ahead of
playback, and because it is local there is no network to stall on.

Three things keep the stream seamless:

1. LOOKAHEAD SYNTHESIS - a worker runs ahead of playback and keeps a small
   buffer of ready clips.
2. GAPLESS HANDOFF - all speech goes out on ONE pygame channel via
   Channel.queue(), which starts the next clip the instant the current ends.
3. SELF-FEEDING BUFFER - when the buffer dips, the speaker pulls another line
   from its filler callback itself. It never waits on the match poll loop, so
   a slow Cricbuzz request, an over break or an innings break cannot create
   silence.

Audio goes to pygame as an in-memory array (no temp files): Piper gives 22.05k
mono int16, which is resampled to the mixer's rate and duplicated to stereo.
"""
import os
import time
import random
import queue
import threading

import numpy as np
import pygame

from piper import PiperVoice, SynthesisConfig

try:
    from kokoro_onnx import Kokoro
except ImportError:          # Kokoro is optional; Piper is the fallback
    Kokoro = None

import translit

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(HERE, "voices")

# hi_IN-pratham-medium is the male Hindi voice; -rohan- is the alternative,
# -priyamvada- is female. Download more with:
#   python -m piper.download_voices hi_IN-rohan-medium
VOICE_NAME = "hi_IN-pratham-medium"

# What a ball interrupts: talk that is stale once the next delivery comes
STALE_FOR_BALL = ("filler", "over", "break", "note")

BUFFER_TARGET = 2        # ready-but-unplayed clips (~6-9s of speech)
SYNTH_THREADS = 3        # CPU threads for the voice; the rest keep the 3D smooth

# ── Emotion ─────────────────────────────────────────────────────────────────
# A real commentator does not read every line at one speed: a six is shouted
# fast and loud, a quiet passage of play is slower and softer. Piper has no
# SSML, but length_scale and volume per utterance give genuine variation.
#
#   excited  six / wicket / milestone - fast, loud
#   lively   four, a ball in the death overs
#   neutral  an ordinary delivery
#   calm     between-ball filler and colour
#   slow     over breaks, rain, innings break
# length_scale: HIGHER is slower. Measured on this voice, same sentence:
#   0.88 -> 197 wpm    1.15 -> 172 wpm    1.38 -> 150 wpm
#   1.05 -> 181 wpm    1.28 -> 158 wpm    1.50 -> 135 wpm
# The original 0.88 baseline chased the reference stream's rate and was too
# fast to follow; 1.28 overshot the other way and dragged. Neutral now sits
# near 172 wpm, between the two, with a ~38 wpm spread from excited to slow so
# the emotion stays clearly audible rather than just labelled.
EMOTION = {
    # A person's pace moves a little with the moment, not 40%: the old range
    # (0.95 .. 1.35) made neighbouring lines lurch fast-slow-fast, and high
    # noise_scale made the pitch wobble - both read as a cartoon. Now the
    # whole range is ~18% and expressiveness sits near Piper's defaults
    # (0.667 / 0.8).
    "excited": {"length_scale": 1.00, "volume": 1.00,
                "noise_scale": 0.68, "noise_w_scale": 0.82},
    "amazed":  {"length_scale": 1.03, "volume": 0.98,
                "noise_scale": 0.68, "noise_w_scale": 0.82},
    "lively":  {"length_scale": 1.07, "volume": 0.97,
                "noise_scale": 0.667, "noise_w_scale": 0.80},
    "neutral": {"length_scale": 1.12, "volume": 0.93,
                "noise_scale": 0.667, "noise_w_scale": 0.80},
    "tense":   {"length_scale": 1.12, "volume": 0.91,
                "noise_scale": 0.64, "noise_w_scale": 0.78},
    "calm":    {"length_scale": 1.16, "volume": 0.89,
                "noise_scale": 0.62, "noise_w_scale": 0.76},
    "slow":    {"length_scale": 1.20, "volume": 0.86,
                "noise_scale": 0.60, "noise_w_scale": 0.74},
}
DEFAULT_EMOTION = "neutral"

# ── Broadcast tone ──────────────────────────────────────────────────────────
# The raw Piper voice sits at a 108 Hz median (rohan, the other male Hindi
# voice, measured 159 Hz - higher, so pratham stays). Three things turn it
# into a commentary-box voice:
#
#   pitch  resample ratio. <1 deepens. Synthesis runs faster by the same
#          factor, so the tempo is unchanged - deeper, not slower.
#          Calm talk sits ~10% below the raw voice; a shout comes back UP
#          above it, because that is what a real commentator's voice does.
#   loud   peak level. Calm ~-4 dB, a shout at full scale: big moments are
#          audibly louder, not just faster.
#   drive  saturation. A little warmth on normal lines, real strain on a
#          shout - the grit of someone yelling into a mic over a crowd.
#
# Every line also goes through a broadcast EQ (rumble cut, chest warmth,
# mud cut, presence for clarity over the crowd) and a compressor.
TONE = {
    # Same voice on every line - the pitch moves at most ~2% from normal
    # talk. (An earlier tuning raised shouts 13% with heavy saturation, and
    # they sounded like a second commentator.) A shout is loud because it is
    # packed harder - more compression, full level - not because it changes
    # the speaker.
    "excited": {"pitch": 0.93, "loud": 1.00, "drive": 1.45, "ratio": 12.0,
                "thr": 0.025},
    "amazed":  {"pitch": 0.92, "loud": 0.97, "drive": 1.35, "ratio": 8.0,
                "thr": 0.035},
    "lively":  {"pitch": 0.92, "loud": 0.92, "drive": 1.25, "ratio": 6.0,
                "thr": 0.05},
    "tense":   {"pitch": 0.90, "loud": 0.72, "drive": 1.10, "ratio": 3.0,
                "thr": 0.10},
    "neutral": {"pitch": 0.91, "loud": 0.70, "drive": 1.15, "ratio": 3.0,
                "thr": 0.10},
    "calm":    {"pitch": 0.89, "loud": 0.66, "drive": 1.00, "ratio": 3.0,
                "thr": 0.10},
    "slow":    {"pitch": 0.88, "loud": 0.62, "drive": 1.00, "ratio": 3.0,
                "thr": 0.10},
}


def breath(text, emotion, kind):
    """Seconds of silence after an utterance. Short: lines now flow into
    one another inside an utterance, and long gaps between them (0.3-0.6 s
    before) made the voice stop and start - "atak atak ke"."""
    if emotion in ("excited", "amazed"):
        return random.uniform(0.04, 0.08)
    if kind in ("filler", "over", "break", "event", "note"):
        return random.uniform(0.18, 0.30)
    return random.uniform(0.10, 0.16)


# ── One flow: lines of similar energy are said as one utterance ────────────
_TIER = {"excited": 2, "amazed": 2, "lively": 1, "neutral": 1, "tense": 1,
         "calm": 0, "slow": 0}
_RANK = ["excited", "amazed", "lively", "tense", "neutral", "calm", "slow"]
FLOW_CHARS = 170


def clean_for_voice(t):
    """'...' made the voice pause dramatically and '!!' over-act; a person
    just runs on with a comma."""
    t = str(t or "").strip()
    t = t.replace("…", "...")
    while "...." in t:
        t = t.replace("....", "...")
    t = t.replace("...", ",").replace("!!", "!").replace(",,", ",")
    t = t.replace(" ,", ",").strip(" ,")
    return t


def group_lines(lines):
    """[(text, emotion, cue)] -> [(parts, emotion)] where parts is
    [(text, cue)] said as ONE utterance. A new utterance starts when the
    energy changes tier (talk -> shout -> talk) or it gets long."""
    groups = []
    for text, emo, cue in lines:
        text = clean_for_voice(text)
        if not text and not cue:
            continue
        tier = _TIER.get(emo, 1)
        g = groups[-1] if groups else None
        if g and g["tier"] == tier and \
                g["chars"] + len(text) < FLOW_CHARS:
            g["parts"].append((text, cue))
            g["chars"] += len(text)
            g["emos"].append(emo)
        else:
            groups.append({"tier": tier, "parts": [(text, cue)],
                           "chars": len(text), "emos": [emo]})
    out = []
    for g in groups:
        emo = min(g["emos"], key=lambda e: _RANK.index(e)
                  if e in _RANK else 9)
        out.append((g["parts"], emo))
    return out


def join_parts(parts):
    """Join spoken parts with commas (keeping ! and ?), returning the text
    and where each part starts (fraction of the text)."""
    txt, starts = "", []
    for t, _ in parts:
        if not t:
            starts.append(len(txt))
            continue
        if txt and txt[-1] not in "!?।,":
            txt += ","
        if txt:
            txt += " "
        starts.append(len(txt))
        txt += t
    total = max(1, len(txt))
    return txt, [st / total for st in starts]


def _biquad(kind, f0, fs, gain_db=0.0, q=0.707):
    """RBJ audio-EQ-cookbook biquad as one scipy sos row."""
    A = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f0 / fs
    c, s = np.cos(w), np.sin(w)
    al = s / (2 * q)
    if kind == "hp":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + al, -2 * c, 1 - al]
    elif kind == "peak":
        b = [1 + al * A, -2 * c, 1 - al * A]
        a = [1 + al / A, -2 * c, 1 - al / A]
    elif kind in ("lowshelf", "highshelf"):
        sq = 2 * np.sqrt(A) * al
        sg = 1 if kind == "lowshelf" else -1
        b = [A * ((A + 1) - sg * (A - 1) * c + sq),
             sg * 2 * A * ((A - 1) - sg * (A + 1) * c),
             A * ((A + 1) - sg * (A - 1) * c - sq)]
        a = [(A + 1) + sg * (A - 1) * c + sq,
             -sg * 2 * ((A - 1) + sg * (A + 1) * c),
             (A + 1) + sg * (A - 1) * c - sq]
    b = np.array(b) / a[0]
    a = np.array(a) / a[0]
    return np.concatenate([b, a])


def broadcast_eq(fs):
    return np.array([
        _biquad("hp", 75, fs, q=0.7),               # rumble / plosive thump
        _biquad("lowshelf", 160, fs, 3.5),          # chest: the deep voice
        _biquad("peak", 450, fs, -2.0, 1.0),        # boxy mud
        _biquad("peak", 3000, fs, 3.0, 0.9),        # presence: cuts the crowd
        _biquad("highshelf", 8000, fs, 1.0),        # a little air
    ])


def broadcast(a, fs, sos, tone):
    """EQ -> compressor -> saturation -> level. float in, float out (int16
    scale)."""
    from scipy.signal import sosfilt
    x = np.asarray(a, np.float32) / 32768.0
    if x.size == 0:
        return x
    x = sosfilt(sos, x).astype(np.float32)
    # compressor on a 12 ms RMS envelope: 3:1 for talk, harder for a shout
    w = max(1, int(fs * 0.012))
    env = np.sqrt(np.convolve(x * x, np.ones(w) / w, "same")) + 1e-6
    thr, ratio = tone.get("thr", 0.10), tone.get("ratio", 3.0)
    g = np.where(env > thr, (thr * (env / thr) ** (1 / ratio)) / env, 1.0)
    g = np.convolve(g, np.ones(w) / w, "same")
    x = x * g
    peak = float(np.max(np.abs(x))) or 1.0
    x = x / peak
    d = tone["drive"]
    x = np.tanh(x * d) / np.tanh(d)
    return x * tone["loud"] * 0.97 * 32767.0

# ── Kokoro (preferred backend) ──────────────────────────────────────────────
# Kokoro-82M sounds more natural than Piper and has a male Hindi voice, but like
# Piper it has no emotion control. Emotion is built from three levers instead:
#   speed  - speaking rate handed to the model
#   pitch  - resample ratio (>1 lifts pitch); speed is divided by it so pitch
#            changes without also changing the tempo
#   gain   - loudness, soft-clipped so shouts stay punchy but never harsh
# Setup:  download kokoro-v1.0.onnx and voices-v1.0.bin into voices/kokoro/
# (github.com/thewh1teagle/kokoro-onnx releases, tag model-files-v1.0).
KOKORO_DIR = os.path.join(VOICE_DIR, "kokoro")
KOKORO_VOICE = "hm_omega"        # alternative male Hindi voice: hm_psi
KOKORO_EMOTION = {
    "excited": {"speed": 1.10, "pitch": 1.07, "gain": 1.00},
    "lively":  {"speed": 1.04, "pitch": 1.03, "gain": 0.96},
    "neutral": {"speed": 0.98, "pitch": 1.00, "gain": 0.92},
    "calm":    {"speed": 0.93, "pitch": 0.98, "gain": 0.87},
    "slow":    {"speed": 0.87, "pitch": 0.97, "gain": 0.84},
    "tense":   {"speed": 0.94, "pitch": 0.99, "gain": 0.90},
    "amazed":  {"speed": 1.04, "pitch": 1.05, "gain": 0.98},
}


def kokoro_paths():
    return (os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"),
            os.path.join(KOKORO_DIR, "voices-v1.0.bin"))


def kokoro_available():
    """Opt-in (set USE_KOKORO=1). Measured on this machine its Hindi has ~2x
    the internal pauses of Piper - word-by-word, stop-start delivery - so Piper
    stays the default."""
    if os.environ.get("USE_KOKORO") != "1":
        return False
    return Kokoro is not None and all(os.path.exists(p) for p in kokoro_paths())


def voice_path(name=VOICE_NAME):
    return os.path.join(VOICE_DIR, name + ".onnx")


def ensure_voice(name=VOICE_NAME):
    """True if a usable model is on disk, else print how to fetch it."""
    if kokoro_available():
        return True
    p = voice_path(name)
    if os.path.exists(p):
        return True
    print("  Hindi voice model missing: %s" % p)
    print("  Get it with:")
    print("      cd voices && python -m piper.download_voices %s" % name)
    return False


class Speaker:
    """Continuous Hindi speech. say() for live events, filler_cb for the gaps."""

    def __init__(self, filler_cb=None, on_say=None, voice_name=VOICE_NAME):
        self.filler_cb = filler_cb
        self.on_say = on_say or (lambda t, k, e=None: None)

        self._kokoro = Kokoro(*kokoro_paths()) if kokoro_available() else None
        if self._kokoro:
            self._src_rate = 24000
        else:
            self._voice = PiperVoice.load(voice_path(voice_name))
            # Piper's default session takes every core and spin-waits, which
            # starved the 3D/overlay loop (measured 15-22 fps while talking).
            # 3 threads still synthesize ~5x faster than real time.
            try:
                import onnxruntime as ort
                o = ort.SessionOptions()
                o.intra_op_num_threads = SYNTH_THREADS
                o.inter_op_num_threads = 1
                o.add_session_config_entry("session.intra_op.allow_spinning",
                                           "0")
                self._voice.session = ort.InferenceSession(
                    voice_path(voice_name), sess_options=o,
                    providers=["CPUExecutionProvider"])
            except Exception as e:
                print("  [voice] keeping default session: %s" % e)
            # synthesize faster by the pitch factor: the deepening resample
            # stretches it back, so pace is unchanged
            self._syn = {k: SynthesisConfig(**dict(
                v, length_scale=v["length_scale"] * TONE[k]["pitch"]))
                for k, v in EMOTION.items()}
            self._src_rate = self._voice.config.sample_rate

        mix = pygame.mixer.get_init()
        if not mix:
            raise RuntimeError("call pygame.mixer.init() before Speaker()")
        self._dst_rate, _, self._channels = mix

        self._prio = queue.Queue()
        self._lock = threading.Lock()
        self._slots = {}
        self._submit_i = 0
        self._play_i = 0
        self._spoken = 0
        self._stop = threading.Event()
        self._queued = False        # a clip is queued behind the current one
        self._ball_waiting = False  # a ball line wants the queued slot
        self._eq = None             # broadcast EQ, built on first use

    # -- public API ----------------------------------------------------------
    def start(self):
        # Kokoro runs ~0.7x realtime, so two workers keep it ahead of playback
        # (onnxruntime sessions are thread-safe); Piper needs only one.
        for _ in range(2 if self._kokoro else 1):
            threading.Thread(target=self._synth_worker, daemon=True).start()
        threading.Thread(target=self._play_worker, daemon=True).start()
        threading.Thread(target=self._feeder, daemon=True).start()

    def say(self, text, kind="ball", emotion=DEFAULT_EMOTION, cue=None):
        """Queue a real commentary line. These outrank filler.

        `cue` is called the moment this line becomes audible - that is how
        the scoreboard changes exactly when the voice reaches the ball."""
        if text and str(text).strip():
            if emotion not in EMOTION:
                emotion = DEFAULT_EMOTION
            self._prio.put((str(text).strip(), kind, emotion, cue))
        elif cue:
            self._prio.put((None, kind, emotion, cue))   # cue with no speech

    def say_many(self, lines, kind="ball", emotion=DEFAULT_EMOTION):
        """lines may be plain strings, (text, emotion) pairs from Director, or
        (text, emotion, cue) triples. Lines of similar energy are said as ONE
        utterance - a ball in one breath, not 3-4 stop-start sentences -
        and each line's cue fires where that line falls inside it."""
        norm = []
        for ln in lines:
            if isinstance(ln, (tuple, list)) and len(ln) == 3:
                norm.append((ln[0], ln[1], ln[2]))
            elif isinstance(ln, (tuple, list)) and len(ln) == 2:
                norm.append((ln[0], ln[1], None))
            else:
                norm.append((ln, emotion, None))
        for parts, emo in group_lines(norm):
            if emo not in EMOTION:
                emo = DEFAULT_EMOTION
            if not any(t for t, _ in parts):
                for _, c in parts:
                    if c:
                        self._prio.put((None, kind, emo, c))
                continue
            self._prio.put((parts, kind, emo, None))

    def depth(self):
        with self._lock:
            return self._submit_i - self._play_i

    def spoken_count(self):
        return self._spoken

    def stop(self):
        self._stop.set()

    def drain(self, max_wait=25):
        t0 = time.time()
        while time.time() - t0 < max_wait:
            if self.depth() == 0 and self._prio.empty():
                return
            time.sleep(0.2)

    # -- synthesis -----------------------------------------------------------
    def _to_sound(self, a, pitch=1.0, gain=1.0, tail=0.0):
        """Mono int16/float samples at the source rate -> a pygame Sound at the
        mixer's rate/channels. pitch>1 lifts pitch (and tempo) via resampling."""
        a = np.asarray(a, dtype=np.float32)
        if a.size == 0:
            return None
        if self._src_rate != self._dst_rate or pitch != 1.0:
            n = int(round(a.size * self._dst_rate / float(self._src_rate)
                          / pitch))
            a = np.interp(np.linspace(0, a.size - 1, n), np.arange(a.size), a)
        if gain != 1.0:
            a = np.tanh(a * gain * 1.15 / 32768.0) * 32768.0 / 1.15
        self._last_speech_s = a.size / float(self._dst_rate)
        if tail > 0:                    # a breath after the line
            a = np.concatenate([a, np.zeros(int(tail * self._dst_rate),
                                            np.float32)])
        a = np.clip(a, -32768, 32767).astype(np.int16)
        if self._channels == 2:
            a = np.column_stack([a, a])
        return pygame.sndarray.make_sound(np.ascontiguousarray(a))

    def _synth(self, spoken, emotion, kind="ball"):
        if self._kokoro:
            e = KOKORO_EMOTION.get(emotion) or KOKORO_EMOTION[DEFAULT_EMOTION]
            audio, _ = self._kokoro.create(
                spoken, voice=KOKORO_VOICE, lang="hi",
                speed=e["speed"] / e["pitch"])
            return self._to_sound(audio * 32767.0, e["pitch"], e["gain"])
        if emotion not in self._syn:
            emotion = DEFAULT_EMOTION
        # a person's pace drifts line to line; a fixed rate is metronomic
        base = EMOTION[emotion]
        cfg = SynthesisConfig(**dict(
            base, length_scale=base["length_scale"] * TONE[emotion]["pitch"]
            * random.uniform(0.985, 1.015)))
        chunks = [c.audio_int16_bytes
                  for c in self._voice.synthesize(spoken, syn_config=cfg)]
        raw = np.frombuffer(b"".join(chunks), dtype=np.int16)
        tone = TONE[emotion]
        if self._eq is None:
            self._eq = broadcast_eq(self._src_rate)
        return self._to_sound(broadcast(raw, self._src_rate, self._eq, tone),
                              pitch=tone["pitch"],
                              tail=breath(spoken, emotion, kind))

    def _submit(self, text, kind, emotion=DEFAULT_EMOTION, cue=None):
        parts = None
        if isinstance(text, list):          # a grouped utterance
            parts = text
            cues = [c for _, c in parts if c]
            text = " ".join(t for t, _ in parts if t) or None
            if cues:
                # if it is dropped unheard, its cues still fire in order
                cue = (lambda cs=cues: [c() for c in cs])
        with self._lock:
            i = self._submit_i
            # a cue with no text is a marker in the speech order: it fires
            # once everything before it has started playing
            self._slots[i] = {"text": text, "kind": kind, "emotion": emotion,
                              "snd": None, "spoken": None, "cue": cue,
                              "parts": parts, "marks": None,
                              "state": "new" if text else "cue"}
            self._submit_i += 1
        return i

    def _make_way(self, for_kind):
        """A new line is coming in ahead of the queue - clear what it should
        not wait behind.

        Filler always gives way. A BALL also cuts off over-break talk, break
        talk and match notes that are not yet audible: a commentator stops
        mid-thought to call the delivery. (Measured before: the first ball of
        an over waited behind ~40 s of over-break talk, so the result came
        long after the picture.) A dropped line that carried a cue - the
        OVER label, a board update - keeps it as a silent marker, so the
        screen still changes in order.

        One ready clip is kept when nothing is queued on the channel: it
        bridges the ~1 s the new line takes to synthesize (dropping it too
        was measured to leave 0.5-4 s of silence)."""
        stale = STALE_FOR_BALL if for_kind == "ball" else ("filler",)
        with self._lock:
            if for_kind == "ball":
                self._ball_waiting = True
            ready = sorted(i for i, sl in self._slots.items()
                           if sl["kind"] in stale and sl["state"] == "ready")
            keep = set(ready[:1]) if not self._queued else set()
            for i, sl in self._slots.items():
                if sl["kind"] not in stale or i in keep or                         sl["state"] not in ("new", "synth", "ready"):
                    continue
                if sl.get("cue") and sl["kind"] != "filler":
                    sl["state"], sl["text"], sl["snd"] = "cue", None, None
                else:
                    sl["state"] = "dropped"

    def _feeder(self):
        while not self._stop.is_set():
            try:
                if not self._prio.empty():
                    text, kind, emotion, cue = self._prio.get_nowait()
                    self._make_way(kind)
                    self._submit(text, kind, emotion, cue)
                    continue
                if self.depth() < BUFFER_TARGET and self.filler_cb:
                    line = self.filler_cb()
                    if line:
                        # filler is between-ball talk - keep it relaxed
                        if isinstance(line, (tuple, list)) and len(line) == 3:
                            self._submit(line[0], "filler", line[1], line[2])
                        elif isinstance(line, (tuple, list)) and len(line) == 2:
                            self._submit(line[0], "filler", line[1])
                        else:
                            self._submit(line, "filler", "calm")
                        continue
            except queue.Empty:
                pass
            except Exception as e:
                print("  [feeder] %s" % e)
            time.sleep(0.12)

    def _claim(self):
        """Lowest-index slot still needing synthesis (keeps speech in order)."""
        with self._lock:
            for i in sorted(self._slots):
                if self._slots[i]["state"] == "new":
                    self._slots[i]["state"] = "synth"
                    return (i, self._slots[i]["text"],
                            self._slots[i]["emotion"])
        return None, None, None

    def _synth_worker(self):
        while not self._stop.is_set():
            i, text, emotion = self._claim()
            if i is None:
                time.sleep(0.08)
                continue
            with self._lock:
                kind = (self._slots.get(i) or {}).get("kind", "ball")
            try:
                # Transliterate first: the Hindi phonemizer mangles Latin
                # letters, which is what made player names unintelligible.
                with self._lock:
                    parts = (self._slots.get(i) or {}).get("parts")
                marks = None
                if parts:
                    spoken_parts = [(clean_for_voice(translit.say_text(t)), c)
                                    for t, c in parts]
                    spoken, fracs = join_parts(spoken_parts)
                    marks = [(f, c) for f, (_, c) in zip(fracs, spoken_parts)
                             if c]
                else:
                    spoken = clean_for_voice(translit.say_text(text))
                # dashes only add dead air between words
                spoken = spoken.replace("—", ", ")
                snd = self._synth(spoken, emotion, kind)
                with self._lock:
                    sl = self._slots.get(i)
                    # it may have been dropped for a ball while synthesizing
                    if sl and sl["state"] == "synth":
                        sl["spoken"] = spoken
                        sl["snd"] = snd
                        sl["marks"] = marks
                        sl["speech_s"] = getattr(self, "_last_speech_s", 0)
                        sl["state"] = "ready" if snd else "dropped"
            except Exception as e:
                if not self._stop.is_set():     # not just shutting down
                    print("  [synth] %s" % e)
                with self._lock:
                    if i in self._slots:
                        self._slots[i]["state"] = "dropped"

    # -- playback ------------------------------------------------------------
    def _play_worker(self):
        """Gapless playback. A clip is queued behind the current one so it
        starts the instant that ends - but it is only *announced* (subtitle,
        scoreboard cue) once it is actually audible. Announcing on queue, as
        before, ran the subtitle up to a whole clip ahead of the voice."""
        ch = pygame.mixer.Channel(0)
        pending = None              # queued behind the current clip, not heard yet
        while not self._stop.is_set():
            if pending is not None and (ch.get_queue() is None
                                        or not ch.get_busy()):
                self._announce(pending)     # the queued clip just started
                pending = None
                self._queued = False
                # pygame hands over to the queued clip inside the audio
                # thread: for an instant the channel reads "nothing queued"
                # AND "not busy". Deciding now would play() over the clip
                # that just started and cut it off - so let it settle.
                time.sleep(0.03)
                continue

            with self._lock:
                i = self._play_i
                slot = self._slots.get(i)
                state = slot["state"] if slot else None

            if slot is None:
                time.sleep(0.03)
                continue
            if state == "dropped":
                self._advance(i)
                continue
            stale_pending = (pending is not None and self._ball_waiting
                             and pending["kind"] in STALE_FOR_BALL)
            if state == "cue":
                # fires once everything before it is audible - or at once
                # when a ball is about to take the queued clip's place
                if pending is None or stale_pending:
                    self._fire(slot)
                    self._advance(i)
                else:
                    time.sleep(0.02)
                continue
            if state != "ready":
                time.sleep(0.03)
                continue

            snd = slot["snd"]
            # A ball takes the place of stale talk already queued on the
            # channel (pygame replaces a queued sound). It still waits for
            # the sentence being spoken to finish - never cut mid-word.
            if slot["kind"] == "ball" and stale_pending and                     ch.get_queue() is not None:
                ch.queue(snd)
                self._fire(pending)         # its cue still happens
                pending = slot
                self._queued = True
                self._ball_waiting = False
                self._advance(i)
                continue
            if slot["kind"] == "ball":
                self._ball_waiting = False
            if not ch.get_busy() and ch.get_queue() is None:
                time.sleep(0.015)           # idle twice, or it is a handover
                if ch.get_busy() or ch.get_queue() is not None:
                    continue
                ch.play(snd)
                self._announce(slot)
                self._advance(i)
            elif pending is None and ch.get_queue() is None:
                ch.queue(snd)          # starts the instant current ends
                pending = slot
                self._queued = True
                self._advance(i)
            else:
                time.sleep(0.02)

    def _advance(self, i):
        with self._lock:
            self._slots.pop(i, None)
            self._play_i = i + 1

    def _fire(self, slot):
        cue = slot.get("cue")
        if cue:
            try:
                cue()
            except Exception as e:
                print("  [cue] %s" % e)

    def _announce(self, slot):
        self._spoken += 1
        marks = slot.get("marks")
        if marks:
            dur = slot.get("speech_s") or 0
            for frac, cue in marks:
                delay = frac * dur
                if delay <= 0.05:
                    self._fire({"cue": cue})
                else:
                    t = threading.Timer(delay, self._fire, args=({"cue": cue},))
                    t.daemon = True
                    t.start()
        else:
            self._fire(slot)
        try:
            self.on_say(slot.get("spoken") or slot["text"],
                        slot["kind"], slot["emotion"])
        except TypeError:
            self.on_say(slot["text"], slot["kind"])   # older 2-arg callback
        except Exception:
            pass
