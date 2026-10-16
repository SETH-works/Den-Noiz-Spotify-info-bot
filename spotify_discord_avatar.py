"""UDJ: Discord listening presence and avatar updates from Spotify playback."""
import asyncio
import os

import discord
import requests
from dotenv import load_dotenv

from auth import SpotifyAuth, TokenStore

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


def get_current_track_info():
    """Return the current track tuple; no playback and API errors both mean idle."""
    if spotify_auth is None:
        return None, None, None, None
    try:
        access_token = spotify_auth.access_token()
    except requests.RequestException as exc:
        print(f"[Spotify] Token refresh failed ({type(exc).__name__}).")
        return None, None, None, None
    if not access_token:
        return None, None, None, None
    try:
        response = requests.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if response.status_code in (204, 401):
            return None, None, None, None
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        print(f"[Spotify] Request failed ({type(exc).__name__}).")
        return None, None, None, None

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
    global last_track_id
    await client.wait_until_ready()
    print("[Discord] UDJ is ready.")
    while not client.is_closed():
        track_id, artwork_url, track_name, artist_name = await asyncio.to_thread(
            get_current_track_info
        )
        if track_id and track_id != last_track_id:
            print(f"[Spotify] New track: {track_name} – {artist_name}")
            if artwork_url:
                try:
                    image = await asyncio.to_thread(
                        requests.get, artwork_url, timeout=10
                    )
                    image.raise_for_status()
                    await client.user.edit(avatar=image.content)
                    print("[Discord] Avatar updated.")
                except (requests.RequestException, discord.HTTPException) as exc:
                    print(f"[Discord] Avatar update failed ({type(exc).__name__}).")
            try:
                await client.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.listening,
                        name=f"{track_name} – {artist_name}",
                    )
                )
            except discord.HTTPException as exc:
                print(f"[Discord] Presence update failed ({type(exc).__name__}).")
            last_track_id = track_id
        elif not track_id and last_track_id is not None:
            try:
                await client.change_presence(activity=None)
            except discord.HTTPException:
                pass
            last_track_id = None
        await asyncio.sleep(POLL_INTERVAL)


@client.event
async def on_ready():
    print(f"[Discord] Logged in as {client.user}")
    if not getattr(client, "_udj_loop_started", False):
        client._udj_loop_started = True
        asyncio.create_task(update_avatar_loop())


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN in the environment or local .env file")
    if not SPOTIFY_CLIENT_ID:
        raise RuntimeError("Set SPOTIFY_CLIENT_ID in the environment or local .env file")
    if not ENCRYPTION_KEY:
        raise RuntimeError("Set UDJ_TOKEN_ENCRYPTION_KEY in the environment or local .env file")
    token_store = TokenStore(os.path.join(DATA_DIR, "udj.sqlite3"), ENCRYPTION_KEY)
    spotify_auth = SpotifyAuth(token_store, SPOTIFY_CLIENT_ID, REDIRECT_URI)
    client.run(DISCORD_TOKEN)
