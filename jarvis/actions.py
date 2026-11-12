"""Computer control actions: launching apps, browsing, input simulation, and shell access.

Every function here is a single, self-contained action that the brain's tool-use
dispatcher can call by name. Each returns a short human-readable status string
so it can be relayed back to the user and logged to the vault.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
import winreg
from pathlib import Path
from urllib.parse import quote_plus

import pyautogui
import requests
from bs4 import BeautifulSoup

# Cached absolute path to msedge.exe once resolved.
_EDGE_EXE: str | None = None


def _resolve_edge_exe(configured: str = "msedge") -> str | None:
    """Find a launchable Edge executable.

    `subprocess.Popen(["msedge", ...])` fails on most Windows installs because
    Edge lives under the registry's "App Paths" key, not on PATH. Resolve the
    real .exe from there (and common install dirs), caching the result.
    """
    global _EDGE_EXE
    if _EDGE_EXE:
        return _EDGE_EXE

    # 1) An explicit full path in config wins.
    if configured and configured.lower().endswith(".exe") and os.path.isfile(configured):
        _EDGE_EXE = configured
        return _EDGE_EXE

    # 2) The App Paths registry entry (what Explorer/"start" uses).
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
            ) as key:
                path = winreg.QueryValue(key, None)
                if path and os.path.isfile(path):
                    _EDGE_EXE = path
                    return _EDGE_EXE
        except OSError:
            pass

    # 3) Common install locations.
    for candidate in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.isfile(candidate):
            _EDGE_EXE = candidate
            return _EDGE_EXE

    return None


def _open_in_edge(url: str, edge_path: str = "msedge") -> None:
    """Open a URL in Edge, falling back to the microsoft-edge: protocol, then the
    default browser — never raising (callers report status themselves)."""
    exe = _resolve_edge_exe(edge_path)
    if exe:
        subprocess.Popen([exe, url])
        return
    try:
        os.startfile(f"microsoft-edge:{url}")  # protocol handler → Edge
    except OSError:
        os.startfile(url)  # last resort: default browser

# Friendly names -> the token os.startfile / App Paths should resolve.
# Extend this as you find apps that don't resolve under their plain name.
APP_ALIASES = {
    "notepad": "notepad",
    "explorer": "explorer",
    "file explorer": "explorer",
    "calculator": "calc",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "chrome": "chrome",
    "spotify": "spotify",
    "discord": "discord",
    "vs code": "code",
    "visual studio code": "code",
    "vscode": "code",
    "excel": "excel",
    "word": "winword",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "terminal": "wt",
    "settings": "ms-settings:",
}

# Virtual key codes used for the system media keys (no extra dependency needed).
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def open_app(name: str) -> str:
    """Launch an application by its common name."""
    key = name.strip().lower()
    target = APP_ALIASES.get(key, key)
    try:
        os.startfile(target)  # noqa: S606 - intentional, this is the whole feature
        return f"Opened {name}."
    except OSError as exc:
        return f"Couldn't open '{name}': {exc}"


def open_url(url: str, edge_path: str = "msedge") -> str:
    """Open a URL directly in Microsoft Edge."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _open_in_edge(url, edge_path)
    return f"Opened {url} in Edge."


def web_search(query: str, edge_path: str = "msedge") -> str:
    """Search the web and open the top result directly in Edge.

    Falls back to opening the search results page itself if the top result
    can't be parsed (search engine HTML structure changes without notice).
    """
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}"
    try:
        resp = requests.get(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=5,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        first_link = soup.select_one("li.b_algo h2 a")
        if first_link and first_link.get("href", "").startswith("http"):
            top_url = first_link["href"]
            _open_in_edge(top_url, edge_path)
            return f"Opened top result for '{query}': {top_url}"
    except Exception:
        pass

    _open_in_edge(search_url, edge_path)
    return f"Opened search results for '{query}'."


def volume(direction: str, steps: int = 2) -> str:
    """direction: 'up', 'down', or 'mute'."""
    code = {"up": VK_VOLUME_UP, "down": VK_VOLUME_DOWN, "mute": VK_VOLUME_MUTE}.get(direction)
    if code is None:
        return f"Unknown volume direction '{direction}'."
    presses = 1 if direction == "mute" else steps
    for _ in range(presses):
        ctypes.windll.user32.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
    return f"Volume {direction}."


def type_text(text: str) -> str:
    """Type text into whatever window currently has focus."""
    pyautogui.typewrite(text, interval=0.01)
    return "Typed text."


def press_keys(keys: list[str]) -> str:
    """Press a keyboard shortcut, e.g. ['ctrl', 's']."""
    pyautogui.hotkey(*keys)
    return f"Pressed {'+'.join(keys)}."


def _physical_monitors() -> tuple[tuple[int, int], list[tuple[int, int, int, int]]]:
    """Return the virtual-screen origin and each monitor's rect, in *physical* pixels.

    Windows reports monitor geometry in DPI-scaled logical coordinates unless the
    thread is per-monitor DPI aware; ImageGrab captures physical pixels. We flip
    this thread to PER_MONITOR_AWARE_V2 briefly so the two line up, then restore.
    """
    import win32api

    user32 = ctypes.windll.user32
    prev = None
    try:
        prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass
    try:
        vx = win32api.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = win32api.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        rects = [tuple(rect) for (_h, _hdc, rect) in win32api.EnumDisplayMonitors()]
        return (vx, vy), rects
    finally:
        if prev is not None:
            user32.SetThreadDpiAwarenessContext(prev)


def take_screenshot(save_dir: str | Path = "screenshots", monitor: str | int = "all") -> Path:
    """Capture the screen. monitor='all' spans every display; 1, 2, ... targets one."""
    from PIL import ImageGrab

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"screenshot_{int(time.time())}.png"

    # all_screens=True captures the full virtual desktop (all monitors) in physical px.
    image = ImageGrab.grab(all_screens=True)

    if monitor not in ("all", None, ""):
        try:
            (vx, vy), rects = _physical_monitors()
            idx = int(monitor) - 1
            if 0 <= idx < len(rects):
                left, top, right, bottom = rects[idx]
                image = image.crop((left - vx, top - vy, right - vx, bottom - vy))
        except Exception as exc:
            print(f"[actions] monitor {monitor} crop failed, using all screens: {exc}")

    image.save(path)
    return path


def run_powershell(command: str, timeout: int = 20) -> str:
    """Run a PowerShell command and return combined stdout/stderr.

    This is a deliberately powerful, unsandboxed action for a personal
    assistant running on the user's own machine. There is no confirmation
    gate here by design; be mindful of what you ask Jarvis to run.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return output or "(command completed with no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out."
