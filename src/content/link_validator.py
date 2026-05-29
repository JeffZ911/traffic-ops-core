"""HTTP link validator — strip genuinely-dead URLs from article markdown.

The link_rewriter handles brand→Amazon + obviously-hallucinated inline
links by anchor/host rules, but it can't tell whether an
editorial-looking URL (rtings.com/some/deep/path, axis.com/learning/X)
actually resolves. The writer agent invents plausible deep paths that
404. This validator does the one thing the rewriter can't: an actual
HTTP request per URL.

Policy — deliberately conservative (false-negative-biased):
  • 404 / 410          → DEAD. Strip the link (keep anchor text / keep
                         the source name, drop the URL).
  • 403 / 429 / 5xx    → KEEP. These are bot-blocks or transient server
                         errors on REAL pages (B&H, Digital Trends,
                         Hikvision/Cloudflare all 403/567 a bare curl).
                         Stripping them would delete legitimate sources.
  • network error /
    timeout / DNS fail → KEEP. Can't prove it's dead; don't touch.

Handles two markdown link shapes:
  1. Inline:   [anchor](https://url)           → strip → "anchor"
  2. Sources:  - Name — https://url            → strip → "- Name"
     (also "- Name - url", "- [Name](url)", and bare "- https://url")

I/O is isolated behind `checker` (default = urllib HEAD→GET) so unit
tests inject a fake. URLs are de-duped + cached within a run so a
backfill over hundreds of articles checks each unique URL once.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

DEAD_CODES = {404, 410}
DEFAULT_TIMEOUT = 8
DEFAULT_WORKERS = 8

# Inline [anchor](url) — http(s) only, allow one level of nested parens.
_INLINE_RE = re.compile(
    r"\[(?P<anchor>[^\[\]\n]+?)\]\((?P<url>https?://[^\s()]+(?:\([^()]*\))?[^\s()]*)\)"
)
# Bare http(s) URL not immediately preceded by ]( or " — i.e. NOT the url
# half of an inline link (those are handled above). Trailing punctuation
# is trimmed after match.
_BARE_RE = re.compile(r"(?<![(\]\"=])\bhttps?://[^\s)\]<>\"']+")

# Trailing chars that are punctuation, not part of the URL.
_TRIM = ".,;:!?）)"


def _norm(url: str) -> str:
    return url.rstrip(_TRIM)


# ───────────────────────────── HTTP checker ─────────────────────────────

def _default_checker(url: str, timeout: int = DEFAULT_TIMEOUT) -> int | None:
    """Return the HTTP status code, or None on a network-level failure.

    Tries HEAD first (cheap); many servers reject HEAD with 405, in which
    case we retry GET. A browser-ish User-Agent reduces spurious 403s."""
    import urllib.error
    import urllib.request

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*",
    }

    def _do(method: str) -> int | None:
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            return None
        except Exception:
            return None

    code = _do("HEAD")
    # Retry with GET when HEAD is unsupported / ambiguous.
    if code in (405, 400, 501) or code is None:
        get_code = _do("GET")
        if get_code is not None:
            return get_code
    return code


# ───────────────────────────── report types ─────────────────────────────

@dataclass
class DeadLink:
    url: str
    status: int
    shape: str          # "inline" | "source"
    anchor: str | None  # anchor text for inline; source name for source


@dataclass
class ValidateReport:
    text: str
    checked: int = 0
    dead: int = 0
    kept: int = 0
    dead_links: list[DeadLink] = field(default_factory=list)
    skipped_no_urls: bool = False

    def summary(self) -> str:
        return f"checked={self.checked} dead-stripped={self.dead} kept={self.kept}"


# ───────────────────────────── core ─────────────────────────────

def _collect_urls(md: str) -> set[str]:
    urls: set[str] = set()
    for m in _INLINE_RE.finditer(md):
        urls.add(_norm(m.group("url")))
    for m in _BARE_RE.finditer(md):
        urls.add(_norm(m.group(0)))
    return urls


def validate_markdown(
    md: str,
    *,
    checker: Callable[[str, int], int | None] = _default_checker,
    timeout: int = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
    cache: dict[str, int | None] | None = None,
) -> ValidateReport:
    """HTTP-check every URL in `md`; strip the ones that are definitively
    dead (404/410). Returns a ValidateReport with rewritten text.

    `cache` (optional) maps url→status across calls so a backfill over
    many articles checks each unique URL once. Pass the same dict in.
    """
    urls = _collect_urls(md)
    if not urls:
        return ValidateReport(text=md, skipped_no_urls=True)

    cache = cache if cache is not None else {}
    to_check = [u for u in urls if u not in cache]

    if to_check:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = ex.map(lambda u: (u, checker(u, timeout)), to_check)
            for u, code in results:
                cache[u] = code

    dead_urls = {u for u in urls if cache.get(u) in DEAD_CODES}

    report = ValidateReport(
        text=md, checked=len(urls), kept=len(urls) - len(dead_urls),
    )
    if not dead_urls:
        return report

    # CRITICAL: never touch the YAML frontmatter block. Split it off and
    # process ONLY the body. (An earlier version ran the line-tidy pass
    # over the whole document and its `[—\-:]$` regex ate the trailing
    # ':' from YAML keys like `sources:` and a '-' from the `---` fence,
    # corrupting frontmatter across every article that had a dead link.)
    frontmatter, body = _split_frontmatter(md)

    # ---- strip dead inline links: [anchor](dead) → anchor ----
    def _strip_inline(m: re.Match) -> str:
        url = _norm(m.group("url"))
        if url in dead_urls:
            report.dead += 1
            report.dead_links.append(
                DeadLink(url, cache.get(url) or 0, "inline", m.group("anchor"))
            )
            return m.group("anchor")
        return m.group(0)

    new_body = _INLINE_RE.sub(_strip_inline, body)

    # ---- strip dead bare URLs (Sources lists etc.) ----
    # Order matters: run AFTER inline so we don't touch inline URLs.
    def _strip_bare(m: re.Match) -> str:
        url = _norm(m.group(0))
        if url not in dead_urls:
            return m.group(0)
        report.dead += 1
        report.dead_links.append(DeadLink(url, cache.get(url) or 0, "source", None))
        return ""

    new_body = _BARE_RE.sub(_strip_bare, new_body)

    # ---- tidy ONLY Sources-list bullet lines left with a dangling
    # separator. Scope is deliberately narrow:
    #   • only lines matching `^\s*[-*]\s` (markdown list items)
    #   • only strip a trailing em/en-dash separator (the Sources
    #     "Name — URL" format) — NEVER ':' or a lone '-' (would damage
    #     real content / fences).
    SEP = re.compile(r"\s*[—–]\s*$")             # em / en dash only
    EMPTY_ITEM = re.compile(r"^\s*[-*]\s*$")     # bullet with nothing left
    tidied = []
    for line in new_body.splitlines():
        if re.match(r"^\s*[-*]\s", line):
            cleaned = SEP.sub("", line.rstrip())
            if EMPTY_ITEM.match(cleaned):
                continue  # drop a now-empty "- " bullet
            tidied.append(cleaned)
        else:
            tidied.append(line)
    new_body = "\n".join(tidied)

    report.text = frontmatter + new_body
    return report


def _split_frontmatter(md: str) -> tuple[str, str]:
    """Return (frontmatter_including_fences, body). If there's no leading
    `---` YAML fence, frontmatter is '' and body is the whole string."""
    if not md.startswith("---"):
        return "", md
    # Match the opening fence line + everything up to and including the
    # closing fence line.
    m = re.match(r"(---\r?\n.*?\r?\n---\r?\n?)(.*)", md, re.DOTALL)
    if not m:
        return "", md
    return m.group(1), m.group(2)
