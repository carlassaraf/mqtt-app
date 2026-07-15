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

import paho.mqtt.client as mqtt

from app.config import MQTT_CFG
from app.db import insert_log

logger = logging.getLogger("mqtt")

_ws_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_client: mqtt.Client | None = None
_connected = False


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


def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = reason_code == 0
    if _connected:
        logger.info("MQTT connected, subscribing to %s", MQTT_CFG["log_topic"])
        client.subscribe(MQTT_CFG["log_topic"], qos=MQTT_CFG.get("qos", 1))
    else:
        logger.error("MQTT connect failed: %s", reason_code)


def _on_disconnect(client, userdata, reason_code, properties=None):
    global _connected
    _connected = False
    logger.warning("MQTT disconnected: %s", reason_code)


def _on_message(client, userdata, msg):
    payload_str = msg.payload.decode(errors="replace")
    insert_log(msg.topic, payload_str)
    _broadcast({"topic": msg.topic, "payload": payload_str})


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


def publish_command(command_id: str, value=None) -> bool:
    if _client is None or not _connected:
        logger.error("Cannot publish, MQTT not connected")
        return False
    payload = build_payload(command_id, value)
    result = _client.publish(
        MQTT_CFG["command_topic"], payload, qos=MQTT_CFG.get("qos", 1)
    )
    logger.info("Published command: %s", payload)
    return result.rc == mqtt.MQTT_ERR_SUCCESS
