from flask import Blueprint

scanning_bp = Blueprint("scanning", __name__, url_prefix="/scanning")


@scanning_bp.route("/upload")
def upload_page():
    return {"status": "not_implemented"}
