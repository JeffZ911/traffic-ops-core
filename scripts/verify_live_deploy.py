"""QDF P4 — post-deploy live health check + best-effort auto-redeploy.

Manual §3.3.3 (巡检前端运行状态,前端崩溃立即重启). Google's QDF window is
worthless if the crawler hits a broken deploy. This runs right after the Deploy
step and confirms the live site is actually serving real HTML; if not, it
alerts and (with --redeploy) re-pushes the already-built dist once.

This guards the exact failure we've hit twice: a git-connected CF Pages project
with an empty build_command serving a broken/blank deploy that silently
overwrote a good wrangler upload.

Checks (the homepage + the newest published article):
  - HTTP 200
  - body is real HTML (has </html> or <title>, over a min length) — catches
    blank/SPA-error/Cloudflare-error pages that still return 200.

Usage:
  python -m scripts.verify_live_deploy --site quvii.com
  python -m scripts.verify_live_deploy --site quvii.com --redeploy \
      --dist /path/to/quvii-site/dist --project quvii-site
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_MIN_BYTES = 600
_UA = "Mozilla/5.0 (compatible; quvii-deploy-verifier/1.0)"


def _home_path(niche: str) -> str:
    # pixelmatch serves its blog under /blog; others at apex.
    return "/blog/" if niche == "ecommerce_tools" else "/"


def _check(url: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            body = resp.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    if len(body) < _MIN_BYTES:
        return False, f"body too small ({len(body)}B) — likely blank/error page"
    low = body.lower()
    if "</html>" not in low and "<title" not in low:
        return False, "no real HTML markers (</html>/<title)"
    return True, f"ok ({len(body)//1024}KB)"


def _alert(site: str, detail: str) -> None:
    try:
        subprocess.run(
            ["python", "-m", "src.utils.send_alert",
             "--type=deploy_broken", f"--workflow=verify_live_deploy",
             "--severity=critical",
             f"--subject=[{site}] live deploy looks BROKEN",
             f"--message={detail}"],
            check=False, timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  alert send failed: {type(e).__name__}")


def _redeploy(dist: str, project: str) -> bool:
    print(f"  ♻️  attempting redeploy of {dist} → CF Pages {project}")
    try:
        r = subprocess.run(
            ["npx", "--yes", "wrangler@latest", "pages", "deploy", dist,
             "--project-name", project, "--branch", "main",
             "--commit-dirty=true"],
            check=False, timeout=300, capture_output=True, text=True,
        )
        print((r.stdout or "")[-400:])
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  redeploy failed: {type(e).__name__}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--redeploy", action="store_true",
                    help="re-push the built dist once if the live check fails")
    ap.add_argument("--dist", default=None, help="path to built dist (for --redeploy)")
    ap.add_argument("--project", default=None, help="CF Pages project name (for --redeploy)")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select config from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        niche = ((row[0] or {}).get("niche") if row else None) or "gaming"
        cur.execute(
            """select published_url from articles a join sites s on s.id=a.site_id
               where s.domain=%s and a.status='published' and a.published_url is not null
               order by a.published_at desc limit 1""",
            (args.site,),
        )
        r2 = cur.fetchone()

    targets = [f"https://{args.site}{_home_path(niche)}"]
    if r2 and r2[0]:
        u = r2[0]
        targets.append(u if u.startswith("http") else f"https://{args.site}{u}")

    def run_checks() -> list[tuple[str, bool, str]]:
        return [(t, *_check(t)) for t in targets]

    results = run_checks()
    for url, ok, msg in results:
        print(f"  {'✅' if ok else '❌'} {url} — {msg}")
    broken = [r for r in results if not r[1]]

    if not broken:
        print(f"  ✓ {args.site} live deploy healthy")
        return 0

    detail = "; ".join(f"{u} → {m}" for u, ok, m in broken)
    print(f"  ❌ {args.site} live deploy looks broken: {detail}")

    if args.redeploy and args.dist and args.project:
        if _redeploy(args.dist, args.project):
            import time
            time.sleep(8)  # let CF Pages edge propagate before re-verifying
            results = run_checks()
            broken = [r for r in results if not r[1]]
            for url, ok, msg in results:
                print(f"  {'✅' if ok else '❌'} (post-redeploy) {url} — {msg}")
            if not broken:
                print(f"  ✓ {args.site} recovered after redeploy")
                _alert(args.site, f"was broken ({detail}) — auto-redeploy RECOVERED it.")
                return 0

    _alert(args.site, detail + " — auto-redeploy did not recover; needs a human.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
