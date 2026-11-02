# -*- coding: utf-8 -*-
"""
translit.py  -  make the Hindi voice say English names correctly.

The feed hands us Latin text ("Bumrah to Babar Azam", "Nathan Coulter-Nile").
Piper's Hindi voice runs that through the Hindi phonemizer, which mangles it -
that was the single biggest pronunciation problem. Everything spoken is
therefore converted to Devanagari first.

Two layers:

  NAME_HI    a curated spelling for players and teams that actually turn up in
             these feeds. Always prefer this - English spelling is not
             phonetic, so no rule set gets "Laughlin" or "Coulter-Nile" right.

  translit() an ordered-digraph fallback for anyone not in the map. Imperfect
             by nature, but far closer than letting the Hindi phonemizer read
             Latin letters.

Use say_text() on anything heading for the voice: it rewrites Latin runs inside
an otherwise-Hindi sentence and leaves the Devanagari alone.
"""
import re

# ── Curated: first names, surnames and single-word tokens ───────────────────
# Keys are lowercase. Multi-word entries are matched before single words.
NAME_HI = {
    # India
    "rohit": "रोहित", "sharma": "शर्मा", "virat": "विराट", "kohli": "कोहली",
    "bumrah": "बुमराह", "jasprit": "जसप्रीत", "shubman": "शुभमन",
    "gill": "गिल", "rahul": "राहुल", "kl": "के एल", "pant": "पंत",
    "rishabh": "ऋषभ", "hardik": "हार्दिक", "pandya": "पांड्या",
    "jadeja": "जडेजा", "ravindra": "रविंद्र", "ashwin": "अश्विन",
    "axar": "अक्षर", "patel": "पटेल", "kuldeep": "कुलदीप",
    "yadav": "यादव", "suryakumar": "सूर्यकुमार", "shreyas": "श्रेयस",
    "iyer": "अय्यर", "tilak": "तिलक", "varma": "वर्मा",
    "siraj": "सिराज", "mohammed": "मोहम्मद", "shami": "शमी",
    "arshdeep": "अर्शदीप", "singh": "सिंह", "nitish": "नितीश",
    "reddy": "रेड्डी", "sanju": "संजू", "samson": "सैमसन",
    "yashasvi": "यशस्वी", "jaiswal": "जायसवाल", "dhruv": "ध्रुव",
    "jurel": "जुरेल", "washington": "वॉशिंगटन", "sundar": "सुंदर",
    "prasidh": "प्रसिद्ध", "krishna": "कृष्णा", "naman": "नमन",
    "ojha": "ओझा", "yuvraj": "युवराज", "yusuf": "यूसुफ",
    "pathan": "पठान", "dhoni": "धोनी", "raina": "रैना",
    "sehwag": "सहवाग", "gambhir": "गंभीर", "harbhajan": "हरभजन",
    "zaheer": "ज़हीर", "irfan": "इरफ़ान", "binny": "बिन्नी",
    "dhir": "धीर", "ishan": "ईशान", "kishan": "किशन",
    "ruturaj": "ऋतुराज", "gaikwad": "गायकवाड़", "abhishek": "अभिषेक",
    "rinku": "रिंकू", "jitesh": "जितेश", "mukesh": "मुकेश",
    "smaran": "स्मरण", "shubham": "शुभम", "sai": "साई",
    # Pakistan
    "babar": "बाबर", "azam": "आज़म", "rizwan": "रिज़वान",
    "shaheen": "शाहीन", "afridi": "अफ़रीदी", "haris": "हारिस",
    "rauf": "रऊफ़", "naseem": "नसीम", "shah": "शाह",
    "fakhar": "फ़खर", "zaman": "ज़मान", "iftikhar": "इफ़्तिख़ार",
    "ahmed": "अहमद", "shadab": "शादाब", "khan": "ख़ान",
    "nawaz": "नवाज़", "imad": "इमाद", "wasim": "वसीम",
    "saim": "साइम", "ayub": "अयूब", "abrar": "अबरार",
    "salman": "सलमान", "agha": "आगा", "usman": "उस्मान",
    "shan": "शान", "masood": "मसूद", "sarfaraz": "सरफ़राज़",
    "hasan": "हसन", "ali": "अली", "asif": "आसिफ़",
    # Australia
    "cummins": "कमिंस", "pat": "पैट", "smith": "स्मिथ",
    "steve": "स्टीव", "steven": "स्टीवन", "warner": "वॉर्नर",
    "david": "डेविड", "head": "हेड", "travis": "ट्रैविस",
    "maxwell": "मैक्सवेल", "glenn": "ग्लेन", "marsh": "मार्श",
    "mitchell": "मिचेल", "starc": "स्टार्क", "hazlewood": "हेज़लवुड",
    "josh": "जॉश", "zampa": "ज़ाम्पा", "adam": "एडम",
    "labuschagne": "लाबुशेन", "marnus": "मार्नस", "carey": "कैरी",
    "alex": "एलेक्स", "inglis": "इंग्लिस", "stoinis": "स्टोइनिस",
    "green": "ग्रीन", "cameron": "कैमरन", "lyon": "लायन",
    "nathan": "नेथन", "coulter": "कोल्टर", "nile": "नाइल",
    "laughlin": "लॉफ़लिन", "ben": "बेन", "richardson": "रिचर्डसन",
    "hilfenhaus": "हिल्फ़नहॉस", "finch": "फ़िंच", "aaron": "एरन",
    "watson": "वॉटसन", "shane": "शेन", "hussey": "हसी",
    # England
    "root": "रूट", "joe": "जो", "stokes": "स्टोक्स",
    "bairstow": "बेयरस्टो", "jonny": "जॉनी", "buttler": "बटलर",
    "jos": "जॉस", "archer": "आर्चर", "jofra": "जोफ़्रा",
    "wood": "वुड", "mark": "मार्क", "rashid": "राशिद",
    "bethell": "बेथेल", "brook": "ब्रूक", "harry": "हैरी",
    "duckett": "डकेट", "crawley": "क्रॉली", "anderson": "एंडरसन",
    "broad": "ब्रॉड", "curran": "करन", "livingstone": "लिविंगस्टन",
    "salt": "सॉल्ट", "phil": "फ़िल", "atkinson": "एटकिंसन",
    # West Indies
    "pooran": "पूरन", "nicholas": "निकोलस", "hetmyer": "हेटमायर",
    "shimron": "शिमरॉन", "russell": "रसेल", "andre": "आंद्रे",
    "narine": "नरेन", "sunil": "सुनील", "holder": "होल्डर",
    "jason": "जेसन", "hope": "होप", "shai": "शाई",
    "chase": "चेस", "roston": "रोस्टन", "motie": "मोती",
    "gudakesh": "गुडाकेश", "alzarri": "अल्ज़ारी", "joseph": "जोसेफ़",
    "shepherd": "शेफ़र्ड", "romario": "रोमारियो", "king": "किंग",
    "brandon": "ब्रैंडन", "jewel": "ज्वेल", "andrew": "एंड्रू",
    "rutherford": "रदरफ़र्ड", "sherfane": "शेरफ़ेन",
    "gayle": "गेल", "chris": "क्रिस", "bravo": "ब्रावो",
    # South Africa
    "markram": "मार्कराम", "aiden": "एडन", "klaasen": "क्लासेन",
    "heinrich": "हेनरिक", "miller": "मिलर", "rabada": "रबाडा",
    "kagiso": "कगिसो", "ngidi": "न्गिडी", "jansen": "यानसन",
    "bavuma": "बावुमा", "temba": "टेम्बा", "quinton": "क्विंटन",
    "kock": "कॉक", "maharaj": "महाराज", "keshav": "केशव",
    "stubbs": "स्टब्स", "breetzke": "ब्रीट्ज़के", "amla": "अमला",
    # New Zealand
    "williamson": "विलियमसन", "kane": "केन", "conway": "कॉनवे",
    "devon": "डेवन", "mitchell": "मिचेल", "daryl": "डेरिल",
    "boult": "बोल्ट", "trent": "ट्रेंट", "southee": "साउदी",
    "santner": "सैंटनर", "phillips": "फ़िलिप्स", "ravindra": "रविंद्र",
    "rachin": "रचिन", "latham": "लैथम", "tom": "टॉम",
    "henry": "हेनरी", "matt": "मैट", "ferguson": "फ़र्गुसन",
    # Sri Lanka / Bangladesh / Afghanistan
    "kusal": "कुसल", "mendis": "मेंडिस", "nissanka": "निसंका",
    "pathum": "पथुम", "hasaranga": "हसरंगा", "wanindu": "वनिंदु",
    "theekshana": "थीक्षणा", "asalanka": "असलंका", "charith": "चरित",
    "shakib": "शाकिब", "mushfiqur": "मुश्फ़िकुर", "rahim": "रहीम",
    "mustafizur": "मुस्तफ़िज़ुर", "rahman": "रहमान",
    "litton": "लिटन", "das": "दास", "taskin": "तास्किन",
    "mehidy": "मेहदी", "hasan": "हसन", "najmul": "नजमुल",
    "shanto": "शांतो", "rashid": "राशिद", "mujeeb": "मुजीब",
    "nabi": "नबी", "gurbaz": "गुरबाज़", "ibrahim": "इब्राहिम",
    "zadran": "ज़दरान", "omarzai": "उमरज़ई", "fazalhaq": "फ़ज़लहक़",
    "farooqi": "फ़ारूक़ी", "naveen": "नवीन",
    # common initials / particles
    "a": "ए", "b": "बी", "c": "सी", "d": "डी", "e": "ई", "f": "एफ़",
    "g": "जी", "h": "एच", "i": "आई", "j": "जे", "k": "के", "l": "एल",
    "m": "एम", "n": "एन", "o": "ओ", "p": "पी", "q": "क्यू", "r": "आर",
    "s": "एस", "t": "टी", "u": "यू", "v": "वी", "w": "डब्ल्यू",
    "x": "एक्स", "y": "वाई", "z": "ज़ेड",
    "de": "डे", "van": "वान", "der": "डर", "du": "डू", "jr": "जूनियर",
    "kumar": "कुमार", "raj": "राज", "prasad": "प्रसाद",
    # rule-fallback misses, corrected by hand
    "zimmerman": "ज़िमरमैन", "bracewell": "ब्रेसवेल",
    "hartley": "हार्टली", "verreynne": "वेरेन",
    "mccullum": "मैककलम", "mcsweeney": "मैकस्वीनी",
    "chakravarthy": "चक्रवर्ती", "pretorius": "प्रिटोरियस",
    "brevis": "ब्रेविस", "ferreira": "फ़रेरा", "coetzee": "कुट्ज़े",
    "chawla": "चावला", "uthappa": "उथप्पा", "tiwary": "तिवारी",
    "dhawan": "धवन", "rishi": "ऋषि", "powar": "पोवार",
    "karthik": "कार्तिक", "nayar": "नायर", "mithun": "मिथुन",
    "vinay": "विनय", "munaf": "मुनाफ़", "nehra": "नेहरा",
    "symonds": "सायमंड्स", "haddin": "हैडिन", "tait": "टेट",
    "bollinger": "बॉलिंजर", "voges": "वोजेस", "hodge": "हॉज",
    "hopes": "होप्स", "gilchrist": "गिलक्रिस्ट", "ponting": "पोंटिंग",
    "clarke": "क्लार्क", "katich": "कैटिच", "lee": "ली",
    "collingwood": "कॉलिंगवुड", "trescothick": "ट्रेस्कोथिक",
    "sangakkara": "संगकारा", "jayawardene": "जयवर्धने",
    "jayasuriya": "जयसूर्या", "vaas": "वास", "murali": "मुरली",
    "muralitharan": "मुरलीधरन", "dilshan": "दिलशान",
    "afridi": "अफ़रीदी", "razzaq": "रज़्ज़ाक", "akhtar": "अख़्तर",
    "misbah": "मिसबाह", "younis": "यूनिस", "inzamam": "इंज़माम",
    "sarwan": "सरवन", "chanderpaul": "चंद्रपॉल", "samuels": "सैमुअल्स",
    "taylor": "टेलर", "powell": "पॉवेल", "simmons": "सिमंस",
    # team / country words, so "India Champions" isn't spelled out
    "india": "भारत", "indian": "भारतीय", "pakistan": "पाकिस्तान",
    "australia": "ऑस्ट्रेलिया", "england": "इंग्लैंड",
    "west": "वेस्ट", "indies": "इंडीज", "africa": "अफ्रीका",
    "south": "साउथ", "north": "नॉर्थ", "new": "न्यू",
    "zealand": "ज़ीलैंड", "lanka": "लंका", "sri": "श्री",
    "bangladesh": "बांग्लादेश", "afghanistan": "अफ़ग़ानिस्तान",
    "zimbabwe": "ज़िम्बाब्वे", "ireland": "आयरलैंड",
    "scotland": "स्कॉटलैंड", "netherlands": "नेदरलैंड्स",
    "namibia": "नामीबिया", "nepal": "नेपाल", "oman": "ओमान",
    "emirates": "अमीरात", "champions": "चैंपियंस",
    "legends": "लेजेंड्स", "women": "महिला", "xi": "इलेवन",
    "jammu": "जम्मू", "kashmir": "कश्मीर", "rest": "रेस्ट", "of": "ऑफ़",
    "and": "और", "vs": "बनाम", "inv": "इनवाइटेशन", "csa": "सी एस ए",
}

