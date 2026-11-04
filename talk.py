# -*- coding: utf-8 -*-
"""
talk.py  -  how a person says it.

phrasing.py holds WHAT can be said; this module is about sounding like a
person saying it. Audited from a long generated sample, the robotic tells
were:

  * every ball had the same 5-6 line skeleton - opener, description,
    "बल्लेबाज़ ने ...", result, fielder, opinion - even for a dot ball
  * the same fact said 2-3 times ("बल्ले के पास से!" / "बल्ले को छुए बिना
    निकल गई" / "डॉट!")
  * the fielding position as its own line, every ball, after the result
  * full names of both players on every single ball
  * numbers read like a calculator: "पांच दशमलव छह सात"
  * stats without meaning: a centurion read out as "एक सौ एक रन बनाकर खेल
    रहे हैं"; "एक गेंदों पर शून्य"
  * no memory of the ball before

So a normal ball is now ONE spoken sentence in the order a commentator says
it - "शॉर्ट गेंद, मिड विकेट की तरफ़ पुल किया, एक रन" - names are full on
first mention and surnames after (or left out), numbers are spoken the way
people speak them ("पौने छह के आसपास"), and each stat comes with what it
means.
"""
import random

import phrasing as P


def surname(nm):
    parts = str(nm or "").split()
    return parts[-1] if parts else ""


class Namer:
    """Full name the first time in a while, then the surname - the way you
    talk about someone you are watching."""

    def __init__(self, fresh_after=14):
        self.last = {}
        self.fresh_after = fresh_after

    def __call__(self, nm, now):
        if not nm:
            return ""
        last = self.last.get(nm)
        self.last[nm] = now
        if last is None or now - last > self.fresh_after:
            return nm
        return surname(nm)


