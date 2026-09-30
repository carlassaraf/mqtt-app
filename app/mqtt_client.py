"""
Wraps paho-mqtt in a small manager:
- connects on startup, auto-reconnects (paho handles backoff)
- subscribes to the log topic, stores messages in sqlite, and fans them
  out to any connected websocket clients (for the live log view)
- exposes publish_command() for the UI / scheduler to send commands

paho's callbacks run on paho's own network thread, not the asyncio event
loop, so we hop into the loop with call_soon_threadsafe when we need to
push a message to websocket subscribers.
"""
import asyncio
import logging
import threading
import time

import paho.mqtt.client as mqtt

from app import device_state
from app.config import DEVICES, MQTT_CFG
from app.db import insert_log
from app.message_log import log_message

logger = logging.getLogger("mqtt")

_ws_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_client: mqtt.Client | None = None
_connected = False

# All devices share one broker connection and the same log_topic; only
# their command_topic differs. _active_device_id picks which command_topic
# publish_command() uses -- see set_active_device(). Not persisted across
# restarts on purpose (this is a single kiosk, not a fleet), so it always
# starts on the first configured device.
_devices_by_id = {d["id"]: d for d in DEVICES}
_active_device_id = DEVICES[0]["id"]

# STA replies only show in the Status tab's live log when someone asked for
# one by hand; the app's own automatic STAs (connect, device switch, hourly,
# 2-minute in automatic mode, after CLR) would otherwise flood it. Maps
# device id -> monotonic deadline for its manually requested reply to arrive.
_manual_status_until: dict[str, float] = {}
MANUAL_STATUS_REPLY_WINDOW_S = 30

# Gap before the automatic STA that follows CLR, mirroring scheduler.py's
# INTER_COMMAND_DELAY_S (the firmware misbehaves on back-to-back commands).
POST_CLR_STATUS_DELAY_S = 0.3


def get_devices() -> list[dict]:
    return [{"id": d["id"], "label": d["label"]} for d in DEVICES]


def get_active_device_id() -> str:
    return _active_device_id


def set_active_device(device_id: str) -> bool:
    """Switches which device's command_topic publish_command() uses from here
    on -- the log subscription is shared across devices and untouched by this.
    Returns False for an unknown device_id."""
    global _active_device_id
    if device_id not in _devices_by_id:
        return False
    _active_device_id = device_id
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


def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = reason_code == 0
    if _connected:
        logger.info("MQTT connected, subscribing to %s", MQTT_CFG["log_topic"])
        client.subscribe(MQTT_CFG["log_topic"], qos=MQTT_CFG.get("qos", 1))
        # Covers both app boot and resyncing after any dropped connection:
        # ask every device for a fresh status so the command cards show real
        # values instead of whatever (if anything) was assumed before. On its
        # own thread: publish_command()'s failure path reconnects, which
        # mustn't happen from inside paho's own callback.
        threading.Thread(target=request_status_all, daemon=True).start()
    else:
        logger.error("MQTT connect failed: %s", reason_code)


def _on_disconnect(client, userdata, reason_code, properties=None):
    global _connected
    _connected = False
    logger.warning("MQTT disconnected: %s", reason_code)


def _on_message(client, userdata, msg):
    payload_str = msg.payload.decode(errors="replace")
    insert_log(msg.topic, payload_str)
    log_message(msg.topic, payload_str)
    report = device_state.apply_status_report(payload_str)
    if report:
        _broadcast_device_state(*report)
        if time.monotonic() > _manual_status_until.pop(report[0], 0):
            return  # automatic STA reply: stored and logged to file, kept out of the live view
    _broadcast({"topic": msg.topic, "payload": payload_str})


def _broadcast_device_state(device_id: str, state: dict):
    _broadcast({"type": "device_state", "device_id": device_id, "state": state})


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


def _try_publish(payload: str, device_id: str) -> bool:
    try:
        result = _client.publish(
            _devices_by_id[device_id]["command_topic"], payload, qos=MQTT_CFG.get("qos", 1)
        )
    except OSError as e:
        logger.error("Publish raised %s: %s", e, payload)
        return False
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        logger.info("Published command: %s", payload)
        return True
    logger.error("Publish failed with rc=%s: %s", result.rc, payload)
    return False


def publish_command(command_id: str, value=None, device_id: str | None = None) -> tuple[bool, str]:
    """
    Sends to the active device unless device_id names another one.

    _connected can still read True right after the network path actually died
    (WiFi/LTE handover) since paho only notices on the next keepalive or write
    attempt -- often the write this function is about to make. So a failed
    publish here is reconnected and retried once before giving up, instead of
    trusting the stale flag and erroring out immediately.

    Returns (ok, error_message); error_message is a user-facing string in
    Spanish, empty on success.
    """
    global _connected
    if _client is None or not _connected:
        logger.error("Cannot publish, MQTT not connected")
        return False, "No hay conexión con el broker MQTT."
    device_id = device_id or _active_device_id
    payload = build_payload(command_id, value)
    if _try_publish(payload, device_id):
        _after_sent(device_id, command_id, value)
        return True, ""

    _connected = False
    logger.warning("Publish failed, reconnecting and retrying: %s", payload)
    try:
        _client.reconnect()
    except OSError as e:
        logger.error("Reconnect failed: %s", e)
        return False, "Se perdió la conexión con el broker MQTT y no se pudo reconectar."

    if _try_publish(payload, device_id):
        _connected = True
        _after_sent(device_id, command_id, value)
        return True, ""

    logger.error("Publish retry failed after reconnect: %s", payload)
    return False, "El comando no pudo enviarse; la conexión MQTT sigue inestable."


def _after_sent(device_id: str, command_id: str, value):
    state = device_state.record_sent(device_id, command_id, value)
    if state is not None:
        _broadcast_device_state(device_id, state)
    # CLR undoes the quick color presets, leaving the device in a state this
    # app can't predict -- ask for it instead of guessing.
    if command_id.strip().upper() == "CLR":
        threading.Timer(
            POST_CLR_STATUS_DELAY_S, publish_command, args=("STA",), kwargs={"device_id": device_id}
        ).start()


def expect_manual_status_reply(device_id: str):
    """Call right before publishing a STA someone asked for by hand, so its
    reply shows in the live log like any other message."""
    _manual_status_until[device_id] = time.monotonic() + MANUAL_STATUS_REPLY_WINDOW_S


def request_status_all():
    """Sends STA to every configured device. Each has its own command topic,
    so no pacing is needed between them (the firmware-queue burst problem is
    per device). Fire-and-forget: the replies update device_state whenever --
    if ever -- they arrive."""
    for device_id in _devices_by_id:
        publish_command("STA", device_id=device_id)


def request_status_automatic():
    """STA to every device currently known to be in automatic mode, whose
    frame changes on its own -- keeps the frame card roughly in step."""
    for device_id in _devices_by_id:
        if device_state.is_automatic(device_id):
            publish_command("STA", device_id=device_id)
