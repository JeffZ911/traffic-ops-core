"""One-time interactive OAuth bootstrap for GA4 + GSC collectors.

Run on your laptop (NOT in CI). It opens a browser, you click through the
consent screen, and a refresh_token is printed at the end.

Usage:
    python -m scripts.oauth_setup

Required env (in .env):
    GOOGLE_OAUTH_CLIENT_JSON   the full OAuth client JSON from GCP Console
                               (APIs & Services → Credentials → OAuth 2.0
                                Client IDs → Download JSON)

Output:
    GOOGLE_OAUTH_REFRESH_TOKEN=<long string>

Copy that line and:
    1. Add to .env so local collectors work
    2. Add to GitHub Secrets named GOOGLE_OAUTH_REFRESH_TOKEN
       (used by .github/workflows/content_daily.yml collector step)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from src.utils.google_oauth import DEFAULT_SCOPES


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    raw = os.getenv("GOOGLE_OAUTH_CLIENT_JSON") or ""
    if not raw:
        print("❌ GOOGLE_OAUTH_CLIENT_JSON missing from .env")
        print()
        print("How to get it:")
        print("  1. https://console.cloud.google.com → your project")
        print("  2. APIs & Services → Credentials")
        print("  3. Create Credentials → OAuth client ID → Application type: Web")
        print("     Authorized redirect URI: http://localhost:0/")
        print("  4. Download JSON, paste the WHOLE file (single-line ok) into .env as:")
        print("       GOOGLE_OAUTH_CLIENT_JSON='{...}'")
        return 2

    try:
        client_obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ GOOGLE_OAUTH_CLIENT_JSON is not valid JSON: {e}")
        return 2

    # google-auth-oauthlib InstalledAppFlow takes a path; write to a tmp file
    from google_auth_oauthlib.flow import InstalledAppFlow

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(client_obj, f)
        client_path = f.name

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            client_path, scopes=list(DEFAULT_SCOPES)
        )
        # access_type=offline + prompt=consent guarantees we get a refresh_token
        creds = flow.run_local_server(
            host="localhost",
            port=0,
            access_type="offline",
            prompt="consent",
            open_browser=True,
            authorization_prompt_message=(
                "👉 Browser should open. If not, copy this URL: {url}"
            ),
            success_message=(
                "Authorisation OK — you can close this browser tab and return "
                "to the terminal."
            ),
        )
    finally:
        try:
            os.unlink(client_path)
        except OSError:
            pass

    if not creds.refresh_token:
        print("❌ No refresh_token returned by Google.")
        print("   This usually means the OAuth app has already been granted")
        print("   on this Google account. Either:")
        print("   1. Visit https://myaccount.google.com/permissions, revoke")
        print("      the app entry, and re-run this script, OR")
        print("   2. Use a different Google account.")
        return 1

    print()
    print("=" * 78)
    print("✅ refresh_token acquired. Copy the line below.")
    print("=" * 78)
    print()
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("Next steps:")
    print("  1. Add it to your local .env (so collectors run locally)")
    print("  2. Add it to GitHub Secrets in BOTH repos as GOOGLE_OAUTH_REFRESH_TOKEN")
    print("     (only traffic-ops-core needs it for now)")
    print()
    print("It does NOT expire unless you revoke at myaccount.google.com.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
