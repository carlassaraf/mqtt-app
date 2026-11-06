"""
Wraps paho-mqtt in a small manager:
- connects on startup, auto-reconnects (paho handles backoff)
- subscribes to <prefix>/<inst>/# for every configured installation and
  routes each message by topic: log lines to sqlite + the log file + the
  live log view, unit statuses / hello / online to device_state, versions
  to the installation manager, SMS orders from cmd/in to the manager
- exposes send_order() for the UI / scheduler, which builds each command
  string with build_payload() and hands the order to manager.submit()

paho's callbacks run on paho's own network thread, not the asyncio event
loop, so we hop into the loop with call_soon_threadsafe when we need to
push a message to websocket subscribers.
"""
import asyncio
import json
import logging
import threading

import paho.mqtt.client as mqtt

from app import device_state, manager
from app.config import INSTALLATIONS, MQTT_CFG, TOPIC_PREFIX
from app.db import insert_log
from app.message_log import log_message

logger = logging.getLogger("mqtt")

_ws_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_client: mqtt.Client | None = None
_connected = False

# Which installation the UI's commands go to -- see set_active_installation().
# Not persisted across restarts on purpose (this is a single kiosk, not a
# fleet), so it always starts on the first configured installation.
_installations_by_inst = {i["inst"]: i for i in INSTALLATIONS}
_active_inst = INSTALLATIONS[0]["inst"]

# Maintenance commands for one unit, on <prefix>/<inst>/<group>/<unit>/cmd.
# STA works for any unit; ROL (roll call of the whole group) and NET (the
# master's data connection) only for the group's master, unit 0.
_UNIT_COMMANDS = {"STA"}
_MASTER_COMMANDS = {"ROL", "NET0", "NET1"}

# Statuses arrive in bursts (every retained one right after subscribing, a
# whole group after a roll call), so the units table is pushed to the UI at
# most this often per installation instead of once per message.
UNITS_BROADCAST_DEBOUNCE_S = 0.5
_units_broadcast_pending: set[int] = set()
_units_broadcast_lock = threading.Lock()


def get_installations() -> list[dict]:
    return [{"inst": i["inst"], "label": i["label"], "groups": i["groups"]} for i in INSTALLATIONS]


def get_active_inst() -> int:
    return _active_inst


def set_active_installation(inst: int) -> bool:
    """Switches which installation the UI's orders go to from here on.
    Returns False for an unknown installation."""
    global _active_inst
    if inst not in _installations_by_inst:
        return False
    _active_inst = inst
    return True


def register_ws_queue() -> asyncio.Queue:
    q = asyncio.Queue()
    _ws_subscribers.add(q)
    return q


def unregister_ws_queue(q: asyncio.Queue):
    _ws_subscribers.discard(q)


def is_connected() -> bool:
    return _connected


def _broadcast(payload: dict):
    if _loop is None:
        return
    for q in list(_ws_subscribers):
        _loop.call_soon_threadsafe(q.put_nowait, payload)


def broadcast_event(payload: dict):
    """Public entry point onto the same /ws/logs fan-out used for MQTT
    messages, for other modules (e.g. scheduler.py firing from its own
    thread) that have no MQTT message of their own to piggyback on. Callers
    should include a "type" field so the frontend can tell it apart from a
    plain {topic, payload} log line."""
    _broadcast(payload)


def _broadcast_installation_state(inst: int, state: dict):
    _broadcast({"type": "installation_state", "inst": inst, "state": state})


def units_snapshot(inst: int) -> dict:
    """The units table for one installation, plus the latest order version so
    the UI can tell which units are behind."""
    return {"inst": inst, "latest_v": manager.latest_version(inst), "groups": device_state.get_units(inst)}


def _schedule_units_broadcast(inst: int):
    with _units_broadcast_lock:
        if inst in _units_broadcast_pending:
            return
        _units_broadcast_pending.add(inst)

    def flush():
        with _units_broadcast_lock:
            _units_broadcast_pending.discard(inst)
        _broadcast({"type": "units", **units_snapshot(inst)})

    timer = threading.Timer(UNITS_BROADCAST_DEBOUNCE_S, flush)
    timer.daemon = True
    timer.start()


# ---------- incoming messages ----------

def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = reason_code == 0
    if _connected:
        topics = [(f"{TOPIC_PREFIX}/{inst}/#", MQTT_CFG.get("qos", 1)) for inst in _installations_by_inst]
        logger.info("MQTT connected, subscribing to %s", ", ".join(t for t, _ in topics))
        client.subscribe(topics)
        manager.mark_subscribed()
        # Retained statuses arrive on their own right after subscribing; a
        # roll call on top makes them fresh. On its own thread: publishing's
        # failure path reconnects, which mustn't happen inside paho's callback.
        threading.Thread(target=request_status_all, daemon=True).start()
    else:
        logger.error("MQTT connect failed: %s", reason_code)


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = False
    logger.warning("MQTT disconnected: %s", reason_code)


