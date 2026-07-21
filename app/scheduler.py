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
import threading
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app import db
from app.mqtt_client import is_connected, publish_command

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()

# Firmware observed corrupting/misattributing payloads when a burst of commands
# arrives faster than its ~100ms dispatch tick can drain them (device-side
# queueing bug, not something this app can fix) -- so pace successive commands
# in one scheduled state out with a gap comfortably above that tick instead of
# firing them back-to-back.
INTER_COMMAND_DELAY_S = 0.3

# The device is normally powered off overnight, so the app itself may not be
# running when a schedule's run_at passes. On restart, a schedule missed by
# no more than this long is still worth catching up (it's just last night's
# run); anything older is stale and gets marked 'missed' instead of firing a
# possibly-days-old state out of nowhere.
MISSED_SCHEDULE_CATCHUP_S = 16 * 3600

# How long to wait for MQTT to actually finish connecting before firing
# catch-up commands at startup. main.py calls mqtt_client.start() (which only
# *initiates* the connection via connect_async) immediately before
# scheduler.start() -- so right after boot, is_connected() is almost always
# still False, and publish_command() would otherwise fail immediately.
CATCHUP_CONNECT_TIMEOUT_S = 30
CATCHUP_POLL_INTERVAL_S = 1


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


def _wait_for_mqtt_then_catch_up(catchup: list[tuple[int, list[dict]]]):
    """Runs on its own thread at startup. Waits (briefly, with a timeout) for
    MQTT to connect, then fires each missed-but-recent schedule in
    chronological order -- if several were missed, the last one firing last
    is correct, it's exactly what would have happened had the device stayed
    on overnight. Paced the same as commands within one schedule, for the
    same reason (avoids a firmware-queue-corrupting burst)."""
    waited = 0
    while not is_connected() and waited < CATCHUP_CONNECT_TIMEOUT_S:
        time.sleep(CATCHUP_POLL_INTERVAL_S)
        waited += CATCHUP_POLL_INTERVAL_S
    for i, (schedule_id, commands) in enumerate(catchup):
        if i > 0:
            time.sleep(INTER_COMMAND_DELAY_S)
        _run_job(schedule_id, commands)


def _rearm_pending():
    now = datetime.now().timestamp()
    catchup = []
    for row in db.list_schedules():
        commands = json.loads(row["commands_json"])
        if row["run_at"] <= now:
            if now - row["run_at"] <= MISSED_SCHEDULE_CATCHUP_S:
                catchup.append((row["id"], commands))
            else:
                db.mark_schedule(row["id"], "missed")
            continue
        scheduler.add_job(
            _run_job,
            "date",
            run_date=datetime.fromtimestamp(row["run_at"]),
            args=[row["id"], commands],
            id=str(row["id"]),
            misfire_grace_time=3600,
        )
    if catchup:
        logger.info("Catching up %d missed-but-recent schedule(s) once MQTT connects", len(catchup))
        threading.Thread(target=_wait_for_mqtt_then_catch_up, args=(catchup,), daemon=True).start()
    logger.info("Rearmed pending scheduled command(s)")


def stop():
    scheduler.shutdown(wait=False)
