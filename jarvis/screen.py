"""Vision helpers: describe the screen or the webcam via Claude's vision."""

from __future__ import annotations

import base64
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


def describe_screen(
    client: anthropic.Anthropic, model: str, question: str = "", monitor: str | int = "all"
) -> str:
    """Take a screenshot (all monitors by default) and ask Claude about it."""
    path = take_screenshot(monitor=monitor)
    image_b64 = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    prompt = question.strip() or "Describe what's currently on screen, concisely."
    return describe_image_b64(client, model, image_b64, "image/png", prompt)
