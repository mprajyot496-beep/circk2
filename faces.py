# -*- coding: utf-8 -*-
"""
faces.py  -  real player photos (background removed) and country flags.

Both come from Cricbuzz, the same source as the score:

    /profiles/{id}/{slug}          the player's page names his headshot image
                                   ".../i1/c{faceId}/{slug}.jpg" and his
                                   country flag ".../30x20/i1/c{flagId}/x.jpg"
    static.cricbuzz.com/.../300x300/i1/c{faceId}/i.jpg     the headshot
    static.cricbuzz.com/.../240x160/i1/c{flagId}/i.jpg     the flag

Headshots are shot on a flat grey studio backdrop, so a salient-object model
cuts them out cleanly. That model is u2net run straight through onnxruntime -
the same file rembg uses (~/.u2net/u2net.onnx), without importing rembg, whose
numba dependency is version-fragile. No model on disk -> photos are kept with
their backdrop and masked to a soft rounded shape instead; nothing downloads.

Lesser-known players have no photo, only Cricbuzz's grey silhouette. That is
detected (a real photo has colour, the silhouette has none) and the card
draws a clean drawn avatar instead of a grey blob.

Everything runs on one background thread and is cached in assets/players/ and
assets/flags/, so each player costs one fetch ever, and the render loop never
waits on the network. The render thread only ever reads finished PNGs.
"""
import io
import os
import re
import json
import queue
import threading

import numpy as np

try:
    from PIL import Image
except ImportError:                     # photos are optional
    Image = None

import cricbuzz
from curl_cffi import requests as creq

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYER_DIR = os.path.join(HERE, "assets", "players")
FLAG_DIR = os.path.join(HERE, "assets", "flags")
META = os.path.join(PLAYER_DIR, "meta.json")
STATIC = "https://static.cricbuzz.com/a/img/v1"
U2NET = os.path.join(os.path.expanduser("~"), ".u2net", "u2net.onnx")

_lock = threading.Lock()
_meta = {}          # pid -> {"face": bool, "flag": flag id or None}
_team_flag = {}     # team id -> flag id, learned from that team's players
_asked = set()
_jobs = queue.Queue()
_worker = None
_session = None


def _load_meta():
    global _meta
    try:
        with open(META, encoding="utf-8") as f:
            _meta = json.load(f)
    except (OSError, ValueError):
        _meta = {}


def _save_meta():
    try:
        os.makedirs(PLAYER_DIR, exist_ok=True)
        with open(META, "w", encoding="utf-8") as f:
            json.dump(_meta, f)
    except OSError:
        pass


def _get(url):
    try:
        r = creq.get(url, impersonate="chrome", timeout=25)
        return r.content if r.status_code == 200 and r.content else None
    except Exception:
        return None


# ── Background removal ─────────────────────────────────────────────────────
def _u2net():
    global _session
    if _session is None:
        if not os.path.exists(U2NET):
            _session = False
        else:
            try:
                import onnxruntime as ort
                o = ort.SessionOptions()
                o.intra_op_num_threads = 2      # leave the CPU to the window
                o.add_session_config_entry("session.intra_op.allow_spinning",
                                           "0")
                _session = ort.InferenceSession(
                    U2NET, sess_options=o, providers=["CPUExecutionProvider"])
            except Exception as e:
                print("  [faces] cut-out model unavailable: %s" % e)
                _session = False
    return _session or None