# ── Numbers the way people say them ─────────────────────────────────────────
def rate_words(v):
    """5.67 -> 'पौने छह', 5.5 -> 'साढ़े पांच', 1.5 -> 'डेढ़', 2.5 -> 'ढाई'."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    w = int(v)
    f = v - w
    if f < 0.13:
        return P.num_hi(w)
    if f < 0.38:
        return "सवा " + P.num_hi(w)
    if f < 0.63:
        return {1: "डेढ़", 2: "ढाई"}.get(w, "साढ़े " + P.num_hi(w))
    if f < 0.88:
        return "पौने " + P.num_hi(w + 1)
    return P.num_hi(w + 1)


def balls_hi(n):
    n = int(n or 0)
    return "एक गेंद" if n == 1 else "%s गेंदें" % P.num_hi(n)


def balls_obl(n):
    """Oblique: 'एक गेंद पर' / 'बीस गेंदों पर'."""
    n = int(n or 0)
    return "एक गेंद" if n == 1 else "%s गेंदों" % P.num_hi(n)


# ── How a ball is started ───────────────────────────────────────────────────
# Short, and usually ONE name or none - the old opener named bowler and
# batsman in full on every ball. The "..." is a real pause in the voice.
OPEN_PLAIN = ["अगली गेंद...", "और ये गेंद...", "चलिए, अगली...",
              "अब अगली गेंद...", "रन-अप पर बॉलर...", "और ये..."]
OPEN_BOWLER = ["{bw}...", "{bw} दौड़ते हुए...", "{bw} की अगली गेंद...",
               "{bw} फिर से रन-अप पर...", "गेंद {bw} के हाथ में..."]
OPEN_BAT = ["सामने {bt}...", "{bt} स्ट्राइक पर...", "{bt} तैयार..."]
OPEN_BOTH = ["{bw}, और सामने {bt}...", "{bw} बनाम {bt}..."]


# ── One-breath ball description ─────────────────────────────────────────────
LEN_SAY = {
    "yorker": ["यॉर्कर", "पैरों में यॉर्कर"],
    "fulltoss": ["फुल टॉस"],
    "halfvolley": ["हाफ वॉली"],
    "bouncer": ["बाउंसर", "तेज़ बाउंसर"],
    "backlen": ["थोड़ी पीछे की लेंथ", "बैक ऑफ़ लेंथ"],
    "good": ["गुड लेंथ", "अच्छी लेंथ"],
    "short": ["शॉर्ट गेंद", "छोटी गेंद", "शॉर्ट"],
    "full": ["फुल गेंद", "आगे की गेंद", "पिच अप"],
}
LINE_SAY = {
    "wide": ["काफ़ी बाहर"], "outoff": ["ऑफ़ के बाहर"],
    "offstump": ["ऑफ़ स्टंप पर"], "middle": ["सीधी स्टंप्स पर"],
    "legstump": ["लेग स्टंप पर"], "pads": ["पैड्स पर"],
}
# verb phrases; "{pos} की तरफ़" can go in front of the movable ones
SHOT_SAY = {
    "drive": ["ड्राइव किया", "पूरे बल्ले से ड्राइव"],
    "pull": ["पुल किया", "खींच दिया"],
    "hook": ["हुक किया"],
    "cut": ["कट किया", "काट दिया"],
    "sweep": ["स्वीप किया"],
    "flick": ["फ्लिक किया", "कलाइयों से मोड़ा"],
    "slog": ["बल्ला घुमाया"],
    "dance": ["आगे निकलकर खेला"],
    "defend": ["रोक दिया", "सीधे बल्ले से रोका", "डिफ़ेंड किया"],
    "leave": ["छोड़ दी", "जाने दी"],
    "ducks": ["झुककर निकाल दी"],
}
MOVABLE = {"drive", "pull", "hook", "cut", "sweep", "flick", "slog", "dance"}
RES_SAY = {
    "dot": ["कोई रन नहीं", "रन नहीं", "डॉट", "खाली गई ये गेंद",
            "रन नहीं बना इस बार", "कोई रन नहीं, रोक दिया"],
    "one": ["एक रन", "सिंगल", "एक रन मिल गया", "एक रन, स्ट्राइक बदल गई",
            "आराम से एक रन", "एक रन लेकर छोर बदला"],
    "two": ["दो रन", "दो रन ले लिए", "दो रन, बढ़िया दौड़",
            "पहला तेज़ी से, और दूसरा भी पूरा"],
    "three": ["तीन रन!", "तीन रन, बढ़िया रनिंग"],
    "wide": ["वाइड", "वाइड गेंद"],
    "bye": ["बाई का एक रन"],
    "legbye": ["लेग बाई"],
}


def _res(kind, runs):
    if kind in RES_SAY:
        return random.choice(RES_SAY[kind])
    if kind == "runs":
        return "%s रन" % P.num_hi(runs)
    return ""


def ball_sentence(play, kind, runs, pos, bat_name="", again=False):
    """One spoken sentence for an ordinary ball, e.g.
    'शॉर्ट गेंद, मिड विकेट की तरफ़ पुल किया, एक रन'."""
    L, ln, act = play.get("length"), play.get("line"), play.get("act")
    parts = []
    said_line = False
    if L:
        f = random.choice(LEN_SAY[L])
        if ln and random.random() < 0.3:
            f = "%s, %s" % (f, random.choice(LINE_SAY[ln]))
            said_line = True
        parts.append(("फिर से " + f) if again else f)
    elif ln and random.random() < 0.6:
        parts.append(random.choice(LINE_SAY[ln]))

    res = _res(kind, runs)
    shot = random.choice(SHOT_SAY[act]) if act in SHOT_SAY else ""
    scoring = kind in ("one", "two", "three", "runs")
    if act == "defend" and scoring:
        shot = random.choice(["हल्का सा धकेला", "धीरे से खेला"])
    if shot:
        if pos and (act in MOVABLE or scoring):
            shot = "%s की तरफ़ %s" % (pos, shot)
        # a name too, but not on top of length AND line - too long a breath
        if bat_name and not said_line and random.random() < 0.3:
            shot = "%s ने %s" % (bat_name, shot)
        parts.append(shot)
        # leave / defend on a dot: the result is obvious, don't say it
        if kind == "dot" and act in ("leave", "defend", "ducks") and \
                random.random() < 0.7:
            res = ""
    elif pos and scoring:
        res = "%s की तरफ़ खेलकर %s" % (pos, res)
    if res:
        parts.append(res)
    if not parts:
        return ""
    return ", ".join(parts)


# ── Stats with meaning ──────────────────────────────────────────────────────
def batter_line(b, nm, fmt_max=None):
    """What a batsman's score MEANS, not just what it is."""
    r, bl = b.get("runs"), b.get("balls") or 0
    if not nm or r is None:
        return ""
    R, B = P.num_hi(r), balls_obl(bl)
    if r >= 100:
        return random.choice([
            "%s का शतक हो चुका है, अब %s पर खेल रहे हैं" % (nm, R),
            "शतकवीर %s अब भी क्रीज़ पर, %s रन, %s में" % (nm, R, B),
            "क्या पारी है %s की, %s रन" % (nm, R)])
    if 90 <= r <= 99:
        return random.choice([
            "%s %s पर, शतक बस कुछ ही रन दूर" % (nm, R),
            "नर्वस नाइंटीज़ में हैं %s, %s पर" % (nm, R)])
    if r >= 50:
        return random.choice([
            "%s फिफ्टी के बाद भी जमे हुए हैं, %s पर" % (nm, R),
            "%s %s रन बना चुके हैं, %s में" % (nm, R, B)])
    if 40 <= r <= 49:
        return "%s %s पर, फिफ्टी अब क़रीब है" % (nm, R)
    if r == 0:
        return random.choice(["%s का खाता अभी खुलना बाकी है" % nm,
                              "%s अभी शून्य पर" % nm])
    if bl <= 6:
        return random.choice([
            "%s अभी-अभी आए हैं, %s रन" % (nm, R),
            "%s ने अभी पारी शुरू की है, %s पर" % (nm, R),
            "नए बल्लेबाज़ %s, अभी सेट होने में वक़्त लगेगा" % nm])
    return random.choice([
        "%s %s पर, %s खेल चुके हैं" % (nm, R, balls_hi(bl)),
        "%s के %s रन" % (nm, R),
        "%s %s रन बनाकर टिके हुए हैं" % (nm, R)])


