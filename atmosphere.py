# -*- coding: utf-8 -*-
"""
atmosphere.py  -  stadium crowd that actually sounds like people

The previous version layered smoothed white noise and brown noise, which reads
as hiss and rumble, not a crowd. A crowd is not noise: it is hundreds of
*voices*, so this synthesizes voices.

Per layer:

  babble   ~70 independent voices. Each is a sawtooth at its own pitch
           (80-260 Hz, the human range), amplitude-modulated at 2-6 Hz so it
           has a syllable rhythm. Voices are grouped by vowel, and each group
           is pushed through a formant filter - Gaussian peaks at the real
           F1/F2/F3 of /a/, /e/, /o/, /u/. Formants are what make a sound read
           as a voice rather than as noise.

  claps    short bandpassed transients around 1-3 kHz with a fast decay, which
           is what a handclap actually is. Scattered at random.

  whistles be filtered sine sweeps, for the big moments.

  stadium  everything then goes through a small multi-tap reverb and a
           distance roll-off, because a crowd is heard across a bowl, not up
           close.

Generation takes a few seconds, so the result is cached to assets/*.wav and
reused on every later run. Drop your own ambient.wav / cheer.wav / roar.wav /
clap.wav in assets/ and those are used instead.

Layered UNDER the commentary on dedicated pygame channels (5 and 6; speech
owns channel 0).
"""
import os
import wave
import random

try:
    import numpy as np
    import pygame
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False

SR = 44100
ASSET_DIR = "assets"

_sounds = {}
_amb_ch = None
_react_ch = None
_enabled = False

# Real vowel formants (F1, F2, F3) in Hz. A crowd is mostly open vowels -
# people shout "aaah" and "ooh", not "eee".
VOWELS = {
    "a": (730, 1090, 2440),
    "e": (530, 1840, 2480),
    "o": (570, 840, 2410),
    "u": (300, 870, 2240),
}
VOWEL_MIX = [("a", 0.40), ("o", 0.30), ("e", 0.18), ("u", 0.12)]


# ── helpers ─────────────────────────────────────────────────────────────────
def _norm(x):
    m = float(np.max(np.abs(x))) or 1.0
    return x / m


def _formant_filter(x, vowel, bw=110.0, tilt=5200.0):
    """Shape a signal with a vowel's formant peaks, via FFT.

    Gaussian peaks at F1/F2/F3 plus a gentle high roll-off. This is the step
    that turns a buzz into something the ear hears as a voice.
    """
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    f1, f2, f3 = VOWELS[vowel]
    H = (1.00 * np.exp(-0.5 * ((f - f1) / bw) ** 2) +
         0.55 * np.exp(-0.5 * ((f - f2) / (bw * 1.4)) ** 2) +
         0.22 * np.exp(-0.5 * ((f - f3) / (bw * 2.0)) ** 2))
    H += 0.03 * np.exp(-f / 3000.0)              # a little broadband body
    H /= 1.0 + (f / tilt) ** 2                   # distance roll-off
    return np.fft.irfft(X * H, n=n)


def _syllable_env(n, rate_lo=2.0, rate_hi=6.0):
    """Slow amplitude wobble, so a voice has a syllable rhythm."""
    t = np.arange(n) / SR
    env = np.zeros(n)
    for _ in range(3):                           # a few rates stacked
        r = random.uniform(rate_lo, rate_hi)
        env += np.sin(2 * np.pi * r * t + random.uniform(0, 6.283))
    env = 0.55 + 0.45 * _norm(env)
    return env


def _voice_group(n, vowel, n_voices, f0_lo, f0_hi, detune=1.0):
    """Many sawtooth voices at different pitches, then formant-filtered."""
    t = np.arange(n) / SR
    buf = np.zeros(n)
    for _ in range(n_voices):
        f0 = random.uniform(f0_lo, f0_hi) * detune
        # sawtooth: harmonically rich, so the formant filter has something
        # to shape (a pure sine would come out as a whistle)
        saw = 2.0 * ((t * f0 + random.random()) % 1.0) - 1.0
        buf += saw * _syllable_env(n) * random.uniform(0.5, 1.0)
    return _formant_filter(_norm(buf), vowel)


def _babble(seconds, n_voices=70, intensity=1.0):
    """The crowd bed: voices across all vowel groups."""
    n = int(seconds * SR)
    out = np.zeros(n)
    for vowel, share in VOWEL_MIX:
        k = max(2, int(n_voices * share))
        out += _voice_group(n, vowel, k, 85.0, 255.0) * share
    # a breathy layer so it is not purely tonal
    breath = np.random.standard_normal(n)
    breath = _formant_filter(breath, "a", bw=400.0, tilt=3000.0)
    return _norm(out + 0.18 * _norm(breath)) * intensity


