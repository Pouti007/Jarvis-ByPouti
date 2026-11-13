"""Claude-powered reasoning engine: decides which action to take and drives the conversation.

Uses Anthropic's tool-use (function calling) so Claude returns structured,
typed action requests instead of free-form text we'd have to parse. Falls
back to a local Ollama model if the Anthropic API is unreachable.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

import anthropic
import requests

from jarvis import actions, market
from jarvis.obsidian import ObsidianVault
from jarvis.openai_compat import (
    NVIDIA_BASE_URL,
    LLMError,
    OpenAICompatClient,
    anthropic_tools_to_openai,
    clean_reply,
)
from jarvis.screen import describe_image_b64, screenshot_b64, shrink_image_b64

DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_NVIDIA_VISION_MODEL = "google/gemma-4-31b-it"

TOOLS = [
    {
        "name": "open_app",
        "description": "Launch a desktop application by its common name (e.g. 'spotify', 'notepad', 'vs code').",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a specific URL directly in Microsoft Edge.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for a query and open the top result in Edge.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "control_volume",
        "description": "Change the system volume.",
        "input_schema": {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": ["up", "down", "mute"]}},
            "required": ["direction"],
        },
    },
    {
        "name": "type_text",
        "description": "Type literal text into whatever window currently has focus.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "press_keys",
        "description": "Press a keyboard shortcut, e.g. ['ctrl', 's'] to save.",
        "input_schema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
        },
    },
    {
        "name": "run_powershell",
        "description": "Run a PowerShell command on the user's machine and return its output.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "describe_screen",
        "description": "Take a screenshot and describe or answer a question about it. Captures all monitors by default; pass monitor=1, 2, ... to target one display.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Optional specific question about the screen."},
                "monitor": {"type": "string", "description": "'all' (default) or a monitor number like '1' or '2'."},
            },
        },
    },
    {
        "name": "create_note",
        "description": "Create a new note in the Obsidian second-brain vault.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "search_vault",
        "description": "Search the Obsidian vault for notes matching a keyword or topic, e.g. for 'what do I know about X'.",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "get_stock_quote",
        "description": (
            "Get a live stock/ETF quote (price, daily change, day range) from Finnhub. "
            "Accepts a ticker symbol or company name. For US indices use: 'S&P 500', 'Dow', "
            "'Nasdaq' (mapped to ETF proxies automatically)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Ticker, company name, or index."}},
            "required": ["query"],
        },
    },
    {
        "name": "describe_camera",
        "description": "Capture a frame from the webcam and describe or answer a question about what the camera sees.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "Optional specific question about the camera view."}},
        },
    },
]


class JarvisBrain:
    def __init__(self, config: dict, vault: ObsidianVault):
        self.config = config
        self.vault = vault

        # --- Claude (paid) — only created if a key exists, so JARVIS can run on NVIDIA alone.
        anthropic_cfg = config.get("anthropic", {})
        anthropic_key = anthropic_cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
        self.model = anthropic_cfg.get("model", "claude-sonnet-5")
        self.vision_model = anthropic_cfg.get("vision_model", self.model)
        self.max_history_turns = anthropic_cfg.get("max_history_turns", 20)

        # --- NVIDIA (free, rate-limited) — OpenAI-compatible hosted open models.
        nvidia_cfg = config.get("nvidia", {})
        nvidia_key = nvidia_cfg.get("api_key") or os.environ.get("NVIDIA_API_KEY", "")
        self.nvidia = (
            OpenAICompatClient(nvidia_cfg.get("base_url", NVIDIA_BASE_URL), nvidia_key) if nvidia_key else None
        )
        self.nvidia_model = nvidia_cfg.get("model", DEFAULT_NVIDIA_MODEL)
        self.nvidia_vision_model = nvidia_cfg.get("vision_model", DEFAULT_NVIDIA_VISION_MODEL)

        # Which brain to try first; the other one (if configured) is the backup.
        preferred = str(config.get("brain", {}).get("provider", "auto")).lower()
        order = ["nvidia", "anthropic"] if preferred == "nvidia" else ["anthropic", "nvidia"]
        available = {"anthropic": self.client is not None, "nvidia": self.nvidia is not None}
        self.providers = [p for p in order if available[p]]
        self._tools_ran = False  # set when an action executes during the current command
        self.edge_path = config.get("browser", {}).get("edge_path", "msedge")
        self.ollama_url = config.get("ollama", {}).get("url", "http://localhost:11434")
        self.ollama_model = config.get("ollama", {}).get("model", "llama3.2")
        self.user_name = config.get("user", {}).get("name", "there")
        self.finnhub_key = config.get("finnhub", {}).get("api_key", "") or os.environ.get("FINNHUB_API_KEY", "")
        # Returns a webcam frame (BGR numpy array) or None; set by main.py once the
        # camera source (shared hand-tracker frame vs. on-demand capture) is known.
        self.camera_provider = None
        self.camera_index = config.get("vision", {}).get("camera_index", 0)
        self.history: list[dict] = []
        # Serializes commands so a typed command and a spoken one can't interleave.
        self._lock = threading.Lock()

    # ------------------------------------------------------------- system prompt

    CAPABILITIES = (
        "Your capabilities (each backed by a tool you can call):\n"
        "- Launch desktop apps by name (Spotify, Discord, VS Code, Notepad, Edge, Excel, etc.).\n"
        "- Open a specific URL in Edge, or web-search a query and open the top result.\n"
        "- Control system volume (up / down / mute).\n"
        "- Type text into the focused window and press keyboard shortcuts.\n"
        "- Run PowerShell commands on the machine and report their output.\n"
        "- Take a screenshot (all monitors, or a specific one) and describe or answer questions about it.\n"
        "- Look through the webcam and describe or answer questions about what the camera sees.\n"
        "- Fetch live stock/ETF market quotes (price, daily change, day range) via Finnhub, "
        "including US indices (S&P 500, Dow, Nasdaq).\n"
        "- Create notes in the Obsidian second-brain vault, and search the vault by keyword.\n"
        "- Every exchange is auto-logged to a daily note, so you can recall earlier conversations. "
        "For anything from a PREVIOUS day, use the search_vault tool on the 'Daily' folder rather than "
        "saying you don't remember.\n\n"
        "How you run (so you can answer questions about yourself):\n"
        "- Voice in: faster-whisper speech-to-text, triggered by the wake word 'Jarvis'.\n"
        "- Voice out: Windows SAPI text-to-speech (interruptible).\n"
        "- Brain: the Claude API and/or free open models hosted by NVIDIA (whichever keys are set; "
        "each backs up the other), with a local Ollama model as a last resort.\n"
        "- A dark arc-reactor HUD dashboard shows a live transcript, your second-brain notes, and "
        "system stats; it minimizes to the system tray and auto-starts at login.\n"
        "- Optional webcam hand-gesture control (when enabled in config): an open palm wakes you, "
        "a fist stops/cancels you, and a horizontal hand-swipe shows or hides the dashboard.\n\n"
        "When the user asks what you can do, or what's new/updated about you, answer from this list "
        "in your own words — do not claim you have no self-awareness."
    )

    def _system_prompt(self) -> str:
        now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
        recent_notes = self.vault.recent_notes_summary()
        recent_log = self.vault.read_recent_log()

        prompt = (
            f"You are JARVIS, {self.user_name}'s personal AI assistant running locally on their Windows PC. "
            f"Current time: {now}.\n\n"
            "Use a tool whenever the user's request requires taking an action; otherwise just reply "
            "conversationally. Keep spoken replies concise (1-3 sentences) since they are read aloud via "
            "text-to-speech.\n\n"
            f"{self.CAPABILITIES}\n\n"
            f"Recently modified vault notes (context on what {self.user_name} has been working on):\n"
            f"{recent_notes}"
        )
        if recent_log:
            prompt += (
                "\n\nFor continuity, here is the tail of today's conversation log so far "
                "(you may reference it if relevant):\n"
                f"{recent_log}"
            )
        return prompt

    # ------------------------------------------------------------------ tools

    def _dispatch_tool(self, name: str, tool_input: dict) -> str:
        if name == "open_app":
            return actions.open_app(tool_input["name"])
        if name == "open_url":
            return actions.open_url(tool_input["url"], self.edge_path)
        if name == "web_search":
            return actions.web_search(tool_input["query"], self.edge_path)
        if name == "control_volume":
            return actions.volume(tool_input["direction"])
        if name == "type_text":
            return actions.type_text(tool_input["text"])
        if name == "press_keys":
            return actions.press_keys(tool_input["keys"])
        if name == "run_powershell":
            return actions.run_powershell(tool_input["command"])
        if name == "describe_screen":
            image_b64 = screenshot_b64(tool_input.get("monitor", "all"))
            question = tool_input.get("question", "") or "Describe what's currently on screen, concisely."
            return self._vision(image_b64, "image/png", question)
        if name == "get_stock_quote":
            return market.get_quote(tool_input["query"], self.finnhub_key)
        if name == "describe_camera":
            return self._describe_camera(tool_input.get("question", ""))
        if name == "create_note":
            path = self.vault.create_note(
                tool_input["title"], tool_input["content"], tool_input.get("tags")
            )
            return f"Created note: {path.name}"
        if name == "search_vault":
            matches = self.vault.search_vault(tool_input["keyword"])
            if not matches:
                return f"No notes found matching '{tool_input['keyword']}'."
            return "\n".join(f"[{m.title}] ...{m.snippet}..." for m in matches)
        return f"Unknown tool: {name}"

    def describe_providers(self) -> str:
        """Human-readable summary of the configured AI brains, for the HUD at startup."""
        names = {"anthropic": f"Claude ({self.model})", "nvidia": f"NVIDIA free ({self.nvidia_model})"}
        if not self.providers:
            return "No AI key configured — add ANTHROPIC_API_KEY or a free NVIDIA_API_KEY to .env."
        text = f"Brain: {names[self.providers[0]]}"
        if len(self.providers) > 1:
            text += f", backup: {names[self.providers[1]]}"
        return text

    def _vision(self, image_b64: str, media_type: str, question: str) -> str:
        """Answer a question about an image using the first provider that succeeds."""
        prompt = question.strip() or "Describe what's in this image, concisely."
        errors = []
        for provider in self.providers:
            try:
                if provider == "anthropic":
                    return describe_image_b64(self.client, self.vision_model, image_b64, media_type, prompt)
                small = shrink_image_b64(image_b64)
                message = self.nvidia.chat(
                    self.nvidia_vision_model,
                    [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{small}"}},
                            ],
                        }
                    ],
                    max_tokens=1024,
                )
                answer = clean_reply(message.get("content"))
                if answer:
                    return answer
                errors.append("the vision model returned an empty answer")
            except (anthropic.APIError, LLMError) as exc:
                print(f"[brain] {provider} vision failed: {exc}")
                errors.append(self._error_hint(provider, exc))
        return "I couldn't analyse the image: " + ("; ".join(errors) or "no AI provider is configured.")

    def _describe_camera(self, question: str) -> str:
        """Grab a webcam frame and ask the vision model about it."""
        # Imported lazily so the app still runs if MediaPipe/OpenCV isn't installed.
        from jarvis import vision

        frame = None
        if self.camera_provider is not None:
            frame = self.camera_provider()
        if frame is None:
            frame = vision.capture_frame(self.camera_index)
        if frame is None:
            return "I couldn't access the webcam — it may be in use by another app or disabled."

        image_b64 = vision.frame_to_jpeg_b64(frame)
        if image_b64 is None:
            return "I captured a camera frame but couldn't encode it."
        return self._vision(image_b64, "image/jpeg", question)

    # -------------------------------------------------------------- main loop

    def process_command(self, user_text: str) -> str:
        with self._lock:
            return self._process_command_locked(user_text)

    def _process_command_locked(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})

        reply = None
        errors: list[str] = []
        self._tools_ran = False
        for provider in self.providers:
            try:
                reply = self._run_anthropic_loop() if provider == "anthropic" else self._run_nvidia_loop()
                break
            except (anthropic.APIError, LLMError) as exc:
                # Bad key, out of credits, rate limit, network... Record the real
                # reason and move on to the next configured brain.
                print(f"[brain] {provider} failed: {exc}")
                errors.append(self._error_hint(provider, exc))
                if self._tools_ran:
                    # An action already happened (e.g. an app opened). Retrying on
                    # another provider could repeat it, so stop and explain instead.
                    break

        if reply is None:
            fallback = "" if self._tools_ran else self._query_ollama(user_text)
            if fallback and "fallback isn't running" not in fallback:
                reply = fallback
            elif errors:
                reply = " ".join(errors)
            else:
                reply = "No AI key is configured. Add ANTHROPIC_API_KEY or a free NVIDIA_API_KEY to the .env file."

        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()
        self.vault.log_conversation(user_text, reply)
        return reply

    def _error_hint(self, provider: str, exc: Exception) -> str:
        """Turn a provider error into a clear, spoken-friendly explanation."""
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if provider == "anthropic":
            if status == 401:
                return "My Anthropic API key appears to be invalid. Check ANTHROPIC_API_KEY in the .env file."
            if status == 403:
                return f"The Anthropic key doesn't have access to the model '{self.model}'."
            if status == 404:
                return f"The Claude model '{self.model}' wasn't found. Check the model name in config.yaml."
            if status == 429:
                return "I'm rate-limited on the Anthropic API right now."
            if status == 400 and "credit" in str(exc).lower():
                return "The Anthropic account is out of credits."
            if status is None:
                return "I couldn't reach the Anthropic API."
            return f"The Anthropic API returned an error ({status})."
        if status in (401, 403):
            return "My NVIDIA API key appears to be invalid. Check NVIDIA_API_KEY in the .env file."
        if status == 404:
            return f"The NVIDIA model '{self.nvidia_model}' isn't available. Pick another model in config.yaml."
        if status == 429:
            return "I'm hitting the NVIDIA free-tier rate limit; try again in a minute."
        if status is None:
            return "I couldn't reach NVIDIA's API."
        return f"NVIDIA's API returned an error ({status})."

    def _safe_dispatch(self, name: str, tool_input: dict | None) -> str:
        """Run a tool, never letting an exception escape (a failing action must not crash JARVIS)."""
        if not isinstance(tool_input, dict):
            return f"ERROR running {name}: the model sent malformed arguments."
        self._tools_ran = True
        try:
            return str(self._dispatch_tool(name, tool_input))
        except Exception as exc:
            print(f"[brain] tool '{name}' failed: {exc}")
            return f"ERROR running {name}: {exc}"

    def _run_anthropic_loop(self) -> str:
        messages = list(self.history)
        for _ in range(6):  # hard cap on tool-call round-trips per command
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self._system_prompt(),
                messages=messages,
                tools=TOOLS,
            )

            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text").strip()

            assistant_content = [b.model_dump() for b in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._safe_dispatch(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                            "is_error": result.startswith("ERROR"),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        return "I ran into trouble completing that after several steps."

    def _run_nvidia_loop(self) -> str:
        """Same tool-use loop as Claude's, but over NVIDIA's OpenAI-compatible API."""
        messages = [{"role": "system", "content": self._system_prompt()}] + [dict(m) for m in self.history]
        tools = anthropic_tools_to_openai(TOOLS)
        for _ in range(6):  # hard cap on tool-call round-trips per command
            message = self.nvidia.chat(self.nvidia_model, messages, tools=tools)
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                reply = clean_reply(message.get("content"))
                if not reply:
                    raise LLMError("the model returned an empty reply")
                return reply

            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                function = call.get("function", {})
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        arguments = None
                result = self._safe_dispatch(function.get("name", ""), arguments)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

        return "I ran into trouble completing that after several steps."

    def _trim_history(self) -> None:
        max_messages = self.max_history_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    # --------------------------------------------------------------- fallback

    def _query_ollama(self, user_text: str) -> str:
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {"role": "system", "content": self._system_prompt()},
                        {"role": "user", "content": user_text},
                    ],
                    "stream": False,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return clean_reply(resp.json().get("message", {}).get("content", "")) or (
                "I couldn't get a reply from my AI providers or from Ollama."
            )
        except requests.RequestException:
            return "I couldn't reach my AI providers, and my local Ollama fallback isn't running either."
