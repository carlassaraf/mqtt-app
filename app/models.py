from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel


class SendCommandRequest(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None


class OrderCommand(BaseModel):
    command_id: str
    value: Optional[Union[int, str]] = None


class SendOrderRequest(BaseModel):
    commands: list[OrderCommand]  # applied together, as one order


class SelectTargetRequest(BaseModel):
    target: str  # "inst:1" (an installation) or "legacy:nqn" (a firmware 1.x device)


class UnitCommandRequest(BaseModel):
    command: str  # STA, or ROL for a group master (unit 0)


class ScheduleRequest(BaseModel):
    label: str  # human-readable description of the state, shown in the pending list
    commands: list[OrderCommand]  # sent as one order when the schedule fires
    run_at: datetime  # ISO 8601, e.g. from <input type="datetime-local">
