# Physical power button (GPIO3)

Lets the client shut the Pi down and turn it back on with one physical
button, no daemon/service required — Raspberry Pi 4 has GPIO3 (physical pin
5) wired into the SoC's always-on power-management block specifically for
this. It's the one GPIO that works both directions:

- Press while running → clean shutdown (equivalent to `shutdown -h now`).
- Press again while halted (mains power still applied) → boots back up.

This is independent of the in-app power button (`app/routes/system.py`
`shutdown_system()`) — that one lets the client shut down from the kiosk UI
without reaching for the physical button; this one is the only way back on
once it's off.

## Wiring

Momentary push button between **GPIO3 (pin 5)** and any **GND** pin (e.g.
pin 6, physically adjacent). No pull resistor needed — GPIO3 has an internal
pull-up enabled by the overlay below.

## Setup

Add to `/boot/firmware/config.txt` (Raspberry Pi OS Bookworm and newer) or
`/boot/config.txt` (older releases):

```
dtoverlay=gpio-shutdown
```

Reboot once for it to take effect (`sudo reboot`).

## Verifying

1. While the kiosk is running, press the button → the Pi should shut down
   cleanly within a few seconds (check with `journalctl -b -1` after next
   boot, or just watch the display/HDMI go blank).
2. While halted (power light steady, nothing on screen), press the button
   again → it should power back on and boot normally.

If step 1 doesn't shut the board down, double check the overlay line landed
in the config.txt that's actually being read (`/boot/firmware/` vs `/boot/`
depends on OS version — `cat /proc/cmdline` or `raspi-config` can confirm
which one is live) and that pin 5 is genuinely GPIO3 and not a neighboring
pin (see `pinout` command on-device).
