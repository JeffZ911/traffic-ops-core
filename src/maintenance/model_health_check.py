"""Weekly health check for every model in `model_catalog`.

For each row with status in ('active', 'preview'):
  - text:  send a 'ping' prompt with max_tokens=10
  - image: send a tiny image-generation prompt
  - on success: bump last_verified_at, clear last_verify_error
  - on "model not found"/404 etc: status='deprecated' + email alert
  - on quota / transient: keep status, set last_verify_error for visibility
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.db.model_catalog_client import list_active_models, mark_deprecated
from src.utils.llm import LLMError, get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


# Heuristic substrings that distinguish a hard "model gone" from a
# transient quota / rate-limit / connection blip.
PERMANENT_SIGNATURES = (
    "404",
    "not found",
    "not_found",
    "does not exist",
    "is not supported",
    "is not available",
    "deprecated",
    "decommission",
    "unsupported",
)


def _is_permanent(err_msg: str) -> bool:
    s = err_msg.lower()
    return any(sig in s for sig in PERMANENT_SIGNATURES)


def _set_last_verified(model_id: str, ok: bool, err: Optional[str]) -> None:
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            update model_catalog
               set last_verified_at = now(),
                   last_verify_error = %s
             where model_id = %s
            """,
            (None if ok else (err or "")[:1000], model_id),
        )


def _check_text_model(model_id: str) -> tuple[bool, Optional[str]]:
    provider = get_llm_provider("gemini")
    try:
        resp = provider.generate(
            prompt="ping", model=model_id, max_tokens=10, temperature=0,
        )
        # tokens_in > 0 is enough — we're testing reachability, not output
        if resp.tokens_in <= 0:
            return False, "no token usage recorded — suspicious"
        return True, None
    except LLMError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_image_model(model_id: str) -> tuple[bool, Optional[str]]:
    """Send a minimal image prompt. Cost ~$0.04-$0.12 per check."""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return False, "GEMINI_API_KEY not set"
    client = genai.Client(api_key=api_key)
    try:
        cfg = types.GenerateContentConfig(response_modalities=["Image"])
        resp = client.models.generate_content(
            model=model_id, contents="a single red dot on white", config=cfg,
        )
        # Look for any image part
        cands = getattr(resp, "candidates", None) or []
        for c in cands:
            content = getattr(c, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "inline_data", None):
                    return True, None
        return False, "no image part in response"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    print(f"🔬 Model health check — {datetime.now(timezone.utc).isoformat()}")
    print()

    rows = list_active_models()
    if not rows:
        print("No active/preview models. Nothing to check.")
        return 0

    deprecated: list[tuple[str, str]] = []
    transient: list[tuple[str, str]] = []
    healthy: list[str] = []

    for m in rows:
        label = f"{m.provider}:{m.model_id}  ({m.modality.value})"
        print(f"▶ {label}")

        if m.modality.value == "text":
            ok, err = _check_text_model(m.model_id)
        elif m.modality.value == "image":
            ok, err = _check_image_model(m.model_id)
        else:
            print(f"   ⏭️  skip — modality={m.modality.value} not testable")
            continue

        if ok:
            _set_last_verified(m.model_id, True, None)
            healthy.append(m.model_id)
            print(f"   ✅ healthy")
            continue

        # Failure path
        _set_last_verified(m.model_id, False, err)
        if _is_permanent(err or ""):
            mark_deprecated(m.model_id, (err or "")[:1000])
            deprecated.append((m.model_id, err or ""))
            print(f"   ❌ DEPRECATED: {err[:160] if err else ''}")
        else:
            transient.append((m.model_id, err or ""))
            print(f"   ⚠️  transient (kept active): {err[:160] if err else ''}")

    # Summary
    print()
    print("=" * 78)
    print("=== Summary ===")
    print(f"  healthy    : {len(healthy)}")
    print(f"  transient  : {len(transient)}")
    print(f"  deprecated : {len(deprecated)}")
    print("=" * 78)

    # Alert on deprecation
    if deprecated and os.getenv("SMTP_HOST"):
        try:
            from src.utils.send_alert import send_alert

            body = "Models marked deprecated in this run:\n\n"
            for model_id, err in deprecated:
                body += f"  • {model_id}\n    error: {err[:300]}\n\n"
            body += (
                "Action: open the dashboard, switch sites away from these "
                "models. Or update model_catalog with the replacement model_id."
            )
            send_alert(
                subject=f"{len(deprecated)} model(s) deprecated",
                body=body,
                severity="critical",
            )
            print(f"📧 alert sent for {len(deprecated)} deprecated model(s)")
        except Exception as e:
            print(f"⚠️  alert failed: {type(e).__name__}: {e}")

    return 1 if deprecated else 0


if __name__ == "__main__":
    sys.exit(main())
