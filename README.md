# UDJ

UDJ is a personal Discord bot. Every 20 seconds it checks the linked Spotify account's current playback. When the track changes, it updates the bot avatar to the album artwork and sets Discord's listening presence to `Track – Artist`. When playback stops or Spotify reports no current track, it clears the listening presence. It does not change the avatar when playback stops.

Spotify uses Authorization Code with PKCE. The one-time setup command listens only on an explicit loopback address, handles one callback, saves the encrypted token response, and exits. The bot itself opens no HTTP port. No Spotify client secret, hosted sign-in page, admin account, or public callback service is needed.

## Requirements and configuration

- Python 3.10 or newer
- One Discord bot token
- A Spotify Developer app and its client ID; Spotify Development Mode requires the app owner's active Premium subscription
- A Linux host that stays online for unattended operation (SethLinux is the intended host)

Create a virtual environment and install the runtime requirements:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux: . .venv/bin/activate
python -m pip install -r requirements.txt
```

The runtime reads these environment variables from the process environment, a local `.env`, or `/etc/udj/udj.env`:

- `DISCORD_TOKEN`: token for the UDJ bot application.
- `SPOTIFY_CLIENT_ID`: Spotify Developer app client ID. PKCE does not use a client secret.
- `SPOTIFY_REDIRECT_URI`: `http://127.0.0.1:8888/callback`.
- `UDJ_TOKEN_ENCRYPTION_KEY`: one Fernet key, kept private and stable across restarts.
- `UDJ_DATA_DIR`: persistent private data directory containing `udj.sqlite3`.

For local runs, use `UDJ_DATA_DIR=./data`; on SethLinux use `/var/lib/udj`. UDJ creates new data directories owner-only and refuses an existing POSIX data directory with group/other access. After creating `/etc/udj/udj.env` and installing the virtual environment below, generate the Fernet key directly into the protected environment file (the command prints no key):

```sh
sudo /opt/udj/.venv/bin/python - <<'PY'
from cryptography.fernet import Fernet
from pathlib import Path
path = Path("/etc/udj/udj.env")
text = path.read_text()
if "UDJ_TOKEN_ENCRYPTION_KEY=" not in text:
    with path.open("a") as env_file:
        env_file.write("UDJ_TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\n")
PY
```

Use `http://127.0.0.1:8888/callback` as the redirect URI and add that exact value to the Spotify Developer Dashboard. Spotify requires an explicit loopback IP; `localhost` is not accepted. PKCE means the Spotify client secret is not used. Keep the environment file private (on SethLinux, root-owned and readable only by the `udj` group); never commit it, the encryption key, or the token database. Keep the encryption key stable and back it up separately from the encrypted database. Losing the key makes the encrypted token database unusable.

## One-time Spotify sign-in

Install the Python requirements and prepare the persistent data directory before setup. On a local computer, run `python spotify_auth_setup.py`; it starts the callback listener only for this command, prints an authorization URL, and exits after success. Open that URL in a browser and approve access. For SethLinux, use an SSH local-port forward so the browser's loopback callback reaches the server's temporary listener:

1. On your PC, keep this tunnel open: `ssh -N -L 127.0.0.1:8888:127.0.0.1:8888 seth@SethLinux` (replace `SethLinux` with the host name or address you normally use).
2. In a second SSH session, run `sudo -u udj /opt/udj/.venv/bin/python /opt/udj/spotify_auth_setup.py`. The setup script reads `/etc/udj/udj.env` and writes to `/var/lib/udj` using the configured key.
3. Open the printed Spotify authorization URL in your PC browser. Spotify returns to `127.0.0.1:8888`, and SSH forwards the callback to the temporary server listener. The listener closes when it completes.
4. Close the SSH tunnel after setup finishes.

The local callback state and PKCE verifier exist only in setup process memory. The resulting token response is Fernet-encrypted in SQLite under `UDJ_DATA_DIR`; the database persists through process restarts. Each successful access-token refresh is saved, including a replacement refresh token if Spotify returns one. [Spotify documents a six-month lifetime](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens) for refresh tokens issued to Developer Dashboard apps, measured from user authorization; refreshing does not extend it. That is Spotify's provider policy, not a duration specified by OAuth generally. UDJ does not add its own expiration timer: it handles the provider's `invalid_grant` response by clearing unusable tokens, after which run setup again to authorize.

To remove UDJ's local token record, run `python spotify_auth_setup.py --disconnect` with the same environment and data directory. To revoke Spotify's grant as well, remove UDJ in your Spotify account's app access settings.

