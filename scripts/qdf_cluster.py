"""QDF P2 — inbound topic-cluster linking for fresh trend pages.

When a trend (QDF) article publishes, the strongest signal we can send Google
is INBOUND internal links from established pages → the fresh page. Outbound
links on the new page already happen at write-time (_internal_links); this
script does the reverse half: it walks every OTHER published article and, where
the old body NATURALLY mentions the trend topic, inserts one contextual link to
the new page.

Why this is safe:
  - Reuses inject_internal_links: whole-word match, skips code/headings/existing
    links, once per keyword per doc — no link spam, idempotent.
  - Only links where the phrase ALREADY appears in the old article (contextual,
    never forced). If nothing matches, the file is untouched.
  - Same-site only (niche-safe — never crosses sites).
  - Dry-run by default; --apply commits + pushes per site repo.

Anchors per trend article: the full keyword + a "core" phrase (keyword minus
leading/trailing generic qualifier / year words), each ≥ 2 tokens so we never
turn a single common word into a link.

Usage:
  python -m scripts.qdf_cluster --site quvii.com               # dry-run preview
  python -m scripts.qdf_cluster --site quvii.com --apply       # write+commit+push
  python -m scripts.qdf_cluster --site quvii.com --days 3
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents._internal_links import inject_internal_links
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Generic words stripped when deriving the short "core" anchor — they make an
# anchor non-distinctive (every article has "best", "guide", "2026"…).
_FILLER = {
    "best", "top", "guide", "how", "to", "the", "a", "an", "for", "of", "in",
    "on", "with", "without", "vs", "review", "reviews", "deal", "deals", "2024",
    "2025", "2026", "2027", "cheap", "under", "over", "and", "or", "your", "is",
    "are", "what", "why", "when", "should", "do", "does", "step", "by",
}

# Map domain → local git checkout (CI sets SITE_REPO_PATH; locally we guess).
def resolve_repo(domain: str) -> Path | None:
    env_path = os.environ.get("SITE_REPO_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    slug = domain.split(".")[0]
    for guess in (
        Path.home() / "Documents" / "traffic-ops" / f"{slug}-site",
        Path(__file__).resolve().parent.parent.parent / f"{slug}-site",
    ):
        if guess.exists():
            return guess
    return None


def _candidate_anchors(keyword: str, generic: set[str]) -> list[str]:
    """Distinctive 2-3 word phrases from the keyword that are likely to appear
    in a related article's body. The full keyword rarely matches verbatim, so
    we mine contiguous 2- and 3-grams (e.g. 'wyze solar cam', 'prime day',
    'product studio'), dropping all-filler grams. ≥2 tokens always — we never
    turn a single common word into a link. Longest first (3-grams beat 2-grams
    in inject_internal_links, so the most specific phrase wins).

    `generic` = site-wide common tokens (IDF: appear in many keywords, e.g.
    'home', 'security', 'camera'). An anchor must contain ≥1 NON-generic
    (distinctive) token — a brand/product/event word like 'wyze' or 'prime'.
    This is what stops generic over-linking ('smart home' → every article)."""
    toks = re.findall(r"[a-z0-9]+", keyword.lower())
    grams: list[str] = []
    for n in (3, 2):
        for i in range(len(toks) - n + 1):
            gram = toks[i : i + n]
            if gram[0] in _FILLER or gram[-1] in _FILLER:
                continue          # trim filler-edged grams ("the wyze", "cam for")
            # Require a distinctive (non-generic, non-filler) token — kills
            # site-wide phrases like "smart home" / "home security".
            if not any(t not in generic and t not in _FILLER for t in gram):
                continue
            grams.append(" ".join(gram))
    seen: set[str] = set()
    out: list[str] = []
    for g in grams:
        if g not in seen:
            seen.add(g); out.append(g)
    return out


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_incl_fences, body). Body is what we link into —
    NEVER touch YAML frontmatter."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[: nl + 1], text[nl + 1:]
    return "", text


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--days", type=int, default=2,
                    help="treat trend articles published within N days as 'fresh'")
    ap.add_argument("--max-inbound", type=int, default=6,
                    help="max inbound links to add per fresh trend page per run")
    ap.add_argument("--max-per-anchor", type=int, default=2,
                    help="max times one anchor phrase may be used (diversity — "
                         "stops a generic phrase becoming a repeated footprint)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes + commit + push (default: dry-run)")
    args = ap.parse_args()

    repo = resolve_repo(args.site)
    if not repo:
        print(f"❌ no local checkout for {args.site} (set SITE_REPO_PATH)")
        return 2

    # 1) Fresh trend pages → anchor→url map. Anchors: full keyword + core phrase.
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites"); return 2
        site_id = str(row[0])
        cur.execute(
            """
            select distinct a.published_url, k.keyword
              from articles a
              join article_keywords ak on ak.article_id = a.id
              join keywords k on k.id = ak.keyword_id
             where a.site_id = %s and a.status = 'published'
               and a.published_url is not null
               and k.source = 'trend'
               and a.published_at >= now() - %s * interval '1 day'
            """,
            (site_id, args.days),
        )
        trend_rows = cur.fetchall()
        # IDF: tokens appearing in >12% of this site's keywords are "generic"
        # (the niche's core vocab — home/security/camera). Anchors need a token
        # OUTSIDE this set to be distinctive enough to link on.
        cur.execute("select keyword from keywords where site_id=%s", (site_id,))
        all_kw = [r[0] for r in cur.fetchall()]
    from collections import Counter
    df: Counter = Counter()
    for kw in all_kw:
        for t in set(re.findall(r"[a-z0-9]+", (kw or "").lower())):
            if t not in _FILLER and len(t) > 2:
                df[t] += 1
    # The site's niche-core vocabulary = its most frequent tokens (home,
    # security, camera, smart…). Anchors built ONLY from these are non-
    # distinctive and would over-link, so they're treated as generic. Top-N
    # adapts per site and is robust on small pools (a % threshold isn't).
    generic = {t for t, _ in df.most_common(20)}

    if not trend_rows:
        print(f"  no fresh trend pages (last {args.days}d) on {args.site} — nothing to cluster")
        return 0

    anchor_to_url: dict[str, str] = {}
    for url, kw in trend_rows:
        full = f"https://{args.site}{url}" if not url.startswith("http") else url
        for anchor in _candidate_anchors(kw, generic):
            # First trend page to claim an anchor keeps it (avoid ambiguous
            # double-mapping of a generic 2-gram across two trend pages).
            anchor_to_url.setdefault(anchor, full)
    print(f"  {len(trend_rows)} fresh trend page(s); {len(anchor_to_url)} candidate anchor(s):")
    for a in sorted(anchor_to_url, key=len, reverse=True):
        print(f"     • {a!r} → {anchor_to_url[a]}")

    # 2) Walk every OTHER published .md; insert inbound links where the topic
    #    naturally appears in the body.
    md_files = sorted((repo / "src" / "content").rglob("*.md"))
    inbound_per_url: dict[str, int] = {u: 0 for u in anchor_to_url.values()}
    from collections import Counter
    anchor_use: Counter = Counter()
    changed: list[Path] = []
    changed_urls: set[str] = set()

    for md in md_files:
        text = md.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        # self-url: this file's public path (so a trend page never links to itself)
        slug = md.stem
        self_url = next((u for u in anchor_to_url.values() if u.rstrip("/").endswith(slug)), None)

        # Drop anchors whose target page hit the per-page cap OR whose phrase
        # hit the per-anchor diversity cap.
        live_map = {a: u for a, u in anchor_to_url.items()
                    if inbound_per_url.get(u, 0) < args.max_inbound
                    and anchor_use[a] < args.max_per_anchor}
        if not live_map:
            break
        new_body, linked = inject_internal_links(body, live_map, self_url=self_url)
        if not linked:
            continue
        for a in linked:
            inbound_per_url[anchor_to_url[a]] = inbound_per_url.get(anchor_to_url[a], 0) + 1
            anchor_use[a] += 1
        rel = md.relative_to(repo)
        print(f"  + {rel}: linked {linked}")
        changed.append(md)
        changed_urls.add(slug)
        if args.apply:
            md.write_text(fm + new_body, encoding="utf-8")

    if not changed:
        print("  no old articles naturally mention the fresh trend topics — no inbound links added")
        return 0

    print(f"\n  inbound links per trend page: "
          + ", ".join(f"{u.split('/')[-1] or u}={n}" for u, n in inbound_per_url.items() if n))

    if not args.apply:
        print(f"\nDRY RUN — {len(changed)} file(s) would change. Re-run with --apply.")
        return 0

    # 3) commit + push (CF Pages rebuilds → updated lastmod on those pages).
    _git(repo, "add", "src/content")
    staged = _git(repo, "diff", "--cached", "--name-only").strip()
    if not staged:
        print("  nothing staged"); return 0
    if not _git(repo, "config", "user.email").strip():
        _git(repo, "config", "user.email", "jeffzen@sunaofe.com")
        _git(repo, "config", "user.name", "Jeff Zen")
    _git(repo, "commit", "-m",
         f"QDF cluster: +{len(changed)} inbound link(s) to fresh trend pages")
    # Rebase before push: the content cron pushes articles concurrently, so the
    # remote may have moved since checkout — a plain push would be rejected.
    _git(repo, "pull", "--rebase", "origin", "main")
    out = _git(repo, "push", "origin", "main")
    print(f"  git push: {out.strip().splitlines()[-1] if out.strip() else 'done'}")
    print(f"  ✓ {len(changed)} old article(s) now link to the fresh trend pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
