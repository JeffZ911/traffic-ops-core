"""Cross-site markdown link rewriter.

The writer agent (Gemini Flash) reliably hallucinates external URLs —
plausible-looking links that 404. This module is the defensive layer:
scan a piece of markdown for `[anchor](url)` external links, classify
each one, and rewrite.

Three outcomes per link:

  1. KEEP — URL host is on the per-site `external_allowlist` (Wikipedia,
     official manufacturer apex, RTINGS/Wirecutter/The Verge, FCC,
     IEEE, direct Amazon product pages, etc.). The site's editorial
     position trusts these.

  2. AMAZON — Anchor text contains a per-site `brand_pattern`
     (substring match). Rewrite the URL to an Amazon search URL with
     the site's Amazon Associates tag, and tag the link with
     `rel="sponsored nofollow noopener"` per FTC / Google guidance.

  3. STRIP — Default. Remove the URL, keep the anchor as plain text.
     Hallucinated terminology links ("Wi-Fi 6", "USB-C power",
     "Target Wake Time") become bold-but-unlinked phrases rather than
     dead links.

The rewriter is pure (no I/O), takes a site_config dict, returns
rewritten markdown + a diff report for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlparse
from typing import Literal


@dataclass
class RewriteRule:
    """Per-site config for the rewriter. Wraps what comes out of
    sites.config under `affiliate` + `link_rewriter`."""

    # Amazon Associates tag (jeffzen911-20 across all 3 sites today).
    amazon_tag: str = ""
    amazon_tld: str = "com"

    # Anchor substring matches that should rewrite to Amazon search.
    # Case-insensitive substring match against the anchor text.
    brand_patterns: list[str] = field(default_factory=list)

    # EDITORIAL trust tier — independent reviewers, encyclopedias,
    # standards bodies. These trump brand-anchor matching, because we
    # trust RTINGS / Wirecutter / Wikipedia / FCC to link them
    # correctly even when the anchor mentions a brand.
    editorial_allowlist: list[str] = field(default_factory=list)

    # MANUFACTURER trust tier — eufy.com, ring.com, canva.com, etc.
    # LLMs frequently hallucinate these. Only honored if the anchor
    # does NOT match a brand pattern (i.e. it's a non-product link to
    # a manufacturer page, like a help-center article). For
    # brand-anchor links, Amazon wins.
    manufacturer_allowlist: list[str] = field(default_factory=list)


# Inline-link regex: matches [anchor](url) where neither contains
# unescaped brackets/parens. Allows nested parens in URL up to one
# level (Wikipedia URLs commonly have parens). Anchor can span multiple
# words but no newline.
_LINK_RE = re.compile(
    r"\[(?P<anchor>[^\[\]\n]+?)\]\((?P<url>[^\s()]+(?:\([^()]*\))?[^\s()]*)\)"
)


Action = Literal["keep", "amazon", "strip"]


@dataclass
class LinkChange:
    anchor: str
    original_url: str
    new_url: str | None       # None when stripped
    action: Action

    def render(self) -> str:
        """Markdown rendering for the rewritten anchor."""
        if self.action == "strip":
            return self.anchor
        if self.action == "amazon":
            # Trailing space + HTML attr block keeps the markdown
            # parseable. Astro's default markdown plugin honors raw
            # HTML, so we emit a raw <a> tag for affiliate links so
            # rel="sponsored nofollow noopener" survives.
            return (
                f'<a href="{self.new_url}" '
                f'rel="sponsored nofollow noopener" '
                f'target="_blank">{self.anchor}</a>'
            )
        # "keep"
        return f"[{self.anchor}]({self.new_url})"


@dataclass
class RewriteReport:
    text: str
    changes: list[LinkChange]
    # Counts for quick UI:
    kept: int = 0
    rewritten: int = 0
    stripped: int = 0

    def summary(self) -> str:
        return (
            f"kept={self.kept} · rewritten→amazon={self.rewritten} · "
            f"stripped={self.stripped}  (total {len(self.changes)})"
        )


def _classify(anchor: str, url: str, rule: RewriteRule) -> Action:
    """Decide what to do with one [anchor](url) pair.

    Order matters here — brand-match wins over allowlist because the
    writer agent frequently invents plausible manufacturer URLs
    (eufy.com/solocam-fake, ring.com/whatever) that 404. We can't trust
    LLM URLs for product anchors, so anything that LOOKS like a brand
    anchor is force-routed to Amazon search regardless of what the
    URL claims to be."""
    # 1. Page-internal anchors / non-http schemes — leave alone.
    if not url.lower().startswith(("http://", "https://")):
        return "keep"

    url_lc = url.lower()

    # 2. KEEP — editorial source allowlist. RTINGS / Wirecutter /
    # Wikipedia / FCC etc. are trusted even when the anchor mentions a
    # brand, because we're citing their review of the brand, not
    # the brand's own site.
    for needle in rule.editorial_allowlist:
        if needle and needle in url_lc:
            return "keep"

    # 3. AMAZON — anchor has a brand pattern. Word-boundary match so
    # "asus" doesn't fire on "Pegasus" and "ring" doesn't fire on
    # "string" / "spring". The brand has to start at a word boundary
    # in the anchor.
    anchor_lc = anchor.lower()
    for brand in rule.brand_patterns:
        if not brand:
            continue
        b = brand.lower()
        if re.search(r"\b" + re.escape(b), anchor_lc):
            return "amazon"

    # 4. AMAZON — any amazon.com URL that isn't a verified /dp/ or
    # /gp/ product page. This catches:
    #   - hallucinated paths    /amazon.com/security-camera-X-Y
    #   - LLM-built search URLs /amazon.com/s?k=foo  (these work but
    #     lack our affiliate tag — rewrite to capture it)
    #   - half-formed product   /amazon.com/anti-theft-tether/s?k=...
    # Real product pages on /dp/ and /gp/ get caught by editorial
    # allowlist above and survive untouched.
    if "amazon." in url_lc and rule.amazon_tag:
        return "amazon"

    # 5. KEEP — non-brand anchor, manufacturer-allowlist hit. (E.g.
    # a generic "Eufy support center" link where anchor doesn't match
    # brand pattern, or a Shopify help doc.)
    for needle in rule.manufacturer_allowlist:
        if needle and needle in url_lc:
            return "keep"

    # 6. Default — strip URL, keep anchor as plain text.
    return "strip"


def _to_amazon_search(anchor: str, rule: RewriteRule) -> str:
    """Build an Amazon search URL with the site's affiliate tag."""
    q = quote_plus(anchor.strip())
    base = f"https://www.amazon.{rule.amazon_tld}/s?k={q}"
    if rule.amazon_tag:
        base += f"&tag={rule.amazon_tag}"
    return base


