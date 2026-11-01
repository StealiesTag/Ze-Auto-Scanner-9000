# Paparazzi Standard V3

## Source assets

The three revised PDFs, combined PDF, and active profile are under `static/answer_sheets/v3/Paparazzi_Answer_Sheets_V3_Package/`. The original PDFs and profile are preserved in `originals/`. Run `python scripts/generate_paparazzi_v3_pdfs.py` to regenerate the PDFs from those backups; the generator only changes answer-bubble outlines.

- `Answer_Sheet_20Q_V3.pdf`
- `Answer_Sheet_50Q_V3.pdf`
- `Answer_Sheet_100Q_V3.pdf`
- `Answer_Sheets_20_50_100_V3.pdf`
- `Paparazzi_Standard_V3_Profile.json`

`Paparazzi_Standard_V3_Vector_Origins.json` records grid origins measured from the original vector circles. All answer centers and pitches remain unchanged.

## Geometry and detector

All PDFs are single-page A4 portrait (210 x 297 mm). The four filled 6 mm square marks with 7 mm edge insets, 1.12 mm student-ID bubble radius, 10.1 mm ID-column pitch, 2.85 mm ID-row pitch, and 5.15 mm answer-row pitch are unchanged. Answer bubbles are enlarged from 1.48 mm to 1.85 mm radius (3.70 mm diameter, a 25% increase); the closest answer-bubble center pitch is 5.0 mm, leaving 1.30 mm between outlines. First ID center (30.3, 46.85) mm, first answer-row center Y=98.15 mm, and 5 mm answer-choice pitch are preserved.

`services/paparazzi_v3_scanner.py` loads the profile and measured origins, detects candidate square contours with Otsu and adaptive thresholding, checks four-corner arrangement and page aspect, tries four page rotations, and chooses the orientation with the strongest fit to the profile's printed bubble rings. It rectifies to A4 at 300 DPI. OMR uses a per-bubble local background ring to derive fill measurements, then reports detected, blank, multiple-mark, or ambiguous states and confidence. Seven ID columns are decoded with the same bubble scoring. Unmatched or unreadable IDs require manual student selection; assessment association is selected before upload because the printed handwritten assessment-code area is not OCR-read.

The legacy ArUco/QR pipeline remains separate and unchanged. Manual image upload and the optional ESP32 capture queue both feed the same V3 draft service. ESP32 software behavior and physical validation status are documented in [esp32-automatic-scanning.md](esp32-automatic-scanning.md).

## Database and review

Migration `20261101_0002` adds optional `option_e`, widens answer constraints to A–E, and creates `scan_drafts`. Existing A–D rows are retained. Assessment forms allow up to 100 questions and E is optional on legacy assessments; V3 processing requires E text for every question. V3 scans are stored as drafts with original, aligned, and diagnostic images. A teacher must select/verify the student, review each answer, explicitly choose blank or A–E, and confirm before the existing grading service creates a Submission.

Apply the migration to the configured PostgreSQL database before using the scan-draft page:

```powershell
python -m flask --app app db upgrade
```

For an existing deployment, take and verify a PostgreSQL backup first, confirm `flask db current` reports `20261026_0001`, and apply the upgrade during a maintenance window. Then confirm `flask db current` reports `20261101_0002` and run the PostgreSQL integration suite against a dedicated database whose name ends in `_test` or `_testing`. Never point `TEST_DATABASE_URL` at the configured application database. The upgrade retains existing A–D rows and initializes `option_e` to an empty string. Downgrading from V3 drops `scan_drafts` and `option_e`; it can fail if any E answers have been stored, so do not use downgrade as a routine rollback after V3 data exists.

## Verification status

Automated tests render the revised PDFs and verify page boxes, marker placement, answer and ID bubble dimensions/centers, safe spacing, all layouts, all four orientations, perspective correction, student-ID decoding, blank/light/multiple/ambiguous marks, low contrast, uneven light, blur/noise, and fail-closed invalid pages. The supplied camera image remains a regression fixture and is rejected when its marks do not meet V3 geometry. Tests are synthetic; physical accuracy has not been measured.

These are PDF-rendered synthetic marks, not photographs of physically printed sheets. No physical-printer/camera accuracy claim is made. Test printed sheets under varied lighting and camera conditions before relying on grades in production.
