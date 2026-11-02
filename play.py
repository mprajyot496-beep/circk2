# -*- coding: utf-8 -*-
"""
play.py  -  read what actually happened on the ball and talk about it like a
person, instead of reading a template.

The Cricbuzz delivery text already says it all:

    "full and swinging away outside off, Iqbal gets forward and knocks it down"
    "full delivery just outside off, Iqbal looks to drive but it zips past the
     edge to the keeper"
    "dug in short ... This time he ducks and evades"

parse_play() pulls that apart into length / line / movement / what the batter
did / how the ball ended up. Commentary is then built from those parts in the
order a commentator says them:

    1. the ball       "फुल गेंद, ऑफ़ के बाहर, बाहर की ओर स्विंग होती हुई"
    2. the response   "बल्लेबाज़ ने ड्राइव करने की कोशिश की"
    3. the result     "गेंद बल्ले के किनारे से बाल बाल बच कर कीपर के पास"
    4. the reading    "देखिए, गेंदबाज़ ने यही तो चाहा था ..."

Only things the feed says are stated as fact. The "what was he thinking" part is
always hedged (शायद / लगता है) and is keyed on real context - a dot-ball streak,
a batter on 97, a loose ball being punished - never invented detail.
"""
import re
import random

# ── 1. Reading the feed text ────────────────────────────────────────────────
# Each table is ordered: the first key whose phrase appears wins.
_LENGTH = [
    ("yorker", ("yorker",)),
    ("fulltoss", ("full toss", "full-toss", "beamer")),
    ("halfvolley", ("half-volley", "half volley")),
    ("bouncer", ("bouncer", "bumper", "throat")),
    ("backlen", ("back of a length", "back of length", "short of a length")),
    ("good", ("good length", "length ball", "good-length")),
    ("short", ("dug in short", "short and", "short ball", "short,", " short ",
               "short outside", "short on")),
    ("full", ("full and", "full delivery", "full ball", "full outside",
              "full on", "full,", "fuller", "pitched up", "full ")),
]
_LINE = [
    # not a bare " wide ": "pushed wide of point" describes the SHOT, and
    # read as the ball's line it called a ball on the pads "very wide"
    ("wide", ("wide outside off", "way outside off", "very wide",
              "wide of off", "wide of the off", "wide delivery", "too wide")),
    ("outoff", ("outside off", "outside the off", "just outside off")),
    ("offstump", ("off stump", "on off", "top of off")),
    ("middle", ("middle stump", "on middle", "on the stumps", "middle and")),
    ("legstump", ("leg stump", "on leg", "down leg", "leg side", "legside")),
    ("pads", ("on the pads", "into the pads", "on the hips", "pads")),
]
_MOVE = [
    ("away", ("swinging away", "swings away", "angling away", "moving away",
              "nipping away", "nips away", "shaping away", "away from the")),
    ("in", ("swinging back in", "swinging in", "nipping in", "nips in",
            "coming back in", "jagging back", "cutting back in", "angling in",
            "shaping in", "darting in")),
    ("seam", ("seam", "sharp movement", "movement", "nips", "zips", "zipped",
              "skids", "skiddy")),
    ("turn", ("turning", "turns", "spinning", "spins", "turned", "grip",
              "drift")),
]
# What the batter did / what happened. Order matters: strongest first.
_ACT = [
    ("chance", ("dropped by", "dropped at", "dropped the", "dropped it",
                "dropped catch", "catch dropped", "drops the", "put down",
                "misses the catch", "spills", "fumbles", "misfield",
                "mis-field", "overthrow", "overthrows")),
    ("ducks", ("ducks", "evades", "sways", "leaves it alone and ducks",
               "gets under", "bobs")),
    ("beaten", ("beaten", "beats", "play and a miss", "swing and a miss",
                "misses", "missed", "zips past", "past the edge", "past the bat",
                "completely opened up", "no contact", "plays all over")),
    ("edge", ("inside edge", "outside edge", "top edge", "thick edge",
              "thin edge", "edged", "edges", "nick", "takes the edge",
              "leading edge")),
    ("appeal", ("appeal", "lbw", "reviews", "review taken", "drs")),
    ("leave", ("left alone", "lets it go", "let it go", "leaves", "left it",
               "lets it through", "no shot", "watchful leave", "shoulders arms")),
    ("slog", ("slog", "heave", "swings", "big shot", "across the line",
              "hoick", "wild")),
    ("hook", ("hook", "hooks")),
    ("pull", ("pull", "pulls", "pulled")),
    ("sweep", ("sweep", "swept", "reverse")),
    ("cut", ("cut", "cuts", "slash", "upper-cut", "upper cut")),
    ("drive", ("drive", "driven", "drives", "punched", "punch", "lofts",
               "lofted", "straight batted")),
    ("flick", ("flick", "flicked", "clipped", "glance", "glanced", "whipped",
               "worked", "tucked", "nudged", "pushed", "dabbed", "steered",
               "dropped", "turned away")),
    ("dance", ("advancing", "advances", "down the track", "comes down",
               "dances", "charges", "jumps down")),
    ("defend", ("defended", "defends", "blocked", "blocks", "knocks it down",
                "knocked it down", "pushes", "patted", "taps", "solid",
                "forward and", "gets behind", "defence", "defense", "dead bat",
                "gets forward")),
]
_AIR = ("in the air", "mistimed", "skied", "lofted", "top-edge", "top edge",
        "balloons", "hangs")
