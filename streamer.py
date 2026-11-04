# -*- coding: utf-8 -*-
"""
streamer.py  -  run the engine on a server with no screen and no sound card,
and push what it shows and says to YouTube.

    Xvfb :9N          a virtual 1280x720 screen the 3D window opens on
    PulseAudio sink   a virtual speaker the commentary plays into
    main.py           the engine, unchanged, on that screen and speaker
    ffmpeg            grabs the screen + the speaker's monitor -> RTMP

Each slot gets its own display and sink, so two matches can run side by side
on one big machine. Linux only (Xvfb and PulseAudio).

    python streamer.py 177681 out.flv        test: 2 minutes into a file
"""
import os
import sys
import time
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
W, H = 1280, 720
FPS = int(os.environ.get("CRICK_FPS", "25"))
VBITRATE = os.environ.get("STREAM_VBITRATE", "3000k")


def _run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, **kw)


def ensure_pulse(sink):
    """A running PulseAudio with a null sink called `sink`."""
    if not os.environ.get("XDG_RUNTIME_DIR"):
        # a service / CI user has no login session to provide one
        d = "/tmp/xdg-%d" % os.getuid()
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.environ["XDG_RUNTIME_DIR"] = d
    if _run(["pulseaudio", "--check"]).returncode != 0:
        _run(["pulseaudio", "-D", "--exit-idle-time=-1", "--disallow-exit",
              "--log-target=stderr"])
        for _ in range(20):
            if _run(["pulseaudio", "--check"]).returncode == 0:
                break
            time.sleep(0.5)
    sinks = _run(["pactl", "list", "short", "sinks"]).stdout
    if sink not in sinks:
        _run(["pactl", "load-module", "module-null-sink",
              "sink_name=%s" % sink,
              "sink_properties=device.description=%s" % sink])


class Streamer:
    def __init__(self, slot, mid, target):
        self.slot, self.mid, self.target = slot, str(mid), target
        self.display = ":%d" % (90 + slot)
        self.sink = "crick%d" % slot
        os.makedirs(LOG_DIR, exist_ok=True)
        self.engine_log = os.path.join(LOG_DIR, "engine_slot%d.log" % slot)
        self.ffmpeg_log = os.path.join(LOG_DIR, "ffmpeg_slot%d.log" % slot)
        self.xvfb = self.engine = self.ffmpeg = None
        self.ffmpeg_restarts = 0

    # -- start ----------------------------------------------------------
    def start(self, wait_live=150, gate=None):
        """Screen, speaker, engine, then ffmpeg. gate: called with the engine
        warm, just before going on air - it may wait (a successor job waiting
        for the old one to let go); False aborts, and start returns False."""
        if not shutil.which("Xvfb") or not shutil.which("ffmpeg"):
            raise RuntimeError("Xvfb / ffmpeg not installed (run deploy/setup_linux.sh)")
        lock = "/tmp/.X%d-lock" % (90 + self.slot)
        if os.path.exists(lock):
            os.remove(lock)
        self.xvfb = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", "%dx%dx24" % (W, H),
             "-nolisten", "tcp", "-ac", "+extension", "GLX", "+render"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        ensure_pulse(self.sink)

        env = dict(os.environ, DISPLAY=self.display, PULSE_SINK=self.sink,
                   SDL_AUDIODRIVER="pulseaudio", CRICK_STREAM="1",
                   CRICK_FPS=str(FPS), PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        self._elog = open(self.engine_log, "a", encoding="utf-8")
        self._elog.write("\n==== %s match %s ====\n" % (time.ctime(), self.mid))
        self._elog.flush()
        self.engine = subprocess.Popen(
            [sys.executable, "-u", os.path.join(HERE, "main.py"),
             "--match", self.mid], cwd=HERE, env=env,
            stdout=self._elog, stderr=subprocess.STDOUT)

        # the engine prints "LIVE: <title>" once the window is up and the
        # first poll is in - only then is there something worth sending
        t0 = time.time()
        while time.time() - t0 < wait_live:
            if self.engine.poll() is not None:
                raise RuntimeError("engine exited early - see %s" % self.engine_log)
            if "  LIVE: " in self._tail(self.engine_log, 4000):
                break
            time.sleep(2)
        time.sleep(4)               # first frames drawn, first line queued
        if gate is not None and not gate():
            return False
        self._start_ffmpeg()
        return True

    def _start_ffmpeg(self):
        g = str(FPS * 2)            # a keyframe every 2 s, as YouTube asks
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
               "-thread_queue_size", "512", "-f", "x11grab", "-draw_mouse", "0",
               "-framerate", str(FPS), "-video_size", "%dx%d" % (W, H),
               "-i", self.display + ".0",
               "-thread_queue_size", "1024", "-f", "pulse", "-ac", "2",
               "-ar", "44100", "-i", self.sink + ".monitor",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-b:v", VBITRATE, "-maxrate", VBITRATE,
               "-bufsize", str(int(VBITRATE.rstrip("k")) * 2) + "k",
               "-g", g, "-keyint_min", g, "-sc_threshold", "0",
               "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
               "-f", "flv", self.target]
        self._flog = open(self.ffmpeg_log, "a", encoding="utf-8")
        self._flog.write("\n==== %s ====\n" % time.ctime())
        self._flog.flush()
        self.ffmpeg = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                       stdout=self._flog,
                                       stderr=subprocess.STDOUT)

    # -- health -----------------------------------------------------------
    def engine_alive(self):
        return self.engine is not None and self.engine.poll() is None

    def ffmpeg_alive(self):
        return self.ffmpeg is not None and self.ffmpeg.poll() is None

    def restart_ffmpeg(self):
        """A dropped RTMP connection: reconnect into the same broadcast."""
        self._kill(self.ffmpeg, 5)
        self.ffmpeg_restarts += 1
        time.sleep(min(30, 3 * self.ffmpeg_restarts))
        self._start_ffmpeg()

    @staticmethod
    def _tail(path, n=3000):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - n))
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def engine_tail(self, n=1500):
        return self._tail(self.engine_log, n)

    # -- stop -------------------------------------------------------------
    @staticmethod
    def _kill(p, wait):
        if p is None or p.poll() is not None:
            return
        p.terminate()
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass

    def stop(self):
        self._kill(self.ffmpeg, 10)     # SIGTERM: ffmpeg closes the FLV cleanly
        self._kill(self.engine, 15)
        self._kill(self.xvfb, 5)
        for f in (getattr(self, "_elog", None), getattr(self, "_flog", None)):
            try:
                f and f.close()
            except Exception:
                pass


if __name__ == "__main__":
    mid = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "logs", "test.flv")
    secs = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    s = Streamer(1, mid, target)
    try:
        s.start()
        print("  streaming match %s to %s for %ds" % (mid, target, secs))
        t0 = time.time()
        while time.time() - t0 < secs:
            if not s.engine_alive():
                print("  engine died:\n" + s.engine_tail())
                break
            if not s.ffmpeg_alive():
                print("  ffmpeg died:\n" + s._tail(s.ffmpeg_log))
                break
            time.sleep(5)
    finally:
        s.stop()
    print(s.engine_tail(2500))
