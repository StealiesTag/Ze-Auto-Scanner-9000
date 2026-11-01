"""Safely enlarge V3 answer bubbles in the supplied vector PDFs.

The first run keeps byte-for-byte copies of the original PDFs and profile in
the package's ``originals`` directory. Answer bubble centers and every other
vector element remain in place.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pymupdf


ASSET_DIR = Path(__file__).resolve().parents[1] / "static" / "answer_sheets" / "v3" / "Paparazzi_Answer_Sheets_V3_Package"
BACKUP_DIR = ASSET_DIR / "originals"
PROFILE_NAME = "Paparazzi_Standard_V3_Profile.json"
ORIGINS_NAME = "Paparazzi_Standard_V3_Vector_Origins.json"
POINTS_PER_MM = 72.0 / 25.4


def _backup_originals() -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    names = [*(f"Answer_Sheet_{n}Q_V3.pdf" for n in (20, 50, 100)), "Answer_Sheets_20_50_100_V3.pdf"]
    for name in names:
        source, backup = ASSET_DIR / name, BACKUP_DIR / name
        if not backup.exists():
            shutil.copy2(source, backup)
    profile_backup = BACKUP_DIR / PROFILE_NAME
    if not profile_backup.exists():
        original_profile = json.loads((ASSET_DIR / PROFILE_NAME).read_text(encoding="utf-8"))
        # Derive the original radius from the unmodified source PDF vector.
        source_doc = pymupdf.open(BACKUP_DIR / "Answer_Sheet_20Q_V3.pdf")
        answer_diameter = next(
            drawing["rect"].width / POINTS_PER_MM
            for drawing in source_doc[0].get_drawings()
            if abs(drawing["rect"].width - drawing["rect"].height) < 0.02
            and abs(drawing["rect"].width / POINTS_PER_MM - 2.96) < 0.02
        )
        source_doc.close()
        original_profile["answers"]["bubble_radius_mm"] = round(answer_diameter / 2, 2)
        profile_backup.write_bytes((json.dumps(original_profile, indent=2) + "\n").encode("utf-8"))


def _answer_centers_mm(count: int, profile: dict, origins: dict) -> list[tuple[float, float]]:
    layout = profile["answers"]["layouts"][str(count)]
    xs = origins["answers"]["column_first_bubble_x_mm_by_question_count"][str(count)]
    first_y = origins["answers"]["first_row_center_y_mm"]
    row_pitch = profile["answers"]["row_pitch_mm"]
    choice_pitch = origins["answers"]["choice_pitch_mm"]
    points = []
    for question_index in range(count):
        col, row = divmod(question_index, layout["rows_per_column"])
        points.extend((xs[col] + choice * choice_pitch, first_y + row * row_pitch) for choice in range(5))
    return points


def _update_pdf(count: int, profile: dict, origins: dict) -> None:
    path = ASSET_DIR / f"Answer_Sheet_{count}Q_V3.pdf"
    doc = pymupdf.open(BACKUP_DIR / path.name)
    page = doc[0]
    circles = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        diameter_mm = rect.width / POINTS_PER_MM
        if abs(rect.width - rect.height) < 0.02 and abs(diameter_mm - 2.96) < 0.02 and drawing.get("color") == (0.0, 0.0, 0.0):
            circles.append(drawing)
    expected = count * len(profile["answers"]["choices"])
    if len(circles) != expected:
        raise RuntimeError(f"{path.name}: expected {expected} original answer bubbles, found {len(circles)}")

    # Cover only each original answer circle, including its stroke, then redraw
    # at exactly the same center with the profile radius.
    for drawing in circles:
        rect = drawing["rect"]
        center = rect.tl + (rect.br - rect.tl) * 0.5
        old_radius = rect.width / 2
        cover = old_radius + float(drawing.get("width") or 0.7) / 2 + 0.15
        page.draw_circle(center, cover, color=None, fill=(1, 1, 1), overlay=True)
        page.draw_circle(
            center,
            profile["answers"]["bubble_radius_mm"] * POINTS_PER_MM,
            color=(0, 0, 0),
            fill=(1, 1, 1),
            width=float(drawing.get("width") or 0.7),
            overlay=True,
        )
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def main() -> None:
    _backup_originals()
    profile_path = ASSET_DIR / PROFILE_NAME
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    origins = json.loads((ASSET_DIR / ORIGINS_NAME).read_text(encoding="utf-8"))
    if profile["version"] != origins["version"]:
        raise RuntimeError("V3 profile and measured vector origins version differ")
    for count in (20, 50, 100):
        _update_pdf(count, profile, origins)
    combined = pymupdf.open()
    for count in (20, 50, 100):
        source = pymupdf.open(ASSET_DIR / f"Answer_Sheet_{count}Q_V3.pdf")
        combined.insert_pdf(source)
        source.close()
    combined.save(ASSET_DIR / "Answer_Sheets_20_50_100_V3.pdf", garbage=4, deflate=True)
    combined.close()


if __name__ == "__main__":
    main()
