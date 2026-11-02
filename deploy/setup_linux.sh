#!/usr/bin/env bash
# One setup for every Linux box the streamer runs on: a GitHub runner, an
# Oracle Cloud VM (x86 or ARM), a Docker image. Safe to re-run.
#
#   bash deploy/setup_linux.sh            uses python3
#   PYTHON=.venv/bin/python bash deploy/setup_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SUDO=""
[ "$(id -u)" != "0" ] && SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive

# virtual screen + virtual speaker + encoder + software OpenGL for the 3D
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends \
    xvfb x11-utils pulseaudio pulseaudio-utils ffmpeg \
    libgl1 libglx-mesa0 libgl1-mesa-dri libegl1 libx11-6 libxext6 \
    libxrandr2 libxcursor1 libxi6 libxxf86dga1 libxrender1 \
    fontconfig curl ca-certificates >/dev/null

PY="${PYTHON:-python3}"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements-server.txt

# the Hindi voice (Piper), ~60 MB
mkdir -p voices
if [ ! -s voices/hi_IN-pratham-medium.onnx ]; then
    "$PY" -m piper.download_voices hi_IN-pratham-medium --data-dir voices || {
        base=https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/pratham/medium
        curl -fsSL -o voices/hi_IN-pratham-medium.onnx "$base/hi_IN-pratham-medium.onnx"
        curl -fsSL -o voices/hi_IN-pratham-medium.onnx.json "$base/hi_IN-pratham-medium.onnx.json"
    }
fi

# the cut-out model for player photos and thumbnails, ~170 MB
if [ ! -s "$HOME/.u2net/u2net.onnx" ]; then
    mkdir -p "$HOME/.u2net"
    curl -fsSL -o "$HOME/.u2net/u2net.onnx" \
        https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx
fi

echo "setup done: $("$PY" --version), $(ffmpeg -version | head -1 | cut -c1-40)"
