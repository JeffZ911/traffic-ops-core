"""PublishAgent — write a qa_passed article to the site repo as a Markdown file.

Per SITE-STRUCTURE.md §2 the destination directory depends on article_type.
Phase 1.A scope: write the file only. git push / GSC / deploy polling are
deferred until the Astro template is ready.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.agents.base import BaseAgent
from src.agents._internal_links import (
    build_keyword_lookup_from_articles,
    inject_internal_links,
)
from src.db.client import get_db_connection


# article_type → relative path under <site_repo>/src/content/
# tier_list and faq aggregate into a single page per SITE-STRUCTURE §2; we
# stash their source in a separate folder so the aggregator can scan them.
PATH_BY_TYPE: dict[str, str] = {
    # ── gaming niche (ntecodex) ────────────────────────────────────
    "build":          "guides/{slug}.md",
    "comparison":     "guides/{slug}.md",
    "boss_guide":     "boss/{slug}.md",
    "reroll":         "guides/reroll/{slug}.md",
    "character_db":   "characters/{slug}.md",
    "weapon_db":      "weapons/{slug}.md",
    "news":           "news/{slug}.md",
    "tier_list":      "tier-list-source/{slug}.md",
    "faq":            "faq-source/{slug}.md",
    # ── ecommerce niche (pixelmatch — Phase 1A 2026-05-14) ─────────
    # Collection folder names are SaaS-style so Astro's default
    # collection-slug derivation gives URLs like /learn/<slug>.
    "tool_guide":     "learn/{slug}.md",
    "vs_comparison":  "compare/{slug}.md",
    "use_case":       "stories/{slug}.md",
    "policy_guide":   "policy/{slug}.md",
    # ── security_cameras niche (quvii — Phase 0 2026-05-27) ────────
    # Quvii uses 4 top-level collections (blog/learn/reviews/support).
    # blog/* gets sub-categorized via article body's category, not URL.
    # Names prefixed `camera_` to avoid colliding with gaming `comparison`
    # / `news` which already map to different folders.
    "camera_buying_guide":  "blog/{slug}.md",
    "camera_comparison":    "blog/{slug}.md",
    "camera_install":       "blog/{slug}.md",
    "camera_troubleshoot":  "blog/{slug}.md",
    "camera_news":          "blog/{slug}.md",
    "camera_learn":         "learn/{slug}.md",
    "camera_review":        "reviews/{slug}.md",
    "camera_support":       "support/{slug}.md",
}

# Public URL pattern (for articles.published_url). Aggregated types map to
# the aggregation page rather than a per-slug URL.
URL_BY_TYPE: dict[str, str] = {
    # ── gaming ─────────────────────────────────────────────────────
    "build":          "/guides/{slug}",
    "comparison":     "/guides/{slug}",
    "boss_guide":     "/boss/{slug}",
    "reroll":         "/guides/reroll/{slug}",
    "character_db":   "/characters/{slug}",
    "weapon_db":      "/weapons/{slug}",
    "news":           "/news/{slug}",
    "tier_list":      "/tier-list",   # aggregated page
    "faq":            "/faq",         # aggregated page
    # ── ecommerce ──────────────────────────────────────────────────
    # All ecommerce articles live under /blog/<type>/<slug> on the
    # canonical domain (pixelmatch.art/blog/...). The /blog prefix is
    # INCLUDED here so the published_url written into the articles
    # table — and propagated to listing cards, sitemaps, and external
    # references — matches the canonical URL emitted by Astro's
    # `base: '/blog'`. Without the prefix, cards linked to /learn/...
    # while the canonical was /blog/learn/..., creating inconsistent
    # internal navigation (Phase 1B UX audit, 2026-05-19).
    "tool_guide":     "/blog/learn/{slug}",
    "vs_comparison":  "/blog/compare/{slug}",
    "use_case":       "/blog/stories/{slug}",
    "policy_guide":   "/blog/policy/{slug}",
    # ── security_cameras (quvii) ─────────────────────────────────
    "camera_buying_guide":  "/blog/{slug}",
    "camera_comparison":    "/blog/{slug}",
    "camera_install":       "/blog/{slug}",
    "camera_troubleshoot":  "/blog/{slug}",
    "camera_news":          "/blog/{slug}",
    "camera_learn":         "/learn/{slug}",
    "camera_review":        "/reviews/{slug}",
    "camera_support":       "/support/{slug}",
}


# ──── Phase 2.6 editorial-tier banners ────────────────────────────────
# Auto-inserted right after the H1 by PublishAgent. Two flavors:
#
#   tier='note'   → mild blockquote: "AI-assisted, cross-reference in-game"
#   tier='strong' → stronger blockquote: "Auto-generated, may contain
#                   approximations, help us improve via comments"
#
# No frontmatter flags needed — banner lives in the article body, so
# ArticleLayout / Pagefind / GSC all see it the same as user content.

EDITORIAL_NOTE_TEMPLATE = (
    "\n> 📝 **Editorial Note:** This guide is AI-assisted and game data evolves "
    "rapidly. Please cross-reference with in-game information. "
    "_Updated: {date}._\n"
)

EDITORIAL_STRONG_TEMPLATE = (
    "\n> ⚠️ **Notice:** Auto-generated content. May contain approximations "
    "or minor inaccuracies in supporting details. "
    "Help us improve via the comment section below. "
    "_Last reviewed: {date}._\n"
)


# ──── PixelMatch CTA injector (Phase 1A 2026-05-14) ───────────────────
# Two CTAs auto-inserted into ecommerce articles:
#   1. Mid-article "soft" pitch — after the 3rd H2 (or 2nd if shorter).
#   2. Footer "hard" CTA — right before the `## Sources` H2.
#
# Skipped entirely when the site's niche is not "ecommerce_tools" or
# when the CTA config is missing — gaming articles get neither.
# Idempotent: re-publish detects existing markers and no-ops.

_CTA_MARKER_MID = "<!-- pm-cta:mid -->"
_CTA_MARKER_FOOT = "<!-- pm-cta:foot -->"

_CTA_MID_TEMPLATE = (
    "\n{marker}\n"
    "> 💡 **Skip the manual editing.** {brand_name} batch-generates "
    "{audience_short}-ready product images in 60 seconds — white background, "
    "lifestyle scenes, and variant mockups from a single source photo.\n"
    "> **[Try {brand_name} free →]({signup_url})**\n"
)

_CTA_FOOT_TEMPLATE = (
    "\n{marker}\n"
    "### Ready to scale your listings?\n\n"
    "{brand_name} generates white-background, lifestyle, and variant "
    "mockups from a single source photo — built specifically for "
    "{audience_long}. 50 free images on signup, no credit card.\n\n"
    "**[Start free →]({signup_url})**\n"
)


def _inject_pixelmatch_ctas(
    content_md: str,
    *,
    brand_name: str,
    signup_url_base: str,
    slug: str,
    platform: str | None = None,
) -> str:
    """Insert mid-article + footer CTAs into the markdown body.

    The signup_url gets per-article UTM params so the brand's
    backend can attribute conversions to specific blog slugs.
    Idempotent — markers prevent duplicate insertion on republish.
    """
    # Audience phrasing: tuned by primary platform if known.
    audience_short, audience_long = {
        "amazon_fba":   ("Amazon",       "Amazon FBA sellers"),
        "shopify":      ("Shopify",      "Shopify and DTC store owners"),
        "etsy":         ("Etsy",         "Etsy and print-on-demand sellers"),
        "tiktok_shop":  ("TikTok Shop",  "TikTok Shop and short-video sellers"),
    }.get(platform or "", ("ecommerce", "multi-platform ecommerce sellers"))

    def _utm(medium: str) -> str:
        sep = "&" if "?" in signup_url_base else "?"
        return (
            f"{signup_url_base}{sep}utm_source=blog&utm_medium={medium}"
            f"&utm_campaign={slug}"
        )

    out = content_md

    # --- Mid CTA: after the 3rd H2 (fall back to 2nd if article is short).
    if _CTA_MARKER_MID not in out:
        # Match H2 lines that are NOT "## Sources".
        h2_iter = list(re.finditer(r"(?m)^##\s+(?!Sources\b).+$", out))
        if h2_iter:
            target_idx = 2 if len(h2_iter) >= 3 else min(1, len(h2_iter) - 1)
            target = h2_iter[target_idx]
            mid_block = _CTA_MID_TEMPLATE.format(
                marker=_CTA_MARKER_MID,
                brand_name=brand_name,
                audience_short=audience_short,
                signup_url=_utm("mid"),
            )
            # Insert right after the line containing the target H2.
            line_end = out.find("\n", target.end())
            if line_end == -1:
                line_end = len(out)
            out = out[: line_end + 1] + mid_block + out[line_end + 1 :]

    # --- Footer CTA: right before ## Sources (or at the end if no Sources).
    if _CTA_MARKER_FOOT not in out:
        foot_block = _CTA_FOOT_TEMPLATE.format(
            marker=_CTA_MARKER_FOOT,
            brand_name=brand_name,
            audience_long=audience_long,
            signup_url=_utm("footer"),
        )
        m = re.search(r"(?m)^##\s+Sources\b", out)
        if m:
            out = out[: m.start()] + foot_block + "\n" + out[m.start() :]
        else:
            out = out.rstrip() + "\n\n" + foot_block

    return out


def _inject_editorial_banner(content_md: str, tier: str, date_iso: str) -> str:
    """Insert the banner blockquote after the first H1, or at the very
    top if no H1 found. Idempotent — if the banner already exists, no
    duplicate insertion."""
    template = (
        EDITORIAL_STRONG_TEMPLATE if tier == "strong"
        else EDITORIAL_NOTE_TEMPLATE
    )
    banner = template.format(date=date_iso)
    # Detect prior injection to avoid duplicates on re-runs
    if "Editorial Note:" in content_md and tier == "note":
        return content_md
    if "Notice:** Auto-generated" in content_md and tier == "strong":
        return content_md
    # Find first H1 line and insert banner right after it
    lines = content_md.splitlines(keepends=False)
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            return "\n".join(lines[: i + 1]) + banner + "\n" + "\n".join(lines[i + 1:])
    # No H1 found — prepend banner
    return banner + "\n" + content_md


def _yaml_escape(s: str) -> str:
    """Quote a YAML scalar that may contain unsafe chars."""
    if any(c in s for c in (':', '#', '\n', '"', '\'')) or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    return s


def _emit_yaml(d: dict) -> str:
    """Tiny YAML emitter for our flat-ish frontmatter shape."""
    out: list[str] = []
    for k, v in d.items():
        if v is None:
            out.append(f"{k}: null")
        elif isinstance(v, bool):
            out.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            out.append(f"{k}: {v}")
        elif isinstance(v, str):
            out.append(f"{k}: {_yaml_escape(v)}")
        elif isinstance(v, list):
            if not v:
                out.append(f"{k}: []")
            elif all(isinstance(x, str) for x in v):
                out.append(f"{k}:")
                for x in v:
                    out.append(f"  - {_yaml_escape(x)}")
            else:
                out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, dict):
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    return "\n".join(out)


class PublishAgent(BaseAgent):
    name = "publish"
    task_type = "publish"     # not in site_config; this Agent does no LLM calls
    max_retries = 0           # filesystem ops; retries here would mask bugs

    def __init__(self, llm, site_config, *, site_repo_path: Path):
        super().__init__(llm=llm, site_config=site_config)
        self.site_repo_path = Path(site_repo_path)

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        article_id = UUID(input_data["article_id"])

        # Load the article + its writing-agent sources from agent_runs
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select id, slug, title, article_type, status, content_md, "
                "       qa_score, word_count, outline, qa_feedback "
                "from articles where id = %s",
                (str(article_id),),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"article not found: {article_id}")
            cols = [d.name for d in cur.description]
            article = dict(zip(cols, row))

            cur.execute(
                """
                select output->'_sources' from agent_runs
                 where article_id = %s and agent_name = 'writing'
                       and status = 'success'
                 order by created_at desc limit 1
                """,
                (str(article_id),),
            )
            src_row = cur.fetchone()
            sources = src_row[0] if src_row and src_row[0] else []

        if article["status"] != "qa_passed":
            raise RuntimeError(
                f"refusing to publish article in status={article['status']!r}; "
                f"expected qa_passed"
            )

        article_type = article["article_type"]
        slug = article["slug"]
        if article_type not in PATH_BY_TYPE:
            raise ValueError(f"unknown article_type: {article_type}")

        rel = PATH_BY_TYPE[article_type].format(slug=slug)
        out_path = self.site_repo_path / "src" / "content" / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        url_pattern = URL_BY_TYPE[article_type]
        published_url = url_pattern.format(slug=slug)
        published_at = datetime.now(timezone.utc)

        # Astro Content Collections derives an entry's slug from the file
        # path *relative to the collection root*. When we write to a
        # sub-folder (e.g. guides/reroll/<slug>.md), the entry.slug Astro
        # exposes is "reroll/<slug>" — and the homepage / listing pages
        # build URLs from entry.slug. If frontmatter `slug` doesn't match
        # that sub-path, Astro lets frontmatter override → JS gets the
        # wrong slug → listing pages produce dead links.
        # Always write the *collection-relative* path here.
        rel_without_collection = rel.split("/", 1)[1] if "/" in rel else rel
        entry_slug = rel_without_collection[:-len(".md")] if rel_without_collection.endswith(".md") else rel_without_collection

        # Carry the article's audience tag from outline jsonb into
        # frontmatter so Astro can filter / route by it without a
        # schema change. Niche decides the field name:
        #   gaming         → "game"     (wuwa | hsr | zzz | genshin | nte)
        #   ecommerce_tools → "platform" (amazon_fba | shopify | etsy | tiktok_shop | multi)
        outline_blob = article.get("outline") or {}
        site_niche = (self.site_config.get("niche") or "gaming")

        # Build frontmatter
        fm: dict[str, Any] = {
            "title": article["title"] or slug,
            "slug": entry_slug,
            "article_type": article_type,
            "qa_score": float(article["qa_score"] or 0),
            "word_count": int(article["word_count"] or 0),
            "published_at": published_at.isoformat(),
            "published_url": published_url,
            "sources": [s.get("uri") for s in sources if s.get("uri")],
        }

        # Surface outline.quick_answer (1-2 sentence answer callout) to
        # frontmatter so the Astro layout can render it as a top-of-article
        # card. Optional — if the outline didn't generate it (older articles
        # or models that skipped the field), nothing is written and the
        # site falls back to showing only the prose body.
        if isinstance(outline_blob, dict):
            qa_text = outline_blob.get("quick_answer")
            if isinstance(qa_text, str) and qa_text.strip():
                fm["quick_answer"] = qa_text.strip()

            # Comparison / affiliate round-ups: surface the structured
            # `products` array into frontmatter. ntecodex's ProductRoundup
            # component renders these as cards (with comparison table)
            # above the prose body. Mark affiliate=true so ArticleLayout
            # also renders the FTC disclosure banner.
            products = outline_blob.get("products")
            if (
                article_type == "comparison"
                and isinstance(products, list)
                and len(products) > 0
            ):
                fm["products"] = products
                fm["affiliate"] = True

        if site_niche == "ecommerce_tools":
            platform_slug = (
                (outline_blob.get("platform")
                 if isinstance(outline_blob, dict) else None)
                or "multi"
            )
            fm["platform"] = platform_slug
            # use_case-specific structured fields (Phase 1B): surface
            # them so the stories/[...slug].astro layout can render
            # the before/after metric table above the body.
            if article_type == "use_case" and isinstance(outline_blob, dict):
                for k in ("seller_profile", "is_composite", "key_metrics"):
                    if k in outline_blob:
                        fm[k] = outline_blob[k]
        else:
            # Gaming default — preserves ntecodex behavior exactly.
            game_slug = (
                (outline_blob.get("game") if isinstance(outline_blob, dict) else None)
                or "nte"
            )
            fm["game"] = game_slug
        # character_db: surface the structured outline so Astro template can render cards
        if article_type == "character_db" and isinstance(article["outline"], dict):
            fm["character_data"] = article["outline"]

        # AdSense placement is now fully handled by Google Auto Ads — the
        # adsbygoogle.js loader in BaseLayout is enough. We no longer
        # inject <ins> blocks at publish time; old _ad_inject.py is kept
        # in the tree for reference / future opt-out scenarios.

        # Auto-insert internal links: pull every other published article's
        # name + URL, then walk this article's body and replace the first
        # occurrence of each matching name with a markdown link. Skips
        # code blocks, headings, and existing links.
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select title, published_url, article_type, outline
                  from articles
                 where status = 'published' and id <> %s
                """,
                (str(article_id),),
            )
            link_cols = [d.name for d in cur.description]
            other_rows = [dict(zip(link_cols, r)) for r in cur.fetchall()]
        keyword_lookup = build_keyword_lookup_from_articles(other_rows)
        content_md = article["content_md"] or ""
        if keyword_lookup:
            content_md, linked_keywords = inject_internal_links(
                content_md, keyword_lookup, self_url=published_url
            )
        else:
            linked_keywords = []

        # ──── Phase 1A pixelmatch (2026-05-14): CTA injection ───────
        # When the site's niche is ecommerce_tools we inject a mid-article
        # soft pitch + footer hard CTA pointing at the brand's signup
        # page. Gaming articles get neither — the function exits early
        # on niche mismatch. CTA position uses the article's
        # primary_platform (from outline.platform or input_data.platform)
        # to tune audience phrasing.
        niche = (self.site_config.get("niche") or "gaming")
        brand = (self.site_config.get("brand") or {})
        cta_cfg = (self.site_config.get("cta") or {})
        signup_url_base = cta_cfg.get("primary_url") or brand.get("signup_url")
        brand_name = brand.get("name")
        if niche == "ecommerce_tools" and signup_url_base and brand_name:
            outline_blob_for_platform = article.get("outline") or {}
            platform_slug = (
                (outline_blob_for_platform.get("platform")
                 if isinstance(outline_blob_for_platform, dict) else None)
                or input_data.get("platform")
            )
            content_md = _inject_pixelmatch_ctas(
                content_md,
                brand_name=brand_name,
                signup_url_base=signup_url_base,
                slug=slug,
                platform=platform_slug,
            )

        # ──── Phase 2.6 (2026-05-13): editorial-tier banner ────
        # QAAgent classifies every published article into one of three
        # tiers (clean / note / strong) and writes it to
        # articles.qa_feedback.editorial_tier. Articles with tier='note'
        # or 'strong' get an auto-inserted banner right after the H1
        # so readers know the calibration up front. Banner content is
        # static markdown — no JS, no component, indexed by Pagefind,
        # part of the article body for SEO honesty signals.
        qa_fb = article.get("qa_feedback") or {}
        tier = (qa_fb.get("editorial_tier")
                if isinstance(qa_fb, dict) else None) or "clean"
        if tier in ("note", "strong"):
            content_md = _inject_editorial_banner(
                content_md, tier=tier,
                date_iso=published_at.date().isoformat(),
            )

        # ──── Phase 3.0 (2026-05-28): defensive link rewrite ────
        # The writer agent reliably hallucinates external URLs. Run
        # the per-site link_rewriter to:
        #   - convert product-brand anchors into Amazon search URLs
        #     with the site's affiliate tag (rel="sponsored nofollow")
        #   - preserve editorial sources (Wikipedia, RTINGS, FCC, etc.)
        #   - strip URLs the LLM hallucinated but anchor is a generic
        #     phrase — anchor text remains, link is gone.
        # See src/content/link_rewriter.py for the rule schema.
        from src.content.link_rewriter import rewrite_markdown, rule_for_site
        site_domain = self.site_config.get("domain") or ""
        site_niche  = self.site_config.get("niche")
        rewrite_override = self.site_config.get("link_rewriter") or {}
        rule = rule_for_site(site_domain, niche=site_niche, override=rewrite_override)
        if rule.amazon_tag or rule.brand_patterns:  # rule actually configured
            report = rewrite_markdown(content_md, rule)
            if report.rewritten or report.stripped:
                print(f"      link_rewriter: {report.summary()}")
                content_md = report.text
                # Tag frontmatter so the affiliate-disclosure banner can
                # auto-mount on articles with rewritten amazon links.
                if report.rewritten > 0 and not fm.get("affiliate"):
                    fm["affiliate"] = True

        # ---- Dead-link validation (HTTP check) ----
        # After brand/hallucination rewriting, HTTP-check whatever URLs
        # remain (inline + Sources-list) and strip the genuinely-dead
        # ones (404/410). 403/timeout/5xx are KEPT — those are
        # bot-blocks on real pages. Conservative by design; see
        # src/content/link_validator.py.
        try:
            from src.content.link_validator import validate_markdown
            vreport = validate_markdown(content_md)
            if vreport.dead > 0:
                print(f"      link_validator: {vreport.summary()}")
                for d in vreport.dead_links[:10]:
                    print(f"        ✗ {d.status} [{d.shape}] {d.url[:70]}")
                content_md = vreport.text
        except Exception as e:
            # Network hiccup in CI must never block a publish. Log + skip.
            print(f"      link_validator: skipped ({e})")

        body = (
            "---\n"
            + _emit_yaml(fm)
            + "\n---\n\n"
            + content_md
            + "\n"
        )
        out_path.write_text(body, encoding="utf-8")

        # Update articles row
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update articles
                   set status = 'published',
                       published_url = %s,
                       published_at = %s
                 where id = %s
                """,
                (published_url, published_at, str(article_id)),
            )

        return {
            "article_id": str(article_id),
            "file_path": str(out_path.relative_to(self.site_repo_path)),
            "absolute_path": str(out_path),
            "published_url": published_url,
            "bytes_written": len(body.encode("utf-8")),
            "source_count": len(fm["sources"]),
            "linked_keywords": linked_keywords,
        }
