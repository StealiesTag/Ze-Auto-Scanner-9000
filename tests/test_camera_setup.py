import pytest

from extensions import db
from models import CameraSetup
from services.camera_setup import get_camera_setup, save_camera_setup, validate_setup


def test_camera_setup_is_validated_and_saved_once(app):
    with app.app_context():
        setup = save_camera_setup("classroom-esp32", "http://192.168.1.80:81/stream", "http://192.168.1.80/capture", 6)
        assert setup.id == 1
        assert db.session.get(CameraSetup, 1).camera_id == "classroom-esp32"
        app.config["PAPARAZZI_CAMERA_ID"] = "fallback"
        assert get_camera_setup(app.config) == {
            "camera_id": "classroom-esp32",
            "stream_url": "http://192.168.1.80:81/stream",
            "capture_url": "http://192.168.1.80/capture",
            "timeout_seconds": 6,
        }


@pytest.mark.parametrize("values", [
    ("cam", "http://example.com/stream", "http://example.com/capture"),
    ("cam", "http://192.168.1.80/stream", "http://192.168.1.81/capture"),
    ("cam", "http://192.0.2.10/stream", "http://192.0.2.10/capture"),
    ("cam", "http://user:pass@192.168.1.80/stream", "http://192.168.1.80/capture"),
])
def test_camera_setup_rejects_remote_or_mismatched_addresses(values):
    with pytest.raises(ValueError):
        validate_setup(*values)
