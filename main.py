"""JARVIS entry point: wires voice, brain, vault, GUI, and tray together."""

from __future__ import annotations

import os

# Silence the harmless Hugging Face "symlinks / Developer Mode" warning that
# faster-whisper's model download prints on Windows. Must be set before any
# huggingface_hub import (i.e. before importing jarvis.voice). Downloads still
# work fine; HF just copies model files instead of symlinking them.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

SINGLE_INSTANCE_KEY = "jarvis_single_instance"

from gui.dashboard import Dashboard
from jarvis.brain import JarvisBrain
from jarvis.obsidian import ObsidianVault
from jarvis.tray import TrayIcon
from jarvis.voice import VoiceEngine

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.yaml"

# Secrets (ANTHROPIC_API_KEY, FINNHUB_API_KEY) live in .env, which is git-ignored.
load_dotenv(PROJECT_DIR / ".env")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    # Resolve a relative vault path against the project folder, so it works no
    # matter which directory JARVIS is launched from (e.g. the startup shortcut).
    vault_path = Path(config["obsidian"]["vault_path"])
    if not vault_path.is_absolute():
        config["obsidian"]["vault_path"] = str(PROJECT_DIR / vault_path)
    return config


def time_of_day_greeting(name: str) -> str:
    hour = datetime.now().hour
    if hour < 12:
        part = "morning"
    elif hour < 18:
        part = "afternoon"
    else:
        part = "evening"
    return f"Good {part}, {name}. All systems online."


class VoiceWorker(QThread):
    state_changed = pyqtSignal(str)
    wake_detected = pyqtSignal()
    user_said = pyqtSignal(str)
    jarvis_said = pyqtSignal(str)

    def __init__(self, voice: VoiceEngine, brain: JarvisBrain):
        super().__init__()
        self.voice = voice
        self.brain = brain
        self._running = True
        self.voice.on_state_change = self.state_changed.emit

    def run(self) -> None:
        while self._running:
            # A failure in any single turn must not kill the listening loop.
            try:
                heard = self.voice.listen_for_wake_word(stop_check=lambda: not self._running)
                if not heard:
                    break
                self.wake_detected.emit()

                command_text = self.voice.listen_for_command()
                if not command_text.strip():
                    continue
                self.user_said.emit(command_text)

                reply = self.brain.process_command(command_text)
                self.jarvis_said.emit(reply)
                self.voice.speak(reply)
            except Exception as exc:  # noqa: BLE001 - keep the assistant alive no matter what
                import traceback

                traceback.print_exc()
                self.jarvis_said.emit(f"Sorry, something went wrong: {exc}")

    def stop(self) -> None:
        self._running = False


class TextCommandWorker(QThread):
    """Processes a single typed command off the GUI thread (so the UI stays responsive)."""

    jarvis_said = pyqtSignal(str)

    def __init__(self, voice: VoiceEngine, brain: JarvisBrain, text: str):
        super().__init__()
        self.voice = voice
        self.brain = brain
        self.text = text

    def run(self) -> None:
        try:
            reply = self.brain.process_command(self.text)
            self.jarvis_said.emit(reply)
            self.voice.speak(reply)
        except Exception as exc:  # noqa: BLE001 - never crash the app over one command
            import traceback

            traceback.print_exc()
            self.jarvis_said.emit(f"Sorry, something went wrong: {exc}")


