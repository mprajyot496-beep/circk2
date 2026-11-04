# -*- coding: utf-8 -*-
"""
thumbnail.py  -  the 1280x720 stream thumbnail.

Laid out the way the big Hindi live-score channels do theirs (CricTalks,
Iqbal Sports, Cric Bihar, Sports Khabar - measured off their top streams):

    +---------------------------------------------------------------+
    |  (flag)            [ 1ST INNINGS ]             (flag)          |
    |   CAPTAIN 1          L I V E                  CAPTAIN 2        |
    |   cut-out,          [ 5TH T20I ]              cut-out,         |
    |   white edge       HINDI COMMENTARY           white edge       |
    | team colour  \      series name             /  team colour     |
    | [ INDIA       >          (VS)           <      NEW ZEALAND ]   |
    +---------------------------------------------------------------+

Two captains facing each other on their team colours, a huge red LIVE, the
match in a white box, team names in arrow banners. Captains come from the
Cricbuzz scorecard (`isCaptain`); a team with no captain known yet (match
not started) gets its badge instead.

    python thumbnail.py 161234            render one match to thumbs/
"""
import io
import os
import re
import sys
import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

import cricbuzz
import faces

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "thumbs")
FONT_DIR = os.path.join(HERE, "assets", "fonts")
STATIC = "https://static.cricbuzz.com/a/img/v1"
W, H = 1280, 720

RED = (230, 20, 30)
GOLD = (255, 200, 30)
YELLOW = (255, 221, 0)
WHITE = (255, 255, 255)
BLACK = (10, 10, 14)

# kit colours by team name (lower case, "women"/" a" stripped)
TEAM_RGB = {
    "india": (12, 82, 205), "new zealand": (25, 25, 28),
    "australia": (250, 190, 10), "england": (16, 40, 120),
    "pakistan": (6, 120, 60), "south africa": (0, 120, 75),
    "sri lanka": (16, 50, 150), "bangladesh": (0, 106, 78),
    "afghanistan": (20, 80, 190), "west indies": (122, 0, 40),
    "zimbabwe": (205, 20, 30), "ireland": (20, 150, 70),
    "netherlands": (240, 110, 0), "scotland": (40, 60, 150),
    "nepal": (200, 16, 46), "oman": (200, 20, 40), "uae": (10, 120, 60),
    "usa": (20, 40, 120), "namibia": (20, 70, 160), "japan": (230, 60, 140),
    "chennai super kings": (250, 200, 0), "mumbai indians": (0, 75, 160),
    "royal challengers bengaluru": (200, 20, 30),
    "kolkata knight riders": (60, 30, 110), "sunrisers hyderabad": (240, 100, 20),
    "delhi capitals": (20, 60, 160), "punjab kings": (210, 20, 40),
    "rajasthan royals": (230, 40, 140), "gujarat titans": (20, 40, 80),
    "lucknow super giants": (0, 90, 170),
}


def font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default()


def heavy(size):
    return font("Anton-Regular.ttf", size)


def bold(size):
    return font("BarlowCondensed-ExtraBold.ttf", size)


# ── data ───────────────────────────────────────────────────────────────────
def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def captains(mid):
    """team id -> (player id, name) from the scorecard, where each XI's
    captain is flagged. Empty before the toss."""
    d = cricbuzz._json("/api/mcenter/scorecard/%s" % mid) or {}
    out = {}
    for inn in d.get("scoreCard") or []:
        bat = inn.get("batTeamDetails") or {}
        for p in (bat.get("batsmenData") or {}).values():
            if p.get("isCaptain"):
                out.setdefault(bat.get("batTeamId"), (p.get("batId"),
                                                      p.get("batName")))
        bowl = inn.get("bowlTeamDetails") or {}
        for p in (bowl.get("bowlersData") or {}).values():
            if p.get("isCaptain"):
                out.setdefault(bowl.get("bowlTeamId"), (p.get("bowlerId"),
                                                        p.get("bowlName")))
    return out


def _download(url):
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, impersonate="chrome", timeout=25)
        if r.status_code == 200 and r.content:
            return Image.open(io.BytesIO(r.content))
    except Exception:
        pass
    return None


def captain_photo(pid, name):
    """Big cut-out headshot (RGBA), cached in assets/players/{pid}_big.png."""
    if not pid:
        return None
    path = os.path.join(faces.PLAYER_DIR, "%s_big.png" % pid)
    if os.path.exists(path):
        return Image.open(path).convert("RGBA")
    slug = _slug(name or "")
    html = cricbuzz._page("/profiles/%s/%s" % (pid, slug))
    m = re.search(r"/i1/c(\d+)/%s\.jpg" % re.escape(slug), html) or \
        re.search(r"/\d+x\d+/i1/c(\d+)/[a-z0-9\-]+\.jpg", html)
    if not m:
        return None
    im = _download("%s/600x600/i1/c%s/i.jpg" % (STATIC, m.group(1)))
    if im is None or faces._is_silhouette(im):
        return None
    cut = faces._cutout(im)
    os.makedirs(faces.PLAYER_DIR, exist_ok=True)
    cut.save(path)
    return cut


