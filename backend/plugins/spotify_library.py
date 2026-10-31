"""Spotify library on the notch (app plugin): your playlists and their tracks, in Lyra's own style.

Sign-in is Spotify's official login (OAuth with PKCE, no client secret). You need to do two things once:
  1. Create a free app at developer.spotify.com/dashboard and add this Redirect URI to it:
         http://127.0.0.1:17653/callback
  2. Paste the app's Client ID into the Client ID box on the Spotify screen in the notch, then press Connect.

Since February 2026, Spotify's "development mode" apps (the only kind a hobby project gets) need the person
who owns the app to have an active Premium subscription, and playlist contents are only returned for playlists
you own or collaborate on. This plugin follows those rules; it cannot get around them.

"Play" opens the playlist or track in the Spotify desktop app (a spotify: link), which also works without
Premium; the notch does not stream audio itself. Play, pause and skip stay on Lyra's normal media controls.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import ui
from plugins import apps

log = ui.log

APP_ID = "spotify"
PORT = 17653
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative user-read-private"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
LOGIN_WAIT_S = 180


def make_challenge(verifier: str) -> str:
    """PKCE S256: base64url(sha256(verifier)) without padding (RFC 7636)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _first_image(images: Any) -> str:
    if isinstance(images, list):
        # smallest picture that is still sharp enough for a thumbnail
        usable = [i for i in images if isinstance(i, dict) and str(i.get("url") or "").startswith("https://")]
        usable.sort(key=lambda i: abs(int(i.get("width") or 64) - 120))
        if usable:
            return str(usable[0]["url"])
    return ""


