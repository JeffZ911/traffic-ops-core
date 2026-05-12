# Multi-Game Strategy (Phase 2.3)

How and why ntecodex.com pivoted from "NTE-only site" to "Gacha Codex,
multi-game strategy hub." Shipped 2026-05-12.

---

## The trigger

After the 2026-05-11 P0 二次修复 / Task A+C / Task Z sequence the
pipeline mechanics were clean, but the 5-article batch still produced
only 1/5 qa_passed. Root cause: **NTE was released 13 days ago — public
information about supporting facts (banner names, weapon names, mechanic
names) is too thin for the WritingAgent to verify**. Real-world fix
horizon: weeks-to-months waiting for community wikis to fill in.

Decision: instead of waiting on NTE, **broaden the site to 5 popular
gacha games**. Games with mature public info (WuWa year 2, ZZZ year 2,
HSR year 3, Genshin year 6) write themselves; NTE stays as long-tail
coverage for when the community catches up.

Core principle: **what has traffic, we cover.** Diversifying across 5
games multiplies the addressable keyword space ~5× while preserving NTE
as one of the long-tail bets.

---

## Game mix (`sites.config.game_priorities`)

| Game | Priority | Blacklist | Wiki sources |
|---|---|---|---|
| Wuthering Waves | 25% | (none) | wuthering.wiki, prydwen.gg/wuthering-waves, game8 |
| Zenless Zone Zero | 25% | (none) | zenless-zone-zero.fandom.com, prydwen.gg/zzz, game8 |
| Honkai: Star Rail | 25% | (none) | honkai-star-rail.fandom.com, prydwen.gg/star-rail, game8 |
| Genshin Impact | 15% | (none) | fandom, game8 (competition mature, can be intense) |
| Neverness to Everness | 10% | news, banner, weapon_db | neverness.gg, kaiden.gg |

KeywordSelector reads `game_priorities` and adds a per-keyword
`game_bonus = priority * 40` to `priority_with_bonus`. So a wuwa keyword
gets +10 over a comparable NTE keyword (0.25*40 vs 0.10*40), pushing
the LLM toward the high-traffic games on equal-priority ties.

---

## What changed in the pipeline

| Layer | Change |
|---|---|
| `sites.config` | `primary_games`, `game_priorities`, `game_metadata`, `type_blacklist_per_game` — all jsonb keys, no schema migration |
| `keywords.notes` | Carries `game=<slug>\|...` prefix. `_game_from_notes()` regex parses it back. New column NOT created (per "不修改 schema" rule). |
| `articles.outline` (jsonb) | Carries `game: <slug>` set by orchestrator after KeywordSelector picks |
| `KeywordSelectorAgent` | Loads `game_priorities` + `type_blacklist_per_game` from site config; tags each candidate with `game`; adds `game_bonus`; per-game blacklist filter; output normalized to include `game` |
| `OutlineAgent` / `WritingAgent` / `QAAgent` | All receive `game` in `input_data`. WritingAgent looks up `game_metadata[game]` for `display_name`, `release_date`, `wiki_sources` and injects them into the FACTUAL_RULES block. |
| `PublishAgent` | Reads `articles.outline.game`, writes `game:` into Astro frontmatter. Astro content-collection schema accepts `game` with default `"nte"` (back-compat for pre-pivot articles). |
| `keyword_gardener.py` | Already had blacklist awareness from 2026-05-11; now also visible to per-game blacklist via site config. |
| `seed_keywords_for_game.py` (new) | Pro+grounded LLM proposes 30-50 keywords for one game + runs them through the existing entity-verify gate + INSERTs with `notes='game=<slug>\|...'`. |

---

## URL architecture (interim)

Phase 2.3 keeps the existing flat URL structure
(`/guides/<slug>`, `/characters/<slug>`, etc.). New multi-game articles
land at the same flat URLs but carry `game:` frontmatter. This:

- preserves all existing NTE article URLs (SEO equity safe)
- avoids a high-risk same-day URL refactor across 6 page routes + 8 backfilled markdown files
- defers `/wuthering-waves/`, `/zzz/`, etc. namespace into a later iteration once we have ≥3 articles per game to populate them

The Astro content collection has a `game` field; per-game index pages
(`/games/<slug>/`) can land in Phase 2.4 by filtering existing
collections rather than rearranging the content tree.

---

## Cost expectations

| Item | Cost |
|---|---|
| 4× seed keyword runs (wuwa / zzz / hsr / genshin) | ~$1.80 |
| Initial batch=5 validation | ~$1.75 |
| Daily cron, ongoing | ~$0.40-0.50/day per article (unchanged) |
| **Phase 2.3 total spend** | **~$3.55 one-shot** |

Monthly budget guard ($30/month, `sites.config.monthly_budget_usd`)
still gates everything; this pivot doesn't shift any spend ceilings.

---

## Anti-patterns

- **Don't drop NTE entirely.** Keeping the 10% slice means we capture
  NTE long-tail traffic as the community wiki fills in over the next
  6-12 months. Cutting it would forfeit that compounding bet.
- **Don't push to ≥7 games at once.** Each new game needs ~$0.30 of
  initial seeding + ongoing budget share. Beyond 5 games the per-game
  signal dilutes; pick the next game only after one of the current 5
  shows publish traction.
- **Don't change the article_type CHECK constraint.** All games share
  the same nine types (build / tier_list / boss_guide / reroll /
  character_db / weapon_db / news / faq / comparison). Per-game type
  variation lives in `type_blacklist_per_game` (jsonb), not in schema.
- **Don't hard-code games in agents.** Always look up via
  `site_config.game_metadata`. Adding a 6th game must be a SQL update
  + a `seed_keywords_for_game` run; never a code change.

---

## What success looks like (next 14 days)

| Day | Signal |
|---|---|
| D+0 (today) | 4 games seeded with ≥30 keywords each; site rebuilt + deployed with new brand; first multi-game batch run completes with ≥3 qa_passed |
| D+1 | Daily cron picks a non-NTE keyword + produces qa_passed article |
| D+3 | ≥3 different games have at least one qa_passed article |
| D+7 | Dashboard shows ≥10 published articles across ≥4 games |
| D+14 | GSC starts surfacing wuwa / zzz / hsr queries; long-tail discovery loop seeds new keywords per game automatically |

If by D+7 we still have only NTE articles published, the QA bar
threshold may need a per-game adjustment — to be decided based on
qa_feedback patterns at that point.
