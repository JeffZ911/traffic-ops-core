"""ImageAgent — generate hero + inline images for an article via Gemini 2.5 Flash Image.

Per CODE-SPEC §3.2.7: image_provider model is read from sites.config.image_provider
so the operator can swap models from the dashboard without code change.

Phase 1.A scope:
  - Generate 1 hero (16:9) + N inline images per call
  - Save to <site_repo>/public/img/<slug>/hero.webp etc.
  - Update articles.<frontmatter or DB> separately (caller's job)
  - Insert one row per image into the `images` table
"""

from __future__ import annotations

import base64
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from src.agents.base import BaseAgent
from src.db.client import get_db_connection
from src.db.model_catalog_client import get_model as get_catalog_entry


# Per Part A.2 of Phase 1.B: produce 1 hero + up to 6 inline images per
# article (cap 7 total, ~$0.27 per article). Each inline image is keyed
# to a specific H2 section topic so the article body can interleave them
# at PublishAgent injection time.
MAX_IMAGES_PER_ARTICLE = 10       # hard cost ceiling (single article)
DEFAULT_INLINE_COUNT = 6          # fallback when caller passes no section list


# Copyright guard: the legal line is the IP's *specific named characters* and
# logos — anime *style* itself is not protected. So we allow rich, original
# anime scenes (generic adventurers, environments, FX) and only forbid the
# game's actual characters / trademarks / text.
_COPYRIGHT_GUARD = (
    "Hard rules: render ZERO text of any kind — no words, letters, labels, "
    "captions, numbers, signage, packaging copy, or simulated UI text; leave "
    "every surface, screen, label and package blank and unlettered. "
    "NO logos, NO watermarks, NO real photographic people. Do NOT depict the "
    "game's actual named or copyrighted characters, and do not make any figure "
    "recognizable as an existing IP character — use ONLY original, generic "
    "characters. No trademarked logos or brand marks."
)

# Per-game art direction so each game looks like itself (key-art vibe) instead
# of one flat palette. Operator can override per game via
# sites.config.game_metadata[slug].art_style.
GAME_ART_STYLE: dict[str, str] = {
    "genshin": "vibrant high-fantasy anime style, lush painterly landscapes, glowing elemental magic effects, bright saturated color",
    "hsr": "sci-fi space-fantasy anime style, sleek cosmic / interstellar environments, neon astral energy effects",
    "zzz": "stylish urban cyberpunk anime style, neon-lit city, bold graphic energy and motion",
    "wuthering_waves": "post-apocalyptic anime style, windswept ruins reclaimed by nature, resonant energy effects",
    "nte": "modern supernatural anime style, near-future urban-fantasy settings, mysterious atmosphere",
}
_DEFAULT_GAME_ART = (
    "polished high-fantasy anime style, cinematic environments, dramatic "
    "elemental effects, rich saturated color"
)
# Quality bar applied to EVERY image regardless of niche/style. The look may
# vary with the content (that's wanted) but it must always be refined.
_QUALITY = (
    "Professional, highly detailed, polished and visually striking, "
    "magazine / key-art quality, tasteful composition, 16:9 horizontal."
)
# Gaming render: anime key-art, but the scene/style adapts to the content.
GAMING_RENDER = (
    "Polished anime-style key-art digital illustration, cinematic dramatic "
    "lighting, atmospheric depth, dynamic composition. " + _QUALITY
)
# Ecommerce: let the FORMAT follow the content but favour PHOTOGRAPHIC formats
# that don't invite lettering. We deliberately avoid dashboard/UI mockups and
# infographics — those make the model render garbled fake text.
ECOM_STYLE = (
    "Style: pick the photographic format that best fits this specific content "
    "— a clean studio product shot, a tasteful flat-lay, a real-life lifestyle "
    "scene, or a before/after split of physical products — rendered in a "
    "modern, high-end e-commerce editorial look with bright clean lighting. "
    "Show physical objects and scenes, NOT screens or interfaces. "
    + _QUALITY + " " + _COPYRIGHT_GUARD
)


def _is_ecom(niche: str | None) -> bool:
    return (niche or "gaming") == "ecommerce_tools"


def resolve_art_style(game: str | None, site_config: dict | None = None) -> str:
    """Per-game art direction: operator override → builtin map → default."""
    if site_config:
        meta = (site_config.get("game_metadata") or {}).get(game or "", {}) or {}
        if meta.get("art_style"):
            return str(meta["art_style"])
    return GAME_ART_STYLE.get((game or "").lower(), _DEFAULT_GAME_ART)


