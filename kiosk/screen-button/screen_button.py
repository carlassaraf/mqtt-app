#!/usr/bin/env python3
"""GPIO button daemon: toggles the HDMI display on/off via wlopm.
Standalone (not part of the FastAPI app) since it needs GPIO access and must
keep working independent of the backend/browser. See README.md for wiring
and setup."""
import subprocess
from signal import pause

from gpiozero import Button

BUTTON_PIN = 17  # physical pin 11 -- GPIO14/15 are already used by the
                 # A7600 modem's UART, see kiosk/network/

OUTPUT = "HDMI-A-1"  # confirmed via `wlr-randr`/`wlopm` on this device --
                     # re-check if the monitor is ever swapped

# wlopm (wlr-output-power-management-v1), not wlr-randr (wlr-output-management-v1):
# wlr-randr's --off/--on fully disables/re-enables the output, tearing down
# its mode entirely -- confirmed on hardware that re-enabling it that way
# fails ("failed to apply configuration") and doesn't recover without a
# reboot. wlopm is the DPMS-equivalent soft on/off that leaves the mode
# intact, confirmed to round-trip cleanly on this device.


def _screen_is_on() -> bool:
    out = subprocess.run(["wlopm"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        name, _, state = line.partition(" ")
        if name == OUTPUT:
            return state.strip() == "on"
    return False


def toggle_screen():
    subprocess.run(["wlopm", "--off" if _screen_is_on() else "--on", OUTPUT])


if __name__ == "__main__":
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2)
    button.when_pressed = toggle_screen
    pause()
