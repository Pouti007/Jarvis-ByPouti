# Javis2-ByAdders

An Iron Man–style JARVIS desktop assistant for Windows 11, powered by Claude.

- **Voice**: wake word "Jarvis", local speech-to-text with faster-whisper, Windows SAPI text-to-speech
- **Brain**: Claude API with tool use (falls back to a local Ollama model if offline)
- **Computer control**: open apps and URLs, web search, volume, typing, shortcuts, PowerShell
- **Vision**: describe your screen (all monitors or one) or your webcam
- **Second brain**: Obsidian vault integration, with every conversation logged to a daily note
- **Market data**: live stock and index quotes via Finnhub
- **HUD**: dark arc-reactor dashboard (PyQt6) with transcript, notes, system stats and a text command box; minimizes to the tray
- **Optional hand gestures** (MediaPipe): open palm wakes Jarvis, fist stops it

## Setup

Requires Python 3.12 on Windows.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env    # then put your API keys in .env
```

Adjust everything else (user name, voice, models, gestures) in `config.yaml`.

## Run

Double-click `run_jarvis.bat`, or:

```powershell
.\.venv\Scripts\python.exe main.py            # with dashboard
.\.venv\Scripts\python.exe main.py --minimized  # straight to the tray
```

The Whisper and gesture models download automatically on first use.