# Cricket vocabulary that leaks out of the feed in English
WORD_HI = {
    "unofficial": "अनऑफ़िशियल", "under": "अंडर", "women": "महिला",
    "out": "आउट", "caught": "कैच", "bowled": "बोल्ड", "lbw": "एल बी डब्ल्यू",
    "stumped": "स्टंप्ड", "run": "रन", "runs": "रन", "wide": "वाइड",
    "four": "चौका", "six": "छक्का", "no": "नो", "ball": "बॉल",
    "wicket": "विकेट", "over": "ओवर", "maiden": "मेडन",
    "timeout": "टाइम आउट", "time": "टाइम", "drinks": "ड्रिंक्स",
    "innings": "इनिंग्स", "break": "ब्रेक", "stumps": "स्टंप्स",
    "crease": "क्रीज़", "bat": "बैट", "right": "राइट", "left": "लेफ़्ट",
    "handed": "हैंडेड", "comes": "आ रहे हैं", "to": "", "the": "",
    "review": "रिव्यू", "appeal": "अपील", "umpire": "अंपायर",
    "retired": "रिटायर्ड", "hurt": "हर्ट", "powerplay": "पावरप्ले",
}

# ── Rule-based fallback ─────────────────────────────────────────────────────
# Ordered longest-first; each entry is (latin, consonant-or-vowel, devanagari).
_CONS = [
    ("tth", "थ"), ("tch", "च"), ("sch", "श"), ("sh", "श"), ("ch", "च"), ("th", "थ"),
    ("ph", "फ़"), ("kh", "ख"), ("gh", "घ"), ("bh", "भ"), ("dh", "ध"),
    ("jh", "झ"), ("ck", "क"), ("ll", "ल"), ("tt", "ट"), ("nn", "न"),
    ("mm", "म"), ("ss", "स"), ("ff", "फ़"), ("pp", "प"), ("bb", "ब"),
    ("gg", "ग"), ("dd", "ड"), ("rr", "र"), ("wh", "व"), ("qu", "क्व"),
    ("ng", "ंग"), ("gn", "न"), ("ps", "प्स"), ("x", "क्स"),
    ("k", "क"), ("g", "ग"), ("j", "ज"), ("t", "ट"), ("d", "ड"),
    ("n", "न"), ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"),
    ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"),
    ("h", "ह"), ("z", "ज़"), ("f", "फ़"), ("q", "क"), ("c", "क"),
]
# (latin, independent, matra)
_VOWS = [
    ("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"),
    ("ay", "ए", "े"), ("ee", "ई", "ी"), ("ea", "ी", "ी"),
    ("ie", "ी", "ी"), ("oo", "ऊ", "ू"), ("ou", "औ", "ौ"),
    ("ow", "ओ", "ो"), ("oa", "ओ", "ो"), ("oi", "ऑय", "ॉय"),
    ("ey", "े", "े"), ("eu", "यू", "्यू"), ("ew", "यू", "्यू"),
    ("a", "अ", "ा"), ("e", "ए", "े"), ("i", "इ", "ि"),
    ("o", "ओ", "ो"), ("u", "उ", "ु"),
]
_VIRAMA = "्"