def rewrite_markdown(md: str, rule: RewriteRule) -> RewriteReport:
    """Rewrite all external [anchor](url) links in `md` per `rule`.

    Returns a RewriteReport with the new text and a per-link change log.
    """
    changes: list[LinkChange] = []
    kept = rewritten = stripped = 0

    def replace(match: re.Match) -> str:
        nonlocal kept, rewritten, stripped
        anchor = match.group("anchor")
        url = match.group("url")
        action = _classify(anchor, url, rule)

        if action == "keep":
            kept += 1
            change = LinkChange(anchor, url, url, "keep")
        elif action == "amazon":
            new_url = _to_amazon_search(anchor, rule)
            rewritten += 1
            change = LinkChange(anchor, url, new_url, "amazon")
        else:
            stripped += 1
            change = LinkChange(anchor, url, None, "strip")

        changes.append(change)
        return change.render()

    new_text = _LINK_RE.sub(replace, md)
    return RewriteReport(
        text=new_text, changes=changes,
        kept=kept, rewritten=rewritten, stripped=stripped,
    )


# ─────────────────────────────────────────────────────────────────────
# Per-site defaults — what gets seeded into sites.config.link_rewriter
# on first migration. Operator can edit the JSON in DB later via the
# Dashboard; nothing here is hardcoded into the pipeline.
# ─────────────────────────────────────────────────────────────────────

# Shared EDITORIAL allowlist — independent / authoritative sources.
# These trump brand-anchor matching because they're third-party citations,
# not the brand's own site.
SHARED_EDITORIAL = [
    "wikipedia.org",
    "amazon.com/dp/",         # direct Amazon product pages (real listings)
    "amazon.com/gp/",         # alt product page URL form
    "fcc.gov",
    "ieee.org",
    "schema.org",
    "developers.google.com",
    "support.google.com",
    "github.com",
    "ftc.gov",
]

