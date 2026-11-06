from fastapi import APIRouter, HTTPException

from app import legacy

router = APIRouter(prefix="/api/legacy", tags=["legacy"])


@router.get("/state")
def get_legacy_states():
    """Last-known values per firmware 1.x device (see app/legacy.py), keyed by
    device id then command id, sensor readings (TMP, HUM, CUR) included. Live
    updates arrive over /ws/logs as {"type": "legacy_state", ...} messages."""
    return legacy.get_all()


@router.post("/{device_id}/status")
def request_status(device_id: str):
    """A STA asked for by hand: its reply shows in the live log."""
    if not legacy.is_known(device_id):
        raise HTTPException(404, "Dispositivo desconocido.")
    ok, error = legacy.request_status(device_id, manual=True)
    if not ok:
        raise HTTPException(503, error)
    return {"status": "sent"}
