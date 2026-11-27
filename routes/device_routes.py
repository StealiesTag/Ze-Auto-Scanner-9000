"""Teacher-authorized provisioning and a narrowly scoped camera heartbeat."""
from datetime import datetime, timezone
from hmac import compare_digest, digest
from ipaddress import IPv4Address
import os
import re
import threading

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import csrf, db
from models import CaptureJob, ScannerControl
from services.camera_setup import save_camera_setup
from services.device_provisioning import DeviceError, USBDevices, lan_addresses, validate_config

devices_bp = Blueprint("devices", __name__)
heartbeat_bp = Blueprint("device_heartbeat", __name__)
csrf.exempt(heartbeat_bp)
DEVICE_ID = re.compile(r"paparazzi-[a-fA-F0-9]{6,12}")
ALLOWED = {"PING", "GET_DEVICE_INFO", "GET_CONFIG", "GET_STATUS", "SET_CONFIG",
           "TEST_WIFI", "TEST_SERVER", "RESTART", "FACTORY_RESET"}
PUBLIC_FIELDS = {"device_id", "product", "protocol_version", "firmware_version", "device_name",
                 "ssid", "server_url", "capture_interval_ms", "jpeg_quality", "resolution",
                 "password_set", "wifi_connected", "camera_ready", "server_connected", "ip", "saved", "test_passed"}


def public_result(result):
    return {key: value for key, value in result.items()
            if key in PUBLIC_FIELDS and type(value) in {str, bool, int}}


def register_device_routes(app):
    app.extensions["usb_devices"] = USBDevices()
    app.extensions["device_heartbeats"] = ({}, threading.Lock())
    app.register_blueprint(devices_bp)
    app.register_blueprint(heartbeat_bp)


def token_for(device_id):
    key = current_app.secret_key
    return digest(key.encode() if isinstance(key, str) else key, f"device-heartbeat:{device_id}".encode(), "sha256").hex()


def require_id(device_id):
    if not DEVICE_ID.fullmatch(device_id):
        abort(404)


def printer_capture_delay(camera_id, default_seconds):
    statuses, lock = current_app.extensions["device_heartbeats"]
    with lock:
        status = statuses.get(camera_id)
        if status and (datetime.now(timezone.utc) - status["last_seen"]).total_seconds() < 75:
            return max(default_seconds, status["capture_interval_ms"] / 1000)
    return default_seconds


@devices_bp.before_request
def require_usb_host():
    host = request.host.rsplit(":", 1)[0] if request.host.count(":") == 1 else request.host
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    if request.remote_addr not in {"127.0.0.1", "::1"} or host not in {"localhost", "127.0.0.1", "[::1]"}:
        abort(403)


@devices_bp.errorhandler(DeviceError)
def device_error(error):
    return jsonify(error=str(error), code=error.code), 409


@devices_bp.errorhandler(ValueError)
def validation_error(error):
    return jsonify(error=str(error), code="invalid_config"), 400


@devices_bp.get("/devices")
def setup():
    return render_template("devices.html")


@devices_bp.get("/api/devices/ports")
def ports():
    manager = current_app.extensions["usb_devices"]
    return jsonify(ports=manager.ports(), devices=manager.connected())


@devices_bp.get("/api/devices")
def devices():
    statuses, lock = current_app.extensions["device_heartbeats"]
    now = datetime.now(timezone.utc)
    with lock:
        wireless = [
            {**status, "last_seen": status["last_seen"].isoformat(),
             "online": (now - status["last_seen"]).total_seconds() < 75}
            for key, status in statuses.items()
        ]
    if wireless:
        try:
            captures = dict(db.session.execute(
                select(CaptureJob.camera_id, func.max(CaptureJob.captured_at))
                .where(CaptureJob.camera_id.in_([item["device_id"] for item in wireless]))
                .group_by(CaptureJob.camera_id)
            ).all())
        except SQLAlchemyError:
            db.session.rollback()
            captures = {}
        for item in wireless:
            captured_at = captures.get(item["device_id"])
            item["last_capture"] = captured_at.isoformat() if captured_at else None
    return jsonify(wireless=wireless, usb=current_app.extensions["usb_devices"].connected())


