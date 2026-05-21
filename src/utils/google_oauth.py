"""OAuth user-credential helper for GA4 + Google Search Console collectors.

Why user credentials (not service account):
  - GA4: a service account can be granted Viewer on a property — works, but
    requires per-property setup
  - GSC: service accounts CANNOT access Search Console at all; only
    user-OAuth tokens. So we standardise on user OAuth for both.

How it works:
  1. One-time bootstrap (interactive, on operator's laptop):
       python -m scripts.oauth_setup
     → opens browser, you grant scopes, refresh_token is printed.
     You paste it into GitHub Secrets as GOOGLE_OAUTH_REFRESH_TOKEN.
  2. Every collector run (in CI):
       creds = get_user_credentials(scopes=[...])
     Reads GOOGLE_OAUTH_CLIENT_JSON + GOOGLE_OAUTH_REFRESH_TOKEN from env,
     rebuilds Credentials, auto-refreshes the access token.

The refresh_token never expires unless you revoke at myaccount.google.com.
"""

from __future__ import annotations

import json
import os
from typing import Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# Default scopes for the COLLECTORS (read-only). The existing refresh
# token is bound to this exact set — do not broaden it here or token
# refresh fails with invalid_scope.
DEFAULT_SCOPES: Sequence[str] = (
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
)

# Write scope needed for sitemaps.submit (scripts/resubmit_sitemap.py).
# Re-run scripts.oauth_setup with WRITE_SCOPES to mint a token that can
# both read (collectors) and submit sitemaps. Until then the existing
# readonly token keeps the collectors working; only the sitemap-submit
# call 403s.
WRITE_SCOPES: Sequence[str] = (
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters",
)


def _load_client_secret() -> dict:
    """Parse the OAuth client JSON env var into a dict."""
    raw = os.getenv("GOOGLE_OAUTH_CLIENT_JSON") or ""
    if not raw:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_JSON not set. Get it from "
            "Google Cloud Console → APIs & Services → Credentials → "
            "OAuth 2.0 Client IDs → download JSON, then paste the full "
            "JSON string into env."
        )
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_OAUTH_CLIENT_JSON is not valid JSON: {e}")
    # Google returns either {"web": {...}} or {"installed": {...}}
    for k in ("web", "installed"):
        if k in obj:
            return obj[k]
    raise RuntimeError(
        "GOOGLE_OAUTH_CLIENT_JSON: expected a key 'web' or 'installed' at top level"
    )


def get_user_credentials(scopes: Sequence[str] = DEFAULT_SCOPES) -> Credentials:
    """Build a refreshable user Credentials object from env vars.

    Required env vars:
      - GOOGLE_OAUTH_CLIENT_JSON    : full OAuth client JSON
      - GOOGLE_OAUTH_REFRESH_TOKEN  : refresh_token from a prior consent flow
    """
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN") or ""
    if not refresh_token:
        raise RuntimeError(
            "GOOGLE_OAUTH_REFRESH_TOKEN not set. Run "
            "`python -m scripts.oauth_setup` once locally to obtain one."
        )

    secret = _load_client_secret()
    creds = Credentials(
        token=None,                       # forces a refresh on first use
        refresh_token=refresh_token,
        token_uri=secret.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=list(scopes),
    )
    creds.refresh(Request())
    return creds
