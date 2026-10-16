"""Detector messages -> validated Detection models.

No ROS import: works on a live rclpy message or an mcap-decoded one.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# autoware_perception_msgs/ObjectClassification label constants
LABELS = {0: "unknown object", 1: "car", 2: "truck", 3: "bus", 4: "trailer",
          5: "motorcycle", 6: "bicycle", 7: "pedestrian", 8: "animal",
          9: "hazard", 10: "over-drivable object", 11: "under-drivable object"}


class Detection(BaseModel):
    """One detected object, from either detector."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1, description="class name, e.g. 'pedestrian'")
    confidence: float = Field(ge=0.0, le=1.0)
    position: tuple[float, float, float] | None = Field(
        default=None, description="(x, y, z) in the message's header.frame_id; "
        "None for 2D detectors")


def parse_detected_objects(ros_msg) -> list[Detection]:
    """autoware_perception_msgs/DetectedObjects -> detections, using each
    object's top classification. Malformed objects are skipped."""
    detections = []
    for obj in ros_msg.objects:
        if not obj.classification:
            continue
        cls = max(obj.classification, key=lambda c: c.probability)
        p = obj.kinematics.pose_with_covariance.pose.position
        try:
            detections.append(Detection(label=LABELS.get(cls.label, f"class {cls.label}"),
                                        confidence=cls.probability,
                                        position=(p.x, p.y, p.z)))
        except ValidationError:
            continue
    return detections


def parse_detection2d_array(ros_msg) -> list[Detection]:
    """vision_msgs/Detection2DArray -> detections, one per hypothesis (2D boxes
    carry no position). Malformed hypotheses are skipped."""
    detections = []
    for det in ros_msg.detections:
        for result in det.results:
            try:
                detections.append(Detection(label=result.hypothesis.class_id,
                                            confidence=result.hypothesis.score))
            except ValidationError:
                continue
    return detections
