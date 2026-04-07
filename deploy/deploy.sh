#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/PowerScalper"
BRANCH="main"
SERVICE_NAME="powerscalper"
WORKER_SERVICE_NAME="powerscalper-worker"

cd "$REPO_DIR"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"
sudo systemctl restart "$SERVICE_NAME"
if systemctl list-unit-files | grep -q "^${WORKER_SERVICE_NAME}\.service"; then
  sudo systemctl restart "$WORKER_SERVICE_NAME"
fi
sudo systemctl is-active --quiet "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME" | sed -n '1,12p'
