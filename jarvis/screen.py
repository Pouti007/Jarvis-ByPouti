"""Vision helpers: capture/encode images and describe them with Claude."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import anthropic

from jarvis.actions import take_screenshot


def describe_image_b64(
    client: anthropic.Anthropic,
    model: str,
    image_b64: str,
    media_type: str,
    question: str,
) -> str:
    """Send a base64-encoded image to Claude and return its description/answer."""
    prompt = question.strip() or "Describe what's in this image, concisely."
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def screenshot_b64(monitor: str | int = "all") -> str:
    """Take a screenshot (all monitors by default) and return it as base64 PNG."""
    path = take_screenshot(monitor=monitor)
    return base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")


def shrink_image_b64(image_b64: str, max_side: int = 1280, quality: int = 80) -> str:
    """Downscale and re-encode an image as base64 JPEG.

    Hosted open-model endpoints (e.g. NVIDIA) reject very large inline images,
    and a dual-monitor PNG can be several MB. Long-edge 1280px keeps text legible.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
    image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")
