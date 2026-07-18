# LTE failover (SIMCOM A7600)

Backup link for when the kiosk's WiFi drops. WiFi stays the preferred
default route; LTE only carries traffic when WiFi is down. Failover is
kernel-level via NetworkManager route metrics, not a polling/switching
daemon, so it reacts as fast as the WiFi interface actually goes down.

**Status: written, not yet run against the physical A7600 + Pi.** Review
both scripts before running — the AT port and USB mode-switch command in
`switch_a7600_qmi.sh` in particular are SIMCOM's documented defaults, not
confirmed against this specific unit.

## Steps

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

## Verifying failover

```
nmcli device status                          # wifi and gsm/wwan both "connected"
ip route                                     # default route via the wifi iface
sudo nmcli device disconnect <wifi-iface>    # simulate WiFi loss
ip route                                     # default route now via wwan0
sudo nmcli device connect <wifi-iface>       # restore
```

## Notes

- `/api/status` now also reports which interface currently carries traffic
  (`network_type`: `wifi`/`lte`/`ethernet`/`unknown`), and the kiosk UI
  shows it next to the MQTT status (e.g. "broker conectado · LTE").
- Both links are meant to autoconnect and stay up simultaneously — this is
  not "connect LTE on demand," so expect the LTE radio to always be
  active. That's a fine tradeoff for a mains-powered kiosk.
