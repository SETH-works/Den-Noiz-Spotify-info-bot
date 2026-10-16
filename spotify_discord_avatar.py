import os
import asyncio
import requests
import discord
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()

# ============ CONFIG ============

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE = "user-read-currently-playing"
POLL_INTERVAL = 20

# ============ SPOTIFY AUTH ============

auth_manager = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SPOTIFY_SCOPE,
    cache_path=".spotify_cache",
    open_browser=False,
)

spotify = Spotify(auth_manager=auth_manager)

# ============ DISCORD BOT ============

intents = discord.Intents.none()
client = discord.Client(intents=intents)

last_track_id = None


def get_current_track_info():
    try:
        data = spotify.current_user_playing_track()
    except Exception as e:
        print(f"[Spotify] Error: {e}")
        return None, None, None, None

    if not data:
        return None, None, None, None

    if not data.get("is_playing"):
        return None, None, None, None

    item = data.get("item")
    if not item:
        return None, None, None, None

    track_id = item.get("id")
    track_name = item.get("name")
    artists = item.get("artists", [])
    artist_name = artists[0]["name"] if artists else "Unknown Artist"

    album = item.get("album", {})
    images = album.get("images", [])
    artwork_url = images[0]["url"] if images else None

    return track_id, artwork_url, track_name, artist_name


async def update_avatar_loop():
    global last_track_id
    await client.wait_until_ready()

    print("[Discord] Bot is ready.")

    while not client.is_closed():
        track_id, artwork_url, track_name, artist_name = get_current_track_info()

        if track_id and track_id != last_track_id:
            # New song detected
            print(f"[Spotify] New track: {track_name} – {artist_name}")

            # Update avatar
            try:
                if artwork_url:
                    img_data = requests.get(artwork_url).content
                    await client.user.edit(avatar=img_data)
                    print("[Discord] Avatar updated.")
            except Exception as e:
                print(f"[Discord] Failed to update avatar: {e}")

            # Update status to "Listening to: Song – Artist"
            try:
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name=f"{track_name} – {artist_name}"
                )
                await client.change_presence(activity=activity)
                print("[Discord] Presence updated.")
            except Exception as e:
                print(f"[Discord] Failed to update presence: {e}")

            last_track_id = track_id

        elif not track_id and last_track_id is not None:
            # Nothing playing anymore -> clear presence once
            try:
                await client.change_presence(activity=None)
                print("[Discord] Cleared presence (no track playing).")
            except Exception as e:
                print(f"[Discord] Failed to clear presence: {e}")
            last_track_id = None

        await asyncio.sleep(POLL_INTERVAL)


@client.event
async def on_ready():
    print(f"[Discord] Logged in as {client.user}")
    client.loop.create_task(update_avatar_loop())


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
