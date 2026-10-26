import os

from flask import Flask, render_template
from sqlalchemy.engine import make_url

from config import config
from extensions import csrf, db, migrate
from models import AnswerCorrection, Assessment, LearningResource, Question, Response, Student, Submission


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
