"""Speaker node: voices the narrations published by narration_node.

    python speaker_node.py
    python speaker_node.py --ros-args -p backend:=espeak -p voice:=am_adam

Subscribes to std_msgs/String on `narration_topic` and speaks each line, in
arrival order, one at a time - a line is never cut off by the next one. Lines
that waited longer than `max_age_s` are dropped as no longer relevant.

TTS backend: Kokoro (neural, `uv sync --extra tts`) -> espeak-ng -> mute.
"""
from __future__ import annotations

import queue
import signal
import threading
import time

import rclpy
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String

from utils.tts import make_speaker
from utils.utils import load_node_config


class SpeakerConfig(BaseModel):
    """The node's ROS parameters, validated at startup."""

    model_config = ConfigDict(extra="forbid")

    narration_topic: str = Field(default="/narration", pattern="^/")
    backend: str = Field(default="auto", pattern="^(auto|kokoro|espeak|mute)$")
    voice: str = Field(default="af_heart", min_length=1, description="Kokoro voice id")
    speed: float = Field(default=1.0, gt=0.0, le=3.0, description="Kokoro speaking rate")
    lang_code: str = Field(default="a", min_length=1, description="Kokoro language code")
    espeak_rate: int = Field(default=160, gt=0, description="espeak-ng words per minute")
    max_age_s: float = Field(default=30.0, gt=0.0,
                             description="drop lines that waited longer than this")
    max_queue: int = Field(default=10, gt=0)


class Utterance(BaseModel):
    """One line to speak and when it arrived."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    text: str = Field(min_length=1)
    received: float = Field(gt=0.0)


class SpeakerNode(Node):
    """Speaks each narration in order through the selected TTS backend."""

    def __init__(self) -> None:
        super().__init__("speaker_node")
        self.cfg = c = load_node_config(self, SpeakerConfig)
        self._tts = make_speaker(c.backend, voice=c.voice, speed=c.speed,
                                 lang_code=c.lang_code, espeak_rate=c.espeak_rate,
                                 logger=self.get_logger())

        self._lines: queue.Queue[Utterance | None] = queue.Queue(maxsize=c.max_queue)
        self._worker = threading.Thread(target=self._run_worker, name="speaker",
                                        daemon=True)
        self._worker.start()

        self.create_subscription(String, c.narration_topic, self._on_narration, 10)
        self.get_logger().info(f"speaking {c.narration_topic} with {self._tts.name}")

    def _on_narration(self, msg: String) -> None:
        try:
            line = Utterance(text=msg.data, received=time.time())
        except ValidationError:
            self.get_logger().warning("ignoring empty narration")
            return
        try:
            self._lines.put_nowait(line)
        except queue.Full:
            self.get_logger().warning(f"speech queue full, dropping: {line.text}")

    def _run_worker(self) -> None:
        """Serial worker: one line at a time, oldest first."""
        while (line := self._lines.get()) is not None:
            age = time.time() - line.received
            if age > self.cfg.max_age_s:
                self.get_logger().warning(f"dropping {age:.1f}s-old line: {line.text}")
                continue
            start = time.time()
            try:
                self._tts.say(line.text)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.get_logger().error(f"TTS failed: {exc}")
                continue
            self.get_logger().info(f"SPOKE: {line.text} | waited {age:.2f}s, "
                                   f"spoke {time.time() - start:.2f}s")

    def close(self) -> None:
        self._tts.stop()
        while True:                     # discard what's queued, then stop the worker
            try:
                self._lines.get_nowait()
            except queue.Empty:
                break
        self._lines.put(None)
        self._worker.join(timeout=5.0)
        self.destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SpeakerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # ros2 launch forwards a second SIGINT; don't let it interrupt cleanup
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.close()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
