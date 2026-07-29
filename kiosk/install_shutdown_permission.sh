#!/bin/bash
# Lets the unprivileged kiosk user (pi4) run `shutdown -h now` without a
# password prompt, scoped to exactly that command -- not a blanket sudo
# grant. Needed by both the in-app power button (app/routes/system.py
# shutdown_system()) and, indirectly, the physical GPIO button (which uses
# the kernel's own gpio-shutdown overlay and doesn't go through this user at
# all, but is documented alongside this script in kiosk/power-button/).
#
# Run once by hand during device setup: sudo ./install_shutdown_permission.sh
# [username]  (defaults to pi4, the user the systemd services run as)
# Safe to re-run -- does nothing if the rule is already installed.
set -e

KIOSK_USER="${1:-pi4}"
SHUTDOWN_BIN="$(command -v shutdown)"
SUDOERS_FILE="/etc/sudoers.d/led-kiosk-shutdown"
RULE="${KIOSK_USER} ALL=(root) NOPASSWD: ${SHUTDOWN_BIN} -h now"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo." >&2
  exit 1
fi

if [ -f "$SUDOERS_FILE" ] && grep -qF "$RULE" "$SUDOERS_FILE"; then
  echo "==> already installed, nothing to do"
  exit 0
fi

TMP_FILE="$(mktemp)"
echo "$RULE" > "$TMP_FILE"

echo "==> validating sudoers syntax"
visudo -c -f "$TMP_FILE"

echo "==> installing $SUDOERS_FILE"
install -m 0440 "$TMP_FILE" "$SUDOERS_FILE"
rm -f "$TMP_FILE"

echo "==> done: $KIOSK_USER can now run '${SHUTDOWN_BIN} -h now' without a password"
