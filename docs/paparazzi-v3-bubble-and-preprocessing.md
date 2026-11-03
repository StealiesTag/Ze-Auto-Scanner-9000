# Paparazzi Standard V3 bubble and image-processing update

## Revised templates

The answer-bubble radius is now 1.85 mm (3.70 mm diameter), increased 25% from the original 1.48 mm radius (2.96 mm diameter). Answer centers, A–E spacing, row and column pitches, the student-ID section, registration marks, text, margins, and A4 page boxes are retained. The closest answer-bubble center spacing is 5.0 mm, leaving 1.30 mm between outlines. The student-ID bubble radius remains 1.12 mm.

The active geometry profile is `static/answer_sheets/v3/Paparazzi_Answer_Sheets_V3_Package/Paparazzi_Standard_V3_Profile.json`. Original PDFs and the profile are backed up in the sibling `originals/` directory. Run `python scripts/generate_paparazzi_v3_pdfs.py` to regenerate the individual 20Q, 50Q, 100Q, and combined PDFs from the preserved originals.

## Preprocessing and scanning

The scanner converts captures to grayscale, corrects broad illumination gradients, applies CLAHE, mild median denoising, and conservative sharpening only when the image appears blurred. Marker candidate extraction evaluates Otsu, adaptive Gaussian, and adaptive mean thresholds. Candidate groups must match the existing four-corner layout and expected printed marker size; image processing does not create or infer missing marks. The existing perspective transform normalizes accepted pages to the profile's A4 coordinates, and orientation scoring tries 0°, 90°, 180°, and 270°.

The preprocessing defaults can be tuned with environment variables: `PAPARAZZI_V3_ILLUMINATION_CORRECTION`, `PAPARAZZI_V3_CLAHE_ENABLED`, `PAPARAZZI_V3_CLAHE_CLIP_LIMIT`, `PAPARAZZI_V3_CLAHE_GRID_SIZE`, `PAPARAZZI_V3_DENOISE_MEDIAN_KERNEL`, `PAPARAZZI_V3_SHARPEN_AMOUNT`, `PAPARAZZI_V3_SHARPEN_BLUR_THRESHOLD`, `PAPARAZZI_V3_THRESHOLD_OTSU`, `PAPARAZZI_V3_THRESHOLD_ADAPTIVE_GAUSSIAN`, `PAPARAZZI_V3_THRESHOLD_ADAPTIVE_MEAN`, and the adaptive block/C values. Each stage keeps its current tested default when unset.

OMR uses each selected profile's bubble dimensions and spacing, with a local-background ring constrained to avoid neighboring bubbles. Blank, multiple, and near-tied uncertain responses remain unresolved for teacher review.

## User-supplied large-bubble 50Q sheet

The supplied Canva 50Q PDF keeps the A4 page and four V3 corner markers, but its answer grid differs from the standard 25/25 layout. The scanner compares both profiles after marker-based rectification and selects the one that best matches the printed bubble locations. The supplemental geometry is in `Paparazzi_50Q_Large_Bubble_Profile.json`: 20 questions in the left block, 30 in the right block, 3.81 mm answer-bubble radius, and measured row/choice centers. The standard V3 profile remains available for the original 50Q sheet.

The supplied sheet's student ID block is not used for automatic ID recognition; digits require teacher selection during review. Answer detection remains automatic with the usual blank, multiple-mark, and uncertain states. The PDF is retained as `test_fixtures/Paparazzi_50Q_Large_Bubble_User.pdf` for regression tests. No physical photograph has been validated with this format yet.

## Verification and limits

Regression tests render each revised PDF, check unchanged answer centers and student-ID geometry, marker placement, page size, and bubble spacing, and exercise synthetic marks under low contrast, illumination variation, mild blur, and noise. The supplied ESP32 camera capture is stored in `test_fixtures/paparazzi_v3_camera_capture.jpg` as a fail-closed geometry regression fixture. It remains an invalid V3 page when its candidate marks do not fit the registered geometry.

Synthetic tests are not a measurement of real-world accuracy. The supplied camera image previously failed marker geometry validation; preprocessing must not turn that invalid page into an accepted scan. Physical camera and printed-sheet accuracy remain to be measured with valid full-page captures.
