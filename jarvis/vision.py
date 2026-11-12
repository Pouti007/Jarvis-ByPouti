"""Optional webcam hand-tracking with gesture control.

Uses MediaPipe's GestureRecognizer (Tasks API) to spot two built-in gestures —
Open_Palm and Closed_Fist — and derives a horizontal "swipe" from wrist motion.
Runs on a background thread; recognized gestures are delivered via callbacks.

This module is heavy (continuous webcam + CPU inference), so it is disabled by
default and only started when `vision.hand_tracking` is true in config.yaml.
"""

from __future__ import annotations

import base64
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable

import cv2
import mediapipe as mp
import numpy as np

MODEL_PATH = Path(__file__).parent.parent / "models" / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)

# Gesture debounce: ignore repeats of the same gesture within this many seconds.
GESTURE_COOLDOWN_SEC = 2.0
# Swipe detection: wrist must move at least this fraction of frame width...
SWIPE_MIN_DX = 0.35
# ...within this time window.
SWIPE_WINDOW_SEC = 0.5


def ensure_model() -> Path:
    """Download the gesture model on first use (mirrors how Whisper caches models)."""
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print("[vision] Downloading gesture recognizer model (~8MB)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def capture_frame(camera_index: int = 0):
    """Open the webcam briefly, grab one frame (BGR numpy array), and release it.

    Returns None if the camera can't be opened — e.g. it's already held by the
    hand tracker, in which case callers should use the tracker's shared frame.
    """
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        return None
    try:
        # Discard a few frames so the sensor can auto-expose before we keep one.
        frame = None
        for _ in range(5):
            ok, frame = cap.read()
        return frame if (frame is not None) else None
    finally:
        cap.release()


def frame_to_jpeg_b64(frame) -> str | None:
    """Encode a BGR frame as base64 JPEG for sending to Claude vision."""
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return base64.standard_b64encode(buf.tobytes()).decode("utf-8")


class HandTracker(threading.Thread):
    """Background webcam gesture tracker.

    Callbacks (each optional) fire on the tracker thread, so consumers that touch
    the GUI must marshal onto the Qt thread themselves (e.g. via a signal).
    """

    def __init__(
        self,
        on_open_palm: Callable[[], None] | None = None,
        on_fist: Callable[[], None] | None = None,
        on_swipe: Callable[[str], None] | None = None,
        on_gesture: Callable[[str], None] | None = None,
        camera_index: int = 0,
        show_window: bool = False,
        enable_swipe: bool = True,
    ):
        super().__init__(daemon=True)
        self.on_open_palm = on_open_palm
        self.on_fist = on_fist
        self.on_swipe = on_swipe
        self.on_gesture = on_gesture  # notified with a label for any recognized gesture (HUD feedback)
        self.camera_index = camera_index
        self.show_window = show_window
        self.enable_swipe = enable_swipe

        self._running = False
        self._last_gesture_time: dict[str, float] = {}
        self._wrist_history: deque[tuple[float, float]] = deque()  # (timestamp, x)
        self._last_ts_ms = -1  # ensures strictly increasing timestamps for MediaPipe

        # Most recent camera frame, shared so the vision Q&A feature can reuse it
        # instead of trying (and failing) to open the busy webcam a second time.
        self._latest_frame = None
        self._frame_lock = threading.Lock()

    def get_latest_frame(self):
        """Thread-safe copy of the most recent webcam frame, or None."""
        with self._frame_lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    # ------------------------------------------------------------- lifecycle

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        ensure_model()
        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
        )
        recognizer = mp.tasks.vision.GestureRecognizer.create_from_options(options)

        # On Windows, the default backend can be slow to open; DSHOW is more reliable.
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.camera_index)  # fall back to default backend
        if not cap.isOpened():
            print(f"[vision] Could not open webcam index {self.camera_index}; hand tracking disabled.")
            recognizer.close()
            return

        self._running = True
        try:
            self._loop(cap, recognizer)
        finally:
            cap.release()
            recognizer.close()
            if self.show_window:
                cv2.destroyAllWindows()

    # ------------------------------------------------------------- main loop

    def _next_timestamp_ms(self) -> int:
        """A strictly increasing millisecond timestamp (MediaPipe VIDEO mode rejects duplicates)."""
        ts = int(time.monotonic() * 1000)
        if ts <= self._last_ts_ms:
            ts = self._last_ts_ms + 1
        self._last_ts_ms = ts
        return ts

    def _loop(self, cap: cv2.VideoCapture, recognizer) -> None:
        while self._running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            try:
                frame = cv2.flip(frame, 1)  # mirror, so movement feels natural
                with self._frame_lock:
                    self._latest_frame = frame
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = recognizer.recognize_for_video(mp_image, self._next_timestamp_ms())
                self._handle_result(result)
            except Exception as exc:  # never let one bad frame kill the whole tracker
                print(f"[vision] frame processing error (continuing): {exc}")
                continue

            if self.show_window:
                self._draw_debug(frame, result)
                cv2.imshow("JARVIS Hand Tracking", frame)
                if cv2.waitKey(1) & 0xFF == 27:  # Esc closes the debug window
                    break

    def _handle_result(self, result) -> None:
        now = time.monotonic()

        # Track wrist x for swipe detection (landmark 0 is the wrist).
        if self.enable_swipe and result.hand_landmarks:
            wrist_x = result.hand_landmarks[0][0].x
            self._wrist_history.append((now, wrist_x))
            self._prune_history(now)
            self._check_swipe(now)
        else:
            self._wrist_history.clear()

        # Named gestures from the model.
        if not result.gestures:
            return
        top = result.gestures[0][0]
        name = top.category_name
        if top.score < 0.6:
            return

        if name == "Open_Palm":
            if self._fire("open_palm", self.on_open_palm, now) and self.on_gesture:
                self.on_gesture("open palm -> wake")
        elif name == "Closed_Fist":
            if self._fire("fist", self.on_fist, now) and self.on_gesture:
                self.on_gesture("fist -> stop")

    # ------------------------------------------------------------- helpers

    def _prune_history(self, now: float) -> None:
        while self._wrist_history and now - self._wrist_history[0][0] > SWIPE_WINDOW_SEC:
            self._wrist_history.popleft()

    def _check_swipe(self, now: float) -> None:
        if len(self._wrist_history) < 2:
            return
        xs = [x for _, x in self._wrist_history]
        dx = xs[-1] - xs[0]
        if abs(dx) >= SWIPE_MIN_DX:
            direction = "right" if dx > 0 else "left"
            if self._fire(f"swipe_{direction}", None, now):
                if self.on_swipe:
                    self.on_swipe(direction)
                if self.on_gesture:
                    self.on_gesture(f"swipe {direction} -> toggle window")
            self._wrist_history.clear()

    def _fire(self, key: str, callback: Callable[[], None] | None, now: float) -> bool:
        """Fire a gesture callback if it's off cooldown. Returns True if it fired."""
        last = self._last_gesture_time.get(key, 0.0)
        if now - last < GESTURE_COOLDOWN_SEC:
            return False
        self._last_gesture_time[key] = now
        if callback:
            callback()
        return True

    def _draw_debug(self, frame: np.ndarray, result) -> None:
        label = ""
        if result.gestures:
            g = result.gestures[0][0]
            label = f"{g.category_name} {g.score:.2f}"
        cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 217, 255), 2)
