"""RViz chase view of the drive: rviz2 + the ego vehicle model.

    source /opt/ros/humble/setup.bash
    ros2 launch launch/rviz.launch.py
    ros2 launch launch/rviz.launch.py ego_model:=false     # bag already has /robot_description

Runs on sim time, so play the bag with --clock:
    ros2 bag play ~/Downloads/overlap_02 --clock
"""
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

ROOT = Path(__file__).resolve().parents[1]
RVIZ_DIR = ROOT / "src" / "Rviz"

# VS Code (snap) terminals inject GTK/GIO paths that crash rviz2; drop them.
_SNAP_ENV = ("GTK_PATH", "GTK_EXE_PREFIX", "GTK_IM_MODULE_FILE", "GDK_PIXBUF_MODULEDIR",
             "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "LOCPATH")
CLEAN_ENV = "env " + " ".join(f"-u {name}" for name in _SNAP_ENV)


def generate_launch_description() -> LaunchDescription:
    use_sim_time = ParameterValue(LaunchConfiguration("use_sim_time"), value_type=bool)
    return LaunchDescription([
        DeclareLaunchArgument("rviz_config", default_value=str(RVIZ_DIR / "narration_chase.rviz"),
                              description="RViz config file"),
        DeclareLaunchArgument("ego_model", default_value="true",
                              description="publish src/Rviz/ego_vehicle.urdf as "
                              "/robot_description (false if the bag already has it)"),
        DeclareLaunchArgument("use_sim_time", default_value="true",
                              description="follow the bag's /clock (play it with --clock)"),
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             name="ego_model", output="screen",
             condition=IfCondition(LaunchConfiguration("ego_model")),
             parameters=[{"robot_description": (RVIZ_DIR / "ego_vehicle.urdf").read_text(),
                          "use_sim_time": use_sim_time}]),
        Node(package="rviz2", executable="rviz2", name="rviz2", output="screen",
             prefix=CLEAN_ENV,
             arguments=["-d", LaunchConfiguration("rviz_config")],
             parameters=[{"use_sim_time": use_sim_time}],
             on_exit=Shutdown(reason="rviz2 closed")),
    ])
