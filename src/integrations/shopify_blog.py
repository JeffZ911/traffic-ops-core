"""Shopify Blog publish adapter — the imade4u tenant's publish backend.

Other tenants publish by writing a Markdown file into an Astro site repo
(git → Cloudflare Pages). imade4u instead publishes the SAME generated content
straight into its existing Shopify blog via the Admin API — no Astro, no repo,
no migration. The QDF content brain (keywords/trend/writing/QA/AI loop) is
unchanged; only this final hop differs.

Auth/config from env (per-site IMADE4U_ prefix; tokens live in .env / CF env):
  IMADE4U_SHOPIFY_DOMAIN        e.g. 5ff20f-15.myshopify.com
  IMADE4U_SHOPIFY_ADMIN_TOKEN   shpat_... (write_content scope)
  IMADE4U_SHOPIFY_API_VERSION   e.g. 2026-04
  IMADE4U_SHOPIFY_BLOG_HANDLE   default 'personalized-gifting-guide'

SEO note: a Shopify article's <title>/meta description come from the metafields
`global.title_tag` / `global.description_tag` — we set them on create.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

import markdown as _md


@dataclass
class ShopifyArticleResult:
    id: int
    handle: str
    url: str
    admin_url: str
    published: bool


def _cfg() -> tuple[str, str, str]:
    dom = os.getenv("IMADE4U_SHOPIFY_DOMAIN")
    tok = os.getenv("IMADE4U_SHOPIFY_ADMIN_TOKEN")
    ver = os.getenv("IMADE4U_SHOPIFY_API_VERSION", "2026-04")
    if not dom or not tok:
        raise RuntimeError("IMADE4U_SHOPIFY_DOMAIN / _ADMIN_TOKEN not set in env")
    return dom, tok, ver


def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    dom, tok, ver = _cfg()
    url = f"https://{dom}/admin/api/{ver}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Shopify {method} {path} → {e.code}: {detail}") from e


def resolve_blog_id(handle: Optional[str] = None) -> int:
    """Blog id for the target blog handle (default the gifting guide)."""
    handle = handle or os.getenv("IMADE4U_SHOPIFY_BLOG_HANDLE") or "personalized-gifting-guide"
    blogs = _api("GET", "blogs.json?fields=id,handle").get("blogs", [])
    for b in blogs:
        if b.get("handle") == handle:
            return int(b["id"])
    if blogs:  # fall back to the first blog
        return int(blogs[0]["id"])
    raise RuntimeError("no blogs found on the store")


def markdown_to_html(content_md: str) -> str:
    """Convert article markdown → HTML for Shopify body_html. Strips a leading
    H1 (Shopify renders the article title separately, so a body H1 duplicates it)."""
    body = re.sub(r"^\s*#\s+.*?(?:\n|$)", "", content_md, count=1)  # drop first H1
    return _md.markdown(
        body, extensions=["extra", "sane_lists", "tables", "smarty"], output_format="html5"
    )


def publish_article(
    *,
    title: str,
    content_md: str,
    tags: Optional[list[str]] = None,
    summary: Optional[str] = None,
    meta_title: Optional[str] = None,
    meta_description: Optional[str] = None,
    image_url: Optional[str] = None,
    image_alt: Optional[str] = None,
    handle: Optional[str] = None,
    blog_handle: Optional[str] = None,
    author: str = "iMade4U",
    published: bool = False,
) -> ShopifyArticleResult:
    """Create an article in the Shopify blog. `published=False` → DRAFT (safe,
    not publicly visible) — the pipeline flips it True only after QA passes."""
    dom, _, _ = _cfg()
    blog_id = resolve_blog_id(blog_handle)
    body_html = markdown_to_html(content_md)

    article: dict = {
        "title": title,
        "body_html": body_html,
        "author": author,
        "published": bool(published),
    }
    if handle:
        article["handle"] = handle
    if tags:
        article["tags"] = ", ".join(tags)
    if summary:
        article["summary_html"] = f"<p>{summary}</p>"
    if image_url:
        article["image"] = {"src": image_url, "alt": image_alt or title}
    metafields = []
    if meta_title:
        metafields.append({"namespace": "global", "key": "title_tag",
                           "type": "single_line_text_field", "value": meta_title[:70]})
    if meta_description:
        metafields.append({"namespace": "global", "key": "description_tag",
                           "type": "single_line_text_field", "value": meta_description[:160]})
    if metafields:
        article["metafields"] = metafields

    out = _api("POST", f"blogs/{blog_id}/articles.json", {"article": article})["article"]
    aid, ahandle = int(out["id"]), out.get("handle", "")
    blog_handle_resolved = blog_handle or os.getenv("IMADE4U_SHOPIFY_BLOG_HANDLE") or "personalized-gifting-guide"
    public_domain = dom.replace(".myshopify.com", "")  # only for admin link host
    return ShopifyArticleResult(
        id=aid,
        handle=ahandle,
        url=f"https://imade4u.com/blogs/{blog_handle_resolved}/{ahandle}",
        admin_url=f"https://admin.shopify.com/store/{public_domain}/content/articles/{aid}",
        published=bool(out.get("published_at")),
    )


def delete_article(article_id: int, blog_handle: Optional[str] = None) -> None:
    blog_id = resolve_blog_id(blog_handle)
    _api("DELETE", f"blogs/{blog_id}/articles/{article_id}.json")
