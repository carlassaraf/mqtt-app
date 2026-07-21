#!/bin/bash
# Pulls the latest code, restarts the backend (so any Python changes take
# effect -- static JS/CSS/HTML don't need this, but uvicorn isn't running
# with --reload), clears Chromium's disk cache (so stale JS/CSS isn't served
# from it), and relaunches the kiosk browser fresh. Run manually (terminal,
# SSH, or its own desktop icon) whenever new commits need to reach the
# device -- nothing here runs automatically or on a schedule.
#
# Checks for actual new commits first and exits without touching anything
# (no kill, no cache clear, no relaunch) if there's nothing to pull -- the
# update button shouldn't interrupt the client's session for a no-op.
set -e
APP_DIR="$HOME/led-kiosk"
cd "$APP_DIR"

echo "==> checking for updates"
git fetch
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse @{u})
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "==> already up to date, nothing to do"
  exit 0
fi

echo "==> pulling changes"
git merge --ff-only @{u}

echo "==> updating dependencies"
venv/bin/pip install -q -r requirements.txt

echo "==> stopping the running backend + browser"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f chromium 2>/dev/null || true
sleep 1

echo "==> clearing Chromium's cache"
rm -rf ~/.cache/chromium ~/.cache/chromium-browser

echo "==> relaunching"
exec "$APP_DIR/kiosk/launch_app.sh"
