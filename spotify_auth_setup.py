"""Run UDJ's one-time, loopback-only Spotify authorization setup."""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from auth import SpotifyAuth, TokenStore, run_local_authorization


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value == "***PASTEHERE***":
        raise RuntimeError(f"Set {name} in the environment or local .env file")
    return value


def main() -> int:
    load_dotenv("/etc/udj/udj.env")
    load_dotenv()
    parser = argparse.ArgumentParser(description="Connect or disconnect UDJ's Spotify account")
    parser.add_argument(
        "--disconnect",
        action="store_true",
        help="delete UDJ's locally stored Spotify tokens",
    )
    args = parser.parse_args()

    data_dir = Path(os.getenv("UDJ_DATA_DIR", "./data"))
    store = TokenStore(data_dir / "udj.sqlite3", required("UDJ_TOKEN_ENCRYPTION_KEY"))
    if args.disconnect:
        store.clear()
        print("UDJ's local Spotify tokens were deleted.")
        print("To revoke Spotify's grant too, remove UDJ from your Spotify account's app access page.")
        return 0

    auth = SpotifyAuth(
        store,
        required("SPOTIFY_CLIENT_ID"),
        os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip(),
    )
    try:
        run_local_authorization(auth)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Spotify setup failed: {exc}")
        return 1
    print("Spotify is connected. The temporary callback listener has stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
