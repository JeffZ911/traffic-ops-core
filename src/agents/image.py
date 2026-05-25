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


# Per-niche STYLE suffixes give every image on a site a cohesive look
# (brand consistency) and hard copyright guardrails. Kept separate from the
# subject so the subject can be content-aware while the look stays uniform.
_COPYRIGHT_GUARD = (
    "Absolutely NO text, NO captions, NO logos, NO watermarks, NO UI chrome, "
    "NO real or recognizable human faces, and NO copyrighted or trademarked "
    "character, brand, or IP likenesses."
)
GAMING_STYLE = (
    "Style: modern editorial vector/3D-render illustration, cohesive cool-tone "
    "palette (deep indigo and teal with cyan accents), soft studio lighting, "
    "clean composition with negative space, 16:9 horizontal. " + _COPYRIGHT_GUARD
)
ECOM_STYLE = (
    "Style: clean commercial product photography / minimal flat-lay / tidy "
    "workspace, bright neutral studio lighting, white or soft-gradient "
    "background, modern e-commerce aesthetic, 16:9 horizontal. " + _COPYRIGHT_GUARD
)


def _is_ecom(niche: str | None) -> bool:
    return (niche or "gaming") == "ecommerce_tools"


def hero_prompt(niche: str | None, title: str) -> str:
    """Niche-aware, content-aware cover image. Avoids the old 'abstract
    particle' emptiness by asking for concrete topic-evoking subjects."""
    if _is_ecom(niche):
        subject = (
            f"Editorial cover image representing the concept of: \"{title}\". "
            "Depict concrete relevant objects — product photos, a tidy desk "
            "setup, packaging, or a stylized dashboard/chart — never people."
        )
        return f"{subject} {ECOM_STYLE}"
    subject = (
        f"Editorial cover image for a video-game strategy guide titled: "
        f"\"{title}\". Depict an abstract themed scene, environment, or "
        "stylized objects/diagram that evoke the topic — never a specific "
        "character or person."
    )
    return f"{subject} {GAMING_STYLE}"


def inline_prompt(niche: str | None, section_topic: str, article_theme: str) -> str:
    """Section image bound to the specific H2 topic (content-aware), composed
    to look different from the cover and to favour concrete, non-empty visuals
    (diagrams, charts, scenes, product shots) over vague atmosphere."""
    if _is_ecom(niche):
        subject = (
            f"Section illustration for the '{section_topic}' part of an article "
            f"about {article_theme}. Show one concrete relevant visual: a "
            "product shot, a before/after, a stylized data chart, or a "
            "workflow-step scene — never people."
        )
        return f"{subject} Compose clearly differently from a cover shot. {ECOM_STYLE}"
    subject = (
        f"Section illustration for the '{section_topic}' part of a game guide "
        f"about {article_theme}. Show one specific concept, scene, stylized "
        "diagram, or interface motif from this section — never a copyrighted "
        "character or person."
    )
    return f"{subject} Compose clearly differently from a cover shot. {GAMING_STYLE}"


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

        # 1) Hero
        hero_prompt_text = hero_prompt(niche, title)
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
            prompt = inline_prompt(niche, topic, title)
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
