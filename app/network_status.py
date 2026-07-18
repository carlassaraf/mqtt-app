"""Determines which network interface currently carries the default route,
so the kiosk can show whether it's on WiFi or the LTE failover link.
"""
import re
import subprocess

_IFACE_PATTERNS = [
    (re.compile(r"^wlan"), "wifi"),
    (re.compile(r"^wwan"), "lte"),
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