def _clap_sound():
    """One handclap: a bandpassed transient with a very fast decay."""
    n = int(0.045 * SR)
    burst = np.random.standard_normal(n)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    H = np.exp(-0.5 * ((f - 1900.0) / 1100.0) ** 2) + 0.25
    H /= 1.0 + (f / 6000.0) ** 2
    burst = np.fft.irfft(np.fft.rfft(burst) * H, n=n)
    return _norm(burst * np.exp(-np.linspace(0, 11, n)))


def _applause(seconds, density):
    """Scattered claps. density = claps per second across the whole crowd."""
    n = int(seconds * SR)
    buf = np.zeros(n)
    clap = _clap_sound()
    cl = len(clap)
    for _ in range(int(density * seconds)):
        pos = random.randint(0, max(0, n - cl - 1))
        buf[pos:pos + cl] += clap * random.uniform(0.2, 1.0)
    return _norm(buf)


def _whistle(seconds=0.8):
    """A fan's whistle - a swept, slightly noisy tone."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f = np.linspace(random.uniform(1800, 2300), random.uniform(2600, 3200), n)
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR)
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 0.6
    return _norm(sig * env)


def _reverb(x, taps=((0.031, 0.5), (0.057, 0.36), (0.089, 0.26),
                     (0.131, 0.18), (0.190, 0.11))):
    """Cheap multi-tap reverb - a stadium is a big reflective bowl."""
    out = x.copy()
    for delay, gain in taps:
        d = int(delay * SR)
        if d < len(x):
            out[d:] += x[:-d] * gain
    return _norm(out)


def _loopable(sig, fade=0.9):
    """Crossfade the tail into the head so the bed loops without a seam."""
    n = len(sig)
    xf = min(int(fade * SR), n // 3)
    head = sig[:xf].copy()
    sig[:xf] *= np.linspace(0, 1, xf)
    sig[-xf:] = sig[-xf:] * np.linspace(1, 0, xf) + head * np.linspace(0, 1, xf)
    return _norm(sig)


# ── the four layers ─────────────────────────────────────────────────────────
def _gen_ambient(seconds=20):
    """Continuous murmur: babble plus sparse polite clapping."""
    bed = _babble(seconds, n_voices=70, intensity=0.9)
    t = np.arange(len(bed)) / SR
    swell = 0.72 + 0.28 * np.sin(2 * np.pi * 0.045 * t + random.random())
    sig = bed * swell + 0.10 * _applause(seconds, density=26)
    return _loopable(_reverb(_norm(sig)))


def _gen_cheer(seconds=3.2):
    """Four / good shot: applause with a lift in the voices."""
    n = int(seconds * SR)
    bab = _babble(seconds, n_voices=55, intensity=1.0)
    app = _applause(seconds, density=420)
    rise = np.linspace(0.25, 1.0, n) ** 1.3
    sig = _norm(bab * rise * 0.8 + app * 0.9)
    tail = int(0.5 * SR)
    sig[-tail:] *= np.linspace(1, 0.15, tail)
    return _reverb(sig)


def _gen_roar(seconds=4.5):
    """Six / wicket: a full eruption, with whistles over the top."""
    n = int(seconds * SR)
    # pitched up a little - a roaring crowd sits higher than a murmuring one
    bab = np.zeros(n)
    for vowel, share in (("a", 0.6), ("o", 0.4)):
        bab += _voice_group(n, vowel, int(70 * share), 120.0, 330.0) * share
    app = _applause(seconds, density=700)

    env = np.ones(n)
    a, d = int(0.06 * n), int(0.42 * n)
    env[:a] = np.linspace(0, 1, a) ** 0.6
    env[-d:] = np.linspace(1, 0.12, d)

    sig = _norm(bab) * 1.0 + app * 0.75
    wh = np.zeros(n)
    for _ in range(5):
        w = _whistle(random.uniform(0.5, 1.0))
        pos = random.randint(int(0.1 * n), max(int(0.1 * n) + 1, n - len(w) - 1))
        wh[pos:pos + len(w)] += w * random.uniform(0.15, 0.3)
    return _reverb(_norm((sig + wh) * env))


def _sustained(n, vowel, n_voices, f0_lo, f0_hi, glide):
    """Many voices holding one vowel together, their pitch following
    `glide` (a multiplier per sample) - a crowd reacting as one."""
    t = np.arange(n) / SR
    buf = np.zeros(n)
    phase_rate = np.cumsum(glide) / SR
    for _ in range(n_voices):
        f0 = random.uniform(f0_lo, f0_hi)
        wob = 1.0 + 0.012 * np.sin(2 * np.pi * random.uniform(4, 7) * t +
                                    random.random() * 6.28)
        saw = 2.0 * ((phase_rate * f0 * wob + random.random()) % 1.0) - 1.0
        buf += saw * random.uniform(0.5, 1.0)
    return _formant_filter(_norm(buf), vowel)


def _gen_ooh(seconds=2.0):
    """'Ooooh!' - a play-and-miss, an edge, a ball in the air: the crowd
    draws breath together, pitch rising then falling away."""
    n = int(seconds * SR)
    u = np.linspace(0, 1, n)
    glide = 1.0 + 0.22 * np.sin(np.pi * np.clip(u * 1.3, 0, 1))
    sig = _sustained(n, "o", 45, 120.0, 300.0, glide) * 0.8 +         _sustained(n, "u", 25, 110.0, 260.0, glide) * 0.4
    env = np.minimum(1.0, u / 0.12) * np.clip((1 - u) / 0.55, 0, 1) ** 1.2
    return _reverb(_norm(sig * env))


def _gen_groan(seconds=1.8):
    """'Aaah...' - a dropped catch: the pitch falls as the crowd deflates."""
    n = int(seconds * SR)
    u = np.linspace(0, 1, n)
    glide = 1.12 - 0.3 * u
    sig = _sustained(n, "a", 50, 110.0, 280.0, glide)
    env = np.minimum(1.0, u / 0.08) * np.clip(1 - u, 0, 1) ** 0.9
    return _reverb(_norm(sig * env))


def _gen_clap(seconds=2.2):
    """Polite applause on its own."""
    return _reverb(_norm(_applause(seconds, density=300) * 0.9 +
                         _babble(seconds, n_voices=30, intensity=0.35)))


# ── cache to disk, so this is generated once ────────────────────────────────
def _save_wav(path, mono):
    a = np.int16(_norm(mono) * 32767 * 0.92)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(a.tobytes())


def _cached(name, gen, asset_dir):
    """Load assets/<name>.wav if present, else generate it and save it."""
    path = os.path.join(asset_dir, name + ".wav")
    if os.path.exists(path):
        return pygame.mixer.Sound(path)
    sig = gen()
    try:
        os.makedirs(asset_dir, exist_ok=True)
        _save_wav(path, sig)
    except Exception as e:
        print("  [atmosphere] could not cache %s: %s" % (name, e))
    return _to_sound(sig)


def _to_sound(mono, gain=0.92):
    """Mono float -> a pygame Sound matching the mixer's format."""
    a = np.int16(_norm(mono) * 32767 * gain)
    init = pygame.mixer.get_init()
    channels = init[2] if init else 1
    if channels == 2:
        # tiny inter-channel delay widens the crowd instead of leaving it
        # pinned dead centre
        d = int(0.013 * SR)
        right = np.concatenate([np.zeros(d, dtype=np.int16), a[:-d]])
        a = np.column_stack([a, right])
    return pygame.sndarray.make_sound(np.ascontiguousarray(a))


