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

    # VERIFIED ASIN map — case-insensitive anchor substring → ASIN.
    # When a product anchor matches, we emit a direct /dp/<ASIN> link
    # (converts far better than a search page). ASINs are HUMAN-verified
    # and entered via the Dashboard (sites.config.link_rewriter.asin_map);
    # code NEVER invents them — an unmatched anchor falls back to the
    # safe Amazon search link. Longest matching key wins.
    asin_map: dict[str, str] = field(default_factory=dict)


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
    #
    # Guard: if the ANCHOR is itself a URL (the writer sometimes emits a
    # citation as [https://brand.com/x](https://brand.com/x)), it's a
    # source reference, NOT a product mention — never turn it into an
    # Amazon search of the URL string. Let it fall through to the
    # allowlist/keep/strip logic on its actual destination.
    anchor_lc = anchor.lower()
    # URL-like anchors include BARE domains ("eufy.com", "wyze.com/SCPrecall",
    # "ring.com/support") — the writer cites portals this way constantly. These
    # are site references, never product mentions; routing them to an Amazon
    # search hijacks things like a fire-recall refund portal into a nonsense
    # search page (shipped live on quvii before this guard — ~40 links).
    anchor_is_url = bool(
        anchor_lc.startswith(("http://", "https://", "www."))
        or re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,6}(/\S*)?$", anchor_lc)
    )
    if not anchor_is_url:
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
    """Affiliate URL for a product anchor: a direct /dp/ link when a
    human-verified ASIN matches the anchor (longest key wins), else the
    safe Amazon search link. Both carry the site's tag."""
    anchor_lc = anchor.strip().lower()
    best = ""
    for key in rule.asin_map:
        k = key.strip().lower()
        if k and k in anchor_lc and len(k) > len(best):
            best = k
    if best:
        asin = rule.asin_map.get(best) or next(
            v for kk, v in rule.asin_map.items() if kk.strip().lower() == best)
        base = f"https://www.amazon.{rule.amazon_tld}/dp/{asin}"
        if rule.amazon_tag:
            base += f"?tag={rule.amazon_tag}"
        return base
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

# Amazon Associates tag — shared across all Jeff's sites today.
# A new site automatically inherits this. Override only via
# sites.config.link_rewriter.amazon_tag if a site needs a different
# affiliate account.
DEFAULT_AMAZON_TAG = "jeffzen911-20"


# ─────────────────────────────────────────────────────────────────────
# NICHE_DEFAULTS — keyed by sites.config.niche, NOT by domain.
#
# A new site (e.g. "smartthings-fan-blog.com") with niche="smart_home"
# automatically inherits the smart_home brand_patterns + allowlists.
# No code changes needed to onboard the new domain — just set its
# niche in the bootstrap_<site>.py + sites.config.
#
# Add a new niche entry below when a brand-new category launches.
# Per-site overrides go in DEFAULTS_BY_DOMAIN (rare) or in
# sites.config.link_rewriter (Dashboard-edited, takes top precedence).
# ─────────────────────────────────────────────────────────────────────

