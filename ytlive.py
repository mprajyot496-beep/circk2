# -*- coding: utf-8 -*-
"""
ytlive.py  -  the YouTube side: broadcasts, stream keys, thumbnails.

Plain HTTPS against the Data API v3, no Google client library.

One-time setup on your own PC (opens a browser to sign in to the channel):

    python ytlive.py auth           -> prints YT_REFRESH_TOKEN, saves it to .env
    python ytlive.py streams        -> creates/lists the two reusable stream keys
    python ytlive.py list           -> the channel's recent broadcasts

Credentials come from the environment (or .env):

    YT_CLIENT_ID / YT_CLIENT_SECRET   or YOUTUBE_OAUTH_CLIENT_JSON (the whole
                                      "installed" client JSON from Cloud Console)
    YT_REFRESH_TOKEN                  from `python ytlive.py auth`

State lives on the channel itself: every broadcast this creates ends its
description with a tag line

    ref: cb177681 p1 s2

(Cricbuzz match id, part, slot). The autopilot reads those back, so a fresh
GitHub runner or a rebooted server knows what has been streamed today without
any database.
"""
import os
import re
import sys
import json
import time
import socket
import calendar
import secrets
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = "https://www.googleapis.com/auth/youtube"

REF = re.compile(r"ref: cb(\d+) p(\w+) s(\d+)")


