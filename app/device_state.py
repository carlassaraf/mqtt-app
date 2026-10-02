"""
Last-known value of each device property, per device, so the command cards
can show e.g. "70%" on the brightness button instead of a bare letter.

Two sources feed it:
- record_sent(): every successful publish_command() -- from the UI, a quick
  preset or the scheduler alike -- assumes the device took the value. There's
  no ack to wait for (the device may be offline or powered off), so the value
  is trusted until a STA reply says otherwise.
- apply_status_report(): a STA reply on the shared log topic overwrites every
  property it reports.

Properties STA doesn't report (ping-pong/strip colors, FIL, ...) are only
ever known from what this app sent. Anything never sent nor reported stays
absent, and the frontend falls back to the command's letter for it.

Kept in memory only: after a restart everything is unknown until the
connect-time STA reply arrives (see mqtt_client._on_connect).
"""
import re
import threading

from app.config import DEVICES

# Commands whose value is a lasting device property worth showing on its card.
# STA/CLR/NET are actions, not properties, so they're never recorded (CLR's
# effect is picked up by the STA mqtt_client sends right after it). AUT and
# INV take no value but still get a 1/0 state: AUT while the device is in
# automatic loop mode, INV while rotation is inverted (see _side_effects).
TRACKED_COMMAND_IDS = {"FRM", "BRI", "BLK", "ROT", "PPG", "PPC", "PPK", "SCR", "SCL", "OUT", "FIL"}

# The firmware's modes, one flag each; these commands' on/off states mirror
# them. The modes are mutually exclusive: turning one on turns the rest off.
_MODE_COMMAND_IDS = ("AUT", "PPG", "SCR", "FIL")

# Sending any of these pauses automatic mode until AUT is sent again.
_PAUSES_AUTOMATIC = {"FRM", "BRI", "BLK", "ROT", "INV"}

_lock = threading.Lock()
_state: dict[str, dict] = {d["id"]: {} for d in DEVICES}


def _status_tag(device: dict) -> str | None:
    """The name a device prefixes its STA reply with. Configurable per device
    as `status_tag`; otherwise taken from the parenthesised part of its label,
    e.g. "Columna 1 (NQN)" -> "NQN"."""
    if device.get("status_tag"):
        return device["status_tag"]
    m = re.search(r"\(([^)]+)\)", device["label"])
    return m.group(1).strip() if m else None


_devices_by_tag = {tag.upper(): d["id"] for d in DEVICES if (tag := _status_tag(d))}


def _normalize(command_id: str, value):
    """Same value shapes the frontend sends: ints for numeric/toggle commands,
    uppercase hex without '#' for colors."""
    if command_id in ("PPC", "PPK", "SCL"):
        return str(value).lstrip("#").upper()
    return int(value)


def _side_effects(command_id: str, value, current: dict) -> dict:
    """State changes a command causes beyond its own value. AUT (no value)
    and PPG1/SCR1/FIL1 switch to their mode and turn the other modes off;
    FRM/BRI/BLK/ROT/INV pause automatic mode; INV flips the rotation
    direction, when it's known at all."""
    if command_id == "AUT":
        value = 1
    if command_id in _MODE_COMMAND_IDS and value == 1:
        return {mode: int(mode == command_id) for mode in _MODE_COMMAND_IDS}
    effects = {}
    if command_id in _PAUSES_AUTOMATIC:
        effects["AUT"] = 0
    if command_id == "INV" and "INV" in current:
        effects["INV"] = 1 - current["INV"]
    return effects


def record_sent(device_id: str, command_id: str, value) -> dict | None:
    """Returns the device's updated state, or None if nothing changed (an
    untracked command, or one sent without a value)."""
    command_id = command_id.strip().upper()
    updates = {}
    if command_id in TRACKED_COMMAND_IDS and value is not None and value != "":
        try:
            updates[command_id] = _normalize(command_id, value)
        except (TypeError, ValueError):
            return None
    with _lock:
        state = _state.setdefault(device_id, {})
        updates.update(_side_effects(command_id, updates.get(command_id), state))
        if not updates:
            return None
        state.update(updates)
        return dict(state)


# STA reports come in two firmware dialects, both on the shared log topic and
# both prefixed with the device tag (plus a timestamp):
#   Spanish, one value per line:  "Demo 02/10/2026 06:52:58 Escena 3 cargada\n
#     Brillo 7%\nBlink 0ms\nRotacion 100ms\nModo: manual (paused)\n
#     Corriente: 1.49A\n...Temperatura: 10.33C\nHumedad: 73.83%\nLuminaria: on..."
#   English, sentences:  "COL-02 02/10/2026 06:52:58 Frame 3 loaded, brightness
#     70%, blink 0ms, rotation 100ms, mode: manual (paused).\n...Power sense:
#     1829mV, 3435mA calculated. LED indicator: on. ...\nPing-pong colors: ball
#     #FFFFFF, background #000000. Strip colors: #FF0000/#00FF00/#0000FF.\n..."
# Each field lists one pattern per dialect (a device only ever matches its own);
# convert gets the match. Fields a dialect doesn't report (the English one has
# no temperature/humidity) just stay as they were.
def _flag(m):
    return 1 if m.group(1).lower() == "on" else 0


