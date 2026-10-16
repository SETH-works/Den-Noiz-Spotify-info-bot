"""Spotify Authorization Code with PKCE and encrypted persistent token storage."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from flask import Flask, Response, abort, redirect, request
from werkzeug.security import check_password_hash, generate_password_hash

AUTHORIZE = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
SCOPE = "user-read-currently-playing"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value == "***PASTEHERE***":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class TokenStore:
    """SQLite token store. Token JSON is authenticated-encrypted with Fernet."""
    def __init__(self, path: str, key: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fernet = Fernet(key.encode("ascii"))
        self.lock = threading.RLock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS spotify_tokens (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL)")

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, token: dict) -> None:
        import json
        blob = self.fernet.encrypt(json.dumps(token).encode())
        with self.lock, self._db() as db:
            db.execute("INSERT INTO spotify_tokens(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (blob,))

    def load(self):
        import json
        with self.lock, self._db() as db:
            row = db.execute("SELECT payload FROM spotify_tokens WHERE id=1").fetchone()
        return json.loads(self.fernet.decrypt(row[0])) if row else None

    def clear(self):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM spotify_tokens")


class SpotifyAuth:
    def __init__(self, store: TokenStore, client_id: str, redirect_uri: str):
        self.store, self.client_id, self.redirect_uri = store, client_id, redirect_uri
        self.pending: dict[str, tuple[str, float]] = {}
        self.lock = threading.Lock()

    def begin(self) -> str:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self.lock:
            now = time.time()
            self.pending = {k: v for k, v in self.pending.items() if v[1] > now}
            self.pending[state] = (verifier, now + 600)
        return AUTHORIZE + "?" + urlencode({"client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri, "scope": SCOPE, "state": state, "code_challenge_method": "S256", "code_challenge": challenge})

    def callback(self, state: str, code: str) -> None:
        with self.lock:
            item = self.pending.pop(state, None)
        if not item or item[1] < time.time():
            raise ValueError("OAuth state is missing, expired, or already used")
        response = requests.post(TOKEN, data={"client_id": self.client_id, "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri, "code_verifier": item[0]}, timeout=15)
        response.raise_for_status()
        token = response.json()
        token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
        self.store.save(token)

    def access_token(self) -> str | None:
        token = self.store.load()
        if not token:
            return None
        if token.get("expires_at", 0) > time.time() + 60:
            return token.get("access_token")
        refresh = token.get("refresh_token")
        if not refresh:
            self.store.clear()
            return None
        response = requests.post(TOKEN, data={"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": refresh}, timeout=15)
        if response.status_code >= 400:
            self.store.clear()
            return None
        updated = response.json()
        updated["refresh_token"] = updated.get("refresh_token") or refresh
        updated["expires_at"] = int(time.time()) + int(updated.get("expires_in", 3600))
        self.store.save(updated)
        return updated.get("access_token")


def create_app(auth: SpotifyAuth) -> Flask:
    app = Flask(__name__)
    admin_user = _required("UDJ_ADMIN_USER")
    admin_hash = generate_password_hash(_required("UDJ_ADMIN_PASSWORD"))

    def authorized():
        auth_header = request.authorization
        return bool(auth_header and isinstance(auth_header.username, str) and isinstance(auth_header.password, str) and secrets.compare_digest(auth_header.username, admin_user) and check_password_hash(admin_hash, auth_header.password))

    def challenge():
        return Response("Authentication required.", 401, {"WWW-Authenticate": 'Basic realm="UDJ"'})

    @app.get("/health")
    def health():
        return {"status": "ok"}, 200

    @app.get("/")
    def home():
        if not authorized():
            return challenge()
        return ("UDJ is connected to Spotify." if auth.store.load() else "UDJ is ready. Connect Spotify at /connect."), 200

    @app.get("/connect")
    def connect():
        if not authorized():
            return challenge()
        return redirect(auth.begin(), code=302)

    @app.get("/callback")
    def callback():
        if request.args.get("error"):
            return ("Spotify authorization was not granted. You can close this page.", 400)
        try:
            auth.callback(request.args.get("state", ""), request.args.get("code", ""))
        except (ValueError, requests.RequestException):
            return ("Spotify sign-in failed. Return to UDJ and try again.", 400)
        return ("Spotify is connected to UDJ. You can close this page.", 200)

    @app.post("/disconnect")
    def disconnect():
        if not authorized():
            return challenge()
        if not secrets.compare_digest(request.headers.get("X-UDJ-Action", ""), "disconnect"):
            abort(403)
        auth.store.clear()
        with auth.lock:
            auth.pending.clear()
        return ("Spotify disconnected and stored tokens deleted.", 200)

    return app

