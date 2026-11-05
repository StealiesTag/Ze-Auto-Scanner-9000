"""Validate and store source test PDFs with extracted text for teaching suggestions."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

import fitz
from werkzeug.utils import secure_filename


MAX_EXTRACTED_TEXT_CHARS = 100_000
MAX_PDF_PAGES = 100


def read_assessment_pdf(upload, max_bytes: int) -> dict:
    filename = secure_filename(upload.filename or "")
    if not filename or Path(filename).suffix.lower() != ".pdf":
        raise ValueError("Upload the original assessment as a PDF file.")
    data = upload.stream.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise ValueError(f"The source PDF must be smaller than {max_bytes // (1024 * 1024)} MB.")
    if not data.startswith(b"%PDF-"):
        raise ValueError("The selected file is not a valid PDF.")
    try:
        document = fitz.open(stream=data, filetype="pdf")
        if document.needs_pass:
            raise ValueError("Password-protected PDFs are not supported.")
        if len(document) < 1 or len(document) > MAX_PDF_PAGES:
            raise ValueError(f"The source PDF must contain between 1 and {MAX_PDF_PAGES} pages.")
        chunks = []
        page_texts = []
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            page_texts.append({"page_number": index, "text": text})
            if text:
                chunks.append(f"[Page {index}]\n{text}")
        document.close()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("The PDF could not be read. Re-export it as a standard PDF and try again.") from error
    extracted = "\n\n".join(chunks)[:MAX_EXTRACTED_TEXT_CHARS]
    if len(extracted.strip()) < 30:
        raise ValueError("This PDF has no selectable question text. OCR it or add the question prompts to the answer key before uploading.")
    return {"bytes": data, "filename": filename, "text": extracted, "pages": page_texts,
            "page_count": len(page_texts), "sha256": hashlib.sha256(data).hexdigest()}


_QUESTION_LINE = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,3})(?!\d)\s*[.):\-]\s*(.+)$", re.IGNORECASE)


def map_question_text(pages: list[dict], question_number: int) -> tuple[int | None, str | None]:
    """Find a numbered question block without guessing when the PDF has no clear numbering."""
    for page in pages:
        lines = (page.get("text") or "").splitlines()
        starts = []
        for index, line in enumerate(lines):
            match = _QUESTION_LINE.match(line)
            if match:
                starts.append((index, int(match.group(1))))
        for position, (line_index, number) in enumerate(starts):
            if number != question_number:
                continue
            end = starts[position + 1][0] if position + 1 < len(starts) else min(len(lines), line_index + 16)
            excerpt = "\n".join(lines[line_index:end]).strip()
            if excerpt:
                return int(page["page_number"]), excerpt[:3000]
    return None, None


def attach_assessment_pdf(assessment, pdf: dict, upload_folder: str) -> None:
    root = Path(upload_folder).resolve()
    folder = root / "assessment_sources"
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"assessment-{assessment.id}-{uuid.uuid4().hex}.pdf"
    destination = folder / stored_name
    temporary = destination.with_suffix(".pdf.tmp")
    temporary.write_bytes(pdf["bytes"])
    os.replace(temporary, destination)
    old_relative = assessment.source_pdf_path
    assessment.source_pdf_filename = pdf["filename"]
    assessment.source_pdf_path = (Path("assessment_sources") / stored_name).as_posix()
    assessment.source_pdf_text = pdf["text"]
    assessment.source_pdf_sha256 = pdf["sha256"]
    assessment.source_pdf_page_count = pdf["page_count"]
    for question in assessment.questions:
        question.source_pdf_page, question.source_pdf_excerpt = map_question_text(pdf["pages"], question.question_number)
        question.source_pdf_mapping_reviewed = False
    try:
        from extensions import db
        db.session.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if old_relative:
        old_path = (root / old_relative).resolve()
        if old_path.is_relative_to(root) and old_path != destination:
            old_path.unlink(missing_ok=True)
