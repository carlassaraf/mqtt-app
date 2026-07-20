"""
Command scheduling. Uses APScheduler's in-memory job store for the actual
timing, backed by our own sqlite table (app.db) so pending jobs survive an
app restart -- on startup we re-read pending rows and re-arm them.

Kept independent from mqtt_client's connection state: if MQTT happens to be
down at fire time, publish_command() returns False and we mark the job
'failed' rather than losing it silently.
"""
import json
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app import db
from app.mqtt_client import publish_command

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()

# Firmware observed corrupting/misattributing payloads when a burst of commands
# arrives faster than its ~100ms dispatch tick can drain them (device-side
# queueing bug, not something this app can fix) -- so pace successive commands
# in one scheduled state out with a gap comfortably above that tick instead of
# firing them back-to-back.
INTER_COMMAND_DELAY_S = 0.3


def _run_job(schedule_id: int, commands: list[dict]):
    """Fires every command in the state, in order, paced by INTER_COMMAND_DELAY_S.
    One failed publish doesn't stop the rest from going out -- but the row is
    marked 'failed' overall if any of them didn't make it, since the resulting
    state may be incomplete. Runs on APScheduler's own worker thread, so the
    blocking sleep here doesn't hold up the web server."""
    all_ok = True
    for i, cmd in enumerate(commands):
        if i > 0:
            time.sleep(INTER_COMMAND_DELAY_S)
        ok = publish_command(cmd["command_id"], cmd.get("value"))
        all_ok = all_ok and ok
    db.mark_schedule(schedule_id, "sent" if all_ok else "failed")
    logger.info("Scheduled state %s (%d commands) -> %s", schedule_id, len(commands), "sent" if all_ok else "failed")


def add_scheduled_command(label: str, commands: list[dict], run_at: datetime) -> int:
    schedule_id = db.insert_schedule(label, json.dumps(commands), run_at.timestamp())
    scheduler.add_job(
        _run_job,
        "date",
        run_date=run_at,
        args=[schedule_id, commands],
        id=str(schedule_id),
        misfire_grace_time=3600,
    )
    return schedule_id


def remove_scheduled_command(schedule_id: int):
    try:
        scheduler.remove_job(str(schedule_id))
    except Exception:
        pass  # already fired or never registered
    db.delete_schedule(schedule_id)


def start():
    scheduler.start()
    _rearm_pending()


def _rearm_pending():
    now = datetime.now().timestamp()
    for row in db.list_schedules():
        if row["run_at"] <= now:
            db.mark_schedule(row["id"], "missed")
            continue
        scheduler.add_job(
            _run_job,
            "date",
            run_date=datetime.fromtimestamp(row["run_at"]),
            args=[row["id"], json.loads(row["commands_json"])],
            id=str(row["id"]),
            misfire_grace_time=3600,
        )
    logger.info("Rearmed %d pending scheduled command(s)", len(db.list_schedules()))


def stop():
    scheduler.shutdown(wait=False)
