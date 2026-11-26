import os
import secrets
import threading
from hmac import compare_digest
from pathlib import Path

from flask import Flask, Response as HTTPResponse, render_template, request
from sqlalchemy import event
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
        raise RuntimeError(f"{variable} is required for this application configuration.")
    database_backend = make_url(database_url).get_backend_name()
    if database_backend not in {"sqlite", "postgresql"} or (
        database_backend == "postgresql" and make_url(database_url).drivername != "postgresql+psycopg"
    ):
        raise RuntimeError("Use the default local SQLite database or PostgreSQL with the Psycopg 3 driver.")
    app.config["PAPARAZZI_DATABASE_BACKEND"] = database_backend
    if database_backend == "sqlite":
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            **app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}),
            "connect_args": {"check_same_thread": False, "timeout": 30},
        }
    if not app.config.get("SECRET_KEY"):
        if database_backend == "sqlite":
            key_path = Path(app.instance_path) / ".secret_key"
            try:
                secret = key_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                secret = ""
            if not secret:
                secret = secrets.token_urlsafe(48)
                try:
                    key_path.write_text(secret, encoding="utf-8")
                except FileExistsError:
                    secret = key_path.read_text(encoding="utf-8").strip()
            app.config["SECRET_KEY"] = secret
        else:
            raise RuntimeError("SECRET_KEY is required for PostgreSQL deployments; configure it in .env.")

    db.init_app(app)
    migrate.init_app(app, db)

    @app.before_request
    def authenticate_teacher():
        if request.blueprint == "camera_api":
            return None
        password = app.config["PAPARAZZI_TEACHER_PASSWORD"]
        if not password:
            if request.remote_addr in {"127.0.0.1", "::1"}:
                return None
            return HTTPResponse("Teacher access requires a configured password.", status=403)
        credentials = request.authorization
        if (
            credentials and credentials.type == "basic" and credentials.username == "teacher"
            and compare_digest((credentials.password or "").encode(), password.encode())
        ):
            return None
        return HTTPResponse(
            "Sign in to Paparazzi.", status=401,
            headers={"WWW-Authenticate": 'Basic realm="Paparazzi", charset="UTF-8"'},
        )

    csrf.init_app(app)

    from routes import register_blueprints

    register_blueprints(app)
    if database_backend == "sqlite":
        # The packaged single-teacher install starts at the current model
        # schema without requiring a separate migration command.
        with app.app_context():
            engine = db.engine

            @event.listens_for(engine, "connect")
            def configure_sqlite_connection(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            db.create_all()
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
            or not 0 <= app.config["CAMERA_AUTO_FINALIZE_MIN_CONFIDENCE"] <= 1
            or app.config["CAMERA_POLL_INTERVAL_SECONDS"] <= 0
            or app.config["ESP32_REQUEST_TIMEOUT_SECONDS"] <= 0
            or app.config["MUSE_TIMEOUT_SECONDS"] <= 0
            or app.config["MUSE_MIN_INTERVAL_SECONDS"] <= 0
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

    if config_name != "testing" and app.config.get("AUTO_START_SCAN_WORKER", True):
        from services.scan_worker import run_scan_worker

        stop_event = threading.Event()

        def start_queue_consumer() -> None:
            try:
                run_scan_worker(app, stop_event)
            except RuntimeError as error:
                # A separately managed worker may already own the database lock.
                app.logger.info("Embedded Paparazzi queue consumer not started: %s", error)

        worker_thread = threading.Thread(
            target=start_queue_consumer,
            name="paparazzi-scan-worker-supervisor",
            daemon=True,
        )
        worker_thread.start()
        app.extensions["paparazzi_scan_worker"] = (worker_thread, stop_event)

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
        return render_template("error.html", status_code=500, message="The request could not be completed. Check the relevant assessment or scan list before retrying."), 500

    return app


if __name__ == "__main__":
    app = create_app()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    if os.getenv("PAPARAZZI_OPEN_BROWSER", "").strip().lower() in {"1", "true", "yes"}:
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(host=host, port=port, debug=False)
