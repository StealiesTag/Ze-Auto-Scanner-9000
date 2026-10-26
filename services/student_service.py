from __future__ import annotations

import csv
import io

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Student


def validate_student_payload(payload: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    student_number = (payload.get("student_number") or "").strip()
    name = (payload.get("name") or "").strip()
    class_section = (payload.get("class_section") or "").strip()
    if not student_number:
        errors.append("Student number is required.")
    if len(student_number) > 50:
        errors.append("Student number must be 50 characters or fewer.")
    if not name:
        errors.append("Student name is required.")
    if len(name) > 200:
        errors.append("Student name must be 200 characters or fewer.")
    if not class_section:
        errors.append("Class or section is required.")
    if len(class_section) > 100:
        errors.append("Class or section must be 100 characters or fewer.")
    return not errors, errors


def list_students(search: str = "") -> list[Student]:
    query = select(Student).order_by(Student.name, Student.student_number)
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Student.name.ilike(pattern), Student.student_number.ilike(pattern)))
    return list(db.session.scalars(query))


def create_student(payload: dict) -> Student:
    valid, errors = validate_student_payload(payload)
    if not valid:
        raise ValueError(" ".join(errors))
    number = payload["student_number"].strip()
    if db.session.scalar(select(Student.id).where(Student.student_number == number)):
        raise ValueError("That student number is already in use.")
    student = Student(student_number=number, name=payload["name"].strip(), class_section=payload["class_section"].strip())
    db.session.add(student)
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ValueError("That student number is already in use.") from error
    return student


def update_student(student: Student, payload: dict) -> Student:
    valid, errors = validate_student_payload(payload)
    if not valid:
        raise ValueError(" ".join(errors))
    number = payload["student_number"].strip()
    duplicate = db.session.scalar(select(Student.id).where(Student.student_number == number, Student.id != student.id))
    if duplicate:
        raise ValueError("That student number is already in use.")
    student.student_number = number
    student.name = payload["name"].strip()
    student.class_section = payload["class_section"].strip()
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise ValueError("That student number is already in use.") from error
    return student


def import_students_from_csv(file_storage) -> tuple[list[dict], list[str]]:
    raw = file_storage.read()
    file_storage.seek(0)
    try:
        stream = io.StringIO(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return [], ["CSV must be UTF-8 encoded."]
    reader = csv.DictReader(stream)
    required = {"student_number", "name", "class_section"}
    if not reader.fieldnames or not required.issubset({name.strip() for name in reader.fieldnames}):
        return [], ["CSV must include student_number, name, and class_section columns."]
    valid_rows = []
    invalid_rows = []
    seen = set()
    for row_number, row in enumerate(reader, start=2):
        values = {key.strip(): value for key, value in row.items() if key}
        payload = {key: (values.get(key) or "").strip() for key in required}
        valid, errors = validate_student_payload(payload)
        number = payload["student_number"]
        if number in seen:
            errors.append("student number is duplicated in this CSV")
        elif number and db.session.scalar(select(Student.id).where(Student.student_number == number)):
            errors.append("student number already exists")
        if errors or not valid:
            invalid_rows.append(f"Row {row_number}: {', '.join(errors)}")
        else:
            seen.add(number)
            valid_rows.append(payload)
    if invalid_rows:
        return valid_rows, invalid_rows
    try:
        db.session.add_all(Student(**row) for row in valid_rows)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return [], ["Import conflicted with an existing student number; no rows were imported."]
    return valid_rows, []
