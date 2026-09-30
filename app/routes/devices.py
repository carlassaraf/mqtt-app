from fastapi import APIRouter, HTTPException

from app.models import SelectDeviceRequest
from app import device_state
from app.mqtt_client import get_active_device_id, get_devices, publish_command, set_active_device

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
def list_devices():
    """Devices the topbar dropdown can target, plus which one is active."""
    return {"devices": get_devices(), "active_id": get_active_device_id()}


@router.post("/select")
def select_device(req: SelectDeviceRequest):
    if not set_active_device(req.device_id):
        raise HTTPException(404, "Dispositivo desconocido.")
    # Refresh the newly targeted device's card values. Fire-and-forget: a
    # failed publish (MQTT down) shouldn't fail the switch itself.
    publish_command("STA")
    return {"status": "ok", "active_id": req.device_id}


@router.get("/state")
def get_device_states():
    """Last-known property values per device (see app/device_state.py), keyed
    by device id then command id. Live updates arrive over /ws/logs as
    {"type": "device_state", ...} messages."""
    return device_state.get_all()
