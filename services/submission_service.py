from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Assessment, Response, Student, Submission
from services.grading_service import grade_detections
from services.image_processor import align_sheet, decode_qr, load_uploaded_image
from services.omr_service import detect_answers
from services.sheet_layout import BUBBLE_RADIUS_MM, PIXELS_PER_MM_X, PIXELS_PER_MM_Y, bubble_center_mm


class DuplicateSubmissionError(ValueError):
    def __init__(self, submission_id: int):
        super().__init__("A submission for this student and assessment already exists; existing grades were not changed.")
        self.submission_id = submission_id


def parse_qr_identity(payload: str) -> tuple[int, int]:
    try:
        identity = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("The QR code does not contain a valid student and assessment identity.") from error
    if not isinstance(identity, dict) or set(identity) != {"s", "a"}:
        raise ValueError("The QR code must contain only student and assessment identifiers.")
    student_id, assessment_id = identity["s"], identity["a"]
    if type(student_id) is not int or type(assessment_id) is not int or student_id < 1 or assessment_id < 1:
        raise ValueError("The QR code contains invalid identifiers.")
    return student_id, assessment_id


def _write_image(folder: Path, image, file_name: str) -> str:
    path = folder / file_name
    if not cv2.imwrite(str(path), image):
        raise OSError("Could not save the processed scan image.")
    return file_name


def process_answer_sheet(file_storage, app_config) -> Submission:
    image, _ = load_uploaded_image(
        file_storage,
        max_bytes=int(app_config["MAX_CONTENT_LENGTH"]),
        max_dimension=int(app_config["MAX_IMAGE_DIMENSION"]),
    )
    alignment = align_sheet(image)
    student_id, assessment_id = parse_qr_identity(decode_qr(alignment.aligned_image))
    student = db.session.get(Student, student_id)
    assessment = db.session.get(Assessment, assessment_id)
    if student is None or assessment is None:
        raise ValueError("The QR code identifiers do not match a registered student and assessment.")
    if student.class_section != assessment.class_section:
        raise ValueError("The identified student is not assigned to this assessment's class section.")
    existing = db.session.scalar(
        select(Submission).where(Submission.student_id == student_id, Submission.assessment_id == assessment_id)
    )
    if existing:
        raise DuplicateSubmissionError(existing.id)

    detections = detect_answers(
        alignment.aligned_image,
        assessment.questions,
        fill_threshold=float(app_config["OMR_FILL_THRESHOLD"]),
        ambiguity_margin=float(app_config["OMR_AMBIGUITY_MARGIN"]),
    )
    grade = grade_detections(assessment.questions, detections)
    needs_review = grade["needs_review"] or bool(alignment.warnings)
    folder = Path(app_config["UPLOAD_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    image_names = [f"{token}-original.png", f"{token}-aligned.png", f"{token}-debug.png"]
    saved_names: list[str] = []
    committed = False
    try:
        saved_names.append(_write_image(folder, image, image_names[0]))
        saved_names.append(_write_image(folder, alignment.aligned_image, image_names[1]))
        debug = alignment.aligned_image.copy()
        for question in assessment.questions:
            for option in "ABCD":
                x_mm, y_mm = bubble_center_mm(question.question_number, option)
                center = (round(x_mm * PIXELS_PER_MM_X), round(y_mm * PIXELS_PER_MM_Y))
                cv2.circle(debug, center, round(BUBBLE_RADIUS_MM * PIXELS_PER_MM_X), (30, 145, 230), 1)
        cv2.putText(debug, f"Markers: {alignment.detected_marker_ids} | Blur: {alignment.blur_score:.1f}",
                    (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 80, 220), 2)
        saved_names.append(_write_image(folder, debug, image_names[2]))

        submission = Submission(
            student_id=student.id,
            assessment_id=assessment.id,
            score=grade["score"],
            total_points=grade["total_points"],
            percentage_score=grade["percentage"],
            processing_status="processed",
            review_status="required" if needs_review else "not_required",
            original_image=image_names[0],
            aligned_image=image_names[1],
            debug_image=image_names[2],
            processing_notes={"warnings": alignment.warnings, "blur_score": alignment.blur_score},
        )
        for item in grade["responses"]:
            submission.responses.append(Response(
                question_id=item["question"].id,
                detected_answer=item["detected_answer"],
                answer=item["answer"],
                detection_status=item["status"],
                diagnostic_values=item["values"],
                correctness=item["correctness"],
                confidence=item["confidence"],
                manual_correction_flag=False,
            ))
        db.session.add(submission)
        db.session.commit()
        committed = True
        return submission
    except IntegrityError as error:
        db.session.rollback()
        existing = db.session.scalar(
            select(Submission).where(Submission.student_id == student_id, Submission.assessment_id == assessment_id)
        )
        if existing:
            raise DuplicateSubmissionError(existing.id) from error
        raise ValueError("The submission could not be saved because a database constraint was violated.") from error
    except (OSError, ValueError):
        db.session.rollback()
        raise
    finally:
        if not committed:
            for file_name in saved_names:
                (folder / file_name).unlink(missing_ok=True)
            db.session.rollback()