_FIELDED = ("fielded", "dives", "diving", "dive", "stops", "stopped",
            "cuts it off", "saved", "mid-off", "mid-on", "keeper", "to the keeper",
            "to the bowler", "back to the bowler")
_FIELD_ACT = [
    ("dive", ("dives", "diving", "dive", "stretches", "full stretch")),
    ("direct", ("direct hit", "hits the stumps", "throws down")),
    ("keeper", ("to the keeper", "keeper", "wicketkeeper")),
    ("bowler", ("back to the bowler", "to the bowler", "straight to the bowler",
                "returned")),
]


def _first(table, low):
    for key, phrases in table:
        for ph in phrases:
            if ph in low:
                return key
    return None


def parse_play(detail):
    """Structured reading of one delivery's description."""
    low = " %s " % (detail or "").lower()
    return {
        "length": _first(_LENGTH, low),
        "line": _first(_LINE, low),
        "move": _first(_MOVE, low),
        "act": _first(_ACT, low),
        "air": any(a in low for a in _AIR),
        "field": _first(_FIELD_ACT, low),
        "raw": low,
    }


_DISMISS = re.compile(
    r"\b(?P<how>b|c|st|lbw|run out|hit wicket)\b(?P<rest>[^\[]*)", re.I)


def parse_dismissal(text):
    """How the wicket fell, from "Smaran Ravichandran b Sunil Kumar 135(153)".

    Returns {"how": bowled|caught|lbw|stumped|runout|hitwicket|other,
             "fielder": name or None, "bowler": name or None}
    """
    t = text or ""
    low = t.lower()
    how, fielder, bowler = "other", None, None
    m = re.search(r"\bc\s+(?:&\s*b\s+)?([A-Z][A-Za-z.' \-]+?)\s+b\s+"
                  r"([A-Z][A-Za-z.' \-]+?)\s+\d+\s*\(", t)
    if m:
        how, fielder, bowler = "caught", m.group(1).strip(), m.group(2).strip()
    elif re.search(r"\bst\s+[A-Z]", t):
        m = re.search(r"\bst\s+([A-Z][A-Za-z.' \-]+?)\s+b\s+"
                      r"([A-Z][A-Za-z.' \-]+?)\s+\d+", t)
        how = "stumped"
        if m:
            fielder, bowler = m.group(1).strip(), m.group(2).strip()
    elif re.search(r"\blbw\b", low):
        how = "lbw"
    elif "run out" in low:
        how = "runout"
        m = re.search(r"run out\s*\(([^)]+)\)", t, re.I)
        if m:
            fielder = m.group(1).split("/")[0].strip()
    elif "hit wicket" in low:
        how = "hitwicket"
    elif re.search(r"\sb\s+[A-Z][A-Za-z.' \-]+\s+\d+\s*\(", t) \
            or "bowled" in low or "hits the top" in low or "knocks" in low:
        how = "bowled"
    elif "caught" in low or "catch" in low:
        how = "caught"
    return {"how": how, "fielder": fielder, "bowler": bowler}