@devices_bp.get("/api/devices/network")
def network():
    port = os.getenv("PORT") or request.environ.get("SERVER_PORT", "5000")
    urls = [{**item, "url": f"http://{item['address']}:{port}/api/camera/ingest"}
            for item in lan_addresses(request.args.get("peer"))]
    host = os.getenv("HOST", "0.0.0.0")
    return jsonify(addresses=urls, loopback_only=host in {"localhost", "127.0.0.1", "::1"})


@devices_bp.post("/api/devices/connect")
def connect():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("port"), str):
        raise ValueError("Choose a USB serial port.")
    info = current_app.extensions["usb_devices"].connect(body["port"])
    return jsonify(public_result(info))


@devices_bp.post("/api/devices/<device_id>/disconnect")
def disconnect(device_id):
    require_id(device_id)
    current_app.extensions["usb_devices"].disconnect(device_id)
    return jsonify(success=True)


@devices_bp.post("/api/devices/<device_id>/command")
def command(device_id):
    require_id(device_id)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("command"), str) or body["command"] not in ALLOWED:
        raise ValueError("Choose a supported camera action.")
    action = body["command"]
    data = {}
    if action in {"SET_CONFIG", "TEST_WIFI", "TEST_SERVER"}:
        control = db.session.get(ScannerControl, 1)
        if control and control.active:
            raise DeviceError("scan_active", "Stop automatic scanning before changing or testing camera settings.")
        data = validate_config(body.get("data"))
        data["heartbeat_token"] = token_for(device_id)
    if action == "FACTORY_RESET":
        if body.get("confirmation") != device_id:
            raise ValueError("Confirm the camera ID to erase its settings.")
        data["confirmation"] = device_id
    result = current_app.extensions["usb_devices"].command(device_id, action, data)
    # Allowlist serial data so a faulty firmware cannot leak extra configuration fields.
    return jsonify(public_result(result))


@devices_bp.post("/api/devices/<device_id>/use-camera")
def use_camera(device_id):
    require_id(device_id)
    status = current_app.extensions["usb_devices"].command(device_id, "GET_STATUS")
    if not status.get("camera_ready") or not status.get("wifi_connected"):
        raise ValueError("Connect the camera to Wi-Fi and confirm the camera sensor is ready.")
    ip = str(IPv4Address(status["ip"]))
    save_camera_setup(device_id, f"http://{ip}:81/stream", f"http://{ip}/capture")
    return jsonify(success=True)


@heartbeat_bp.post("/api/devices/heartbeat")
def heartbeat():
    if not request.content_length or request.content_length > 1024:
        abort(400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("device_id"), str):
        abort(400)
    device_id = body["device_id"]
    require_id(device_id)
    if not compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token_for(device_id)}".encode()):
        abort(401)
    if body.get("probe") is True:
        return jsonify(success=True)
    statuses, lock = current_app.extensions["device_heartbeats"]
    with lock:
        if len(statuses) >= 128 and device_id not in statuses:
            statuses.pop(min(statuses, key=lambda key: statuses[key]["last_seen"]))
        name = body.get("device_name")
        ssid = body.get("ssid")
        interval = body.get("capture_interval_ms")
        statuses[device_id] = {"device_id": device_id, "ip": request.remote_addr,
                               "device_name": name[:48] if isinstance(name, str) else "",
                               "ssid": ssid[:32] if isinstance(ssid, str) else "",
                               "capture_interval_ms": interval if type(interval) is int and 500 <= interval <= 60000 else 1000,
                               "camera_ready": body.get("camera_ready") is True,
                               "last_seen": datetime.now(timezone.utc)}
    return jsonify(success=True)
