import os

from flask import Flask, render_template
from sqlalchemy.engine import make_url

from config import config
from extensions import csrf, db, migrate
from models import AnswerCorrection, Assessment, LearningResource, Question, Response, Student, Submission
from services.camera_poller import validate_camera_url


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    database_url = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not database_url:
        variable = "TEST_DATABASE_URL" if config_name == "testing" else "DATABASE_URL"
        raise RuntimeError(f"{variable} is required; configure a PostgreSQL connection in .env.")
    if make_url(database_url).drivername != "postgresql+psycopg":
        raise RuntimeError("Only PostgreSQL using the Psycopg 3 driver is supported.")
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is required; configure it in .env.")

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    from routes import register_blueprints

    register_blueprints(app)
    app.config["MAX_CONTENT_LENGTH"] = int(app.config["MAX_CONTENT_LENGTH"])
    if not 0 < app.config["RECOMMENDATION_ERROR_THRESHOLD"] <= 1:
        raise RuntimeError("RECOMMENDATION_ERROR_THRESHOLD must be greater than 0 and no greater than 1.")
    if not 0 < app.config["OMR_FILL_THRESHOLD"] <= 1 or not 0 <= app.config["OMR_AMBIGUITY_MARGIN"] <= 1:
        raise RuntimeError("OMR thresholds must be ratios between 0 and 1.")
    if (app.config["CAMERA_STABLE_FRAME_COUNT"] < 2
            or app.config["CAMERA_BOTTOM_MISSING_FRAME_COUNT"] < 2
            or app.config["CAMERA_MARKER_MOVEMENT_TOLERANCE_PX"] < 0
            or not 0 < app.config["CAMERA_MIN_PAGE_CHANGE_RATIO"] <= 1
            or not 0 < app.config["CAMERA_MIN_PAGE_AREA_RATIO"] <= 1
            or not 0 <= app.config["CAMERA_MIN_MARKER_CONFIDENCE"] <= 1
            or app.config["CAMERA_POLL_INTERVAL_SECONDS"] <= 0
            or app.config["ESP32_REQUEST_TIMEOUT_SECONDS"] <= 0
            or app.config["PAPARAZZI_CAMERA_POLL_INTERVAL"] <= 0
            or app.config["PAPARAZZI_CAMERA_TIMEOUT"] <= 0
            or app.config["CAPTURE_QUEUE_CAPACITY"] < 1):
        raise RuntimeError("Automatic scanning thresholds and queue capacity must be positive and within their documented ranges.")
    if app.config["PAPARAZZI_CAMERA_URL"]:
        try:
            validate_camera_url(app.config["PAPARAZZI_CAMERA_URL"])
        except ValueError as error:
            raise RuntimeError(str(error)) from error
    if not app.config["PAPARAZZI_CAMERA_ID"] or len(app.config["PAPARAZZI_CAMERA_ID"]) > 100:
        raise RuntimeError("PAPARAZZI_CAMERA_ID must contain 1-100 characters.")

    @app.errorhandler(413)
    def upload_too_large(_error):
        return render_template("error.html", status_code=413, message="The uploaded file exceeds the configured size limit."), 413

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", status_code=404, message="The requested page or record was not found."), 404

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", status_code=400, message=error.description), 400

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception("Unhandled request failed (%s)", type(error).__name__)
        db.session.rollback()
        return render_template("error.html", status_code=500, message="The request could not be completed. No changes were saved."), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=False)
