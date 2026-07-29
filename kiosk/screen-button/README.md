# Screen on/off button (GPIO17)

Blanks and wakes the 7" HDMI display on a single button press — no full
system shutdown involved, Chromium and the backend keep running the whole
time. The touchscreen is a separate USB HID device, so it stays live
regardless of the display's power state.

This device runs **Wayland with the `labwc` compositor** (confirmed on
hardware: Raspberry Pi OS on Debian 13 trixie) — not X11, so `xset`/DPMS,
matchbox-window-manager, and `vcgencmd display_power` all assume the wrong
stack and don't apply here.

The actual toggle uses **`wlopm`** (`wlr-output-power-management-v1`), not
`wlr-randr`. This distinction matters and was confirmed the hard way:
`wlr-randr --off`/`--on` uses a *different* protocol
(`wlr-output-management-v1`) that fully disables/re-enables the output,
tearing its mode down entirely — turning it off worked, but re-enabling it
reliably failed (`failed to apply configuration`, even with an explicit
`--mode`) and the display stayed dead until a reboot. `wlopm` is the actual
DPMS-equivalent soft on/off (blanks the signal, leaves the mode/config
intact) and round-trips cleanly. `wlr-randr` is still useful for
*identifying* the output name (`HDMI-A-1`, found via its full listing) but
is not used for the actual toggle.

## Wiring

Momentary push button between **GPIO17 (physical pin 11)** and any **GND**
(e.g. pin 9). GPIO14/15 are already reserved for the A7600 modem's UART
(see `kiosk/network/`), so GPIO17 avoids that conflict. No pull resistor
needed — `screen_button.py` enables GPIO17's internal pull-up.

## Setup (fresh device, in order)

Each step below exists because skipping it caused a real failure the first
time this was set up — follow them in order rather than jumping to step 2.

1. **System packages** — `lgpio`'s Python bindings compile a C extension
   that needs both a SWIG-based build tool and the underlying C library's
   dev headers to link against; `wlr-randr` is normally preinstalled on the
   Wayland desktop image but costs nothing to make explicit; `wlopm` (the
   tool actually used for the on/off toggle) is a separate package and not
   preinstalled:
   ```
   sudo apt update
   sudo apt install swig liblgpio-dev wlr-randr wlopm -y
   ```
2. **Python packages**:
   ```
   cd ~/led-kiosk
   venv/bin/pip install -r requirements.txt
   ```
   This installs `gpiozero`, `RPi.GPIO`, and `lgpio` (all Linux-only
   markers, so this step is skipped on non-Pi dev machines). Only `lgpio`
   actually matters at runtime here: on Raspberry Pi OS Bookworm/trixie,
   `gpiozero`'s older `RPi.GPIO` backend fails outright with
   `RuntimeError: Failed to add edge detection` (the newer kernel GPIO
   character-device interface isn't compatible with `RPi.GPIO`'s
   edge-detection mechanism) — `gpiozero` prefers `lgpio` automatically
   once it's installed, which is why step 1 has to happen first, or this
   install fails to build it and silently falls back to the broken
   backend instead.
3. **GPIO group membership** — confirm `pi4` can access GPIO without root:
   ```
   groups pi4   # should list "gpio"; if not: sudo usermod -aG gpio pi4
   ```
   then log out/in (or reboot) for the group change to take effect.
4. **Install the systemd unit** (the shipped file already sets the
   `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR`/`WorkingDirectory` values this
   device needs — see the unit file's own comments for why each one
   matters):
   ```
   sudo cp led-kiosk-screen-button.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now led-kiosk-screen-button
   sudo systemctl status led-kiosk-screen-button   # should be active, no restart loop
   ```

## Verifying

1. Press the button once while the kiosk is showing → the display should
   blank almost immediately. Confirm the backend/browser are still alive
   underneath (`systemctl status led-kiosk-backend`, `ps aux | grep -i chromium`).
2. Press it again → the display should come back showing the live kiosk
   (not a reload).
3. Touch the (still-live) touchscreen, or move a mouse if one's attached,
   while the display is blanked. **This is genuinely unconfirmed on this
   stack** — X11's DPMS had built-in "any input wakes the display"
   behavior; there's no guarantee `labwc` reconnects wake-on-touch to an
   output that was blanked *externally* via `wlopm` the same way. If it
   doesn't wake on its own, the fix is having `screen_button.py` itself
   watch the touchscreen/mouse's evdev device and call
   `wlopm --on HDMI-A-1` on activity while blanked — not implemented yet
   since it may not be necessary; confirm the gap exists first before
   adding it.

If the service is `active` but the button does nothing, check the logs
while pressing it:
```
journalctl -u led-kiosk-screen-button -f
```
and separately confirm the command works when run by hand:
```
wlopm
```
If that errors (e.g. "WAYLAND_DISPLAY is not set" or "failed to connect to
compositor"), the service's `WAYLAND_DISPLAY=wayland-0` /
`XDG_RUNTIME_DIR=/run/user/1000` values don't match this device's actual
session — check `echo $WAYLAND_DISPLAY` and `ls $XDG_RUNTIME_DIR | grep
wayland` from a shell within the real desktop session (e.g. over SSH after
`export XDG_RUNTIME_DIR=/run/user/$(id -u)`) and update the unit file to
match.

**Do not use `wlr-randr --off`/`--on` to debug this** — confirmed on
hardware that it can leave the output stuck disabled with no software
recovery short of a reboot (see the note above on why `wlopm` is used
instead). If the display ever does get stuck off with no response to
`wlopm --on`, `sudo reboot` is the known-working recovery.

If the logs show no error at all when the button is pressed, the daemon
isn't seeing the GPIO event — double check the wiring (GPIO17/pin 11 to
GND) and that `pi4` is in the `gpio` group.