def team_badge(image_id):
    """The team's flag / logo from the schedule's imageId."""
    if not image_id:
        return None
    im = _download("%s/240x160/i1/c%s/i.jpg" % (STATIC, image_id))
    return im.convert("RGB") if im else None


def team_rgb(name, badge=None):
    n = name.lower().replace(" women", "").strip()
    if n.endswith(" a") or n.endswith(" u19"):
        n = n.rsplit(" ", 1)[0]
    if n in TEAM_RGB:
        return TEAM_RGB[n]
    if badge is not None:
        # most saturated common colour in the badge
        a = np.asarray(badge.resize((48, 32)), np.float32).reshape(-1, 3)
        sat = a.max(1) - a.min(1)
        good = a[(sat > 60) & (a.max(1) > 60)]
        if len(good) > 20:
            q = (good // 32).astype(int)
            keys, counts = np.unique(q, axis=0, return_counts=True)
            k = keys[counts.argmax()]
            c = good[(q == k).all(1)].mean(0)
            return tuple(int(v) for v in c)
    random.seed(n)
    return tuple(random.randint(40, 200) for _ in range(3))


# ── drawing helpers ────────────────────────────────────────────────────────
def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def _grad_panel(size, top, bot):
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None]
    a = np.array(top, np.float32) * (1 - t) + np.array(bot, np.float32) * t
    return Image.fromarray(np.repeat(a, w, axis=1).astype(np.uint8), "RGB")


def _text_size(d, text, f):
    b = d.textbbox((0, 0), text, font=f)
    return b[2] - b[0], b[3] - b[1], b[1]


def _fit(d, text, maker, size, max_w, min_size=28):
    while size > min_size:
        f = maker(size)
        if _text_size(d, text, f)[0] <= max_w:
            return f
        size -= 2
    return maker(min_size)


def _stroke_text(img, xy, text, f, fill, stroke, sw, anchor="mm",
                 shadow=8):
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((xy[0] + shadow, xy[1] + shadow), text,
                                font=f, fill=(0, 0, 0, 170), anchor=anchor,
                                stroke_width=sw, stroke_fill=(0, 0, 0, 170))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(shadow / 2)))
    ImageDraw.Draw(img).text(xy, text, font=f, fill=fill, anchor=anchor,
                             stroke_width=sw, stroke_fill=stroke)


def _outlined(cut, px=7, color=WHITE):
    """Sticker look: a solid white edge around the cut-out, then a shadow."""
    a = cut.split()[-1].point(lambda v: 255 if v > 90 else 0)
    grow = a.filter(ImageFilter.MaxFilter(px * 2 + 1))
    pad = Image.new("RGBA", cut.size, color + (0,))
    pad.putalpha(grow)
    out = Image.new("RGBA", cut.size, (0, 0, 0, 0))
    out.alpha_composite(pad)
    out.alpha_composite(cut)
    return out


