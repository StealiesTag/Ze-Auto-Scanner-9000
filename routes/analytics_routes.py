from flask import Blueprint, render_template

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@analytics_bp.route("")
def index():
    return render_template("analytics.html")
