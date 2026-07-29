#!/bin/bash
# One-click launcher for led-kiosk-launcher.desktop: starts the backend if it
# isn't already running (e.g. led-kiosk-backend.service isn't installed/enabled
# on this device), then hands off to start_kiosk.sh for the browser, which
# already knows how to wait for the backend to come up. The systemd services
# in this folder are still the recommended always-on setup; this script is
# the "just double-click the icon" path so the client doesn't need them
# installed for the app to start.
#
# Doubles as the "restore" path for the in-app minimize button
# (app/routes/system.py minimize_browser()): if Chromium is already running
# (just hidden, not killed), re-show and focus that window instead of
# launching a second one.
APP_DIR="$HOME/led-kiosk"

if pgrep -f chromium > /dev/null; then
  xdotool search --class chromium windowmap windowactivate
  exit 0
fi

if ! curl -s http://127.0.0.1:8000/api/status > /dev/null; then
  cd "$APP_DIR"
  nohup venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
    > /tmp/led-kiosk-backend.log 2>&1 &
  disown
fi

exec "$APP_DIR/kiosk/start_kiosk.sh"