class TrayBridge(QObject):
    """Marshals callbacks fired on other threads (tray, hand tracker) onto the Qt main thread."""

    show_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    toggle_requested = pyqtSignal()
    gesture_detected = pyqtSignal(str)


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS assistant")
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start hidden in the system tray (used by the auto-start shortcut).",
    )
    args = parser.parse_args()

    config = load_config()
    # Start minimized if either the CLI flag or the config setting asks for it.
    start_minimized = args.minimized or config.get("gui", {}).get("start_minimized", False)

    # Last-resort safety net: log unhandled exceptions instead of letting them
    # tear the process down (PyQt aborts on unhandled exceptions by default).
    def _log_uncaught(exc_type, exc, tb):
        import traceback

        print("[jarvis] UNHANDLED EXCEPTION:")
        traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = _log_uncaught
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda a: _log_uncaught(a.exc_type, a.exc_value, a.exc_traceback)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing the window minimizes to tray instead of quitting

    # Single-instance guard: if JARVIS is already running, ask that instance to
    # show its window and exit now — before loading Whisper/models — so we never
    # end up with two processes fighting over the microphone.
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_KEY)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        print("JARVIS is already running — focusing the existing window.")
        return
    # We are the first instance: listen so future launches can reach us.
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)  # clear any stale socket from a crash
    local_server = QLocalServer()
    local_server.listen(SINGLE_INSTANCE_KEY)

    obs_cfg = config["obsidian"]
    vault = ObsidianVault(
        vault_path=obs_cfg["vault_path"],
        daily_folder=obs_cfg.get("daily_notes_folder", "Daily"),
        jarvis_folder=obs_cfg.get("jarvis_notes_folder", "Jarvis"),
    )

    dashboard = Dashboard(vault)
    if not start_minimized:
        dashboard.show()

    brain = JarvisBrain(config, vault)

    voice_cfg = config["voice"]
    voice = VoiceEngine(
        wake_words=voice_cfg["wake_words"],
        whisper_model=voice_cfg.get("whisper_model", "small"),
        wake_word_whisper_model=voice_cfg.get("wake_word_whisper_model", "tiny"),
        whisper_device=voice_cfg.get("whisper_device", "cpu"),
        whisper_compute_type=voice_cfg.get("whisper_compute_type", "int8"),
        input_device=voice_cfg.get("input_device"),
        silence_timeout_sec=voice_cfg.get("silence_timeout_sec", 1.2),
        command_timeout_sec=voice_cfg.get("command_timeout_sec", 12.0),
        vad_aggressiveness=voice_cfg.get("vad_aggressiveness", 1),
        tts_rate=voice_cfg.get("tts_rate", 0),
        tts_voice=voice_cfg.get("tts_voice", ""),
    )

    worker = VoiceWorker(voice, brain)
    worker.state_changed.connect(dashboard.set_state)
    worker.wake_detected.connect(lambda: dashboard.add_transcript("System", "Wake word detected."))
    worker.user_said.connect(lambda t: dashboard.add_transcript("You", t))

    def on_jarvis_said(text: str) -> None:
        dashboard.add_transcript("Jarvis", text)
        dashboard.refresh_notes()

    worker.jarvis_said.connect(on_jarvis_said)

    # Typed commands: run each in its own short-lived worker, keeping a reference
    # until it finishes so it isn't garbage-collected mid-run.
    text_workers: set[TextCommandWorker] = set()

    def on_command_typed(text: str) -> None:
        dashboard.add_transcript("You (typed)", text)
        tw = TextCommandWorker(voice, brain, text)
        tw.jarvis_said.connect(on_jarvis_said)
        tw.finished.connect(lambda: text_workers.discard(tw))
        text_workers.add(tw)
        tw.start()

    dashboard.command_entered.connect(on_command_typed)

    def show_window() -> None:
        dashboard.showNormal()
        dashboard.activateWindow()

    def toggle_window() -> None:
        if dashboard.isVisible():
            dashboard.hide()
        else:
            show_window()

    # When a second launch pings our local server, surface the existing window.
    def on_second_instance() -> None:
        conn = local_server.nextPendingConnection()
        if conn is not None:
            conn.readAll()
            conn.disconnectFromServer()
        show_window()

    local_server.newConnection.connect(on_second_instance)

    # Optional webcam hand tracking (heavy; off unless enabled in config).
    hand_tracker = None
    vision_cfg = config.get("vision", {})
    if vision_cfg.get("hand_tracking", False):
        from jarvis.vision import HandTracker

        hand_tracker = HandTracker(
            on_open_palm=voice.trigger_wake,
            on_fist=voice.request_stop,
            on_swipe=lambda direction: bridge.toggle_requested.emit(),
            on_gesture=lambda label: bridge.gesture_detected.emit(label),
            camera_index=vision_cfg.get("camera_index", 0),
            show_window=vision_cfg.get("show_camera_window", False),
            enable_swipe=vision_cfg.get("swipe_gesture", True),
        )

    # Camera provider for the vision Q&A tool: reuse the hand tracker's live frame
    # when it's running (avoids a webcam conflict); otherwise capture on demand.
    def camera_provider():
        if hand_tracker is not None and hand_tracker.is_alive():
            frame = hand_tracker.get_latest_frame()
            if frame is not None:
                return frame
        from jarvis.vision import capture_frame

        return capture_frame(vision_cfg.get("camera_index", 0))

    brain.camera_provider = camera_provider

    def quit_app() -> None:
        worker.stop()
        if hand_tracker is not None:
            hand_tracker.stop()
        local_server.close()
        voice.speak(f"Goodbye, {config['user']['name']}. Shutting down.")
        worker.wait(2000)
        app.quit()

    bridge = TrayBridge()
    bridge.show_requested.connect(show_window)
    bridge.quit_requested.connect(quit_app)
    bridge.toggle_requested.connect(toggle_window)
    bridge.gesture_detected.connect(lambda label: dashboard.add_transcript("Gesture", label))

    tray = TrayIcon(on_show=bridge.show_requested.emit, on_quit=bridge.quit_requested.emit)
    tray.run_detached()
    app.aboutToQuit.connect(tray.stop)

    # Boot sequence: confirmation tone + personalized greeting
    voice.confirmation_tone()
    greeting = time_of_day_greeting(config["user"]["name"])
    dashboard.add_transcript("Jarvis", greeting)
    voice.speak(greeting)

    worker.start()
    if hand_tracker is not None:
        hand_tracker.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
