#!/usr/bin/env bash
# Oracle Cloud (or any Ubuntu VM): install once, then it runs by itself -
# restarts on crash, starts on boot.
#
#   git clone https://github.com/crickp88-debug/crick-.git crick && cd crick
#   nano .env                 # YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN,
#                             # YOUTUBE_API_KEY, CHANNEL_NAME
#   bash deploy/install_server.sh        # both slots
#   SLOTS=1 bash deploy/install_server.sh   # one slot (a small VM)
#
#   journalctl -u crick-live@1 -f        watch it
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
USER_NAME="$(id -un)"
SLOTS="${SLOTS:-1 2}"

sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git >/dev/null
[ -d .venv ] || python3 -m venv .venv
PYTHON="$DIR/.venv/bin/python" bash deploy/setup_linux.sh

# PulseAudio wants a runtime dir; lingering gives the user one without a login
sudo loginctl enable-linger "$USER_NAME"

sudo tee /etc/systemd/system/crick-live@.service >/dev/null <<EOF
[Unit]
Description=crick-live autopilot, slot %i
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
Environment=PYTHONUTF8=1
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)
ExecStartPre=-/usr/bin/git -C $DIR pull --ff-only
ExecStart=$DIR/.venv/bin/python autopilot.py --slot %i --forever
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
for s in $SLOTS; do
    sudo systemctl enable --now "crick-live@$s"
done
echo "running: $(systemctl is-active $(for s in $SLOTS; do echo crick-live@$s; done) | tr '\n' ' ')"
