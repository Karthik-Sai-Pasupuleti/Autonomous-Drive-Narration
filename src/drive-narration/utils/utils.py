"""Small helpers shared across the narration package."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no tomllib
    import tomli as tomllib

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def load_config(config_path: str) -> dict:
    """Load a TOML config file and return it as a dict.

    Args:
        config_path (str): path to the TOML file.

    Returns:
        dict: the parsed configuration.
    """
    return tomllib.loads(Path(config_path).read_text(encoding="utf-8"))


def load_node_config(node, config_model: type[ConfigT]) -> ConfigT:
    """Declare one ROS parameter per field of `config_model` (its default as
    the parameter default), then validate the launch values with pydantic.

    Args:
        node: the rclpy Node to declare the parameters on.
        config_model (type[BaseModel]): the node's config schema.

    Returns:
        BaseModel: the validated config.

    Raises:
        ValidationError: if a parameter value is invalid (logged as fatal).
    """
    defaults = config_model.model_construct()
    values = {name: node.declare_parameter(name, getattr(defaults, name)).value
              for name in config_model.model_fields}
    try:
        return config_model(**values)
    except ValidationError as exc:
        node.get_logger().fatal(f"invalid parameters:\n{exc}")
        raise
