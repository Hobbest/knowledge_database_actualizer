from __future__ import annotations

import base64
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def describe_image(
    image_bytes: bytes,
    *,
    mime_type: str = "image/png",
    context: str = "",
) -> str | None:
    """Best-effort multimodal image description without changing the text LLM API."""
    if not settings.vision_media_enabled or not image_bytes:
        return None
    provider = (settings.llm_provider or "").lower()
    try:
        if provider == "gemini":
            return _describe_gemini(image_bytes, mime_type, context)
        if provider == "openai":
            return _describe_openai(image_bytes, mime_type, context)
    except Exception as exc:  # noqa: BLE001 - captions must always retain their fallback
        logger.warning("Vision description failed; retaining fallback caption: %s", exc)
    return None


def _prompt(context: str) -> str:
    suffix = f"\nNearby source context: {context[:1000]}" if context.strip() else ""
    return (
        "Describe this figure for a factual knowledge note in 2-4 sentences. "
        "State visible relationships, labels, trends, or structure. Do not guess "
        "details that are not visible." + suffix
    )


def _describe_gemini(image_bytes: bytes, mime_type: str, context: str) -> str | None:
    if not settings.llm_api_key or not settings.vision_model:
        return None
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.llm_api_key)
    contents: Any = [
        types.Part.from_text(text=_prompt(context)),
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
    ]
    response = client.models.generate_content(
        model=settings.vision_model,
        contents=contents,
    )
    return (response.text or "").strip() or None


def _describe_openai(image_bytes: bytes, mime_type: str, context: str) -> str | None:
    if not settings.llm_api_key or not settings.vision_model:
        return None
    from openai import OpenAI

    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = OpenAI(api_key=settings.llm_api_key).chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt(context)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            }
        ],
        max_tokens=300,
    )
    return (response.choices[0].message.content or "").strip() or None
