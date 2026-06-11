"""Generate a high-quality personalized-gift guide → publish as a Shopify DRAFT.

Quality-first sample for imade4u (NOT the old spammy near-duplicate style):
each article is a distinct, genuinely useful gift guide that recommends REAL
products from the store (linked), occasion/emotion-driven. Lets the operator
review quality as Shopify drafts BEFORE the full QDF pipeline is wired.

Usage:
  python -m scripts.gift_sample --topic "Personalized Mother's Day Jewelry Gifts" \
      --match necklace,bracelet,jewelry --tag mothers-day,jewelry
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request

from dotenv import load_dotenv

from src.utils.llm import get_llm_provider
from src.integrations.shopify_blog import publish_article

load_dotenv()

# Verified-200 authority references the writer MAY cite (0-2 per article).
# A closed whitelist because the gift writer runs WITHOUT search grounding —
# any other external URL it produced would be hallucinated.
TRUSTED_REFS = """  - Father's Day (history & dates) — https://en.wikipedia.org/wiki/Father%27s_Day
  - Mother's Day (history & dates) — https://en.wikipedia.org/wiki/Mother%27s_Day
  - Valentine's Day (history) — https://en.wikipedia.org/wiki/Valentine%27s_Day
  - Birthstones by month — https://en.wikipedia.org/wiki/Birthstone
  - Jewelry/gem care basics (GIA) — https://www.gia.edu/gem-care"""

PROMPT = """You are an expert gift curator writing for iMade4U
(imade4u.com), a store of PERSONALIZED / custom gifts (engraved jewelry,
custom keychains, pet portraits & memorials, custom home decor).

Write ONE genuinely useful, DISTINCT gift guide on this topic:
  "{topic}"

Hard quality rules (this store was hurt by thin, repetitive AI content — do
the opposite):
- Every gift idea must be DISTINCT and specific, with a real reason it's
  meaningful for this occasion/recipient. No filler, no repetition, no
  generic "the perfect gift" padding.
- Warm, heartfelt, helpful voice. Speak to the gift-giver's emotion + practical
  needs (personalization, timing, who it's for).
- Recommend SPECIFIC products from the REAL product list below, and link them
  inline as markdown: [Product Name](https://imade4u.com/products/HANDLE).
  Only use products from this list — never invent products, prices, or links.
- You know each product ONLY by its title. NEVER state materials, dimensions,
  prices, shipping times, or features that the title itself does not say —
  describe what the gift IS and why it's meaningful, not invented specs.
  (e.g. if the title doesn't say "sterling silver", you may not call it
  sterling silver.) Same for the outside world: no celebrity claims, no
  statistics, no "trending on TikTok" stated as fact — speak of styles and
  occasions in general terms instead.
- Weave product links into the prose naturally across the gift ideas — every
  recommended gift idea links its product (6+ linked products per article).
- Near the end include `## Frequently Asked Questions` with 3-4 `###`
  questions gift shoppers actually ask about this occasion/product type,
  each answered in 2-3 sentences (same factual rules — no invented policies,
  prices, or shipping promises; say "check the product page" instead).
- You MAY cite 1-2 of these VERIFIED reference links inline where genuinely
  helpful (e.g. explaining the occasion) — these are the ONLY external links
  allowed; never invent any other URL:
{trusted_refs}
- 1000-1500 words. Use H2/H3 structure, a short intro, 6-9 gift ideas, a couple
  of practical buying/personalization tips, and a warm closing.

REAL PRODUCTS you may recommend (Title — handle):
{products}

Reply ONLY with a JSON object (no fence):
{{
  "title": "<compelling, specific blog title, 50-65 chars>",
  "meta_title": "<SEO title <=60 chars>",
  "meta_description": "<SEO description 140-160 chars>",
  "tags": ["<occasion>", "<recipient>", "<product type>", ...],
  "summary": "<1-sentence summary>",
  "body_md": "<the full article in markdown, with inline product links>"
}}
"""


