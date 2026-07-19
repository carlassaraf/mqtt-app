from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel


class SendCommandRequest(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None


class ScheduleCommand(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None


class ScheduleRequest(BaseModel):
    label: str  # human-readable description of the state, shown in the pending list
    commands: list[ScheduleCommand]  # sent in order when the schedule fires
    run_at: datetime  # ISO 8601, e.g. from <input type="datetime-local">