## First deployment checklist

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create a Web API app and add exactly `http://127.0.0.1:8888/callback` under Redirect URIs. Copy its client ID into `/etc/udj/udj.env`. UDJ requests only `user-read-currently-playing`; PKCE does not use a Spotify client secret. Spotify requires the explicit loopback IP and does not accept `localhost` here. Development Mode requires the app owner's active Premium subscription. Add the Spotify account you will authorize to the app's Users Management allowlist; without that, Spotify can allow login but reject API access with 403.
2. In the [Discord Developer Portal](https://discord.com/developers/applications), create the UDJ application, add a bot, and store its token in `/etc/udj/udj.env` as `DISCORD_TOKEN`. Install the bot in the Discord server where you want to see its profile and presence using OAuth2 scope `bot` and no guild permissions. UDJ reads no guild events, so leave privileged intents disabled.
3. On SethLinux, create the dedicated account and paths (these are instructions only; this audit has not run them):

   ```sh
   sudo useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin udj
   sudo install -d -o root -g root -m 0755 /opt/udj
   sudo install -d -o root -g udj -m 0750 /etc/udj
   sudo install -d -o udj -g udj -m 0700 /var/lib/udj
   sudo install -o root -g udj -m 0640 /dev/null /etc/udj/udj.env
   sudoedit /etc/udj/udj.env
   ```

   Put the following settings in that file, replacing placeholders locally in the editor:

   ```dotenv
   DISCORD_TOKEN=***PASTEHERE***
   SPOTIFY_CLIENT_ID=***PASTEHERE***
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
   UDJ_DATA_DIR=/var/lib/udj
   ```

   After copying the reviewed source into `/opt/udj`, install dependencies and generate the key without displaying it:

   ```sh
   sudo python3 -m venv /opt/udj/.venv
   sudo /opt/udj/.venv/bin/python -m pip install -r /opt/udj/requirements.txt
   sudo /opt/udj/.venv/bin/python - <<'PY'
   from cryptography.fernet import Fernet
   from pathlib import Path
   path = Path("/etc/udj/udj.env")
   text = path.read_text()
   if "UDJ_TOKEN_ENCRYPTION_KEY=" not in text:
       with path.open("a") as env_file:
           env_file.write("UDJ_TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\n")
   PY
   sudo chown -R root:root /opt/udj
   sudo chmod -R go-w /opt/udj
   ```

   Keep `/etc/udj/udj.env` root-owned, group `udj`, mode `0640`, and `/var/lib/udj` owned by `udj`, mode `0700`. Back up the encryption key securely and separately from the encrypted database.
4. Open the SSH tunnel and run the one-time Spotify setup above. The temporary callback binds only to `127.0.0.1`; SethLinux needs no inbound firewall rule or public callback.
5. With the bot stopped, run `sudo -u udj /opt/udj/.venv/bin/python /opt/udj/spotify_auth_setup.py --verify-refresh` twice. It reports only success or failure, saves a rotated token if Spotify returns one, and does not display credentials. Running it twice in separate processes checks persistence and real refresh acceptance.
6. Start the bot manually with `sudo -u udj /opt/udj/.venv/bin/python /opt/udj/spotify_discord_avatar.py`. Play a Spotify track and confirm the Discord avatar and `Track – Artist` presence update within about 20 seconds. Pause Spotify and confirm the presence clears while the avatar stays on the last album art. Stop UDJ with Ctrl+C. This step is the live-service check; automated tests use mocks.
7. After the manual check, install `deploy/udj.service`, run `sudo systemctl daemon-reload`, then `sudo systemctl enable --now udj.service`. Confirm `systemctl status udj.service` and `journalctl -u udj.service -n 50 --no-pager`; logs should not contain credentials. Test persistence across service restart with `sudo systemctl restart udj.service`, then confirm it reconnects and continues using `/var/lib/udj`.

## Run as a Linux service

`deploy/udj.service` runs as a dedicated, non-login `udj` account. It reads `/etc/udj/udj.env` and keeps encrypted tokens in systemd's persistent `/var/lib/udj` state directory (mode `0700`). It expects `/opt/udj` readable/executable by `udj` and `/etc/udj/udj.env` root-owned with group `udj` and mode `0640`. Set `UDJ_DATA_DIR=/var/lib/udj`. Install Python dependencies in `/opt/udj/.venv` before starting the service. The unit opens no inbound port. systemd restarts a failed process, limits rapid crash loops, and sends SIGTERM for a clean Discord shutdown.

Before enabling systemd, run UDJ manually once as `udj`: `sudo -u udj /opt/udj/.venv/bin/python /opt/udj/spotify_discord_avatar.py`. Confirm the Discord login and live behavior described below, then stop it with Ctrl+C. Only after that, install the unit, run `sudo systemctl daemon-reload`, enable and start `udj.service`, and confirm `systemctl status udj.service`. Do not run a second bot instance at the same time. These are future deployment instructions; the current audit has not changed SethLinux.

The unit is provided as a deployment aid only. This change does not install it or change SethLinux. Render is not the target deployment: its current free offering does not provide an always-on background worker with persistent disk. A paid Render worker could run without an auth endpoint, but it would add recurring cost and token provisioning steps without benefiting this single-account bot.

## Tests

Install test dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m pytest`. Tests exercise PKCE/state validation and callback handling, encrypted token persistence and corrupt/wrong-key handling, refresh rotation and invalid-grant behavior, missing config, Spotify outages and rate-limit backoff, Discord avatar/presence retry behavior, and playback stopping. Live Spotify and Discord behavior requires credentials and is not simulated by the suite.

## Credential history

The previously public Git history contains an old `.spotify_cache` with Spotify token values. Removing it from the current tree does not erase historical copies. Treat those values as exposed. The history cleanup remains separate and requires separately approved history rewriting and force-updating.

