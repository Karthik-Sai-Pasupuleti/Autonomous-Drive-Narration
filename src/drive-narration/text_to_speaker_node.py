"""Manual narration: type a line, speaker_node speaks it.

    python text_to_speaker_node.py                          # interactive prompt
    python text_to_speaker_node.py "The robot detected a person."   # one line, then exit

Publishes each line as std_msgs/String on `narration_topic` - the same topic
narration_node publishes on - so speaker_node voices it like any narration.
Interactive mode: Enter sends, an empty line is skipped, `quit` or Ctrl+D exits.
"""
from __future__ import annotations

import signal
import sys
import time

import rclpy
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rclpy.node import Node
from rclpy.utilities import remove_ros_args
from std_msgs.msg import String

from utils.utils import load_node_config


class TextToSpeakerConfig(BaseModel):
    """The node's ROS parameters, validated at startup."""

    model_config = ConfigDict(extra="forbid")

    narration_topic: str = Field(default="/narration", pattern="^/")
    connect_timeout_s: float = Field(
        default=3.0, ge=0.0, description="how long to wait for speaker_node to "
        "subscribe before sending, so the first line isn't lost")


class ManualNarration(BaseModel):
    """One typed line to publish."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=500)


class TextToSpeakerNode(Node):
    """Publishes manually typed narrations for speaker_node."""

    def __init__(self) -> None:
        super().__init__("text_to_speaker_node")
        self.cfg = load_node_config(self, TextToSpeakerConfig)
        self._pub = self.create_publisher(String, self.cfg.narration_topic, 10)

    def wait_for_speaker(self) -> bool:
        """Wait up to connect_timeout_s for a subscriber on the topic."""
        deadline = time.monotonic() + self.cfg.connect_timeout_s
        while self._pub.get_subscription_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.1)
        if self._pub.get_subscription_count() == 0:
            self.get_logger().warning(f"no subscriber on {self.cfg.narration_topic} - "
                                      "is speaker_node running?")
            return False
        return True

    def send(self, text: str) -> bool:
        """Validate and publish one line; False if it was rejected."""
        try:
            line = ManualNarration(text=text)
        except ValidationError as exc:
            self.get_logger().warning(f"not sent: {exc.errors()[0]['msg']}")
            return False
        self._pub.publish(String(data=line.text))
        self.get_logger().info(f"SENT: {line.text}")
        return True

    def interactive(self) -> None:
        """Read lines from the terminal until `quit` or Ctrl+D."""
        print(f"Type a narration for {self.cfg.narration_topic} "
              "(Enter = send, 'quit' or Ctrl+D = exit)")
        while True:
            try:
                text = input("> ")
            except EOFError:
                print()
                return
            if text.strip().lower() in {"quit", "exit"}:
                return
            if text.strip():
                if self._pub.get_subscription_count() == 0:
                    self.get_logger().warning("no subscriber yet - this line may be lost")
                self.send(text)

def main(args=None) -> None:
    ros_args = remove_ros_args(sys.argv if args is None else args)
    rclpy.init(args=args)
    node = TextToSpeakerNode()
    try:
        node.wait_for_speaker()
        if len(ros_args) > 1:                  # one-shot: text given on the command line
            node.send(" ".join(ros_args[1:]))
            time.sleep(0.2)                    # let the message leave before shutdown
        else:
            node.interactive()
    except KeyboardInterrupt:
        print()
    finally:
        # ros2 launch forwards a second SIGINT; don't let it interrupt cleanup
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
