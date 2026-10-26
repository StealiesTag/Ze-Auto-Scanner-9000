from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from extensions import db
from models import Student, Submission
from services.student_service import create_student, import_students_from_csv, list_students, update_student

student_bp = Blueprint("student", __name__, url_prefix="/students")


@student_bp.route("")
def list_students():
    return render_template("students.html", students=list_students(request.args.get("q", "")), search=request.args.get("q", ""))


@student_bp.route("/new", methods=["GET", "POST"])
def new_student():
    if request.method == "POST":
        try:
            create_student(request.form)
            flash("Student added.", "success")
            return redirect(url_for("student.list_students"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("student_form.html", student=None)


@student_bp.route("/<int:student_id>/edit", methods=["GET", "POST"])
def edit_student(student_id: int):
    student = db.get_or_404(Student, student_id)
    if request.method == "POST":
        try:
            update_student(student, request.form)
            flash("Student record updated.", "success")
            return redirect(url_for("student.list_students"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("student_form.html", student=student)


@student_bp.route("/<int:student_id>/delete", methods=["POST"])
def delete_student(student_id: int):
    student = db.get_or_404(Student, student_id)
    if student.submissions:
        flash("This student has saved submissions and cannot be deleted.", "error")
    else:
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted.", "success")
    return redirect(url_for("student.list_students"))


@student_bp.route("/import", methods=["POST"])
def import_csv():
    upload = request.files.get("csv_file")
    if not upload or not upload.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("student.list_students"))
    rows, errors = import_students_from_csv(upload)
    if errors:
        flash("No rows imported. " + " | ".join(errors), "error")
    else:
        flash(f"Imported {len(rows)} students.", "success")
    return redirect(url_for("student.list_students"))


@student_bp.route("/<int:student_id>/results")
def student_results(student_id: int):
    student = db.get_or_404(Student, student_id)
    submissions = db.session.scalars(
        select(Submission).where(Submission.student_id == student_id).order_by(Submission.submitted_at.desc())
    ).all()
    return render_template("student_results.html", student=student, submissions=submissions)
