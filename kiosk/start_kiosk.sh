#!/bin/bash
# Launches Chromium in kiosk mode pointed at the local backend.
# Run as the desktop user via the led-kiosk-browser systemd service (see the
# .service files in this folder) or an X11 autostart entry.

# Wait for the backend to actually be up before pointing the browser at it.
until curl -s http://127.0.0.1:8000/api/status > /dev/null; do
  sleep 1
done

exec chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  http://127.0.0.1:8000/
