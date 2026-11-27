"""USB provisioning protocol and host network selection."""
import ipaddress
import json
import socket
import threading
import time
import uuid

from urllib.parse import urlsplit

LIMIT = 2048
VERSION = 1
RESOLUTIONS = ("VGA", "SVGA")
LAN_NETWORKS = tuple(ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def is_lan(address):
    return any(address in network for network in LAN_NETWORKS)


class DeviceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def wifi_failure_message(result):
    """Translate ESP-IDF station-disconnect reasons without exposing credentials."""
    result = result if isinstance(result, dict) else {}
    reason = result.get("wifi_reason")
    messages = {
        2: "The access point authentication timed out or expired before connection completed. This can be caused by a missing saved password or router security negotiation.",
        4: "The access point did not complete the Wi-Fi association.",
        15: "The Wi-Fi security handshake did not complete. Check router security compatibility.",
        200: "The ESP32 lost the access point beacon during connection.",
        201: "The ESP32 could not see that Wi-Fi network. Check its 2.4 GHz band, channel, and signal.",
        202: "The access point rejected Wi-Fi authentication. Check its security mode and saved password.",
        203: "The access point did not accept the ESP32 association.",
        204: "The Wi-Fi security handshake timed out. Check router security compatibility and signal.",
    }
    if type(reason) is int:
        return f"{messages.get(reason, 'The ESP32 could not complete Wi-Fi association.')} (ESP32 reason {reason})."
    return "The ESP32 could not complete Wi-Fi association before the timeout. The firmware did not report a disconnect reason."


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError("Enter camera settings.")
    result = {}
    for name, maximum in (("ssid", 32), ("password", 63), ("device_name", 48), ("server_url", 240)):
        if name == "password" and name not in data:
            continue
        value = data.get(name)
        if (not isinstance(value, str) or len(value.encode("utf-8")) > maximum
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError(f"Check the {name.replace('_', ' ')} field.")
        if name == "password" and value and len(value.encode("utf-8")) < 8:
            raise ValueError("A Wi-Fi password must contain at least eight bytes.")
        if name in {"ssid", "device_name"} and not value.strip():
            raise ValueError(f"Enter a {name.replace('_', ' ')}.")
        result[name] = value
    try:
        url = urlsplit(result["server_url"])
        address = ipaddress.IPv4Address(url.hostname or "")
        if (url.scheme != "http" or not is_lan(address) or url.port == 0 or url.username or url.password
                or url.netloc != str(address) + (f":{url.port}" if url.port else "")
                or url.path != "/api/camera/ingest" or url.query or url.fragment):
            raise ValueError
    except ValueError:
        raise ValueError("Use a local IPv4 HTTP address ending in /api/camera/ingest.") from None
    for name, low, high in (("capture_interval_ms", 500, 60000), ("jpeg_quality", 10, 40)):
        value = data.get(name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name.replace('_', ' ').capitalize()} must be between {low} and {high}.")
        result[name] = value
    if data.get("resolution") not in RESOLUTIONS:
        raise ValueError("Choose VGA or SVGA.")
    result["resolution"] = data["resolution"]
    return result


def lan_addresses(peer=None):
    import psutil

    interfaces = psutil.net_if_stats()
    addresses = {}
    for name, entries in psutil.net_if_addrs().items():
        if not interfaces.get(name) or not interfaces[name].isup:
            continue
        if any(word in name.lower() for word in ("virtual", "vethernet", "vpn", "tailscale", "wireguard", "docker", "loopback", "vmware", "hyper-v", "wsl", "tun", "tap")):
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            address = ipaddress.IPv4Address(entry.address)
            if is_lan(address):
                addresses[str(address)] = name
    preferred = None
    try:
        target = str(ipaddress.IPv4Address(peer)) if peer else "192.0.2.1"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((target, 9))
            preferred = route.getsockname()[0]
    except (OSError, ValueError):
        pass
    return [{"address": ip, "interface": addresses[ip]} for ip in sorted(addresses, key=lambda ip: (ip != preferred, ip))]


class USBDevices:
    def __init__(self):
        self.devices = {}
        self.lock = threading.Lock()

    def ports(self):
        from serial.tools.list_ports import comports

        return [{"port": item.device, "description": item.description, "likely_camera": item.vid == 0x303A}
                for item in sorted(comports())]

    def connect(self, port):
        import serial

        with self.lock:
            for device_id, link in self.devices.items():
                if link.port == port:
                    return self._exchange(link, "GET_DEVICE_INFO")
            if port not in {item["port"] for item in self.ports()}:
                raise DeviceError("unknown_port", "Choose an available USB serial port.")
            link = serial.Serial(port=None, baudrate=115200, timeout=0.25, write_timeout=1)
            link.port = port
            link.dtr = False
            link.rts = False
            try:
                link.open()
            except (serial.SerialException, OSError):
                link.close()
                raise DeviceError("port_unavailable", "Could not open the port. Close Arduino Serial Monitor and reconnect USB.") from None
            try:
                deadline = time.monotonic() + 7
                while True:
                    try:
                        info = self._exchange(link, "GET_DEVICE_INFO", timeout=1.5)
                        break
                    except DeviceError as error:
                        if error.code != "timeout" or time.monotonic() >= deadline:
                            raise
                device_id = info.get("device_id")
                if info.get("product") != "Paparazzi" or info.get("protocol_version") != VERSION:
                    raise DeviceError("unsupported_firmware", "Flash firmware that supports Paparazzi USB protocol 1.")
                if not isinstance(device_id, str) or not device_id.startswith("paparazzi-"):
                    raise DeviceError("invalid_device", "The camera did not report a valid device ID.")
                self.disconnect_locked(device_id)
                self.devices[device_id] = link
                return info
            except Exception:
                link.close()
                raise

    def disconnect_locked(self, device_id):
        link = self.devices.pop(device_id, None)
        if link:
            link.close()

    def disconnect(self, device_id):
        with self.lock:
            self.disconnect_locked(device_id)

    def connected(self):
        with self.lock:
            available = {item["port"] for item in self.ports()}
            for key, link in list(self.devices.items()):
                if link.port not in available:
                    self.disconnect_locked(key)
            return [{"device_id": key, "port": link.port, "usb_connected": True} for key, link in self.devices.items()]

    def command(self, device_id, command, data=None):
        with self.lock:
            link = self.devices.get(device_id)
            if link is None:
                raise DeviceError("disconnected", "Reconnect the camera through USB.")
            try:
                return self._exchange(link, command, data, 30)
            except DeviceError as error:
                if error.code in {"disconnected", "timeout"}:
                    self.disconnect_locked(device_id)
                raise
            finally:
                if command in {"RESTART", "FACTORY_RESET"}:
                    self.disconnect_locked(device_id)

    @staticmethod
    def _exchange(link, command, data=None, timeout=5):
        import serial

        request_id = uuid.uuid4().hex
        message = json.dumps({"request_id": request_id, "command": command, "data": data or {}}, ensure_ascii=False).encode()
        if len(message) >= LIMIT:
            raise DeviceError("message_too_large", "The camera settings exceed the serial message limit.")
        try:
            link.write(b"\n" + message + b"\n")
            deadline = time.monotonic() + timeout
            partial = bytearray()
            discard = False
            while time.monotonic() < deadline:
                chunk = link.read_until(b"\n", LIMIT)
                partial.extend(chunk)
                discard = discard or len(partial) >= LIMIT
                if not chunk.endswith(b"\n"):
                    if discard:
                        partial.clear()
                    continue
                try:
                    reply = json.loads(partial) if not discard else None
                except (ValueError, UnicodeError):
                    reply = None
                partial.clear()
                discard = False
                if not isinstance(reply, dict) or reply.get("request_id") != request_id:
                    continue
                if reply.get("success") is not True:
                    code = reply.get("code")
                    errors = {
                        "invalid_config": "The camera rejected the settings.",
                        "wifi_failed": "The ESP32 could not complete Wi-Fi association.",
                        "server_failed": "The camera could not reach Paparazzi. Check LAN access, Windows Firewall, and Wi-Fi isolation.",
                        "camera_failed": "The camera could not apply these image settings.",
                        "storage_failed": "The camera could not save its settings.",
                        "busy": "The camera is finishing another request.",
                    }
                    message = wifi_failure_message(reply.get("result")) if code == "wifi_failed" else errors.get(code, "The camera rejected the request.")
                    raise DeviceError(code if isinstance(code, str) and code in errors else "device_error",
                                      message)
                return reply["result"] if isinstance(reply.get("result"), dict) else {}
        except (serial.SerialException, OSError):
            raise DeviceError("disconnected", "USB communication was interrupted. Reconnect the camera.") from None
        raise DeviceError("timeout", "The camera did not reply. Check the USB cable and firmware.")