def _parse_topic(topic: str) -> tuple[int, list[str]] | None:
    """"columna/1/2/3/status" -> (1, ["2", "3", "status"]); None for topics
    outside <prefix>/<inst>/."""
    prefix = f"{TOPIC_PREFIX}/"
    if not topic.startswith(prefix):
        return None
    inst, _, rest = topic[len(prefix):].partition("/")
    if not inst.isdigit() or int(inst) not in _installations_by_inst:
        return None
    return int(inst), rest.split("/")


def _parse_json(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _on_message(client, userdata, msg):
    try:
        _route(msg.topic, msg.payload.decode(errors="replace"), msg.retain)
    except Exception:
        # Device data is untrusted: a malformed message must never take
        # down paho's network thread.
        logger.exception("Failed to handle message on %s", msg.topic)


def _route(topic: str, payload: str, retained: bool):
    parsed = _parse_topic(topic)
    if parsed is None:
        return
    inst, rest = parsed

    if rest == ["log"]:
        insert_log(topic, payload)
        log_message(topic, payload)
        _broadcast({"topic": topic, "payload": payload})
        return

    if rest == ["cmd"]:
        return  # our own orders coming back

    log_message(topic, payload)

    if rest == ["cmd", "in"]:
        if retained:
            return  # a stale raw order must never be numbered again
        data = _parse_json(payload)
        if data is not None:
            threading.Thread(target=_handle_raw_order, args=(inst, data), daemon=True).start()
        return

    if rest == ["scene"]:
        data = _parse_json(payload)
        if data is not None and manager.observe_version(inst, data.get("v")):
            _schedule_units_broadcast(inst)
        return

    if len(rest) != 3 or not rest[0].isdigit() or not rest[1].isdigit():
        return
    group, unit, kind = int(rest[0]), int(rest[1]), rest[2]

    if kind == "status":
        if not payload.strip():
            device_state.forget_unit(inst, group, unit)
        else:
            data = _parse_json(payload)
            if data is None:
                return
            device_state.apply_unit_status(inst, group, unit, data)
            if "v" in data:
                manager.observe_version(inst, data["v"])
        _schedule_units_broadcast(inst)
    elif kind == "hello" and unit == 0:
        data = _parse_json(payload)
        if data is not None:
            device_state.apply_hello(inst, group, data)
            _schedule_units_broadcast(inst)
    elif kind == "online" and unit == 0:
        device_state.apply_master_online(inst, group, payload.strip() == "1")
        _schedule_units_broadcast(inst)
    # kind == "cmd": our own maintenance commands coming back


def _handle_raw_order(inst: int, data: dict):
    """A master forwarding an SMS order on cmd/in: validated, numbered and
    published like any other order."""
    cmds = data.get("cmds")
    origin = data.get("from") or "?"
    source = str(data.get("src") or "sms")
    if not isinstance(cmds, list):
        return
    ok, result = _submit(inst, [str(c) for c in cmds], source)
    if ok:
        logger.info("Raw order from %s (%s) numbered as v%s", origin, source, result)
        return
    logger.warning("Raw order from %s (%s) rejected: %s", origin, source, result)
    _broadcast({
        "topic": f"{TOPIC_PREFIX}/{inst}/cmd/in",
        "payload": f"Orden de {origin} rechazada: {result}",
    })


# ---------- outgoing messages ----------

def start(loop: asyncio.AbstractEventLoop):
    """Call once at FastAPI startup, passing the running event loop."""
    global _client, _loop
    _loop = loop

    _client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CFG.get("client_id", "led-kiosk"),
    )
    if MQTT_CFG.get("username"):
        _client.username_pw_set(MQTT_CFG["username"], MQTT_CFG.get("password"))

    _client.on_connect = _on_connect
    _client.on_disconnect = _on_disconnect
    _client.on_message = _on_message

    manager.set_publisher(_publish)
    _client.connect_async(MQTT_CFG["host"], MQTT_CFG["port"], keepalive=30)
    _client.loop_start()


def stop():
    if _client:
        _client.loop_stop()
        _client.disconnect()


def build_payload(command_id: str, value=None) -> str:
    """
    Builds the device's wire format: 3 letters + value, no separators,
    e.g. FRM5, BRI70, PPCFF0000, or just INV/AUT/STA with no value.
    The device requires this UPPERCASE EXACTLY over MQTT (SMS is
    case-insensitive, but that path isn't handled by this app), so the
    whole string is forced uppercase here regardless of how the UI/caller
    sent it -- callers never need to worry about casing.
    """
    command_id = command_id.strip().upper()
    if value is None or value == "":
        return command_id
    value_str = str(value)
    if value_str.startswith("#"):  # hex colors from <input type="color">
        value_str = value_str[1:]
    return f"{command_id}{value_str}".upper()


