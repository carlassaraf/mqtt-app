import subprocess

from fastapi import APIRouter

from app.config import ROOT

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


@router.post("/update")
def update_app():
    """Runs kiosk/update_app.sh: git pull, restart the backend, clear
    Chromium's cache, relaunch. Fired off fully detached (start_new_session)
    rather than awaited -- the script's own job is to kill *this* backend
    process partway through so the new code actually takes effect, so this
    handler can't wait around for it to finish, and the child must survive
    its parent (this process) dying mid-script."""
    script = ROOT / "kiosk" / "update_app.sh"
    subprocess.Popen([str(script)], start_new_session=True)
    return {"status": "updating"}


@router.post("/minimize")
def minimize_browser():
    """Hides the Chromium window without killing it (unlike quit_browser,
    which pkills it). Restoring happens outside this app entirely -- see
    kiosk/launch_app.sh, which detects the still-running process and
    un-hides/activates it instead of relaunching. Uses windowunmap rather
    than windowminimize: minimize relies on ICCCM WM_CHANGE_STATE/iconify
    support from the window manager, which matchbox-window-manager (the
    one this kiosk runs -- see led-kiosk-browser.service) doesn't
    implement, so the button did nothing. windowunmap hides the window
    directly via X11 without needing WM cooperation, and pairs with the
    windowmap already used to restore it in launch_app.sh."""
    subprocess.run(["xdotool", "search", "--class", "chromium", "windowunmap"], check=False)
    return {"status": "minimized"}
