"""RewriterAgent — deepen an underperforming published article.

Closes the GSC feedback loop: `seo_intelligence_weekly` marks articles as
rewrite candidates (impressions ≥50, position 11-30) and dumps the list
into `daily_reports.data_snapshot.seo_intelligence.rewrite_candidates`.
RewriterAgent picks one of those at a time and tries to push it to page 1.

Pipeline:

  1. Load article (content_md, outline, sources) + GSC stats.
  2. Use Google-search-grounded Pro model to find what the *competing*
     top results look like for the article's primary query — H2 themes
     they cover that we don't.
  3. Generate a rewrite directive: missing sub-topics, shallow sections
     to deepen, stale information to update.
  4. Pro-model rewrite of the whole article with explicit goals:
       - word_count: 1.5x to 2.0x of original
       - H2 count:    +20-30%
       - ≥2 new real-source citations
  5. QAAgent (Pro, Google search grounding) reviews the new version.
  6. Decision:
       new_qa_score > old_qa_score + 0.5  → swap in DB + return
       otherwise                          → keep old + mark rewrite_failed

  Output dict makes it clear which branch was taken so the caller
  (daily-cron step) can update markdown / send email / record the
  attempt counter.

Safety / cost shape:
  - Two Pro-model calls per attempt (analysis + rewrite) + one QA call.
    At gemini-3.1-pro-preview ≈ $0.20/call worst case → ~$0.60 per attempt.
  - Caller enforces a daily 1-rewrite cap and a $1 budget.
  - max_retries=0 — the QA threshold is the de-facto retry gate; we don't
    silently re-roll a rewrite if it scores low.

Schema note:
  No new columns. We persist the rewrite attempt counter in
  `articles.qa_feedback->>'rewrite_attempts'`. qa_feedback is already a
  jsonb column owned by QAAgent; we just add a `rewrite_*` sub-tree so
  prior keys survive. Caller is responsible for setting it after the
  result lands.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from src.agents._json_extract import extract_json
from src.agents.base import BaseAgent
from src.db.client import get_db_connection


# ---------------------------------------------------------------- prompts

ANALYSIS_PROMPT = """You are an SEO content strategist for a {game_name} ({game_abbr}) guide site.

Below is one of our PUBLISHED articles. Google Search Console says it's
ranking on page 2 (avg position {gsc_position}, {gsc_impressions}
impressions / 14d, CTR {gsc_ctr_pct:.1f}%). The article is BELOW the
top-10 results we need to compete with.

Use Google Search to find the current top 5 results for the query
"{primary_query}". For each, identify the H2 sections / topics they cover.

Then compare to our article (provided below) and produce a STRUCTURED
"deepening plan":

1. **missing_sections**: H2 topics in competitor results that our article
   lacks entirely. Up to 4.
2. **shallow_sections**: H2 sections in our article that are noticeably
   thinner than competitor coverage — needs concrete numbers, tables,
   examples, or longer prose. Up to 4. Cite which competitor goes deeper.
3. **stale_info**: facts that may be outdated given the {release_date}
   release date + current patch context. Up to 3.
4. **competitor_urls**: the 3-5 top results you actually consulted.

Our article (Markdown):
---
{content}
---

Reply ONLY with a single JSON object, no markdown fence. Schema:
{{
  "missing_sections": [
    {{"h2_title": "<proposed H2>", "why": "<one sentence>",
      "covered_by": ["<competitor_url>", "..."]}}
  ],
  "shallow_sections": [
    {{"existing_h2": "<our current H2>",
      "gap": "<what's missing>",
      "competitor_url": "<url that does it better>"}}
  ],
  "stale_info": [
    {{"claim": "<excerpt from our article>",
      "issue": "<why it's stale>"}}
  ],
  "competitor_urls": ["<url1>", "<url2>", "..."]
}}
"""


REWRITE_PROMPT = """You are rewriting a {game_name} ({game_abbr}) guide article to
make it the BEST result for the query "{primary_query}".

Current article (Markdown) — keep what works, expand what's thin, ADD what's missing:
---
{content}
---

Deepening plan (use ALL of it):
{analysis_json}

Hard requirements:
- Final word_count target: between {target_min_words} and {target_max_words}
  (current is {current_words}). This is ~1.5-2x growth.
- Final H2 count: at least {target_h2_count} (current is {current_h2_count}).
  Add the `missing_sections` H2s. Keep the existing structure for sections
  worth keeping.
- Verify every proper noun (character name, weapon name, mechanic name)
  against Google Search before including it. If you can't verify a name,
  drop the sentence rather than hallucinate.
- Cite at least 2 NEW external sources in a `## Sources` section at the
  end (in addition to any sources from the original).
- Open with a 1-2 sentence hook then the H1. Same banned-phrase list as
  WritingAgent: NO "delve into", "in the realm of", "embark on",
  "navigating the", "in conclusion", "remember that", "it's important
  to note".
- DO NOT insert internal links (no `[text](/path)` to other articles on
  this site) and DO NOT insert image markdown. The CMS adds those after
  publication.

