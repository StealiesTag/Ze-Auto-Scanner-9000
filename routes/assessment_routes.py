from flask import Blueprint, render_template

assessment_bp = Blueprint("assessment", __name__, url_prefix="/assessments")


@assessment_bp.route("")
def list_assessments():
    return render_template("assessments.html")
