from flask import Blueprint, current_app, render_template

from services.analytics_service import build_analytics

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    analytics = build_analytics(recommendation_threshold=current_app.config["RECOMMENDATION_ERROR_THRESHOLD"])
    return render_template("dashboard.html", analytics=analytics)


@dashboard_bp.route("/health")
def health():
    return {"status": "ok"}
