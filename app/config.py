"""
Loads config.json (copy config.example.json -> config.json and edit it).
Kept deliberately dumb (plain JSON, no env-var magic) so it's easy for
a non-dev to edit on the device itself if needed.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.json"
        raise FileNotFoundError(
            f"Missing {CONFIG_PATH}. Copy {example.name} to config.json and edit it."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _load_devices(mqtt_cfg: dict) -> list[dict]:
    """
    Multiple physical columns share one broker and the same log_topic
    (everyone's status messages land in one shared stream); only their
    command_topic differs, so that's all "devices" in config.json lists.
    Older config.json files (from before device switching existed) instead
    have a single flat command_topic right on the mqtt block; that shape
    still works, normalized here into a one-item devices list, so an
    existing on-device config.json isn't broken by this update until
    someone gets around to editing it.
    """
    devices = mqtt_cfg.get("devices")
    if devices:
        return devices
    return [{
        "id": "columna1",
        "label": "Columna 1 (NQN)",
        "command_topic": mqtt_cfg["command_topic"],
        "status_tag": "COL-02",
    }]


config = load_config()
MQTT_CFG = config["mqtt"]
DEVICES = _load_devices(MQTT_CFG)
APP_CFG = config["app"]
DB_PATH = ROOT / APP_CFG["db_path"]
PROFILE_PATH = ROOT / APP_CFG["profile_path"]
