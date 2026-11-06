"""
Firmware 1.x devices ("legacy"), kept working until they're replaced: the
NQN column. Listed under `legacy_devices` in config.json.

They predate installations and numbered orders:
- Each one has its own command topic and takes one plain command per message
  (FRM5, BRI70...). Messages to one device go out at least
  INTER_COMMAND_DELAY_S apart: the firmware corrupts/misattributes payloads
  that arrive faster than its ~100 ms dispatch tick drains them.
- They report on a log topic (shared by every 1.x device) in free text. A STA
  reply starts with the device's tag (status_tag, e.g. "COL-02") and is
  parsed into the same display state the command cards show, plus the sensor
  readings (TMP, HUM, CUR).

Display state is assumed from every command sent (there's no ack) and
overwritten by every STA reply. It's kept in memory only: after a restart it's
unknown until the connect-time STA reply arrives. STAs this app sends on its
own (connect, device switch, hourly, every 2 minutes in automatic mode, after
CLR) are kept out of the live log; one asked for by hand shows up there.

Publishing and pushing to the UI go through functions mqtt_client hands in
(set_io()), so this module doesn't depend on paho.
"""
import logging
import re
import threading
import time
from typing import Callable

from app import manager
from app.config import LEGACY_DEVICES
from app.device_state import MODE_COMMAND_IDS, TRACKED_COMMAND_IDS, normalize_value, side_effects, split_command

logger = logging.getLogger("legacy")

INTER_COMMAND_DELAY_S = 0.3

# How long after a manual STA its reply still counts as asked for by hand.
MANUAL_STATUS_REPLY_WINDOW_S = 30

_devices = {d["id"]: d for d in LEGACY_DEVICES}
LOG_TOPICS = sorted({d["log_topic"] for d in LEGACY_DEVICES})

Publisher = Callable[[str, str], tuple[bool, str]]
_publish: Publisher | None = None
_broadcast: Callable[[dict], None] | None = None

# One sender per device at a time, so the gap holds across requests too
# (a quick preset while a schedule fires, back-to-back catch-up schedules).
_send_locks = {device_id: threading.Lock() for device_id in _devices}
_last_sent: dict[str, float] = {}  # device id -> monotonic time of its last publish

_lock = threading.Lock()
_state: dict[str, dict] = {device_id: {} for device_id in _devices}
_manual_status_until: dict[str, float] = {}


def set_io(publisher: Publisher, broadcaster: Callable[[dict], None]):
    """publisher(topic, payload) -> (ok, error); broadcaster(dict) pushes to
    the UI's websocket. Set once by mqtt_client at startup."""
    global _publish, _broadcast
    _publish, _broadcast = publisher, broadcaster


def get_devices() -> list[dict]:
    return [{"id": d["id"], "label": d["label"]} for d in LEGACY_DEVICES]


def is_known(device_id: str) -> bool:
    return device_id in _devices


def get_all() -> dict[str, dict]:
    with _lock:
        return {device_id: dict(s) for device_id, s in _state.items()}


def is_automatic(device_id: str) -> bool:
    with _lock:
        return _state.get(device_id, {}).get("AUT") == 1


def _broadcast_state(device_id: str, state: dict):
    if _broadcast is not None:
        _broadcast({"type": "legacy_state", "id": device_id, "state": state})


def _record_sent(device_id: str, cmd: str):
    command_id, value = split_command(cmd)
    updates = {}
    if command_id in TRACKED_COMMAND_IDS and value is not None:
        try:
            updates[command_id] = normalize_value(command_id, value)
        except (TypeError, ValueError):
            return
    with _lock:
        state = _state.setdefault(device_id, {})
        updates.update(side_effects(command_id, updates.get(command_id), state))
        if not updates:
            return
        state.update(updates)
        snapshot = dict(state)
    _broadcast_state(device_id, snapshot)


# ---------- sending ----------

def _publish_one(device_id: str, cmd: str) -> tuple[bool, str]:
    """Caller holds _send_locks[device_id]."""
    if _publish is None:
        return False, "No hay conexión con el broker MQTT."
    wait = _last_sent.get(device_id, 0) + INTER_COMMAND_DELAY_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    result = _publish(_devices[device_id]["command_topic"], cmd)
    _last_sent[device_id] = time.monotonic()
    return result


