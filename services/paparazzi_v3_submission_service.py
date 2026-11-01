from __future__ import annotations

import uuid
from pathlib import Path

import cv2
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Assessment, ScanDraft, Student
from services.image_processor import ImageProcessingError, load_uploaded_image
from services.paparazzi_v3_scanner import (
    align_paparazzi_v3,
    detect_v3_answers,
    detect_v3_student_id,
    load_v3_profile,
)

SUPPORTED_V3_QUESTION_COUNTS = {20, 50, 100}


def _save_image(folder: Path, image, name: str) -> str:
    path = folder / name
    if not cv2.imwrite(str(path), image):
        raise OSError("Could not save the uploaded scan image.")
    return name


def create_v3_scan_draft(file_storage, app_config, assessment: Assessment, question_count: int, selected_student_id=None, *, capture_id=None, original_filename=None) -> ScanDraft:
    image, _metadata = load_uploaded_image(
        file_storage,
        max_bytes=int(app_config["MAX_CONTENT_LENGTH"]),
        max_dimension=int(app_config["MAX_IMAGE_DIMENSION"]),
    )
    return create_v3_scan_draft_from_image(image, app_config, assessment, question_count, selected_student_id, capture_id=capture_id, original_filename=original_filename)


def create_v3_scan_draft_from_image(image, app_config, assessment: Assessment, question_count: int, selected_student_id=None, *, capture_id=None, original_filename=None) -> ScanDraft:
    if question_count not in SUPPORTED_V3_QUESTION_COUNTS:
        raise ValueError("Choose a 20, 50, or 100 question V3 sheet.")
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if len(questions) != question_count or [question.question_number for question in questions] != list(range(1, question_count + 1)):
        raise ValueError("The selected V3 template question count must match the assessment answer key exactly.")
    if any(not question.option_e.strip() for question in questions):
        raise ValueError("Choice E must be completed for every question before using a V3 sheet.")
    if any(question.correct_answer not in {"A", "B", "C", "D", "E"} for question in questions):
        raise ValueError("The answer key contains an invalid V3 answer choice.")

    profile = load_v3_profile()
    alignment = align_paparazzi_v3(image, profile, question_count=question_count)
    student_id_detection = detect_v3_student_id(alignment.aligned_image, profile)
    recognized_answers = detect_v3_answers(alignment.aligned_image, question_count, profile)

    matched_student = None
    if student_id_detection["student_id"]:
        matched_student = db.session.scalar(
            select(Student).where(Student.student_number == student_id_detection["student_id"])
        )
        if matched_student and matched_student.class_section != assessment.class_section:
            matched_student = None

    selected_student = None
    if selected_student_id not in (None, ""):
        try:
            selected_student = db.session.get(Student, int(selected_student_id))
        except (TypeError, ValueError):
            selected_student = None
        if selected_student is None:
            raise ValueError("The selected student record does not exist.")
        if selected_student.class_section != assessment.class_section:
            raise ValueError("The selected student is not assigned to this assessment's class section.")

    folder = Path(app_config["UPLOAD_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    image_names = [original_filename or f"{token}-original.png", f"{token}-v3-aligned.png", f"{token}-v3-debug.png"]
    saved_names = []
    try:
        if original_filename:
            original_path = (folder / original_filename).resolve()
            if original_path.parent != folder.resolve() or not original_path.is_file():
                raise OSError("The preserved camera capture could not be found.")
        else:
            saved_names.append(_save_image(folder, image, image_names[0]))
        saved_names.append(_save_image(folder, alignment.aligned_image, image_names[1]))
        debug = alignment.aligned_image.copy()
        for item in recognized_answers:
            color = (0, 150, 40) if item["status"] == "detected" else (0, 150, 230)
            for choice, (x_mm, y_mm) in zip(profile["answers"]["choices"], item["bubble_centers_mm"]):
                x = round(x_mm * debug.shape[1] / profile["paper"]["width_mm"])
                y = round(y_mm * debug.shape[0] / profile["paper"]["height_mm"])
                radius = round(profile["answers"]["bubble_radius_mm"] * debug.shape[1] / profile["paper"]["width_mm"])
                cv2.circle(debug, (x, y), radius, color, 1)
        saved_names.append(_save_image(folder, debug, image_names[2]))

        notes = {
            "warnings": alignment.warnings,
            "blur_score": alignment.blur_score,
            "orientation": alignment.orientation,
            "profile_version": profile["version"],
            "matched_student_id": matched_student.id if matched_student else None,
            "matched_student_name": matched_student.name if matched_student else None,
            "student_id_unmatched": bool(student_id_detection["student_id"] and not matched_student),
            "capture_id": capture_id,
        }
        draft = ScanDraft(
            assessment_id=assessment.id,
            student_id=selected_student.id if selected_student else (matched_student.id if matched_student else None),
            detected_student_number=student_id_detection["student_id"],
            question_count=question_count,
            profile_version=profile["version"],
            original_image=image_names[0],
            aligned_image=image_names[1],
            debug_image=image_names[2],
            student_id_detection=student_id_detection,
            recognized_answers=recognized_answers,
            processing_notes=notes,
        )
        db.session.add(draft)
        db.session.commit()
        return draft
    except (OSError, ValueError, ImageProcessingError):
        db.session.rollback()
        for name in saved_names:
            (folder / name).unlink(missing_ok=True)
        raise


def confirm_v3_scan_draft(draft: ScanDraft, student: Student, final_answers: list[str | None]):
    from services.grading_service import grade_detections
    from models import AnswerCorrection, CaptureJob, Response, Submission

    assessment = draft.assessment
    questions = sorted(assessment.questions, key=lambda question: question.question_number)
    if student.class_section != assessment.class_section:
        raise ValueError("The selected student is not assigned to this assessment's class section.")
    if len(final_answers) != draft.question_count or len(questions) != draft.question_count:
        raise ValueError("The reviewed response count does not match the V3 sheet and assessment.")
    existing = db.session.scalar(
        select(Submission).where(Submission.student_id == student.id, Submission.assessment_id == assessment.id)
    )
    if existing:
        raise ValueError("This student already has a submission for this assessment.")

    detections = []
    for number, (answer, original) in enumerate(zip(final_answers, draft.recognized_answers), start=1):
        if answer not in {None, "A", "B", "C", "D", "E"}:
            raise ValueError(f"Question {number} has an invalid reviewed answer.")
        status = "detected" if answer else "blank"
        detections.append({
            "question_number": number,
            "answer": answer,
            "status": status,
            "confidence": float(original["confidence"]),
            "values": original["values"],
            "manual_correction_flag": answer != original["answer"] or original["status"] not in {"detected", "blank"},
        })
    grade = grade_detections(questions, detections)

    submission = Submission(
        student_id=student.id,
        assessment_id=assessment.id,
        score=grade["score"],
        total_points=grade["total_points"],
        percentage_score=grade["percentage"],
        processing_status="processed",
        review_status="required" if grade["needs_review"] else "not_required",
        original_image=draft.original_image,
        aligned_image=draft.aligned_image,
        debug_image=draft.debug_image,
        processing_notes={**draft.processing_notes, "student_id_detection": draft.student_id_detection},
    )
    for index, item in enumerate(grade["responses"]):
        original = draft.recognized_answers[index]
        response = Response(
            question_id=item["question"].id,
            detected_answer=original["answer"],
            answer=item["answer"],
            detection_status=original["status"],
            diagnostic_values=original["values"],
            correctness=item["correctness"],
            confidence=item["confidence"],
            manual_correction_flag=detections[index]["manual_correction_flag"],
        )
        submission.responses.append(response)
        if response.manual_correction_flag:
            response.corrections.append(AnswerCorrection(
                previous_answer=original["answer"],
                corrected_answer=item["answer"],
                note="Teacher confirmation during V3 scan review.",
            ))
    db.session.add(submission)
    capture_id = (draft.processing_notes or {}).get("capture_id")
    if capture_id:
        capture_job = db.session.scalar(select(CaptureJob).where(CaptureJob.capture_id == capture_id))
        if capture_job is not None:
            from datetime import datetime, timezone
            db.session.flush()
            capture_job.submission_id = submission.id
            capture_job.status = "COMPLETED"
            capture_job.completed_at = datetime.now(timezone.utc)
            capture_job.error_message = None
    db.session.delete(draft)
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        existing = db.session.scalar(
            select(Submission).where(Submission.student_id == student.id, Submission.assessment_id == assessment.id)
        )
        if existing:
            raise ValueError("This student already has a submission for this assessment.") from error
        raise
    return submission