Reply with the new Markdown body ONLY. No JSON wrapping, no preamble.
Start directly with the opening hook line. End with `## Sources` plus a
Markdown bullet per source URL.
"""


# ----------------------------------------------------------------- helpers

def _h2_count(md: str) -> int:
    return len(re.findall(r"^##\s+\S", md or "", re.MULTILINE))


def _word_count(md: str) -> int:
    # Match WritingAgent's word counter: alphanumeric tokens only, body only.
    body = re.split(r"\n##\s*Sources\s*\n", md or "", maxsplit=1)[0]
    return len(re.findall(r"\b\w+\b", body))


def _primary_query_for(article: dict) -> str:
    """Best-effort: title minus the site brand suffix.

    Only strips trailing separators that are space-padded (i.e. real
    title separators like " | NTE Codex" or " — NTE"). We deliberately
    do NOT strip hyphens, because slugs use them as word separators
    (`some-slug` must stay as-is when title is missing).
    """
    title = (article.get("title") or article.get("slug") or "").strip()
    # Match " | xxx", " — xxx", " – xxx", " - xxx" at end of string.
    # The leading space is required, so `some-slug` is left alone.
    title = re.sub(r"\s+[\|—–-]\s+[^\|—–]+$", "", title)
    return title


# --------------------------------------------------------------- the agent

class RewriterAgent(BaseAgent):
    name = "rewriter"
    task_type = "rewriting"
    max_retries = 0    # QA threshold is our retry gate, not silent re-roll

    # Even though task_type doesn't exist in site_config.text_provider,
    # we want the Pro model. Override to point at qa_model.
    def get_model(self) -> str:
        tc = self.site_config["text_provider"]
        return tc.get("rewriting_model") or tc["qa_model"]

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        article_id = UUID(input_data["article_id"])
        gsc_stats = input_data.get("gsc_stats") or {}
        old_qa_score = float(input_data.get("old_qa_score") or 0)

        # Load the article + its primary keyword.
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select a.slug, a.title, a.article_type, a.content_md,
                       a.outline, a.word_count, a.qa_score, a.qa_feedback,
                       (select k.keyword
                          from article_keywords ak
                          join keywords k on k.id = ak.keyword_id
                         where ak.article_id = a.id
                         order by ak.is_primary desc, ak.keyword_id
                         limit 1) as primary_keyword
                  from articles a
                 where a.id = %s
                """,
                (str(article_id),),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"article {article_id} not found")
            cols = [d.name for d in cur.description]
            article = dict(zip(cols, row))

        game = self.site_config.get("game", {})
        content = article["content_md"] or ""
        primary_query = (
            article["primary_keyword"]
            or _primary_query_for(article)
        )
        current_words = int(article["word_count"] or _word_count(content))
        current_h2 = _h2_count(content)

        # Word/H2 targets: 1.5-2x growth, +25% H2 (rounded up).
        target_min_words = int(current_words * 1.5)
        # Honour site_config max so we don't overshoot pipeline norms
        cap = int(self.site_config.get("content_plan", {}).get("max_word_count", 4000))
        target_max_words = min(int(current_words * 2.0), cap)
        target_h2_count = max(current_h2 + 2, int(current_h2 * 1.25))

        # ---------- Step 1: competitor analysis (Pro + grounding) ----------
        analysis_prompt = ANALYSIS_PROMPT.format(
            game_name=game.get("name", "the game"),
            game_abbr=game.get("abbreviation", ""),
            release_date=game.get("release_date", "recently"),
            primary_query=primary_query,
            gsc_position=gsc_stats.get("position", "?"),
            gsc_impressions=gsc_stats.get("impressions", "?"),
            gsc_ctr_pct=float(gsc_stats.get("ctr", 0)) * 100,
            content=content,
        )
        analysis_resp = self._call_llm(
            prompt=analysis_prompt,
            max_tokens=6000,
            temperature=0.2,
            json_mode=True,
            enable_search=True,
        )
        try:
            analysis = extract_json(analysis_resp.text)
        except Exception as e:
            raise RuntimeError(f"analysis JSON parse failed: {e}") from None

        # ---------- Step 2: rewrite (Pro + grounding) ----------
        rewrite_prompt = REWRITE_PROMPT.format(
            game_name=game.get("name", "the game"),
            game_abbr=game.get("abbreviation", ""),
            primary_query=primary_query,
            content=content,
            analysis_json=json.dumps(analysis, indent=2, ensure_ascii=False),
            current_words=current_words,
            current_h2_count=current_h2,
            target_min_words=target_min_words,
            target_max_words=target_max_words,
            target_h2_count=target_h2_count,
        )
        # Rewrites target 1.5-2x the source word count, capped at 4000 words.
        # At ~0.75 words/token, the body alone can reach ~5300 tokens, plus
        # the model burns 2-4k "thinking" tokens with Pro + grounding. The
        # e2e smoke proved max_tokens=12000 truncated mid-rewrite (1977 →
        # 369 words). 32k gives comfortable headroom without risking the
        # provider's hard caps.
        rewrite_resp = self._call_llm(
            prompt=rewrite_prompt,
            max_tokens=32000,
            temperature=0.5,
            json_mode=False,
            enable_search=True,
        )
        new_content = rewrite_resp.text.strip()
        if not new_content:
            raise RuntimeError("rewriter returned empty markdown")
        # Strip a leading code fence the model sometimes adds
        if new_content.startswith("```"):
            new_content = re.sub(
                r"^```[a-zA-Z]*\n?|\n?```\s*$", "", new_content
            ).strip()

        new_words = _word_count(new_content)
        new_h2 = _h2_count(new_content)

        if "##" not in new_content:
            raise RuntimeError("rewriter output has no H2 headings")

        return {
            "article_id": str(article_id),
            "slug": article["slug"],
            "primary_query": primary_query,
            "analysis": analysis,
            "new_content_md": new_content,
            "old_word_count": current_words,
            "new_word_count": new_words,
            "old_h2_count": current_h2,
            "new_h2_count": new_h2,
            "old_qa_score": old_qa_score,
            "targets": {
                "min_words": target_min_words,
                "max_words": target_max_words,
                "h2_count": target_h2_count,
            },
        }
