from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SendCommandRequest(BaseModel):
    command_id: str
    args: dict[str, Any] = {}


class ScheduleRequest(BaseModel):
    command_id: str
    args: dict[str, Any] = {}
    run_at: datetime  # ISO 8601, e.g. from <input type="datetime-local">