def load_env(path=os.path.join(HERE, ".env")):
    """KEY=value lines into os.environ (never overriding what is set)."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                os.environ.setdefault(k.strip(), v)
    except OSError:
        pass


def client():
    cid, sec = os.environ.get("YT_CLIENT_ID"), os.environ.get("YT_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    raw = os.environ.get("YOUTUBE_OAUTH_CLIENT_JSON", "")
    if raw and not raw.lstrip().startswith("{") and os.path.exists(raw):
        raw = open(raw, encoding="utf-8").read()
    if raw:
        j = json.loads(raw)
        j = j.get("installed") or j.get("web") or j
        return j["client_id"], j["client_secret"]
    raise SystemExit("No OAuth client: set YT_CLIENT_ID + YT_CLIENT_SECRET "
                     "or YOUTUBE_OAUTH_CLIENT_JSON")


# ── HTTP ───────────────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, code, body):
        super().__init__("HTTP %s: %s" % (code, body[:500]))
        self.code, self.body = code, body


def _http(method, url, data=None, headers=None, timeout=60):
    h = dict(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code >= 500 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise ApiError(e.code, body)
        except (urllib.error.URLError, socket.timeout, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


_token = {"value": None, "until": 0}


def access_token():
    if _token["value"] and time.time() < _token["until"] - 60:
        return _token["value"]
    refresh = os.environ.get("YT_REFRESH_TOKEN")
    if not refresh:
        raise SystemExit("YT_REFRESH_TOKEN missing - run `python ytlive.py auth`")
    cid, sec = client()
    r = _http("POST", TOKEN_URL, urllib.parse.urlencode({
        "client_id": cid, "client_secret": sec, "refresh_token": refresh,
        "grant_type": "refresh_token"}),
        {"Content-Type": "application/x-www-form-urlencoded"})
    _token.update(value=r["access_token"], until=time.time() + r.get("expires_in", 3600))
    return _token["value"]


def call(method, path, params=None, body=None, base=API, headers=None):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"Authorization": "Bearer " + access_token()}
    h.update(headers or {})
    return _http(method, url, body, h)


# ── one-time sign-in ───────────────────────────────────────────────────────
def auth():
    """Loopback OAuth: open the consent page, catch the code on localhost."""
    cid, sec = client()
    state = secrets.token_urlsafe(16)
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [""])[0]
                got["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Done - you can close this tab.</h2>")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    redirect = "http://localhost:%d" % srv.server_address[1]
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent",
        "state": state})
    print("\n  Sign in with the account that owns the YouTube channel:\n\n  %s\n"
          % url)
    webbrowser.open(url)
    while "code" not in got:
        srv.handle_request()
    if not got.get("code"):
        raise SystemExit("Sign-in refused: %s" % got.get("error"))
    r = _http("POST", TOKEN_URL, urllib.parse.urlencode({
        "code": got["code"], "client_id": cid, "client_secret": sec,
        "redirect_uri": redirect, "grant_type": "authorization_code"}),
        {"Content-Type": "application/x-www-form-urlencoded"})
    tok = r.get("refresh_token")
    if not tok:
        raise SystemExit("Google returned no refresh token: %s" % r)
    env = os.path.join(HERE, ".env")
    lines = []
    if os.path.exists(env):
        lines = [l for l in open(env, encoding="utf-8").read().splitlines()
                 if not l.startswith("YT_REFRESH_TOKEN=")]
    lines.append("YT_REFRESH_TOKEN=%s" % tok)
    with open(env, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ["YT_REFRESH_TOKEN"] = tok
    print("  Saved YT_REFRESH_TOKEN to .env. Put the same value in the GitHub\n"
          "  secret YT_REFRESH_TOKEN (and on the server's .env).")
    return tok


# ── streams (the reusable keys ffmpeg pushes to) ───────────────────────────
def stream_for_slot(slot):
    """The reusable 'crick-live slot N' stream, created on first use.
    Returns (stream id, rtmp url with key)."""
    title = "crick-live slot %d" % slot
    r = call("GET", "/liveStreams", {"part": "id,snippet,cdn", "mine": "true",
                                     "maxResults": 50})
    hit = next((s for s in r.get("items", [])
                if s["snippet"]["title"] == title), None)
    if not hit:
        hit = call("POST", "/liveStreams", {"part": "id,snippet,cdn,contentDetails"}, {
            "snippet": {"title": title},
            "cdn": {"ingestionType": "rtmp", "resolution": "variable",
                    "frameRate": "variable"},
            "contentDetails": {"isReusable": True}})
    ing = hit["cdn"]["ingestionInfo"]
    return hit["id"], "%s/%s" % (ing["ingestionAddress"], ing["streamName"])


def stream_status(stream_id):
    r = call("GET", "/liveStreams", {"part": "status", "id": stream_id})
    it = (r.get("items") or [{}])[0].get("status") or {}
    return it.get("streamStatus"), (it.get("healthStatus") or {}).get("status")


# ── broadcasts ─────────────────────────────────────────────────────────────
def recent(hours=30):
    """Our broadcasts created in the last `hours`, newest first, each with
    the ref tag parsed: {id, title, status, created, mid, part, slot}."""
    out, page = [], None
    cutoff = time.time() - hours * 3600
    while True:
        p = {"part": "id,snippet,status", "mine": "true", "maxResults": 50,
             "broadcastType": "all"}
        if page:
            p["pageToken"] = page
        r = call("GET", "/liveBroadcasts", p)
        older = False
        for it in r.get("items", []):
            sn = it["snippet"]
            created = _ts(sn.get("publishedAt"))
            if created < cutoff:
                older = True
                continue
            m = REF.search(sn.get("description") or "")
            out.append({"id": it["id"], "title": sn.get("title"),
                        "status": it["status"].get("lifeCycleStatus"),
                        "created": created,
                        "mid": m.group(1) if m else None,
                        "part": m.group(2) if m else None,
                        "slot": int(m.group(3)) if m else None})
        page = r.get("nextPageToken")
        if not page or older:
            break
    out.sort(key=lambda b: -b["created"])
    return out


def _ts(s):
    """'2026-11-02T12:13:14.5Z' (the API always answers in UTC) -> epoch."""
    if not s:
        return 0
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def create(title, description, ref, stream_id, thumb=None, tags=None,
           privacy="public"):
    """A broadcast that goes live by itself the moment the stream arrives
    (autoStart) and is ended explicitly by finish() - autoStop would end it
    on any short network drop."""
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))
    b = call("POST", "/liveBroadcasts", {"part": "id,snippet,status,contentDetails"}, {
        "snippet": {"title": title[:100],
                    "description": (description + "\n\n" + ref)[:4900],
                    "scheduledStartTime": when},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        "contentDetails": {"enableAutoStart": True, "enableAutoStop": False,
                           "enableDvr": True, "recordFromStart": True,
                           "latencyPreference": "low",
                           "monitorStream": {"enableMonitorStream": False}}})
    bid = b["id"]
    call("POST", "/liveBroadcasts/bind", {"part": "id,contentDetails",
                                          "id": bid, "streamId": stream_id})
    try:
        call("PUT", "/videos", {"part": "snippet"}, {
            "id": bid, "snippet": {
                "title": title[:100],
                "description": (description + "\n\n" + ref)[:4900],
                "categoryId": "17", "tags": (tags or [])[:30],
                "defaultLanguage": "hi", "defaultAudioLanguage": "hi"}})
    except ApiError as e:
        print("  [yt] tags/category not set: %s" % e)
    if thumb:
        set_thumbnail(bid, thumb)
    return bid


def set_thumbnail(video_id, path):
    try:
        with open(path, "rb") as f:
            call("POST", "/thumbnails/set", {"videoId": video_id,
                                             "uploadType": "media"},
                 f.read(), base=UPLOAD, headers={"Content-Type": "image/jpeg"})
        return True
    except ApiError as e:
        # 403 here = the channel is not phone-verified for custom thumbnails
        print("  [yt] thumbnail not set: %s" % e)
        return False


def status(bid):
    r = call("GET", "/liveBroadcasts", {"part": "status", "id": bid})
    items = r.get("items") or []
    return items[0]["status"].get("lifeCycleStatus") if items else None


def finish(bid):
    """End a broadcast: complete it if it went live, delete it if it never
    did (an empty 'upcoming' card helps nobody)."""
    st = status(bid)
    try:
        if st in ("live", "liveStarting", "testing"):
            call("POST", "/liveBroadcasts/transition",
                 {"part": "status", "id": bid, "broadcastStatus": "complete"})
        elif st in ("created", "ready"):
            call("DELETE", "/liveBroadcasts", {"id": bid})
    except ApiError as e:
        print("  [yt] could not end %s (%s): %s" % (bid, st, e))
    return st


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "auth":
        auth()
    elif cmd == "streams":
        for s in (1, 2):
            sid, url = stream_for_slot(s)
            print("  slot %d: stream %s  (key ends ...%s)" % (s, sid, url[-4:]))
    elif cmd == "list":
        for b in recent(72):
            print("  %-12s %-10s %s  %s" % (b["id"], b["status"],
                                            time.strftime("%d %b %H:%M", time.localtime(b["created"])),
                                            b["title"]))
    else:
        print(__doc__)
