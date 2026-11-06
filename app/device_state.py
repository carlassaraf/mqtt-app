"""
What each installation shows, and how each of its units is doing.

Display state (per installation): the last-known value of each display
property, so the command cards can show e.g. "70%" on the brightness button
instead of a bare letter. Firmware 2.0 units no longer report these: every
unit applies the same numbered orders, so they show the same thing by
construction, and that thing follows from the orders sent (record_order()).
The starting point is version 0: since firmware 2.1.0 every unit boots
showing frame 3 at 70% with 100 ms rotation and the loop off (BOOT_STATE).
On startup the state is rebuilt by replaying the stored orders (load()).

Unit health (per installation, per (group, unit)): the retained JSON each
unit publishes on <prefix>/<inst>/<group>/<unit>/status, plus each group
master's hello and online topics. Shown in the UI's units table.

Properties never sent stay absent, and the frontend falls back to the
command's letter for them.
"""
import json
import threading
import time

from app import db
from app.config import INSTALLATIONS

# Commands whose value is a lasting display property worth showing on its
# card. CLR/NET/STA are actions, not properties, so they're never recorded.
# AUT and INV take no value but still get a 1/0 state: AUT while the loop
# runs, INV while rotation is inverted (see side_effects).
TRACKED_COMMAND_IDS = {"FRM", "BRI", "BLK", "ROT", "PPG", "PPC", "PPK", "SCR", "SCL", "OUT", "FIL", "OVR"}

# The firmware's modes, one flag each; these commands' on/off states mirror
# them. The modes are mutually exclusive: turning one on turns the rest off.
MODE_COMMAND_IDS = ("AUT", "PPG", "SCR", "FIL")

# Sending any of these pauses automatic mode until AUT is sent again.
_PAUSES_AUTOMATIC = {"FRM", "BRI", "BLK", "ROT", "INV"}

# CLR is a one-level undo (firmware ARCHITECTURE.md §4.1.2): right before a
# FRM, PPG or FIL is applied the firmware snapshots the scenario (frame,
# brightness, blink, rotation and its direction, loop and modes, not the
# colors or OUT/OVR), and CLR restores that snapshot once.
_SNAPSHOT_BEFORE = {"FRM", "PPG", "FIL"}
_SCENARIO_KEYS = ("FRM", "BRI", "BLK", "ROT", "INV", "AUT", "PPG", "SCR", "FIL")

# Version 0: what every unit shows after booting (firmware 2.1.0+).
BOOT_STATE = {"FRM": 3, "BRI": 70, "BLK": 0, "ROT": 100, "INV": 0, "AUT": 0, "PPG": 0, "SCR": 0, "FIL": 0}

_lock = threading.Lock()
_state: dict[int, dict] = {i["inst"]: dict(BOOT_STATE) for i in INSTALLATIONS}
_snapshot: dict[int, dict | None] = {}

_configured_groups = {i["inst"]: set(i["groups"]) for i in INSTALLATIONS}
_units: dict[int, dict[tuple[int, int], dict]] = {i["inst"]: {} for i in INSTALLATIONS}
_groups: dict[int, dict[int, dict]] = {i["inst"]: {} for i in INSTALLATIONS}


# ---------- display state ----------

def split_command(cmd: str) -> tuple[str, str | None]:
    """"FRM5" -> ("FRM", "5"); "AUT" -> ("AUT", None)."""
    cmd = cmd.strip().upper()
    return cmd[:3], (cmd[3:] or None)


def normalize_value(command_id: str, value):
    """Same value shapes the frontend sends: ints for numeric/toggle commands,
    uppercase hex without '#' for colors."""
    if command_id in ("PPC", "PPK", "SCL"):
        return str(value).lstrip("#").upper()
    return int(value)


def side_effects(command_id: str, value, current: dict) -> dict:
    """State changes a command causes beyond its own value. AUT (no value)
    and PPG1/SCR1/FIL1 switch to their mode and turn the other modes off;
    FRM/BRI/BLK/ROT/INV pause automatic mode; INV flips the rotation
    direction, when it's known at all."""
    if command_id == "AUT":
        value = 1
    if command_id in MODE_COMMAND_IDS and value == 1:
        return {mode: int(mode == command_id) for mode in MODE_COMMAND_IDS}
    effects = {}
    if command_id in _PAUSES_AUTOMATIC:
        effects["AUT"] = 0
    if command_id == "INV" and "INV" in current:
        effects["INV"] = 1 - current["INV"]
    return effects


