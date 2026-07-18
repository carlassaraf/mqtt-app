# LED kiosk controller — project context

Handoff notes for continuing this project in a fresh session (Claude Code,
VS Code, or otherwise). Drop this in the project root.

## What this is

A touchscreen kiosk app on a Raspberry Pi 4 that controls an RGB LED column
("Columna RGB") over MQTT. It abstracts the device's raw text-command
protocol into buttons/sliders/color pickers, shows a live log of a
subscribed MQTT topic, and lets commands be scheduled for a future
date/time. LoRa is a planned future transport, not yet built.

**Status: working.** Backend confirmed running on the actual Pi 4, connected
to the real broker, subscribing and receiving logs correctly. Command
sending has been confirmed against the physical device for the commands
tested so far.

## Stack & why

- **Backend**: FastAPI + paho-mqtt + APScheduler + sqlite (stdlib
  `sqlite3`, no ORM). Chosen over a pure desktop app because a touchscreen
  kiosk + this developer's UX/web background made a browser-rendered UI the
  faster path to something polished, and it costs little on a Pi 4 for a
  single always-on kiosk.
- **Frontend**: vanilla HTML/CSS/JS, no build step, no framework. Deployed
  via Chromium in `--kiosk` mode. Dark control-panel theme, teal accent,
  large touch targets (deliberately not the generic "AI design" cream/serif
  or near-black/acid-green look).
- **Kiosk lockdown**: not yet done. `kiosk/start_kiosk.sh` +
  `led-kiosk-backend.service` + `led-kiosk-browser.service` are written but
  unverified on real hardware. Needs: auto-login to a graphical session, a
  minimal window manager (e.g. `matchbox-window-manager`), and ideally a
  read-only root filesystem / overlayfs so power-cycling mid-write can't
  corrupt the SD card.

## Architecture

```
Raspberry Pi 4 (kiosk app)
  Connectivity (WiFi / LTE via A7600, OS-level, not yet configured)
  MQTT client  <-->  MQTT broker (broker.hivemq.com, public, no auth)
  Scheduler (APScheduler, sqlite-persisted, re-arms pending jobs on restart)
  Command UI (buttons/sliders/color pickers generated from a JSON profile)
       |
       v (future, not built)
     LoRa
```

`app/mqtt_client.py:build_payload()` is the single choke point that turns a
`(command_id, value)` pair into the device's wire string. Both the
immediate-send path (`routes/commands.py`) and the scheduler
(`scheduler.py`) call `publish_command()`, which calls `build_payload()`
internally — so a future LoRa transport just needs a sibling function that
reuses `build_payload()` and sends over the radio instead.

## The device protocol (Columna RGB) — from the client's own guide

Format: **3-letter command + optional value, no separators**
(`FRM5`, `BRI70`, `PPCFF0000`). **Must be uppercase exactly over MQTT**
(SMS is case-insensitive, MQTT is not). `build_payload()` force-uppercases
the whole string regardless of input casing.

| Code | Does | Value | Notes |
|---|---|---|---|
| `FRM` | Show frame N | int, 1..22 | 22 frames confirmed loaded on the device |
| `BRI` | Brightness | int 0-100 (%) | |
| `BLK` | Blink | int ms, 0 = off | |
| `ROT` | Rotation/marquee speed | int ms, 0 = off | |
| `INV` | Invert rotation direction | none | |
| `AUT` | Resume automatic mode | none | |
| `STA` | Request status report | none | |
| `NET` | Enable/disable MQTT | `0`/`1` | **`0` sendable via MQTT; `1` only via SMS** (device is offline from MQTT's view after `NET0`) — only `NET0` is in the app's profile, gated behind a confirm dialog |
| `PPG` | Ping-pong mode | `0`/`1` | |
| `PPC` | Ping-pong ball color | hex, no `#` | |
| `PPK` | Ping-pong background color | hex, no `#` | |

Sending `FRM`/`BRI`/`BLK`/`ROT`/`INV`/`PPG1` pauses automatic mode until
`AUT` is sent or the device reboots.

**MQTT connection**: `broker.hivemq.com:1883`, no auth. Command topic
`columna/led`, log topic `columna/log`. Device's own MQTT client ID is
`columna-master` — **this app must use a different client ID**
(`led-kiosk-controller` in config) or the broker will boot one of the two
connections on collision.

**Out of scope / not built, by design:**
- SMS backup channel (device's own no-internet fallback + status
  notifications to 2 authorized numbers) — independent of this app.
  Worth asking the client if they want SMS notification visibility surfaced
  in the UI too.
- `NET1` — SMS-only per the device's own design, deliberately excluded
  from the command profile.
- Any auth/access control on the broker itself — it's public, the device's
  own input validation is the real safety net.

## File structure

```
app/
  main.py             FastAPI app, startup/shutdown wiring
  config.py            loads config.json (copy from config.example.json)
  db.py                 sqlite: logs + scheduled_commands tables
  mqtt_client.py        paho-mqtt wrapper + build_payload() + websocket fan-out
  scheduler.py           APScheduler wrapper, sqlite-persisted, re-arms on restart
  network_status.py       reads active default-route interface (wifi/lte/ethernet) for /api/status
  models.py               SendCommandRequest / ScheduleRequest (command_id + single value)
  routes/
    commands.py            GET profile, POST send
    logs.py                  GET history, WS live tail (/ws/logs)
    schedule.py              CRUD scheduled commands
  static/js/app.js       all frontend logic (single-value-per-command rendering), UI is in Spanish
  static/css/style.css    dark kiosk theme
  templates/index.html
profiles/device_commands.json   <- edit this to add/adjust commands, no code changes needed (labels in Spanish)
kiosk/                            start script + 2 systemd units (untested on hardware)
  network/                         LTE failover scripts + README (untested on hardware, see gap #1 below)
config.example.json               copy to config.json, already has real broker/topics filled in
```

## Known gaps / next steps (in rough priority order)

1. **WiFi/LTE failover** for the A7600 — scripts written in
   `kiosk/network/` (`switch_a7600_qmi.sh`, `setup_lte_failover.sh`, see
   that folder's README) using NetworkManager + ModemManager in QMI mode,
   kernel-level failover via route metrics. **Not yet run against the
   physical A7600 + Pi** — review before running, especially the AT port
   and USB mode-switch command. `/api/status` now also reports
   `network_type` (wifi/lte/ethernet/unknown) and the UI shows it next to
   the MQTT status.
2. **Kiosk lockdown** — systemd units are written but unverified: need
   auto-login + minimal WM on the Pi, and ideally overlayfs/read-only root.
3. **LoRa transport** — deferred by design, see architecture note above for
   how it should slot in.
4. Decide whether SMS notification visibility belongs in the UI (open
   question for the client, not yet decided either way).

## Local dev

```bash
python3.11 -m venv venv   # 3.11, not 3.14 -- pydantic-core has no prebuilt
                            # wheel for 3.14 yet and will try to compile from
                            # source, which fails without a Rust toolchain
source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
uvicorn app.main:app --reload
```
