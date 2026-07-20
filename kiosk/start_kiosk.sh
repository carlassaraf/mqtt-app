#!/bin/bash
# Launches Chromium in kiosk mode pointed at the local backend.
# Run as the desktop user via the led-kiosk-browser systemd service (see the
# .service files in this folder) or an X11 autostart entry.

# Wait for the backend to actually be up before pointing the browser at it.
until curl -s http://127.0.0.1:8000/api/status > /dev/null; do
  sleep 1
done

# Raspberry Pi OS Bookworm renamed the package from chromium-browser to
# chromium (the old wrapper name isn't shipped anymore); older releases still
# use chromium-browser. Try both rather than hardcoding one.
BROWSER_BIN=$(command -v chromium-browser || command -v chromium)
if [ -z "$BROWSER_BIN" ]; then
  echo "start_kiosk.sh: no chromium-browser or chromium binary found -- install one with 'sudo apt install chromium'" >&2
  exit 1
fi

exec "$BROWSER_BIN" \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  http://127.0.0.1:8000/