def translit(word):
    """Latin word -> approximate Devanagari. Used only when NAME_HI misses.

    English spelling is not phonetic, so this is an approximation. It handles
    the patterns that actually recur in cricket surnames; anything important
    belongs in NAME_HI instead.
    """
    w = re.sub(r"[^A-Za-z]", "", word).lower()
    if not w:
        return ""

    pre = ""
    if w.startswith("mc") and len(w) > 2:
        pre, w = "मैक", w[2:]              # McSweeney, McCullum
    elif w.startswith("mac") and len(w) > 3:
        pre, w = "मैक", w[3:]

    # whole-ending rewrites, longest first
    for suf, dev in (("tion", "शन"), ("ious", "ियस"), ("ius", "ियस"),
                     ("eigh", "े"), ("ough", "ो")):
        if w.endswith(suf) and len(w) > len(suf):
            return pre + translit(w[:-len(suf)]) + dev

    # a final -y after a consonant is a vowel (Chakravarthy -> ...र्ती)
    final_y = ""
    if len(w) > 2 and w.endswith("y") and w[-2] not in "aeiou":
        final_y, w = "ी", w[:-1]

    out = []
    i = 0
    prev_was_cons = False
    while i < len(w):
        for lat, dev in _CONS:
            if w.startswith(lat, i):
                if prev_was_cons:
                    out.append(_VIRAMA)
                out.append(dev)
                i += len(lat)
                prev_was_cons = True
                break
        else:
            for lat, indep, matra in _VOWS:
                if w.startswith(lat, i):
                    # 'a' after a consonant is the inherent vowel - write nothing
                    if prev_was_cons:
                        if lat != "a":
                            out.append(matra)
                    else:
                        out.append(indep)
                    i += len(lat)
                    prev_was_cons = False
                    break
            else:
                i += 1
    # a trailing bare consonant is normal for English names in Hindi (स्मिथ)
    return pre + "".join(out) + final_y