# ── 2. Hindi phrase banks ───────────────────────────────────────────────────
LENGTH_HI = {
    "yorker": ["यॉर्कर लेंथ", "सीधा यॉर्कर", "पैरों के पास यॉर्कर"],
    "fulltoss": ["फुल टॉस गेंद", "फुल टॉस, बिना टप्पा खाए"],
    "halfvolley": ["हाफ़ वॉली", "हाफ़ वॉली, खेलने के लिए पूरा न्योता"],
    "bouncer": ["तेज़ उछाल वाली बाउंसर", "बाउंसर, सीने की ऊँचाई पर"],
    "backlen": ["बैक ऑफ़ अ लेंथ गेंद", "थोड़ी पीछे की लेंथ पर", "बैक ऑफ़ लेंथ"],
    "good": ["गुड लेंथ गेंद", "बिल्कुल सटीक लेंथ पर", "सही जगह पर पड़ी गेंद"],
    "short": ["शॉर्ट गेंद", "ज़ोर से पटकी हुई शॉर्ट गेंद", "छोटी लेंथ की गेंद"],
    "full": ["फुल गेंद", "फुल लेंथ पर", "आगे पड़ी हुई गेंद", "पिच अप गेंद"],
}
LINE_HI = {
    "wide": ["काफ़ी वाइड, ऑफ़ स्टंप के बाहर", "बहुत बाहर की लाइन में"],
    "outoff": ["ऑफ़ स्टंप के बाहर", "ऑफ़ के बाहर की लाइन पर",
               "ऑफ़ स्टंप से बाहर"],
    "offstump": ["ऑफ़ स्टंप की लाइन पर", "ठीक ऑफ़ स्टंप पर"],
    "middle": ["सीधे स्टंप्स पर", "मिडिल स्टंप की लाइन में"],
    "legstump": ["लेग स्टंप की तरफ़", "लेग साइड की ओर"],
    "pads": ["पैड्स पर", "पैड्स की लाइन में"],
}
MOVE_HI = {
    "away": ["बाहर की ओर स्विंग होती हुई", "बल्लेबाज़ से दूर जाती हुई",
             "बाहर निकलती हुई"],
    "in": ["अंदर की तरफ़ आती हुई", "अंदर घूमती हुई", "वापस अंदर आती हुई"],
    "seam": ["सीम से हिलती हुई", "पिच से तेज़ निकलती हुई"],
    "turn": ["टर्न होती हुई", "घूमती हुई"],
}

# What the batter did. Read after a subject ("{bat} ने", "इन्होंने"), so every
# entry is a verb phrase. Shapes from the stream: "बैकफुट पर जाते हुए इस गेंद
# को कट" (x10), "फ्लिक शॉट के रूप में" (x9), "सामने स्ट्रेट पर" (x12).
ACT_DID = {
    "leave": ["गेंद को जाने दिया", "इसे छोड़ दिया, कोई शॉट नहीं",
              "बल्ला उठाकर गेंद जाने दी"],
    "defend": ["आगे आकर सीधे बल्ले से रोक दिया", "गेंद को नीचे दबा दिया",
               "डिफ़ेंस किया, कोई रिस्क नहीं", "बल्ला आगे रखकर गेंद रोक दी"],
    "drive": ["इस गेंद को ड्राइव किया", "खुलकर ड्राइव किया",
              "सामने स्ट्रेट पर ड्राइव खेला", "पूरे बल्ले से ड्राइव किया"],
    "pull": ["पुल शॉट खेला", "गेंद को खींचकर मारा",
             "बैकफुट पर जाकर पुल किया"],
    "hook": ["हुक शॉट खेला", "गेंद को पीछे की ओर हुक किया"],
    "cut": ["कट शॉट के रूप में खेला", "बैकफुट पर जाते हुए इस गेंद को कट किया",
            "जगह बनाकर कट मारा"],
    "sweep": ["स्वीप शॉट के रूप में खेला", "घुटना टेककर स्वीप किया"],
    "flick": ["कलाई घुमाकर फ्लिक किया", "फ्लिक शॉट के रूप में खेला",
              "गेंद को हल्के से गैप में धकेला"],
    "dance": ["क्रीज़ से बाहर निकलकर खेलने की कोशिश की",
              "आगे निकलकर गेंद का सामना किया"],
    "slog": ["पूरी ताक़त से बल्ला घुमाया", "बड़ा शॉट खेलने के लिए बल्ला घुमाया"],
    "ducks": ["सिर नीचे करके गेंद निकल जाने दी", "झुककर बाउंसर से बचाव किया"],
    "edge": ["गेंद बल्ले का किनारा लेकर निकली"],
    "beaten": ["गेंद बल्ले को छुए बिना निकल गई"],
}

