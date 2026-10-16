# Autonomous-Drive-Narration

Spoken narration for passengers in an autonomous vehicle. Three roadside
sources sit at different places along the route: a **mobile pole**, a
**robot** and a **drone**. When a source signals that the car is approaching,
a vision-language model (VLM) looks at that source's latest camera frame and
writes one sentence describing what it sees, e.g. *"The mobile pole detected a
person standing close to a car."* A speaker node then reads it aloud.

Built on ROS 2 Humble, Ollama (local VLM, default `minicpm-v4.6`) and Kokoro /
espeak-ng for text-to-speech.

## How it works

```
 /mobile_pole/signal_car_approaching ─┐
 /robot/signal_car_approaching ───────┤  (UInt64, 1 = car approaching)
 /drone/signal_car_approaching ───────┤
                                      ▼
 camera + detector topics ──▶  narration_node ──▶ /narration ──▶ speaker_node ──▶ 🔊
 /tf (ego pose)                (VLM, once per        (String)      (Kokoro /
                                source)                ▲            espeak-ng)
                                                       │
                               text_to_speaker_node ───┘  (type a line manually)
```

- **`narration_node`** keeps the latest camera frame and detections from every
  source. The *only* trigger is that source's `signal_car_approaching` topic
  reading `1`. On the trigger it sends the frame to the VLM, along with
  optional context from the detections, and publishes the sentence on
  `/narration`.
  - **One narration per source per run.** Each source has a flag that starts
    `False` and flips to `True` on its first signal, so the narration never
    repeats. If the VLM call fails, the flag resets and the next signal retries.
  - **Context in the prompt:**
    - Mobile pole: the nearest pedestrian and its distance to the car (from TF
      `map → base_link`), e.g. *"A pedestrian is 3.5 m from the vehicle."*
    - Robot: the detected class, e.g. *"A person was detected."*
    - Drone: none, image only.

    Context is only added when the detections are fresh (< 1 s). Without it,
    the VLM narrates from the image alone.
- **`speaker_node`** speaks every line on `/narration` in arrival order, one at
  a time, never cutting a line off. Lines that waited more than 30 s are dropped.
- **`text_to_speaker_node`** lets you type a narration by hand. It publishes to
  the same `/narration` topic, so the speaker treats it like any other line.

All settings are ROS parameters, validated with pydantic at startup. A bad
value, e.g. `min_confidence:=2.0`, stops the node with a clear error.

## Repository layout

```
launch/
  vlm_narration.launch.py      narration_node + speaker_node
  text_narration.launch.py     speaker_node + text_to_speaker_node (own terminal)
  rviz.launch.py               RViz chase view + ego vehicle model
docker/
  Dockerfile                   ROS 2 Humble image with the nodes, deps and Kokoro voice
  entrypoint.sh                sources ROS, then runs the command
  fastdds_udp_only.xml         DDS over UDP so host topics reach the container
docker-compose.yml             narration, speaker and text services
src/drive-narration/
  narration_node.py            signals → VLM → /narration
  speaker_node.py              /narration → speech
  text_to_speaker_node.py      keyboard → /narration
  Bot/
    bot.py                     LLMBot: LangChain + Ollama/OpenAI, pydantic I/O models
    configs/prompt.toml        system prompt + user template for the VLM
  utils/
    detections.py              DetectedObjects / Detection2DArray → Detection models
    images.py                  sensor_msgs Image / CompressedImage → base64 JPEG
    tts.py                     Kokoro → espeak-ng → mute speech backends
    utils.py                   TOML loader, ROS-parameter → pydantic config loader
src/Rviz/
  narration_chase.rviz         optional chase-camera RViz view
  ego_vehicle.urdf             ego vehicle model for that view
```

## Setup

Requirements: Ubuntu 22.04, ROS 2 Humble, Python 3.10, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com).

```bash
# ROS message packages used by narration_node
sudo apt install ros-humble-vision-msgs ros-humble-autoware-perception-msgs espeak-ng

# Python environment (.venv). Add --extra tts for the Kokoro neural voice
uv sync --extra tts

# VLM
ollama pull minicpm-v4.6
```

The launch files use the project's `.venv` Python automatically. Run
`source /opt/ros/humble/setup.bash` in every terminal before the commands below.

## Running

### VLM narration with a rosbag

