"""Dry-run / apply driver for the cross-site link rewriter.

Walks each connected site's git checkout, finds all .md files under
src/content/, runs link_rewriter on each, and either:

  - DRY (default): prints a per-article summary + the actual rewrites.
    Does NOT touch the filesystem, DB, or git. Safe to run anywhere.

  - APPLY (--apply): writes the new markdown back to disk, stages,
    commits, and pushes per site repo. Requires SITE_REPO_PATHS env
    or the per-site repo to already be checked out locally.

Usage:
    python -m scripts.link_rewriter_apply              # dry-run all sites
    python -m scripts.link_rewriter_apply --site quvii.com  # one site
    python -m scripts.link_rewriter_apply --apply      # actually write
    python -m scripts.link_rewriter_apply --diff       # show full unified diff

Designed to be runnable both locally (operator inspects the output)
and from CI (a one-shot recovery job).
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from pathlib import Path

from src.content.link_rewriter import rewrite_markdown, rule_for_domain


# Resolve where each site's git checkout lives on the operator's machine
# when running locally. CI uses SITE_REPO_PATH env (single site per run)
# so this map is only consulted in local mode.
LOCAL_REPO_MAP = {
    "quvii.com":      Path("/Users/jeffzen/Documents/traffic-ops/quvii-site"),
    "ntecodex.com":   Path("/Users/jeffzen/Documents/traffic-ops/ntecodex-site"),
    "pixelmatch.art": Path("/Users/jeffzen/Documents/traffic-ops/pixelmatch-site"),
}


def resolve_repo(domain: str) -> Path | None:
    """Pick the right git checkout. Prefers SITE_REPO_PATH env (CI),
    falls back to LOCAL_REPO_MAP for operator-machine runs."""
    env_path = os.environ.get("SITE_REPO_PATH")
    if env_path:
        return Path(env_path)
    p = LOCAL_REPO_MAP.get(domain)
    if p and p.exists():
        return p
    return None


def find_markdown_files(repo: Path) -> list[Path]:
    """All .md files under src/content/ — articles + collection roots."""
    return sorted((repo / "src" / "content").rglob("*.md"))


def process_file(
    md_path: Path, domain: str, apply: bool, show_diff: bool,
) -> tuple[int, int, int]:
    """Process one markdown file. Returns (kept, rewritten, stripped)."""
    rule = rule_for_domain(domain)
    original = md_path.read_text(encoding="utf-8")
    report = rewrite_markdown(original, rule)

    if not report.changes:
        return (0, 0, 0)

    rel = md_path.relative_to(md_path.parent.parent.parent.parent)
    print(f"  {rel} — {report.summary()}")

    # Only the changes that actually transform the markdown matter for
    # the operator. Keep entries are no-ops.
    for c in report.changes:
        if c.action == "keep":
            continue
        arrow = "→ amazon" if c.action == "amazon" else "→ strip "
        anchor = c.anchor[:55]
        if c.action == "amazon":
            tail = c.new_url[40:80] if c.new_url else ""
            print(f"      {arrow}  '{anchor}' (...{tail})")
        else:
            print(f"      {arrow}  '{anchor}'  (was: {c.original_url[:50]})")

    if show_diff and original != report.text:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            report.text.splitlines(keepends=True),
            fromfile=str(rel), tofile=str(rel) + ".rewritten",
            n=1,
        )
        print("".join(diff))

    if apply and original != report.text:
        md_path.write_text(report.text, encoding="utf-8")

    return (report.kept, report.rewritten, report.stripped)


def commit_and_push(repo: Path, summary: str) -> None:
    """Stage + commit + push the rewritten files."""
    def run(*args: str) -> str:
        result = subprocess.run(
            args, cwd=repo, capture_output=True, text=True, check=False,
        )
        return result.stdout + result.stderr

    print(f"\n  $ git -C {repo} status --short")
    print(run("git", "status", "--short"))

    run("git", "add", "src/content")
    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo, check=False,
    )
    if diff_check.returncode == 0:
        print("  ℹ️  no markdown changes to commit")
        return

    msg = (
        f"link_rewriter: defensive rewrite of hallucinated external "
        f"URLs ({summary})\n\n"
        f"Auto-rewritten by scripts/link_rewriter_apply.py:\n"
        f"  - product-brand anchors → Amazon search + affiliate tag\n"
        f"  - editorial sources (Wikipedia, RTINGS, etc.) preserved\n"
        f"  - other hallucinated external URLs stripped, anchor kept\n"
    )
    run("git", "commit", "-m", msg)
    push = run("git", "push", "origin", "main")
    print(f"  git push: {push.strip().splitlines()[-1]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", action="append",
                   help="Restrict to one or more site domains. "
                        "Default: all 3.")
    p.add_argument("--apply", action="store_true",
                   help="Actually write rewrites + commit + push. "
                        "Without this flag, runs read-only dry-run.")
    p.add_argument("--diff", action="store_true",
                   help="Print unified diff for every changed file.")
    args = p.parse_args()

    sites = args.site or list(LOCAL_REPO_MAP.keys())
    grand_kept = grand_rewritten = grand_stripped = 0

    for domain in sites:
        repo = resolve_repo(domain)
        if not repo:
            print(f"⚠️  {domain}: no repo path resolved — skipping")
            continue
        if not repo.exists():
            print(f"⚠️  {domain}: {repo} does not exist — skipping")
            continue

        md_files = find_markdown_files(repo)
        if not md_files:
            print(f"\n── {domain} ── (no markdown files found in {repo}/src/content)")
            continue

        print(f"\n══════════════════════════════════════════════════════════════")
        print(f"  {domain}  ·  {repo}  ·  {len(md_files)} markdown file(s)")
        print(f"══════════════════════════════════════════════════════════════")

        site_kept = site_rewritten = site_stripped = 0
        for md_path in md_files:
            k, r, s = process_file(md_path, domain, args.apply, args.diff)
            site_kept += k
            site_rewritten += r
            site_stripped += s

        print(f"\n  ── {domain} totals: kept={site_kept} amazon={site_rewritten} stripped={site_stripped}")
        grand_kept += site_kept
        grand_rewritten += site_rewritten
        grand_stripped += site_stripped

        if args.apply:
            commit_and_push(
                repo,
                f"{site_rewritten} amazon, {site_stripped} stripped, "
                f"{site_kept} kept",
            )

    print(f"\n══════════════════════════════════════════════════════════════")
    print(f"  GRAND TOTAL across {len(sites)} site(s):")
    print(f"    kept={grand_kept}  amazon={grand_rewritten}  stripped={grand_stripped}")
    print(f"══════════════════════════════════════════════════════════════")
    if not args.apply:
        print("\nDRY RUN — no files modified. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
