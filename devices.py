"""Device registry loaded from device.yaml, plus the startup GitHub update check."""
import hashlib
import os
import sys
import threading
import urllib.request

import yaml
from PyQt6.QtCore import QObject, pyqtSignal

DEVICE_YAML_NAME = "device.yaml"
# shortcut: GitHub repo is still named 3DSC2; flip this path (and README links) when it is renamed.
DEVICE_YAML_URL = "https://raw.githubusercontent.com/cylin577/3DSC2/main/device.yaml"
UPDATE_TIMEOUT_S = 5


def local_device_yaml_path():
    """Writable copy: repo root in dev, next to the exe in a PyInstaller build."""
    # shortcut: exe-dir must be writable or updates silently no-op; upgrade to a per-user config dir
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, DEVICE_YAML_NAME)


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def valid_device_data(data):
    """True when data is a registry the app can build its UI and ROIs from."""
    if not isinstance(data, dict):
        return False
    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        return False
    for device in devices:
        if not isinstance(device, dict):
            return False
        if not device.get("id") or not device.get("friendly_name"):
            return False
        screens = device.get("screens")
        if not isinstance(screens, list) or not screens:
            return False
        for screen in screens:
            if not isinstance(screen, dict):
                return False
            # bool is an int subclass: width: true would validate as 1 pixel wide.
            for key in ("width", "height"):
                value = screen.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    return False
    default = data.get("default_device")
    if default is not None and default not in {d["id"] for d in devices}:
        return False
    return True


def _bundled_yaml_bytes():
    bundled = getattr(sys, "_MEIPASS", None)
    if not bundled:
        return None
    return _read_bytes(os.path.join(bundled, DEVICE_YAML_NAME))


def _parse_or_none(raw):
    """Parsed data, or None when the bytes are not YAML at all."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return None


def load_devices():
    """Prefer the writable local copy; fall back to the copy frozen into the exe.
    A local file that is unparsable or fails valid_device_data (e.g. one a user
    hand-edited) is replaced by the bundled copy; if both are bad, this fails
    loudly instead of handing KeyErrors down to uc2.py."""
    local = _read_bytes(local_device_yaml_path())
    if local is not None:
        data = _parse_or_none(local)
        if valid_device_data(data):
            return data
        fallback = _bundled_yaml_bytes()
        if fallback is not None:
            bundled = _parse_or_none(fallback)
            if valid_device_data(bundled):
                return bundled
        raise ValueError(
            f"{DEVICE_YAML_NAME} is unparsable or invalid, and the bundled copy is no better")
    raw = _bundled_yaml_bytes()
    if raw is None:
        raise FileNotFoundError(f"{DEVICE_YAML_NAME} not found next to the app")
    data = _parse_or_none(raw)
    if not valid_device_data(data):
        raise ValueError(f"bundled {DEVICE_YAML_NAME} is unparsable or invalid")
    return data


def device_by_id(data, device_id):
    for device in data["devices"]:
        if device["id"] == device_id:
            return device
    return None


def default_device_id(data):
    return data.get("default_device") or data["devices"][0]["id"]


def screen_layout(device):
    """Fill each screen's x/y: screens stack vertically, horizontally centered.
    Reproduces the old 3DS geometry exactly: 400x240 top at (0,0), 320x240 bottom
    at (40,240), canvas 400x480."""
    screens = device["screens"]
    width = max(s["width"] for s in screens)
    y = 0
    for screen in screens:
        screen["x"] = (width - screen["width"]) // 2
        screen["y"] = y
        y += screen["height"]
    return width, y


def replace_device_yaml_if_changed():
    """Replace the local file when its sha256 differs from GitHub's; atomic write.
    Returns the new bytes, or None when unchanged or on any failure (offline stays silent).
    A remote file that fails validation is never written, so one bad upstream commit
    cannot leave the local copy unbootable."""
    try:
        with urllib.request.urlopen(DEVICE_YAML_URL, timeout=UPDATE_TIMEOUT_S) as resp:
            remote = resp.read()
        local = _read_bytes(local_device_yaml_path())
        if local is not None and hashlib.sha256(local).digest() == hashlib.sha256(remote).digest():
            return None
        if not valid_device_data(yaml.safe_load(remote)):
            return None
        path = local_device_yaml_path()
        with open(path + ".tmp", "wb") as f:
            f.write(remote)
        os.replace(path + ".tmp", path)
        return remote
    except Exception:
        return None


class DeviceUpdateSignals(QObject):
    updated = pyqtSignal(str)


def start_update_check(signals):
    def worker():
        if replace_device_yaml_if_changed() is not None:
            signals.updated.emit("device.yaml updated from GitHub (applies on next start)")
    threading.Thread(target=worker, daemon=True).start()
