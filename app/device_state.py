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


# Lines of the firmware's STA report (see its snprintf format), mapped to the
# command whose value they report. "Rotacion" may carry "invertida" glued in
# front of the number.
_STATUS_FIELDS = [
    ("FRM", re.compile(r"Escena (\d+) cargada"), int),
    ("BRI", re.compile(r"Brillo (\d+)%"), int),
    ("BLK", re.compile(r"Blink (\d+)ms"), int),
    ("ROT", re.compile(r"Rotacion\s*(?:invertida)?\s*(\d+)ms"), int),
    ("INV", re.compile(r"Rotacion\s*(invertida)?\s*\d+ms"), lambda s: 1 if s else 0),
    ("OUT", re.compile(r"^Luminaria: (on|off)\s*$", re.MULTILINE), lambda s: 1 if s == "on" else 0),
]
_STATUS_MARKER = re.compile(r"Escena \d+ cargada")
_STATUS_MODE = re.compile(r"^Modo: (.+?)\s*$", re.MULTILINE)
# mode_label_verbose()'s output -> which mode command it means
_MODE_LABELS = {
    "automatic loop": "AUT",
    "ping-pong mode": "PPG",
    "strip-color-rotate mode": "SCR",
    "fill mode": "FIL",
    "manual (paused)": None,
}


def apply_status_report(payload: str) -> tuple[str, dict] | None:
    """If payload is a STA reply from a known device, merges its values in and
    returns (device_id, updated state); otherwise None. The reply starts with
    the device's tag, i.e. whatever comes before "Escena N cargada"."""
    marker = _STATUS_MARKER.search(payload)
    if not marker:
        return None
    prefix = payload[: marker.start()].upper()
    device_id = next(
        (dev_id for tag, dev_id in _devices_by_tag.items() if re.search(rf"\b{re.escape(tag)}\b", prefix)),
        None,
    )
    if device_id is None:
        return None

    updates = {}
    for command_id, pattern, convert in _STATUS_FIELDS:
        m = pattern.search(payload)
        if m:
            updates[command_id] = convert(m.group(1))
    mode = _STATUS_MODE.search(payload)
    if mode and mode.group(1) in _MODE_LABELS:
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
