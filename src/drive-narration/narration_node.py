"""Narration trigger node.

    python narration_node.py
    python narration_node.py --ros-args -p model:=qwen2.5vl:7b

Three sources sit at different places along the route - the mobile pole, the
robot and the drone. Each publishes a std_msgs/UInt64 "car approaching" signal
(value 1) and a camera feed. When a source signals, the vision model narrates
that source's latest camera frame and the line is published as std_msgs/String
on `narration_topic` (speaker_node voices it).

The node keeps listening to every topic and holds the latest message of each.
Only the signals trigger; the detector topics just add context to the prompt
when fresh data is available:
- mobile_pole: autoware DetectedObjects -> the hazard and its distance to the
  ego vehicle (via TF).
- robot: vision_msgs Detection2DArray -> the hazard class (2D, no distance).
- drone: camera only.

Each source narrates exactly once per run: its narration flag starts False and
flips to True on its first signal, so later signals are ignored. The car meets
the sources in route order (pole -> robot -> drone), but each one simply
narrates when its own signal arrives. One VLM call runs at a time.
"""
from __future__ import annotations

import math
import os
import queue
import signal
import threading
import time
from functools import partial
from pathlib import Path

import rclpy
import requests
import tf2_ros
from autoware_perception_msgs.msg import DetectedObjects
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String, UInt64
from vision_msgs.msg import Detection2DArray

from Bot.bot import AgentConfig, LLMBot, Narration_Input, Narration_Output
from utils.detections import Detection, parse_detected_objects, parse_detection2d_array
from utils.images import is_blank, to_b64_jpeg
from utils.utils import load_node_config

HERE = Path(__file__).resolve().parent

CAR_APPROACHING = 1     # signal value meaning "car approaching"
SOURCES = ("mobile_pole", "robot", "drone")     # in the order the car meets them

_TOPIC_FIELDS = ("narration_topic", "pole_signal_topic", "pole_image_topic",
                 "pole_objects_topic", "robot_signal_topic", "robot_image_topic",
                 "robot_image_compressed_topic", "robot_detections_topic",
                 "drone_signal_topic", "drone_image_topic", "drone_image_raw_topic")