# ── Indian-style names ──────────────────────────────────────────────────────
# English spelling rules mangle South Asian names ("Thakur" -> थकुर, "Arshad"
# -> अर्शड, "Suryansh" -> सुर्यन्श): those names are romanized from Hindi /
# Urdu / Bengali, where "t" and "d" are dental, a final "a" is long, "sh" is
# श and "n" before a consonant is a nasal. Two layers:
#   INDIC_NAME_HI  curated spellings of common name parts (always used)
#   indic()        romanization rules, used for players of South Asian teams
#                  (registered from the live feed by mark_indic)
INDIC_NAME_HI = {
    "thakur": "ठाकुर", "roy": "रॉय", "iyer": "अय्यर", "yadav": "यादव",
    "sharma": "शर्मा", "singh": "सिंह", "kumar": "कुमार", "khan": "ख़ान",
    "ahmed": "अहमद", "ahmad": "अहमद", "mohammad": "मोहम्मद",
    "mohammed": "मोहम्मद", "muhammad": "मुहम्मद", "mohamed": "मोहम्मद",
    "ali": "अली", "hussain": "हुसैन", "hossain": "हुसैन", "shah": "शाह",
    "patel": "पटेल", "pandya": "पंड्या", "pant": "पंत", "kohli": "कोहली",
    "gill": "गिल", "jaiswal": "जायसवाल", "rahul": "राहुल", "rohit": "रोहित",
    "virat": "विराट", "jasprit": "जसप्रीत", "bumrah": "बुमराह",
    "siraj": "सिराज", "shami": "शमी", "ashwin": "अश्विन", "jadeja": "जडेजा",
    "axar": "अक्षर", "kuldeep": "कुलदीप", "chahal": "चहल",
    "hardik": "हार्दिक", "rinku": "रिंकू", "tilak": "तिलक", "varma": "वर्मा",
    "samson": "सैमसन", "sanju": "संजू", "suryakumar": "सूर्यकुमार",
    "ishan": "ईशान", "kishan": "किशन", "abhishek": "अभिषेक",
    "shivam": "शिवम", "dube": "दुबे", "arshdeep": "अर्शदीप",
    "arshad": "अरशद", "nishant": "निशांत", "suryansh": "सूर्यांश",
    "shedge": "शेडगे", "vipraj": "विपराज", "nigam": "निगम",
    "anukul": "अनुकूल", "sai": "साई", "sudharsan": "सुदर्शन",
    "sudarshan": "सुदर्शन", "yash": "यश", "dayal": "दयाल",
    "harshit": "हर्षित", "rana": "राणा", "prasidh": "प्रसिद्ध",
    "krishna": "कृष्णा", "mukesh": "मुकेश", "avesh": "आवेश",
    "umran": "उमरान", "malik": "मलिक", "deepak": "दीपक", "chahar": "चाहर",
    "washington": "वॉशिंगटन", "sundar": "सुंदर", "ravi": "रवि",
    "bishnoi": "बिश्नोई", "ruturaj": "ऋतुराज", "gaikwad": "गायकवाड़",
    "prithvi": "पृथ्वी", "shaw": "शॉ", "mayank": "मयंक",
    "agarwal": "अग्रवाल", "rajat": "रजत", "patidar": "पाटीदार",
    "dhruv": "ध्रुव", "jurel": "जुरेल", "sarfaraz": "सरफ़राज़",
    "devdutt": "देवदत्त", "padikkal": "पडिक्कल", "karun": "करुण",
    "nair": "नायर", "riyan": "रियान", "parag": "पराग", "nitish": "नीतीश",
    "reddy": "रेड्डी", "tushar": "तुषार", "deshpande": "देशपांडे",
    "khaleel": "ख़लील", "babar": "बाबर", "azam": "आज़म",
    "rizwan": "रिज़वान", "fakhar": "फ़ख़र", "zaman": "ज़मान",
    "shaheen": "शाहीन", "afridi": "अफ़रीदी", "naseem": "नसीम",
    "haris": "हारिस", "rauf": "रऊफ़", "imam": "इमाम", "shadab": "शादाब",
    "saim": "सईम", "ayub": "अयूब", "salman": "सलमान", "agha": "आग़ा",
    "abrar": "अबरार", "sufiyan": "सुफ़ियान", "muqeem": "मुक़ीम",
    "sahibzada": "साहिबज़ादा", "farhan": "फ़रहान", "hasan": "हसन",
    "nawaz": "नवाज़", "usman": "उस्मान", "shakib": "शाकिब",
    "mushfiqur": "मुशफ़िकुर", "rahim": "रहीम", "litton": "लिटन",
    "das": "दास", "najmul": "नजमुल", "shanto": "शान्तो",
    "mustafizur": "मुस्तफ़िज़ुर", "rahman": "रहमान", "taskin": "तस्कीन",
    "mehidy": "मेहदी", "rashid": "राशिद", "nabi": "नबी",
    "gurbaz": "गुरबाज़", "ibrahim": "इब्राहिम", "zadran": "ज़दरान",
    "naveen": "नवीन", "haq": "हक़", "farooqi": "फ़ारूक़ी",
    "mujeeb": "मुजीब", "wanindu": "वानिंदु", "hasaranga": "हसरंगा",
    "kusal": "कुसल", "mendis": "मेंडिस", "perera": "परेरा",
    "pathum": "पथुम", "nissanka": "निसांका", "charith": "चरित",
    "asalanka": "असलंका", "maheesh": "महीश", "theekshana": "तीक्षणा",
    "dasun": "दसुन", "shanaka": "शनाका", "matheesha": "मथीशा",
    "pathirana": "पथिराना", "dunith": "दुनिथ", "wellalage": "वेल्लालागे",
    "dhananjaya": "धनंजय", "silva": "सिल्वा", "sandeep": "संदीप",
    "lamichhane": "लामिछाने", "paudel": "पौडेल", "dipendra": "दीपेंद्र",
    "airee": "ऐरी", "aarif": "आरिफ़", "sheikh": "शेख़", "kushal": "कुशल",
    "bhurtel": "भुर्तेल", "malla": "मल्ल", "gupta": "गुप्ता",
    "mishra": "मिश्रा", "tiwari": "तिवारी", "chaudhary": "चौधरी",
    "choudhary": "चौधरी", "joshi": "जोशी", "rao": "राव", "naidu": "नायडू",
    "raina": "रैना", "dhoni": "धोनी", "mahendra": "महेंद्र",
    "sachin": "सचिन", "tendulkar": "तेंदुलकर", "sourav": "सौरव",
    "ganguly": "गांगुली", "dravid": "द्रविड़", "anil": "अनिल",
    "kumble": "कुंबले", "zaheer": "ज़हीर", "harbhajan": "हरभजन",
    "yuvraj": "युवराज", "gautam": "गौतम", "gambhir": "गंभीर",
    "virender": "वीरेंद्र", "sehwag": "सहवाग", "irfan": "इरफ़ान",
    "pathan": "पठान", "yusuf": "यूसुफ़", "ishant": "इशांत",
    "umesh": "उमेश", "bhuvneshwar": "भुवनेश्वर", "shardul": "शार्दुल",
    "navdeep": "नवदीप", "saini": "सैनी", "mohit": "मोहित",
    "vaibhav": "वैभव", "suryavanshi": "सूर्यवंशी", "aayush": "आयुष",
    "mhatre": "म्हात्रे", "vihaan": "विहान", "malhotra": "मल्होत्रा",
    "abhigyan": "अभिज्ञान", "kundu": "कुंडू", "henil": "हेनिल",
    "kanishk": "कनिष्क", "chouhan": "चौहान", "deepesh": "दीपेश",
    "devendran": "देवेंद्रन", "khilan": "खिलन", "ambrish": "अंबरीश",
    "vedant": "वेदांत", "trivedi": "त्रिवेदी", "naman": "नमन",
    "pushpak": "पुष्पक", "enaan": "इनान", "udhav": "उद्धव",
    "mohan": "मोहन", "prabhsimran": "प्रभसिमरन", "abishek": "अभिषेक",
    "porel": "पोरेल", "ayush": "आयुष", "badoni": "बडोनी",
    "manav": "मानव", "suthar": "सुथार", "akash": "आकाश",
    "deep": "दीप", "madhwal": "मधवाल", "kartik": "कार्तिक",
    "tyagi": "त्यागी", "ramandeep": "रमनदीप", "sameer": "समीर",
    "rizvi": "रिज़वी", "aniket": "अनिकेत", "nehal": "नेहाल",
    "wadhera": "वढेरा", "shashank": "शशांक", "harpreet": "हरप्रीत",
    "brar": "बराड़", "gurnoor": "गुरनूर", "anshul": "अंशुल",
    "kamboj": "कंबोज", "vijaykumar": "विजयकुमार", "vyshak": "वैशाख",
    "vijay": "विजय", "shankar": "शंकर",
    # English names the generic rules got wrong in the live audit
    "sutherland": "सदरलैंड", "riley": "राइली", "meredith": "मेरेडिथ",
    "murphy": "मर्फ़ी", "spencer": "स्पेंसर", "johnson": "जॉनसन",
    "liam": "लियाम", "scott": "स्कॉट", "will": "विल", "josh": "जोश",
    "philippe": "फ़िलिप", "cooper": "कूपर", "connolly": "कोनोली",
    "fraser": "फ़्रेज़र", "jake": "जेक", "mcgurk": "मैकगर्क",
    "aaron": "एरन", "hardie": "हार्डी", "todd": "टॉड",
    "mcsweeney": "मैकस्वीनी", "sam": "सैम", "konstas": "कोन्स्टास",
    "christine": "क्रिस्टीन", "chris": "क्रिस", "ndhlovu": "एनड्लोवु",
    "ngidi": "एनगिडी", "george": "जॉर्ज", "anne": "एन", "matthews": "मैथ्यूज़",
    "matthew": "मैथ्यू", "hayley": "हेली", "devine": "डिवाइन",
}

