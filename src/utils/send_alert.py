"""Lightweight SMTP alert dispatcher.

Reads SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / ALERT_RECIPIENT_EMAIL
from env. CLI usable as `python -m src.utils.send_alert ...` from CI failure
hooks.

Usage as a module:
    from src.utils.send_alert import send_alert
    send_alert("[ntecodex] something broke", "body...", severity="critical")

Usage as CLI:
    python -m src.utils.send_alert \
        --type=pipeline_failure --workflow=content_daily \
        --message="step XYZ failed: ..."
"""

from __future__ import annotations

import argparse
import os
import smtplib
import socket
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str) -> str:
    v = os.getenv(name) or ""
    if not v:
        raise RuntimeError(f"{name} not set")
    return v


def send_alert(subject: str, body: str, severity: str = "info") -> None:
    """Send a plain-text email. Raises if SMTP creds missing."""
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT"))
    user = _env("SMTP_USER")
    pw = _env("SMTP_PASS")
    rcpt = _env("ALERT_RECIPIENT_EMAIL")

    msg = EmailMessage()
    full_subject = f"[ntecodex/{severity}] {subject}"
    msg["Subject"] = full_subject
    msg["From"] = user
    msg["To"] = rcpt
    body_full = (
        f"Severity: {severity}\n"
        f"Host:     {socket.gethostname()}\n"
        f"Time:     {datetime.now(timezone.utc).isoformat()}\n"
        f"---\n"
        f"{body}\n"
    )
    msg.set_content(body_full)

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)


def _cli() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--type", default="generic",
                   help="e.g. pipeline_failure, model_deprecated")
    p.add_argument("--workflow", default="",
                   help="Workflow name for context")
    p.add_argument("--message", default="",
                   help="Body. If empty, reads from stdin")
    p.add_argument("--severity", default="critical",
                   choices=["info", "warning", "critical"])
    p.add_argument("--subject", default=None,
                   help="Override subject; default is auto-generated")
    args = p.parse_args()

    body = args.message or sys.stdin.read()
    subject = args.subject or (
        f"{args.workflow or 'pipeline'} {args.type}".strip()
    )

    try:
        send_alert(subject, body, severity=args.severity)
    except Exception as e:
        print(f"❌ alert send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"✅ alert sent: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
