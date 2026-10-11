from .analytics_routes import analytics_bp
from .assessment_routes import assessment_bp
from .dashboard_routes import dashboard_bp
from .scanning_routes import scanning_bp
from .student_routes import student_bp


def register_blueprints(app):
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(assessment_bp)
    app.register_blueprint(scanning_bp)
    app.register_blueprint(analytics_bp)
    return app