NICHE_DEFAULTS: dict[str, dict] = {
    # ──────────────── security_cameras (quvii) ────────────────
    "security_cameras": {
        "brand_patterns": [
            # Major D2C/retail security camera brands.
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

    # ──────────────── gaming (ntecodex) ────────────────
    "gaming": {
        "brand_patterns": [
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

    # ──────────────── ecommerce_tools (pixelmatch) ────────────────
    "ecommerce_tools": {
        "brand_patterns": [
            # Physical hardware brands relevant to FBA / Etsy / Shopify
            # sellers (camera gear, lighting, color tools). SaaS brands
            # (Canva, Adobe, Claid) are intentionally absent — Amazon
            # search for "Canva" returns unrelated merch.
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
            # Path-specific only. SaaS apex domains excluded —
            # LLM hallucinates canva.com/X and claid.ai/X.
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

    # ──────────────── smart_home (future Quvii-adjacent niches) ────────────────
    "smart_home": {
        "brand_patterns": [
            # Smart locks, doorbells, hubs, lighting, plugs.
            "philips hue", "hue",
            "lifx",
            "lutron", "caseta",
            "ecobee",
            "honeywell home",
            "nest", "google nest",
            "amazon echo", "echo dot", "echo show",
            "ring", "ring doorbell",
            "august lock", "august smart",
            "yale", "schlage",
            "kwikset",
            "level lock",
            "wemo",
            "tp-link", "kasa", "tapo",
            "smartthings", "samsung smartthings",
            "aqara",
            "lockly",
            "ubiquiti", "unifi",
            "eero",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "rtings.com",
            "wirecutter.com",
            "theverge.com",
            "tomsguide.com",
            "cnet.com",
            "thesweethome.com",
            "the-ambient.com",
            "stacey-on-iot.com",
        ],
        "manufacturer_allowlist": [
            "philips-hue.com", "meethue.com",
            "lifx.com",
            "lutron.com", "casetawireless.com",
            "ecobee.com",
            "honeywellhome.com",
            "store.google.com",
            "ring.com",
            "august.com",
            "yalehome.com",
            "schlage.com",
            "level.co",
            "smartthings.com",
            "aqara.com",
            "ui.com",          # ubiquiti
            "eero.com",
        ],
    },

    # ──────────────── audio_gear ────────────────
    "audio_gear": {
        "brand_patterns": [
            "bose",
            "sony wh", "sony wf",
            "sennheiser",
            "shure",
            "audio-technica", "audio technica",
            "akg",
            "beyerdynamic",
            "focal",
            "klipsch",
            "kef",
            "jbl",
            "sonos",
            "apple airpods", "airpods",
            "beats", "beats by dre",
            "anker soundcore", "soundcore",
            "jabra",
            "razer",
            "logitech",
            "hifiman",
            "drop",
            "schiit",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "rtings.com",
            "wirecutter.com",
            "theverge.com",
            "whathifi.com",
            "soundguys.com",
            "head-fi.org",
            "audioholics.com",
        ],
        "manufacturer_allowlist": [
            "bose.com",
            "sony.com",
            "sennheiser.com",
            "shure.com",
            "audio-technica.com",
            "sonos.com",
            "apple.com/airpods",
            "beatsbydre.com",
            "soundcore.com",
        ],
    },

    # ──────────────── home_office / desk-setup ────────────────
    "home_office": {
        "brand_patterns": [
            "uplift desk", "uplift",
            "fully", "jarvis", "fully jarvis",
            "secretlab",
            "herman miller",
            "steelcase",
            "humanscale",
            "autonomous",
            "ikea bekant", "ikea markus",
            "branch furniture",
            "vari", "vari desk",
            "flexispot",
            "logitech",
            "anker",
            "elgato",
            "balance ball",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "rtings.com",
            "wirecutter.com",
            "theverge.com",
            "tomsguide.com",
            "ergonomics.com",
            "officechairsource.com",
        ],
        "manufacturer_allowlist": [
            "upliftdesk.com",
            "fully.com",
            "secretlab.co",
            "hermanmiller.com",
            "steelcase.com",
            "humanscale.com",
            "autonomous.ai",
            "flexispot.com",
            "logitech.com",
        ],
    },

    # ──────────────── fitness_gear ────────────────
    "fitness_gear": {
        "brand_patterns": [
            "peloton",
            "nordictrack",
            "bowflex",
            "lululemon",
            "garmin", "garmin fenix", "garmin forerunner",
            "apple watch",
            "fitbit",
            "polar",
            "whoop",
            "oura", "oura ring",
            "theragun", "therabody",
            "hyperice",
            "rogue fitness", "rogue",
            "concept2",
            "tonal",
            "mirror",
            "hyrox",
            "rumble roller",
            "trx",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "wirecutter.com",
            "rtings.com",
            "theverge.com",
            "outdoorgearlab.com",
            "runnersworld.com",
            "selfmagazine.com",
            "menshealth.com",
            "womenshealthmag.com",
        ],
        "manufacturer_allowlist": [
            "onepeloton.com",
            "nordictrack.com",
            "bowflex.com",
            "garmin.com",
            "fitbit.com",
            "ouraring.com",
            "whoop.com",
            "therabody.com",
            "concept2.com",
            "roguefitness.com",
            "tonal.com",
            "lululemon.com",
        ],
    },

    # ──────────────── pet_products ────────────────
    "pet_products": {
        "brand_patterns": [
            "petcube",
            "furbo",
            "wyze cam pan",       # also caught by gaming/security cams
            "litter-robot", "litter robot",
            "petsafe",
            "sureflap",
            "kong",
            "chuckit",
            "outward hound",
            "petfusion",
            "snuggle puppy",
            "frisco",
            "chewy",              # brand often appears as anchor
            "blue buffalo",
            "purina",
            "hill's", "hills science diet",
            "royal canin",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "wirecutter.com",
            "rover.com",
            "akc.org",
            "aspca.org",
            "petmd.com",
            "consumeraffairs.com",
        ],
        "manufacturer_allowlist": [
            "petcube.com",
            "furbo.com",
            "litter-robot.com",
            "petsafe.net",
            "sureflap.com",
            "chewy.com",
        ],
    },

    # ──────────────── outdoor_recreation ────────────────
    "outdoor_recreation": {
        "brand_patterns": [
            "yeti",
            "rtic",
            "hydro flask",
            "stanley",
            "patagonia",
            "the north face", "north face",
            "rei", "rei co-op",
            "msr",
            "big agnes",
            "nemo",
            "rei", "kelty",
            "osprey",
            "thermarest",
            "garmin inreach", "inreach",
            "goal zero",
            "jackery",
            "ego power+", "ego",
            "blackstone",
            "weber",
            "traeger",
            "coleman",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "wirecutter.com",
            "outdoorgearlab.com",
            "thedyrt.com",
            "treelinereview.com",
            "switchbacktravel.com",
            "outsideonline.com",
            "rei.com/learn",
            "backpacker.com",
        ],
        "manufacturer_allowlist": [
            "yeti.com",
            "hydroflask.com",
            "stanley1913.com",
            "patagonia.com",
            "thenorthface.com",
            "rei.com",
            "ospreypacks.com",
            "garmin.com",
            "goalzero.com",
            "jackery.com",
        ],
    },

    # ──────────────── creator_tools (cameras, mics, streaming) ────────────────
    "creator_tools": {
        "brand_patterns": [
            "sony alpha", "sony a7", "sony a6",
            "canon eos", "canon r5", "canon r6",
            "fujifilm xt", "fujifilm x-t",
            "panasonic lumix",
            "blackmagic", "blackmagic pocket",
            "dji",
            "gopro",
            "insta360",
            "rode", "rode wireless go", "rode podmic",
            "shure",
            "elgato",
            "stream deck",
            "atomos",
            "godox",
            "aputure",
            "manfrotto",
            "peak design",
            "smallrig",
            "tilta",
        ],
        "editorial_allowlist": SHARED_EDITORIAL + [
            "dpreview.com",
            "the-verge.com",
            "theverge.com",
            "wirecutter.com",
            "petapixel.com",
            "noamkroll.com",
            "fstoppers.com",
            "diyphotography.net",
        ],
        "manufacturer_allowlist": [
            "sony.com",
            "canon.com",
            "fujifilm-x.com",
            "panasonic.com",
            "blackmagicdesign.com",
            "dji.com",
            "gopro.com",
            "insta360.com",
            "rode.com",
            "elgato.com",
            "atomos.com",
            "godox.com",
            "aputure.com",
            "peakdesign.com",
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────
# DEFAULTS_BY_DOMAIN — site-specific overrides for the niche defaults.
#
# Rare. Use only when a single site within a niche needs different
# brand_patterns than the niche default (e.g. one quvii sister-site
# focuses on commercial CCTV instead of consumer cams). Most sites
# inherit niche defaults and don't need anything here.
# ─────────────────────────────────────────────────────────────────────

DEFAULTS_BY_DOMAIN: dict[str, dict] = {
    # (empty by default — niche resolution handles all 3 existing
    # sites. Add domain-specific overrides here if a site diverges
    # from its niche's defaults.)
}


def rule_for_site(
    domain: str,
    niche: str | None = None,
    override: dict | None = None,
) -> RewriteRule:
    """Resolve the RewriteRule for a site.

    Precedence (top wins):
      1. `override`              — sites.config.link_rewriter from DB
      2. DEFAULTS_BY_DOMAIN[domain] — explicit per-domain config
      3. NICHE_DEFAULTS[niche]     — niche-shared defaults
      4. empty rule (rewrite is a no-op)

    A new site only needs `niche` set to inherit a working rule. The
    Amazon Associates tag defaults to DEFAULT_AMAZON_TAG.
    """
    base: dict = {}
    if niche and niche in NICHE_DEFAULTS:
        base = {**NICHE_DEFAULTS[niche]}
    if domain in DEFAULTS_BY_DOMAIN:
        base = {**base, **{k: v for k, v in DEFAULTS_BY_DOMAIN[domain].items() if v}}
    if override:
        base = {**base, **{k: v for k, v in override.items() if v}}

    return RewriteRule(
        amazon_tag=base.get("amazon_tag", DEFAULT_AMAZON_TAG),
        amazon_tld=base.get("amazon_tld", "com"),
        brand_patterns=list(base.get("brand_patterns") or []),
        editorial_allowlist=list(base.get("editorial_allowlist") or []),
        manufacturer_allowlist=list(base.get("manufacturer_allowlist") or []),
        asin_map=dict(base.get("asin_map") or {}),
    )


# Back-compat shim — existing callers used rule_for_domain(). New code
# should call rule_for_site(domain, niche=...).
_NICHE_BY_LEGACY_DOMAIN = {
    "quvii.com":      "security_cameras",
    "ntecodex.com":   "gaming",
    "pixelmatch.art": "ecommerce_tools",
}


def rule_for_domain(domain: str, override: dict | None = None) -> RewriteRule:
    """Deprecated. Use rule_for_site(domain, niche=...) instead.
    Kept for the 3 existing sites whose niche we know from their
    domain. New sites should always call rule_for_site directly."""
    niche = _NICHE_BY_LEGACY_DOMAIN.get(domain)
    return rule_for_site(domain, niche=niche, override=override)
