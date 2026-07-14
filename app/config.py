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


config = load_config()
MQTT_CFG = config["mqtt"]
APP_CFG = config["app"]
DB_PATH = ROOT / APP_CFG["db_path"]
PROFILE_PATH = ROOT / APP_CFG["profile_path"]