def bowler_line(b, nm, good_eco=6.0, bad_eco=10.0):
    w, r, ov = b.get("wickets"), b.get("runs"), b.get("overs")
    if not nm or w is None or r is None:
        return ""
    try:
        ovn = int(float(ov or 0))
        eco = float(b.get("economy") or 0)
    except (TypeError, ValueError):
        ovn, eco = 0, 0.0
    O, R, W = P.num_hi(ovn), P.num_hi(r), P.num_hi(w)
    if w >= 3:
        return random.choice([
            "%s आज कमाल कर रहे हैं, %s विकेट ले चुके हैं" % (nm, W),
            "%s का ज़बरदस्त स्पेल, %s ओवर में %s विकेट" % (nm, O, W)])
    if w == 0 and ovn >= 2 and eco and eco <= good_eco:
        return "%s को विकेट भले न मिला हो, पर रन रोककर रखे हैं" % nm
    if ovn >= 2 and eco >= bad_eco:
        return "%s आज थोड़े महँगे रहे हैं, %s ओवर में %s रन" % (nm, O, R)
    if w:
        return random.choice([
            "%s के आँकड़े, %s ओवर, %s रन, %s विकेट" % (nm, O, R, W),
            "%s ने %s रन देकर %s विकेट लिए हैं" % (nm, R, W)])
    return "%s ने %s ओवर में %s रन दिए हैं" % (nm, O, R)


def last_wicket_line(nm, r, b):
    r, b = int(r), int(b)
    if r == 0:
        return random.choice([
            "%s बिना खाता खोले लौटे थे" % nm,
            "पिछली बार %s शून्य पर आउट हुए थे" % nm])
    return random.choice([
        "पिछला विकेट %s का था, %s रन बनाकर, %s में" % (nm, P.num_hi(r),
                                                     balls_obl(b)),
        "%s %s बनाकर गए थे" % (nm, P.num_hi(r))])


def score_short(team, runs, wkts):
    R, W = P.num_hi(runs), P.num_hi(wkts)
    if wkts >= 10:                      # nobody says "दस विकेट"
        return random.choice(["%s की पूरी टीम %s रन पर ऑल आउट" % (team, R),
                              "%s की पारी %s रन पर सिमट गई" % (team, R)])
    if not wkts:
        return random.choice(["%s %s पर, कोई विकेट नहीं" % (team, R),
                              "स्कोर %s, बिना विकेट गँवाए" % R])
    return random.choice(["%s %s पर %s" % (team, R, W),
                          "स्कोर %s पर %s विकेट" % (R, W),
                          "%s रन, %s विकेट" % (R, W)])


