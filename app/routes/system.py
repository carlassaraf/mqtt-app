import subprocess

from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/quit-browser")
def quit_browser():
    """Closes the kiosk browser. There's no window chrome in --kiosk mode, so
    this is the client's only way back to the Pi's desktop. Matches both the
    chromium and chromium-browser binary names (see start_kiosk.sh), since
    either could be running depending on the OS version. Leaves the backend
    running -- reopening via the desktop launcher just relaunches the browser
    against the already-running server, no restart needed."""
    subprocess.run(["pkill", "-f", "chromium"], check=False)
    return {"status": "closing"}