def _paste_shadowed(img, layer, xy, blur=14, off=(10, 12), alpha=150):
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(layer.split()[-1].point(lambda v: v * alpha // 255))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(sh, (xy[0] + off[0], xy[1] + off[1]))
    img.alpha_composite(layer, xy)


def _circle_badge(badge, d, ring=GOLD):
    """Flag / logo in a ringed circle, like the flag discs behind captains."""
    if badge is None:
        return None
    b = badge.copy()
    s = min(b.size)
    b = b.crop(((b.width - s) // 2, (b.height - s) // 2,
                (b.width + s) // 2, (b.height + s) // 2)).resize((d, d),
                                                                  Image.LANCZOS)
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d - 1, d - 1), fill=255)
    out = Image.new("RGBA", (d + 16, d + 16), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse((0, 0, d + 15, d + 15), fill=ring + (255,))
    disc = b.convert("RGBA")
    disc.putalpha(m)
    out.alpha_composite(disc, (8, 8))
    return out


def _fallback_player(name, color):
    """No photo: a big ringed badge with initials in place of the captain."""
    s = 420
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, s - 10, s - 10), fill=_shade(color, 0.55) + (255,),
              outline=WHITE, width=10)
    ini = "".join(w[0] for w in (name or "?").split()[:2]).upper()
    d.text((s // 2, s // 2), ini, font=heavy(170), fill=WHITE, anchor="mm")
    return im


# ── the thumbnail ──────────────────────────────────────────────────────────
def short_desc(desc, fmt):
    """'5th T20I' -> '5TH T20I'; 'Elite Group B' (no number) -> the format."""
    d = re.sub(r"\s+", " ", (desc or "").upper()).strip()
    if not d or len(d) > 16:
        d = {"T20": "T20 MATCH", "ODI": "ODI MATCH", "TEST": "TEST MATCH"}.get(
            fmt, "LIVE MATCH")
    return d


def render(m, part_label="", out_path=None, channel=""):
    """m: a picker.schedule() match (t1, t2, id1, id2, img1, img2, desc,
    format, series). Returns the JPEG path."""
    rng = random.Random(m.get("mid"))
    img = Image.new("RGBA", (W, H), BLACK + (255,))

    badge1, badge2 = team_badge(m.get("img1")), team_badge(m.get("img2"))
    c1, c2 = team_rgb(m["t1"], badge1), team_rgb(m["t2"], badge2)

    # background: two team panels split on a slant, light falling from above
    split_top, split_bot = 700, 580
    p1 = _grad_panel((W, H), _shade(c1, 1.25), _shade(c1, 0.45)).convert("RGBA")
    p2 = _grad_panel((W, H), _shade(c2, 1.25), _shade(c2, 0.45)).convert("RGBA")
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (split_top, 0), (split_bot, H),
                                  (0, H)], fill=255)
    img.paste(p2, (0, 0))
    img.paste(p1, (0, 0), mask)

    # speed stripes + floodlight glows + vignette
    fx = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fx)
    for _ in range(26):
        x = rng.randint(-200, W)
        w = rng.randint(6, 40)
        d.polygon([(x, 0), (x + w, 0), (x + w - 160, H), (x - 160, H)],
                  fill=(255, 255, 255, rng.randint(8, 22)))
    for gx in (120, 640, 1160):
        d.ellipse((gx - 260, -330, gx + 260, 150), fill=(255, 255, 240, 60))
    img.alpha_composite(fx.filter(ImageFilter.GaussianBlur(18)))
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse((-260, -200, W + 260, H + 300), fill=255)
    vig = ImageChops.invert(vig.filter(ImageFilter.GaussianBlur(120)))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dark.putalpha(vig.point(lambda v: v * 200 // 255))
    img.alpha_composite(dark)

    # the slant: a white edge with a gold core
    sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sl)
    ds.polygon([(split_top - 12, 0), (split_top + 12, 0),
                (split_bot + 12, H), (split_bot - 12, H)], fill=WHITE + (255,))
    ds.polygon([(split_top - 5, 0), (split_top + 5, 0),
                (split_bot + 5, H), (split_bot - 5, H)], fill=GOLD + (255,))
    img.alpha_composite(sl)

    # flag discs behind the captains
    for badge, cx in ((badge1, 150), (badge2, W - 150)):
        disc = _circle_badge(badge, 250)
        if disc is not None:
            _paste_shadowed(img, disc, (cx - disc.width // 2, 18), blur=10,
                            off=(6, 8))

    # captains, facing each other
    caps = captains(m["mid"]) if m.get("mid") else {}
    for side, (tid, tname, col) in enumerate(((m.get("id1"), m["t1"], c1),
                                              (m.get("id2"), m["t2"], c2))):
        pid, pname = caps.get(tid, (None, None))
        cut = captain_photo(pid, pname) if pid else None
        if cut is None:
            # no captain yet: the team badge, big
            b = _circle_badge(badge1 if side == 0 else badge2, 330)
            cut = b if b is not None else _fallback_player(tname, col)
            bh = 360
        else:
            bb = cut.getbbox()
            if bb:
                cut = cut.crop(bb)
            bh = 500
            if side == 1:
                cut = cut.transpose(Image.FLIP_LEFT_RIGHT)
        scale = bh / cut.height
        cut = cut.resize((int(cut.width * scale), bh), Image.LANCZOS)
        cut = _outlined(cut)
        cx = 275 if side == 0 else W - 275
        y = 612 - cut.height if bh == 500 else 190
        _paste_shadowed(img, cut, (cx - cut.width // 2, y))

    d = ImageDraw.Draw(img)

    # centre column
    if part_label:
        f = bold(40)
        tw, th, _ = _text_size(d, part_label.upper(), f)
        x0, y0 = W // 2 - tw // 2 - 26, 22
        d.rounded_rectangle((x0, y0, x0 + tw + 52, y0 + 58), 29,
                            fill=BLACK + (235,), outline=YELLOW, width=4)
        d.text((W // 2, y0 + 29), part_label.upper(), font=f, fill=YELLOW,
               anchor="mm")

    f = heavy(178)
    lw = _text_size(d, "LIVE", f)[0]
    _stroke_text(img, (W // 2 + 26, 182), "LIVE", f, RED, WHITE, 9,
                 shadow=10)
    # the red dot of a live tag, just left of the word
    d = ImageDraw.Draw(img)
    x = W // 2 + 26 - lw // 2 - 34
    d.ellipse((x - 22, 161, x + 22, 205), fill=RED, outline=WHITE, width=6)

    desc = short_desc(m.get("desc"), m.get("format"))
    f = _fit(d, desc, heavy, 74, 380)
    tw, th, top = _text_size(d, desc, f)
    bw = tw + 60
    box = Image.new("RGBA", (bw, 100), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle((0, 0, bw - 1, 99), 14,
                                          fill=WHITE + (255,),
                                          outline=RED, width=6)
    ImageDraw.Draw(box).text((bw // 2, 50), desc, font=f, fill=RED,
                             anchor="mm")
    _paste_shadowed(img, box, (W // 2 - bw // 2, 290), blur=8, off=(6, 8))

    rib = "HINDI COMMENTARY"
    f = bold(46)
    tw, th, _ = _text_size(d, rib, f)
    rw = tw + 70
    r = Image.new("RGBA", (rw, 64), (0, 0, 0, 0))
    ImageDraw.Draw(r).polygon([(18, 0), (rw, 0), (rw - 18, 63), (0, 63)],
                              fill=YELLOW + (255,))
    ImageDraw.Draw(r).text((rw // 2, 31), rib, font=f, fill=BLACK,
                           anchor="mm")
    _paste_shadowed(img, r, (W // 2 - rw // 2, 408), blur=6, off=(5, 6))

    series = re.sub(r",?\s*\d{4}(-\d{2,4})?$", "", m.get("series") or "")
    if series:
        f = _fit(d, series.upper(), bold, 34, 440, 22)
        _stroke_text(img, (W // 2, 506), series.upper(), f, WHITE, BLACK, 3,
                     shadow=4)

    # arrow banners with the team names, VS between them
    by, bh = 606, 96
    for side, (name, col) in enumerate(((m["t1"], c1), (m["t2"], c2))):
        ban = Image.new("RGBA", (600, bh), (0, 0, 0, 0))
        db = ImageDraw.Draw(ban)
        mid, end = bh // 2, bh - 1
        if side == 0:       # points right, at the VS
            outer = [(0, 0), (540, 0), (598, mid), (540, end), (0, end)]
            inner = [(0, 6), (536, 6), (588, mid), (536, end - 6), (0, end - 6)]
        else:               # points left
            outer = [(60, 0), (599, 0), (599, end), (60, end), (2, mid)]
            inner = [(64, 6), (599, 6), (599, end - 6), (64, end - 6),
                     (12, mid)]
        db.polygon(outer, fill=BLACK + (255,))
        g = _grad_panel((600, bh), _shade(col, 1.15),
                        _shade(col, 0.55)).convert("RGBA")
        gm = Image.new("L", (600, bh), 0)
        ImageDraw.Draw(gm).polygon(inner, fill=255)
        ban.paste(g, (0, 0), gm)
        label = name.upper()
        f = _fit(db, label, heavy, 76, 480, 40)
        tx = 270 if side == 0 else 330
        _stroke_text(ban, (tx, bh // 2 - 2), label, f, WHITE, BLACK, 4,
                     shadow=0)
        img.alpha_composite(ban, (0 if side == 0 else W - 600, by))

    vs = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
    dv = ImageDraw.Draw(vs)
    dv.ellipse((4, 4, 145, 145), fill=BLACK + (255,), outline=GOLD, width=7)
    _stroke_text(vs, (75, 72), "VS", heavy(70), GOLD, BLACK, 3, shadow=0)
    _paste_shadowed(img, vs, (W // 2 - 75, by + bh // 2 - 75), blur=8,
                    off=(4, 6))

    if channel:
        f = bold(26)
        _stroke_text(img, (W - 20, H - 6), channel, f, WHITE, BLACK, 2,
                     anchor="rd", shadow=0)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = out_path or os.path.join(
        OUT_DIR, "%s_%s.jpg" % (m.get("mid"), _slug(part_label) or "x"))
    img.convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


if __name__ == "__main__":
    import picker
    want = sys.argv[1] if len(sys.argv) > 1 else None
    ms = picker.schedule()
    hit = [m for m in ms if m["mid"] == want] if want else ms[:1]
    if not hit:
        sys.exit("match %s is not on the Cricbuzz schedule pages" % want)
    print(render(hit[0], sys.argv[2] if len(sys.argv) > 2 else "1st Innings",
                 channel=os.environ.get("CHANNEL_NAME", "")))
