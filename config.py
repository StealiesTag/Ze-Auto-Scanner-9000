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
    AI_API_KEY = os.getenv("AI_API_KEY") or os.getenv("MODEL_API_KEY") or os.getenv("META_MODEL_API_KEY")
    AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.meta.ai/v1")
    AI_MODEL = os.getenv("AI_MODEL", "muse-spark-1.3")
    AI_REASONING_EFFORT = os.getenv("AI_REASONING_EFFORT", "low")
    AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "180"))
    ESP32_STREAM_URL = os.getenv("ESP32_STREAM_URL", "")
    ESP32_CAPTURE_URL = os.getenv("ESP32_CAPTURE_URL", "")
    ESP32_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ESP32_REQUEST_TIMEOUT_SECONDS", "8"))
    CAMERA_POLL_INTERVAL_SECONDS = float(os.getenv("CAMERA_POLL_INTERVAL_SECONDS", "3.5"))
    CAMERA_STABLE_FRAME_COUNT = int(os.getenv("CAMERA_STABLE_FRAME_COUNT", "5"))
    CAMERA_BOTTOM_MISSING_FRAME_COUNT = int(os.getenv("CAMERA_BOTTOM_MISSING_FRAME_COUNT", "5"))
    CAMERA_MARKER_MOVEMENT_TOLERANCE_PX = float(os.getenv("CAMERA_MARKER_MOVEMENT_TOLERANCE_PX", "3.0"))
    CAMERA_MIN_PAGE_CHANGE_RATIO = float(os.getenv("CAMERA_MIN_PAGE_CHANGE_RATIO", "0.0015"))
    CAMERA_MIN_PAGE_AREA_RATIO = float(os.getenv("CAMERA_MIN_PAGE_AREA_RATIO", "0.25"))
    CAMERA_MIN_MARKER_CONFIDENCE = float(os.getenv("CAMERA_MIN_MARKER_CONFIDENCE", "0.10"))
    CAMERA_AUTO_FINALIZE_MIN_CONFIDENCE = float(os.getenv("CAMERA_AUTO_FINALIZE_MIN_CONFIDENCE", "0.80"))
    MUSE_API_KEY = os.getenv("MUSE_API_KEY", "")
    MUSE_BASE_URL = os.getenv("MUSE_BASE_URL", "https://api.meta.ai/v1")
    MUSE_MODEL = os.getenv("MUSE_MODEL", "muse-spark-1.3")
    MUSE_TIMEOUT_SECONDS = float(os.getenv("MUSE_TIMEOUT_SECONDS", "12"))
    MUSE_MIN_INTERVAL_SECONDS = float(os.getenv("MUSE_MIN_INTERVAL_SECONDS", "2"))
    PAPARAZZI_CAMERA_URL = os.getenv("PAPARAZZI_CAMERA_URL", "")
    PAPARAZZI_CAMERA_ID = os.getenv("PAPARAZZI_CAMERA_ID", "paparazzi-esp32-01")
    PAPARAZZI_CAMERA_POLL_INTERVAL = float(os.getenv("PAPARAZZI_CAMERA_POLL_INTERVAL", "3.5"))
    PAPARAZZI_CAMERA_TIMEOUT = float(os.getenv("PAPARAZZI_CAMERA_TIMEOUT", "5"))
    AUTO_START_SCAN_WORKER = os.getenv("AUTO_START_SCAN_WORKER", "true").strip().lower() not in {"0", "false", "no", "off"}
    PRINTER_BLANK_FEED_PAGE_COUNT = int(os.getenv("PRINTER_BLANK_FEED_PAGE_COUNT", "200"))
    SCAN_IDLE_STOP_SECONDS = int(os.getenv("SCAN_IDLE_STOP_SECONDS", "90"))
    SCAN_DUPLICATE_STOP_SECONDS = float(os.getenv("SCAN_DUPLICATE_STOP_SECONDS", "8"))
    SCAN_DUPLICATE_PAGE_MAX_CHANGE_RATIO = float(os.getenv("SCAN_DUPLICATE_PAGE_MAX_CHANGE_RATIO", "0.02"))
    MAX_ASSESSMENT_PDF_BYTES = int(os.getenv("MAX_ASSESSMENT_PDF_BYTES", str(20 * 1024 * 1024)))
    CANVAS_TOKEN_ENCRYPTION_KEY = os.getenv("CANVAS_TOKEN_ENCRYPTION_KEY", "")
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
