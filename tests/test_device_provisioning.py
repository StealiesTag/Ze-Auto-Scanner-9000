import json
import socket
from types import SimpleNamespace

import pytest
import serial

from app import create_app
from config import config
from routes.device_routes import token_for
from services import device_provisioning as usb


@pytest.fixture
def settings():
    return {"ssid": "Classroom", "password": "example-pass", "device_name": "Camera A",
            "capture_interval_ms": 1000, "jpeg_quality": 20, "resolution": "SVGA",
            "server_url": "http://192.168.1.10:5000/api/camera/ingest"}


@pytest.mark.parametrize(("key", "value"), [
    ("ssid", "é" * 17), ("password", "short"), ("password", "abcd\nefgh"),
    ("capture_interval_ms", True), ("jpeg_quality", 41), ("resolution", "UXGA"),
    ("server_url", "http://127.0.0.1:5000/api/camera/ingest"),
    ("server_url", "http://192.168.1.10:0/api/camera/ingest"),
])
def test_validation(settings, key, value):
    assert usb.validate_config(settings) == settings
    with pytest.raises(ValueError):
        usb.validate_config({**settings, key: value})


@pytest.mark.parametrize(("reason", "expected"), [
    (2, "authentication timed out or expired"),
    (201, "could not see that Wi-Fi network"),
    (202, "rejected Wi-Fi authentication"),
    (15, "security handshake did not complete"),
    (999, "reason 999"),
])
def test_wifi_failure_message_uses_firmware_reason(reason, expected):
    message = usb.wifi_failure_message({"wifi_reason": reason, "wifi_status": 6})
    assert expected.lower() in message.lower()
    assert "password" not in message.lower() or reason in {2, 202}


class MockLink:
    def __init__(self, **options):
        self.closed = False
        self.options = options
        self.lines = []

    def open(self):
        pass

    def close(self):
        self.closed = True

    def write(self, payload):
        request = json.loads(payload)
        response = {
            "request_id": request["request_id"], "success": True,
            "result": {"device_id": "paparazzi-abcdef", "product": "Paparazzi", "protocol_version": 1},
        }
        line = json.dumps(response).encode() + b"\n"
        self.lines.extend([b"boot message\n", b"{malformed\n",
                           b'{"request_id":"older","success":true}\n',
                           b"x" * usb.LIMIT, b"remainder\n", line[:12], line[12:]])

    def read_until(self, *_):
        return self.lines.pop(0)


def test_handshake_matches_request_and_closes_removed_port(monkeypatch):
    manager = usb.USBDevices()
    link = MockLink()
    monkeypatch.setattr(serial, "Serial", lambda **kwargs: link)
    monkeypatch.setattr(manager, "ports", lambda: [{"port": "COM11"}])
    assert manager.connect("COM11")["protocol_version"] == 1
    assert manager.connected()[0]["usb_connected"]
    monkeypatch.setattr(manager, "ports", lambda: [])
    assert manager.connected() == []
    assert link.closed


def test_unplug_and_timeout_release_port(monkeypatch):
    manager = usb.USBDevices()
    link = MockLink()
    manager.devices["camera"] = link
    def unplug(_):
        raise serial.SerialException
    monkeypatch.setattr(link, "write", unplug)
    with pytest.raises(usb.DeviceError, match="interrupted"):
        manager.command("camera", "PING")
    assert link.closed
    link = MockLink()
    clock = iter([0, 60])
    monkeypatch.setattr(usb.time, "monotonic", lambda: next(clock))
    with pytest.raises(usb.DeviceError, match="did not reply"):
        usb.USBDevices._exchange(link, "PING")


def test_lan_selection_prefers_routed_physical_network(monkeypatch):
    import psutil
    class Route:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def connect(self, peer): assert peer == ("192.168.2.123", 9)
        def getsockname(self): return ("192.168.2.5", 0)

    monkeypatch.setattr(socket, "socket", lambda *_: Route())
    interfaces = {"Wi-Fi": "192.168.1.5", "Ethernet": "192.168.2.5", "vEthernet": "172.20.0.1", "Down": "10.0.0.1"}
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {name: SimpleNamespace(isup=name != "Down") for name in interfaces})
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        name: [SimpleNamespace(family=socket.AF_INET, address=address)] for name, address in interfaces.items()
    })
    assert [item["address"] for item in usb.lan_addresses("192.168.2.123")] == ["192.168.2.5", "192.168.1.5"]


def test_device_routes_scope_csrf_redaction_and_wireless_status(tmp_path, monkeypatch):
    device_id = "paparazzi-abcdef"
    monkeypatch.setattr(config["testing"], "SQLALCHEMY_DATABASE_URI", f"sqlite:///{(tmp_path / 'devices.db').as_posix()}")
    app = create_app("testing")
    app.config.update(WTF_CSRF_ENABLED=True, PAPARAZZI_TEACHER_PASSWORD="")
    manager = app.extensions["usb_devices"]
    monkeypatch.setattr(manager, "connect", lambda port: {"device_id": device_id, "password": "secret"})
    monkeypatch.setattr(manager, "connected", lambda: [])
    client = app.test_client()
    assert client.get("/devices", environ_base={"REMOTE_ADDR": "192.168.1.9"}).status_code == 403
    assert client.get("/devices", base_url="http://foreign.example").status_code == 403
    page = client.get("/devices")
    assert page.status_code == 200 and b'id="camera-settings"' in page.data
    assert client.post("/api/devices/connect", json={"port": "COM2"}).status_code == 400
    import re
    csrf_token = re.search(rb'data-csrf="([^"]+)"', page.data)[1].decode()
    headers = {"X-CSRFToken": csrf_token}
    assert client.post("/api/devices/connect", json={"port": "COM2"}, headers=headers).json == {"device_id": device_id}
    assert client.post(f"/api/devices/{device_id}/command", json={"command": "FACTORY_RESET"}, headers=headers).status_code == 400
    with app.app_context():
        token = token_for(device_id)
    endpoint = "/api/devices/heartbeat"
    assert client.post(endpoint, json={"device_id": device_id}).status_code == 401
    heartbeat_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(endpoint, json={"device_id": device_id, "probe": True}, headers=heartbeat_headers).status_code == 200
    assert client.get("/api/devices").json["wireless"] == []
    assert client.post(endpoint, json={"device_id": device_id, "camera_ready": True}, headers=heartbeat_headers).status_code == 200
    status = client.get("/api/devices").json
    assert status["wireless"][0]["online"] is True
    assert status["usb"] == []