Use three terminals, plus an optional fourth for RViz.

**1. Narration and speaker.** Start this first. It loads the model into
memory; wait for the line `narrating with ...`.
```bash
ros2 launch launch/vlm_narration.launch.py
```

**2. Play the bag.** `--clock` publishes the bag's time, which RViz follows.
Press Space in this terminal to pause and resume.
```bash
ros2 bag play ~/Downloads/overlap_02 --clock
```

**3. Send the car-approaching signals.** Current bags don't contain these
topics, so publish them by hand when each source's scene is on screen:
```bash
ros2 topic pub --once /mobile_pole/signal_car_approaching std_msgs/msg/UInt64 "{data: 1}"
ros2 topic pub --once /robot/signal_car_approaching       std_msgs/msg/UInt64 "{data: 1}"
ros2 topic pub --once /drone/signal_car_approaching       std_msgs/msg/UInt64 "{data: 1}"
```
Each source narrates once. Restart terminal 1 to narrate again.

### Manual narration

```bash
ros2 launch launch/text_narration.launch.py
```
A new terminal window opens. Type a sentence and press Enter to hear it.
`quit`, Ctrl+D or closing the window ends the launch.

To send a single line instead, while a speaker node is running:
```bash
.venv/bin/python src/drive-narration/text_to_speaker_node.py "The robot detected a person."
```

### Launch options

| Launch file | Argument | Default | |
|---|---|---|---|
| both | `narration_topic` | `/narration` | topic narrations are published on |
| both | `backend` | `auto` | TTS: `auto` (kokoro → espeak → mute), `kokoro`, `espeak`, `mute` |
| both | `voice` | `af_heart` | Kokoro voice id |
| vlm_narration | `provider` | `ollama` | `ollama` or `openai` (needs `OPENAI_API_KEY`) |
| vlm_narration | `model` | `minicpm-v4.6:latest` | VLM model / Ollama tag |
| vlm_narration | `temperature` | `0.1` | VLM sampling temperature |
| text_narration | `terminal` | `gnome-terminal --wait --` | program that opens the typing window |

Example:
```bash
ros2 launch launch/vlm_narration.launch.py model:=minicpm-v4.5:latest backend:=espeak
```

Every other node parameter can be set when running a node directly, e.g. the
topic names, `min_confidence`, `detection_max_age_s`, `map_frame` /
`ego_frame` for `narration_node`, or `max_age_s` for `speaker_node`:
```bash
.venv/bin/python src/drive-narration/narration_node.py --ros-args -p min_confidence:=0.6
```
See the `*Config` classes at the top of each node for the full list and defaults.

## Topics

| Topic | Type | Role |
|---|---|---|
| `/mobile_pole/signal_car_approaching` | `std_msgs/UInt64` | pole trigger (`1`) |
| `/mobile_pole/axis_rgb_6_42/image_visualization` | `sensor_msgs/Image` | pole camera |
| `/mobile_pole/axis_rgb_6_42/autoware_objects_3d` | `autoware_perception_msgs/DetectedObjects` | pole detections (distance context) |
| `/robot/signal_car_approaching` | `std_msgs/UInt64` | robot trigger (`1`) |
| `/robot/image_annotated` (`/compressed`) | `sensor_msgs/Image` / `CompressedImage` | robot camera |
| `/robot/detections` | `vision_msgs/Detection2DArray` | robot detections (class context) |
| `/drone/signal_car_approaching` | `std_msgs/UInt64` | drone trigger (`1`) |
| `/drone/camera/traffic_objects_annotated`, `/drone/image_annotated/compressed` | `sensor_msgs/Image` / `CompressedImage` | drone camera |
| `/tf`, `/tf_static` | `tf2_msgs/TFMessage` | ego pose for the pole distance |
| `/narration` | `std_msgs/String` | narration output → speaker |

## Docker

The image bundles ROS 2 Humble, the message packages, the locked Python
dependencies (CPU-only PyTorch), and the Kokoro voice. Nothing is downloaded
at run time. Three things stay on the host:
- **The bag:** the container joins the host's ROS graph (`network_mode: host`).
- **Ollama:** the container uses the host's Ollama at `localhost:11434`, with its models and GPU.
- **Sound:** played through the host's PulseAudio.

```bash
docker compose build
```

