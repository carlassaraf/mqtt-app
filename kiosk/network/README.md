# LTE failover (SIMCOM A7600)

Backup link for when the kiosk's WiFi drops. WiFi stays the preferred
default route; LTE only carries traffic when WiFi is down.

There are two ways to wire the A7600 up, and they use different mechanisms
to get that failover behavior:

- **USB, QMI mode** (`switch_a7600_qmi.sh` + `setup_lte_failover.sh`) —
  NetworkManager/ModemManager manage the link, failover is kernel-level via
  route metrics, no daemon involved.
- **UART/GPIO** (`uart-ppp/` + `setup_lte_failover_uart.sh`) — used when USB
  enumeration wasn't usable on this unit. ModemManager can't grab a
  platform-bus tty AT port (see below), so this dials a plain PPP link with
  `pppd` directly and enforces the same route-metric priority via
  `ip-up.d`/`ip-down.d` hooks instead of NetworkManager.

Pick one path — they're alternatives, not both-at-once.

## Status

- **Confirmed against physical hardware:** AT communication over both USB
  (`/dev/ttyUSB2`) and UART (`/dev/serial0` @ 115200) — SIM ready, signal
  present, registered, attached (`AT+CPIN?`, `AT+CSQ`, `AT+CREG?`/`AT+CGATT?`
  all healthy).
- **QMI mode switch (USB path):** the module defaults to (or gets left in)
  RNDIS mode (idVendor=1e0e, idProduct=9011, `rndis_host` + 4 `ttyUSB`
  ports) — QMI mode is actually PID **9001**, not 9011. An earlier version
  of `switch_a7600_qmi.sh` sent 9011 thinking it was the QMI PID; that was
  backwards and has been fixed. Not yet re-tested against hardware since
  the fix.
- **ModemManager cannot manage this module over UART.** Its `generic`
  plugin fails with `Cannot add port 'tty/ttyAMA0', unhandled port type`
  even once the port is correctly tagged for probing
  (`ID_MM_DEVICE_PROCESS`, `ID_MM_PORT_TYPE_AT_PRIMARY`) and successfully
  answers `AT`. This is a known, apparently-unresolved issue for GSM
  modules on Pi UART (not specific to this setup) — see the Raspberry Pi
  forum threads linked in `uart-ppp/`'s git history / this file's prior
  revisions. That's why the UART path bypasses ModemManager/NetworkManager
  entirely and dials PPP directly.
- **`setup_lte_failover.sh` (USB/NetworkManager) and
  `setup_lte_failover_uart.sh` (UART/pppd):** written, not yet run against
  real hardware end-to-end (APN/CGDCONT, dial string, and auth are
  unverified either way).

## Steps — USB / QMI path

1. `sudo ./switch_a7600_qmi.sh [AT_PORT]` — one-time, puts the module in
   QMI mode. Default AT port is `/dev/ttyUSB2`; check `dmesg | grep -i tty`
   after plugging the modem in if that's wrong for this unit.
2. Wait ~15s for the module to reboot, then `lsusb` / `nmcli device status`
   should show a new gsm/wwan device.
3. `sudo ./setup_lte_failover.sh` — installs ModemManager +
   NetworkManager's GSM support if missing, creates the `lte-backup`
   connection (APN `datos.personal.com`, no auth by default — edit the
   script if this SIM needs credentials), and sets route metrics so WiFi
   (metric 100) is preferred over LTE (metric 700).

### Verifying (USB path)

```
nmcli device status                          # wifi and gsm/wwan both "connected"
ip route                                     # default route via the wifi iface
sudo nmcli device disconnect <wifi-iface>    # simulate WiFi loss
ip route                                     # default route now via wwan0
sudo nmcli device connect <wifi-iface>       # restore
```

## Steps — UART / pppd path

Prereqs on the Pi 4: `enable_uart=1` and `dtoverlay=disable-bt` in
`/boot/firmware/config.txt` (frees the good PL011 UART from Bluetooth onto
GPIO14/15), serial console login disabled (`raspi-config` → Interface
Options → Serial Port → login shell: No), and the module wired through a
**3.3V↔1.8V level shifter** (the A7600's UART is 1.8V logic, per SIMCOM's
hardware design guide — do not connect it directly to the Pi's 3.3V GPIO).
Only TX/RX/GND are wired (no RTS/CTS/DCD), which is why the pppd peer
config uses `local`/`nocrtscts`.

1. Confirm AT communication first: `sudo minicom -D /dev/serial0 -b 115200`
   → type `AT` → expect `OK`. Don't proceed until this works.
2. `sudo ./setup_lte_failover_uart.sh` — installs `ppp`, points
   NetworkManager away from managing `/etc/resolv.conf` and writes a
   static one with public resolvers (see "DNS" below), tells
   ModemManager to ignore the port (`ID_MM_DEVICE_IGNORE`, since it can't
   use it anyway and would otherwise contend with pppd for the port),
   installs the chat script + pppd peer config (APN `datos.personal.com` —
   edit `uart-ppp/peers-lte-backup`'s auth lines if this SIM needs
   credentials) and an `lte-backup` systemd service that keeps pppd
   running (`persist`, auto-restart).

### DNS

The pppd peer config does **not** use `usepeerdns`. The carrier's DNS
servers are only reachable over the cellular link itself — accepting them
overwrites `/etc/resolv.conf` and breaks name resolution whenever WiFi
(not LTE) is actually carrying traffic, which is exactly backwards for a
backup link. `setup_lte_failover_uart.sh` instead sets `dns=none` in
`NetworkManager.conf` and writes a static `/etc/resolv.conf` pointing at
public resolvers (1.1.1.1, 8.8.8.8), which resolve correctly over either
uplink.

### Verifying (UART path)

```
systemctl status lte-backup                  # active (running)
ip route                                     # default route via the wifi iface
sudo nmcli device disconnect <wifi-iface>    # simulate WiFi loss
ip route                                     # default route now via ppp0
sudo nmcli device connect <wifi-iface>       # restore
```

If the dial fails, `journalctl -u lte-backup -n 50` or run by hand for
verbose chat output: `sudo pppd call lte-backup nodetach debug`.

## Notes

- `/api/status` also reports which interface currently carries traffic
  (`network_type`: `wifi`/`lte`/`ethernet`/`unknown`), and the kiosk UI
  shows it next to the MQTT status (e.g. "broker conectado · LTE"). Both
  `wwan*` (USB/QMI) and `ppp*` (UART/pppd) are classified as `lte`.
- Both links are meant to autoconnect and stay up simultaneously — this is
  not "connect LTE on demand," so expect the LTE radio to always be
  active. That's a fine tradeoff for a mains-powered kiosk.
