# UDJ

UDJ updates a Discord bot's avatar and listening status from the connected Spotify account's currently playing track. Spotify access uses Authorization Code with PKCE, with an exact HTTPS redirect URI on Render or a loopback IPv4 URI for local development.

## Local setup

Install Python 3.10 or newer, then:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set local values in `.env`. In the Spotify Developer Dashboard, add the exact value of `SPOTIFY_REDIRECT_URI` to the app's Redirect URIs. For local development use `http://127.0.0.1:8888/callback` (Spotify requires an explicit loopback IP; `localhost` is not accepted). Create a Discord bot and copy its token into `.env`.

Generate two independent values and place them in `.env`:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use those for `UDJ_TOKEN_ENCRYPTION_KEY` and a strong `UDJ_ADMIN_PASSWORD`; set `UDJ_ADMIN_USER` to your chosen admin name. Never share or commit `.env`, the encryption key, or the `data/` directory. The encryption key must stay stable: losing it makes stored tokens unreadable.

Start with `python spotify_discord_avatar.py`, then visit `http://127.0.0.1:8888/connect` and authenticate with the UDJ admin username/password when prompted. Spotify will ask you to authorize `user-read-currently-playing`. A successful callback stores encrypted tokens locally; access tokens refresh automatically. To disconnect, send an authenticated `POST` to `/disconnect` with the `X-UDJ-Action: disconnect` header (for example, `curl.exe -u admin -H "X-UDJ-Action: disconnect" http://127.0.0.1:8888/disconnect -X POST`, which prompts for the password). This deletes local tokens; also remove UDJ from your Spotify account's app access page to revoke the grant.

## Render deployment

The blueprint runs the Discord bot and OAuth web routes in one Render web service. A persistent disk at `/var/data` holds the encrypted token database across deploys. The disk requires a paid Render plan; do not deploy this configuration on an ephemeral filesystem because a restart would lose the refresh token. Deploy the blueprint, then set every `sync: false` value in the Render dashboard. Set:

- `SPOTIFY_REDIRECT_URI` to `https://YOUR-SERVICE.onrender.com/callback` and add that exact URL to the Spotify app.
- `UDJ_DATA_DIR` to `/var/data` (the blueprint sets this).
- Fresh unique values for the Discord token, Spotify client ID, admin credentials and Fernet encryption key.

Open `https://YOUR-SERVICE.onrender.com/connect` and sign in with the UDJ admin credentials, then approve Spotify access. Protect the admin password; anyone with it can replace the linked Spotify account or disconnect it. The Spotify client secret is not used: PKCE is designed for this client-secret-free flow.

## Tests

Run `python -m pytest`. Tests cover PKCE authorization parameters, single-use CSRF state, encrypted token persistence, authenticated management routes, and disconnect. Live Spotify and Discord behavior requires your own credentials and is not simulated by the test suite.

## Credential history

The previously public repository history contains an old `.spotify_cache` with Spotify token values. Removing it from the current branch does not erase the historical copies. Treat those credentials as exposed, revoke the old app authorization, and separately review a history rewrite and force-push before considering the historical exposure purged.

