# Screen on/off button (GPIO17)

Blanks and wakes the 7" HDMI display on a single button press — no full
system shutdown involved, Chromium and the backend keep running the whole
time. The touchscreen is a separate USB HID device, so it stays live
regardless of the display's power state.

This device runs **Wayland with the `labwc` compositor** (confirmed on
hardware: Raspberry Pi OS on Debian 13 trixie) — not X11. `wlr-randr`
(already installed) is the Wayland-native way to power an output on/off;
`xset`/DPMS, matchbox-window-manager, and `vcgencmd display_power` all
assume the wrong stack for this device and don't apply here.

## Wiring

Momentary push button between **GPIO17 (physical pin 11)** and any **GND**
(e.g. pin 9). GPIO14/15 are already reserved for the A7600 modem's UART
(see `kiosk/network/`), so GPIO17 avoids that conflict. No pull resistor
needed — `screen_button.py` enables GPIO17's internal pull-up.

## Setup

1. `gpiozero`/`RPi.GPIO` are in `requirements.txt` (Linux-only markers, so
   they're skipped on non-Pi dev machines) — a normal
   `venv/bin/pip install -r requirements.txt` on the Pi already covers them.
2. Confirm `pi4` can access GPIO without root — on Raspberry Pi OS this is
   normally granted via the `gpio` group:
   ```
   groups pi4   # should list "gpio"; if not: sudo usermod -aG gpio pi4
   ```
3. Install the systemd unit:
   ```
   sudo cp led-kiosk-screen-button.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now led-kiosk-screen-button
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
   behavior, but there's no guarantee `labwc` reconnects wake-on-touch to
   an output that was turned off *externally* via `wlr-randr` (as opposed
   to via the compositor's own idle timeout, if it has one). If it doesn't
   wake on its own, the fix is having `screen_button.py` itself watch the
   touchscreen/mouse's evdev device and call `wlr-randr --output HDMI-A-1
   --on` on activity while blanked — not implemented yet since it may not
   be necessary; confirm the gap exists first before adding it.

If the service is `active` but the button does nothing, check the logs
while pressing it:
```
journalctl -u led-kiosk-screen-button -f
```
and separately confirm the command works when run by hand:
```
wlr-randr --output HDMI-A-1
```
If that errors (e.g. "failed to connect to compositor"), the service's
`WAYLAND_DISPLAY=wayland-0` / `XDG_RUNTIME_DIR=/run/user/1000` values don't
match this device's actual session — check `echo $WAYLAND_DISPLAY` and
`ls $XDG_RUNTIME_DIR | grep wayland` from a shell within the real desktop
session (e.g. over SSH after `export XDG_RUNTIME_DIR=/run/user/$(id -u)`)
and update the unit file to match.

If the logs show no error at all when the button is pressed, the daemon
isn't seeing the GPIO event — double check the wiring (GPIO17/pin 11 to
GND) and that `pi4` is in the `gpio` group.