_INDIC_TOKENS = set()          # name parts of players on South Asian teams


def mark_indic(name):
    """Register a player whose name is romanized from a South Asian
    language, so its unknown parts use the Indic rules."""
    for part in re.split(r"[\s\-]+", str(name or "")):
        k = re.sub(r"[^A-Za-z]", "", part).lower()
        if k:
            _INDIC_TOKENS.add(k)


_I_CONS = [("chh", "छ"), ("ksh", "क्ष"), ("kh", "ख"), ("gh", "घ"),
           ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"), ("ph", "फ"),
           ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("c", "क"),
           ("j", "ज"), ("t", "त"), ("d", "द"), ("n", "न"), ("p", "प"),
           ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"),
           ("v", "व"), ("w", "व"), ("s", "स"), ("h", "ह"), ("z", "ज़"),
           ("f", "फ़"), ("q", "क़"), ("x", "क्स")]
_I_VOWS = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"),
           ("ee", "ई", "ी"), ("ii", "ई", "ी"), ("oo", "ऊ", "ू"),
           ("ou", "औ", "ौ"), ("a", "अ", ""), ("i", "इ", "ि"), ("u", "उ", "ु"),
           ("e", "ए", "े"), ("o", "ओ", "ो")]
_NASAL_BEFORE = set("kgcjtdpbshz")


