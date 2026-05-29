"""Backfill driver for the HTTP link validator.

Walks each site's git checkout, HTTP-checks every URL in every article,
and strips the genuinely-dead ones (404/410). 403/timeout/5xx are kept.

  DRY (default): print per-article dead-link report; no writes.
  --apply      : write stripped markdown, commit, push per site repo.

A shared URL cache means each unique URL is checked once across the
entire run (737 articles share many of the same source domains).

Usage:
    python -m scripts.link_validator_backfill                  # dry-run all
    python -m scripts.link_validator_backfill --site quvii.com
    python -m scripts.link_validator_backfill --apply
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from src.content.link_validator import validate_markdown

LOCAL_REPO_MAP = {
    "quvii.com":      Path("/Users/jeffzen/Documents/traffic-ops/quvii-site"),
    "ntecodex.com":   Path("/Users/jeffzen/Documents/traffic-ops/ntecodex-site"),
    "pixelmatch.art": Path("/Users/jeffzen/Documents/traffic-ops/pixelmatch-site"),
}


def resolve_repo(domain: str) -> Path | None:
    env_path = os.environ.get("SITE_REPO_PATH")
    if env_path:
        return Path(env_path)
    p = LOCAL_REPO_MAP.get(domain)
    return p if (p and p.exists()) else None


def find_md(repo: Path) -> list[Path]:
    return sorted((repo / "src" / "content").rglob("*.md"))


def commit_and_push(repo: Path, summary: str) -> None:
    def run(*a: str) -> str:
        return subprocess.run(a, cwd=repo, capture_output=True, text=True).stdout

    run("git", "add", "src/content")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode == 0:
        print("  ℹ️  nothing to commit")
        return
    msg = (
        f"link_validator: strip dead (404/410) source + inline links "
        f"({summary})\n\n"
        f"HTTP-checked every URL; removed links to pages that return "
        f"404/410. Anchor text + source names preserved. Bot-blocked "
        f"(403) and transient (5xx/timeout) links kept.\n"
    )
    run("git", "commit", "-m", msg)
    out = run("git", "push", "origin", "main")
    print(f"  push: {out.strip().splitlines()[-1] if out.strip() else 'done'}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", action="append")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--timeout", type=int, default=8)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    sites = args.site or list(LOCAL_REPO_MAP.keys())
    cache: dict[str, int | None] = {}   # shared across all articles + sites
    grand_dead = grand_checked = 0

    for domain in sites:
        repo = resolve_repo(domain)
        if not repo:
            print(f"⚠️  {domain}: no repo — skip")
            continue
        md_files = find_md(repo)
        print(f"\n══ {domain} · {repo} · {len(md_files)} files ══")

        site_dead = 0
        for md_path in md_files:
            original = md_path.read_text(encoding="utf-8")
            r = validate_markdown(
                original, timeout=args.timeout, workers=args.workers, cache=cache,
            )
            grand_checked += r.checked
            if r.dead > 0:
                rel = md_path.relative_to(repo)
                print(f"  {rel} — {r.summary()}")
                for d in r.dead_links:
                    label = d.anchor or "(source)"
                    print(f"      ✗ {d.status} [{d.shape}] {label[:35]:35s} {d.url[:55]}")
                site_dead += r.dead
                if args.apply and r.text != original:
                    md_path.write_text(r.text, encoding="utf-8")

        print(f"  ── {domain}: {site_dead} dead links stripped")
        grand_dead += site_dead
        if args.apply and site_dead:
            commit_and_push(repo, f"{site_dead} dead links")

    print(f"\n══ GRAND TOTAL: checked {grand_checked} url-refs · "
          f"{len(cache)} unique · stripped {grand_dead} dead ══")
    if not args.apply:
        print("DRY RUN — no files modified. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
