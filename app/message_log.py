"""
Writes every message received on the subscribed MQTT log topic to a plain
text file, so there's something to open/grep/tail after the fact. This is
separate from db.insert_log(), which only keeps the messages sqlite feeds to
the Status tab's live view and GET /api/logs (capped at whatever `limit` the
caller asks for) -- fine for "what just happened" but not for "what happened
last week".

Rotates at midnight, keeping app.log_retention_days days (config.json,
defaults to 14) before deleting the oldest file.
"""
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.config import APP_CFG, ROOT

_LINUX_LOG_DIR = Path("/var/log/led-kiosk")


def _resolve_log_dir() -> Path:
    """
    Prefers the standard Linux log location, /var/log/led-kiosk. On the Pi
    this only works because kiosk/led-kiosk-backend.service declares
    `LogsDirectory=led-kiosk`, which makes systemd pre-create that directory
    (owned by the service's User=) before this code ever runs -- plain
    application code can't mkdir under /var/log without root.

    Falls back to a repo-local logs/ directory, which covers local dev
    (laptop, `uvicorn --reload`, no systemd involved) where /var/log/led-kiosk
    doesn't exist and can't be created.
    """
    try:
        _LINUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
        probe = _LINUX_LOG_DIR / ".write_test"
        probe.touch()
        probe.unlink()
        return _LINUX_LOG_DIR
    except OSError:
        fallback = ROOT / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


_logger = logging.getLogger("mqtt.messages")
_logger.setLevel(logging.INFO)
_logger.propagate = False  # separate file, not mixed into the app's own console/journal output

_handler = TimedRotatingFileHandler(
    _resolve_log_dir() / "mqtt.log",
    when="midnight",
    backupCount=APP_CFG.get("log_retention_days", 14),
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_logger.addHandler(_handler)


def log_message(topic: str, payload: str):
    _logger.info("%s %s", topic, payload)
