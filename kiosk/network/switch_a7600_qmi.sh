#!/bin/bash
# One-time: switch the SIMCOM A7600 from its default PPP-style USB
# composition to QMI mode, which is what ModemManager/NetworkManager can
# actually manage without deprecated ppp plumbing.
#
# UNVERIFIED against the real module -- the AT command and port below are
# SIMCOM's documented default (AT+CUSBPIDSWITCH=9011,1,1 on the AT port,
# usually /dev/ttyUSB2), but confirm against this specific A7600 unit's AT
# command manual and `dmesg | grep -i tty` output before trusting it
# blindly. The module reboots itself after this command; QMI mode persists
# across power cycles once set, so this should only need to run once.
set -euo pipefail

AT_PORT="${1:-/dev/ttyUSB2}"

if [ ! -e "$AT_PORT" ]; then
  echo "No such port: $AT_PORT." >&2
  echo "Run 'dmesg | grep -i tty' after plugging in the modem to find the" >&2
  echo "right AT port (commonly ttyUSB2 on the A7600), and pass it as an" >&2
  echo "argument: $0 /dev/ttyUSBx" >&2
  exit 1
fi

echo "Sending AT+CUSBPIDSWITCH=9011,1,1 to $AT_PORT (module will reboot)..."
echo -e 'AT+CUSBPIDSWITCH=9011,1,1\r' > "$AT_PORT"
sleep 1
echo "Done. Give the module ~10-15s to reboot, then check 'lsusb' and"
echo "'nmcli device status' for a new gsm/wwan device before running"
echo "setup_lte_failover.sh."
