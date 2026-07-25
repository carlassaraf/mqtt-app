#!/bin/bash
# One-time: switch the SIMCOM A7600 from RNDIS mode to QMI mode, which is
# what ModemManager/NetworkManager can actually manage without deprecated
# ppp plumbing.
#
# PID 9001 = QMI, PID 9011 = RNDIS on A76xx-series firmware -- confirmed
# against a physical unit (idVendor=1e0e, idProduct=9011 showed up with an
# rndis_host `usb0` interface plus 4 ttyUSB ports, not qmi_wwan/cdc-wdm).
# An earlier version of this script sent 9011 thinking it was the QMI PID;
# that was backwards. The AT port below is SIMCOM's documented default
# (usually /dev/ttyUSB2), but confirm with `dmesg | grep -i tty` if this
# unit differs. The module reboots itself after this command; QMI mode
# persists across power cycles once set, so this should only need to run
# once.
set -euo pipefail

AT_PORT="${1:-/dev/ttyUSB2}"

if [ ! -e "$AT_PORT" ]; then
  echo "No such port: $AT_PORT." >&2
  echo "Run 'dmesg | grep -i tty' after plugging in the modem to find the" >&2
  echo "right AT port (commonly ttyUSB2 on the A7600), and pass it as an" >&2
  echo "argument: $0 /dev/ttyUSBx" >&2
  exit 1
fi

echo "Sending AT+CUSBPIDSWITCH=9001,1,1 to $AT_PORT (module will reboot)..."
echo -e 'AT+CUSBPIDSWITCH=9001,1,1\r' > "$AT_PORT"
sleep 1
echo "Done. Give the module ~10-15s to reboot, then check 'lsusb' and"
echo "'nmcli device status' for a new gsm/wwan device before running"
echo "setup_lte_failover.sh."