class Spotify:
    def __init__(self, store: Optional[apps.AppStore] = None, http: Optional[Callable[..., Any]] = None,
                 opener: Optional[Callable[[str], Any]] = None):
        self.store = store or apps.store(APP_ID)
        self._http = http
        self._open = opener or _open_uri
        self._login: dict[str, Any] = {}
        self._server: Optional[HTTPServer] = None
        self._lock = threading.Lock()

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def http(self, method: str, url: str, **kw: Any):
        if self._http is not None:
            return self._http(method, url, **kw)
        import requests
        return requests.request(method, url, timeout=12, **kw)

    def client_id(self) -> str:
        s = self.store.get("settings", {})
        return str((s or {}).get("client_id") or "").strip() if isinstance(s, dict) else ""

    # ── Sign-in ──────────────────────────────────────────────────────────────

    def connect(self) -> dict:
        cid = self.client_id()
        if not cid:
            return {"message": "Paste your Spotify Client ID in the box first, then press Connect."}
        verifier = secrets.token_urlsafe(64)[:96]
        state = secrets.token_urlsafe(16)
        with self._lock:
            self._login = {"verifier": verifier, "state": state, "cid": cid, "started": time.time()}
        try:
            self._listen(state)
        except OSError as e:
            log.warning("spotify: cannot listen on port %s: %s", PORT, e)
            return {"message": f"Port {PORT} is busy, so the Spotify login cannot finish. Close the other program and try again."}
        query = urlencode({"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
                           "state": state, "code_challenge_method": "S256", "code_challenge": make_challenge(verifier)})
        return {"url": f"{AUTH_URL}?{query}", "message": "Finish signing in in your browser."}

    def close(self) -> None:
        """Stop a login that is still waiting for the browser (Connect pressed again, or Lyra closing)."""
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except OSError:
                pass

    def _listen(self, state: str) -> None:
        self.close()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silence
                pass

            def do_GET(self) -> None:  # noqa: N802
                q = parse_qs(urlparse(self.path).query)
                if urlparse(self.path).path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = q.get("state", [""])[0] == state and bool(q.get("code"))
                if ok:
                    ok = outer.finish_login(q["code"][0])
                body = ("<h2>Lyra is connected to Spotify.</h2><p>You can close this tab.</p>" if ok
                        else "<h2>Spotify sign-in did not finish.</h2><p>Go back to Lyra and try Connect again.</p>").encode()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                threading.Thread(target=outer.close, daemon=True).start()

        server = HTTPServer(("127.0.0.1", PORT), Handler)
        with self._lock:
            self._server = server
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="lyra-spotify-login", daemon=True).start()
        threading.Timer(LOGIN_WAIT_S, lambda: self.close() if self._server is server else None).start()

    def finish_login(self, code: str) -> bool:
        with self._lock:
            login = dict(self._login)
        if not login:
            return False
        try:
            r = self.http("POST", TOKEN_URL, data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                "client_id": login["cid"], "code_verifier": login["verifier"]})
            if r.status_code != 200:
                log.warning("spotify: token exchange failed (%s)", r.status_code)
                return False
            self._save_tokens(r.json())
            self.store.delete("profile")
            self.profile()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("spotify: login failed: %s", e)
            return False

    def _save_tokens(self, data: dict) -> None:
        old = self.store.get("tokens", {}) or {}
        self.store.set("tokens", {
            "access": data.get("access_token", ""),
            "refresh": data.get("refresh_token") or old.get("refresh", ""),
            "expires": time.time() + int(data.get("expires_in") or 3600),
        })

    def token(self) -> str:
        t = self.store.get("tokens", {})
        if not isinstance(t, dict) or not t.get("refresh"):
            return ""
        if t.get("access") and float(t.get("expires") or 0) > time.time() + 60:
            return str(t["access"])
        try:
            r = self.http("POST", TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": t["refresh"],
                                                   "client_id": self.client_id()})
        except Exception as e:  # noqa: BLE001
            log.warning("spotify: refresh failed: %s", e)
            return ""
        if r.status_code != 200:
            if r.status_code in (400, 401):      # the grant is gone (revoked): sign in again
                self.store.delete("tokens")
            return ""
        self._save_tokens(r.json())
        return str((self.store.get("tokens", {}) or {}).get("access", ""))

    def disconnect(self) -> None:
        self.close()
        self.store.delete("tokens")
        self.store.delete("profile")

    # ── API ──────────────────────────────────────────────────────────────────

    def get(self, path: str, **params: Any) -> tuple[int, dict]:
        tok = self.token()
        if not tok:
            return 401, {}
        r = self.http("GET", API + path, headers={"Authorization": f"Bearer {tok}"}, params=params)
        try:
            return r.status_code, (r.json() if r.content else {})
        except ValueError:
            return r.status_code, {}

    def profile(self) -> str:
        cached = self.store.get("profile")
        if cached:
            return str(cached)
        code, me = self.get("/me")
        name = str(me.get("display_name") or me.get("id") or "") if code == 200 else ""
        if name:
            self.store.set("profile", name)
        return name

    def status(self) -> dict:
        if not self.client_id():
            return {"connected": False, "account": "", "message": "Add your Spotify Client ID to connect."}
        if not self.token():
            return {"connected": False, "account": "", "message": "Press Connect to sign in to Spotify."}
        return {"connected": True, "account": self.profile(), "message": ""}

    # ── Views ────────────────────────────────────────────────────────────────

    def view(self, route: str) -> dict:
        st = self.status()
        if not st["connected"]:
            return {"type": "message", "title": "Spotify", "text": st["message"]}
        if route.startswith("playlist:"):
            return self.playlist_view(route[9:])
        code, data = self.get("/me/playlists", limit=50)
        if code != 200:
            return {"type": "message", "title": "Spotify", "text": _problem(code)}
        items = []
        for p in data.get("items") or []:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            total = (p.get("items") or p.get("tracks") or {}).get("total")
            items.append({"id": p["id"], "title": p.get("name") or "Playlist",
                          "subtitle": f"{total} tracks" if isinstance(total, int) else "",
                          "image": _first_image(p.get("images")),
                          "action": {"name": "open_playlist", "params": {"id": p["id"]}}})
        return {"type": "list", "title": "Your playlists", "items": items, "route": "",
                "empty": "No playlists found on this account."}

    def playlist_view(self, pid: str) -> dict:
        code, data = self.get(f"/playlists/{pid}/items", limit=50)
        if code == 403:
            return {"type": "message", "title": "Spotify",
                    "text": "Spotify only shares the tracks of playlists you own or collaborate on.",
                    "route": f"playlist:{pid}"}
        if code != 200:
            return {"type": "message", "title": "Spotify", "text": _problem(code), "route": f"playlist:{pid}"}
        rows = []
        for entry in data.get("items") or []:
            t = (entry or {}).get("item") or (entry or {}).get("track")    # "item" since Feb 2026, "track" before
            if not isinstance(t, dict) or not t.get("uri"):
                continue
            artists = ", ".join(a.get("name", "") for a in t.get("artists") or [] if isinstance(a, dict))
            rows.append({"id": t.get("id") or t["uri"], "title": t.get("name") or "Track", "subtitle": artists,
                         "image": _first_image((t.get("album") or {}).get("images")),
                         "action": {"name": "play", "params": {"uri": t["uri"]}}})
        return {"type": "list", "title": "Tracks", "items": rows, "route": f"playlist:{pid}",
                "actions": [{"label": "Play playlist", "name": "play", "params": {"uri": f"spotify:playlist:{pid}"}}],
                "empty": "No tracks to show."}

    def action(self, name: str, params: dict) -> dict:
        if name == "open_playlist":
            pid = str(params.get("id") or "")
            return {"ok": bool(pid), "view": self.playlist_view(pid)} if pid else {"ok": False, "message": "No playlist."}
        if name == "play":
            uri = str(params.get("uri") or "")
            if not uri.startswith("spotify:") or len(uri) > 100 or not all(c.isalnum() or c == ":" for c in uri):
                return {"ok": False, "message": "That is not a Spotify link."}
            try:
                self._open(uri)
            except Exception as e:  # noqa: BLE001
                log.warning("spotify: could not open %s: %s", uri, e)
                return {"ok": False, "message": "Could not open Spotify."}
            return {"ok": True, "message": "Opening in Spotify"}
        return {"ok": False, "message": "Unknown action."}


def _problem(code: int) -> str:
    if code in (401, 0):
        return "Spotify sign-in expired. Press Connect again."
    if code == 403:
        return "Spotify refused this. Development-mode apps need an owner with Premium, and your account must be added to the app."
    if code == 429:
        return "Spotify asks to slow down. Try again in a moment."
    return f"Spotify did not answer (error {code})."


def _open_uri(uri: str) -> None:
    if sys.platform == "win32":
        os.startfile(uri)  # type: ignore[attr-defined]  # opens the Spotify desktop app
    else:
        webbrowser.open(uri)


_client: Optional[Spotify] = None


def client() -> Spotify:
    global _client
    if _client is None:
        _client = Spotify()
    return _client


PLUGIN = {
    "name": "Spotify library",
    "description": "Browse your Spotify playlists and open them in Spotify, from the notch.",
    "app": {
        "id": APP_ID,
        "title": "Spotify",
        "icon": "noteFill",
        "accent": "#2fd16f",
        "description": "Browse your playlists on the notch and open them in Spotify. Needs a free developer app (see below).",
        "status": lambda: client().status(),
        "connect": lambda: client().connect(),
        "disconnect": lambda: client().disconnect(),
        "view": lambda route, params: client().view(route),
        "action": lambda name, params: client().action(name, params),
        "settings": [{"key": "client_id", "label": "Client ID",
                      "hint": "From developer.spotify.com/dashboard. Add http://127.0.0.1:17653/callback as its Redirect URI."}],
    },
}