def _products(match: list[str], limit: int = 12) -> list[dict]:
    """Active products matching any keyword → [{title, handle, image}]. Real
    product photos (Shopify CDN) are used as article images — on-brand, no AI."""
    dom = os.getenv("IMADE4U_SHOPIFY_DOMAIN"); tok = os.getenv("IMADE4U_SHOPIFY_ADMIN_TOKEN")
    ver = os.getenv("IMADE4U_SHOPIFY_API_VERSION", "2026-04")
    req = urllib.request.Request(
        f"https://{dom}/admin/api/{ver}/products.json?limit=250&fields=title,handle,product_type,tags,status,image,variants",
        headers={"X-Shopify-Access-Token": tok})
    prods = json.load(urllib.request.urlopen(req, timeout=30))["products"]
    # Robust matching: a `match` entry may be a phrase ("custom photo mugs");
    # reduce to distinctive product tokens (≥4 chars, non-generic, singularized)
    # so "custom photo mugs" still finds a "Custom Mug".
    _GEN = {"custom", "personalized", "photo", "gift", "gifts", "best", "unique",
            "the", "for", "and", "with", "your", "ideas"}
    toks = set()
    for m in match:
        for w in re.findall(r"[a-z]+", m.lower()):
            if len(w) >= 4 and w not in _GEN:
                toks.add(w); toks.add(w.rstrip("s"))  # singular + plural
    # Rank by RELEVANCE (matched-token count) instead of catalog order, so
    # topic-central products dominate the writer's list. Catalog-order picking
    # caused "bait-and-switch" articles: a wooden-sign topic got mostly mugs/
    # necklaces (first 12 catalog matches on a generic token) and the writer
    # wrote about those, breaking the title's promise.
    scored = []
    for p in prods:
        if p.get("status") != "active":
            continue
        hay = f"{p.get('title','')} {p.get('product_type','')} {p.get('tags','')}".lower()
        hits = sum(1 for t in toks if t in hay)
        if hits:
            price = None
            try:
                price = float((p.get("variants") or [{}])[0].get("price") or 0) or None
            except (TypeError, ValueError):
                price = None
            scored.append((hits, {"title": p["title"], "handle": p["handle"],
                                  "image": (p.get("image") or {}).get("src"),
                                  "price": price}))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def _embed_product_images(body_md: str, by_handle: dict) -> str:
    """After each gift-idea's product link, drop that product's real photo —
    turns the guide into a visual, conversion-friendly gift list."""
    used: set[str] = set()
    out_lines = []
    for line in body_md.splitlines():
        out_lines.append(line)
        for h in re.findall(r"/products/([a-z0-9-]+)", line):
            p = by_handle.get(h)
            if p and p.get("image") and h not in used:
                used.add(h)
                out_lines.append(f"\n![{p['title']}]({p['image']})\n")
    return "\n".join(out_lines)


def build_article(topic: str, match: list[str], extra_tags: list[str] | None = None,
                  model: str = "gemini-3.1-pro-preview") -> dict | None:
    """Generate one gift-guide article (no publish). Returns a dict with the
    finished body (real product links + inline product photos), SEO fields,
    hero image, and metrics — or None on failure. Reused by the CLI sample and
    the content_imade4u pipeline."""
    prods = _products([m for m in match if m])
    if not prods:
        return None
    by_handle = {p["handle"]: p for p in prods}
    plist = "\n".join(f"  - {p['title']} — {p['handle']}" for p in prods)
    prompt = PROMPT.format(topic=topic, products=plist, trusted_refs=TRUSTED_REFS)

    # A full ~1300-word body inside a JSON field is token-heavy; give Pro room
    # and retry once if the response truncates / isn't valid JSON.
    obj = resp = None
    for attempt in range(2):
        resp = get_llm_provider("gemini").generate(
            prompt=prompt, model=model, max_tokens=10000, temperature=0.6, json_mode=True)
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        i, j = text.find("{"), text.rfind("}")
        try:
            obj = json.loads(text[i:j + 1]); break
        except Exception:
            obj = None
    if obj is None:
        return None

    # strip any product link whose handle isn't real (anti-fabrication)
    real = set(by_handle)
    def _scrub(m):
        h = m.group(2).rstrip("/").split("/")[-1]
        return m.group(0) if h in real else m.group(1)
    body = re.sub(r"\[([^\]]+)\]\(https://imade4u\.com/products/([^)]+)\)", _scrub, obj["body_md"])
    body = _embed_product_images(body, by_handle)
    hero = next((by_handle[h]["image"] for h in re.findall(r"/products/([a-z0-9-]+)", body)
                 if by_handle.get(h, {}).get("image")), None)

    linked_handles = set(re.findall(r"/products/([a-z0-9-]+)", body))
    return {
        "title": obj["title"],
        "body_md": body,
        "products": [p for p in prods if p["handle"] in linked_handles],
        "meta_title": obj.get("meta_title"),
        "meta_description": obj.get("meta_description"),
        "summary": obj.get("summary"),
        "tags": [t for t in (extra_tags or []) if t] + obj.get("tags", []),
        "hero": hero,
        "cost_usd": float(resp.cost_usd or 0),
        "n_product_links": len(set(re.findall(r"/products/([a-z0-9-]+)", body))),
        "n_images": body.count("!["),
        "word_count": len(re.findall(r"[A-Za-z']+", re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body))),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--match", required=True, help="comma keywords to pick relevant products")
    ap.add_argument("--tag", default="", help="comma tags for the article")
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    args = ap.parse_args()

    a = build_article(args.topic, args.match.split(","),
                      [t for t in args.tag.split(",") if t], args.model)
    if not a:
        print("⚠️  generation failed (no products / unparseable)"); return 1
    r = publish_article(
        title=a["title"], content_md=a["body_md"], tags=a["tags"], summary=a["summary"],
        meta_title=a["meta_title"], meta_description=a["meta_description"],
        image_url=a["hero"], image_alt=a["title"], published=False)
    print(f"  ✓ DRAFT created: {a['title']}")
    print(f"    words≈{a['word_count']}  product links: {a['n_product_links']}  "
          f"images: {a['n_images']} (+1 hero)  cost ${a['cost_usd']:.4f}")
    print(f"    review: {r.admin_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
