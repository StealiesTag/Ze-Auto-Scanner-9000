from sqlalchemy import CheckConstraint, func

from extensions import db


class CameraSetup(db.Model):
    __tablename__ = "camera_setup"
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.String(100), nullable=False)
    stream_url = db.Column(db.String(500), nullable=False)
    capture_url = db.Column(db.String(500), nullable=False)
    timeout_seconds = db.Column(db.Float, nullable=False, default=8.0)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_camera_setup_singleton"),
        CheckConstraint("length(camera_id) BETWEEN 1 AND 100", name="ck_camera_setup_camera_id_length"),
        CheckConstraint("timeout_seconds > 0 AND timeout_seconds <= 30", name="ck_camera_setup_timeout"),
    )
