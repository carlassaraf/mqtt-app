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
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app import db
from app.mqtt_client import publish_command

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()


def _run_job(schedule_id: int, command_id: str, value):
    ok = publish_command(command_id, value)
    db.mark_schedule(schedule_id, "sent" if ok else "failed")
    logger.info("Scheduled command %s (%s) -> %s", schedule_id, command_id, "sent" if ok else "failed")


def add_scheduled_command(command_id: str, value, run_at: datetime) -> int:
    schedule_id = db.insert_schedule(command_id, json.dumps(value), run_at.timestamp())
    scheduler.add_job(
        _run_job,
        "date",
        run_date=run_at,
        args=[schedule_id, command_id, value],
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
            args=[row["id"], row["command_id"], json.loads(row["value_json"])],
            id=str(row["id"]),
            misfire_grace_time=3600,
        )
    logger.info("Rearmed %d pending scheduled command(s)", len(db.list_schedules()))


def stop():
    scheduler.shutdown(wait=False)
