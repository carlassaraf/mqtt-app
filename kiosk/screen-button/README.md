# Screen on/off button (GPIO17)

Blanks and wakes the 7" HDMI display on a single button press — no full
system shutdown involved, Chromium and the backend keep running the whole
time. The touchscreen is a separate USB HID device, so it stays live
regardless of the display's power state.

Uses X11 DPMS (`xset dpms force off/on`) rather than `vcgencmd
display_power`: DPMS is the driver-agnostic, X11-native way to blank a
display and this kiosk is confirmed running X11 (see
`kiosk/led-kiosk-browser.service`), whereas `vcgencmd display_power` is
known to be unreliable on newer KMS video drivers.

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
   sudo systemctl enable --now led-kiosk-screen-button
   ```

## Verifying

1. Press the button once while the kiosk is showing → the display should
   blank almost immediately. Confirm the backend/browser are still alive
   underneath (`systemctl status led-kiosk-backend led-kiosk-browser`).
2. Press it again → the display should come back showing the live kiosk
   (not a reload).
3. Touch the (still-live) touchscreen, or move a mouse if one's attached,
   while the display is blanked → confirm the display wakes on its own,
   with no button press needed. This is intended: X11's DPMS wakes the
   display on any input event by default, and the `xset +dpms` the script
   runs at startup is the only thing needed for that to work — no extra
   config. The button remains available to manually blank the screen again
   (or wake it) on demand.

If `xset` reports an error like "unable to open display", double check
`DISPLAY=:0` matches the actual X session's display number (usually `:0`
on a Pi with a single auto-login desktop session) and that Raspberry Pi OS
is configured to auto-login to that desktop on boot (`raspi-config` →
System Options → Boot / Auto Login → Desktop Autologin) — this service
only waits for `graphical.target`, not for the kiosk browser specifically,
so it'll start even if the browser is being launched manually rather than
via `led-kiosk-browser.service`.
