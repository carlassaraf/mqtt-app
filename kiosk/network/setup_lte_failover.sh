#!/bin/bash
# One-time provisioning: adds the SIMCOM A7600 as a NetworkManager-managed
# LTE backup link, kept as a standby default route behind WiFi.
#
# Failover is kernel-level via route metrics, not a polling/switching
# daemon: both links autoconnect and stay up, WiFi's route just has a
# lower (preferred) metric. If the WiFi interface goes down, its route
# disappears from the routing table and the kernel falls through to the
# LTE route immediately -- no NetworkManager decision-making in the loop.
#
# Prereq: the A7600 must already be in QMI mode -- run switch_a7600_qmi.sh
# first if `nmcli device status` doesn't show a gsm/wwan device.
#
# Run as root (sudo) on the Pi itself. UNVERIFIED against real hardware --
# review before running, especially the route-metric values.
set -euo pipefail

APN="datos.personal.com"
# Uncomment and fill in only if the carrier requires auth on this SIM
# (Personal AR typically doesn't):
# GSM_USER="..."
# GSM_PASS="..."

WIFI_METRIC=100   # lower metric == preferred route
LTE_METRIC=700

echo "Installing ModemManager + NetworkManager GSM/QMI support..."
apt-get update
apt-get install -y modemmanager network-manager libqmi-utils usb-modeswitch

echo "Waiting for ModemManager to see the modem..."
sleep 3
mmcli -L || {
  echo "No modem detected by ModemManager -- is the A7600 in QMI mode?" >&2
  echo "See switch_a7600_qmi.sh." >&2
  exit 1
}

echo "Creating LTE backup connection (lte-backup, APN ${APN})..."
if nmcli -t -f NAME connection show | grep -qx "lte-backup"; then
  nmcli connection modify lte-backup gsm.apn "$APN"
else
  nmcli connection add type gsm ifname "*" con-name lte-backup apn "$APN"
fi

if [ -n "${GSM_USER:-}" ]; then
  nmcli connection modify lte-backup gsm.username "$GSM_USER"
fi
if [ -n "${GSM_PASS:-}" ]; then
  nmcli connection modify lte-backup gsm.password "$GSM_PASS"
fi

nmcli connection modify lte-backup \
  connection.autoconnect yes \
  connection.autoconnect-priority 0 \
  ipv4.route-metric "$LTE_METRIC" \
  ipv6.route-metric "$LTE_METRIC"

echo "Setting WiFi route metric as a device-type default (${WIFI_METRIC})..."
# A device-type default in conf.d, not a specific connection profile: this
# kiosk may be pointed at different SSIDs over its lifetime (home network,
# phone hotspot, site WiFi, ...), and whichever one is actually active
# needs to win over LTE. Modifying a single detected profile (the old
# approach here) silently stops working the moment a different network is
# used instead.
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-wifi-metric.conf <<EOF
[connection-wifi-metric]
match-device=type:wifi
ipv4.route-metric=${WIFI_METRIC}
ipv6.route-metric=${WIFI_METRIC}
EOF
systemctl restart NetworkManager

echo "Bringing lte-backup up..."
nmcli connection up lte-backup || {
  echo "lte-backup didn't come up -- check 'journalctl -u ModemManager'" >&2
  echo "and 'nmcli device status'." >&2
}

echo
echo "Done. Both links should now autoconnect and stay up; the kernel picks"
echo "whichever has the lower route metric (WiFi=$WIFI_METRIC, LTE=$LTE_METRIC),"
echo "so losing WiFi should fail over to LTE automatically."
echo "Verify with: nmcli device status ; ip route"