def indic(word):
    """Romanized South Asian name -> Devanagari, by Indic rules:
    'Pandya' -> पंड्या, 'Nishant' -> निशंत, 'Sharma' -> शर्मा."""
    w = word.lower()
    if w.endswith("ey") or w.endswith("ay"):
        w = w[:-2] + "e"
    units = []                      # ("C", deva) / ("V", independent, matra)
    i = 0
    while i < len(w):
        for lat, dev in _I_CONS:
            if w.startswith(lat, i):
                units.append(("C", dev, lat))
                i += len(lat)
                break
        else:
            for lat, ind, mat in _I_VOWS:
                if w.startswith(lat, i):
                    units.append(("V", ind, mat))
                    i += len(lat)
                    break
            else:
                i += 1
    # a doubled final consonant is one sound: Gill -> गिल
    if len(units) >= 2 and units[-1][0] == "C" and units[-1] == units[-2]:
        units.pop()
    out = []
    n = len(units)
    for k, u in enumerate(units):
        if u[0] == "V":
            if k == 0 or units[k - 1][0] == "V":
                out.append(u[1])
            else:
                mat = u[2]
                if k == n - 1 and u[1] == "अ":
                    mat = "ा"          # final a is long: Sharma, Pandya
                out.append(mat)
            continue
        nxt = units[k + 1] if k + 1 < n else None
        if u[2] in ("n", "m") and nxt and nxt[0] == "C" and \
                nxt[2][0] in _NASAL_BEFORE and k > 0:
            out.append("ं")           # Pandya, Suryansh
            continue
        out.append(u[1])
        if nxt and nxt[0] == "C":
            out.append(_VIRAMA)
    return "".join(out)


