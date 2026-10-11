from __future__ import annotations

import csv
import io


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

    return not errors, errors


def import_students_from_csv(file_storage) -> tuple[list[dict], list[str]]:
    raw = file_storage.read()
    stream = io.StringIO(raw.decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    valid_rows: list[dict] = []
    invalid_rows: list[str] = []

    for row_number, row in enumerate(reader, start=2):
        student_number = (row.get("student_number") or "").strip()
        name = (row.get("name") or "").strip()
        class_section = (row.get("class_section") or "").strip()
        if not student_number or not name or not class_section:
            invalid_rows.append(f"Row {row_number}: missing required fields")
            continue
        valid_rows.append({
            "student_number": student_number,
            "name": name,
            "class_section": class_section,
        })

    return valid_rows, invalid_rows
