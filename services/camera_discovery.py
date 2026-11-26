"""Discover Paparazzi ESP32 cameras advertised on the local network via mDNS."""
from __future__ import annotations

import ipaddress
import threading
import time

SERVICE_TYPE = "_paparazzi-camera._tcp.local."

try:
    from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf
except ImportError:  # Keep the rest of Paparazzi usable without discovery installed.
    IPVersion = ServiceBrowser = ServiceListener = Zeroconf = None

_ListenerBase = ServiceListener or object


class _CameraListener(_ListenerBase):
    def __init__(self, zeroconf):
        self.zeroconf = zeroconf
        self.lock = threading.Lock()
        self.cameras: dict[tuple[str, str], dict] = {}

    def add_service(self, zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name, timeout=1200)
        if info is None:
            return
        raw_id = info.properties.get(b"camera_id")
        camera_id = raw_id.decode("utf-8", "replace") if raw_id else (info.server or name).split(".")[0]
        for raw_address in info.addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError:
                continue
            if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
                continue
            item = {"camera_id": camera_id, "address": str(address)}
            with self.lock:
                self.cameras[(camera_id, str(address))] = item

    def remove_service(self, _zeroconf, _service_type: str, _name: str) -> None:
        return

    def update_service(self, zeroconf, service_type: str, name: str) -> None:
        self.add_service(zeroconf, service_type, name)


def discover_camera(timeout_seconds: float = 3.0) -> dict:
    """Return the single discovered camera or a teacher-readable setup error."""
    if Zeroconf is None:
        raise ValueError(
            "Camera discovery support is not installed in this Python environment. "
            "Run '.venv-1\\Scripts\\python.exe -m pip install -r requirements-local.txt' "
            "from the Paparazzi folder, then restart the app."
        )
    timeout_seconds = min(max(float(timeout_seconds), 1.0), 8.0)
    try:
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = _CameraListener(zeroconf)
        browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener=listener)
        try:
            time.sleep(timeout_seconds)
        finally:
            browser.cancel()
            zeroconf.close()
    except (OSError, RuntimeError) as error:
        raise ValueError(
            "Camera discovery could not access the local network. Allow Paparazzi through the computer firewall and try again."
        ) from error

    with listener.lock:
        cameras = list(listener.cameras.values())
    if not cameras:
        raise ValueError(
            "No Paparazzi camera was found. Power it on, finish its Wi-Fi setup, and make sure this computer and camera are on the same network."
        )
    if len(cameras) > 1:
        names = ", ".join(sorted({item["camera_id"] for item in cameras}))
        raise ValueError(f"More than one Paparazzi camera was found ({names}). Temporarily power off the other cameras and try again.")
    return cameras[0]
