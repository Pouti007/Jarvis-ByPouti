"""Minimal OpenAI-compatible chat client, used for NVIDIA's free hosted models.

NVIDIA (https://build.nvidia.com) serves many open models — Nemotron, Gemma,
Kimi, gpt-oss and more — behind an OpenAI-style /chat/completions endpoint,
free for development use (rate-limited). Get a key (it starts with "nvapi-")
and put it in .env as NVIDIA_API_KEY.

Uses plain `requests` so no extra dependency is needed. The same client also
works with any other OpenAI-compatible server (e.g. Ollama's /v1 endpoint).
"""

from __future__ import annotations

import re

import requests

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    """A request to an OpenAI-compatible endpoint failed. `status` is the HTTP code, if any."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-style tool definitions into OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for tool in tools
    ]


def clean_reply(text: str | None) -> str:
    """Strip any <think>…</think> reasoning that some open models put in their answer."""
    return _THINK_BLOCK.sub("", text or "").strip()


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> dict:
        """Send one chat request and return the assistant message dict
        (with `content` and possibly `tool_calls`). Raises LLMError on failure."""
        body: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"connection failed: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}", status=resp.status_code)
        try:
            return resp.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response: {resp.text[:300]}") from exc