class NodeConfig(BaseModel):
    """The node's ROS parameters. Every field is declared as a parameter with
    this default, then the values set at launch are validated here."""

    model_config = ConfigDict(extra="forbid")

    # model
    provider: str = Field(default="ollama", pattern="^(ollama|openai)$")
    model: str = Field(default="minicpm-v4.6:latest", min_length=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    keep_alive: str = "30m"
    prompt: str = str(HERE / "Bot" / "configs" / "prompt.toml")
    # output
    narration_topic: str = "/narration"
    # mobile pole
    pole_signal_topic: str = "/mobile_pole/signal_car_approaching"
    pole_image_topic: str = "/mobile_pole/axis_rgb_6_42/image_visualization"
    pole_objects_topic: str = "/mobile_pole/axis_rgb_6_42/autoware_objects_3d"
    # robot: bags carry either the raw or the compressed feed, both are read
    robot_signal_topic: str = "/robot/signal_car_approaching"
    robot_image_topic: str = "/robot/image_annotated"
    robot_image_compressed_topic: str = "/robot/image_annotated/compressed"
    robot_detections_topic: str = "/robot/detections"
    # drone: like the robot, both the compressed and the raw feed are read
    drone_signal_topic: str = "/drone/signal_car_approaching"
    drone_image_topic: str = "/drone/image_annotated/compressed"
    drone_image_raw_topic: str = "/drone/camera/traffic_objects_annotated"
    # usable-image check: no VLM call without a real, recent camera frame
    frame_max_age_s: float = Field(
        default=5.0, gt=0.0, description="an older frame means the camera stopped")
    blank_std_threshold: float = Field(
        default=3.0, ge=0.0, description="grey-level std (0-255) below which a frame "
        "counts as blank, e.g. an all-black feed; 0 disables the check")
    # detection context for the prompt
    pole_hazard_classes: list[str] = Field(default_factory=lambda: ["pedestrian"],
                                           min_length=1)
    robot_hazard_classes: list[str] = Field(default_factory=lambda: ["person"],
                                            min_length=1)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    detection_max_age_s: float = Field(
        default=1.0, gt=0.0, description="older detections are left out of the prompt")
    # TF frames for the pole's pedestrian-to-vehicle distance:
    # ego_frame is the car itself, looked up in the detections' header.frame_id;
    # map_frame is the fixed world frame, used only when that frame_id is empty.
    map_frame: str = Field(default="map", min_length=1)
    ego_frame: str = Field(default="base_link", min_length=1)

    @field_validator("prompt")
    @classmethod
    def _prompt_exists(cls, path: str) -> str:
        if not Path(path).is_file():
            raise ValueError(f"prompt file not found: {path}")
        return path

    @field_validator(*_TOPIC_FIELDS)
    @classmethod
    def _absolute_topic(cls, topic: str) -> str:
        if not topic.startswith("/"):
            raise ValueError(f"topic must start with '/', got {topic!r}")
        return topic


class Frame(BaseModel):
    """Latest camera message of a source and its wall-clock arrival time."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    msg: Image | CompressedImage
    stamp: float = Field(gt=0.0)


class DetectionSnapshot(BaseModel):
    """Latest hazard detections of a source, kept as prompt context."""

    model_config = ConfigDict(frozen=True)

    stamp: float = Field(gt=0.0)
    frame_id: str = ""
    hazards: list[Detection]


class Job(BaseModel):
    """One narration request for the worker."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    frame: Frame
    hint: str = ""


class NarrationNode(Node):
    """Narrates a source's camera frame whenever it signals a car approaching."""

    def __init__(self) -> None:
        super().__init__("narration_node")
        self.cfg = c = load_node_config(self, NodeConfig)

        agent = AgentConfig(provider=c.provider, model=c.model, temperature=c.temperature,
                            keep_alive=c.keep_alive, prompt=c.prompt, vision=True)
        _warm_ollama(agent, self.get_logger())
        self._bot = LLMBot(agent, Narration_Output)

        # Per-source state, keyed by "mobile_pole" / "robot" / "drone":
        self._frames: dict[str, Frame] = {}                  # latest camera frame
        self._detections: dict[str, DetectionSnapshot] = {}  # latest hazards (context)
        # Narration flags: False until the source's first "car approaching"
        # signal, then True for the rest of the run so it never repeats.
        self._narrated: dict[str, bool] = dict.fromkeys(SOURCES, False)
        self._lock = threading.Lock()   # the worker resets a flag if narration fails

        self._jobs: queue.Queue[Job | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run_worker, name="narrator",
                                        daemon=True)
        self._worker.start()

        self._tf_buffer = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)
        self._pub = self.create_publisher(String, c.narration_topic, 10)

        sensors = qos_profile_sensor_data
        signals = 10    # reliable, matching the signal publishers
        # mobile pole
        self.create_subscription(UInt64, c.pole_signal_topic,
                                 partial(self._on_signal, "mobile_pole"), signals)
        self.create_subscription(Image, c.pole_image_topic,
                                 partial(self._store_frame, "mobile_pole"), sensors)
        self.create_subscription(DetectedObjects, c.pole_objects_topic,
                                 self._on_pole_objects, sensors)
        # robot
        self.create_subscription(UInt64, c.robot_signal_topic,
                                 partial(self._on_signal, "robot"), signals)
        self.create_subscription(Image, c.robot_image_topic,
                                 partial(self._store_frame, "robot"), sensors)
        self.create_subscription(CompressedImage, c.robot_image_compressed_topic,
                                 partial(self._store_frame, "robot"), sensors)
        self.create_subscription(Detection2DArray, c.robot_detections_topic,
                                 self._on_robot_detections, sensors)
        # drone
        self.create_subscription(UInt64, c.drone_signal_topic,
                                 partial(self._on_signal, "drone"), signals)
        self.create_subscription(CompressedImage, c.drone_image_topic,
                                 partial(self._store_frame, "drone"), sensors)
        self.create_subscription(Image, c.drone_image_raw_topic,
                                 partial(self._store_frame, "drone"), sensors)

        self.get_logger().info(
            f"narrating with {c.provider}/{c.model} -> {c.narration_topic} | triggers: "
            f"{c.pole_signal_topic}, {c.robot_signal_topic}, {c.drone_signal_topic}")

    # ------------------------------------------------------------ triggers
    def _on_signal(self, source: str, msg: UInt64) -> None:
        """A 1 triggers the source's narration, using its latest frame and
        detections at this instant. Any other value is ignored."""
        if msg.data == CAR_APPROACHING:
            self._trigger(source)

    # ------------------------------------------------------------ context
    def _store_frame(self, source: str, msg: Image | CompressedImage) -> None:
        self._frames[source] = Frame(msg=msg, stamp=time.time())

    def _on_pole_objects(self, msg: DetectedObjects) -> None:
        self._store_detections("mobile_pole", parse_detected_objects(msg),
                               self.cfg.pole_hazard_classes, msg.header.frame_id)

    def _on_robot_detections(self, msg: Detection2DArray) -> None:
        self._store_detections("robot", parse_detection2d_array(msg),
                               self.cfg.robot_hazard_classes)

    def _store_detections(self, source: str, detections: list[Detection],
                          classes: list[str], frame_id: str = "") -> None:
        hazards = [d for d in detections
                   if d.label in classes and d.confidence >= self.cfg.min_confidence]
        self._detections[source] = DetectionSnapshot(stamp=time.time(), frame_id=frame_id,
                                                     hazards=hazards)

    def _hint(self, source: str) -> str:
        """Prompt context from the source's fresh detections: the nearest
        hazard and its distance when there is a position and an ego pose,
        else the most confident hazard, else nothing."""
        snap = self._detections.get(source)
        if (snap is None or not snap.hazards
                or time.time() - snap.stamp > self.cfg.detection_max_age_s):
            return ""
        located = [d for d in snap.hazards if d.position is not None]
        ego = self._ego_xy(snap.frame_id or self.cfg.map_frame) if located else None
        if ego is not None:
            nearest = min(located, key=lambda d: math.dist(d.position[:2], ego))
            dist = math.dist(nearest.position[:2], ego)
            return f"A {nearest.label} is {dist:.1f} m from the vehicle."
        best = max(snap.hazards, key=lambda d: d.confidence)
        return f"A {best.label} was detected."

    def _ego_xy(self, frame_id: str) -> tuple[float, float] | None:
        """Ego (x, y) in `frame_id`, or None if TF can't resolve it yet."""
        try:
            tf = self._tf_buffer.lookup_transform(frame_id, self.cfg.ego_frame, Time())
        except tf2_ros.TransformException as exc:
            self.get_logger().warning(f"no {frame_id}->{self.cfg.ego_frame} transform, "
                                      f"narrating without distance ({exc})",
                                      throttle_duration_sec=5.0)
            return None
        t = tf.transform.translation
        return t.x, t.y

    # ------------------------------------------------------------ narration
    def _trigger(self, source: str) -> None:
        """Queue `source`'s one narration with its latest frame and distance,
        unless its flag is already True or it has no usable image. Without one
        the VLM is not called and the flag stays False for the next signal."""
        if self._narrated[source]:
            return                           # already narrated this run
        frame = self._frames.get(source)
        problem = self._unusable(frame)
        if problem:
            self.get_logger().warning(f"{source} signalled but {problem}; "
                                      "not calling the VLM, waiting for the next signal")
            return
        with self._lock:
            if self._narrated[source]:
                return
            self._narrated[source] = True
        hint = self._hint(source)            # detections at this same instant
        self.get_logger().info(f"trigger [{source}] car approaching. {hint}".rstrip())
        self._jobs.put(Job(source=source, frame=frame, hint=hint))

    def _unusable(self, frame: Frame | None) -> str | None:
        """Why `frame` can't be narrated, or None if it is a usable image."""
        if frame is None:
            return "it has no camera frame yet"
        age = time.time() - frame.stamp
        if age > self.cfg.frame_max_age_s:
            return f"its latest camera frame is {age:.1f}s old"
        if self.cfg.blank_std_threshold > 0:
            try:
                if is_blank(frame.msg, self.cfg.blank_std_threshold):
                    return "its camera frame is blank"
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return f"its camera frame can't be decoded ({exc})"
        return None

    def _run_worker(self) -> None:
        """Serial worker: one VLM call at a time. A failed narration resets
        the source's flag so its next signal tries again."""
        while (job := self._jobs.get()) is not None:
            try:
                self._narrate(job)
                continue
            except ValidationError as exc:
                self.get_logger().warning(f"narration [{job.source}] rejected: {exc}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.get_logger().error(f"narration [{job.source}] failed: {exc}")
            with self._lock:
                self._narrated[job.source] = False

    def _narrate(self, job: Job) -> None:
        start = time.time()
        # Both ends are validated: a bad input or an empty / malformed model
        # reply raises ValidationError, which the worker logs and skips.
        inputs = Narration_Input(image=to_b64_jpeg(job.frame.msg),
                                 source=job.source, detections=job.hint)
        out = Narration_Output.model_validate(self._bot.invoke(inputs))
        line = out.narration
        self._pub.publish(String(data=line))
        done = time.time()
        self.get_logger().info(
            f"NARRATION [{job.source}]: {line} | {self.cfg.model} {done - start:.2f}s, "
            f"frame age {done - job.frame.stamp:.2f}s | narrated: {self._narrated}")

    def close(self) -> None:
        self._jobs.put(None)
        self._worker.join(timeout=5.0)
        self.destroy_node()


def _warm_ollama(cfg: AgentConfig, logger) -> None:
    """Load the model into VRAM up front so the first trigger doesn't pay the
    cold-load time. Best-effort: on failure the first narration is just slower."""
    if cfg.provider != "ollama":
        return
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    start = time.perf_counter()
    try:
        requests.post(f"{host}/api/generate", timeout=180,
                      json={"model": cfg.model, "prompt": "", "keep_alive": cfg.keep_alive})
        logger.info(f"warmed '{cfg.model}' in {time.perf_counter() - start:.1f}s")
    except requests.RequestException as exc:
        logger.warning(f"could not warm '{cfg.model}': {exc}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = NarrationNode()
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
