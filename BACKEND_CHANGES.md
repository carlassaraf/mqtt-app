# Backend changes for firmware 2.0 (installations)

What [`mqtt-app`](https://github.com/carlassaraf/mqtt-app) has to change to
control firmware 2.0. The design behind it is in
[`ARCHITECTURE.md` §12](ARCHITECTURE.md#12-planned-lora-installations-masterslave);
this document only covers the backend side.

**The short version:** the backend stops talking to devices and starts talking
to **installations**. It publishes **numbered orders** (lists of the same
3-letter commands as today) to one topic per installation. Every unit answers
with **JSON status** on its own retained topic. The text `STA` report and its
regex parsing go away.

## 1. What changes on the wire

| | Firmware 1.x (today) | Firmware 2.0 |
|---|---|---|
| Who you address | A device (`columna/led`, `columna/demo`, …) | An installation (`<prefix>/<inst>/cmd`), all of its units at once |
| Command payload | `FRM5` | `{"v": 12, "at": 1767225600000, "cmds": ["FRM5", "BRI70"]}` |
| Several commands | One publish each, 0.3 s apart | One order with a list; applied together, atomically |
| Status | `STA` → free text on `columna/log`, parsed with regex | Retained JSON per unit on `<prefix>/<inst>/<group>/<unit>/status` |
| Device list | Hand-written in `config.json` | Learned from retained `hello` / `status` topics |
| MQTT client IDs used by devices | `CONFIG_DEVICE_ID` (`COL-02`, …) | `columna-<inst>-<group>-<unit>` |

The 3-letter commands themselves (`FRM`, `BRI`, `BLK`, `ROT`, `INV`, `AUT`,
`CLR`, `PPG`, `PPC`, `PPK`, `SCR`, `SCL`, `FIL`, `OUT`, `OVR`) keep their
meaning and validation rules. `build_payload()` stays exactly as it is.

## 2. Topics

`<prefix>` is the firmware's `CONFIG_APP_TOPIC_PREFIX` (default `columna`);
the backend must use the same value. `<inst>`, `<group>`, `<unit>` are the
numbers printed on each unit's sticker (unit 0 is the group's master).

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `<p>/<inst>/cmd` | backend → masters | **never** | Numbered order (§3). **Only the installation manager publishes here.** |
| `<p>/<inst>/cmd/in` | masters → backend | no | Raw order to be numbered: `{"cmds":["FRM5"],"src":"sms","from":"1/G01/U00"}` — a master forwards SMS orders here. |
| `<p>/<inst>/scene` | masters → everyone | yes | `{"v":12,"at":…,"scene":"<hex>"}`: the current display state. The backend only reads `v` from it. |
| `<p>/<inst>/<group>/<unit>/status` | masters → backend | yes | Unit status, JSON (§4). |
| `<p>/<inst>/<group>/0/hello` | master → backend | yes | `{"inst","group","hwid","imei","fw","leds","frames_version","frames_crc","lat","lon","lora_freq_hz"}` on every connect. `lat`/`lon` are `null` without a GPS fix. |
| `<p>/<inst>/<group>/0/online` | master → backend | yes | `"1"` while connected; the broker publishes `"0"` (Last Will) when the master disappears. |
| `<p>/<inst>/<group>/<unit>/cmd` | backend → master | no | Maintenance for one unit: `STA` (fresh status), `ROL` (roll call of the whole group), `NET0`/`NET1` (unit 0 only: the master's data connection). |
| `<p>/<inst>/log` | masters → backend | no | Human-readable log lines, prefixed `1/G01/U00 dd/mm/yyyy hh:mm:ss`. Keep showing these in the live log view. |

Subscribe to `<p>/<inst>/#` per installation (or `<p>/+/#` for all) and
route by topic.

## 3. Orders and numbering (the important part)

```json
{"v": 12, "at": 1767225600000, "cmds": ["FRM5", "BRI70"]}
```

- `v`: the order's **version**, `1, 2, 3, …` per installation, **no gaps, no
  repeats**. Masters execute an order only if `v` is exactly one more than
  what they have. Lower or equal versions are ignored (already applied). A
  higher one is logged as a gap.
- `at`: when it takes effect, **UTC milliseconds**, normally `now + 3000`.
  Every unit switches at that instant. The Pi's clock must be NTP-synced
  (Raspberry Pi OS does this by default with network access).
- `cmds`: up to 8 commands, applied together as one version.
- Publish with **QoS 1, retain = false**. Masters use a persistent session, so
  orders sent while a master is briefly offline are delivered when it
  reconnects.

### Rules the backend must keep

1. **One numbering source per installation.** Only one process (the
   installation manager) publishes to `<p>/<inst>/cmd`. If two kiosks numbered
   orders independently, they would hand out the same `v` twice.
2. **Persist the counter** (sqlite). On startup, and whenever it sees one, take
   `max(stored, v in the retained scene, highest "v" in any unit status)` and
   continue from there. Masters that numbered SMS orders locally (no data
   connection) may have moved ahead.
3. **Never retain** an order. Masters ignore retained messages on the command
   topic anyway, but a retained order would be replayed to every reconnecting
   client.
4. **Validate before numbering**, with the same rules as the firmware
   (ranges in `ARCHITECTURE.md` §3.5 / §4.2: `FRM` 1..frame count, `BRI` 0..100,
   `BLK`/`ROT` 0..3 600 000, colors 6 hex digits, `SCL` 18 hex digits,
   `PPG`/`SCR`/`FIL`/`OUT`/`OVR` 0 or 1). An invalid command inside an order is
   skipped by every unit identically. The version still advances, but it's
   better not to send one.
5. **Raw orders on `cmd/in`** (from SMS) go through the same path: validate,
   number, publish on `cmd`.

## 4. Unit status JSON

Every unit (the master itself, and each slave via the master) publishes,
retained, every 5 minutes and on request:

```json
{"inst":1,"group":1,"unit":3,"role":"slave","online":true,
 "hwid":"A0B1C2D3E4F5","fw":"2.0.0","v":12,
 "frames":{"version":1,"crc":"e1ed0b07","pending":0},
 "power_mv":1820,"power_ma":3200,"temp_c":31.5,"aht_temp_c":27.2,"aht_hum":61.0,
 "out":1,"gate":1,"ovr":0,"synced":1,
 "rssi_from_master":-62,"snr_from_master":9,"rssi_at_master":-64,"snr_at_master":8,
 "uptime_s":86400,"clock_corr_ms":1,"last_error":""}
```

A unit that stops answering roll calls is published as
`{"inst":1,"group":1,"unit":3,"role":"slave","online":false}`.

| Field | Use in the UI |
|---|---|
| `online` | Unit health (with the master's `online` topic for unit 0) |
| `v` | Has this unit applied the latest order? (`v` < latest = lagging) |
| `synced` | Slave's clock is aligned to its master (should be 1) |
| `frames.crc` | Must be identical across the installation, otherwise `FRM5` shows different images |
| `rssi_*`, `snr_*` | Radio link quality; below about −110 dBm is worth a look |
| `power_ma`, `temp_c`, `aht_*` | Same sensor values the text report had |
| `out`, `gate`, `ovr` | Status LED commanded state, luminaria input gate, override |
| `last_error` | Latest rejected command or fault on that unit |

The display settings (frame, brightness, mode…) are no longer in the status:
they're the same on every unit by construction. The backend already knows
them from the orders it sent (`device_state.record_sent()` logic). Its
starting point is version 0: since firmware 2.1.0 every unit boots showing
**frame 3, brightness 70, rotation 100 ms, automatic loop off** (as if `FRM3`
had been sent); the loop only runs after an `AUT`. So the UI's initial state
is "manual, frame 3", not "automatic". After an
SMS-originated order, the master's log line on `<p>/<inst>/log` says which
order was applied.

## 5. Changes file by file

### New: installation manager (`app/manager.py` + a table)

- sqlite table `orders(inst, v, at_ms, cmds_json, source, created_at)`, with
  `PRIMARY KEY (inst, v)`, plus `installations(inst, label, next_v)`.
- `submit(inst, cmds, source="ui") -> (ok, v | error)`: validate every
  command, take `next_v`, publish `{"v","at","cmds"}` on `<p>/<inst>/cmd` QoS 1,
  store it, increment `next_v`. Must be thread-safe: a lock around
  "take `v` + publish + store".
- Subscribe `<p>/+/cmd/in` → `submit(inst, cmds, source="sms")`.
- On every status/scene message: `next_v = max(next_v, v + 1)`.
- Runs **once** for the whole system. For this test it can live inside the
  kiosk app (one kiosk, one broker). When a cloud server exists (§12.4 of the
  architecture), it moves there and the kiosk calls it instead.

### `app/config.py` / `config.example.json`

Replace `devices` (one `command_topic` per device) with installations:

```json
"topic_prefix": "columna",
"installations": [
  {"inst": 1, "label": "Prueba (5 unidades)", "groups": [1]}
]
```

Keep reading the old shape for a while if old devices still exist; firmware
2.0 units ignore `columna/led`.

### `app/mqtt_client.py`

- `publish_command(command_id, value, device_id)` → `send_order(inst, [(command_id, value), …])`:
  builds each string with `build_payload()` (unchanged) and calls
  `manager.submit()`. The reconnect-and-retry logic stays around the
  publish.
- `_on_connect`: subscribe `<p>/<inst>/#` for every configured installation
  instead of only `log_topic`.
- `_on_message`: route by topic. `…/status` → `device_state.apply_unit_status()`,
  `…/hello` / `…/online` → installation health, `…/log` → the existing log
  pipeline (`insert_log`, `log_message`, websocket), `…/scene` → `manager`
  (version only).
- `request_status_all()`: publish `ROL` to `<p>/<inst>/<group>/0/cmd` for each
  group instead of `STA` on the command topic. Statuses come back as JSON and
  don't flood the live log, so `_manual_status_until` and
  `status_request_echo_device()` can go.
- The `CLR` → delayed `STA` dance (`POST_CLR_STATUS_DELAY_S`) isn't needed: the
  unit statuses report `v`, and the display state follows from the orders.

### `app/device_state.py`

- Drop the two-dialect regex parser (`_STATUS_FIELDS`, `_STATUS_MARKER`,
  `apply_status_report()`, `_status_tag()`).
- Keep `record_sent()` / `_side_effects()`, now keyed by **installation**: it
  tracks what the installation shows, from the orders sent.
- Add `apply_unit_status(inst, group, unit, json)`: per-unit health (online,
  `v`, synced, signal, sensors, last error) for a units table in the UI.

### `app/scheduler.py`

- `_run_job` sends the scheduled state as **one** order
  (`send_order(inst, commands)`) instead of a loop with `time.sleep(0.3)`.
  Remove `INTER_COMMAND_DELAY_S`: with orders, the commands arrive together
  and are applied together.
- `request_status_automatic()` (every 2 minutes in automatic mode) isn't
  needed: the loop's frame changes are computed identically on every unit,
  and the frame shown isn't part of the status any more.
- `MISSED_SCHEDULE_CATCHUP_S` logic stays as is.

### `app/routes/commands.py`, `app/routes/devices.py`, frontend

- "Active device" becomes "active installation" (`/api/devices` →
  `/api/installations`).
- `STA` button → "refresh units" (`ROL`), plus a per-unit `STA` in a units
  table.
- New view: the units table, one row per `(group, unit)` with online, `v`
  ("up to date" / "behind"), signal, temperature and last error, from the
  retained statuses.
- `NET0` moves from the command profile to a maintenance action per group
  master (`<p>/<inst>/<group>/0/cmd`, payload `NET0`), still behind the
  confirmation dialog.

### `profiles/device_commands.json`

No change to the command definitions. Remove `STA` and `NET` from the
command buttons (they become actions, see above).

## 6. What stays the same

- The broker (public HiveMQ while testing; plan in `ARCHITECTURE.md` §12.4.2).
- `build_payload()` and every command's meaning and value format.
- The kiosk, scheduler, sqlite log store and websocket fan-out.

## 7. Testing without the UI

`tools/test_installation.py` in the firmware repo does what the manager will
do (numbering from the retained scene, `at` = now + 3 s, roll calls, checking
every unit's `v`). It's a working reference for the message formats:

```bash
python3 tools/test_installation.py --inst 1 --grupo 1 --unidades 5
```

Don't run it and the kiosk's manager against the same installation at the
same time: both would number orders.
