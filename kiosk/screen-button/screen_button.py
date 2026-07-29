#!/usr/bin/env python3
"""GPIO button daemon: toggles the HDMI display on/off via wlr-randr.
Standalone (not part of the FastAPI app) since it needs GPIO access and must
keep working independent of the backend/browser. See README.md for wiring
and setup."""
import subprocess
from signal import pause

from gpiozero import Button

BUTTON_PIN = 17  # physical pin 11 -- GPIO14/15 are already used by the
                 # A7600 modem's UART, see kiosk/network/

OUTPUT = "HDMI-A-1"  # confirmed via `wlr-randr` on this device -- re-check
                     # with `wlr-randr` if the monitor is ever swapped


def _screen_is_on() -> bool:
    result = subprocess.run(["wlr-randr", "--output", OUTPUT], capture_output=True, text=True)
    is_on = "Enabled: yes" in result.stdout
    print(f"[screen_button] state check: is_on={is_on} stdout={result.stdout!r} stderr={result.stderr!r}", flush=True)
    return is_on


def toggle_screen():
    action = "--off" if _screen_is_on() else "--on"
    print(f"[screen_button] toggling: {action}", flush=True)
    result = subprocess.run(["wlr-randr", "--output", OUTPUT, action], capture_output=True, text=True)
    print(f"[screen_button] toggle result: returncode={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}", flush=True)


if __name__ == "__main__":
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2)
    button.when_pressed = toggle_screen
    pause()
