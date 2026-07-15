import time

from fastapi import APIRouter, HTTPException

from app.db import list_schedules
from app.models import ScheduleRequest
from app.scheduler import add_scheduled_command, remove_scheduled_command

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


@router.get("")
def get_schedules():
    return list_schedules()


@router.post("")
def create_schedule(req: ScheduleRequest):
    if req.run_at.timestamp() <= time.time():
        raise HTTPException(400, "run_at must be in the future")
    schedule_id = add_scheduled_command(req.command_id, req.value, req.run_at)
    return {"id": schedule_id, "status": "scheduled"}


@router.delete("/{schedule_id}")
def cancel_schedule(schedule_id: int):
    remove_scheduled_command(schedule_id)
    return {"status": "cancelled", "id": schedule_id}
