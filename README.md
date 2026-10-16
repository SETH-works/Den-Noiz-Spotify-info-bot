# UDJ

UDJ is a personal Discord bot. Every 20 seconds it checks the linked Spotify account's current playback. When the track changes, it updates the bot avatar to the album artwork and sets Discord's listening presence to `Track – Artist`. When playback stops or Spotify reports no current track, it clears the listening presence. It does not change the avatar when playback stops.

Spotify uses Authorization Code with PKCE. The one-time setup command listens only on an explicit loopback address, handles one callback, saves the encrypted token response, and exits. The bot itself opens no HTTP port. No Spotify client secret, hosted sign-in page, admin account, or public callback service is needed.

## Requirements and configuration

- Python 3.10 or newer
- One Discord bot token
- A Spotify Developer app and its client ID
- A Linux host that stays online for unattended operation (SethLinux is the intended host)

Create a virtual environment and install the runtime requirements:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux: . .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to a private environment file and set `DISCORD_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_REDIRECT_URI`, `UDJ_TOKEN_ENCRYPTION_KEY`, and `UDJ_DATA_DIR`. For local runs, use `UDJ_DATA_DIR=./data`; on SethLinux use `/var/lib/udj`. Generate a Fernet key once:

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use `http://127.0.0.1:8888/callback` as the redirect URI and add that exact value to the Spotify Developer Dashboard. Spotify requires an explicit loopback IP; `localhost` is not accepted. PKCE means the Spotify client secret is not used. Keep the environment file private (mode `600` on Linux); never commit it, the encryption key, or the token database. Keep the encryption key stable and backed up separately from the encrypted database.

## One-time Spotify sign-in

Install the Python requirements and prepare the persistent data directory before setup. On a local computer, run `python spotify_auth_setup.py`; it starts the callback listener only for this command, prints an authorization URL, and exits after success. Open that URL in a browser and approve access. For SethLinux, use an SSH local-port forward so the browser's loopback callback reaches the server's temporary listener:

1. On your PC, keep this tunnel open: `ssh -N -L 8888:127.0.0.1:8888 seth@SethLinux` (replace `SethLinux` with the host name or address you normally use).
2. In a second SSH session, run `python /opt/udj/spotify_auth_setup.py` as the UDJ service user, with the same private environment values and data directory that the service will use.
3. Open the printed Spotify authorization URL in your PC browser. Spotify returns to `127.0.0.1:8888`, and SSH forwards the callback to the temporary server listener. The listener closes when it completes.
4. Close the SSH tunnel after setup finishes.

The local callback state and PKCE verifier exist only in setup process memory. The resulting token response is Fernet-encrypted in SQLite under `UDJ_DATA_DIR`; the database persists through process restarts. Each successful access-token refresh is saved, including a replacement refresh token if Spotify returns one. [Spotify documents a six-month lifetime](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens) for refresh tokens issued to Developer Dashboard apps, measured from user authorization; refreshing does not extend it. That is Spotify's provider policy, not a duration specified by OAuth generally. UDJ does not add its own expiration timer: it handles the provider's `invalid_grant` response by clearing unusable tokens, after which run setup again to authorize.

To remove UDJ's local token record, run `python spotify_auth_setup.py --disconnect` with the same environment and data directory. To revoke Spotify's grant as well, remove UDJ in your Spotify account's app access settings.

## Run as a Linux service

`deploy/udj.service` is a systemd unit template for the existing `seth` account. It uses `/etc/udj/udj.env` for secrets and `/var/lib/udj` for persistent encrypted token storage. For a future installation, copy `.env.example` to `/etc/udj/udj.env`, set `UDJ_DATA_DIR=/var/lib/udj`, and protect the file with owner `seth` and mode `600`. Create `/var/lib/udj` owned by `seth` with mode `700`, install the app under `/opt/udj`, and create `/opt/udj/.venv` with the requirements above. Complete the one-time OAuth setup as `seth`, then review and install the unit. Do not place secrets in the unit file or command line. This service is separate from Minecraft and does not modify or restart any existing server service.

The unit is provided as a deployment aid only. This change does not install it or change SethLinux. Render is not the target deployment: its current free offering does not provide an always-on background worker with persistent disk. A paid Render worker could run without an auth endpoint, but it would add recurring cost and token provisioning steps without benefiting this single-account bot.

## Tests

Install test dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m pytest`. Tests exercise PKCE/state validation and the temporary callback, encrypted token persistence across store recreation, token refresh and replacement, Spotify polling, Discord avatar updates, listening presence, and stopping behavior. Live Spotify and Discord behavior requires credentials and is not simulated by the suite.

## Credential history

The previously public Git history contains an old `.spotify_cache` with Spotify token values. Removing it from the current tree does not erase historical copies. Treat those values as exposed. The history cleanup remains separate and requires separately approved history rewriting and force-updating.

