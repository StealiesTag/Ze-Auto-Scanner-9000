from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from extensions import db
from models.camera_setup import CameraSetup


def _is_local_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback or address.is_link_local:
        return True
    networks = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"))
    return any(address.version == network.version and address in network for network in networks)


def validate_setup(camera_id: str, stream_url: str, capture_url: str) -> tuple[str, str, str]:
    camera_id = (camera_id or "").strip()
    if not camera_id or len(camera_id) > 100:
        raise ValueError("Camera ID must contain 1 to 100 characters.")
    parsed_stream = urlsplit((stream_url or "").strip())
    parsed_capture = urlsplit((capture_url or "").strip())
    if any(url.scheme != "http" or not url.hostname or url.username or url.password or url.query or url.fragment for url in (parsed_stream, parsed_capture)):
        raise ValueError("Use local HTTP camera URLs without credentials, queries, or fragments.")
    try:
        stream_ip = ipaddress.ip_address(parsed_stream.hostname)
        capture_ip = ipaddress.ip_address(parsed_capture.hostname)
        stream_port, capture_port = parsed_stream.port or 80, parsed_capture.port or 80
    except ValueError as error:
        raise ValueError("Camera URLs must use a private or local IP address with a valid port.") from error
    if any(not _is_local_address(ip) or ip.is_multicast or ip.is_unspecified for ip in (stream_ip, capture_ip)):
        raise ValueError("Camera URLs must target a private, loopback, or link-local device address.")
    if stream_ip != capture_ip:
        raise ValueError("The stream and still-capture URLs must use the same camera address.")
    if any(len(url.geturl()) > 500 for url in (parsed_stream, parsed_capture)):
        raise ValueError("Camera URLs must be 500 characters or fewer.")
    if any(not 1 <= port <= 65535 for port in (stream_port, capture_port)):
        raise ValueError("Camera URL ports must be between 1 and 65535.")
    return camera_id, parsed_stream.geturl(), parsed_capture.geturl()


def get_camera_setup(config: dict) -> dict:
    saved = db.session.get(CameraSetup, 1)
    if saved:
        return {"camera_id": saved.camera_id, "stream_url": saved.stream_url,
                "capture_url": saved.capture_url, "timeout_seconds": saved.timeout_seconds}
    return {"camera_id": config.get("PAPARAZZI_CAMERA_ID", "paparazzi-esp32-01"),
            "stream_url": config.get("ESP32_STREAM_URL", ""),
            "capture_url": config.get("ESP32_CAPTURE_URL", ""),
            "timeout_seconds": float(config.get("ESP32_REQUEST_TIMEOUT_SECONDS", 8))}


def save_camera_setup(camera_id: str, stream_url: str, capture_url: str, timeout_seconds: float = 8) -> CameraSetup:
    camera_id, stream_url, capture_url = validate_setup(camera_id, stream_url, capture_url)
    try:
        timeout_seconds = float(timeout_seconds)
    except (TypeError, ValueError) as error:
        raise ValueError("Camera timeout must be a number between 1 and 30 seconds.") from error
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("Camera timeout must be between 1 and 30 seconds.")
    setup = db.session.get(CameraSetup, 1)
    if setup is None:
        setup = CameraSetup(id=1, camera_id=camera_id, stream_url=stream_url, capture_url=capture_url, timeout_seconds=timeout_seconds)
        db.session.add(setup)
    else:
        setup.camera_id, setup.stream_url, setup.capture_url = camera_id, stream_url, capture_url
        setup.timeout_seconds = timeout_seconds
    db.session.commit()
    return setup
