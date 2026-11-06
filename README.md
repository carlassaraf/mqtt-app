# LED kiosk controller

Touchscreen kiosk app for a Raspberry Pi 4 that abstracts MQTT commands to an
LED-strip device into a friendly button/slider interface, shows a live log of
a subscribed topic, and lets you schedule commands for a future date/time.

## What's here

```
app/
  main.py            FastAPI app, mounts routes + static/templates
  config.py          loads config.json
  db.py              sqlite: logs, scheduled_commands, orders, installations tables
  manager.py          installation manager: validates, numbers and publishes orders
  mqtt_client.py      paho-mqtt wrapper: subscribe, route by topic, send orders, websocket fan-out
  device_state.py     what each installation shows + per-unit health
  legacy.py           firmware 1.x devices (the NQN column) until they're replaced
  scheduler.py        APScheduler wrapper, persisted via sqlite, re-arms on restart
  models.py            request/response pydantic models
  routes/
    commands.py        GET profile, POST send command / order
    installations.py   targets (installations + legacy devices), units table, maintenance (ROL/STA)
    legacy.py           firmware 1.x device state, manual STA
    logs.py             GET history, WS live tail
    schedule.py         CRUD for scheduled commands
  static/              vanilla JS + CSS frontend (no build step)
  templates/index.html
profiles/
  device_commands.json  <-- edit this to add/change LED effects & their params
kiosk/
  start_kiosk.sh                launches Chromium in kiosk mode
  launch_app.sh                 starts the backend (if not already running) + start_kiosk.sh
  update_app.sh                 git pull + restart backend + clear Chromium cache + relaunch
  led-kiosk-backend.service     systemd unit for the FastAPI app
  led-kiosk-browser.service     systemd unit for the kiosk browser
  led-kiosk-launcher.desktop    one-click desktop icon that runs launch_app.sh
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

## The device protocol (Columna RGB)

This targets a specific device firmware: 3-letter command codes + an
optional value, no separators (`FRM5`, `BRI70`, `PPCFF0000`), sent
**UPPERCASE EXACTLY** over MQTT -- the device rejects anything else.
`app/mqtt_client.py:build_payload()` is the single place that builds and
force-uppercases the final string, so nothing upstream of it (the UI, the
scheduler) needs to worry about casing.

Everything in `profiles/device_commands.json` becomes a button in the UI
automatically -- no frontend code changes needed to add or adjust a command:

```json
{ "id": "BRI", "label": "Brightness", "value_type": "slider", "min": 0, "max": 100, "default": 70, "unit": "%" }
```

Supported `value_type`s: `slider`, `number`, `hex_color` (color picker,
`#` stripped automatically), `toggle` (renders 0/1), and `none` (no input --
`INV`, `AUT`). Only display commands belong in the profile: they're sent as
numbered orders to a whole installation (firmware 2.0, see `CONTEXT.md`).
Unit maintenance (`STA`, `ROL`) lives in the Unidades tab. A `fixed_value` field skips the input
entirely and always sends that exact value; pair it with `"confirm": true` +
`"confirm_text"` to force a confirmation dialog before sending.

**Known gaps worth closing once you have more info from the client:**
- `FRM`'s `min`/`max` in the profile is a placeholder (`min: 1`, no `max`)
  -- update it once you know how many frames are loaded on the device.
- `BLK`/`ROT` have no upper bound in the profile since the guide doesn't
  specify one; the device will reject out-of-range values regardless, so
  this is just about UI friendliness, not correctness.

**Things this app deliberately does NOT do**, per the guide:
- **SMS backup channel.** The device also accepts commands via SMS as a
  no-internet fallback, and sends status notifications to two authorized
  phone numbers. That's independent of this app and not wired in here --
  worth asking the client whether they want SMS visibility in the UI too
  (e.g. showing the same notifications this app can't see).
- **`NET`.** Turning a master's data connection off and on is an admin
  command, not an operator one, so this app doesn't offer it at all (the API
  rejects it too). `NET1` only works via SMS anyway, since a master is
  offline from MQTT's perspective once `NET0` is sent.
- **Public broker, no auth.** `broker.hivemq.com` has no username/password,
  so anyone who knows the topic name can publish to it -- the device's own
  input validation is the real safety net, not the broker. Worth knowing if
  the client ever asks "who else can control this."
- **Client ID collision.** Masters use the MQTT client ID
  `columna-<inst>-<group>-<unit>` -- this app's config uses a different one
  (`led-kiosk-controller`) on purpose. Don't change it to match the
  device's, or one of the two will get disconnected by the broker.

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
7. Optional, for a one-click "just double-click it" experience for the client
   (starts the backend if it isn't already running, then opens the kiosk
   browser -- doesn't require the systemd services above to be installed):
   ```bash
   chmod +x kiosk/launch_app.sh kiosk/led-kiosk-launcher.desktop
   cp kiosk/led-kiosk-launcher.desktop ~/Desktop/           # icon on the desktop
   mkdir -p ~/.local/share/applications
   cp kiosk/led-kiosk-launcher.desktop ~/.local/share/applications/  # and/or the app menu
   ```
   Note: the `.desktop` file intentionally has no comment lines -- PCManFM
   (Raspberry Pi OS's file manager) rejects `.desktop` files with comments
   before `[Desktop Entry]` as "Invalid desktop entry file", stricter than
   the freedesktop spec technically requires. Keep it comment-free if you
   edit it.

## Not yet built (intentionally)

- **Moving the installation manager off the kiosk.** `app/manager.py` is the
  one place orders get numbered; it runs inside the kiosk app for now (one
  kiosk, one broker). Once a server exists it moves there and
  `mqtt_client.send_order()` calls it instead. Never run two numbering
  sources against the same installation (that includes the firmware repo's
  `tools/test_installation.py`).
- **Auth.** There's no login on the API/UI. Fine on an isolated kiosk device;
  add something before exposing this beyond localhost.
- **A7600 AT-command integration.** Connectivity is handled at the OS level
  (NetworkManager/ModemManager), not from within this app. The `/api/status`
  endpoint only reports MQTT connection state right now -- extend it if you
  want the UI to show WiFi vs LTE state too.
