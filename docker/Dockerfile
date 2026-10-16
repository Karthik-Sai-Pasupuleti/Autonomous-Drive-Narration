FROM ros:humble-ros-base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON=/usr/bin/python3.10 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    FASTRTPS_DEFAULT_PROFILES_FILE=/app/docker/fastdds_udp_only.xml \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-autoware-perception-msgs \
      ros-humble-vision-msgs \
      espeak-ng \
      alsa-utils \
      libasound2-plugins \
      pulseaudio-utils \
      libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > /etc/asound.conf

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./

RUN GPU_PKGS=$(grep -oP '^name = "\K(nvidia-[^"]+|cuda-[^"]+|triton|torch)(?=")' uv.lock | sort -u) \
    && TORCH_VERSION=$(awk '/^name = "torch"$/{getline; print}' uv.lock | cut -d'"' -f2) \
    && uv sync --frozen --extra tts --no-install-project \
         $(printf -- '--no-install-package %s ' $GPU_PKGS) \
    && uv pip install --python /app/.venv/bin/python --no-deps --index-url https://download.pytorch.org/whl/cpu \
         "torch==${TORCH_VERSION}" \
    && uv pip install --python /app/.venv/bin/python --no-deps \
         https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

RUN HF_HUB_OFFLINE=0 python -c "\
from kokoro import KPipeline; \
p = KPipeline(lang_code='a', device='cpu', repo_id='hexgrad/Kokoro-82M'); \
out = list(p('warm up', voice='af_heart')); \
assert out and len(out[0].audio) > 1000, 'kokoro produced no audio'"

COPY docker ./docker
COPY launch ./launch
COPY src ./src
RUN chmod +x docker/entrypoint.sh

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["ros2", "launch", "launch/vlm_narration.launch.py"]