def _try_publish(topic: str, payload: str) -> bool:
    try:
        result = _client.publish(topic, payload, qos=MQTT_CFG.get("qos", 1), retain=False)
    except OSError as e:
        logger.error("Publish raised %s: %s %s", e, topic, payload)
        return False
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        logger.info("Published %s %s", topic, payload)
        return True
    logger.error("Publish failed with rc=%s: %s %s", result.rc, topic, payload)
    return False


def _publish(topic: str, payload: str) -> tuple[bool, str]:
    """
    Publishes QoS 1, never retained.

    _connected can still read True right after the network path actually died
    (WiFi/LTE handover) since paho only notices on the next keepalive or write
    attempt -- often the write this function is about to make. So a failed
    publish here is reconnected and retried once before giving up, instead of
    trusting the stale flag and erroring out immediately. (Should both
    attempts end up delivered, an order is still applied once: masters ignore
    a version they already have.)

    Returns (ok, error_message); error_message is a user-facing string in
    Spanish, empty on success.
    """
    global _connected
    if _client is None or not _connected:
        logger.error("Cannot publish, MQTT not connected")
        return False, "No hay conexión con el broker MQTT."
    if _try_publish(topic, payload):
        return True, ""

    _connected = False
    logger.warning("Publish failed, reconnecting and retrying: %s", payload)
    try:
        _client.reconnect()
    except OSError as e:
        logger.error("Reconnect failed: %s", e)
        return False, "Se perdió la conexión con el broker MQTT y no se pudo reconectar."

    if _try_publish(topic, payload):
        _connected = True
        return True, ""

    logger.error("Publish retry failed after reconnect: %s", payload)
    return False, "El comando no pudo enviarse; la conexión MQTT sigue inestable."


def _submit(inst: int, cmds: list[str], source: str) -> tuple[bool, int | str]:
    ok, result = manager.submit(inst, cmds, source)
    if ok:
        state = device_state.record_order(inst, cmds)
        if state is not None:
            _broadcast_installation_state(inst, state)
        _schedule_units_broadcast(inst)  # latest_v moved: units are behind until they report it
    return ok, result


def validate_order(commands: list[tuple[str, object]]) -> str | None:
    """None if [(command_id, value), ...] makes a valid order, else a
    user-facing reason in Spanish -- the same check send_order() makes."""
    return manager.validate_order([build_payload(cid, value) for cid, value in commands])


def send_order(inst: int | None, commands: list[tuple[str, object]], source: str = "ui") -> tuple[bool, int | str]:
    """Sends [(command_id, value), ...] as one order to an installation (the
    active one if inst is None); all of them are applied together.

    Returns (True, v) with the order's version, or (False, error) with a
    user-facing error in Spanish.
    """
    inst = _active_inst if inst is None else inst
    return _submit(inst, [build_payload(cid, value) for cid, value in commands], source)


def validate_unit_command(unit: int, command: str) -> str | None:
    """None if `command` is a maintenance command `unit` accepts, else a
    user-facing reason in Spanish."""
    command = command.strip().upper()
    if command not in _UNIT_COMMANDS | _MASTER_COMMANDS:
        return f"Comando de mantenimiento desconocido: {command}."
    if command in _MASTER_COMMANDS and unit != 0:
        return f"{command} solo se envía al maestro del grupo (unidad 0)."
    return None


def send_unit_command(inst: int, group: int, unit: int, command: str) -> tuple[bool, str]:
    """Maintenance for one unit: STA (fresh status), or, for a group master
    (unit 0), ROL (roll call of the whole group) and NET0/NET1 (its data
    connection)."""
    error = validate_unit_command(unit, command)
    if error:
        return False, error
    if inst not in _installations_by_inst:
        return False, f"Instalación {inst} desconocida."
    return _publish(f"{TOPIC_PREFIX}/{inst}/{group}/{unit}/cmd", command.strip().upper())


def request_units_refresh(inst: int) -> tuple[bool, str]:
    """Roll call (ROL) of every group of one installation. The statuses come
    back as retained JSON on each unit's status topic."""
    errors = []
    for group in device_state.get_groups(inst):
        ok, error = send_unit_command(inst, group, 0, "ROL")
        if not ok:
            errors.append(error)
    return (not errors), (errors[0] if errors else "")


def request_status_all():
    """Roll call of every group of every configured installation.
    Fire-and-forget: the statuses update device_state whenever -- if ever --
    they arrive."""
    for inst in _installations_by_inst:
        request_units_refresh(inst)
