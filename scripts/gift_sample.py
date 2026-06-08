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
- 900-1400 words. Use H2/H3 structure, a short intro, 6-9 gift ideas, a couple
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


def _products(match: list[str], limit: int = 12) -> list[tuple[str, str]]:
    dom = os.getenv("IMADE4U_SHOPIFY_DOMAIN"); tok = os.getenv("IMADE4U_SHOPIFY_ADMIN_TOKEN")
    ver = os.getenv("IMADE4U_SHOPIFY_API_VERSION", "2026-04")
    req = urllib.request.Request(
        f"https://{dom}/admin/api/{ver}/products.json?limit=250&fields=title,handle,product_type,tags,status",
        headers={"X-Shopify-Access-Token": tok})
    prods = json.load(urllib.request.urlopen(req, timeout=30))["products"]
    out = []
    for p in prods:
        if p.get("status") != "active":
            continue
        hay = f"{p.get('title','')} {p.get('product_type','')} {p.get('tags','')}".lower()
        if any(m.strip().lower() in hay for m in match):
            out.append((p["title"], p["handle"]))
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--match", required=True, help="comma keywords to pick relevant products")
    ap.add_argument("--tag", default="", help="comma tags for the article")
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    args = ap.parse_args()

    prods = _products([m for m in args.match.split(",") if m])
    if not prods:
        print("⚠️  no matching products found"); return 1
    plist = "\n".join(f"  - {t} — {h}" for t, h in prods)
    prompt = PROMPT.format(topic=args.topic, products=plist)

    resp = get_llm_provider("gemini").generate(
        prompt=prompt, model=args.model, max_tokens=6000, temperature=0.6, json_mode=True)
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    i, j = text.find("{"), text.rfind("}")
    obj = json.loads(text[i:j + 1])

    # safety: strip any product links whose handle isn't real
    real = {h for _, h in prods}
    def _scrub(m):
        h = m.group(2).rstrip("/").split("/")[-1]
        return m.group(0) if h in real else m.group(1)  # keep text, drop bad link
    body = re.sub(r"\[([^\]]+)\]\(https://imade4u\.com/products/([^)]+)\)", _scrub, obj["body_md"])

    r = publish_article(
        title=obj["title"], content_md=body,
        tags=[t for t in (args.tag.split(",") if args.tag else []) if t] + obj.get("tags", []),
        summary=obj.get("summary"), meta_title=obj.get("meta_title"),
        meta_description=obj.get("meta_description"), published=False)
    nlinks = len(re.findall(r"\(https://imade4u\.com/products/", body))
    print(f"  ✓ DRAFT created: {obj['title']}")
    print(f"    words≈{len(re.findall(r'\\w+', body))}  product links: {nlinks}  cost ${resp.cost_usd:.4f}")
    print(f"    review: {r.admin_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