def send(device_id: str, cmds: list[str]) -> tuple[bool, str]:
    """Sends uppercase command strings (e.g. ["AUT", "FRM5"]) to one device,
    in order and paced. Stops at the first one that doesn't go out. Returns
    (ok, error) with a user-facing error in Spanish."""
    if device_id not in _devices:
        return False, f"Dispositivo {device_id} desconocido."
    cmds = [str(c).strip().upper() for c in cmds]
    error = manager.validate_order(cmds)
    if error:
        return False, error
    with _send_locks[device_id]:
        for cmd in cmds:
            ok, error = _publish_one(device_id, cmd)
            if not ok:
                return False, error
            _record_sent(device_id, cmd)
        # CLR undoes the last frame/mode change, leaving the device in a
        # state this app can't predict -- ask for it instead of guessing.
        if "CLR" in cmds:
            _publish_one(device_id, "STA")
    logger.info("Sent to %s: %s", device_id, " ".join(cmds))
    return True, ""


def request_status(device_id: str, manual: bool = False) -> tuple[bool, str]:
    """STA to one device. A manual one's reply shows in the live log."""
    if device_id not in _devices:
        return False, f"Dispositivo {device_id} desconocido."
    if manual:
        _manual_status_until[device_id] = time.monotonic() + MANUAL_STATUS_REPLY_WINDOW_S
    with _send_locks[device_id]:
        return _publish_one(device_id, "STA")


def request_status_all():
    """STA to every legacy device. Fire-and-forget: the replies update the
    state whenever -- if ever -- they arrive."""
    for device_id in _devices:
        request_status(device_id)


def request_status_automatic():
    """STA to every legacy device in automatic mode, whose frame changes on
    its own every ~2 minutes -- keeps the frame card roughly in step."""
    for device_id in _devices:
        if is_automatic(device_id):
            request_status(device_id)


# ---------- STA replies on the log topic ----------
# Two firmware dialects, both prefixed with the device tag plus a timestamp:
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

# The mode line (same in both dialects) -> which mode command it means
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


def _status_tag(device: dict) -> str | None:
    """The name a device starts its STA reply with: `status_tag`, or else the
    parenthesised part of its label, e.g. "Columna 1 (NQN)" -> "NQN"."""
    if device.get("status_tag"):
        return device["status_tag"]
    m = re.search(r"\(([^)]+)\)", device["label"])
    return m.group(1).strip() if m else None


_devices_by_tag = {tag.upper(): d["id"] for d in LEGACY_DEVICES if (tag := _status_tag(d))}


def _device_for(prefix: str) -> str | None:
    prefix = prefix.upper()
    return next(
        (device_id for tag, device_id in _devices_by_tag.items() if re.search(rf"\b{re.escape(tag)}\b", prefix)),
        None,
    )


def _apply_status_report(payload: str) -> str | None:
    """If payload is a known device's report (the frame line and whichever
    other STA fields it carries), merges its values in and returns the device
    id; otherwise None. The tag is whatever comes before the frame line."""
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
        updates.update({m: int(m == active) for m in MODE_COMMAND_IDS})
    with _lock:
        _state.setdefault(device_id, {}).update(updates)
        snapshot = dict(_state[device_id])
    _broadcast_state(device_id, snapshot)
    return device_id


def handle_log(payload: str) -> bool:
    """A message on a legacy log topic. Updates the state from STA replies.
    Returns whether it belongs in the live log view: automatic STA replies
    (and the device's echo of the STA) are kept out of it."""
    device_id = _apply_status_report(payload)
    # A full report also states the mode -- other messages mentioning the
    # frame (e.g. confirming a FRM) don't, and must stay visible.
    if device_id and _STATUS_MODE.search(payload):
        return time.monotonic() <= _manual_status_until.pop(device_id, 0)
    echo = _STATUS_REQUEST_ECHO.search(payload)
    if echo:
        echo_device = _device_for(payload[: echo.start()])
        # The window stays open here: it's closed by the report itself.
        if echo_device and time.monotonic() > _manual_status_until.get(echo_device, 0):
            return False
    return True
