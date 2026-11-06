"""
Installation manager: the single numbering source for orders.

Firmware 2.0 masters execute an order only if its version `v` is exactly one
more than the one they have, so versions must run 1, 2, 3... per
installation with no gaps and no repeats. Every order -- from the UI, the
scheduler or an SMS forwarded on <prefix>/<inst>/cmd/in -- goes through
submit(), which validates it, takes the next version, publishes it on
<prefix>/<inst>/cmd and stores it, all under one lock.

Only one process may do this per installation (two would hand out the same
`v` twice). For now that's this kiosk app; when a cloud server exists, this
module moves there and the kiosk calls it instead.

The counter lives in sqlite (installations.next_v) and is also pushed forward
by every version the units report (retained scene and statuses, see
observe_version()), since masters without a data connection number SMS
orders themselves and may have moved ahead.

Publishing goes through a function mqtt_client hands in (set_publisher()),
so this module doesn't depend on paho or on the reconnect-and-retry logic.
"""
import json
import logging
import re
import threading
import time
from typing import Callable

from app import db
from app.config import INSTALLATIONS, PROFILE_PATH, TOPIC_PREFIX

logger = logging.getLogger("manager")

# How far in the future an order takes effect: enough for MQTT to reach every
# master and for each master to relay it over LoRa. Every unit switches at `at`.
LEAD_MS = 3000

# The firmware applies at most this many commands as one order.
MAX_COMMANDS_PER_ORDER = 8

# Retained scene/status messages arrive right after subscribing and may carry
# a higher version than the stored counter. Orders submitted within this long
# of (re)subscribing wait for them, so they aren't numbered from a stale value
# and silently ignored by the masters.
RETAINED_SETTLE_S = 2.0

Publisher = Callable[[str, str], tuple[bool, str]]

_lock = threading.Lock()
_next_v: dict[int, int] = {}
_publisher: Publisher | None = None
_ready_at = 0.0  # monotonic time after which retained versions have had time to arrive

_known_insts = {i["inst"] for i in INSTALLATIONS}


def _frame_count() -> int:
    """FRM's upper bound: the frame count in the command profile's FRM entry."""
    try:
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
        frm = next(c for c in profile["commands"] if c["id"] == "FRM")
        return int(frm["max"])
    except (OSError, ValueError, KeyError, StopIteration):
        return 22


# Same rules as the firmware (ARCHITECTURE.md §3.5 / §4.2). Integers must be
# plain base-10 digits; the firmware rejects partial or empty payloads too.
_INT_RANGES = {
    "FRM": (1, _frame_count()),
    "BRI": (0, 100),
    "BLK": (0, 3_600_000),
    "ROT": (0, 3_600_000),
}
_FLAG_COMMANDS = {"PPG", "SCR", "FIL", "OUT", "OVR"}
_NO_VALUE_COMMANDS = {"INV", "AUT", "CLR"}
_HEX_LENGTHS = {"PPC": 6, "PPK": 6, "SCL": 18}
_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"[0-9A-F]+")


def validate_command(cmd: str) -> str | None:
    """Returns None if `cmd` (e.g. "FRM5") is a valid display command for an
    order, else a user-facing reason in Spanish."""
    code, value = cmd[:3], cmd[3:]
    if code in _INT_RANGES:
        lo, hi = _INT_RANGES[code]
        if not _DIGITS.fullmatch(value) or not lo <= int(value) <= hi:
            return f"{code}: el valor debe ser un número entre {lo} y {hi}."
        return None
    if code in _FLAG_COMMANDS:
        return None if value in ("0", "1") else f"{code}: el valor debe ser 0 o 1."
    if code in _NO_VALUE_COMMANDS:
        return None if value == "" else f"{code} no lleva valor."
    if code in _HEX_LENGTHS:
        n = _HEX_LENGTHS[code]
        if len(value) != n or not _HEX.fullmatch(value):
            return f"{code}: el valor debe tener {n} dígitos hexadecimales."
        return None
    return f"{cmd or '(vacío)'}: comando desconocido o no permitido en una orden."


def validate_order(cmds: list[str]) -> str | None:
    """None if `cmds` (uppercase command strings) make a valid order, else a
    user-facing reason in Spanish."""
    if not cmds:
        return "La orden no tiene comandos."
    if len(cmds) > MAX_COMMANDS_PER_ORDER:
        return f"Una orden admite hasta {MAX_COMMANDS_PER_ORDER} comandos."
    for cmd in cmds:
        error = validate_command(cmd)
        if error:
            return error
    return None


def set_publisher(publisher: Publisher):
    """publisher(topic, payload) -> (ok, error): publishes QoS 1, never
    retained. Set once by mqtt_client at startup."""
    global _publisher
    _publisher = publisher


def load():
    """Call once at startup, after db.init_db(): registers the configured
    installations and loads their counters."""
    for item in INSTALLATIONS:
        db.ensure_installation(item["inst"], item["label"])
    with _lock:
        _next_v.update(db.get_next_versions())
    logger.info("Order counters: %s", {inst: v - 1 for inst, v in _next_v.items()})


def mark_subscribed():
    """Call right after (re)subscribing to the installations' topics."""
    global _ready_at
    _ready_at = time.monotonic() + RETAINED_SETTLE_S


def observe_version(inst: int, v) -> bool:
    """A version some unit reports (retained scene, unit status). Moves the
    counter past it if it's ahead. Returns True if the counter moved."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return False
    with _lock:
        if inst not in _next_v or v + 1 <= _next_v[inst]:
            return False
        _next_v[inst] = v + 1
    db.set_next_version(inst, v + 1)
    logger.info("Installation %s is at version %s; continuing from there", inst, v)
    return True


def latest_version(inst: int) -> int:
    """The last version handed out or seen for this installation (0 = none)."""
    with _lock:
        return _next_v.get(inst, 1) - 1


def submit(inst: int, cmds: list[str], source: str = "ui") -> tuple[bool, int | str]:
    """Validates, numbers, publishes and stores one order.

    Returns (True, v) on success, or (False, error) with a user-facing error
    in Spanish. Nothing is numbered unless the publish went out, so a failed
    attempt leaves no gap.
    """
    if inst not in _known_insts:
        return False, f"Instalación {inst} desconocida."
    cmds = [str(c).strip().upper() for c in cmds]
    error = validate_order(cmds)
    if error:
        return False, error
    if _publisher is None:
        return False, "No hay conexión con el broker MQTT."

    wait = _ready_at - time.monotonic()
    if wait > 0:
        time.sleep(wait)

    with _lock:
        v = _next_v.setdefault(inst, 1)
        at_ms = int(time.time() * 1000) + LEAD_MS
        payload = json.dumps({"v": v, "at": at_ms, "cmds": cmds}, separators=(",", ":"))
        ok, error = _publisher(f"{TOPIC_PREFIX}/{inst}/cmd", payload)
        if not ok:
            return False, error
        _next_v[inst] = v + 1
        db.insert_order(inst, v, at_ms, json.dumps(cmds), source)
    logger.info("Order %s/v%s (%s): %s", inst, v, source, " ".join(cmds))
    return True, v