# The result, spoken before anything else when the ball was close.
BEATEN_REACT = [
    "ओहो! कितनी क़रीब से निकली!", "अरे अरे! बल्ले के बिल्कुल पास से!",
    "आह! बाल बाल बचे!", "बीट! पूरी तरह चकमा दे दिया!",
    "क्या गेंद है भाई! बल्लेबाज़ के पास कोई जवाब नहीं!",
]
EDGE_REACT = [
    "किनारा लगा! अरे ये तो बाल बाल बचे!", "ओहो! बल्ले का किनारा!",
    "नीक लगी! सबकी साँसें रुक गईं!",
]
CHANCE_REACT = [
    "अरे! कैच छूट गया! बड़ा मौका गँवा दिया!",
    "ओहो! ये तो हाथ से निकल गया, कैच था भाई!",
    "मिसफ़ील्ड! फ़ील्डर से गड़बड़ हो गई!",
]
AIR_REACT = [
    "गेंद हवा में! सबकी साँसें अटक गईं!",
    "हवा में है गेंद! अब क्या होगा?",
    "ओहो! हवा में गई, पर कोई फ़ील्डर नहीं पहुँचा!",
]
APPEAL_REACT = [
    "ज़ोरदार अपील! पूरी टीम अंपायर की तरफ़ देख रही है!",
    "अपील हुई है, अब अंपायर के फ़ैसले का इंतज़ार",
]

# More reactions with the sounds people make - "उफ़्फ़", "अरे", "बस थोड़ा सा".
BEATEN_REACT += [
    "उफ़्फ़! क्या गेंद थी, बल्ला बस थोड़ा सा दूर रह गया!",
    "अरे अरे अरे! ये तो बाल बाल बचे!",
    "आह! बल्लेबाज़ को पता भी नहीं चला गेंद कहाँ गई!",
]
EDGE_REACT += [
    "ओह्ह! किनारा! पर फील्डर तक नहीं पहुँची!",
    "उफ़्फ़! मोटा किनारा, और क़िस्मत साथ थी!",
]
CHANCE_REACT += [
    "ये तो आसान कैच था भाई! कैसे छूट गया!",
    "हाय! हाथ में आई और निकल गई!",
]
AIR_REACT += [
    "बस थोड़ा सा! थोड़ा सा और होता तो कैच था!",
    "अरे! हवा में... पर सुरक्षित, फील्डर से पहले गिर गई!",
]
APPEAL_REACT += [
    "हाउज़ दैट! ज़ोरदार अपील! अंपायर सोच रहे हैं...",
]