def _apply(inst: int, command_id: str, value) -> bool:
    """Applies one command to the installation's state. Caller holds _lock.
    Returns True if anything changed."""
    state = _state.setdefault(inst, {})
    command_id = command_id.strip().upper()

    if command_id == "CLR":
        snapshot = _snapshot.pop(inst, None)
        if snapshot is None:
            return False  # nothing saved: the firmware rejects this CLR too
        for key in _SCENARIO_KEYS:
            if key in snapshot:
                state[key] = snapshot[key]
            else:
                state.pop(key, None)
        return True

    updates = {}
    if command_id in TRACKED_COMMAND_IDS and value is not None and value != "":
        try:
            updates[command_id] = normalize_value(command_id, value)
        except (TypeError, ValueError):
            return False
    updates.update(side_effects(command_id, updates.get(command_id), state))
    if not updates:
        return False
    if command_id in _SNAPSHOT_BEFORE:
        _snapshot[inst] = {k: state[k] for k in _SCENARIO_KEYS if k in state}
    state.update(updates)
    return True


def record_sent(inst: int, command_id: str, value) -> dict | None:
    """Returns the installation's updated state, or None if nothing changed
    (an untracked command, or one sent without a value)."""
    with _lock:
        if not _apply(inst, command_id, value):
            return None
        return dict(_state[inst])


def record_order(inst: int, cmds: list[str]) -> dict | None:
    """Applies every command of an order that went out (e.g. ["FRM5",
    "BRI70"]), in order. Returns the updated state, or None if nothing
    changed."""
    changed = False
    with _lock:
        for cmd in cmds:
            changed |= _apply(inst, *split_command(cmd))
        return dict(_state[inst]) if changed else None


def load():
    """Rebuilds each installation's display state from its stored orders.
    Call once at startup, after db.init_db()."""
    for item in INSTALLATIONS:
        inst = item["inst"]
        with _lock:
            _state[inst] = dict(BOOT_STATE)
            _snapshot.pop(inst, None)
            for order in db.list_orders(inst):
                for cmd in json.loads(order["cmds_json"]):
                    _apply(inst, *split_command(cmd))


def is_automatic(inst: int) -> bool:
    with _lock:
        return _state.get(inst, {}).get("AUT") == 1


def get_all() -> dict[int, dict]:
    with _lock:
        return {inst: dict(s) for inst, s in _state.items()}


# ---------- unit health ----------

def apply_unit_status(inst: int, group: int, unit: int, status: dict):
    """A unit's retained status JSON. Merged into what was known, so a unit
    reported as {"online": false} keeps its last readings, marked offline."""
    with _lock:
        entry = _units.setdefault(inst, {}).setdefault((group, unit), {})
        entry.update(status)
        entry["group"], entry["unit"] = group, unit
        entry["updated_at"] = time.time()


def forget_unit(inst: int, group: int, unit: int):
    """An empty retained status: someone cleared the unit from the broker."""
    with _lock:
        _units.get(inst, {}).pop((group, unit), None)


def apply_hello(inst: int, group: int, hello: dict):
    with _lock:
        _groups.setdefault(inst, {}).setdefault(group, {})["hello"] = hello


def apply_master_online(inst: int, group: int, online: bool):
    with _lock:
        entry = _groups.setdefault(inst, {}).setdefault(group, {})
        entry["online"] = online
        entry["online_at"] = time.time()


def get_groups(inst: int) -> list[int]:
    """Configured groups plus any learned from the units' topics."""
    with _lock:
        seen = set(_groups.get(inst, {})) | {g for g, _ in _units.get(inst, {})}
    return sorted(_configured_groups.get(inst, set()) | seen)


def get_units(inst: int) -> list[dict]:
    """One entry per group: master online/hello info and its units' statuses,
    sorted by unit number."""
    groups = get_groups(inst)
    with _lock:
        units = _units.get(inst, {})
        group_info = _groups.get(inst, {})
        return [
            {
                "group": g,
                "online": group_info.get(g, {}).get("online"),
                "hello": group_info.get(g, {}).get("hello"),
                "units": [dict(units[key]) for key in sorted(k for k in units if k[0] == g)],
            }
            for g in groups
        ]
