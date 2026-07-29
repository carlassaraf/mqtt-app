"""
Sends SMS notifications over the SIMCOM A7600's AT command port, so the
client hears about MQTT broker connectivity and network interface changes
without anyone watching the kiosk screen.

Recipient numbers come from the SMS_NOTIFY_NUMBERS env var (comma
separated, e.g. "+5491122334455,+5491166778899") rather than config.json,
so they never end up committed to the repo. See kiosk/led-kiosk-backend.service
for how that env var reaches the running app on the Pi.

Hardcoded to the UART wiring (kiosk/network/uart-ppp/: /dev/serial0 @
115200) -- that's the path actually deployed on this device, per
kiosk/network/README.md. If this device is ever moved to the USB/QMI path
instead, _PORT/_BAUDRATE need to change to the ttyUSB AT port.

Port contention with lte-backup: on this wiring there's only one AT port,
and it's also the PPP data link pppd holds open while `lte-backup` runs
(which, per that service's own design, is meant to stay up continuously as
a backup route -- see kiosk/network/README.md). Opening the same port for
AT+CMGS while pppd is mid-session would race pppd's own reads/writes on the
wire, which can corrupt both the SMS command sequence and the live PPP
link. So when lte-backup is active, this stops it, sends, then restarts it
(a few seconds' connectivity blip -- acceptable for how rarely this fires,
and far better than corrupting the link). Requires the passwordless sudo
rule documented in kiosk/network/README.md.
"""
import logging
import os
import subprocess
import threading
import time

import serial

logger = logging.getLogger("sms")

_NUMBERS_ENV = "SMS_NOTIFY_NUMBERS"
_PORT = "/dev/serial0"
_BAUDRATE = 115200
_LTE_SERVICE = "lte-backup"
_CTRL_Z = "\x1a"
_AT_TIMEOUT_S = 10


def _numbers() -> list[str]:
    raw = os.environ.get(_NUMBERS_ENV, "")
    return [n.strip() for n in raw.split(",") if n.strip()]


def _lte_backup_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", _LTE_SERVICE], timeout=5
    )
    return result.returncode == 0


def _lte_backup_ctl(action: str):
    subprocess.run(
        ["sudo", "systemctl", action, _LTE_SERVICE], timeout=15, check=False
    )


def _send_one(ser: serial.Serial, number: str, message: str):
    ser.reset_input_buffer()
    ser.write(b"AT+CMGF=1\r")  # text mode, not PDU
    time.sleep(0.3)
    ser.read(ser.in_waiting or 1)
    ser.write(f'AT+CMGS="{number}"\r'.encode())
    time.sleep(0.3)
    ser.write(message.encode() + _CTRL_Z.encode())

    deadline = time.monotonic() + _AT_TIMEOUT_S
    buf = b""
    while time.monotonic() < deadline:
        buf += ser.read(ser.in_waiting or 1)
        if b"OK" in buf or b"ERROR" in buf:
            break
        time.sleep(0.2)

    if b"OK" in buf:
        logger.info("SMS sent to %s", number)
    else:
        logger.error("SMS to %s failed: %r", number, buf)


def _send_over_port(message: str, numbers: list[str]):
    try:
        with serial.Serial(_PORT, _BAUDRATE, timeout=1) as ser:
            for number in numbers:
                _send_one(ser, number, message)
    except (serial.SerialException, OSError) as e:
        logger.error("Could not open modem AT port %s: %s", _PORT, e)


def _send_now(message: str):
    numbers = _numbers()
    if not numbers:
        logger.warning("%s not set, skipping SMS: %s", _NUMBERS_ENV, message)
        return

    lte_was_active = _lte_backup_active()
    if lte_was_active:
        logger.info(
            "%s is up, stopping it to free %s for SMS", _LTE_SERVICE, _PORT
        )
        _lte_backup_ctl("stop")
        time.sleep(1)  # let pppd release the port

    try:
        _send_over_port(message, numbers)
    finally:
        if lte_was_active:
            _lte_backup_ctl("start")


def send_sms(message: str):
    """Fire-and-forget: sends message to every number in SMS_NOTIFY_NUMBERS
    on a background thread. AT+CMGS blocks on the serial port for a few
    seconds (longer if lte-backup needs to be stopped/restarted first), and
    callers here are paho's network thread / APScheduler's worker thread --
    neither should stall on that."""
    threading.Thread(target=_send_now, args=(message,), daemon=True).start()
