"""One-shot brand image generator for quvii-site.

Generates dedicated photoreal hero/banner assets that the homepage can use
independent of article content (so the visual identity doesn't depend on
the cron pipeline state). Saves PNG files to ./out-brand-images/ — the
operator then commits them to quvii-site/public/brand/.

Each image is generated with the same SECURITY_CAMERA_STYLE prompt suffix
the article pipeline uses, so brand assets feel cohesive with article
hero images.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Same style anchor the article pipeline uses. Reproduced inline so this
# script has no dependency on src.agents.image (which pulls in DB code).
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


BRAND_PROMPTS = {
    # Homepage hero — broadest, most cinematic. Modern home at dusk, a
    # single camera mounted unobtrusively over a porch entry. Wide 16:9
    # composition. Dark blue-hour palette so white display type reads.
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
    # CategoryBanner — Reviews. Close-up of a single mounted camera in a
    # 5:4 frame, focus on craftsmanship of the device itself.
    "reviews-banner": (
        "Editorial close-up product photograph: a generic unbranded matte-"
        "black home security camera mounted on a clean white exterior wall "
        "corner. Shot from a 3/4 angle, late-afternoon golden sidelight, "
        "shallow depth-of-field with the wall texture softly out of focus. "
        "Lens glass catches a subtle reflection. Show small mounting screws "
        "and a discreet status LED. Composition leaves room on one side for "
        "headline typography. 5:4 aspect ratio. "
    ),
    # CategoryBanner — Guides. An installer's hands at work, more
    # how-to / craftsmanship vibe.
    "guides-banner": (
        "Editorial photograph from over the shoulder: a pair of adult hands "
        "in dark casual clothing using a small cordless drill to mount a "
        "camera bracket on a beige stucco exterior wall, near a porch eave. "
        "A small unbranded security camera sits in foreground out of focus, "
        "next to a power drill and wall anchors on a clean towel. Soft "
        "natural daylight, professional but human, no faces visible. "
        "Shallow depth of field. 5:4 aspect ratio. "
    ),
}


def generate(prompt: str, model: str) -> bytes:
    from google import genai
    from google.genai import types

    api_key = os.getenv("QUVII_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("QUVII_GEMINI_API_KEY (or GEMINI_API_KEY) not set")

    client = genai.Client(api_key=api_key)
    cfg = types.GenerateContentConfig(response_modalities=["Image"])

    print(f"   → calling {model} …", flush=True)
    t0 = time.perf_counter()
    response = client.models.generate_content(model=model, contents=prompt, config=cfg)
    elapsed = int((time.perf_counter() - t0) * 1000)
    print(f"   ✓ {elapsed}ms")

    cands = getattr(response, "candidates", None) or []
    for c in cands:
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
    raise RuntimeError("API returned no image data")


def main() -> int:
    model = "gemini-3.1-flash-image-preview"
    out_dir = Path(__file__).resolve().parent.parent / "out-brand-images"
    out_dir.mkdir(exist_ok=True)

    for name, subject in BRAND_PROMPTS.items():
        prompt = subject + " " + SECURITY_CAMERA_STYLE
        print(f"\n[{name}]")
        try:
            png = generate(prompt, model)
        except Exception as e:
            print(f"   ✗ failed: {e}", file=sys.stderr)
            continue
        dest = out_dir / f"{name}.png"
        dest.write_bytes(png)
        size_kb = len(png) // 1024
        print(f"   ✓ wrote {dest} ({size_kb} KB)")

    print(f"\nDone. Files in: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