# Reading of the ball - hedged, never a fact the feed didn't give.
READ = {
    "leave": [
        "शायद बल्लेबाज़ को पता था कि इस लाइन पर खेलने का कोई फ़ायदा नहीं",
        "अच्छा फ़ैसला, ऐसी गेंदों को छोड़ना भी एक हुनर है",
        "लगता है बल्लेबाज़ बॉलर के जाल में नहीं फँसना चाहते",
    ],
    "defend": [
        "कोई रिस्क नहीं, और गेंद थी भी अच्छी",
        "अच्छी गेंद को इज़्ज़त देना ही सही था",
        "लगता है बल्लेबाज़ पहले नज़र जमाना चाहते हैं",
    ],
    "beaten": [
        "बॉलर ने बाहर की गेंद पर ललचाया, और बल्लेबाज़ लगभग फँस ही गए थे",
        "बस दो इंच का फ़र्क़, और विकेट गिर सकता था",
        "इस गेंद के बाद बल्लेबाज़ का भरोसा थोड़ा तो हिला होगा",
        "बॉलर यही चाहते हैं, बल्लेबाज़ को अपनी लाइन पर खेलने पर मजबूर करना",
    ],
    "edge": [
        "थोड़ा सा मोटा किनारा होता तो कैच पक्का था",
        "क़िस्मत ने साथ दिया यहाँ बल्लेबाज़ का",
    ],
    "ducks": [
        "गेंद पर पूरी नज़र रखी, इसीलिए आराम से बच गए",
        "बॉलर शॉर्ट गेंद से बल्लेबाज़ को परेशान करना चाहते हैं",
    ],
    "loose": [
        "ऐसी ढीली गेंद को कौन छोड़ता है भाई",
        "बॉलर ने लेंथ ग़लत कर दी, और सज़ा मिल गई",
        "खेलने लायक गेंद मिली, और बल्लेबाज़ ने कोई ग़लती नहीं की",
    ],
    "good_ball_boundary": [
        "गेंद अच्छी थी, पर शॉट उससे भी अच्छा था",
        "ये क्लास है, अच्छी गेंद को भी बाउंड्री बना दिया",
    ],
    "single": [
        "बिना रिस्क का सिंगल, यही समझदारी है",
        "दोनों में अच्छी समझ दिख रही है, स्ट्राइक घूमती रहे",
        "ऐसे सिंगल निकलते रहें तो बॉलर का प्लान बिगड़ता है",
    ],
    "double": [
        "बढ़िया रनिंग, फ़ील्डर को ख़ूब दौड़ा दिया",
        "दो रन लेने की हिम्मत दिखाई, ये फ़िटनेस की बात है",
    ],
    "pressure_dots": [
        "लगातार {n} डॉट बॉल, बल्लेबाज़ पर प्रेशर बढ़ रहा है",
        "{n} गेंदों से कोई रन नहीं, अब कुछ करना पड़ेगा",
        "लगातार {n} डॉट, बॉलर ने शिकंजा कस दिया है",
    ],
    "pressure_broken": [
        "आख़िरकार प्रेशर टूटा, {n} डॉट के बाद रन मिला",
        "{n} गेंदों के बाद खाता खुला, राहत की साँस",
    ],
    "nervous": [
        "नर्वस नाइंटीज़ चल रहे हैं, यहाँ धड़कनें तेज़ हो जाती हैं",
        "शतक बस कुछ ही रन दूर, अब हर गेंद पर प्रेशर है",
    ],
    "fifty_close": [
        "फिफ्टी अब बहुत क़रीब है",
        "पचास के क़रीब पहुँच रहे हैं, देखते हैं",
    ],
    "wicket_pressure": [
        "बॉलर ने काफ़ी देर से प्रेशर बना रखा था, उसी का फल मिला",
        "यही तो बॉलिंग की ख़ूबसूरती है, पहले प्रेशर, फिर विकेट",
        "मैंने कहा था ना, डॉट बॉल्स का प्रेशर विकेट लेकर आता है",
    ],
}

# Conversational glue: what a commentator says between ideas. Kept free of
# opinion markers ("लगता है") because the READ lines already hedge.
DISCOURSE = [
    "देखिए", "दोस्तों", "भाई", "सो", "सच बताऊँ तो", "एक बात ग़ौर कीजिए",
    "और यही तो क्रिकेट की ख़ूबसूरती है", "अब यहाँ देखिए", "बिल्कुल",
    "बात ये है कि",
]
TAG = ["है ना?", "सही बात है ना?", "क्यों दोस्तों?", "क्या कहते हैं आप?"]

