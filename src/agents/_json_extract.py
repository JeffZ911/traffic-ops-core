"""Extract a JSON object from a possibly-wrapped LLM response.

When google_search grounding is on, response_mime_type=application/json is
not allowed, so JSON output may arrive embedded in plain text or fenced
markdown. This helper finds the largest balanced { ... } block and parses it.
"""

from __future__ import annotations

import json
import re


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def extract_json(text: str) -> dict:
    s = text.strip()

    # Strip markdown fence if present
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()

    # Direct parse
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Find a balanced { ... } block (the longest top-level one)
    start = s.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Found {{...}} block but JSON parse failed: {e}; "
                        f"first 200 chars: {candidate[:200]!r}"
                    ) from None

    raise ValueError(f"unbalanced braces in response: {text[:200]!r}")
