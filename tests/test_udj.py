import asyncio
import io
import urllib.parse
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cryptography.fernet import Fernet

import auth as auth_module
import spotify_discord_avatar as bot
from auth import SpotifyAuth, TokenStore, run_local_authorization


@pytest.fixture
def store(tmp_path):
    key = Fernet.generate_key().decode()
    instance = TokenStore(tmp_path / "data" / "udj.sqlite3", key)
    instance.test_key = key
    return instance


@pytest.fixture
def auth(store):
    return SpotifyAuth(store, "test-client-id", "http://127.0.0.1:8888/callback")


def token_response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = auth_module.requests.HTTPError("request failed")
    else:
        response.raise_for_status.return_value = None
    return response


def test_pkce_url_contains_s256_and_state(auth):
    url = auth.begin()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["scope"] == ["user-read-currently-playing"]
    assert len(query["state"][0]) >= 40


def test_invalid_and_reused_state_cannot_exchange_code(auth):
    auth.begin()
    with patch("auth.requests.post") as post:
        with pytest.raises(ValueError):
            auth.complete("wrong-state", "authorization-code")
        post.assert_not_called()


def test_local_callback_binds_loopback_and_completes_pkce(auth, monkeypatch):
    exchange = token_response({"access_token": "test-access", "refresh_token": "test-refresh", "expires_in": 3600})
    post = Mock(return_value=exchange)
    monkeypatch.setattr("auth.requests.post", post)
    captured = {}

    class FakeServer:
        def __init__(self, address, handler_type):
            captured["address"] = address
            self.handler_type = handler_type
            self.completed = False

        def handle_request(self):
            state = next(iter(auth._pending))
            handler = self.handler_type.__new__(self.handler_type)
            handler.server = self
            handler.path = "/callback?" + urllib.parse.urlencode(
                {"state": state, "code": "one-time-code"}
            )
            handler.wfile = io.BytesIO()
            handler.send_response = lambda status: captured.update(status=status)
            handler.send_header = lambda *_args: None
            handler.end_headers = lambda: None
            handler.do_GET()
            captured["body"] = handler.wfile.getvalue()

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr("auth.HTTPServer", FakeServer)
    run_local_authorization(auth)
    assert captured["address"] == ("127.0.0.1", 8888)
    assert captured["status"] == 200
    assert b"connected to UDJ" in captured["body"]
    assert captured["closed"] is True
    assert auth.store.load()["refresh_token"] == "test-refresh"
    payload = post.call_args.kwargs["data"]
    assert payload["grant_type"] == "authorization_code"
    assert payload["client_id"] == "test-client-id"
    assert payload["code_verifier"]


def test_local_callback_rejects_non_loopback_uri(auth):
    auth.redirect_uri = "https://public.example/callback"
    with pytest.raises(ValueError, match="loopback"):
        run_local_authorization(auth)


def test_token_storage_is_encrypted_and_survives_store_recreation(store):
    token = {"access_token": "access-value", "refresh_token": "refresh-value"}
    store.save(token)
    restored = TokenStore(store.path, _fernet_key(store))
    assert restored.load() == token
    assert b"refresh-value" not in store.path.read_bytes()


def _fernet_key(store):
    """Recover a test-only Fernet key from the fixture instance for reopening."""
    return store.test_key


