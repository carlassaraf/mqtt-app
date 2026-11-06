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

**Firmware 2.0: installations, numbered orders.** The app talks to
*installations* (all of their units at once), not to single devices. Every
command goes out as part of a numbered **order**:
`{"v": 12, "at": <UTC ms>, "cmds": ["FRM5", "BRI70"]}` on
`<prefix>/<inst>/cmd` (QoS 1, never retained). Masters apply an order only if
`v` is exactly one more than theirs, and every unit switches at `at`.

- `app/mqtt_client.py:build_payload()` still turns each `(command_id, value)`
  pair into the wire string. `send_order(inst, [(id, value), ...])` builds
  them and hands the list to `app/manager.py:submit()`.
- `app/manager.py` is the **only** numbering source: it validates (same
  ranges as the firmware), takes the next `v`, publishes and stores the order
  in sqlite (`orders`, `installations.next_v`), under one lock. It also moves
  the counter past any higher `v` seen in the retained scene or a unit
  status, and numbers SMS orders masters forward on `<prefix>/<inst>/cmd/in`.
  Only one process may do this per installation (it lives in the kiosk for
  now; it moves to a server once there is one).
- Units report retained JSON on `<prefix>/<inst>/<group>/<unit>/status`;
  group masters also publish `…/0/hello` and `…/0/online`. These feed the
  Unidades tab. Log lines on `<prefix>/<inst>/log` go to the live log view.
- What the installation shows (the values on the command cards) follows from
  the orders sent (`device_state.record_order()`), starting from the boot
  state (frame 3, 70%, 100 ms rotation, loop off) and rebuilt from the stored
  orders on restart. There's no status parsing any more.
- Maintenance goes to one unit on `<prefix>/<inst>/<group>/<unit>/cmd`:
  `STA` (fresh status) and `ROL` (roll call of a group, unit 0 only).

