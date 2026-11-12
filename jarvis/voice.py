"""Speech I/O: faster-whisper STT with VAD-based wake word detection, and SAPI TTS.

Runs fully offline/local except for the Claude API call that happens elsewhere
(brain.py). No cloud wake-word service is used, keeping this dependency-light:
webrtcvad finds speech segments, faster-whisper transcribes them, and we just
check the transcript for the configured wake word(s).
"""

from __future__ import annotations

import threading
import winsound
from typing import Callable

import numpy as np
import pythoncom
import sounddevice as sd
import webrtcvad
import win32com.client
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class VoiceEngine:
    def __init__(
        self,
        wake_words: list[str],
        whisper_model: str = "small",
        wake_word_whisper_model: str = "tiny",
        whisper_device: str = "cpu",
        whisper_compute_type: str = "int8",
        input_device: int | None = None,
        silence_timeout_sec: float = 1.2,
        command_timeout_sec: float = 12.0,
        vad_aggressiveness: int = 1,
        tts_rate: int = 0,
        tts_voice: str = "",
        on_state_change: Callable[[str], None] | None = None,
    ):
        self.wake_words = [w.lower() for w in wake_words]
        self.input_device = input_device
        self.silence_timeout_sec = silence_timeout_sec
        self.command_timeout_sec = command_timeout_sec
        self.tts_rate = tts_rate
        self.tts_voice = tts_voice
        self.on_state_change = on_state_change or (lambda state: None)

        # A small/accurate model for real commands, and a much faster tiny
        # model just for spotting the wake word in short listening windows.
        self.command_model = WhisperModel(whisper_model, device=whisper_device, compute_type=whisper_compute_type)
        self.wake_model = WhisperModel(
            wake_word_whisper_model, device=whisper_device, compute_type=whisper_compute_type
        )
        self.vad = webrtcvad.Vad(vad_aggressiveness)

        # COM (SAPI) is apartment-threaded: each OS thread that calls Speak()
        # must have its own initialized apartment and SpVoice instance.
        self._tts_local = threading.local()

        # Set externally (e.g. by a hand gesture) to trigger listening or to
        # interrupt whatever JARVIS is currently doing.
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()

    # -------------------------------------------------------- gesture hooks

    def trigger_wake(self) -> None:
        """Force the wake-word loop to return immediately (e.g. open-palm gesture)."""
        self.wake_event.set()

    def request_stop(self) -> None:
        """Interrupt current speech or command capture (e.g. fist gesture)."""
        self.stop_event.set()

    # --------------------------------------------------------------- helpers

    def _set_state(self, state: str) -> None:
        self.on_state_change(state)

    def _transcribe(self, audio: np.ndarray, model: WhisperModel) -> str:
        if audio.size == 0:
            return ""
        audio_f32 = audio.astype(np.float32) / 32768.0
        # beam_size=1 (greedy) is much faster than the default beam search of 5,
        # with negligible accuracy loss for short spoken commands — a big
        # responsiveness win on CPU. condition_on_previous_text=False avoids the
        # model dragging in unrelated prior context.
        segments, _ = model.transcribe(
            audio_f32,
            language="en",
            vad_filter=False,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _record_utterance(self, max_seconds: float, silence_timeout: float) -> np.ndarray:
        """Record from the mic, starting once VAD detects speech and stopping
        after `silence_timeout` seconds of silence (or `max_seconds` total)."""
        frames: list[np.ndarray] = []
        speech_started = False
        silence_frames = 0
        silence_limit = max(1, int(silence_timeout * 1000 / FRAME_MS))
        max_frames = int(max_seconds * 1000 / FRAME_MS)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self.input_device,
        ) as stream:
            for _ in range(max_frames):
                if self.stop_event.is_set():
                    break  # gesture (fist) aborted this capture
                block, _ = stream.read(FRAME_SAMPLES)
                block = block[:, 0]
                is_speech = self.vad.is_speech(block.tobytes(), SAMPLE_RATE)

                if is_speech:
                    speech_started = True
                    silence_frames = 0
                    frames.append(block)
                elif speech_started:
                    silence_frames += 1
                    frames.append(block)
                    if silence_frames >= silence_limit:
                        break

        if not frames:
            return np.array([], dtype=np.int16)
        return np.concatenate(frames)

    # ---------------------------------------------------------------- public

    def listen_for_wake_word(self, stop_check: Callable[[], bool] | None = None) -> bool:
        """Blocks until a wake word is heard or a wake gesture fires.

        Returns False if stop_check() becomes True.
        """
        self._set_state("idle")
        self.stop_event.clear()
        while stop_check is None or not stop_check():
            # An open-palm gesture (or any external trigger) short-circuits here.
            if self.wake_event.is_set():
                self.wake_event.clear()
                self.confirmation_tone()
                return True
            audio = self._record_utterance(max_seconds=4.0, silence_timeout=0.8)
            if audio.size == 0:
                continue
            text = self._transcribe(audio, self.wake_model).lower()
            if any(w in text for w in self.wake_words):
                self.confirmation_tone()
                return True
        return False

    def listen_for_command(self) -> str:
        self._set_state("listening")
        self.stop_event.clear()
        audio = self._record_utterance(
            max_seconds=self.command_timeout_sec,
            silence_timeout=self.silence_timeout_sec,
        )
        self._set_state("idle")
        if self.stop_event.is_set():  # fist gesture cancelled the command
            self.stop_event.clear()
            return ""
        return self._transcribe(audio, self.command_model)

    def confirmation_tone(self) -> None:
        winsound.Beep(880, 120)

    def _get_tts_voice(self):
        """Returns this thread's SAPI voice object, initializing COM on first use."""
        voice = getattr(self._tts_local, "voice", None)
        if voice is None:
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._select_voice(voice)
            self._tts_local.voice = voice
        return voice

    def _select_voice(self, voice) -> None:
        """Pick the installed SAPI voice whose name contains self.tts_voice."""
        if not self.tts_voice:
            return
        wanted = self.tts_voice.lower()
        for token in voice.GetVoices():
            if wanted in token.GetDescription().lower():
                voice.Voice = token
                return
        print(f"[voice] No installed TTS voice matched '{self.tts_voice}'; using default.")

    # SAPI ISpVoice.Speak flags
    _SVSF_ASYNC = 1
    _SVSF_PURGE_BEFORE_SPEAK = 2

    def speak(self, text: str, rate: int | None = None) -> None:
        if not text:
            return
        self._set_state("speaking")
        self.stop_event.clear()
        try:
            voice = self._get_tts_voice()
            voice.Rate = self.tts_rate if rate is None else rate
            # Speak asynchronously so we can poll for an interrupt (fist gesture)
            # while still blocking this thread until speech finishes — that keeps
            # the mic from re-opening and hearing JARVIS's own voice.
            voice.Speak(text, self._SVSF_ASYNC)
            while not voice.WaitUntilDone(50):
                if self.stop_event.is_set():
                    voice.Speak("", self._SVSF_PURGE_BEFORE_SPEAK)  # purge queue = stop
                    break
        finally:
            self._set_state("idle")
