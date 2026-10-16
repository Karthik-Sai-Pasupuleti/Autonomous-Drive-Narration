"""VLM narration: narration_node (signals -> VLM -> /narration) + speaker_node.

    source /opt/ros/humble/setup.bash
    ros2 launch launch/vlm_narration.launch.py
    ros2 launch launch/vlm_narration.launch.py model:=minicpm-v4.5:latest backend:=espeak

Needs Ollama running (or provider:=openai with OPENAI_API_KEY set). If either
node exits, the whole launch shuts down.
"""
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, Shutdown
from launch.substitutions import LaunchConfiguration

ROOT = Path(__file__).resolve().parents[1]
NODES = ROOT / "src" / "drive-narration"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"

ARGS = {
    "narration_topic": ("/narration", "topic narrations are published / spoken on"),
    "provider": ("ollama", "VLM provider: ollama or openai"),
    "model": ("minicpm-v4.5:latest", "VLM model id / Ollama tag"),
    "temperature": ("0.1", "VLM sampling temperature"),
    "backend": ("auto", "TTS backend: auto, kokoro, espeak or mute"),
    "voice": ("af_heart", "Kokoro voice id"),
}


def _node(script: str, params: list[str]) -> ExecuteProcess:
    """Run one node script with the given launch arguments as ROS parameters."""
    cmd = [PYTHON, str(NODES / script), "--ros-args"]
    for name in params:
        cmd += ["-p", [f"{name}:=", LaunchConfiguration(name)]]
    return ExecuteProcess(cmd=cmd, cwd=str(NODES), name=Path(script).stem,
                          output="screen", emulate_tty=True,
                          on_exit=Shutdown(reason=f"{script} exited"))


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        *(DeclareLaunchArgument(name, default_value=default, description=desc)
          for name, (default, desc) in ARGS.items()),
        _node("narration_node.py", ["narration_topic", "provider", "model", "temperature"]),
        _node("speaker_node.py", ["narration_topic", "backend", "voice"]),
    ])
