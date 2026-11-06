"""
Loads config.json (copy config.example.json -> config.json and edit it).
Kept deliberately dumb (plain JSON, no env-var magic) so it's easy for
a non-dev to edit on the device itself if needed.
"""
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

logger = logging.getLogger("config")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.json"
        raise FileNotFoundError(
            f"Missing {CONFIG_PATH}. Copy {example.name} to config.json and edit it."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _load_installations(cfg: dict, mqtt_cfg: dict) -> list[dict]:
    """
    Firmware 2.0 is addressed per installation (all of its units at once),
    listed under `installations` -- at the top level of config.json or inside
    the mqtt block, either works. Each one needs `inst` (the number on the
    units' stickers); `label` and `groups` (the group numbers to roll-call)
    are optional.

    Older config.json files list firmware 1.x `devices` (one command_topic
    each) instead. Those are driven through `legacy_devices` now (see
    _load_legacy_devices()), but such a file still loads: it falls back to a
    single installation 1, group 1, so
    an on-device config.json that nobody has edited yet doesn't stop the app
    from starting.
    """
    installations = cfg.get("installations") or mqtt_cfg.get("installations")
    if not installations:
        if mqtt_cfg.get("devices") or mqtt_cfg.get("command_topic"):
            logger.warning(
                "config.json lists firmware 1.x devices; using installation 1 instead. "
                "Add an \"installations\" list, and list devices still on firmware 1.x "
                "under \"legacy_devices\" (see config.example.json)."
            )
        installations = [{"inst": 1}]
    result = []
    for item in installations:
        inst = int(item["inst"])
        result.append({
            "inst": inst,
            "label": item.get("label") or f"Instalación {inst}",
            "groups": [int(g) for g in item.get("groups", [1])],
        })
    return result


def _load_legacy_devices(cfg: dict, mqtt_cfg: dict) -> list[dict]:
    """
    Firmware 1.x devices still in the field until they're replaced (the NQN
    column), listed under `legacy_devices` -- top level or inside the mqtt
    block. Each one needs `id` and `command_topic`; `log_topic` defaults to
    the mqtt block's old `log_topic` (or "columna/log"), and `status_tag` is
    the name its STA reply starts with (e.g. "COL-02").
    """
    devices = cfg.get("legacy_devices") or mqtt_cfg.get("legacy_devices") or []
    default_log_topic = mqtt_cfg.get("log_topic") or "columna/log"
    result = []
    for item in devices:
        device_id = str(item["id"])
        result.append({
            "id": device_id,
            "label": item.get("label") or device_id,
            "command_topic": item["command_topic"],
            "log_topic": item.get("log_topic") or default_log_topic,
            "status_tag": item.get("status_tag"),
        })
    return result


config = load_config()
MQTT_CFG = config["mqtt"]
# The firmware's CONFIG_APP_TOPIC_PREFIX; must match the units'.
TOPIC_PREFIX = (config.get("topic_prefix") or MQTT_CFG.get("topic_prefix") or "columna").strip("/")
INSTALLATIONS = _load_installations(config, MQTT_CFG)
LEGACY_DEVICES = _load_legacy_devices(config, MQTT_CFG)
APP_CFG = config["app"]
DB_PATH = ROOT / APP_CFG["db_path"]
PROFILE_PATH = ROOT / APP_CFG["profile_path"]
