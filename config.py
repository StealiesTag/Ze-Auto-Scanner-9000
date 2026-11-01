import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    UPLOAD_FOLDER = str((BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads")).resolve())
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_SIZE", str(16 * 1024 * 1024)))
    MAX_IMAGE_DIMENSION = int(os.getenv("MAX_IMAGE_DIMENSION", "8000"))
    OMR_FILL_THRESHOLD = float(os.getenv("OMR_FILL_THRESHOLD", "0.18"))
    OMR_AMBIGUITY_MARGIN = float(os.getenv("OMR_AMBIGUITY_MARGIN", "0.10"))
    RECOMMENDATION_ERROR_THRESHOLD = float(os.getenv("RECOMMENDATION_ERROR_THRESHOLD", "0.40"))
    AI_API_KEY = os.getenv("AI_API_KEY")
    AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    AI_MODEL = os.getenv("AI_MODEL", "")
    AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "30"))
    ESP32_STREAM_URL = os.getenv("ESP32_STREAM_URL", "")
    ESP32_CAPTURE_URL = os.getenv("ESP32_CAPTURE_URL", "")
    ESP32_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ESP32_REQUEST_TIMEOUT_SECONDS", "8"))
    CAMERA_POLL_INTERVAL_SECONDS = float(os.getenv("CAMERA_POLL_INTERVAL_SECONDS", "0.2"))
    CAMERA_STABLE_FRAME_COUNT = int(os.getenv("CAMERA_STABLE_FRAME_COUNT", "5"))
    CAMERA_BOTTOM_MISSING_FRAME_COUNT = int(os.getenv("CAMERA_BOTTOM_MISSING_FRAME_COUNT", "5"))
    CAMERA_MARKER_MOVEMENT_TOLERANCE_PX = float(os.getenv("CAMERA_MARKER_MOVEMENT_TOLERANCE_PX", "3.0"))
    CAMERA_MIN_PAGE_CHANGE_RATIO = float(os.getenv("CAMERA_MIN_PAGE_CHANGE_RATIO", "0.0015"))
    CAMERA_MIN_PAGE_AREA_RATIO = float(os.getenv("CAMERA_MIN_PAGE_AREA_RATIO", "0.25"))
    CAMERA_MIN_MARKER_CONFIDENCE = float(os.getenv("CAMERA_MIN_MARKER_CONFIDENCE", "0.10"))
    PAPARAZZI_CAMERA_URL = os.getenv("PAPARAZZI_CAMERA_URL", "")
    PAPARAZZI_CAMERA_ID = os.getenv("PAPARAZZI_CAMERA_ID", "paparazzi-esp32-01")
    PAPARAZZI_CAMERA_POLL_INTERVAL = float(os.getenv("PAPARAZZI_CAMERA_POLL_INTERVAL", "1"))
    PAPARAZZI_CAMERA_TIMEOUT = float(os.getenv("PAPARAZZI_CAMERA_TIMEOUT", "5"))
    CAMERA_CONTROL_TOKEN = os.getenv("CAMERA_CONTROL_TOKEN", "")
    CAPTURE_QUEUE_CAPACITY = int(os.getenv("CAPTURE_QUEUE_CAPACITY", "25"))
    WTF_CSRF_ENABLED = True


class DevelopmentConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = os.getenv("SECRET_KEY", "test-only-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL")
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