# ── Interjections: the sounds a person makes before the words ──────────────
# Picked by what actually happened, never at random - "अरे वाह" on a dot ball
# would be as fake as no interjection at all.
INTERJ = {
    # Kept to what a real commentator actually says; the louder, comic ones
    # ("भागो भागो!", "ओह नो!", "बाल बाल!") made it sound like a cartoon.
    "near": ["उफ़्फ़,", "अरे,", "ओहो,", "बस थोड़ा सा,"],
    "drop": ["अरे, ये क्या हुआ!", "ओह, कैच छूट गया!", "उफ़्फ़, ये तो हाथ में था!"],
    "shot": ["वाह,", "क्या बात है,", "शानदार,", "बहुत बढ़िया,"],
    "tight": ["अच्छी गेंद,", "बढ़िया गेंदबाज़ी,", "अच्छा,"],
    "wide": ["अरे,", "उफ़्फ़,"],
    "wicket": ["ओह!", "ये क्या हुआ!", "अरे!"],
    "run": ["दूसरा भी,", "तेज़ दौड़,"],
}


def interject(kind, act, runs=0):
    """An interjection that fits this ball, or ''."""
    if act == "chance":
        return random.choice(INTERJ["drop"])
    if act in ("beaten", "edge") or (act == "appeal" and kind == "dot"):
        return random.choice(INTERJ["near"])
    if kind == "wide":
        return random.choice(INTERJ["wide"]) if random.random() < 0.5 else ""
    if kind in ("two", "three") and random.random() < 0.2:
        return random.choice(INTERJ["run"])
    if kind in ("one", "two", "three") and act in ("drive", "cut", "pull",
                                                    "flick", "sweep") and \
            random.random() < 0.15:
        return random.choice(INTERJ["shot"])
    if kind == "dot" and act in ("defend", "leave", None) and \
            random.random() < 0.12:
        return random.choice(INTERJ["tight"])
    return ""


# ── Thinking aloud: a commentator's own asides between balls ───────────────
# Conversational, opinionated, a little playful - said to the viewer, not
# read off a sheet.
THINK = [
    "अच्छा, एक चीज़ नोटिस कीजिए, बल्लेबाज़ हर गेंद के बाद क्रीज़ से बाहर निकल रहे हैं",
    "देखिए, बात ऐसी है, इस पिच पर टिक गए तो रन बनाना मुश्किल नहीं",
    "मैं तो कहूँगा, अगले दो ओवर बहुत अहम होने वाले हैं",
    "पिक्चर अभी बाकी है दोस्तों, मैच अभी खुला हुआ है",
    "सवाल ये है कि कप्तान अब किसे गेंद देंगे",
    "मुझे ये मुक़ाबला देखकर बड़ा मज़ा आ रहा है, सच में",
    "एक बात बताऊँ, ऐसे मैचों में छोटी-छोटी चीज़ें ही फ़र्क़ डालती हैं",
    "ये बल्लेबाज़ जितनी देर टिकेंगे, बॉलिंग टीम की टेंशन उतनी बढ़ेगी",
    "सोचिए, अगर यहाँ एक और विकेट गिर जाए तो क्या होगा",
    "क्रिकेट भी कमाल का खेल है, एक गेंद में सब बदल जाता है",
    "बॉलर का चेहरा देखिए, वो किसी प्लान के साथ आए हैं",
    "थोड़ा धैर्य चाहिए यहाँ, जल्दबाज़ी में विकेट जाते हैं",
    "अरे भाई, ऐसे ही थोड़ी ना कहते हैं कि क्रिकेट अनिश्चितताओं का खेल है",
    "मुझे लगता है कप्तान फील्ड में कोई चाल चलने वाले हैं",
    "ईमानदारी से कहूँ, इस वक़्त दोनों टीमें बराबरी पर हैं",
    "अच्छा, आप कमेंट में बताइए, आप होते कप्तान तो क्या करते",
]


