"""
Wraps paho-mqtt in a small manager:
- connects on startup, auto-reconnects (paho handles backoff)
- subscribes to <prefix>/<inst>/# for every configured installation and
  routes each message by topic: log lines to sqlite + the log file + the
  live log view, unit statuses / hello / online to device_state, versions
  to the installation manager, SMS orders from cmd/in to the manager
- also subscribes to the log topic of every firmware 1.x device still in
  use (legacy_devices in config.json) and hands those messages to legacy.py
- exposes send_order() for the UI / scheduler, which builds each command
  string with build_payload() and hands the order to manager.submit() -- or,
  for a legacy device, sends the commands one by one through legacy.send()

paho's callbacks run on paho's own network thread, not the asyncio event
loop, so we hop into the loop with call_soon_threadsafe when we need to
push a message to websocket subscribers.
"""
import asyncio
import json
import logging
import threading

import paho.mqtt.client as mqtt

from app import device_state, legacy, manager
from app.config import INSTALLATIONS, MQTT_CFG, TOPIC_PREFIX
from app.db import insert_log
from app.message_log import log_message

logger = logging.getLogger("mqtt")

_ws_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_client: mqtt.Client | None = None
_connected = False

# What the UI's commands go to (a "target"): an installation, "inst:1"
# (firmware 2.0), or a firmware 1.x device, "legacy:nqn". Not persisted
# across restarts on purpose (this is a single kiosk, not a fleet), so it
# always starts on the first configured installation.
_installations_by_inst = {i["inst"]: i for i in INSTALLATIONS}
_active_target = f"inst:{INSTALLATIONS[0]['inst']}"

# Maintenance commands for one unit, on <prefix>/<inst>/<group>/<unit>/cmd.
# STA works for any unit; ROL (roll call of the whole group) only for the
# group's master, unit 0. NET (a master's data connection) is an admin
# command, deliberately not available from this operator app.
_UNIT_COMMANDS = {"STA"}
_MASTER_COMMANDS = {"ROL"}

# Statuses arrive in bursts (every retained one right after subscribing, a
# whole group after a roll call), so the units table is pushed to the UI at
# most this often per installation instead of once per message.
UNITS_BROADCAST_DEBOUNCE_S = 0.5
_units_broadcast_pending: set[int] = set()
_units_broadcast_lock = threading.Lock()


def get_installations() -> list[dict]:
    return [{"inst": i["inst"], "label": i["label"], "groups": i["groups"]} for i in INSTALLATIONS]


def parse_target(target: str | None) -> tuple[str, int | str] | None:
    """"inst:1" -> ("inst", 1), "legacy:nqn" -> ("legacy", "nqn"); None if it
    isn't a configured installation or legacy device."""
    kind, _, key = str(target or "").partition(":")
    if kind == "inst" and key.isdigit() and int(key) in _installations_by_inst:
        return "inst", int(key)
    if kind == "legacy" and legacy.is_known(key):
        return "legacy", key
    return None


def get_targets() -> list[dict]:
    """Everything the UI can send to: installations first, then legacy devices."""
    return [
        {"target": f"inst:{i['inst']}", "kind": "installation", "inst": i["inst"], "label": i["label"]}
        for i in INSTALLATIONS
    ] + [
        {"target": f"legacy:{d['id']}", "kind": "legacy", "id": d["id"], "label": d["label"]}
        for d in legacy.get_devices()
    ]


def target_label(target: str | None) -> str | None:
    return next((t["label"] for t in get_targets() if t["target"] == target), None)


def get_active_target() -> str:
    return _active_target


def get_active_inst() -> int | None:
    """The active installation, or None while a legacy device is active."""
    kind, key = parse_target(_active_target)
    return key if kind == "inst" else None


def set_active_target(target: str) -> bool:
    """Switches what the UI's commands go to from here on. Returns False for
    an unknown target."""
    global _active_target
    if parse_target(target) is None:
        return False
    _active_target = target
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
        topics += [(topic, MQTT_CFG.get("qos", 1)) for topic in legacy.LOG_TOPICS]
        logger.info("MQTT connected, subscribing to %s", ", ".join(t for t, _ in topics))
        client.subscribe(topics)
        manager.mark_subscribed()
        # Retained statuses arrive on their own right after subscribing; a
        # roll call on top makes them fresh, and legacy devices get a STA.
        # On its own thread: publishing's failure path reconnects, which
        # mustn't happen inside paho's callback.
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
    if topic in legacy.LOG_TOPICS:
        insert_log(topic, payload)
        log_message(topic, payload)
        if legacy.handle_log(payload):
            _broadcast({"topic": topic, "payload": payload})
        return

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
    legacy.set_io(_publish, _broadcast)
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


def send_order(
    target: str | None, commands: list[tuple[str, object]], source: str = "ui"
) -> tuple[bool, int | str | None]:
    """Sends [(command_id, value), ...] to a target (the active one if None).
    To an installation it's one order, all of it applied together; to a
    legacy device, the commands one by one, paced.

    Returns (True, v) with the order's version (None for a legacy device,
    which has no versions), or (False, error) with a user-facing error in
    Spanish.
    """
    parsed = parse_target(target or _active_target)
    if parsed is None:
        return False, "Destino desconocido."
    kind, key = parsed
    cmds = [build_payload(cid, value) for cid, value in commands]
    if kind == "legacy":
        ok, error = legacy.send(key, cmds)
        return (True, None) if ok else (False, error)
    return _submit(key, cmds, source)


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
    (unit 0), ROL (roll call of the whole group)."""
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
    """Roll call of every group of every configured installation, and a STA
    to every legacy device. Fire-and-forget: the statuses update the state
    whenever -- if ever -- they arrive."""
    for inst in _installations_by_inst:
        request_units_refresh(inst)
    legacy.request_status_all()
