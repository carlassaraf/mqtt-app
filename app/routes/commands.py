import json

from fastapi import APIRouter, HTTPException

from app.config import PROFILE_PATH
from app.models import SendCommandRequest, SendOrderRequest
from app.mqtt_client import send_order, validate_order

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
def get_command_profile():
    """Returns the device_commands.json profile the frontend renders buttons from."""
    with open(PROFILE_PATH) as f:
        return json.load(f)


@router.post("/send")
def send_command(req: SendCommandRequest):
    """One command, as a one-command order to the active installation."""
    commands = [(req.command_id, req.value)]
    error = validate_order(commands)
    if error:
        raise HTTPException(400, error)
    ok, result = send_order(None, commands)
    if not ok:
        raise HTTPException(503, result)
    return {"status": "sent", "v": result, "command_id": req.command_id, "value": req.value}


@router.post("/order")
def send_command_order(req: SendOrderRequest):
    """Several commands (e.g. a quick preset), as one order to the active
    installation: every unit applies them together."""
    commands = [(c.command_id, c.value) for c in req.commands]
    error = validate_order(commands)
    if error:
        raise HTTPException(400, error)
    ok, result = send_order(None, commands)
    if not ok:
        raise HTTPException(503, result)
    return {"status": "sent", "v": result}
