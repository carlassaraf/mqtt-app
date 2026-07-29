"""Determines which network interface currently carries the default route,
so the kiosk can show whether it's on WiFi or the LTE failover link.
"""
import re
import subprocess

from app import sms

_IFACE_PATTERNS = [
    (re.compile(r"^wlan"), "wifi"),
    (re.compile(r"^wwan"), "lte"),  # USB/QMI path (setup_lte_failover.sh)
    (re.compile(r"^ppp"), "lte"),  # UART/pppd path (setup_lte_failover_uart.sh)
    (re.compile(r"^eth"), "ethernet"),
]


def get_active_interface() -> dict:
    """Returns {"interface": str|None, "type": str}. type is one of
    "wifi", "lte", "ethernet", "unknown" (no default route, or the `ip`
    command isn't available -- e.g. when running off the target device).
    """
    try:
        out = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {"interface": None, "type": "unknown"}

    match = re.search(r"\bdev (\S+)", out)
    if not match:
        return {"interface": None, "type": "unknown"}

    iface = match.group(1)
    for pattern, kind in _IFACE_PATTERNS:
        if pattern.match(iface):
            return {"interface": iface, "type": kind}
    return {"interface": iface, "type": "unknown"}


_last_type: str | None = None


def check_interface_change() -> None:
    """Polls the active interface and SMS's if the type changed since the
    last poll (see scheduler.start(), which runs this on an interval). The
    first call after startup only records the baseline without notifying --
    otherwise every app restart would fire a spurious "changed to X" text
    for whatever interface just happens to be active at boot."""
    global _last_type
    current = get_active_interface()["type"]
    if _last_type is not None and current != _last_type:
        sms.send_sms(f"Kiosk: cambio de red de {_last_type} a {current}")
    _last_type = current
