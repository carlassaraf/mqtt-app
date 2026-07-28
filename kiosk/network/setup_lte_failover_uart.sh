#!/bin/bash
# One-time provisioning: dials the SIMCOM A7600 as a plain PPP data link
# over its UART connection (GPIO14/15 -- see uart-ppp/ and the README),
# used only when WiFi is down.
#
# This is the UART counterpart to setup_lte_failover.sh, which targets the
# module wired over USB in QMI mode and managed by NetworkManager +
# ModemManager. Over UART, ModemManager's 'generic' plugin can't grab the
# platform-bus tty AT port -- it fails with "Cannot add port ... unhandled
# port type", a problem also reported (unresolved) by others trying the
# same Pi + UART-modem combination. So this script bypasses
# NetworkManager/ModemManager entirely and dials PPP directly with pppd +
# chat, the classic dial-up-modem approach.
#
# WiFi keeps its NetworkManager-assigned route metric (100, set by
# setup_lte_failover.sh). This script's ip-up/ip-down hooks give the
# resulting ppp0 interface a higher (less preferred) metric, so the kernel
# still prefers WiFi whenever its route exists and only falls through to
# LTE once it's gone -- same failover model as the USB/QMI path, just
# enforced via pppd hooks instead of NetworkManager.
#
# DNS: the pppd peer config deliberately does NOT use `usepeerdns` -- the
# carrier's DNS servers are only reachable over the cellular link itself,
# so accepting them clobbers /etc/resolv.conf and breaks name resolution
# whenever WiFi (not LTE) is actually carrying traffic. Instead this
# script points NetworkManager away from managing resolv.conf (dns=none)
# and writes a static, link-independent resolv.conf using public
# resolvers, which work the same over either uplink.
#
# Prereq: AT communication over the UART port must already work (confirm
# with e.g. `sudo minicom -D /dev/serial0 -b 115200` -> AT -> OK) before
# running this.
#
# Run as root (sudo) on the Pi. UNVERIFIED against real hardware -- AT
# communication over /dev/serial0 has been confirmed manually, but the
# actual PPP dial (APN/CGDCONT, dial string, auth) has not. Review
# uart-ppp/chat-lte-backup.chat and uart-ppp/peers-lte-backup before
# running, especially if this SIM needs PAP/CHAP auth.
set -euo pipefail

APN="datos.personal.com"
LTE_METRIC=700
SERIAL_DEV="/dev/serial0"
BAUD=115200

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/uart-ppp" && pwd)"

if [ ! -e "$SERIAL_DEV" ]; then
  echo "No such device: $SERIAL_DEV -- confirm the UART is enabled" >&2
  echo "(enable_uart=1, dtoverlay=disable-bt in config.txt) before running this." >&2
  exit 1
fi

echo "Installing pppd + chat..."
apt-get update
apt-get install -y ppp

echo "Pointing NetworkManager away from resolv.conf management (dns=none)..."
NM_CONF=/etc/NetworkManager/NetworkManager.conf
if grep -q '^\[main\]' "$NM_CONF"; then
  if grep -q '^dns=' "$NM_CONF"; then
    sed -i 's/^dns=.*/dns=none/' "$NM_CONF"
  else
    sed -i '/^\[main\]/a dns=none' "$NM_CONF"
  fi
else
  printf '\n[main]\ndns=none\n' >> "$NM_CONF"
fi

echo "Writing static, link-independent resolv.conf (public resolvers)..."
cat > /etc/resolv.conf <<'EOF'
nameserver 1.1.1.1
nameserver 8.8.8.8
EOF

echo "Restarting NetworkManager to apply dns=none (brief WiFi blip expected)..."
systemctl restart NetworkManager

echo "Telling ModemManager to leave ${SERIAL_DEV} alone (pppd needs the port, not MM)..."
cat > /etc/udev/rules.d/78-mm-custom-uart.rules <<'EOF'
ACTION!="add|change|move|bind", GOTO="mm_custom_end"
SUBSYSTEM=="tty", KERNEL=="ttyAMA0", ENV{ID_MM_DEVICE_IGNORE}="1"
LABEL="mm_custom_end"
EOF
udevadm control --reload
udevadm trigger --action=add --subsystem-match=tty
systemctl restart ModemManager 2>/dev/null || true

echo "Installing chat script (APN ${APN})..."
mkdir -p /etc/chatscripts
sed "s/__APN__/${APN}/" "${SRC_DIR}/chat-lte-backup.chat" > /etc/chatscripts/lte-backup.chat

echo "Installing pppd peer config (${SERIAL_DEV} @ ${BAUD} baud)..."
sed -e "s#__SERIAL_DEV__#${SERIAL_DEV}#" -e "s/__BAUD__/${BAUD}/" \
  "${SRC_DIR}/peers-lte-backup" > /etc/ppp/peers/lte-backup

echo "Installing ip-up/ip-down route-metric hooks (LTE metric ${LTE_METRIC})..."
mkdir -p /etc/ppp/ip-up.d /etc/ppp/ip-down.d
sed "s/__LTE_METRIC__/${LTE_METRIC}/" "${SRC_DIR}/ip-up-lte-metric" > /etc/ppp/ip-up.d/10-lte-metric
sed "s/__LTE_METRIC__/${LTE_METRIC}/" "${SRC_DIR}/ip-down-lte-metric" > /etc/ppp/ip-down.d/10-lte-metric
chmod +x /etc/ppp/ip-up.d/10-lte-metric /etc/ppp/ip-down.d/10-lte-metric

echo "Installing lte-backup systemd service..."
cp "${SRC_DIR}/lte-backup.service" /etc/systemd/system/lte-backup.service
systemctl daemon-reload
systemctl enable --now lte-backup.service

echo
echo "Done. Check with: systemctl status lte-backup ; ip route"
echo "First dial can take 10-30s (network registration + PPP negotiation)."
echo "If it fails: journalctl -u lte-backup -n 50, or run by hand for"
echo "verbose chat output: sudo pppd call lte-backup nodetach debug"
