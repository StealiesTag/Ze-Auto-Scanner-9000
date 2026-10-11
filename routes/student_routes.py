from flask import Blueprint, render_template

student_bp = Blueprint("student", __name__, url_prefix="/students")


@student_bp.route("")
def list_students():
    return render_template("students.html")