THINK += [
    "आप ध्यान दीजिए, फील्डर कितने आगे-पीछे हो रहे हैं, हर गेंद पर प्लान बदल रहा है",
    "मुझे इस बॉलर की एक बात बहुत पसंद आती है, ये घबराते नहीं हैं",
    "अब यहाँ से जो टीम ज़्यादा धैर्य दिखाएगी, वही आगे निकलेगी",
    "सच कहूँ तो ऐसे ही मुक़ाबले के लिए हम क्रिकेट देखते हैं",
    "देखिए, स्कोरबोर्ड जो भी कहे, मैदान पर अभी कुछ भी हो सकता है",
    "बॉलर और बल्लेबाज़ के बीच एक छोटी सी जंग चल रही है, बड़ी मज़ेदार",
    "थोड़ा रुककर सोचिए, पिछले कुछ ओवरों में मैच किस तरफ़ झुका है",
    "मैं होता कप्तान तो यहाँ एक फील्डर को और अंदर ले आता",
    "ये वक़्त बल्लेबाज़ों के लिए सोच-समझकर खेलने का है",
    "अरे भाई, यही तो असली टेस्ट है, प्रेशर में कौन टिकता है",
    "मुझे लगता है बल्लेबाज़ किसी एक बॉलर को निशाना बनाने की सोच रहे हैं",
    "एक मज़ेदार बात, इस तरह के मैचों में अक्सर आख़िरी घंटा सब तय करता है",
    "दर्शकों का जोश देखिए, हर रन पर तालियाँ बज रही हैं",
    "कभी-कभी डॉट बॉल भी चौके जितनी अहम होती है, आज वैसा ही दिन है",
    "एक चीज़ और, फील्डिंग इस मैच में बहुत बड़ा फ़र्क़ डाल सकती है",
    "बल्लेबाज़ शायद अगले ओवर का इंतज़ार कर रहे हैं, जब बड़े शॉट लगाए जा सकें",
    "मुझे इस मैच की रफ़्तार बड़ी दिलचस्प लग रही है, न बहुत तेज़, न बहुत धीमा",
    "आप सोचिए, बॉलिंग कोच अभी ड्रेसिंग रूम में क्या नोट्स बना रहे होंगे",
    "ऐसे वक़्त में अनुभव काम आता है, बड़े खिलाड़ी यहीं पहचाने जाते हैं",
    "थोड़ा सा भी ढीलापन, और मैच हाथ से निकल सकता है",
]

# Observations that name the players on the field - they change with who is
# playing, so they do not wear out the way a fixed line does.
THINK_NAMED = [
    "{bowl} की लाइन आज काफ़ी सटीक रही है",
    "{bowl} गेंद को अच्छे से अंदर-बाहर कर रहे हैं",
    "{bat} और {bat2} के बीच अच्छी समझ दिख रही है",
    "{bat} आज किसी जल्दबाज़ी में नहीं दिख रहे",
    "{bat} का फ़ुटवर्क देखिए, बहुत साफ़",
    "{bat2} दूसरे छोर से अच्छा साथ निभा रहे हैं",
    "{bowl} के पास अभी कुछ ओवर बाकी हैं, कप्तान इनका सही इस्तेमाल करना चाहेंगे",
    "{bat} पर टीम की बड़ी उम्मीदें टिकी हैं",
    "{team} को यहाँ से एक बड़ी पार्टनरशिप चाहिए",
    "{bowl} बल्लेबाज़ों को खुलकर खेलने का मौका नहीं दे रहे",
    "{bat} को अगर ढीली गेंद मिली, तो वो छोड़ने वाले नहीं",
    "{bat2} स्ट्राइक बदलने पर ध्यान दे रहे हैं, समझदारी है",
    "{team} के डगआउट में भी हलचल दिख रही होगी",
    "{bowl} का रन-अप देखिए, एकदम लय में हैं",
    "अब {bat} और {bowl} के बीच की टक्कर देखने लायक होगी",
]


# ── Remembering the last ball ───────────────────────────────────────────────
def callback(prev, ball, play):
    """A line that connects this ball to the one before, or ''."""
    if not prev:
        return ""
    kind = ball["kind"]
    same_bat = prev.get("bat") == ball.get("bat")
    same_bowl = prev.get("bowler") == ball.get("bowler")
    scoring = kind in ("one", "two", "three", "runs")
    if same_bat and prev.get("act") in ("beaten", "edge") and scoring:
        return random.choice([
            "पिछली गेंद पर बीट हुए थे, इस बार रन निकाल लिया",
            "पिछली गेंद पर परेशान थे, पर इस बार अच्छी वापसी"])
    if same_bowl and prev.get("kind") in ("four", "six") and kind == "dot":
        return random.choice([
            "बाउंड्री के बाद अच्छी वापसी बॉलर की",
            "मार खाने के बाद बॉलर ने रन रोक दिए"])
    if same_bowl and prev.get("kind") == "dot" and kind == "dot" and \
            prev.get("dots", 0) >= 1 and random.random() < 0.5:
        return random.choice(["बॉलर लगातार दबाव बना रहे हैं",
                              "बल्लेबाज़ को रन नहीं मिल रहे"])
    if prev.get("singles", 0) >= 2 and kind == "one" and random.random() < 0.5:
        return random.choice(["सिंगल्स आते जा रहे हैं, स्ट्राइक घूम रही है",
                              "एक-एक रन, स्कोरबोर्ड चलता रहे"])
    return ""