# Dismissals, by how the wicket fell. {fielder} {bowler} {bat}
HOW_OUT = {
    "bowled": [
        "बोल्ड! गिल्लियाँ बिखर गईं! {bowler} ने स्टंप्स उड़ा दिए!",
        "बोल्ड! बल्लेबाज़ गेंद को समझ ही नहीं पाए!",
        "सीधा स्टंप्स पर! {bat} की पारी ख़त्म!",
    ],
    "caught": [
        "{fielder} ने कैच लपक लिया! कोई ग़लती नहीं!",
        "कैच! और {fielder} के हाथों में गेंद सुरक्षित!",
        "हवा में गई गेंद, और {fielder} ने थाम ली!",
    ],
    "caught_nofield": [
        "कैच! गेंद फ़ील्डर के हाथों में सुरक्षित!",
        "हवा में थी गेंद, और सीधे हाथों में!",
    ],
    "lbw": [
        "पैड पर लगी! अंपायर की उंगली ऊपर! एल बी डब्ल्यू!",
        "एल बी डब्ल्यू! सामने पकड़े गए, अंपायर ने आउट दे दिया!",
    ],
    "stumped": [
        "स्टंप्ड! {fielder} ने पलक झपकते ही बेल्स उड़ा दीं!",
        "क्रीज़ से बाहर थे, और कीपर ने स्टंप कर दिया!",
    ],
    "runout": [
        "रन आउट! दोनों में तालमेल की कमी, और विकेट गया!",
        "रन आउट! सीधे थ्रो ने काम तमाम कर दिया!",
    ],
    "hitwicket": [
        "हिट विकेट! बल्लेबाज़ ने ख़ुद ही स्टंप्स गिरा दिए!",
    ],
    "other": [
        "और बल्लेबाज़ को लौटना पड़ेगा, ये आउट है!",
    ],
}
WICKET_THOUGHT = [
    "बल्लेबाज़ बहुत निराश होंगे, सेट हो चुके थे",
    "इस विकेट से पूरे मैच की तस्वीर बदल सकती है",
    "बॉलर के लिए ये बहुत बड़ा पल है",
    "बल्लेबाज़ ख़ुद यक़ीन नहीं कर पा रहे होंगे",
]

# Fielder / result of a run-scoring ball, spoken as a story.
FIELD_STORY = {
    "dive": ["फ़ील्डर ने डाइव लगाई", "फ़ील्डर ने डाइव मारकर रोकने की कोशिश की"],
    "direct": ["सीधा थ्रो स्टंप्स की ओर!", "थ्रो आया, और बाल बाल बचे बल्लेबाज़"],
    "keeper": ["गेंद सीधे कीपर के दस्तानों में"],
    "bowler": ["गेंद सीधे बॉलर के पास लौट आई"],
}

# Emotions beyond the original five. Defined in voice.EMOTION.
#   tense  - a close call: slower, hushed, held breath
#   amazed - disbelief at a brilliant shot or ball
TENSE, AMAZED = "tense", "amazed"


# ── 3. Building the lines ───────────────────────────────────────────────────
def describe_ball(play, pick):
    """'फुल गेंद, ऑफ़ के बाहर, बाहर की ओर स्विंग होती हुई', or ''."""
    parts = []
    if play["length"]:
        parts.append(pick(LENGTH_HI[play["length"]]))
    if play["line"]:
        parts.append(pick(LINE_HI[play["line"]]))
    if play["move"]:
        parts.append(pick(MOVE_HI[play["move"]]))
    return ", ".join(parts)


def batter_did(play, pick, bat=None):
    """'{bat} ने ...' clause, or ''. The subject varies, because the same
    "बल्लेबाज़ ने" on every ball is what makes a template sound like one."""
    act = play["act"]
    if act not in ACT_DID:
        return ""
    did = pick(ACT_DID[act])
    if act in ("edge", "beaten"):
        return did
    r = random.random()
    if bat and r < 0.5:
        subj = "%s ने" % bat
    elif r < 0.7:
        subj = "इन्होंने"
    else:
        subj = "बल्लेबाज़ ने"
    return "%s %s" % (subj, did)


def loose_ball(play):
    """Was the bowler's ball asking to be hit?"""
    return (play["length"] in ("halfvolley", "fulltoss", "short")
            or play["line"] in ("legstump", "pads", "wide"))


def with_glue(text, pick, chance=0.5):
    """Prefix a conversational marker, sometimes end on a tag question."""
    if not text or random.random() > chance:
        return text
    out = "%s, %s" % (pick(DISCOURSE), text)
    if random.random() < 0.18:
        out += ", " + pick(TAG)
    return out
