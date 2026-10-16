import urllib.parse
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet

from auth import SpotifyAuth, TokenStore, create_app


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setenv("UDJ_ADMIN_USER", "admin")
    monkeypatch.setenv("UDJ_ADMIN_PASSWORD", "test-password")
    store = TokenStore(str(tmp_path / "tokens.sqlite3"), Fernet.generate_key().decode())
    return SpotifyAuth(store, "client-id", "http://127.0.0.1:8888/callback")


def test_pkce_url_and_state_is_single_use(auth):
    url = auth.begin()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["scope"] == ["user-read-currently-playing"]
    state = query["state"][0]
    with pytest.raises(ValueError):
        auth.callback(state, "fake-code")
    assert state not in auth.pending


def test_tokens_are_persisted_encrypted(auth):
    auth.store.save({"access_token": "access-secret", "refresh_token": "refresh-secret"})
    assert auth.store.load()["refresh_token"] == "refresh-secret"
    assert b"refresh-secret" not in auth.store.path.read_bytes()


def test_connect_requires_admin_and_disconnect_clears_token(auth):
    auth.store.save({"refresh_token": "keep-private"})
    client = create_app(auth).test_client()
    assert client.get("/connect").status_code == 401
    assert client.get("/connect", auth=("admin", "wrong-password")).status_code == 401
    assert client.get("/connect", auth=("admin", "test-password")).status_code == 302
    assert client.post("/disconnect", auth=("admin", "test-password")).status_code == 403
    assert client.post("/disconnect", auth=("admin", "test-password"), headers={"X-UDJ-Action": "disconnect"}).status_code == 200
    assert auth.store.load() is None


def test_oauth_callback_rejects_missing_state(auth):
    client = create_app(auth).test_client()
    response = client.get("/callback?state=unknown&code=fake")
    assert response.status_code == 400
    assert auth.store.load() is None


def test_oauth_callback_exchanges_code_and_saves_tokens(auth):
    state = urllib.parse.parse_qs(urllib.parse.urlparse(auth.begin()).query)["state"][0]
    response = Mock()
    response.json.return_value = {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
    response.raise_for_status.return_value = None
    with patch("auth.requests.post", return_value=response) as post:
        auth.callback(state, "authorization-code")
    sent = post.call_args.kwargs["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "authorization-code"
    assert sent["code_verifier"]
    assert auth.store.load()["refresh_token"] == "refresh"


def test_expired_access_token_is_refreshed_and_rotated(auth):
    auth.store.save({"access_token": "expired", "refresh_token": "old-refresh", "expires_at": 0})
    response = Mock()
    response.json.return_value = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    response.status_code = 200
    with patch("auth.requests.post", return_value=response) as post:
        assert auth.access_token() == "new-access"
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert auth.store.load()["refresh_token"] == "new-refresh"


def test_temporary_refresh_failure_keeps_saved_refresh_token(auth):
    auth.store.save({"access_token": "expired", "refresh_token": "keep-refresh", "expires_at": 0})
    response = Mock()
    response.status_code = 503
    with patch("auth.requests.post", return_value=response):
        assert auth.access_token() is None
    assert auth.store.load()["refresh_token"] == "keep-refresh"

