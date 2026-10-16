"""Spotify PKCE sign-in and encrypted local token storage for UDJ."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from cryptography.fernet import Fernet

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPE = "user-read-currently-playing"


class TokenStore:
    """Persist the token response encrypted in a small local SQLite database."""

    def __init__(self, path: str | Path, key: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fernet = Fernet(key.encode("ascii"))
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS spotify_tokens "
                "(id INTEGER PRIMARY KEY CHECK(id = 1), payload BLOB NOT NULL)"
            )
        # Restrict the database file on POSIX. On Windows, access is governed by
        # the parent directory's ACL; chmod is best-effort and not a substitute.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, token: dict) -> None:
        encrypted = self.fernet.encrypt(json.dumps(token).encode("utf-8"))
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO spotify_tokens(id, payload) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (encrypted,),
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def load(self) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload FROM spotify_tokens WHERE id = 1").fetchone()
        if row is None:
            return None
        return json.loads(self.fernet.decrypt(row[0]).decode("utf-8"))

    def clear(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM spotify_tokens WHERE id = 1")


class SpotifyAuth:
    """PKCE authorization, token exchange, and refresh without a client secret."""

    def __init__(self, store: TokenStore, client_id: str, redirect_uri: str):
        self.store = store
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}

    def begin(self) -> str:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        with self._lock:
            now = time.time()
            self._pending = {
                old_state: item
                for old_state, item in self._pending.items()
                if item[1] > now
            }
            self._pending[state] = (verifier, now + 600)
        return AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": SCOPE,
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            }
        )

    def complete(self, state: str, code: str) -> None:
        with self._lock:
            matched_state = next(
                (candidate for candidate in self._pending if hmac.compare_digest(candidate, state)),
                None,
            )
            pending = self._pending.pop(matched_state, None) if matched_state else None
        if not pending or pending[1] < time.time() or not code:
            raise ValueError("OAuth state is missing, expired, or already used")
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": pending[0],
            },
            timeout=15,
        )
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

        refresh_token = token.get("refresh_token")
        if not refresh_token:
            self.store.clear()
            return None

        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        if response.status_code >= 400:
            try:
                error = response.json().get("error")
            except (ValueError, AttributeError):
                error = None
            if response.status_code == 401 or error == "invalid_grant":
                self.store.clear()
                return None
            response.raise_for_status()

        updated = response.json()
        updated["refresh_token"] = updated.get("refresh_token") or refresh_token
        updated["expires_at"] = int(time.time()) + int(updated.get("expires_in", 3600))
        # Save a possibly rotated refresh token before returning to the caller.
        self.store.save(updated)
        return updated.get("access_token")


def run_local_authorization(auth: SpotifyAuth, timeout: int = 600) -> None:
    """Temporarily listen on a loopback callback, then exit after one sign-in."""
    parsed = urlparse(auth.redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("UDJ setup requires an http://127.0.0.1 loopback redirect URI")
    if not parsed.port or parsed.path != "/callback":
        raise ValueError("The loopback redirect URI must include a port and end in /callback")

    result: dict[str, str | None] = {"error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            # The callback query contains a short-lived authorization code.
            return

        def do_GET(self) -> None:
            callback = urlparse(self.path)
            if callback.path != "/callback":
                self.send_error(404)
                return
            query = parse_qs(callback.query)
            if query.get("error"):
                result["error"] = "Spotify authorization was not granted."
                status, message = 400, result["error"]
            else:
                try:
                    auth.complete(
                        query.get("state", [""])[0], query.get("code", [""])[0]
                    )
                except (ValueError, requests.RequestException):
                    result["error"] = "Spotify sign-in failed; run setup again."
                    status, message = 400, result["error"]
                else:
                    status, message = 200, "Spotify is connected to UDJ. You may close this page."
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.server.completed = True

    server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    server.timeout = 1
    server.completed = False
    authorization_url = auth.begin()
    print("Open this Spotify authorization URL in your browser:\n" + authorization_url)
    print("UDJ is listening only on loopback for the one-time callback.")
    deadline = time.monotonic() + timeout
    try:
        while not server.completed and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not server.completed:
        raise TimeoutError("Spotify authorization callback did not arrive in time")
    if result["error"]:
        raise RuntimeError(result["error"])

