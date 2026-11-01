# ESP32 automatic V3 scanning

## Current validation status

The software integration uses synthetic V3 PDF renders and PostgreSQL test fixtures. No ESP32 camera, printed paper, or ejector mechanism has been connected in this workspace. The bottom-marker disappearance/reappearance hypothesis remains unvalidated on the physical mechanism. Do not use unattended automatic capture for grading until physical tests pass.

The transition is deliberately fail-closed: after a successful capture, four visible markers cannot trigger another capture. The bottom markers must be absent across consecutive frames, then a valid, stable four-marker page must return, and the normalized page signature must differ from the previous capture. This suppresses repeat captures and short obstructions. If consecutive physical sheets produce indistinguishable images (for example, blank pages with identical positioning), the signature may not change enough and the next sheet will wait for review instead of capturing. A long obstruction that resembles an ejection can still be ambiguous without a physical sensor; tune and test using the actual ejector.

## Camera configuration

Set adjustable URLs and thresholds in the ignored local `.env` file. The endpoint examples below match common Espressif CameraWebServer sketches; use the endpoints exposed by the installed firmware rather than assuming a fixed camera address:

```env
ESP32_STREAM_URL=http://192.168.1.80:81/stream
ESP32_CAPTURE_URL=http://192.168.1.80/capture
ESP32_REQUEST_TIMEOUT_SECONDS=8
CAMERA_POLL_INTERVAL_SECONDS=0.2
CAMERA_STABLE_FRAME_COUNT=5
CAMERA_BOTTOM_MISSING_FRAME_COUNT=5
CAMERA_MARKER_MOVEMENT_TOLERANCE_PX=3.0
CAMERA_MIN_PAGE_CHANGE_RATIO=0.0015
CAMERA_MIN_PAGE_AREA_RATIO=0.25
CAPTURE_QUEUE_CAPACITY=25
```

The browser preview uses the configured MJPEG URL. The server reads that same live stream for detection; high-resolution captures use only the still endpoint. HTTP errors, disconnections, invalid MIME types, invalid images, timeout, or oversize images are reported in the dashboard. Camera timeouts do not create jobs.

## Start and operate

1. Back up the PostgreSQL database, inspect the Alembic revision and generated SQL, then apply the pending migrations through `20261101_0004`. This adds the AI suggestions table from `20261101_0003`, then scanner control and capture-job tables. No migration has been applied by this implementation task.
2. Start the Flask app on localhost.
3. In another terminal, run the single camera producer and queue consumer:

   ```powershell
   python -m services.scan_worker
   ```

   A PostgreSQL session advisory lock prevents a second scan worker from starting. The Flask app itself does not spawn worker threads.
4. Open **Automatic scan**, select an assessment with a complete 20/50/100-question A–E key, and start scanning. Pause or stop before changing the assessment. Manual camera capture is available while stopped or paused. Existing file upload remains available at **Scan sheets**.
5. Inspect each queued image under **Recent captures**. The consumer creates a normal V3 scan draft; a teacher still selects/verifies the student and every answer before grading. Confirming that draft marks its capture job completed.

Pause stops new automatic still requests while the consumer continues processing. Stop does the same and clears the active flag. A full queue pauses automatic capture before the still request. Resume revalidates marker geometry and stability. Failed processing retries reuse the same capture ID and preserved original image. Pending `PROCESSING` jobs are returned to `QUEUED` at worker startup; capture IDs and draft notes make the processing step idempotent if a worker stops after draft creation.

## Marker and replacement detection

Live grayscale frames use the V3 profile and vector-origin measurements in `static/answer_sheets/v3/Paparazzi_Answer_Sheets_V3_Package/`. The detector reuses the scanner's CLAHE/Otsu/adaptive square candidate extraction and profile geometry scoring. It checks corner arrangement, expected A4 ratio and marker size, projected page bounds, and minimum page area. It returns marker coordinates, geometry confidence/status, bottom-marker visibility, page visibility, and a normalized signature.

The controller requires multiple stable frames for the first sheet. After queue success it locks. It counts persistent bottom-marker disappearance, waits for a valid full-page geometry to return, requires another stable period, and compares the normalized page signature to the prior capture before allowing one still request. Marker visibility alone never unlocks it. The signature threshold and frame counts are configurable. The same page signature is required to differ from the previous one; it is evidence for a replacement, not proof of physical identity.

## Queue and status

PostgreSQL `capture_jobs` stores a unique capture ID, assessment, original filename, source, status, retry count, timestamps, linked scan draft/submission, and error. The original still is written before the job becomes visible to the consumer. A single consumer calls `create_v3_scan_draft`, then normal manual review and grading remain unchanged. Status values are `QUEUED`, `PROCESSING`, `NEEDS_REVIEW`, `COMPLETED`, and `FAILED`. The browser polls camera, marker, controller, and queue status without blocking on OMR.

If the dashboard reports the migration is missing, keep using manual upload until Alembic is upgraded. If camera status remains disconnected, check both URLs from the Paparazzi host and verify that the still endpoint returns JPEG or PNG. If capture is paused with an error, correct the underlying issue before resuming. Failed jobs can be retried only from their preserved image.

## Tests

`tests/test_automatic_scanning.py` uses rendered official V3 PDFs, synthetic marks, generated frames, mocked camera HTTP responses, and the isolated PostgreSQL test database. It checks marker geometry, lighting, partial pages, false square candidates, stable capture, replacement transitions, queue capacity/order/recovery/retry, preservation of one capture ID, and worker processing into the existing V3 draft/grading flow. These tests are synthetic and do not establish physical reliability.
