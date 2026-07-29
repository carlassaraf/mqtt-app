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
3. Touch the (still-live) touchscreen while the display is blanked. X11's
   default DPMS behavior wakes the display on any input event, which would
   fight this button (a stray touch turning the screen back on instead of
   only the button doing it). If that happens, the fix is to have X ignore
   input for DPMS purposes on this touchscreen device, or simplest: leave
   it as expected/acceptable behavior if a touch-to-wake is actually fine
   for how the kiosk will be used — worth deciding with the client after
   testing on the real hardware rather than pre-emptively working around it.

If `xset` reports an error like "unable to open display", double check
`DISPLAY=:0` matches the browser's session and that this service starts
after `led-kiosk-browser.service` (already set via `After=`/`Requires=`
above).
