from .analytics_routes import analytics_bp
from .automatic_scanning_routes import automatic_scanning_bp
from .camera_api_routes import camera_api_bp
from .answer_sheet_routes import answer_sheet_bp
from .assessment_routes import assessment_bp
from .dashboard_routes import dashboard_bp
from .scanning_routes import scanning_bp
from .student_routes import student_bp


def register_blueprints(app):
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(assessment_bp)
    app.register_blueprint(answer_sheet_bp)
    app.register_blueprint(scanning_bp)
    app.register_blueprint(automatic_scanning_bp)
    app.register_blueprint(camera_api_bp)
    app.register_blueprint(analytics_bp)
    return app
