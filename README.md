# Jarvis-ByPouti

An Iron Man–style JARVIS desktop assistant for Windows, powered by Claude — or, for free, by open models hosted by NVIDIA.

- **Voice**: say "Jarvis", then your command — local speech-to-text (faster-whisper), spoken replies (Windows TTS)
- **Text**: or just type commands into the box at the bottom of the dashboard
- **Computer control**: open apps and websites, web search, volume, typing, keyboard shortcuts, PowerShell
- **Vision**: ask about your screen (all monitors or one) or what your webcam sees
- **Second brain**: Obsidian vault integration; every conversation is logged to a daily note
- **Market data**: live stock and index quotes via Finnhub
- **HUD**: dark arc-reactor dashboard with transcript, notes and system stats; minimizes to the tray
- **Hand gestures** (optional): open palm wakes Jarvis, fist stops it

## Install on a new PC

**You need:** Windows 10 or 11, a microphone, and at least one AI key:

- **NVIDIA — free.** Sign in at [build.nvidia.com](https://build.nvidia.com) with a free NVIDIA account, open any model's page and click **Get API Key** (it starts with `nvapi-`). The free tier is rate-limited, which is plenty for personal use.
- **Anthropic (Claude) — paid, best quality.** Create a key at [console.anthropic.com](https://console.anthropic.com).

If you add both, Claude is used first and NVIDIA takes over automatically if Claude fails (e.g. out of credits).

Optional: a webcam (vision and gestures) and a free [Finnhub key](https://finnhub.io/register) (stock quotes).

### 1. Install Python 3.12

Open PowerShell and run:

```powershell
winget install Python.Python.3.12
```

(Or download Python 3.12 from [python.org](https://www.python.org/downloads/). It must be 3.12 — newer versions aren't supported by the speech libraries yet.)

### 2. Download JARVIS

**Option A — no Git needed:** on this GitHub page click the green **Code** button → **Download ZIP**, then extract the ZIP somewhere permanent, e.g. `C:\Users\<you>\Jarvis`.

**Option B — with Git:**

```powershell
git clone https://github.com/Pouti007/Jarvis-ByPouti.git
cd Jarvis-ByPouti
```

### 3. Run the setup

Open the folder and double-click **`setup.bat`**. It will:

1. create a private Python environment in the folder (`.venv`)
2. install all the packages (takes a few minutes)
3. open a `.env` file in Notepad — paste each key you have right after its `=` sign (no spaces or quotes; leave the `#` lines alone), then **save and close** Notepad

### 4. Start JARVIS

Double-click **`run_jarvis.bat`**. The first launch downloads the speech models (~500 MB), so give it a minute before it greets you.

### 5. (Optional) Start automatically at login

Double-click **`add_to_startup.bat`**. JARVIS will then launch minimized in the system tray every time you log in.
To undo it, press <kbd>Win</kbd>+<kbd>R</kbd>, type `shell:startup`, and delete `JARVIS.lnk`.

## Using it

- Say **"Jarvis"**, wait for the beep, then speak your command — or type it in the box and press Enter.
- Try: *"what can you do?"*, *"open Spotify"*, *"what's on my screen?"*, *"how's Microsoft stock?"*, *"what do you see?"*
- Closing the window hides it to the tray. Right-click the tray icon to show it again or quit.

## Configuration

Everything except your API keys lives in **`config.yaml`**:

| Setting | What it does |
| --- | --- |
| `user.name` | What JARVIS calls you |
| `brain.provider` | `auto`, `nvidia` (prefer the free models, saves Claude credits) or `anthropic` |
| `nvidia.model` / `nvidia.vision_model` | Which free NVIDIA models to use (the main one must support tool calling) |
| `voice.tts_voice` / `voice.tts_rate` | Voice (`David`, `Zira`, `Hazel`…) and speaking speed (-10 to 10) |
| `voice.whisper_model` | Speech accuracy vs speed: `base` is lighter, `small` is more accurate |
| `vision.hand_tracking` | `true` / `false` — turn webcam gestures on or off |
| `obsidian.vault_path` | Where notes and conversation logs are stored (default: `vault` in this folder) |

API keys go in **`.env`** only — it's excluded from Git so they're never uploaded.

Restart JARVIS (tray icon → Quit, then `run_jarvis.bat`) after changing either file.

## Updating

If you used Git, pull the latest version and re-run setup in case packages changed:

```powershell
git pull
.\setup.bat
```

If you downloaded the ZIP, download it again and copy your `.env` into the new folder.
