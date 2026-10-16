"""UDJ: Discord presence and avatar updates from a Spotify account."""
import asyncio
import os
import threading

import discord
import requests
from dotenv import load_dotenv
from waitress import serve

from auth import SpotifyAuth, TokenStore, create_app

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
if not all((DISCORD_TOKEN, CLIENT_ID, REDIRECT_URI)):
    raise RuntimeError("Set DISCORD_TOKEN, SPOTIFY_CLIENT_ID and SPOTIFY_REDIRECT_URI")

data_dir = os.getenv("UDJ_DATA_DIR", "./data")
store = TokenStore(os.path.join(data_dir, "udj.sqlite3"), os.getenv("UDJ_TOKEN_ENCRYPTION_KEY", ""))
spotify_auth = SpotifyAuth(store, CLIENT_ID, REDIRECT_URI)
web_app = create_app(spotify_auth)

intents = discord.Intents.none()
client = discord.Client(intents=intents)
last_track_id = None


def get_current_track_info():
    try:
        token = spotify_auth.access_token()
    except requests.RequestException as exc:
        print(f"[Spotify] Token refresh failed ({type(exc).__name__}).")
        return None, None, None, None
    if not token:
        return None, None, None, None
    try:
        response = requests.get("https://api.spotify.com/v1/me/player/currently-playing", headers={"Authorization": f"Bearer {token}"}, timeout=10)
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
    return item.get("id"), images[0].get("url") if images else None, item.get("name", "Unknown track"), artists[0].get("name", "Unknown artist") if artists else "Unknown artist"


async def update_avatar_loop():
    global last_track_id
    await client.wait_until_ready()
    print("[Discord] UDJ is ready.")
    while not client.is_closed():
        track_id, artwork_url, track_name, artist_name = await asyncio.to_thread(get_current_track_info)
        if track_id and track_id != last_track_id:
            print(f"[Spotify] New track: {track_name} – {artist_name}")
            if artwork_url:
                try:
                    image = await asyncio.to_thread(requests.get, artwork_url, timeout=10)
                    image.raise_for_status()
                    await client.user.edit(avatar=image.content)
                    print("[Discord] Avatar updated.")
                except (requests.RequestException, discord.HTTPException) as exc:
                    print(f"[Discord] Avatar update failed ({type(exc).__name__}).")
            try:
                await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{track_name} – {artist_name}"))
            except discord.HTTPException as exc:
                print(f"[Discord] Presence update failed ({type(exc).__name__}).")
            last_track_id = track_id
        elif not track_id and last_track_id is not None:
            try:
                await client.change_presence(activity=None)
            except discord.HTTPException:
                pass
            last_track_id = None
        await asyncio.sleep(20)


@client.event
async def on_ready():
    print(f"[Discord] Logged in as {client.user}")
    if not getattr(client, "_udj_loop_started", False):
        client._udj_loop_started = True
        asyncio.create_task(update_avatar_loop())


def run_web():
    port = int(os.getenv("PORT", "8888"))
    serve(web_app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    threading.Thread(target=run_web, name="udj-oauth-web", daemon=True).start()
    client.run(DISCORD_TOKEN)

