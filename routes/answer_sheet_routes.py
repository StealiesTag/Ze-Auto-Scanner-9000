from flask import Blueprint, render_template

answer_sheet_bp = Blueprint("answer_sheet", __name__, url_prefix="/answer-sheets")


@answer_sheet_bp.route("", methods=["GET"])
def answer_sheets():
    return render_template("answer_sheets.html")
