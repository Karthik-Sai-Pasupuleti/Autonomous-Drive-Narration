"""Blocking text-to-speech backends: Kokoro (neural) -> espeak-ng -> mute.

Every backend has the same contract: say(text) blocks until the line has
finished playing; stop() cuts off the current line (used on shutdown).
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

KOKORO_SAMPLE_RATE = 24_000  # Hz


class _ProcessSpeaker:
    """Shared playback: runs one audio subprocess at a time, stoppable."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _play(self, cmd: list[str]) -> None:
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        self._proc.wait()

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()


class KokoroSpeaker(_ProcessSpeaker):
    """Kokoro neural TTS on CPU, played through aplay."""

    name = "kokoro"

    def __init__(self, voice: str, speed: float, lang_code: str) -> None:
        super().__init__()
        from kokoro import KPipeline  # heavy, optional (`uv sync --extra tts`)
        if not shutil.which("aplay"):
            raise RuntimeError("aplay not found")
        self._pipeline = KPipeline(lang_code=lang_code, device="cpu")
        self._synth = {"voice": voice, "speed": speed}

    def say(self, text: str) -> None:
        chunks = [np.asarray(r.audio, dtype=np.float32)
                  for r in self._pipeline(text, **self._synth)]
        if not chunks:
            return
        pcm = (np.clip(np.concatenate(chunks), -1.0, 1.0) * 32767).astype(np.int16)
        with NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(KOKORO_SAMPLE_RATE)
                wav.writeframes(pcm.tobytes())
            self._play(["aplay", "-q", str(path)])
        finally:
            path.unlink(missing_ok=True)


class EspeakSpeaker(_ProcessSpeaker):
    """espeak-ng system TTS: robotic, but always available."""

    name = "espeak"

    def __init__(self, rate: int) -> None:
        super().__init__()
        if not shutil.which("espeak-ng"):
            raise RuntimeError("espeak-ng not found")
        self._rate = rate

    def say(self, text: str) -> None:
        self._play(["espeak-ng", "-s", str(self._rate), text])


class MuteSpeaker:
    """No audio: lines are only logged."""

    name = "mute"

    def say(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass


def make_speaker(backend: str, *, voice: str, speed: float, lang_code: str,
                 espeak_rate: int, logger) -> KokoroSpeaker | EspeakSpeaker | MuteSpeaker:
    """Build the requested backend; "auto" tries kokoro -> espeak -> mute."""
    builders = {"kokoro": lambda: KokoroSpeaker(voice, speed, lang_code),
                "espeak": lambda: EspeakSpeaker(espeak_rate),
                "mute": MuteSpeaker}
    order = ["kokoro", "espeak", "mute"] if backend == "auto" else [backend]
    for name in order:
        try:
            return builders[name]()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if backend != "auto":
                raise
            logger.warning(f"{name} TTS unavailable ({exc}), trying the next backend")
    return MuteSpeaker()