# ── Abbreviations and digits ────────────────────────────────────────────────
_UNDER = {15: "अंडर फ़िफ़्टीन", 16: "अंडर सिक्सटीन", 17: "अंडर सेवनटीन",
          19: "अंडर नाइंटीन", 21: "अंडर ट्वेंटी वन", 23: "अंडर ट्वेंटी थ्री"}
_ORD_HI = {1: "पहला", 2: "दूसरा", 3: "तीसरा", 4: "चौथा", 5: "पांचवां",
           6: "छठा", 7: "सातवां", 8: "आठवां", 9: "नौवां", 10: "दसवां"}
_U_RE = re.compile(r"\bU-?(\d{2})\b", re.I)
_ORD_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.I)
_T20_RE = re.compile(r"\bT-?20I?\b", re.I)
_DIGITS = re.compile(r"\d+")


# ── Women's cricket: the verbs agree with the players ───────────────────────
# Every template says "रहे हैं / चुके हैं / आए हैं" - correct for men, wrong
# Hindi for a women's match ("Karishma कैसे ख़त्म करते हैं"). Set FEMININE for
# a women's match and these forms switch. Longest first.
FEMININE = False
_FEM = [
    ("रहे होंगे", "रही होंगी"), ("बने हुए हैं", "बनी हुई हैं"),
    ("दौड़ते हुए आए", "दौड़ती हुई आईं"), ("टिके हुए हैं", "टिकी हुई हैं"),
    ("जमे हुए हैं", "जमी हुई हैं"), ("आते हुए", "आती हुई"),
    ("दौड़ते हुए", "दौड़ती हुई"), ("रहे हैं", "रही हैं"), ("रहे थे", "रही थीं"),
    ("चुके हैं", "चुकी हैं"), ("चुके थे", "चुकी थीं"), ("करते हैं", "करती हैं"),
    ("आए हैं", "आई हैं"), ("आए थे", "आई थीं"), ("गए थे", "गई थीं"),
    ("हुए थे", "हुई थीं"), ("हुए हैं", "हुई हैं"), ("लौटे थे", "लौटी थीं"),
    ("आउट हुए", "आउट हुईं"), ("होंगे", "होंगी"), ("दिख रहे", "दिख रही"),
    ("नहीं पाए", "नहीं पाईं"), ("खेलेंगे", "खेलेंगी"), ("चाहेंगे", "चाहेंगी"),
    ("करेंगे", "करेंगी"), ("निकले", "निकलीं"), ("पकड़े गए", "पकड़ी गईं"),
    ("थे,", "थीं,"),
]


