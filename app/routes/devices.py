from fastapi import APIRouter, HTTPException

from app.models import SelectDeviceRequest
from app.mqtt_client import get_active_device_id, get_devices, set_active_device

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
def list_devices():
    """Devices the topbar dropdown can target, plus which one is active."""
    return {"devices": get_devices(), "active_id": get_active_device_id()}


@router.post("/select")
def select_device(req: SelectDeviceRequest):
    if not set_active_device(req.device_id):
        raise HTTPException(404, "Dispositivo desconocido.")
    return {"status": "ok", "active_id": req.device_id}