**VLM narration.** The container runs the nodes; the bag and the signals
run in ordinary host terminals, with `source /opt/ros/humble/setup.bash` in
each:
```bash
# terminal 1: narration + speaker, in the container
docker compose up narration

# terminal 2: the bag, on the host
ros2 bag play ~/Downloads/overlap_02 --clock

# terminal 3: the signals, on the host
ros2 topic pub --once /mobile_pole/signal_car_approaching std_msgs/msg/UInt64 "{data: 1}"
ros2 topic pub --once /robot/signal_car_approaching       std_msgs/msg/UInt64 "{data: 1}"
ros2 topic pub --once /drone/signal_car_approaching       std_msgs/msg/UInt64 "{data: 1}"
```
Before sending signals, wait for both startup lines in terminal 1:
`narrating with ...` and `speaking /narration with kokoro`. To change the model
or voice backend:
```bash
MODEL=minicpm-v4.6:latest TTS_BACKEND=espeak docker compose up narration
```

The host's topics reach the container as long as:
- `ROS_DOMAIN_ID` is the same on both sides (default `0`). For another domain,
  start the container with it too: `ROS_DOMAIN_ID=5 docker compose up narration`.
- `ROS_LOCALHOST_ONLY` is set the same way on both sides, or not at all.
- Ollama is running on the host.

To check that the bag reaches the container, run this while the bag plays:
```bash
docker compose exec narration bash -c \
  "source /opt/ros/humble/setup.bash && ros2 topic hz /mobile_pole/axis_rgb_6_42/image_visualization"
```
A steady rate means the topics are arriving. Fast DDS is limited to UDP inside
the container (`docker/fastdds_udp_only.xml`). Without that, the container
would see the topic names but receive no messages.

**Manual narration:**
```bash
docker compose --profile manual up -d speaker
docker compose run --rm text
docker compose --profile manual down
```

| Variable | Default | |
|---|---|---|
| `MODEL` | `minicpm-v4.5:latest` | VLM model / Ollama tag |
| `TTS_BACKEND` | `auto` | `auto`, `kokoro`, `espeak` or `mute` |
| `ROS_DOMAIN_ID` | `0` | must match the host terminals |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |

`OPENAI_API_KEY` is read from an optional `.env` file. RViz isn't in the
image; run `launch/rviz.launch.py` on the host. Code changes need a
`docker compose build`.

## Optional: RViz chase view

In a fourth terminal, before or after starting the bag:
```bash
ros2 launch launch/rviz.launch.py
```
This opens RViz with `src/Rviz/narration_chase.rviz`, and publishes the ego
vehicle model (`src/Rviz/ego_vehicle.urdf`) as `/robot_description`. RViz
runs on sim time, so the bag must be played with `--clock`. Closing the RViz
window ends the launch.

| Argument | Default | |
|---|---|---|
| `rviz_config` | `src/Rviz/narration_chase.rviz` | RViz config to load |
| `ego_model` | `true` | publish the ego URDF; set `false` if the bag already has `/robot_description` |
| `use_sim_time` | `true` | follow the bag's `/clock` |

On start-up RViz logs `PluginlibFactory ... failed to load` for a few Autoware
panels and tools (e.g. `AutowareStatePanel`, `CarInitialPoseTool`) that aren't
installed. The view works without them. One `Detected jump back in time` is
normal when the bag's clock starts.

## Notes

- **No usable image, no VLM call.** If a source signals but its camera frame
  is missing, older than 5 s (`frame_max_age_s`) or blank (a flat colour,
  `blank_std_threshold`), the VLM is not called. The node logs why, and the
  source's flag stays `False`, so a later signal can still narrate. This
  matters for `overlap_02`: every frame of `/drone/camera/traffic_objects_annotated`
  in that bag is all zeros, and without the check the VLM invented a scene from
  the black image.
- **Model choice.** `minicpm-v4.6` (1.6 GB) is fast, about 0.5–0.8 s per
  narration. `minicpm-v4.5` (6.1 GB) writes cleaner sentences, and is the better
  choice for German output.
- **Prompt.** The VLM's wording is controlled by
  [`Bot/configs/prompt.toml`](src/drive-narration/Bot/configs/prompt.toml).
- **VS Code terminals.** If `rclpy` fails to import in the VS Code terminal,
  run `unset LD_LIBRARY_PATH` before sourcing ROS.
