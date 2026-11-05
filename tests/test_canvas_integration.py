from decimal import Decimal
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from services.canvas_api import CanvasAPIError, CanvasClient, validate_canvas_base_url
from services.canvas_crypto import TokenEncryptionError, decrypt_access_token, encrypt_access_token
from services.canvas_grades import _posted_grade, ensure_supported_assignment
from services.canvas_grades import build_grade_preview, retry_failed_grade_sync, synchronize_selected_grades
from extensions import db
from models import Assessment, CanvasAssessmentMapping, CanvasGradeSync, Question, Response, Student, Submission


@pytest.mark.parametrize("url", [
    "http://canvas.example.edu", "https://localhost", "https://127.0.0.1",
    "https://canvas.example.edu/admin", "https://teacher:password@canvas.example.edu",
    "https://canvas.example.edu:8443",
])
def test_canvas_rejects_unsafe_or_noninstitution_urls(url):
    with pytest.raises(ValueError):
        validate_canvas_base_url(url)


def test_assignment_validation_and_grade_scaling():
    assignment = {"id": 12, "course_id": 7, "name": "Unit test", "grading_type": "points", "points_possible": 17, "published": True, "submission_types": ["online_upload"]}
    assert ensure_supported_assignment(assignment) == (Decimal("17.00"), "points")
    with pytest.raises(ValueError, match="published, individual"):
        ensure_supported_assignment({**assignment, "is_quiz_assignment": True})
    with pytest.raises(ValueError, match="published, individual"):
        ensure_supported_assignment({**assignment, "published": False})
    submission = Submission(score=2, total_points=3)
    assert _posted_grade(submission, Decimal("10")) == Decimal("6.67")


class _Response:
    def __init__(self, body, link=""):
        self.status = 200
        self.headers = Message()
        if link:
            self.headers["Link"] = link
        self.body = body

    def read(self, limit):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_canvas_pagination_follows_only_same_secure_origin():
    calls = []

    class Opener:
        def open(self, request: Request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return _Response(b'[{"id":1}]', '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next"')
            return _Response(b'[{"id":2}]')

    client = CanvasClient("https://canvas.example.edu", "test-token", opener=Opener())
    assert [row["id"] for row in client.paged("courses")] == [1, 2]
    assert len(calls) == 2

    class UnsafeOpener:
        def open(self, request: Request, timeout):
            return _Response(b'[]', '<https://canvas.example.edu:8443/steal>; rel="next"')

    with pytest.raises(CanvasAPIError, match="outside the configured"):
        CanvasClient("https://canvas.example.edu", "test-token", opener=UnsafeOpener()).paged("courses")


def test_canvas_auth_errors_are_sanitized_and_get_rate_limit_retries(monkeypatch):
    class Unauthorized:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 401, "unauthorized", Message(), BytesIO(b"token-value must not appear"))

    with pytest.raises(CanvasAPIError, match="rejected the authorization token") as error:
        CanvasClient("https://canvas.example.edu", "token-value", opener=Unauthorized()).self_user()
    assert "token-value" not in str(error.value)

    calls = []

    class RateLimited:
        def open(self, request, timeout):
            calls.append(1)
            if len(calls) == 1:
                headers = Message()
                headers["Retry-After"] = "0"
                raise HTTPError(request.full_url, 429, "limited", headers, BytesIO())
            return _Response(b'[{"id":10}]')

    monkeypatch.setattr("services.canvas_api.time.sleep", lambda _seconds: None)
    assert CanvasClient("https://canvas.example.edu", "token-value", opener=RateLimited()).get("courses") == [{"id": 10}]
    assert len(calls) == 2


def test_canvas_token_encryption_round_trips_without_storing_plaintext():
    config = {"SECRET_KEY": "test-secret-for-dpapi", "CANVAS_TOKEN_ENCRYPTION_KEY": ""}
    try:
        encrypted = encrypt_access_token("canvas-token-test", config)
    except TokenEncryptionError as error:
        pytest.skip(f"Windows DPAPI user profile is unavailable in this test runner: {error}")
    assert encrypted.startswith("dpapi:")
    assert "canvas-token-test" not in encrypted
    assert decrypt_access_token(encrypted, config) == "canvas-token-test"


def test_canvas_routes_are_loopback_only(client):
    response = client.get("/canvas", environ_overrides={"REMOTE_ADDR": "192.0.2.12"})
    assert response.status_code == 403


