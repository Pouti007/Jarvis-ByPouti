"""Claude-powered reasoning engine: decides which action to take and drives the conversation.

Uses Anthropic's tool-use (function calling) so Claude returns structured,
typed action requests instead of free-form text we'd have to parse. Falls
back to a local Ollama model if the Anthropic API is unreachable.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

import anthropic
import requests

from jarvis import actions, market
from jarvis.obsidian import ObsidianVault
from jarvis.screen import describe_image_b64, describe_screen

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
        api_key = config["anthropic"].get("api_key") or None  # None -> SDK reads ANTHROPIC_API_KEY env var
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = config["anthropic"]["model"]
        self.vision_model = config["anthropic"].get("vision_model", self.model)
        self.max_history_turns = config["anthropic"].get("max_history_turns", 20)
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
        "- Brain: the Claude API; if it's unreachable you fall back to a local Ollama model.\n"
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
            return describe_screen(
                self.client, self.vision_model, tool_input.get("question", ""), tool_input.get("monitor", "all")
            )
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

    def _describe_camera(self, question: str) -> str:
        """Grab a webcam frame and ask Claude vision about it."""
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
        return describe_image_b64(self.client, self.vision_model, image_b64, "image/jpeg", question)

    # -------------------------------------------------------------- main loop

    def process_command(self, user_text: str) -> str:
        with self._lock:
            return self._process_command_locked(user_text)

    def _process_command_locked(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})

        try:
            reply = self._run_tool_loop()
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            # Genuine network problem — try the local Ollama fallback.
            print(f"[brain] Anthropic connection error: {exc}")
            reply = self._query_ollama(user_text)
        except anthropic.APIStatusError as exc:
            # The request reached Anthropic but was rejected (bad key, no access
            # to the model, out of credits, rate limit...). Surface the real
            # reason instead of silently degrading, then try Ollama as a backup.
            status = getattr(exc, "status_code", "?")
            print(f"[brain] Anthropic API error {status}: {exc}")
            hint = self._api_error_hint(exc)
            fallback = self._query_ollama(user_text)
            reply = fallback if "fallback isn't running" not in fallback else hint

        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()
        self.vault.log_conversation(user_text, reply)
        return reply

    def _api_error_hint(self, exc: "anthropic.APIStatusError") -> str:
        """Turn an Anthropic API error into a clear, spoken-friendly explanation."""
        status = getattr(exc, "status_code", None)
        if status == 401:
            return "My Anthropic API key appears to be invalid or unauthorized. Check the key in config.yaml."
        if status == 403:
            return (
                f"My API key doesn't have access to the model '{self.model}'. "
                "Check your model access in the Anthropic console, or switch the model in config.yaml."
            )
        if status == 404:
            return (
                f"The model '{self.model}' wasn't found — it may be misspelled or unavailable to this key. "
                "Check the model name in config.yaml."
            )
        if status == 429:
            return "I'm rate-limited or out of credits on the Anthropic API right now."
        return f"The Anthropic API returned an error ({status}): {exc}"

    def _run_tool_loop(self) -> str:
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
                    # A failing action must never crash JARVIS — turn any exception
                    # into an error result so Claude can explain it to the user.
                    try:
                        result = self._dispatch_tool(block.name, block.input)
                    except Exception as exc:
                        print(f"[brain] tool '{block.name}' failed: {exc}")
                        result = f"ERROR running {block.name}: {exc}"
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": str(result), "is_error": result.startswith("ERROR")}
                    )
            messages.append({"role": "user", "content": tool_results})

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
            return resp.json().get("message", {}).get("content", "").strip() or (
                "I couldn't reach Claude or get a reply from Ollama."
            )
        except requests.RequestException:
            return "I couldn't reach Claude, and my local Ollama fallback isn't running either."
