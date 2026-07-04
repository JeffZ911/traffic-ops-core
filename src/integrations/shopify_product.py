"""imade4u Shopify PRODUCT / COLLECTION writes — the SEO write hop.

Until now the pipeline could RANK a query to a SKU (gift_sample._products
token-ranker) but only READ the catalog. This adds the WRITE half so the Tier-1
harvest can optimize the pages that actually rank: imade4u's PRODUCT pages hold
pos 1 while blog pages sit pos 6-19, so the money page is the PDP.

SEO fields on a Shopify product live in the `global.title_tag` /
`global.description_tag` metafields (same as articles). We set THOSE (the SERP
<title>/meta) and optionally the body_html — NEVER the product's merchandising
`title` (the buyer-facing name shown in cart/checkout; changing it can hurt
conversion and is a merchandiser's call).

Requires the write_products Admin API scope (current token is write_content
only — Jeff provisions it via the Shopify app/OAuth). Reuses shopify_blog's
_api/_cfg verbatim (same IMADE4U_ env, same auth header).
"""
from __future__ import annotations

from typing import Optional

from src.integrations.shopify_blog import _api  # reuse auth/config/transport


def _seo_metafields(meta_title: Optional[str], meta_description: Optional[str]) -> list[dict]:
    mf = []
    if meta_title:
        mf.append({"namespace": "global", "key": "title_tag",
                   "type": "single_line_text_field", "value": meta_title[:70]})
    if meta_description:
        mf.append({"namespace": "global", "key": "description_tag",
                   "type": "single_line_text_field", "value": meta_description[:160]})
    return mf


def list_products(limit: int = 250,
                  fields: str = "id,title,handle,product_type,tags,status,body_html") -> list[dict]:
    """Active + all products (one page). Shares the same fetch the ranker uses."""
    return _api("GET", f"products.json?limit={limit}&fields={fields}").get("products", [])


def get_product(product_id: int) -> dict:
    return _api("GET", f"products/{product_id}.json").get("product", {})


def get_product_by_handle(handle: str,
                          fields: str = "id,title,handle,product_type,tags,status,body_html") -> Optional[dict]:
    """Resolve a single product by its URL handle. Reliable regardless of catalog
    size — the store has ~700 products (many drafts), so a paged list() misses
    most; the handle filter returns the exact live product."""
    prods = _api("GET", f"products.json?handle={handle}&fields={fields}").get("products", [])
    return prods[0] if prods else None


def set_product_seo(product_id: int, *, meta_title: Optional[str] = None,
                    meta_description: Optional[str] = None,
                    body_html: Optional[str] = None) -> dict:
    """Optimize a product's SEO title/meta (+ optional body_html). Inline
    metafields upsert by (namespace,key) on the product PUT. Never touches the
    merchandising `title`. No-op returns the current product. Returns the
    updated product dict (raises RuntimeError with the Shopify body on 4xx —
    e.g. 403 if the write_products scope isn't granted yet)."""
    product: dict = {"id": int(product_id)}
    mf = _seo_metafields(meta_title, meta_description)
    if mf:
        product["metafields"] = mf
    if body_html is not None:
        product["body_html"] = body_html
    if len(product) == 1:            # nothing to write
        return get_product(product_id)
    return _api("PUT", f"products/{product_id}.json",
                {"product": product}).get("product", {})


def create_custom_collection(title: str, *, body_html: str = "",
                             product_ids: Optional[list[int]] = None,
                             meta_title: Optional[str] = None,
                             meta_description: Optional[str] = None,
                             published: bool = False) -> dict:
    """Create a custom collection (published=False → hidden draft). Attaches
    products via collects. Deferred Tier-2 use — kept here so the write hop is
    complete. Returns the collection dict."""
    coll: dict = {"title": title, "body_html": body_html, "published": bool(published)}
    mf = _seo_metafields(meta_title, meta_description)
    if mf:
        coll["metafields"] = mf
    collection = _api("POST", "custom_collections.json",
                      {"custom_collection": coll}).get("custom_collection", {})
    cid = collection.get("id")
    for pid in (product_ids or []):
        try:
            _api("POST", "collects.json",
                 {"collect": {"collection_id": cid, "product_id": int(pid)}})
        except Exception:
            continue
    return collection
