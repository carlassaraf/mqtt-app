import json

from fastapi import APIRouter, HTTPException

from app.config import PROFILE_PATH
from app.models import SendCommandRequest
from app.mqtt_client import is_connected, publish_command

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
def get_command_profile():
    """Returns the device_commands.json profile the frontend renders buttons from."""
    with open(PROFILE_PATH) as f:
        return json.load(f)


@router.post("/send")
def send_command(req: SendCommandRequest):
    if not is_connected():
        raise HTTPException(503, "MQTT broker not connected")
    ok = publish_command(req.command_id, req.value)
    if not ok:
        raise HTTPException(500, "Failed to publish command")
    return {"status": "sent", "command_id": req.command_id, "value": req.value}
