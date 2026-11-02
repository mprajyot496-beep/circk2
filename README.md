# Live Hindi cricket commentary + 3D ground view

> **On autopilot:** every day the two most-hyped matches (India first), each
> innings its own YouTube live stream with a generated thumbnail - 4 streams a
> day, no one at the keyboard. See [Going live automatically](#going-live-automatically).

Pick a live match. It opens a broadcast-style window - a real 3D ground that
plays out each delivery, under a TV scoreboard with real player photos - and
speaks continuous Hindi ball-by-ball commentary in step with it. **No pauses**,
no API key, no cloud TTS.

```bat
run_live.bat                      :: or: python main.py
python main.py --list             :: what's live right now
python main.py --auto             :: skip the menu, take the top live match
python main.py --match 151554     :: go straight to one Cricbuzz match id
python main.py --no-visual        :: commentary only, no window
```

One file to run: **`main.py`**. Esc or Q closes the window.

## Setup

```bat
pip install -r requirements.txt
cd voices && python -m piper.download_voices hi_IN-pratham-medium
```

---

## The broadcast window

A real 3D ground ([stadium3d.py](stadium3d.py), Panda3D on the GPU) with a
TV-style scoreboard over it ([visual.py](visual.py)), laid out like a live
YouTube cricket stream. No Hindi text on screen - the voice carries the Hindi.

**Scoreboard** — flags and score box for both sides, a centre box with what
just happened (SIX / FOUR / WICKET / 1 RUN / DOT BALL / OVER), CRR / RRR,
partnership, status, the last two overs ball by ball with totals, three player
cards with real cut-out photos, and the last wicket.

**Motion** ([overlay.py](overlay.py), plain pygame on the CPU): the score
counts up with a floating "+4"/"+6" and flashes (red on a wicket); the centre
box pops in with a shine; new ball chips pop into RECENT; SIX / FOUR / WICKET
banners wipe across the bottom of the 3D band with confetti, sparks or shards
(WICKET names the batsman and his score); FIFTY / CENTURY get a gold banner
with the player's photo; an END OF OVER lower-third slides in at the over
break; cards slide up for a new batsman or bowler; the striker marker
pulses; a stats ticker runs along the bottom; a glint crosses the top band.
The scoreboard was trimmed (top 238 → 180 px, cards from 446 → 532 px) so
the 3D gets 70% more height, with the camera framing unchanged.

The overlay goes to the GPU as pygame's raw BGRA buffer with the flip done in
the texture coordinates - 3 ms a frame instead of 29 ms for a flipped,
converted copy, which had held the window to ~24 fps.

**Photos and flags** ([faces.py](faces.py)) — real Cricbuzz headshots,
background removed with the u2net model run straight through onnxruntime
(`~/.u2net/u2net.onnx`; without it photos keep a soft rounded mask). Players
with no Cricbuzz photo get a drawn avatar with initials. Flags come from the
same profile pages. Everything is cached in `assets/players/`, `assets/flags/`.

**The 3D ground** — oval outfield with mowing stripes and the painted 30-yard
circle, a textured pitch with creases and wear, grass detail for close-ups,
stumps and LED bails, rope, advertising boards, two crowd tiers, roof, sight
screens (black behind a white ball, white behind a red one), floodlights.

**The players** — jointed figures with IK arms and legs: face (eyes, nose,
ears, brows), hair, collar and sleeve trim, belt, rounded joints; helmets with
peak, grille and neck guard; batting gloves, ridged pads with knee rolls and
straps, shoes with soles; a bat with grip bands and a sticker; the keeper in
slim pads and keeping gloves, helmeted when standing up to a spinner; team
kits (whites in a Test), surname on the back, two umpires giving the real
signals. Each body part is merged into one draw call. Driven by each player's real Cricbuzz profile:

| From the profile | In the 3D |
|---|---|
| Left Handed Bat | stance, strokes and the whole field mirrored |
| the photo's crown | hair colour |
| spinner (offbreak, orthodox, legbreak…) | short run-up, keeper up at the stumps |
| pace (fast, medium…) | long run-up, keeper and slips back |
| left-arm | delivers from the other side |
| the photo | skin tone sampled from his face |

**A delivery** plays what the feed says: run-up, bound, release, pitches at the
length (yorker / full / good / short / bouncer) on the line, the stroke named
(drive, pull, cut, sweep, flick, defend, leave, duck, advance), the ball to the
fielding position named, fielder chases and throws, batsmen run their runs,
boundary / six into the crowd / bowled (stumps fly) / caught (the catcher takes
it) / lbw (appeal) / stumped / run out, umpire signal, team celebration.

**Cameras** cut like a broadcast: long lens behind the bowler for the delivery,
follow cam on the ball, catcher close-up, side-on for running, stump cam,
celebration, crowd for a six, then a slow-motion **REPLAY** from another angle
after every four, six and wicket. Between balls: wide, batsman close-up,
bowler walking back, aerial, crowd.

### Voice, scoreboard and 3D move together

The feed is polled every 8 s and the voice runs a few seconds behind it, so
nothing on screen changes when data arrives. Each ball is staged with two cues
that fire **the moment a line becomes audible**:

| Line | What changes |
|---|---|
| the opener ("चौदहवें ओवर की तीसरी गेंद…") | 3D run-up and delivery |
| the result ("छक्का!!", "एक रन, आराम से") | 3D shot, score, RECENT chip, centre box, flash, crowd roar |

If the voice reaches the result before the 3D reaches the shot, the delivery
is played faster so they land together. On the last ball of an over the feed
has already moved to the next bowler; the card keeps the man who bowled it and
switches at the over break. A board change with no ball behind it (new batter)
waits until the commentary is back to small talk.

Measured on a live ODI: each result cue fired on exactly the line that said the
result, and the board read the score that line spoke.

---

## Voice: Piper, fully offline

| | |
|---|---|
| Engine | Piper TTS (ONNX, local CPU) |
| Voice | `hi_IN-pratham-medium` |
| Speed | **0.26× realtime** — 1.1 s of CPU per 4.2 s of audio |

No cloning, no training, no cloud TTS. Local is what makes the no-pause
guarantee hold — no network request can stall mid-over. Alternatives:
`hi_IN-rohan-medium` (male), `hi_IN-priyamvada-medium` (female); download the
same way and set `VOICE_NAME` in [voice.py](voice.py).

### Pronunciation

The feed hands over Latin text, and Piper's Hindi voice runs that through the
Hindi phonemizer, which mangles it. **Everything spoken is converted to
Devanagari first** ([translit.py](translit.py)):

- `NAME_HI` — a curated spelling for ~250 players, teams and cricket terms.
  Always preferred: English spelling is not phonetic, so no rule set gets
  "Laughlin" or "Coulter-Nile" right.
- a digraph-ordered fallback for anyone not in the map, handling the patterns
  that recur in cricket surnames (`Mc-`, final `-y`, `-ious`, `-tion`).

```
Ben Laughlin        -> बेन लॉफ़लिन
Nathan Coulter-Nile -> नेथन कोल्टर नाइल
India Champions     -> भारत चैंपियंस
```

The fallback is an approximation by nature. If a name sounds wrong, add one
line to `NAME_HI` and it is fixed permanently.

Fielding positions are curated too ([phrasing.py](phrasing.py) `FIELD_HI`, 41
entries) — the fallback turns "cover" and "gully" into nonsense. Longest match
wins, so "deep mid-wicket" never comes out as "mid-wicket".

```
long on  -> लॉन्ग ऑन        extra cover -> एक्स्ट्रा कवर
gully    -> गली             deep mid-wicket -> डीप मिड विकेट
```

### Broadcast tone

Every line goes through a commentary-box chain ([voice.py](voice.py) `TONE`,
`broadcast()`): pitch lowered with the tempo compensated (deeper, not
slower), broadcast EQ (rumble cut, chest warmth, mud cut, presence over the
crowd), a compressor and light saturation.

A shout stays **the same voice** - pitch within ~2% of normal talk - and is
loud because it is compressed harder and pushed to full level. (A first
tuning lifted shouts 13% with heavy saturation; they sounded like a second
commentator.) Close calls, dropped catches, appeals and the follow-ups after
a boundary use the louder tones too. Measured on one sentence:

| | pitch | loudness |
|---|---|---|
| calm | 98 Hz | −17.7 dBFS |
| neutral | 101 Hz | −16.6 dBFS |
| lively | 99 Hz | −13.3 dBFS |
| amazed | 102 Hz | −12.5 dBFS |
| excited (the shout) | 101 Hz | −10.6 dBFS |

Big moments also get a **build-up** before the shout ("उठाया है... ऊपर...
और ऊपर..." → "छक्का!!"): the 3D shot starts on the build-up and the board,
banner and crowd roar land on the shout, so the voice climbs while the ball is
in the air.

### Emotion and pace

Measured on this voice, same sentence:

| `length_scale` | 0.88 | 1.05 | 1.15 | 1.28 | 1.38 | 1.50 |
|---|---|---|---|---|---|---|
| words/min | 197 | 181 | 172 | 158 | 150 | 135 |

The original 0.88 baseline chased the reference stream's rate and was too fast
to follow; 1.28 overshot the other way and dragged. Neutral now sits near
**170 wpm**, between the two, with a ~40 wpm spread so the emotion is audible
rather than just labelled.

| Emotion | `length_scale` | wpm | Used for |
|---|---|---|---|
| `excited` | 0.95 | 193 | the shout on a six, wicket, no-ball, milestone |
| `lively` | 1.05 | 181 | the follow-up, four, death overs, tight chase |
| `neutral` | 1.15 | 170 | an ordinary delivery, the score |
| `calm` | 1.25 | 160 | between-ball filler and colour |
| `slow` | 1.35 | 153 | over breaks, rain, innings break |

> Piper's duration predictor is stochastic, so a single clip varies ~5%. The
> wpm above are means of 3 runs; don't tune off one measurement.

Tune the table in [voice.py](voice.py).

### Big moments

A six is not one line. A real commentator erupts, says it again, says where it
went, reacts, gives the stat, and only then settles — so sixes, fours, wickets
and no-balls build a 6–8 line sequence with the emotion **decaying** across it,
peaking on the shout and back to normal by the end:

```
lively   तो आइए अगली गेंद पर, बेन लॉफ़लिन की गेंद और सामने युवराज सिंह
excited  छक्का!! क्या मारा है!!
excited  क्या शॉट था दोस्तों, क्या शॉट!
excited  लॉन्ग ऑन के ऊपर से उड़ा दिया!
lively   पूरा स्टेडियम खड़ा हो गया है!
lively   तीसरा छक्का, और गेंदबाज़ परेशान दिख रहे हैं
neutral  भारत यहाँ एक सौ अट्ठावन रन बना चुकी है...
```

A wicket also reads out what the batsman made, from the `31(26)` in the feed.
Ordinary balls stay short and name the fielder the ball went to.

---

## The style model

[reference_transcript.txt](reference_transcript.txt) is the Hindi caption track
of the reference stream (3 h T20 final), pulled with `yt-dlp` and de-overlapped.

| Measured | Value | Consequence |
|---|---|---|
| Speech rate | **194 wpm** sustained | `length_scale` baseline |
| Median sentence | **8 words** | every utterance is one short clause |
| Bridge connectors | ×300, ×142, ×74 | each ball *opens* with a connector |
| Score restatement | ×91 | re-read often, through 6 different frames |
| Register | Hinglish | English cricket nouns stay English |

**Continuity is structural, not vocal.** The reference commentator never waits
for a ball: every delivery is bridged into the next, the score is restated, and
filler absorbs the rest.

### Register: spoken Hinglish, not textbook Hindi

Word choice is taken from what the reference commentator actually says
(counts over the 3 h transcript):

| Says | Never / rarely says |
|---|---|
| प्रेशर (36) | दबाव (0) |
| पार्टनरशिप (30) | साझेदारी (4) |
| एक्स्ट्रा (20) | अतिरिक्त (0) |
| रन रेट (68) | रन गति (0) |
| भाई (108), बिल्कुल (84), बढ़िया (51) | |

Their most frequent shapes are used directly: "इस एक रन के साथ X का स्कोर…"
(×65), "रन बनाने का और मौका" (×41), "लॉन्ग ऑन क्षेत्र में" (×21),
"एक और डॉट डिलीवरी" (×22), "कट शॉट के रूप में", "विकेट्स के नुकसान पर".

### Where we are, and what came before

- **Ball position.** Cricbuzz's `ballMetric` (13.3) becomes "चौदहवें ओवर की
  तीसरी गेंद", "ओवर की आख़िरी गेंद", "पंद्रहवें ओवर की शुरुआत".
- **Score hung off the run.** "इस चौके के साथ भारत का स्कोर पहुँचा…" —
  only on the newest ball of a poll, because older balls in the same batch
  would read out a total they did not produce.
- **Innings memory** (`Director._track`): boundaries in a row ("लगातार तीसरी
  बाउंड्री"), boundaries this over, a boundary drought ending, a dropped
  batter hitting a boundary ("कैच छोड़ा तो मैच छोड़ा"), a bowler dismissing
  the batter who hit him ("बॉलर ने हिसाब बराबर कर दिया"), three wickets
  inside five overs, and momentum across the last two overs.

### The analyst

`Director.insight()` gives an opinion only when a real number backs it, with
an opinion lead-in ("मुझे ऐसा लगता है", "लिखकर ले लीजिए") about half the
time:

| Insight | Fires when (T20 / ODI) |
|---|---|
| fast strike rate | SR ≥ 165 over 10+ balls / ≥ 115 over 20+ |
| slow strike rate | SR < 105 over 15+ balls / < 65 over 30+ |
| economical / expensive bowler | econ ≤ 6 or ≥ 10.5 after 2 overs / ≤ 4 or ≥ 7 after 3 |
| projected total | first innings, from over 4 to 2 overs before the end |
| rate gap | chasing, required rate ≥ 1.5 above the current one |
| partnership | 50+ runs |
| boundary drought | 12+ legal balls since the last four or six |

Thresholds live in `LIMITS` in [situation.py](situation.py). Tests get none,
since a strike rate of 50 means something else there.

> A naive word count gives 387 wpm. YouTube auto-captions are *rolling* — each
> cue repeats the previous tail. After de-overlapping the true rate is 194 wpm.

---

## Hardcoded but dynamic

Sentence *shapes* are fixed; names, runs, overs, the bowler's spell, runs in
the over just bowled, phase, chase pressure and milestones are live.

Nothing runs on a fixed rotation. [situation.py](situation.py) derives context
and `Director` picks topics *weighted by it*, refusing to reuse a topic (last 6)
or a sentence (last 250). Phase-aware colour means powerplay, middle and death
talk come from different banks.

| Phase | T20 | ODI |
|---|---|---|
| `powerplay` | 0–5 | 0–9 |
| `middle` | 6–14 | 10–39 |
| `death` | 15–19 | 40–49 |

**Over breaks** get their own block built from what just happened: over closed →
runs in it (maiden / tight ≤3 / expensive ≥12) → momentum over two overs →
wicket if one fell → bowler's spell → score → chase equation + required rate +
pressure read, or partnership + overs left → an analyst insight → who bowls
next → break colour → striker's progress. The first over after joining is only
partly watched, so its runs are not judged (no false "मेडन"). Over breaks reset
with the innings, so they fire through the chase too.

**Start and finish**: a welcome ("नमस्कार दोस्तों…") and the batting side; at
the end the result is said in Hindi ("भारत की पांच विकेट्स से जीत"). The
feed's English status line is never handed to the Hindi voice.

**Between balls**: score, batsmen progress, strike rate, boundaries,
partnership, bowler figures, run rate, overs, last wicket, chase equation,
pressure, phase colour, and occasional like/subscribe calls to action.

**Breaks in play**: innings break (reads the target), rain, drinks, timeout,
lunch/tea/stumps. Milestones (50/100/150) fire a roar and their own line.

**Match notes** in English are converted to Hindi when recognised ("comes to
the crease", "back into the attack", timeout, drinks, review) and otherwise
**printed but not spoken** — letting a Hindi voice read English prose was half
the pronunciation problem.

---

## Crowd audio

A crowd is not noise — it is hundreds of *voices*, so [atmosphere.py](atmosphere.py)
synthesizes voices: ~70 sawtooth sources at human pitches (85–255 Hz), each
amplitude-modulated at 2–6 Hz for syllable rhythm, grouped by vowel and pushed
through formant filters at the real F1/F2/F3 of /a/, /e/, /o/, /u/. Formants are
what make a sound read as a voice. Then claps (bandpassed transients, not
noise bursts), whistles, a multi-tap stadium reverb and a distance roll-off.

Verified by spectrum: **15.1× above average energy at 200–900 Hz** (first
formant) and 7.2× at 900–2000 Hz, with almost nothing above 4 kHz — the profile
of distant human voices. White noise would be ~1.0× flat across every band.

Generated once (~24 s) and cached to `assets/*.wav`. Drop your own
`ambient.wav` / `cheer.wav` / `roar.wav` / `clap.wav` in `assets/` to override.

> Pixabay was tried first. Their public API only covers images and videos — the
> audio endpoint returns **403 Access denied** regardless of key, so that route
> is closed.

---

## Why there is no pause

1. **Lookahead synthesis** — Piper at 0.26× realtime keeps one worker ahead of
   playback, with no network to stall on.
2. **Gapless handoff** — all speech goes out on one pygame channel via
   `Channel.queue()`, which starts the next clip the instant the current ends.
   Audio is passed in memory (resampled 22.05k→44.1k, stereo); no temp files.
3. **Self-feeding buffer** — the `Speaker` pulls its own filler when the buffer
   dips. It never waits for the poll loop, so a slow request, an over break or
   an innings break can't create silence.

A real ball is submitted with priority and **discards buffered filler** -
keeping one ready clip only when nothing is queued behind the current one, to
bridge the ~1 s the ball's first line takes to synthesize. Before, a ball
waited 10–17 s behind ready filler; now 5–8 s.

A line is announced (and its cues fired) when it is actually audible, not when
it is queued - pygame hands over to a queued clip inside the audio thread, and
for an instant the channel reads "idle"; the player waits that out instead of
`play()`-ing over the clip that just started (which used to cut lines off).

**Measured:** a ball every 20–35 s → no gaps, no cut-off lines, ball heard
5–8 s after the data arrived.

---

## Files

| File | Role |
|---|---|
| `main.py` | **the only file you run** — menu, render loop, poll thread |
| `visual.py` | the window: scoreboard overlay, speech-timed cues |
| `stadium3d.py` | 3D stadium, players, deliveries, cameras, replays |
| `faces.py` | player photos (cut out), flags, batting/bowling style |
| `cricbuzz.py` | live feed + match scan/ranking |
| `situation.py` | match context + the director that decides what to say |
| `phrasing.py` | every Hindi template, plus Hindi number words |
| `translit.py` | Latin → Devanagari, so names are pronounced correctly |
| `voice.py` | Piper speech engine (gapless, self-feeding, emotion) |
| `atmosphere.py` | formant-synthesized stadium crowd |
| `reference_transcript.txt` | the caption track the style was measured from |

Channels: speech owns 0, crowd uses 5 (ambient) and 6 (reactions).

### Tuning

- [voice.py](voice.py): `VOICE_NAME`, `EMOTION` table, `BUFFER_TARGET = 2`
  (raise for stall resistance, lower to cut ball latency; ~3.5 s per unit)
- [visual.py](visual.py): `CHANNEL` - your channel name in the corner
- [stadium3d.py](stadium3d.py): `POS` fielding positions (angle, distance),
  `FIELD_SETS` per phase, `TEAM_COL` kit colours, `_shot_params` cameras
- [main.py](main.py): `POLL = 8` seconds between fetches
- [phrasing.py](phrasing.py): new lines go here. `COLOUR`, `BRIDGE` and
  `SCORE_FRAME` most affect how repetitive a long session feels
- [translit.py](translit.py): add a name to `NAME_HI` to fix its pronunciation

### `real_voices/`

Your original voice recording, untouched — nothing uses it any more, and I'd
rather not delete audio that may not exist elsewhere. Safe to remove yourself.

---

## Going live automatically

| File | Role |
|---|---|
| `autopilot.py` | the loop: pick, wait for the start, go live per innings, end at the break |
| `picker.py` | today's matches from Cricbuzz, ranked by hype (India first) + live YouTube viewers |
| `thumbnail.py` | 1280x720 thumbnail: two captains, flags, LIVE, match box, team banners |
| `ytlive.py` | YouTube API: sign-in, reusable stream keys, broadcasts, thumbnails |
| `streamer.py` | headless Linux: Xvfb screen + PulseAudio speaker + engine + ffmpeg -> RTMP |
| `deploy/setup_linux.sh` | installs everything on any Ubuntu box (runner, VM, Docker) |
| `deploy/install_server.sh` | Oracle/any VM: systemd service per slot, restarts forever |

**A day:** `picker.py` ranks every match playing today. The top
`MATCHES_PER_DAY` (2) are the picks; a pick that has not started is *waited
for*. When it starts, a broadcast is created ("🔴 LIVE: India vs New Zealand,
1st ODI | 1st Innings | Live Score & Hindi Commentary") with its thumbnail,
the stream goes on air, and at the innings break it ends. The 2nd innings is a
new broadcast. Tests: one broadcast per day of play, ending at stumps.

```
python picker.py --buzz          today's ranking
python thumbnail.py 161234       render a thumbnail into thumbs/
python autopilot.py --plan       what it would do now
```

### One-time setup

1. **Google Cloud** (the project your OAuth client is in): *APIs & Services ->
   OAuth consent screen -> Publish app* (status **In production**). In
   "Testing" the sign-in expires every 7 days and the channel stops.
2. **YouTube channel:** verify the phone number (youtube.com/verify - needed
   for live streaming and custom thumbnails) and enable live streaming in
   YouTube Studio. The first time takes up to 24 hours.
3. **Sign in once on your PC:** `python ytlive.py auth` -> writes
   `YT_REFRESH_TOKEN` to `.env`. Then `python ytlive.py streams` creates the
   two reusable stream keys.
4. **GitHub -> Settings -> Secrets and variables -> Actions**
   - Secrets: `YT_CLIENT_ID`, `YT_CLIENT_SECRET` (from the OAuth client JSON),
     `YT_REFRESH_TOKEN`, `YOUTUBE_API_KEY`
   - Variables (optional): `CHANNEL_NAME`, `MATCHES_PER_DAY`, `YT_PRIVACY`
     (`unlisted` for a dry run)

`.env` is never committed (`.gitignore`) - the repo is public.

### Where it runs

**GitHub Actions** (`.github/workflows/live.yml`): every 15 min each slot
checks for work; a stream job runs up to 5.5 h and the next run resumes into
the same broadcast. `keepalive.yml` stops GitHub disabling the schedule after
60 quiet days; `selftest.yml` streams 2 minutes into a file on every push and
publishes a frame + numbers to the `selftest` branch.

**Oracle Cloud Always Free VM** (the reliable home):

```bash
git clone https://github.com/crickp88-debug/crick-.git crick && cd crick
nano .env            # the same keys as the GitHub secrets
bash deploy/install_server.sh          # SLOTS=1 for a single slot
journalctl -u crick-live@1 -f
```

Run **one** of the two, not both - each would stream the same slots.