# Lines about the crowd, flags or the ground keep their own gender - only
# lines about the players change ("झंडे लहरा रहे हैं" stays masculine).
_NOT_PLAYERS = ("झंडे", "दर्शक", "स्टैंड्स", "स्टेडियम", "लोग", "फ़ैन्स",
                "फैन्स", "तालियाँ", "शोर")


def feminine(text):
    if any(w in text for w in _NOT_PLAYERS):
        return text
    for a, b in _FEM:
        text = text.replace(a, b)
    return text


def spoken_numbers(text):
    """U19 -> अंडर नाइंटीन, 1st -> पहला, T20 -> टी ट्वेंटी, and any digits
    left over -> Hindi number words. Digits used to reach the voice raw
    ('यू19')."""
    t = _U_RE.sub(lambda m: " " + _UNDER.get(int(m.group(1)),
                                            "अंडर " + m.group(1)) + " ", text)
    t = _ORD_RE.sub(lambda m: _ORD_HI.get(int(m.group(1)), m.group(1)), t)
    t = _T20_RE.sub(" टी ट्वेंटी ", t)
    if _DIGITS.search(t):
        from phrasing import num_hi
        t = _DIGITS.sub(lambda m: num_hi(int(m.group(0))), t)
    return t


def word_hi(tok):
    """One Latin token -> Devanagari, curated first."""
    key = re.sub(r"[^A-Za-z]", "", tok).lower()
    if not key:
        return ""
    if key in NAME_HI:
        return NAME_HI[key]
    if key in WORD_HI:
        return WORD_HI[key]
    if key in INDIC_NAME_HI:
        return INDIC_NAME_HI[key]
    if key in _INDIC_TOKENS:
        return indic(key)
    if len(key) > 3 and key.endswith("a") and key[-2] not in "aeiou":
        return translit(key[:-1]) + "ा"     # Karishma, Jahzara, Emma
    return translit(_english_spelling(key))


def _english_spelling(k):
    """Respell the English silent-e patterns before the letter rules, which
    otherwise sound the e: Wade -> वडे, Rance -> रन्के.
        -ce        -> s      Rance  -> रन्स
        a_e        -> ay_    Wade   -> वेड,  Kane -> केन
        i_e        -> aai_   Mike   -> माइक, White -> व्हाइट
        o_e        -> o_     Stone  -> स्टोन
        u_e        -> oo_    Luke   -> लूक
    """
    if k.startswith("chr"):               # Christine, Chris: a k sound
        k = "kr" + k[3:]
    if re.match(r"^(nd|ng|mb|nk)[a-z]", k):   # Ndhlovu, Ngidi, Mbeki
        k = "e" + k
    if len(k) < 4:
        return k
    if k.endswith("ce"):
        return k[:-2] + "s"
    m = re.match(r"^(.*?[^aeiou]?)([aiou])([^aeiouwy])e$", k)
    if m and not re.search(r"[aeiou]", m.group(1)[-1:] or ""):
        head, v, c = m.groups()
        return head + {"a": "ay", "i": "aai", "o": "o", "u": "oo"}[v] + c
    return k


def name_hi(name):
    """Full player name -> Devanagari ('Nathan Coulter-Nile')."""
    parts = re.split(r"[\s\-]+", str(name).strip())
    out = [word_hi(p) for p in parts]
    return " ".join(p for p in out if p)


_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z']*")
_HYPHEN_NAME = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")


def say_text(text):
    """Rewrite every Latin run in a sentence to Devanagari.

    Everything handed to the voice goes through this, so the Hindi phonemizer
    never sees Latin letters.
    """
    if not text:
        return text

    # U19 / 1st / T20 / stray digits become spoken Hindi first
    s = spoken_numbers(str(text))
    if FEMININE:
        s = feminine(s)
    # Split hyphenated surnames: "Coulter-Nile" must be transliterated
    # as two names, not as one unpronounceable token.
    s = _HYPHEN_NAME.sub(" ", s)

    def sub(m):
        return word_hi(m.group(0))

    out = _LATIN_RUN.sub(sub, s)
    # digits read fine in Hindi, but tidy up spacing left by dropped words
    return re.sub(r"\s{2,}", " ", out).strip()