_HEX = r"#([0-9A-Fa-f]{6})"
_STATUS_FIELDS = [
    ("FRM", [r"Escena (\d+) cargada", r"Frame (\d+) loaded"], lambda m: int(m.group(1))),
    ("BRI", [r"Brillo (\d+)%", r"brightness (\d+)%"], lambda m: int(m.group(1))),
    ("BLK", [r"[Bb]link (\d+)ms"], lambda m: int(m.group(1))),
    # Any word between "Rotacion"/"rotation" and the number ("invertida" in
    # the Spanish one, glued to the number) means the direction is inverted.
    ("ROT", [r"(?:Rotacion|rotation)\s*([a-z]*)\s*(\d+)ms"], lambda m: int(m.group(2))),
    ("INV", [r"(?:Rotacion|rotation)\s*([a-z]*)\s*(\d+)ms"], lambda m: 1 if m.group(1) else 0),
    ("OUT", [r"^Luminaria: (on|off)", r"LED indicator: (on|off)"], _flag),
    ("PPC", [rf"ball {_HEX}"], lambda m: m.group(1).upper()),
    ("PPK", [rf"background {_HEX}"], lambda m: m.group(1).upper()),
    ("SCL", [rf"Strip colors: {_HEX}/{_HEX}/{_HEX}"], lambda m: "".join(m.groups()).upper()),
    # Sensor readings, not commands: shown on read-only indicator cards. CUR in amps.
    ("CUR", [r"Corriente: (-?\d+(?:\.\d+)?)A", r"(\d+)mA calculated"],
     lambda m: float(m.group(1)) / (1000 if m.group(0).endswith("calculated") else 1)),
    ("TMP", [r"Temperatura: (-?\d+(?:\.\d+)?)C"], lambda m: float(m.group(1))),
    ("HUM", [r"Humedad: (-?\d+(?:\.\d+)?)%"], lambda m: float(m.group(1))),
]
_STATUS_FIELDS = [
    (command_id, [re.compile(p, re.MULTILINE) for p in patterns], convert)
    for command_id, patterns, convert in _STATUS_FIELDS
]
_STATUS_MARKER = re.compile(r"Escena \d+ cargada|Frame \d+ loaded")

# mode_label_verbose()'s output (same in both dialects) -> which mode command it means
_MODE_LABELS = {
    "automatic loop": "AUT",
    "ping-pong mode": "PPG",
    "strip-color-rotate mode": "SCR",
    "fill mode": "FIL",
    "manual (paused)": None,
}
_STATUS_MODE = re.compile(r"(?:Modo|mode): (" + "|".join(re.escape(label) for label in _MODE_LABELS) + ")")

# The device's echo of every STA it receives, sent just before the report.
_STATUS_REQUEST_ECHO = re.compile(r"Received command: status request \('STA'\)")


def _device_for(prefix: str) -> str | None:
    prefix = prefix.upper()
    return next(
        (dev_id for tag, dev_id in _devices_by_tag.items() if re.search(rf"\b{re.escape(tag)}\b", prefix)),
        None,
    )


def is_full_status_report(payload: str) -> bool:
    """A full STA report also states the mode -- other device messages
    mentioning the frame (e.g. confirming a FRM) don't, and must not be
    mistaken for a STA reply and hidden from the live log."""
    return bool(_STATUS_MARKER.search(payload)) and bool(_STATUS_MODE.search(payload))


def status_request_echo_device(payload: str) -> str | None:
    """Device id if payload is a known device's "Received command: status
    request ('STA')" echo, else None."""
    m = _STATUS_REQUEST_ECHO.search(payload)
    return _device_for(payload[: m.start()]) if m else None


def apply_status_report(payload: str) -> tuple[str, dict] | None:
    """If payload reports device values (the frame line and whichever other
    STA fields it carries) from a known device, merges them in and returns
    (device_id, updated state); otherwise None. The message starts with the
    device's tag, i.e. whatever comes before the frame line."""
    marker = _STATUS_MARKER.search(payload)
    if not marker:
        return None
    device_id = _device_for(payload[: marker.start()])
    if device_id is None:
        return None

    updates = {}
    for command_id, patterns, convert in _STATUS_FIELDS:
        for pattern in patterns:
            m = pattern.search(payload)
            if m:
                updates[command_id] = convert(m)
                break
    mode = _STATUS_MODE.search(payload)
    if mode:
        active = _MODE_LABELS[mode.group(1)]
        updates.update({m: int(m == active) for m in _MODE_COMMAND_IDS})
    with _lock:
        _state.setdefault(device_id, {}).update(updates)
        return device_id, dict(_state[device_id])


def is_automatic(device_id: str) -> bool:
    with _lock:
        return _state.get(device_id, {}).get("AUT") == 1


def get_all() -> dict[str, dict]:
    with _lock:
        return {dev_id: dict(s) for dev_id, s in _state.items()}
