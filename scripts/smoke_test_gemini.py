"""
Real Gemini API smoke test.

Iterates every active/preview row in model_catalog. For each text model,
sends a minimal 'PONG' ping; image models are listed but skipped (image
smoke is a separate workflow).

Does NOT auto-mark deprecated models — only reports. Operator decides
whether to run mark_deprecated() or update model_catalog directly.

Usage:
    python -m scripts.smoke_test_gemini
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.model_catalog_client import list_active_models
from src.utils.llm import LLMError, get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


PROMPT = "Reply with the single word 'PONG' and nothing else."


def _candidate_names(model_id: str) -> list[str]:
    """Suggest alternate IDs to check in AI Studio when a model 404s."""
    candidates: list[str] = []
    base = model_id

    # Strip "-preview" suffix
    if base.endswith("-preview"):
        candidates.append(base[: -len("-preview")])

    # Step major versions down (3.1 → 3.0, 3 → 2.5)
    if base.startswith("gemini-3.1"):
        candidates.append(base.replace("gemini-3.1", "gemini-3.0", 1))
        candidates.append(base.replace("gemini-3.1", "gemini-3", 1))
    if base.startswith("gemini-3"):
        candidates.append(base.replace("gemini-3", "gemini-2.5", 1))

    # Drop the variant tail entirely (e.g. -flash-image-preview → -flash)
    if "-image" in base:
        candidates.append(base.replace("-image", ""))

    # Dedupe while preserving order
    seen = set()
    out = []
    for c in candidates:
        if c != model_id and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:5]


def main() -> int:
    print("=" * 78)
    print("=== Gemini Text Models Smoke Test ===")
    print("=" * 78)

    catalog = list_active_models()
    if not catalog:
        print("❌ model_catalog has no active/preview rows. Aborting.")
        return 2

    text_models = [m for m in catalog if m.modality.value == "text"]
    image_models = [m for m in catalog if m.modality.value == "image"]

    provider = get_llm_provider("gemini")

    text_pass = 0
    text_warn = 0
    text_fail: list[tuple[str, str, list[str]]] = []  # (model_id, err, candidates)

    for entry in text_models:
        print(f"\nModel: {entry.model_id}")
        try:
            resp = provider.generate(
                prompt=PROMPT,
                model=entry.model_id,
                max_tokens=20,
                temperature=0,
            )
        except LLMError as e:
            print(f"  ❌ FAILED: {e}")
            cands = _candidate_names(entry.model_id)
            if cands:
                print("     候选 (我去 AI Studio 验证):")
                for c in cands:
                    print(f"       - {c}")
            text_fail.append((entry.model_id, str(e), cands))
            continue

        body = resp.text.strip()
        first_line = body.splitlines()[0] if body else ""
        is_pong = "pong" in body.lower()
        flag = "✅" if is_pong else "⚠️"
        print(
            f"  ✅ Status: ok, tokens {resp.tokens_in}/{resp.tokens_out}, "
            f"cost ${resp.cost_usd:.6f}, duration {resp.duration_ms}ms"
        )
        print(f"  {flag} Response: {first_line[:80]!r}"
              + (" (contains 'PONG')" if is_pong else " (does NOT contain 'PONG')"))
        if is_pong:
            text_pass += 1
        else:
            text_warn += 1

    print()
    print("=" * 78)
    print("=== Image Models (实际调用跳过 — image smoke 在另一个 Step 做) ===")
    print("=" * 78)
    for entry in image_models:
        print(f"\nModel: {entry.model_id}")
        print(f"  ⏭️  Skipped (image smoke deferred)")

    print()
    print("=" * 78)
    print("=== Summary ===")
    print("=" * 78)
    print(f"Text models tested:  {len(text_models)}")
    print(f"  ✅ pass (PONG):    {text_pass}")
    print(f"  ⚠️  warn (no PONG): {text_warn}")
    print(f"  ❌ fail:           {len(text_fail)}")
    print(f"Image models:        {len(image_models)} (skipped)")

    if text_fail:
        print()
        print("⛔ Failed models — operator must investigate. Suggested actions:")
        for model_id, err, cands in text_fail:
            print(f"   • {model_id}")
            print(f"     error: {err[:120]}")
            if cands:
                print("     候选 ID 在 AI Studio 验证后 update model_catalog 或 mark_deprecated:")
                for c in cands:
                    print(f"       - {c}")
        return 1

    print("\n✅ All text models reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
