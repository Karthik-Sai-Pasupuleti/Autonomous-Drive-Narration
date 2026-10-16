"""Manual narration: speaker_node + text_to_speaker_node in its own terminal.

    source /opt/ros/humble/setup.bash
    ros2 launch launch/text_narration.launch.py
    ros2 launch launch/text_narration.launch.py backend:=espeak

ros2 launch doesn't forward keyboard input, so text_to_speaker_node opens in a
new terminal window: type a line there and speaker_node speaks it. Closing that
window (or typing `quit`) shuts the launch down.
"""
import os
import shlex
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, Shutdown
from launch.substitutions import LaunchConfiguration

ROOT = Path(__file__).resolve().parents[1]
NODES = ROOT / "src" / "drive-narration"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"

# A new terminal window starts from the desktop session's environment, not this
# shell's, so ROS is sourced again there and these networking settings carried over.
FORWARDED_ENV = ("ROS_DOMAIN_ID", "ROS_LOCALHOST_ONLY", "RMW_IMPLEMENTATION",
                 "FASTRTPS_DEFAULT_PROFILES_FILE", "CYCLONEDDS_URI")


def _text_node_in_terminal(context):
    topic = LaunchConfiguration("narration_topic").perform(context)
    terminal = shlex.split(LaunchConfiguration("terminal").perform(context))
    distro = os.environ.get("ROS_DISTRO", "humble")
    exports = "".join(f"export {k}={shlex.quote(os.environ[k])}; "
                      for k in FORWARDED_ENV if k in os.environ)
    script = (f"{exports}source /opt/ros/{distro}/setup.bash; "
              f"cd {shlex.quote(str(NODES))}; "
              f"exec {shlex.quote(PYTHON)} text_to_speaker_node.py "
              f"--ros-args -p narration_topic:={shlex.quote(topic)}")
    return [ExecuteProcess(cmd=[*terminal, "bash", "-c", script],
                           name="text_to_speaker_node", output="screen",
                           on_exit=Shutdown(reason="manual narration terminal closed"))]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument("narration_topic", default_value="/narration",
                              description="topic the typed lines are published / spoken on"),
        DeclareLaunchArgument("backend", default_value="auto",
                              description="TTS backend: auto, kokoro, espeak or mute"),
        DeclareLaunchArgument("voice", default_value="af_heart",
                              description="Kokoro voice id"),
        DeclareLaunchArgument("terminal", default_value="gnome-terminal --wait --",
                              description="command that opens the typing window "
                              "(must stay attached until it closes)"),
        ExecuteProcess(
            cmd=[PYTHON, str(NODES / "speaker_node.py"), "--ros-args",
                 "-p", ["narration_topic:=", LaunchConfiguration("narration_topic")],
                 "-p", ["backend:=", LaunchConfiguration("backend")],
                 "-p", ["voice:=", LaunchConfiguration("voice")]],
            cwd=str(NODES), name="speaker_node", output="screen", emulate_tty=True,
            on_exit=Shutdown(reason="speaker_node exited")),
        OpaqueFunction(function=_text_node_in_terminal),
    ])
