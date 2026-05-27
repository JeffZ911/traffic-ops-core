"""Retry generator for the two brand images that failed.

Same prompts as gen_quvii_brand_images.py; only difference is per-image
retry with exponential backoff so a single server-disconnect doesn't
kill the run.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SECURITY_CAMERA_STYLE = (
    "STRICT PHOTOREALISTIC product photography in the style of Apple, DJI, "
    "Reolink, and Eufy marketing pages. Real DSLR / mirrorless camera output, "
    "natural studio or in-context lighting, accurate materials (matte plastic, "
    "brushed aluminum, glass camera lenses with realistic IR ring), shallow "
    "depth-of-field where appropriate, clean neutral backgrounds or genuine "
    "in-home/outdoor installation contexts. NO illustration, NO anime, NO "
    "cartoon, NO 3D-render aesthetic, NO stylized vector art, NO glowing "
    "magical effects. Looks like an editorial photo from The Verge, "
    "Tom's Guide, or RTINGS. No logos, no on-screen text. "
    "Sharp focus, intentional composition, editorial photography. "
    "No watermarks. No screen content. No identifiable people. "
    "Professional commercial photography."
)

PROMPTS = {
    "hero": (
        "Wide cinematic editorial photograph: a contemporary American "
        "single-family home at blue-hour dusk, viewed from the front "
        "walkway. A small matte-black home security camera is mounted "
        "discreetly under the porch eave, lens catching warm interior "
        "light spilling through the front door. Architectural lines, warm "
        "interior glow vs cool exterior blue, soft long shadows, shallow "
        "depth of field. Composition leaves the left third negative space "
        "for large display typography overlay. 16:9 aspect ratio. "
    ),
    "reviews-banner": (
        "Editorial close-up product photograph: a generic unbranded matte-"
        "black home security camera mounted on a clean white exterior wall "
        "corner. Shot from a 3/4 angle, late-afternoon golden sidelight, "
        "shallow depth-of-field with the wall texture softly out of focus. "
        "Lens glass catches a subtle reflection. Show small mounting screws "
        "and a discreet status LED. Composition leaves room on one side for "
        "headline typography. 5:4 aspect ratio. "
    ),
}


def gen_once(prompt: str, model: str) -> bytes:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.getenv("QUVII_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY"))
    cfg = types.GenerateContentConfig(response_modalities=["Image"])
    response = client.models.generate_content(model=model, contents=prompt, config=cfg)
    for c in getattr(response, "candidates", None) or []:
        content = getattr(c, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, str):
                    data = base64.b64decode(data)
                return data
    raise RuntimeError("no image data")


def gen_with_retry(prompt: str, model: str, attempts: int = 4) -> bytes:
    delay = 5
    for i in range(1, attempts + 1):
        try:
            t0 = time.perf_counter()
            data = gen_once(prompt, model)
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f"   ✓ attempt {i} succeeded in {elapsed}ms")
            return data
        except Exception as e:
            print(f"   ✗ attempt {i}: {e}")
            if i == attempts:
                raise
            print(f"   ⏳ sleeping {delay}s …")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError("unreachable")


def main() -> int:
    model = "gemini-3.1-flash-image-preview"
    out_dir = Path(__file__).resolve().parent.parent / "out-brand-images"
    out_dir.mkdir(exist_ok=True)

    for name, subject in PROMPTS.items():
        dest = out_dir / f"{name}.png"
        if dest.exists() and dest.stat().st_size > 50_000:
            print(f"\n[{name}] already exists ({dest.stat().st_size // 1024} KB) — skipping")
            continue
        print(f"\n[{name}]")
        prompt = subject + " " + SECURITY_CAMERA_STYLE
        try:
            png = gen_with_retry(prompt, model)
        except Exception as e:
            print(f"   ✗ final failure: {e}", file=sys.stderr)
            continue
        dest.write_bytes(png)
        print(f"   ✓ wrote {dest} ({len(png) // 1024} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