# ── public API ──────────────────────────────────────────────────────────────
def init(asset_dir=ASSET_DIR):
    """Call AFTER pygame.mixer.init()."""
    global _enabled, _amb_ch, _react_ch
    if not NUMPY_OK:
        print("  [atmosphere] numpy missing - no crowd. (pip install numpy)")
        return False
    try:
        pygame.mixer.set_num_channels(16)
        _amb_ch = pygame.mixer.Channel(5)
        _react_ch = pygame.mixer.Channel(6)

        fresh = not os.path.exists(os.path.join(asset_dir, "ooh.wav"))
        if fresh:
            print("  Building stadium crowd (once, then cached to %s/)"
                  % asset_dir, end="", flush=True)
        for name, gen in (("ambient", _gen_ambient), ("cheer", _gen_cheer),
                          ("roar", _gen_roar), ("clap", _gen_clap),
                          ("ooh", _gen_ooh), ("groan", _gen_groan)):
            _sounds[name] = _cached(name, gen, asset_dir)
            if fresh:
                print(".", end="", flush=True)
        if fresh:
            print(" done")
        _enabled = True
        return True
    except Exception as e:
        print("  [atmosphere] disabled: %s" % e)
        return False


def start_ambient(volume=0.16):
    if _enabled and _amb_ch:
        _amb_ch.set_volume(volume)
        _amb_ch.play(_sounds["ambient"], loops=-1)


def set_ambient_volume(v):
    if _enabled and _amb_ch:
        _amb_ch.set_volume(v)


def react(event, volume=0.55):
    """Crowd reaction for a match event. Overlaps the commentary."""
    if not _enabled or not _react_ch:
        return
    snd = {"six": "roar", "wicket": "roar", "milestone": "roar",
           "four": "cheer", "three": "cheer", "clap": "clap",
           "two": "clap", "near": "ooh", "drop": "groan"}.get(event)
    if snd:
        _react_ch.set_volume(volume)
        _react_ch.play(_sounds[snd])


def stop():
    if _amb_ch:
        _amb_ch.stop()
    if _react_ch:
        _react_ch.stop()
