"""Canvas connection, matching, preview, and verified grade synchronization."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select

from extensions import db
from models import (
    Assessment, CanvasAssessmentMapping, CanvasConnection, CanvasGradeSync,
    CanvasStudentMapping, Response, Student, Submission,
)
from services.canvas_api import CanvasAPIError, CanvasClient
from services.canvas_crypto import decrypt_access_token, encrypt_access_token


CENT = Decimal("0.01")


def get_canvas_client(config: dict, *, opener=None, resolver=None) -> CanvasClient:
    connection = db.session.get(CanvasConnection, 1)
    if connection is None or connection.connection_status != "connected":
        raise CanvasAPIError("Connect Canvas before using course or grade features.")
    token = decrypt_access_token(connection.encrypted_access_token, config)
    return CanvasClient(connection.base_url, token, timeout=config.get("CANVAS_TIMEOUT_SECONDS", 10), opener=opener, resolver=resolver)


def connect_canvas(base_url: str, token: str, config: dict, *, opener=None, resolver=None) -> CanvasConnection:
    if not token or len(token) > 4096 or any(ord(char) < 32 for char in token):
        raise ValueError("Enter a valid Canvas personal access token.")
    client = CanvasClient(base_url, token, timeout=config.get("CANVAS_TIMEOUT_SECONDS", 10), opener=opener, resolver=resolver)
    user = client.self_user()
    encrypted = encrypt_access_token(token, config)
    connection = db.session.get(CanvasConnection, 1)
    if connection is None:
        connection = CanvasConnection(id=1, base_url=client.base_url, encrypted_access_token=encrypted, canvas_user_id=str(user["id"]))
        db.session.add(connection)
    else:
        connection.base_url = client.base_url
        connection.encrypted_access_token = encrypted
        connection.canvas_user_id = str(user["id"])
        connection.connection_status = "connected"
        connection.authorized_at = datetime.now(timezone.utc)
    db.session.commit()
    return connection


def _numeric_id(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 100:
        raise ValueError(f"Choose a valid Canvas {field}.")
    return text


def teacher_courses(client: CanvasClient) -> list[dict]:
    return [course for course in client.courses() if str(course.get("id", "")).isdigit()]


def list_assignments(client: CanvasClient, course_id: str) -> list[dict]:
    course_id = _numeric_id(course_id, "course")
    return [assignment for assignment in client.assignments(course_id) if str(assignment.get("id", "")).isdigit()]


def ensure_supported_assignment(assignment: dict) -> tuple[Decimal, str]:
    grading_type = str(assignment.get("grading_type") or "")
    try:
        points = Decimal(str(assignment.get("points_possible")))
    except (InvalidOperation, TypeError):
        points = Decimal("0")
    submission_types = set(assignment.get("submission_types") or [])
    unsupported = bool(
        not points.is_finite() or points <= 0 or grading_type != "points"
        or assignment.get("is_quiz_assignment") or assignment.get("quiz_id")
        or assignment.get("group_category_id") or "online_quiz" in submission_types
        or assignment.get("published") is False
    )
    if unsupported:
        raise ValueError("Choose a published, individual, ordinary Canvas assignment graded in points with more than 0 points possible. Quiz and restricted grading workflows are not supported.")
    return points.quantize(CENT, rounding=ROUND_HALF_UP), grading_type


def map_assessment_course(assessment_id: int, course: dict) -> CanvasAssessmentMapping:
    course_id = _numeric_id(str(course.get("id", "")), "course")
    name = str(course.get("name") or "").strip()
    if not name:
        raise ValueError("Canvas did not return a course name.")
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    if mapping is None:
        mapping = CanvasAssessmentMapping(assessment_id=assessment_id, course_id=course_id, course_name=name)
        db.session.add(mapping)
    else:
        if mapping.course_id != course_id:
            mapping.assignment_id = None
            mapping.assignment_name = None
            mapping.points_possible = None
            mapping.grading_type = None
        mapping.course_id = course_id
        mapping.course_name = name
    db.session.commit()
    return mapping


def map_assessment_assignment(mapping: CanvasAssessmentMapping, assignment: dict) -> None:
    assignment_id = _numeric_id(str(assignment.get("id", "")), "assignment")
    points, grading_type = ensure_supported_assignment(assignment)
    if str(assignment.get("course_id", mapping.course_id)) != mapping.course_id:
        raise ValueError("That Canvas assignment does not belong to the selected course.")
    name = str(assignment.get("name") or "").strip()
    if not name:
        raise ValueError("Canvas did not return an assignment name.")
    mapping.assignment_id = assignment_id
    mapping.assignment_name = name
    mapping.points_possible = points
    mapping.grading_type = grading_type
    db.session.commit()


def _finalized_submissions(assessment_id: int) -> list[Submission]:
    unresolved = select(Response.id).where(
        Response.submission_id == Submission.id,
        Response.correctness.is_(None),
    ).correlate(Submission).exists()
    return db.session.scalars(select(Submission).where(
        Submission.assessment_id == assessment_id,
        Submission.processing_status == "processed",
        Submission.review_status != "required",
        Submission.total_points > 0,
        ~unresolved,
    ).order_by(Submission.student_id)).all()


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError):
        return None


def _posted_grade(submission: Submission, points_possible: Decimal) -> Decimal:
    score, total = _decimal(submission.score), _decimal(submission.total_points)
    if score is None or total is None or total <= 0:
        raise ValueError("A finalized Paparazzi score is invalid and cannot be sent to Canvas.")
    return (score / total * points_possible).quantize(CENT, rounding=ROUND_HALF_UP)


def _sis_candidates(user: dict, student_number: str) -> bool:
    # Keep the school identifier as a string. Numeric conversion would discard leading zeros.
    return any(str(user.get(key) or "").strip() == student_number for key in ("sis_user_id", "sis_login_id"))


def build_grade_preview(assessment_id: int, client: CanvasClient | None = None) -> dict:
    assessment = db.session.get(Assessment, assessment_id)
    mapping = db.session.scalar(select(CanvasAssessmentMapping).where(CanvasAssessmentMapping.assessment_id == assessment_id))
    if assessment is None or mapping is None or not mapping.assignment_id:
        raise ValueError("Select a Canvas course and compatible assignment for this assessment first.")
    client = client or get_canvas_client(_active_config())
    accessible_courses = teacher_courses(client)
    if not any(str(course.get("id")) == mapping.course_id for course in accessible_courses):
        raise CanvasAPIError("The mapped course is no longer accessible to this Canvas teacher.")
    assignments = list_assignments(client, mapping.course_id)
    assignment = next((item for item in assignments if str(item.get("id")) == mapping.assignment_id), None)
    if assignment is None:
        raise CanvasAPIError("The selected Canvas assignment is no longer available in this course.")
    points_possible, grading_type = ensure_supported_assignment(assignment)
    roster = client.roster(mapping.course_id)
    roster_by_id = {str(item.get("id")): item for item in roster if item.get("id") is not None}
    submissions = client.assignment_submissions(mapping.course_id, mapping.assignment_id)
    grade_by_user = {str(item.get("user_id")): _decimal(item.get("score")) for item in submissions if item.get("user_id") is not None}
    roster_id_by_sis: dict[str, list[str]] = {}
    for user in roster:
        user_id = str(user.get("id", ""))
        for key in ("sis_user_id", "sis_login_id"):
            identifier = str(user.get(key) or "").strip()
            if identifier:
                roster_id_by_sis.setdefault(identifier, [])
                if user_id not in roster_id_by_sis[identifier]:
                    roster_id_by_sis[identifier].append(user_id)
    mappings = {row.student_number: row for row in db.session.scalars(select(CanvasStudentMapping).where(CanvasStudentMapping.course_id == mapping.course_id)).all()}
    submissions_final = _finalized_submissions(assessment_id)
    students = {student.id: student for student in db.session.scalars(select(Student).where(Student.id.in_([item.student_id for item in submissions_final]))).all()} if submissions_final else {}
    history_rows = db.session.scalars(select(CanvasGradeSync).where(
        CanvasGradeSync.assessment_id == assessment_id,
        CanvasGradeSync.course_id == mapping.course_id,
        CanvasGradeSync.assignment_id == mapping.assignment_id,
    ).order_by(CanvasGradeSync.created_at.desc(), CanvasGradeSync.id.desc())).all()
    latest_history: dict[str, CanvasGradeSync] = {}
    for item in history_rows:
        latest_history.setdefault(item.student_number, item)
    preview_rows = []
    for submission in submissions_final:
        student = students.get(submission.student_id)
        if student is None:
            continue
        number = str(student.student_number)  # preserve leading zeroes
        manual = mappings.get(number)
        match_status = "unmatched"
        canvas_user = None
        if manual:
            candidate = roster_by_id.get(manual.canvas_user_id)
            if candidate:
                canvas_user = candidate
                match_status = "manual match verified"
            else:
                match_status = "saved match is not in the selected course roster"
        if canvas_user is None and match_status == "unmatched":
            identifiers = roster_id_by_sis.get(number, [])
            if len(identifiers) == 1:
                canvas_user = roster_by_id.get(identifiers[0])
                match_status = "matched by SIS ID" if canvas_user else "unmatched"
            elif len(identifiers) > 1:
                match_status = "ambiguous SIS ID"
        score = _posted_grade(submission, points_possible)
        user_id = str(canvas_user.get("id")) if canvas_user else None
        previous = grade_by_user.get(user_id) if user_id else None
        latest = latest_history.get(number)
        same_success = bool(
            latest and latest.status == "succeeded"
            and _decimal(latest.posted_grade) == score and previous == score
        )
        status = "Matched" if match_status in {"matched by SIS ID", "manual match verified"} else "Needs mapping"
        if previous is not None:
            status = "Already synchronized" if same_success else "Canvas grade exists — overwrite needs confirmation"
        preview_rows.append({
            "student_db_id": student.id,
            "student_number": number,
            "student_name": student.name,
            "canvas_user_id": user_id,
            "canvas_name": str(canvas_user.get("name") or canvas_user.get("short_name") or "") if canvas_user else "Unmatched",
            "match_status": match_status,
            "paparazzi_score": _decimal(submission.score),
            "paparazzi_total": _decimal(submission.total_points),
            "canvas_grade": score,
            "points_possible": points_possible,
            "previous_grade": previous,
            "status": status,
            "same_success": same_success,
            "submission_id": submission.id,
        })
    return {
        "assessment": assessment,
        "mapping": mapping,
        "assignment": assignment,
        "points_possible": points_possible,
        "grading_type": grading_type,
        "rows": preview_rows,
        "roster": roster,
        "courses": accessible_courses,
    }


def _active_config():
    from flask import current_app
    return current_app.config


def _record_sync(assessment_id, mapping, row, status, *, error=None, verified_at=None):
    sync = CanvasGradeSync(
        assessment_id=assessment_id,
        course_id=mapping.course_id,
        assignment_id=mapping.assignment_id,
        student_number=row["student_number"],
        canvas_user_id=row.get("canvas_user_id"),
        posted_grade=row.get("canvas_grade"),
        previous_grade=row.get("previous_grade"),
        status=status,
        error_detail=(str(error)[:1000] if error else None),
        authorized_by="local teacher; explicit grade confirmation",
        verified_at=verified_at,
    )
    db.session.add(sync)
    db.session.commit()
    return sync


def synchronize_selected_grades(assessment_id: int, selected_ids: set[str], overwrite_ids: set[str], config: dict, *, client=None) -> list[dict]:
    client = client or get_canvas_client(config)
    preview = build_grade_preview(assessment_id, client)
    mapping = preview["mapping"]
    result = []
    for row in preview["rows"]:
        student_key = str(row["student_db_id"])
        reason = None
        if row["same_success"]:
            reason = "This exact grade was already synchronized successfully."
        elif row["match_status"] not in {"matched by SIS ID", "manual match verified"}:
            reason = row["match_status"].capitalize() + "; no grade was sent."
        elif student_key not in selected_ids:
            reason = "Excluded by the teacher from this upload."
        elif row["previous_grade"] is not None and student_key not in overwrite_ids:
            reason = "Canvas already has a grade; overwrite was not confirmed."
        if reason:
            sync = _record_sync(assessment_id, mapping, row, "skipped", error=reason)
            result.append({"student_number": row["student_number"], "status": sync.status, "reason": reason})
            continue
        # Persist an explicit failed placeholder before the network write so process interruption is visible.
        sync = _record_sync(assessment_id, mapping, row, "failed", error="Canvas update is awaiting verification.")
        try:
            client.post_grade(mapping.course_id, mapping.assignment_id, row["canvas_user_id"], f"{row['canvas_grade']:.2f}")
            actual = client.submission(mapping.course_id, mapping.assignment_id, row["canvas_user_id"])
            actual_grade = _decimal(actual.get("score") if isinstance(actual, dict) else None)
            if actual_grade != row["canvas_grade"]:
                raise CanvasAPIError("Canvas did not confirm the requested score on readback.")
            sync.status = "succeeded"
            sync.error_detail = None
            sync.verified_at = datetime.now(timezone.utc)
            db.session.commit()
            result.append({"student_number": row["student_number"], "status": "succeeded", "grade": row["canvas_grade"]})
        except CanvasAPIError as error:
            db.session.rollback()
            sync = db.session.get(CanvasGradeSync, sync.id)
            if sync:
                sync.status = "failed"
                sync.error_detail = str(error)[:1000]
                db.session.commit()
            result.append({"student_number": row["student_number"], "status": "failed", "reason": str(error)})
        except Exception:
            db.session.rollback()
            sync = db.session.get(CanvasGradeSync, sync.id)
            if sync:
                sync.status = "failed"
                sync.error_detail = "Canvas update could not be verified; inspect Canvas before retrying."
                db.session.commit()
            result.append({"student_number": row["student_number"], "status": "failed", "reason": "Canvas update could not be verified; inspect Canvas before retrying."})
    return result


def retry_failed_grade_sync(sync_id: int, config: dict, *, client=None) -> dict:
    failed = db.session.get(CanvasGradeSync, sync_id)
    if failed is None or failed.status != "failed":
        raise ValueError("Only a failed Canvas grade upload can be retried.")
    client = client or get_canvas_client(config)
    preview = build_grade_preview(failed.assessment_id, client)
    row = next((item for item in preview["rows"] if item["student_number"] == failed.student_number), None)
    if row is None or row["canvas_user_id"] != failed.canvas_user_id or row["canvas_grade"] != _decimal(failed.posted_grade):
        raise ValueError("The student's match or Paparazzi score has changed. Review the current grade preview before retrying.")
    current = client.submission(failed.course_id, failed.assignment_id, failed.canvas_user_id)
    current_grade = _decimal(current.get("score") if isinstance(current, dict) else None)
    if current_grade != _decimal(failed.previous_grade) and current_grade != _decimal(failed.posted_grade):
        raise ValueError("The Canvas grade changed since the failed attempt. Review the grade preview before retrying.")
    mapping = preview["mapping"]
    retry_row = {**row, "previous_grade": current_grade}
    retry = _record_sync(failed.assessment_id, mapping, retry_row, "failed", error="Retry is awaiting Canvas verification.")
    if current_grade != row["canvas_grade"]:
        try:
            client.post_grade(failed.course_id, failed.assignment_id, failed.canvas_user_id, f"{row['canvas_grade']:.2f}")
        except CanvasAPIError as error:
            retry.error_detail = str(error)[:1000]
            db.session.commit()
            return {"status": "failed", "reason": str(error)}
    actual = client.submission(failed.course_id, failed.assignment_id, failed.canvas_user_id)
    if _decimal(actual.get("score") if isinstance(actual, dict) else None) != row["canvas_grade"]:
        retry.error_detail = "Canvas did not confirm the requested score on retry."
        db.session.commit()
        return {"status": "failed", "reason": retry.error_detail}
    retry.status = "succeeded"
    retry.error_detail = None
    retry.verified_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"status": "succeeded", "grade": row["canvas_grade"]}
