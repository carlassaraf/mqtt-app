from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel


class SendCommandRequest(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None


class ScheduleRequest(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None
    run_at: datetime  # ISO 8601, e.g. from <input type="datetime-local">
