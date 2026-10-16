"""UDJ: Discord listening presence and avatar updates from Spotify playback."""
import asyncio
import os
import signal

import discord
import requests
from dotenv import load_dotenv

from auth import SpotifyAuth, TokenStore, TokenStoreError

load_dotenv("/etc/udj/udj.env")
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
REDIRECT_URI = os.getenv(
    "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
).strip()
DATA_DIR = os.getenv("UDJ_DATA_DIR", "./data")
ENCRYPTION_KEY = os.getenv("UDJ_TOKEN_ENCRYPTION_KEY", "").strip()
POLL_INTERVAL = 20

spotify_auth: SpotifyAuth | None = None

intents = discord.Intents.none()
client = discord.Client(intents=intents)
last_track_id = None
last_avatar_track_id = None
last_presence_track_id = None


class SpotifyAuthorizationRequired(Exception):
    """The stored Spotify grant is missing, expired, or revoked."""


class SpotifyPollingError(Exception):
    """Spotify could not be queried; playback state is unknown."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _rate_limit_delay(response) -> float:
    raw = getattr(response, "headers", {}).get("Retry-After")
    try:
        return max(POLL_INTERVAL, float(raw))
    except (TypeError, ValueError):
        return 60.0


def validate_runtime_config() -> None:
    required = ("DISCORD_TOKEN", "SPOTIFY_CLIENT_ID", "UDJ_TOKEN_ENCRYPTION_KEY")
    missing = [name for name in required if not os.getenv(name, "").strip() or os.getenv(name, "").strip() == "***PASTEHERE***"]
    if missing:
        raise RuntimeError("Set required UDJ environment variable(s): " + ", ".join(missing))


def get_current_track_info():
    """Return the current track tuple; raise if playback state is unknown."""
    if spotify_auth is None:
        raise SpotifyAuthorizationRequired("Spotify has not been authorized")
    try:
        access_token = spotify_auth.access_token()
    except TokenStoreError as exc:
        raise SpotifyPollingError("Encrypted Spotify token storage is unreadable") from exc
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        delay = _rate_limit_delay(response) if getattr(response, "status_code", None) == 429 else None
        raise SpotifyPollingError("Spotify token refresh failed", delay) from exc
    if not access_token:
        raise SpotifyAuthorizationRequired("Spotify authorization is required")

    def fetch(token):
        return requests.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

    try:
        response = fetch(access_token)
        if response.status_code == 401:
            # The provider may reject an access token before its locally stored
            # expiry time. Refresh once, then retry the playback request.
            access_token = spotify_auth.access_token(force_refresh=True)
            if not access_token:
                raise SpotifyAuthorizationRequired(
                    "Spotify authorization expired or was revoked"
                )
            response = fetch(access_token)
        if response.status_code == 429:
            raise SpotifyPollingError("Spotify rate limit reached", _rate_limit_delay(response))
        if response.status_code == 204:
            return None, None, None, None
        if response.status_code == 401:
            raise SpotifyAuthorizationRequired(
                "Spotify rejected the refreshed access token; run authorization setup again"
            )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SpotifyPollingError("Spotify playback request failed") from exc

    item = data.get("item") if data.get("is_playing") else None
    if not item:
        return None, None, None, None
    artists = item.get("artists", [])
    images = item.get("album", {}).get("images", [])
    return (
        item.get("id"),
        images[0].get("url") if images else None,
        item.get("name", "Unknown track"),
        artists[0].get("name", "Unknown artist") if artists else "Unknown artist",
    )


async def update_avatar_loop():
    """Keep the original 20-second polling and track-change behavior."""
    global last_track_id, last_avatar_track_id, last_presence_track_id
    await client.wait_until_ready()
    print("[Discord] UDJ is ready.")
    last_poll_warning = None
    while not client.is_closed():
        try:
            track = await asyncio.to_thread(get_current_track_info)
        except SpotifyAuthorizationRequired:
            warning = "Spotify authorization is missing or expired; rerun spotify_auth_setup.py."
            retry_delay = POLL_INTERVAL
        except SpotifyPollingError as exc:
            cause = exc.__cause__
            warning = f"Spotify is temporarily unavailable ({type(cause or exc).__name__})."
            retry_delay = exc.retry_after or POLL_INTERVAL
        else:
            warning = None
            retry_delay = POLL_INTERVAL
            if track[0]:
                track_id, artwork_url, track_name, artist_name = track
                if track_id != last_track_id:
                    print(f"[Spotify] New track: {track_name} – {artist_name}")
                if artwork_url and track_id != last_avatar_track_id:
                    try:
                        image = await asyncio.to_thread(
                            requests.get, artwork_url, timeout=10
                        )
                        image.raise_for_status()
                        await client.user.edit(avatar=image.content)
                        last_avatar_track_id = track_id
                        print("[Discord] Avatar updated.")
                    except (requests.RequestException, discord.DiscordException) as exc:
                        print(f"[Discord] Avatar update failed ({type(exc).__name__}); will retry.")
                elif not artwork_url:
                    last_avatar_track_id = track_id
                if track_id != last_presence_track_id:
                    try:
                        await client.change_presence(
                            activity=discord.Activity(
                                type=discord.ActivityType.listening,
                                name=f"{track_name} – {artist_name}",
                            )
                        )
                        last_presence_track_id = track_id
                    except discord.DiscordException as exc:
                        print(f"[Discord] Presence update failed ({type(exc).__name__}); will retry.")
                last_track_id = track_id
            elif last_track_id is not None:
                try:
                    await client.change_presence(activity=None)
                    last_track_id = None
                    last_presence_track_id = None
                except discord.DiscordException as exc:
                    print(f"[Discord] Could not clear stopped presence ({type(exc).__name__}); will retry.")

        if warning and warning != last_poll_warning:
            print(f"[Spotify] {warning}")
        last_poll_warning = warning
        await asyncio.sleep(retry_delay)


@client.event
async def on_ready():
    print(f"[Discord] Logged in as {client.user}")
    if not getattr(client, "_udj_loop_started", False):
        client._udj_loop_started = True
        asyncio.create_task(update_avatar_loop())


async def run_bot():
    """Run Discord with a clean close on systemd's SIGTERM and on Ctrl+C."""
    loop = asyncio.get_running_loop()
    if os.name == "posix":
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(client.close()))
    try:
        await client.start(DISCORD_TOKEN)
    finally:
        await client.close()


if __name__ == "__main__":
    validate_runtime_config()
    token_store = TokenStore(os.path.join(DATA_DIR, "udj.sqlite3"), ENCRYPTION_KEY)
    token_store.load()  # Fail with a clear startup error before connecting to Discord.
    spotify_auth = SpotifyAuth(token_store, SPOTIFY_CLIENT_ID, REDIRECT_URI)
    asyncio.run(run_bot())

