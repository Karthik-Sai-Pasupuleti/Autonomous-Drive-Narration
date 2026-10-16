"""ROS camera messages -> base64 JPEG for the vision model, plus a blank check.

No ROS import: works on any object shaped like sensor_msgs/Image or
sensor_msgs/CompressedImage.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

# sensor_msgs/Image encoding -> (PIL mode, PIL raw decoder mode)
_PIL_MODE = {"rgb8": ("RGB", "RGB"), "bgr8": ("RGB", "BGR"),
             "rgba8": ("RGBA", "RGBA"), "bgra8": ("RGBA", "BGRA"),
             "mono8": ("L", "L")}


def _is_raw(msg) -> bool:
    return hasattr(msg, "encoding")      # Image has .encoding, CompressedImage .format


def to_pil(msg) -> Image.Image:
    """Either camera message type -> PIL image."""
    if _is_raw(msg):
        mode, raw_mode = _PIL_MODE[msg.encoding]
        return Image.frombytes(mode, (msg.width, msg.height), bytes(msg.data),
                               "raw", raw_mode, msg.step)
    return Image.open(io.BytesIO(bytes(msg.data)))


def to_b64_jpeg(msg) -> str:
    """Either camera message type -> base64 JPEG. JPEG payloads pass straight
    through; everything else is decoded and re-encoded."""
    if not _is_raw(msg):
        data = bytes(msg.data)
        if data[:2] == b"\xff\xd8":             # JPEG magic: no re-encode needed
            return base64.b64encode(data).decode("ascii")
    buf = io.BytesIO()
    to_pil(msg).convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def is_blank(msg, min_std: float) -> bool:
    """True if the frame is (nearly) one flat colour - a dead, black or covered
    camera - judged by the grey-level standard deviation (0-255 scale) of a
    downscaled copy."""
    img = to_pil(msg).convert("L")
    img.thumbnail((320, 320))
    return float(np.asarray(img).std()) < min_std
