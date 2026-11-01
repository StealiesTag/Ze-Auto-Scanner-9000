# Paparazzi Standard V3

## Source assets

The five supplied PDFs and the original JSON profile are kept under `static/answer_sheets/v3/Paparazzi_Answer_Sheets_V3_Package/` and are served directly without regeneration or modification:

- `Answer_Sheet_20Q_V3.pdf`
- `Answer_Sheet_50Q_V3.pdf`
- `Answer_Sheet_100Q_V3.pdf`
- `Answer_Sheets_20_50_100_V3.pdf`
- `Paparazzi_Standard_V3_Profile.json`

`Paparazzi_Standard_V3_Vector_Origins.json` is a separate addendum. It records only the grid origins measured from the vector circles in the supplied individual PDFs because the original profile specified mark positions and pitches but omitted grid origins. The originals remain unchanged.

## Geometry and detector

All PDFs are single-page A4 portrait (210 × 297 mm). Their vector paths confirm four filled 6 mm square marks with 7 mm edge insets, 1.12 mm student-ID bubble radius, 1.48 mm answer bubble radius, 10.1 mm ID-column pitch, 2.85 mm ID-row pitch, and 5.15 mm answer-row pitch. Measured from the vector source, the first ID center is (30.3, 46.85) mm, first answer-row center is Y=98.15 mm, and answer-choice pitch is 5 mm. First answer-column X centers are stored per question count in the vector-origin addendum.

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

Automated tests render the actual PDFs and verify page boxes, mark dimensions/locations, ID/answer bubble counts and centers, all three layouts, 0/90/180-degree orientation, perspective warp, ID decoding, blank and multiple marks, and fail-closed invalid pages. A PostgreSQL integration test exercises draft persistence and grading when `TEST_DATABASE_URL` is configured.

These are PDF-rendered synthetic marks, not photographs of physically printed sheets. No physical-printer/camera accuracy claim is made. Test printed sheets under varied lighting and camera conditions before relying on grades in production.
