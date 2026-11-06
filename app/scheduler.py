"""
Command scheduling. Uses APScheduler's in-memory job store for the actual
timing, backed by our own sqlite table (app.db) so pending jobs survive an
app restart -- on startup we re-read pending rows and re-arm them.

Kept independent from mqtt_client's connection state: if MQTT happens to be
down at fire time, send_order() returns False and we mark the job 'failed'
rather than losing it silently.
"""
import json
import logging
import threading
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app import db, legacy
from app.mqtt_client import broadcast_event, get_active_target, is_connected, send_order

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()

# The device is normally powered off overnight, so the app itself may not be
# running when a schedule's run_at passes. On restart, a schedule missed by
# no more than this long is still worth catching up (it's just last night's
# run); anything older is stale and gets marked 'missed' instead of firing a
# possibly-days-old state out of nowhere.
MISSED_SCHEDULE_CATCHUP_S = 16 * 3600

# How long to wait for MQTT to actually finish connecting before firing
# catch-up orders at startup. main.py calls mqtt_client.start() (which only
# *initiates* the connection via connect_async) immediately before
# scheduler.start() -- so right after boot, is_connected() is almost always
# still False, and send_order() would otherwise fail immediately.
CATCHUP_CONNECT_TIMEOUT_S = 30
CATCHUP_POLL_INTERVAL_S = 1

# Firmware 1.x devices (legacy.py) don't report their state on their own.
# A STA this often catches drift (someone changing the device over SMS, a
# reboot into automatic mode); in automatic mode the frame changes on its own
# every ~2 minutes, so those devices are asked on that period.
LEGACY_STATUS_REFRESH_INTERVAL_S = 3600
LEGACY_AUTOMATIC_STATUS_REFRESH_INTERVAL_S = 120


def _run_job(schedule_id: int, label: str, commands: list[dict], target: str | None = None):
    """Sends the whole state to the target it was created for (the active one
    for rows from before targets were stored): to an installation as one
    order, every unit applying all of its commands together at the same
    instant; to a legacy device one by one, paced. Runs on APScheduler's own
    worker thread."""
    ok, result = send_order(
        target, [(cmd["command_id"], cmd.get("value")) for cmd in commands], source="schedule"
    )
    status = "sent" if ok else "failed"
    db.mark_schedule(schedule_id, status)
    logger.info("Scheduled state %s (%d commands) -> %s (%s)", schedule_id, len(commands), status, result)
    # Lets the frontend (open on the kiosk screen or not) pop up a notice the
    # instant a schedule actually fires, over the same /ws/logs socket the
    # live MQTT log already uses -- see broadcast_event's docstring.
    broadcast_event({"type": "schedule_fired", "schedule_id": schedule_id, "label": label, "status": status})


def add_scheduled_command(label: str, commands: list[dict], run_at: datetime) -> int:
    """Schedules a state for the target that's active right now."""
    target = get_active_target()
    schedule_id = db.insert_schedule(label, json.dumps(commands), run_at.timestamp(), target)
    scheduler.add_job(
        _run_job,
        "date",
        run_date=run_at,
        args=[schedule_id, label, commands, target],
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
    if legacy.LOG_TOPICS:
        scheduler.add_job(
            legacy.request_status_all, "interval", seconds=LEGACY_STATUS_REFRESH_INTERVAL_S,
            id="legacy_status_refresh",
        )
        scheduler.add_job(
            legacy.request_status_automatic, "interval", seconds=LEGACY_AUTOMATIC_STATUS_REFRESH_INTERVAL_S,
            id="legacy_automatic_status_refresh",
        )


def _wait_for_mqtt_then_catch_up(catchup: list[tuple[int, str, list[dict], str | None]]):
    """Runs on its own thread at startup. Waits (briefly, with a timeout) for
    MQTT to connect, then fires each missed-but-recent schedule in
    chronological order -- if several were missed, the last one firing last
    is correct, it's exactly what would have happened had the device stayed
    on overnight. Each one is its own numbered order (or, for a legacy
    device, its own paced commands), so they're applied in sequence."""
    waited = 0
    while not is_connected() and waited < CATCHUP_CONNECT_TIMEOUT_S:
        time.sleep(CATCHUP_POLL_INTERVAL_S)
        waited += CATCHUP_POLL_INTERVAL_S
    for schedule_id, label, commands, target in catchup:
        _run_job(schedule_id, label, commands, target)


def _rearm_pending():
    now = datetime.now().timestamp()
    catchup = []
    for row in db.list_schedules():
        commands = json.loads(row["commands_json"])
        if row["run_at"] <= now:
            if now - row["run_at"] <= MISSED_SCHEDULE_CATCHUP_S:
                catchup.append((row["id"], row["label"], commands, row["target"]))
            else:
                db.mark_schedule(row["id"], "missed")
            continue
        scheduler.add_job(
            _run_job,
            "date",
            run_date=datetime.fromtimestamp(row["run_at"]),
            args=[row["id"], row["label"], commands, row["target"]],
            id=str(row["id"]),
            misfire_grace_time=3600,
        )
    if catchup:
        logger.info("Catching up %d missed-but-recent schedule(s) once MQTT connects", len(catchup))
        threading.Thread(target=_wait_for_mqtt_then_catch_up, args=(catchup,), daemon=True).start()
    logger.info("Rearmed pending scheduled command(s)")


def stop():
    scheduler.shutdown(wait=False)