def test_grade_preview_preserves_leading_zeroes_and_submit_is_explicit(app):
    class FakeCanvas:
        def __init__(self, duplicate=False):
            self.grade = None
            self.posts = []
            self.duplicate = duplicate

        def courses(self):
            return [{"id": 10, "name": "Biology"}]

        def assignments(self, _course_id):
            return [{"id": 20, "course_id": 10, "name": "Midterm", "published": True,
                     "grading_type": "points", "points_possible": 100, "submission_types": ["online_upload"]}]

        def roster(self, _course_id):
            rows = [{"id": 101, "name": "Matched Learner", "sis_user_id": "0012345"},
                    {"id": 102, "name": "Unmatched Learner", "sis_user_id": "9999999"}]
            return rows + ([{"id": 103, "name": "Duplicate SIS", "sis_login_id": "0012345"}] if self.duplicate else [])

        def assignment_submissions(self, *_args):
            return ([{"user_id": 101, "score": self.grade}] if self.grade is not None else [])

        def post_grade(self, _course, _assignment, user_id, grade):
            self.posts.append((user_id, grade))
            self.grade = float(grade)

        def submission(self, *_args):
            return {"score": self.grade}

    with app.app_context():
        assessment = Assessment(title="Canvas test", subject="Biology", class_section="1A", total_questions=1)
        question = Question(question_number=1, prompt="Q", option_a="A", option_b="B", option_c="C", option_d="D", option_e="E", correct_answer="A", points=50, learning_objective="Cells")
        assessment.questions = [question]
        student = Student(student_number="0012345", name="Matched Learner", class_section="1A")
        submission = Submission(student=student, assessment=assessment, score=42, total_points=50,
                                percentage_score=84, processing_status="processed", review_status="complete")
        submission.responses = [Response(question=question, answer="A", correctness=True, detection_status="detected")]
        db.session.add_all([assessment, student, submission])
        db.session.flush()
        mapping = CanvasAssessmentMapping(assessment_id=assessment.id, course_id="10", course_name="Biology",
                                          assignment_id="20", assignment_name="Midterm", points_possible=100,
                                          grading_type="points")
        db.session.add(mapping)
        db.session.commit()
        client = FakeCanvas()
        preview = build_grade_preview(assessment.id, client)
        row = preview["rows"][0]
        assert row["student_number"] == "0012345"
        assert row["match_status"] == "matched by SIS ID"
        assert row["canvas_grade"] == Decimal("84.00")
        assert client.posts == []

        result = synchronize_selected_grades(assessment.id, {str(student.id)}, set(), {}, client=client)
        assert result[0]["status"] == "succeeded"
        assert client.posts == [("101", "84.00")]

        duplicate_client = FakeCanvas(duplicate=True)
        duplicate_preview = build_grade_preview(assessment.id, duplicate_client)
        assert duplicate_preview["rows"][0]["match_status"] == "ambiguous SIS ID"
        assert synchronize_selected_grades(assessment.id, {str(student.id)}, set(), {}, client=duplicate_client)[0]["status"] == "skipped"
        assert duplicate_client.posts == []

        overwrite_client = FakeCanvas()
        overwrite_client.grade = 55
        overwrite_preview = build_grade_preview(assessment.id, overwrite_client)
        assert "overwrite needs confirmation" in overwrite_preview["rows"][0]["status"]
        assert synchronize_selected_grades(assessment.id, {str(student.id)}, set(), {}, client=overwrite_client)[0]["status"] == "skipped"
        assert overwrite_client.posts == []
        assert synchronize_selected_grades(assessment.id, {str(student.id)}, {str(student.id)}, {}, client=overwrite_client)[0]["status"] == "succeeded"
        assert overwrite_client.posts[-1] == ("101", "84.00")

        class ReadbackDelayed(FakeCanvas):
            def __init__(self):
                super().__init__()
                self.read_count = 0

            def submission(self, *_args):
                self.read_count += 1
                return {"score": 83 if self.read_count == 1 else self.grade}

        delayed = ReadbackDelayed()
        failed = synchronize_selected_grades(assessment.id, {str(student.id)}, set(), {}, client=delayed)[0]
        assert failed["status"] == "failed"
        failed_row = db.session.query(CanvasGradeSync).filter_by(status="failed").order_by(CanvasGradeSync.id.desc()).first()
        assert failed_row is not None
        retried = retry_failed_grade_sync(failed_row.id, {}, client=delayed)
        assert retried["status"] == "succeeded"
        assert delayed.posts == [("101", "84.00")]
