# LED kiosk controller

Touchscreen kiosk app for a Raspberry Pi 4 that abstracts MQTT commands to an
LED-strip device into a friendly button/slider interface, shows a live log of
a subscribed topic, and lets you schedule commands for a future date/time.

## What's here

```
app/
  main.py            FastAPI app, mounts routes + static/templates
  config.py          loads config.json
  db.py              sqlite: logs + scheduled_commands tables
  mqtt_client.py      paho-mqtt wrapper: subscribe, log, publish, websocket fan-out
  scheduler.py        APScheduler wrapper, persisted via sqlite, re-arms on restart
  models.py            request/response pydantic models
  routes/
    commands.py        GET profile, POST send command
    logs.py             GET history, WS live tail
    schedule.py         CRUD for scheduled commands
  static/              vanilla JS + CSS frontend (no build step)
  templates/index.html
profiles/
  device_commands.json  <-- edit this to add/change LED effects & their params
kiosk/
  start_kiosk.sh                launches Chromium in kiosk mode
  led-kiosk-backend.service     systemd unit for the FastAPI app
  led-kiosk-browser.service     systemd unit for the kiosk browser
config.example.json    copy to config.json and fill in your broker details
```

## Local dev (on your laptop, before deploying to the Pi)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # edit mqtt host/topics
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 -- works in a normal browser window, no Pi needed
to iterate on the UI. You'll need something publishing to the log topic and
subscribed to the command topic to see it fully come alive (or point it at a
real broker/device on your network).

## Editing the LED effects

Everything in `profiles/device_commands.json` becomes a button in the UI
automatically -- no frontend code changes needed to add a new effect:

```json
{
  "id": "chase",
  "label": "Chase",
  "params": [
    { "name": "color", "type": "color", "default": "#3B8BD4" },
    { "name": "speed_ms", "type": "slider", "min": 10, "max": 1000, "default": 120 }
  ]
}
```

Supported param types right now: `color`, `slider` (needs `min`/`max`), and a
plain text fallback for anything else. When a command fires, the target
device receives:

```json
{ "command": "chase", "args": { "color": "#3b8bd4", "speed_ms": 120 } }
```

on the configured `command_topic`.

## Deploying to the Pi (outline)

1. `git clone` (or copy) this folder to `/home/pi/led-kiosk`.
2. `python3 -m venv venv && venv/bin/pip install -r requirements.txt`
3. `cp config.example.json config.json` and fill in the real broker
   host/topics.
4. Set up WiFi/LTE failover at the OS level (NetworkManager + ModemManager
   for the A7600, WiFi as the lower-metric/preferred route) -- this app just
   reads connectivity status, it doesn't manage the interfaces itself.
5. Install the two systemd services in `kiosk/` and enable them.
6. Configure auto-login to a minimal desktop session (raspi-config) so
   Chromium has something to run in, and consider `systemctl mask
   getty@tty2` etc. plus an overlay/read-only root filesystem so the client
   can't corrupt the SD card by power-cycling mid-write.

## Not yet built (intentionally)

- **LoRa transport.** `mqtt_client.publish_command()` is the single choke
  point every command goes through. Adding LoRa later means writing a
  sibling `lora_client.publish_command()` with the same signature and a way
  to pick the transport per-command (or a fallback: try MQTT, fall back to
  LoRa if the broker's unreachable) -- no changes needed to the UI, the
  profile schema, or the scheduler.
- **Auth.** There's no login on the API/UI. Fine on an isolated kiosk device;
  add something before exposing this beyond localhost.
- **A7600 AT-command integration.** Connectivity is handled at the OS level
  (NetworkManager/ModemManager), not from within this app. The `/api/status`
  endpoint only reports MQTT connection state right now -- extend it if you
  want the UI to show WiFi vs LTE state too.