def test_refresh_saves_replacement_token_before_returning(auth):
    auth.store.save({"access_token": "old", "refresh_token": "old-refresh", "expires_at": 0})
    response = token_response({"access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600})
    with patch("auth.requests.post", return_value=response) as post:
        assert auth.access_token() == "new"
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert auth.store.load()["refresh_token"] == "new-refresh"


def test_refresh_without_replacement_keeps_existing_token(auth):
    auth.store.save({"access_token": "old", "refresh_token": "keep-refresh", "expires_at": 0})
    with patch("auth.requests.post", return_value=token_response({"access_token": "new", "expires_in": 3600})):
        assert auth.access_token() == "new"
    assert auth.store.load()["refresh_token"] == "keep-refresh"


def test_invalid_grant_clears_stored_token(auth):
    auth.store.save({"access_token": "old", "refresh_token": "expired", "expires_at": 0})
    response = Mock(status_code=400)
    response.json.return_value = {"error": "invalid_grant"}
    with patch("auth.requests.post", return_value=response):
        assert auth.access_token() is None
    assert auth.store.load() is None


def test_transient_refresh_failure_preserves_stored_refresh_token(auth):
    auth.store.save({"access_token": "old", "refresh_token": "keep-refresh", "expires_at": 0})
    with patch("auth.requests.post", return_value=token_response({}, 503)):
        with pytest.raises(auth_module.requests.HTTPError):
            auth.access_token()
    assert auth.store.load()["refresh_token"] == "keep-refresh"


def test_disconnect_deletes_only_local_token_record(auth):
    auth.store.save({"refresh_token": "stored"})
    auth.store.clear()
    assert auth.store.load() is None


def test_spotify_poll_returns_track_and_album_art():
    bot.spotify_auth = Mock()
    bot.spotify_auth.access_token.return_value = "access-for-test"
    response = Mock(status_code=200)
    response.json.return_value = {
        "is_playing": True,
        "item": {
            "id": "track-1",
            "name": "Example Track",
            "artists": [{"name": "Example Artist"}],
            "album": {"images": [{"url": "https://image.test/art.jpg"}]},
        },
    }
    with patch("spotify_discord_avatar.requests.get", return_value=response) as get:
        assert bot.get_current_track_info() == (
            "track-1", "https://image.test/art.jpg", "Example Track", "Example Artist"
        )
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer access-for-test"


@pytest.mark.parametrize("payload,status", [({"is_playing": False, "item": None}, 200), ({}, 204)])
def test_spotify_poll_reports_no_playback(payload, status):
    bot.spotify_auth = Mock()
    bot.spotify_auth.access_token.return_value = "access-for-test"
    response = Mock(status_code=status)
    response.json.return_value = payload
    with patch("spotify_discord_avatar.requests.get", return_value=response):
        assert bot.get_current_track_info() == (None, None, None, None)


def test_new_track_updates_avatar_and_listening_presence(monkeypatch):
    monkeypatch.setattr(bot, "last_track_id", None)
    fake_client = Mock()
    fake_client.wait_until_ready = AsyncMock()
    fake_client.is_closed.side_effect = [False, False, True]
    fake_client.user.edit = AsyncMock()
    fake_client.change_presence = AsyncMock()
    monkeypatch.setattr(bot, "client", fake_client)
    monkeypatch.setattr(
        bot,
        "get_current_track_info",
        lambda: ("track-1", "https://image.test/art.jpg", "Example Track", "Example Artist"),
    )
    image_response = Mock(content=b"image-bytes")
    image_response.raise_for_status.return_value = None

    async def inline_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "to_thread", inline_to_thread)
    monkeypatch.setattr(bot.asyncio, "sleep", no_wait)
    with patch("spotify_discord_avatar.requests.get", return_value=image_response):
        asyncio.run(bot.update_avatar_loop())

    fake_client.user.edit.assert_awaited_once_with(avatar=b"image-bytes")
    activity = fake_client.change_presence.await_args.kwargs["activity"]
    assert activity.type == bot.discord.ActivityType.listening
    assert activity.name == "Example Track – Example Artist"
    assert bot.last_track_id == "track-1"


def test_playback_stopping_clears_presence_once_without_avatar_change(monkeypatch):
    monkeypatch.setattr(bot, "last_track_id", "track-1")
    fake_client = Mock()
    fake_client.wait_until_ready = AsyncMock()
    fake_client.is_closed.side_effect = [False, True]
    fake_client.user.edit = AsyncMock()
    fake_client.change_presence = AsyncMock()
    monkeypatch.setattr(bot, "client", fake_client)
    monkeypatch.setattr(bot, "get_current_track_info", lambda: (None, None, None, None))

    async def inline_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "to_thread", inline_to_thread)
    monkeypatch.setattr(bot.asyncio, "sleep", no_wait)
    asyncio.run(bot.update_avatar_loop())

    fake_client.change_presence.assert_awaited_once_with(activity=None)
    fake_client.user.edit.assert_not_awaited()
    assert bot.last_track_id is None