def hero_prompt(niche: str | None, title: str, *, art_style: str | None = None) -> str:
    """Niche-aware cover image. Gaming = original anime key-art that actually
    depicts the topic's setting/action (not abstract emptiness)."""
    if _is_ecom(niche):
        subject = (
            f"Editorial cover image representing the concept of: \"{title}\". "
            "Depict concrete physical objects — product photos, a tidy desk or "
            "studio setup, props, or packaging (all unlabeled) — never people, "
            "never screens or text."
        )
        return f"{subject} {ECOM_STYLE}"
    style = art_style or _DEFAULT_GAME_ART
    subject = (
        f"Dynamic key-art cover illustration for a game guide titled: \"{title}\". "
        f"Depict an original, evocative scene in {style} — show the relevant "
        "setting, elemental powers, weapons, or original adventurers (generic, "
        "shown from behind, in silhouette, or stylized). Make it vivid, "
        "cinematic and clearly on-topic — not abstract, not empty."
    )
    return f"{subject} {GAMING_RENDER} {_COPYRIGHT_GUARD}"


def inline_prompt(
    niche: str | None,
    section_topic: str,
    article_theme: str,
    *,
    art_style: str | None = None,
) -> str:
    """Section image bound to the specific H2 topic, composed differently from
    the cover, favouring vivid concrete scenes over vague atmosphere."""
    if _is_ecom(niche):
        subject = (
            f"Section illustration for the '{section_topic}' part of an article "
            f"about {article_theme}. Show one concrete physical visual: a "
            "product shot, a before/after of physical products, a styled "
            "lifestyle scene, or props on a clean surface — never people, "
            "never screens or text."
        )
        return f"{subject} Compose clearly differently from a cover shot. {ECOM_STYLE}"
    style = art_style or _DEFAULT_GAME_ART
    subject = (
        f"Illustration for the '{section_topic}' section of a guide about "
        f"{article_theme}. An original {style} scene that concretely depicts "
        "this section's idea — a setting, an action, an elemental effect, a "
        "weapon, or original generic adventurers. Vivid and specific, not "
        "abstract; clearly different composition from the cover."
    )
    return f"{subject} {GAMING_RENDER} {_COPYRIGHT_GUARD}"