def _cutout(im):
    """RGB headshot -> RGBA with the backdrop removed (or a soft vignette
    mask when the model is not available)."""
    im = im.convert("RGB")
    s = _u2net()
    if s is not None:
        a = np.asarray(im.resize((320, 320), Image.LANCZOS), np.float32) / 255.0
        a = (a - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        x = a.transpose(2, 0, 1)[None].astype(np.float32)
        m = s.run(None, {s.get_inputs()[0].name: x})[0][0, 0]
        m = (m - m.min()) / (m.max() - m.min() + 1e-8)
        # firm up the edge a little: u2net leaves a faint halo on grey
        m = np.clip((m - 0.08) / 0.84, 0.0, 1.0)
        mask = Image.fromarray((m * 255).astype(np.uint8)).resize(
            im.size, Image.LANCZOS)
    else:
        w, h = im.size
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.hypot((xx - w / 2) / (w / 2), (yy - h * 0.45) / (h * 0.62))
        mask = Image.fromarray((np.clip(1.25 - d, 0, 1) * 255).astype(np.uint8))
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def _is_silhouette(im):
    """Cricbuzz's no-photo placeholder is a grey figure on grey - no colour.
    Any real face has skin tones, so mean saturation separates them."""
    a = np.asarray(im.convert("RGB"), np.float32)
    sat = a.max(axis=2) - a.min(axis=2)
    return float(sat.mean()) < 6.0


# ── Fetching ────────────────────────────────────────────────────────────────
def _fetch(pid, url, team_id):
    slug = url.rstrip("/").split("/")[-1]
    html = cricbuzz._page(url)
    face_id = None
    m = re.search(r"/i1/c(\d+)/%s\.jpg" % re.escape(slug), html)
    if m:
        face_id = m.group(1)
    m = re.search(r"/30x20/i1/c(\d+)/[a-z0-9\-]+\.jpg", html)
    flag_id = m.group(1) if m else None
    # "Batting Style | Left Handed Bat", "Bowling Style | Right-arm offbreak"
    # - the 3D view mirrors left-handers and runs spinners in off a short
    # run-up with the keeper up at the stumps
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", html))
    m = re.search(r"Batting Style[ |]+([A-Za-z \-]+?)\s*\|", text)
    bat_style = (m.group(1).strip() if m else "")
    m = re.search(r"Bowling Style[ |]+([A-Za-z \-]+?)\s*\|", text)
    bowl_style = (m.group(1).strip() if m else "")

    has_face = False
    if face_id and Image is not None:
        raw = _get("%s/300x300/i1/c%s/i.jpg" % (STATIC, face_id))
        if raw:
            try:
                im = Image.open(io.BytesIO(raw))
                if not _is_silhouette(im):
                    os.makedirs(PLAYER_DIR, exist_ok=True)
                    _cutout(im).save(os.path.join(PLAYER_DIR, "%s.png" % pid))
                    has_face = True
            except Exception as e:
                print("  [faces] %s: %s" % (pid, e))

    if flag_id and Image is not None:
        path = os.path.join(FLAG_DIR, "%s.png" % flag_id)
        if not os.path.exists(path):
            raw = _get("%s/240x160/i1/c%s/i.jpg" % (STATIC, flag_id))
            if raw:
                try:
                    os.makedirs(FLAG_DIR, exist_ok=True)
                    Image.open(io.BytesIO(raw)).convert("RGB").save(path)
                except Exception:
                    pass

    with _lock:
        _meta[str(pid)] = {"face": has_face, "flag": flag_id,
                           "bat": bat_style, "bowl": bowl_style}
        if team_id is not None and flag_id:
            _team_flag[team_id] = flag_id
        _save_meta()


def _work():
    while True:
        pid, url, team_id = _jobs.get()
        try:
            _fetch(pid, url, team_id)
        except Exception as e:
            print("  [faces] %s: %s" % (pid, e))


def want(player, team_id=None):
    """Ask for a player's photo and flag. Cheap to call every frame: each
    player is fetched once, ever, and only on the background thread."""
    global _worker
    pid, url = player.get("id"), player.get("playerUrl")
    if not pid or not url:
        return
    key = str(pid)
    with _lock:
        if not _meta and not _asked:
            _load_meta()
        known = _meta.get(key)
        if known and "bat" in known:        # older entries lack styles
            if team_id is not None and known.get("flag"):
                _team_flag.setdefault(team_id, known["flag"])
            return
        if key in _asked:
            return
        _asked.add(key)
        if _worker is None:
            _worker = threading.Thread(target=_work, daemon=True)
            _worker.start()
    _jobs.put((key, url, team_id))


def photo_path(pid):
    """PNG of the cut-out headshot, or None (not fetched yet / no photo)."""
    with _lock:
        info = _meta.get(str(pid))
    if info and info.get("face"):
        p = os.path.join(PLAYER_DIR, "%s.png" % pid)
        return p if os.path.exists(p) else None
    return None


def style(pid):
    """(left_handed_bat, bowling_style_lowercase) for a player, as far as
    known. Unknown -> right-handed, pace."""
    with _lock:
        info = _meta.get(str(pid)) or {}
    return ("left" in (info.get("bat") or "").lower(),
            (info.get("bowl") or "").lower())


_skin = {}


def skin(pid):
    """The player's skin tone, sampled from the middle of his face in the
    real photo, as 0-1 RGB - or None if there is no photo yet."""
    if not pid:
        return None
    key = str(pid)
    if key in _skin:
        return _skin[key]
    p = photo_path(pid)
    if not p or Image is None:
        return None
    try:
        a = np.asarray(Image.open(p).convert("RGBA"), np.float32)
        h, w = a.shape[:2]
        face = a[int(h * 0.38):int(h * 0.55), int(w * 0.40):int(w * 0.60)]
        face = face[face[..., 3] > 200][:, :3]
        if len(face) < 20:
            return None
        rgb = tuple(float(v) / 255.0 for v in np.median(face, axis=0))
        _skin[key] = rgb
        return rgb
    except Exception:
        return None


_hair = {}


def hair(pid):
    """Hair colour from the top of the head in the real photo (0-1 RGB), or
    None. Headshots are framed alike, so the crown sits in the same band."""
    if not pid:
        return None
    key = str(pid)
    if key in _hair:
        return _hair[key]
    p = photo_path(pid)
    if not p or Image is None:
        return None
    try:
        a = np.asarray(Image.open(p).convert("RGBA"), np.float32)
        h, w = a.shape[:2]
        top = a[int(h * 0.08):int(h * 0.20), int(w * 0.38):int(w * 0.62)]
        top = top[top[..., 3] > 200][:, :3]
        if len(top) < 20:
            return None
        rgb = tuple(float(v) / 255.0 for v in np.median(top, axis=0))
        _hair[key] = rgb
        return rgb
    except Exception:
        return None


def is_spinner(bowl_style):
    s = (bowl_style or "").lower()
    return any(k in s for k in ("orthodox", "offbreak", "off break", "legbreak",
                                "leg break", "googly", "chinaman", "wrist",
                                "slow left", "spin"))


def flag_path(team_id):
    """PNG of a team's flag, learned from any of its players, or None."""
    with _lock:
        fid = _team_flag.get(team_id)
    if not fid:
        return None
    p = os.path.join(FLAG_DIR, "%s.png" % fid)
    return p if os.path.exists(p) else None
