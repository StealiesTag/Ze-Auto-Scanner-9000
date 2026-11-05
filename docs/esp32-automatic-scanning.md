# ESP32 automatic Paparazzi scanning

## Current validation status

The ESP32 live preview and `/capture` endpoint have been reached from this workspace. The one-page print/capture timing and repeated-signature stop rule still need supervised validation with the actual printer and camera.

Automatic paper feeding is coordinated one page at a time by the browser. When each print is initiated, it waits six seconds, requests an ESP32 still, checks the official sheet's registration geometry, and queues a valid image. The next blank page is not requested until that capture is processed. Consecutive stills are perspective-normalized and compared; an identical repeat is treated as the end-of-paper signal and is not queued a second time. Physical printer behavior still needs validation because this signal cannot distinguish an empty tray from a jam or a second sheet with identical marks.

## Camera configuration

Set adjustable URLs and thresholds in the ignored local `.env` file. The endpoint examples below match common Espressif CameraWebServer sketches; use the endpoints exposed by the installed firmware rather than assuming a fixed camera address:

```env
ESP32_STREAM_URL=http://192.168.1.80:81/stream
ESP32_CAPTURE_URL=http://192.168.1.80/capture
ESP32_REQUEST_TIMEOUT_SECONDS=8
CAMERA_POLL_INTERVAL_SECONDS=3.5
CAMERA_STABLE_FRAME_COUNT=5
CAMERA_BOTTOM_MISSING_FRAME_COUNT=5
CAMERA_MARKER_MOVEMENT_TOLERANCE_PX=3.0
CAMERA_MIN_PAGE_CHANGE_RATIO=0.0015
CAMERA_MIN_PAGE_AREA_RATIO=0.25
CAPTURE_QUEUE_CAPACITY=25
```

The browser preview uses the configured MJPEG URL. Each one-page scan step requests one still from the configured capture endpoint six seconds after initiating print. HTTP errors, disconnections, invalid MIME types, invalid images, timeout, or oversize images pause the scan and are reported in the dashboard. Camera failures do not create jobs.

## Start and operate

1. Back up the PostgreSQL database, inspect the Alembic revision and migration history, then apply revisions through `20261105_0012`. This includes AI suggestions, scanner control/queue, saved camera setup/session progress, PDF mapping, and Canvas sync schema.
2. Start the Flask app on localhost and save the camera ID, stream/still URLs, and timeout once under **Automatic scanning**. Only literal private/local IP camera addresses are accepted.
3. In another terminal, run the queue consumer:

   ```powershell
   python -m services.scan_worker
   ```

   A PostgreSQL session advisory lock prevents a second scan worker from starting. The Flask app itself does not spawn worker threads.
4. Configure silent printing once on the scanning computer. Chrome can use the `--kiosk-printing` launch switch; Edge uses the managed `SilentPrintingEnabled` policy. The scanning page includes setup instructions and requires confirmation before starting. Select an assessment with a complete 20- or 50-question A-E key, then load completed sheets in the printer tray and start. For every cycle, the browser initiates one blank A4 print, waits six seconds, requests a still, validates it, and queues it before printing the next page.
5. Inspect queued images under **Recent captures**. The consumer uses the standard V4 profile, reads the student ID and answers, and creates a normal scan draft. High-confidence, fully matched readings can be graded automatically; other scans remain drafts for teacher review. The teacher can correct any uncertain fields before grading.

When the same normalized page signature appears on two consecutive captures, the second capture is discarded and the print loop ends with `AUTO_STOPPED_DUPLICATE_PAGE`. A page that fails geometry checks pauses scanning for review instead of being treated as an empty-feed signal.

Pause stops the page loop while the consumer continues processing. Stop clears the active flag. A full queue pauses before the next still request. Failed processing retries reuse the same capture ID and preserved original image. Pending `PROCESSING` jobs are returned to `QUEUED` at worker startup; capture IDs and draft notes make the processing step idempotent if a worker stops after draft creation.

## Marker and replacement detection

Live grayscale frames use the shared A4 square-mark geometry. Final answer alignment and bubble reading use the standard V4 profile; its generated profile lives alongside its generated PDFs under `static/answer_sheets/v4/`. The detector checks corner arrangement, expected A4 ratio and marker size, projected page bounds, and minimum page area. It returns marker coordinates, geometry confidence/status, bottom-marker visibility, page visibility, and a normalized signature.

Each scheduled capture validates the marker geometry on its own still image. Its normalized page signature is compared with the preceding accepted capture to detect the repeated last sheet. The signature is evidence of image similarity, not a physical page identifier; identical answer patterns and camera positioning can therefore produce an early stop.

## Queue and status

PostgreSQL `capture_jobs` stores a unique capture ID, assessment, original filename, source, status, retry count, timestamps, linked scan draft/submission, and error. The original still is written before the job becomes visible to the consumer. A single consumer calls the version-aware scan service. Capture states include waiting, stable, capturing, queuing, paused, and interrupted; job status values are `QUEUED`, `PROCESSING`, `NEEDS_REVIEW`, `COMPLETED`, and `FAILED`. The browser polls camera, marker, controller, and queue status without blocking on OMR.

If the dashboard reports the migration is missing, keep using manual upload until Alembic is upgraded. If camera status remains disconnected, check both URLs from the Paparazzi host and verify that the still endpoint returns JPEG or PNG. If capture is paused with an error, correct the underlying issue before resuming. Failed jobs can be retried only from their preserved image.

## Tests

`tests/test_automatic_scanning.py` uses rendered official V3 PDFs, synthetic marks, generated frames, mocked camera HTTP responses, and the isolated PostgreSQL test database. It checks marker geometry, lighting, partial pages, false square candidates, stable capture, replacement transitions, queue capacity/order/recovery/retry, preservation of one capture ID, and worker processing into the existing V3 draft/grading flow. These tests are synthetic and do not establish physical reliability.