See `docs/ARCHITECTURE.md` §12 in the firmware repo for the design.

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
| `CLR` | Undo the last `FRM`/`PPG`/`FIL` (one level) | none | |
| `PPG` | Ping-pong mode | `0`/`1` | |
| `PPC` | Ping-pong ball color | hex, no `#` | |
| `PPK` | Ping-pong background color | hex, no `#` | |
| `SCR` / `SCL` | Strip-color rotation / the 3 strip colors | `0`/`1` / 18 hex digits | |
| `FIL` | Fill mode | `0`/`1` | |
| `OUT` / `OVR` | Status LED / input-gate override | `0`/`1` | |
| `STA` | Fresh status of one unit | none | Maintenance, not an order (Unidades tab) |
| `ROL` | Roll call of a whole group | none | Maintenance, group master only |
| `NET` | Enable/disable a master's data connection | `0`/`1` | Admin command, **not available in this app** (it's for operators). `NET1` only works via SMS anyway (the master is offline from MQTT's view after `NET0`) |

Sending `FRM`/`BRI`/`BLK`/`ROT`/`INV`/`PPG1` pauses automatic mode until
`AUT` is sent or the device reboots.

**MQTT connection**: `broker.hivemq.com:1883`, no auth. Topics under
`<topic_prefix>/<inst>/` (`topic_prefix` in config.json must match the
firmware's `CONFIG_APP_TOPIC_PREFIX`, default `columna`). Masters use the
client ID `columna-<inst>-<group>-<unit>` — **this app must use a different
one** (`led-kiosk-controller` in config) or the broker will boot one of the
two connections on collision.

**Out of scope / not built, by design:**
- SMS backup channel (device's own no-internet fallback + status
  notifications to 2 authorized numbers) — handled by the masters. The only
  part this app sees: a master with a data connection forwards SMS orders on
  `cmd/in`, and the manager numbers them like any other order.
  Worth asking the client if they want SMS notification visibility surfaced
  in the UI too.
- `NET` — an admin command (a master's data connection), deliberately not
  offered in this operator app; `NET1` is SMS-only anyway.
- Any auth/access control on the broker itself — it's public, the device's
  own input validation is the real safety net.

## File structure

```
app/
  main.py             FastAPI app, startup/shutdown wiring
  config.py            loads config.json (copy from config.example.json)
  db.py                 sqlite: logs, scheduled_commands, orders, installations tables
  manager.py            installation manager: validates, numbers (v), publishes and stores orders
  mqtt_client.py        paho-mqtt wrapper + build_payload() + send_order() + topic routing + websocket fan-out
  device_state.py        what each installation shows (from the orders sent) + per-unit health (from the retained statuses)
  scheduler.py           APScheduler wrapper, sqlite-persisted, re-arms on restart; each state fires as one order
  network_status.py       reads active default-route interface (wifi/lte/ethernet) for /api/status
  models.py               SendCommandRequest / ScheduleRequest (label + ordered list of commands, i.e. a "state")
  routes/
    commands.py            GET profile, POST send (one command) / order (several, applied together)
    installations.py         list/select installation, display state, units table, ROL / STA
    logs.py                  GET history, WS live tail (/ws/logs)
    schedule.py              CRUD scheduled commands
  static/js/app.js       all frontend logic (single-value-per-command rendering), UI is in Spanish
  static/css/style.css    dark kiosk theme
  templates/index.html
profiles/device_commands.json   <- edit this to add/adjust commands, no code changes needed (labels in Spanish)
kiosk/                            start script + 2 systemd units (untested on hardware)
  network/                         LTE failover scripts + README (untested on hardware, see gap #1 below)
  screen-button/                   GPIO button daemon that blanks/wakes the HDMI display via X11 DPMS, README + systemd unit
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
2. **Kiosk lockdown** — **confirmed on hardware**: this device runs
   Wayland with the `labwc` compositor (Raspberry Pi OS on Debian 13
   trixie), not X11/matchbox as earlier docs in this repo assumed. It's
   also not confirmed that Chromium is actually launched via
   `kiosk/start_kiosk.sh`/`led-kiosk-browser.service` on the live device —
   the running process has flags those scripts don't set, suggesting
   Raspberry Pi OS's own built-in kiosk-mode boot option may be what's
   actually in use. Worth confirming with the client before relying on
   this repo's own browser-launch scripts, and ideally revisiting
   `start_kiosk.sh` to match whatever's actually deployed. Read-only root /
   overlayfs still not done either.
3. **Screen on/off button** (`kiosk/screen-button/`) — **confirmed working
   on hardware**: a `gpiozero` daemon on GPIO17 toggles the HDMI output
   (`HDMI-A-1`) via `wlopm --off`/`--on` (the DPMS-equivalent
   output-power-management protocol) on each press; touchscreen (USB,
   separate from the display) stays live throughout. Requires
   `sudo apt install wlopm` on-device. **Important pitfall already hit and
   documented in the folder's README**: `wlr-randr --off`/`--on` looks like
   the obvious tool but uses a different protocol that fully disables the
   output — re-enabling it that way failed outright and left the display
   stuck off until a reboot; don't use it for this. **Still unconfirmed**:
   whether touch/mouse input wakes a display blanked via `wlopm` the way
   X11's DPMS guaranteed (see the folder's README for the fallback if not).
4. **Minimize button** (topbar, `app/routes/system.py` `minimize_browser()`)
   — **confirmed working on hardware**: `wlrctl toplevel minimize chromium`
   hides the window, `wlrctl toplevel focus chromium` (used in
   `kiosk/launch_app.sh`'s restore path) un-hides and refocuses it.
   Requires `sudo apt install wlrctl` on-device — not yet added to any
   automated setup step/README, only confirmed manually so far.
5. **LoRa transport** — deferred by design, see architecture note above for
   how it should slot in.
6. Decide whether SMS notification visibility belongs in the UI (open
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
