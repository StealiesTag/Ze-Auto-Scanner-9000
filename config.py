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