DEFAULTS_BY_DOMAIN: dict[str, dict] = {
    # ──────────────── quvii.com (security cameras) ────────────────
    "quvii.com": {
        "amazon_tag": "jeffzen911-20",
        "amazon_tld": "com",
        "brand_patterns": [
            # Major D2C/retail security camera brands. Anchor substrings.
            "eufy", "eufycam", "solocam",
            "ring",
            "arlo",
            "blink",
            "reolink",
            "nest", "google nest",
            "wyze",
            "lorex",
            "amcrest",
            "aosu",
            "hikvision",
            "dahua",
            "imou",
            "tp-link", "tapo", "kasa",
            "swann",
            "annke",
            "anker",
        ],
        # Third-party editorial sources we trust to cite the brand correctly.
        "editorial_allowlist": SHARED_EDITORIAL + [
            "rtings.com",
            "wirecutter.com",
            "theverge.com",
            "tomsguide.com",
            "cnet.com",
            "consumerreports.org",
            "techhive.com",
            "reddit.com/r/homesecurity",
            "reddit.com/r/homedefense",
            "reddit.com/r/amazonring",
        ],
        # Manufacturer official sites — kept only when anchor is NOT brand-like
        # (e.g. a help-center / privacy-policy link, not a product link).
        "manufacturer_allowlist": [
            "eufy.com", "us.eufy.com",
            "ring.com",
            "arlo.com",
            "blink.com", "blinkforhome.com",
            "reolink.com",
            "store.google.com",
            "wyze.com",
            "lorex.com",
            "amcrest.com",
        ],
    },

    # ──────────────── ntecodex.com (gaming + adjacent gear) ────────────────
    "ntecodex.com": {
        "amazon_tag": "jeffzen911-20",
        "amazon_tld": "com",
        "brand_patterns": [
            # Physical-product brands ntecodex articles recommend.
            "secretlab",
            "autonomous",
            "uplift desk", "uplift",
            "herman miller",
            "steelcase",
            "razer",
            "logitech",
            "corsair",
            "alienware",
            "asus", "rog",
            "acer", "predator",
            "samsung", "odyssey",
            "lg", "ultragear",
            "msi",
            "sony", "playstation",
            "microsoft", "xbox",
            "nintendo",
            "steam deck",
            "anker",
            "elgato",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "rtings.com",
            "wirecutter.com",
            "theverge.com",
            "tomsguide.com",
            "ign.com",
            "polygon.com",
            "rockpapershotgun.com",
            "pcgamer.com",
            "reddit.com",
        ],
        "manufacturer_allowlist": [
            "secretlab.co",
            "autonomous.ai",
            "upliftdesk.com",
            "hermanmiller.com",
            "razer.com",
            "logitech.com", "logitechg.com",
            "corsair.com",
            "alienware.com",
            "asus.com", "rog.asus.com",
            "samsung.com",
            "lg.com",
            "playstation.com",
            "xbox.com",
            "nintendo.com",
            "store.steampowered.com",
            "epicgames.com",
            "hoyoverse.com",
            "mihoyo.com",
        ],
    },

    # ──────────────── pixelmatch.art (e-commerce / AI image tools) ────────────────
    "pixelmatch.art": {
        "amazon_tag": "jeffzen911-20",
        "amazon_tld": "com",
        "brand_patterns": [
            # Physical product brands appropriate for Amazon search.
            # SaaS tools (Canva, Adobe, Claid) are intentionally NOT
            # in brand_patterns — Amazon search for "Canva" surfaces
            # unrelated merch. SaaS brand anchors will strip cleanly.
            "godox",
            "neewer",
            "westcott",
            "manfrotto",
            "joby",
            "elgato",
            "sony alpha",
            "canon eos",
            "fujifilm",
            "panasonic lumix",
            "color checker", "colorchecker",
            "x-rite",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "shopify.com/blog",
            "etsy.com/seller-handbook",
            "help.shopify.com",
            "sellercentral.amazon.com",
            "sell.amazon.com",
            "amazon.com/sellercentral",
            "support.tiktokshop.com",
            "tiktokshop.com",
            "theverge.com",
            "wired.com",
        ],
        "manufacturer_allowlist": [
            # Path-specific entries only. Apex SaaS domains are
            # excluded because LLM hallucinates URLs like
            # claid.ai/fake-url and canva.com/nonexistent. Specific
            # known-stable paths (docs, blogs, official help) survive;
            # generic marketing URLs strip cleanly.
            "canva.com/help",
            "canva.com/blog",
            "helpx.adobe.com",
            "adobe.com/products",
            "shopify.dev",
            "support.shopify.com",
            "help.etsy.com",
            "sellercentral.amazon.com",
            "openai.com/blog",
            "anthropic.com/news",
        ],
    },
}


def rule_for_domain(domain: str, override: dict | None = None) -> RewriteRule:
    """Resolve the RewriteRule for a site domain.

    Precedence: explicit override (e.g. from sites.config.link_rewriter
    fetched at runtime) → DEFAULTS_BY_DOMAIN[domain] → empty rule.
    """
    base = DEFAULTS_BY_DOMAIN.get(domain, {})
    if override:
        # Shallow merge — override fields take precedence.
        base = {**base, **{k: v for k, v in override.items() if v}}
    return RewriteRule(
        amazon_tag=base.get("amazon_tag", ""),
        amazon_tld=base.get("amazon_tld", "com"),
        brand_patterns=list(base.get("brand_patterns") or []),
        editorial_allowlist=list(base.get("editorial_allowlist") or []),
        manufacturer_allowlist=list(base.get("manufacturer_allowlist") or []),
    )