def _save_image(raw_bytes: bytes, dest_path: Path) -> int:
    """Persist bytes as WebP (~5-10% the size of Gemini's source PNG).
    Falls back to writing raw bytes if Pillow is missing."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import io
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(dest_path, format="WEBP", quality=80, method=6)
    except Exception:
        dest_path.write_bytes(raw_bytes)
    return dest_path.stat().st_size


class ImageAgent(BaseAgent):
    name = "image"
    task_type = "image_gen"      # not in site_config.text_provider
    max_retries = 2

    def __init__(self, llm, site_config, *, site_repo_path: Path):
        super().__init__(llm=llm, site_config=site_config)
        self.site_repo_path = Path(site_repo_path)
        self._img_provider_cfg = site_config.get("image_provider", {})

    def get_model(self) -> str:
        # Read from sites.config.image_provider.model (CODE-SPEC §3.2.7)
        model = self._img_provider_cfg.get("model")
        if not model:
            raise RuntimeError("site_config.image_provider.model not set")
        return model

    # ----------------------------------------------------- direct SDK call
    # We bypass self._call_llm because LLMResponse is text-shaped; image
    # responses have a different payload. We still record metrics manually.

    def _generate_image(self, prompt: str) -> tuple[bytes, dict]:
        """Call Gemini image API. Returns (bytes, raw_meta)."""
        from google import genai
        from google.genai import types

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=api_key)

        model = self.get_model()
        cfg = types.GenerateContentConfig(
            response_modalities=["Image"],
        )

        start = time.perf_counter()
        response = client.models.generate_content(
            model=model, contents=prompt, config=cfg,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)

        # Extract first inline image part
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
                    return data, {"duration_ms": duration_ms, "model": model}

        raise RuntimeError("Image API returned no image part")

    def _record_image(
        self,
        site_id: UUID,
        article_id: Optional[UUID],
        prompt: str,
        url: str,
        alt_text: str,
        provider: str,
        model: str,
        aspect_ratio: str,
        cost_usd: float,
    ) -> UUID:
        img_id = uuid4()
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into images
                  (id, site_id, article_id, prompt, url, alt_text, provider,
                   model, aspect_ratio, cost_usd)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(img_id), str(site_id),
                    str(article_id) if article_id else None,
                    prompt, url, alt_text, provider, model, aspect_ratio,
                    round(cost_usd, 6),
                ),
            )
        return img_id

    # ------------------------------------------------------------ execute

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        article_id = UUID(input_data["article_id"])
        slug = input_data["slug"]
        title = input_data.get("title", slug)
        article_type = input_data.get("article_type", "")
        section_topics: list[str] = input_data.get("section_topics", [])
        # Default to a multi-image article (1 hero + up to 6 inline). Caller
        # can pass `inline_count` to override, but it's clamped to
        # MAX_IMAGES_PER_ARTICLE - 1 so a single article can never exceed
        # the cost ceiling.
        # Structure-driven: illustrate one image per H2 section by default
        # (so image count tracks the article's real structure), clamped to
        # the per-article ceiling. Caller may still override with inline_count.
        default_inline = len(section_topics) if section_topics else DEFAULT_INLINE_COUNT
        n_inline = min(
            int(input_data.get("inline_count", default_inline)),
            MAX_IMAGES_PER_ARTICLE - 1,
        )

        # Look up cost from model_catalog
        model = self.get_model()
        provider = self._img_provider_cfg.get("provider", "gemini")
        catalog = get_catalog_entry(provider, model)
        per_image_cost = float(catalog.per_image_cost) if catalog and catalog.per_image_cost else 0.039

        site_id_in = input_data.get("site_id")
        site_id = UUID(site_id_in) if isinstance(site_id_in, str) else site_id_in

        out_dir_rel = f"img/{slug}"
        out_dir = self.site_repo_path / "public" / out_dir_rel
        out_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict] = []
        total_cost = 0.0

        niche = self.site_config.get("niche") or "gaming"
        # Per-game art direction (gaming only): game comes from the orchestrator
        # or from the article's outline jsonb (outline.game).
        game = input_data.get("game") or (input_data.get("outline") or {}).get("game")
        art_style = None if _is_ecom(niche) else resolve_art_style(game, self.site_config)

        # 1) Hero
        hero_prompt_text = hero_prompt(niche, title, art_style=art_style)
        bytes_, meta = self._generate_image(hero_prompt_text)
        hero_path = out_dir / "hero.webp"
        size = _save_image(bytes_, hero_path)
        url_path = f"/{out_dir_rel}/hero.webp"
        self._record_image(
            site_id=site_id, article_id=article_id, prompt=hero_prompt_text,
            url=url_path, alt_text=f"{title} – cover illustration",
            provider=provider, model=model, aspect_ratio="16:9",
            cost_usd=per_image_cost,
        )
        total_cost += per_image_cost
        results.append({
            "kind": "hero", "url": url_path, "bytes": size,
            "duration_ms": meta["duration_ms"],
        })

        # 2) Inline images for the first N section topics. Each inline image
        # is bound to a specific H2 by index so PublishAgent can interleave
        # them with the matching section at injection time.
        for i, topic in enumerate(section_topics[:n_inline], start=1):
            prompt = inline_prompt(niche, topic, title, art_style=art_style)
            try:
                bytes_, meta = self._generate_image(prompt)
            except Exception as e:
                results.append({
                    "kind": f"inline_{i}", "url": None, "error": str(e)[:200],
                })
                continue
            inline_path = out_dir / f"inline-{i}.webp"
            size = _save_image(bytes_, inline_path)
            url_path = f"/{out_dir_rel}/inline-{i}.webp"
            self._record_image(
                site_id=site_id, article_id=article_id, prompt=prompt,
                url=url_path, alt_text=topic, provider=provider, model=model,
                aspect_ratio="16:9", cost_usd=per_image_cost,
            )
            total_cost += per_image_cost
            results.append({
                "kind": f"inline_{i}", "url": url_path, "bytes": size,
                "duration_ms": meta["duration_ms"],
                "section_topic": topic,   # PublishAgent uses this to pair
                                          # the image with the matching H2.
            })

        return {
            "article_id": str(article_id),
            "slug": slug,
            "images": results,
            "total_cost_usd": round(total_cost, 6),
            "model": model,
        }
