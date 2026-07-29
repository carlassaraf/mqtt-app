#!/usr/bin/env python3
"""GPIO button daemon: toggles the HDMI display on/off via X11 DPMS.
Standalone (not part of the FastAPI app) since it needs GPIO access and must
keep working independent of the backend/browser. See README.md for wiring
and setup."""
import subprocess
from signal import pause

from gpiozero import Button

BUTTON_PIN = 17  # physical pin 11 -- GPIO14/15 are already used by the
                 # A7600 modem's UART, see kiosk/network/


def _screen_is_on() -> bool:
    out = subprocess.run(["xset", "q"], capture_output=True, text=True).stdout
    return "Monitor is On" in out


def toggle_screen():
    subprocess.run(["xset", "dpms", "force", "off" if _screen_is_on() else "on"])


if __name__ == "__main__":
    subprocess.run(["xset", "+dpms"])  # ensure DPMS is enabled before relying on it
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.2)
    button.when_pressed = toggle_screen
    pause()
