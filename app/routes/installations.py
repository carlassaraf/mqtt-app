from fastapi import APIRouter, HTTPException

from app import device_state, legacy
from app.models import SelectTargetRequest, UnitCommandRequest
from app.mqtt_client import (
    get_active_target,
    get_installations,
    get_targets,
    parse_target,
    request_units_refresh,
    send_unit_command,
    set_active_target,
    units_snapshot,
    validate_unit_command,
)

router = APIRouter(prefix="/api/installations", tags=["installations"])


def _check_inst(inst: int):
    if inst not in {i["inst"] for i in get_installations()}:
        raise HTTPException(404, "Instalación desconocida.")


@router.get("")
def list_installations():
    """What the topbar dropdown can target (installations, then firmware 1.x
    devices), plus which one is active."""
    return {"installations": get_installations(), "targets": get_targets(), "active": get_active_target()}


@router.post("/select")
def select_target(req: SelectTargetRequest):
    if not set_active_target(req.target):
        raise HTTPException(404, "Destino desconocido.")
    kind, key = parse_target(req.target)
    if kind == "legacy":
        # Refresh its card values. A failed publish (MQTT down) shouldn't
        # fail the switch itself.
        legacy.request_status(key)
    return {"status": "ok", "active": req.target}


@router.get("/state")
def get_installation_states():
    """What each installation shows (see app/device_state.py), keyed by
    installation then command id. Live updates arrive over /ws/logs as
    {"type": "installation_state", ...} messages."""
    return device_state.get_all()


@router.get("/{inst}/units")
def get_units(inst: int):
    """Per-group master info and per-unit statuses. Live updates arrive over
    /ws/logs as {"type": "units", ...} messages."""
    _check_inst(inst)
    return units_snapshot(inst)


@router.post("/{inst}/refresh")
def refresh_units(inst: int):
    """Roll call (ROL) of every group: fresh statuses from every unit."""
    _check_inst(inst)
    ok, error = request_units_refresh(inst)
    if not ok:
        raise HTTPException(503, error)
    return {"status": "sent"}


@router.post("/{inst}/units/{group}/{unit}/cmd")
def unit_command(inst: int, group: int, unit: int, req: UnitCommandRequest):
    """Maintenance for one unit: STA, or ROL for a group master."""
    _check_inst(inst)
    error = validate_unit_command(unit, req.command)
    if error:
        raise HTTPException(400, error)
    ok, error = send_unit_command(inst, group, unit, req.command)
    if not ok:
        raise HTTPException(503, error)
    return {"status": "sent", "command": req.command.strip().upper()}
