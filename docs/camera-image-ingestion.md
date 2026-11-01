# Camera image ingestion

Paparazzi accepts camera images over HTTP without requiring ESP32 firmware or an active camera connection. The endpoint validates V3 registration geometry and queues eligible pages for the existing single scan worker. OMR and grading do not run in the request. For polling an existing ESP32 CameraWebServer `/capture` URL, see [CameraWebServer poller](camera-webserver-poller.md).

## Database prerequisite

The API adds revision `20261101_0005` after `20261101_0004`; the CameraWebServer poller adds revision `20261101_0006`. These add camera state and metrics, capture metadata, and allow `api` as a capture source. They do not rewrite existing assessment, draft, or submission records. Review the generated migrations and back up the target database before deployment. **Do not run `flask db upgrade` against the configured development database without explicit authorization.**

Downgrade removes per-camera counters/state and capture validation metadata. Its migration guard refuses downgrade while any per-camera state or API capture exists. Export those records and plan an application-level rollback before attempting it.

## Endpoint

`POST /api/camera/ingest`

Send one JPEG or PNG either as multipart form data (`image` or `file`) or as raw bytes. Multipart fields or headers may provide:

| Metadata | Multipart / form field | Header | Behavior |
| --- | --- | --- | --- |
| Camera identifier | `camera_id` | `X-Camera-ID` | Optional; defaults to `default-camera`; 1–100 printable characters |
| Capture identifier | `capture_id` | `X-Capture-ID` | Optional; generated when absent; repeated accepted IDs return `duplicate` |
| Capture time | `captured_at` | `X-Captured-At` | Optional ISO-8601; naive values are interpreted as UTC |
| Content type | `content_type` | `Content-Type` | `image/jpeg` or `image/png`; multipart can infer it from the file |

The selected assessment comes from Automatic Scanning and must have a complete 20, 50, or 100 question A–E key. Start the application and the one processing worker:

```powershell
python -m flask --app app run
python -m services.scan_worker
```

Example multipart request:

```powershell
curl.exe -F "camera_id=bench-camera" -F "capture_id=bench-0001" -F "image=@filled-sheet.png;type=image/png" http://127.0.0.1:5000/api/camera/ingest
```

Or use the included Python uploader:

```powershell
python scripts/test_camera_ingest.py path\to\filled-sheet.png --camera-id bench-camera
```

The API returns JSON. `queued` (HTTP 202) means an accepted image was saved and a persistent job was committed. `rejected` means format, decoding, dimensions, marker count, geometry, page visibility, or confidence failed; these frames are not stored as jobs. `duplicate` means the sender ID was already accepted or the current physical sheet remains locked. `deferred` means a valid observation is contributing to stabilization or replacement confirmation. Errors such as a missing assessment, full queue, or unavailable database use a non-2xx status and do not queue an image.

Example accepted response:

```json
{
  "success": true,
  "status": "queued",
  "capture_id": "bench-0001",
  "detected_markers": 4,
  "geometry_valid": true,
  "confidence": 0.98,
  "job_id": 14
}
```

`GET /api/camera/captures/<capture_id>` returns job state, marker-validation metadata, and an image URL. `GET /api/camera/captures/<capture_id>/image` serves the preserved accepted original. `GET /api/camera/status` reports received, rejected, accepted, and duplicate counts.

## Validation and duplicate protection

The reusable `validate_camera_image` function checks image MIME/signature agreement, decodes JPEG/PNG, enforces configured size and dimensions, and invokes the existing V3 marker detector. That detector uses the four marker centers and expected geometry from `Paparazzi_Standard_V3_Profile.json`, the scanner's candidate and quadrilateral scoring routines, projected page bounds, and visible page area. It reports marker coordinates, geometry, confidence, and a page signature. The API applies `CAMERA_MIN_MARKER_CONFIDENCE` and `CAMERA_MIN_PAGE_AREA_RATIO`; detector thresholds stay configurable and V3 templates/geometry are unchanged.

The replacement state is stored per camera in PostgreSQL and locked while a request is evaluated, so separate Flask workers share the same state. A page must remain geometrically stable across configured consecutive observations. After acceptance, it is locked. Consecutive observations without bottom markers move the camera state to waiting for a replacement; a new valid page must then stabilize and differ from the previous normalized page signature. Repeated sender `capture_id` values are independently idempotent. If the replacement transition is not observed confidently, the image is rejected or deferred instead of queued.

## Queue and worker

Only accepted originals are written to the configured upload folder. The same database transaction stores a `capture_jobs` row with camera ID, capture time, status, and bounded marker metadata. Queue capacity is enforced with a PostgreSQL advisory transaction lock. The HTTP request performs validation and persistence only; one `services.scan_worker` consumer claims jobs in order and calls the existing V3 draft service. That pipeline aligns the sheet, recognizes the student ID and answers, creates a review draft, and leaves final correction/grading in existing review flows. Failures are recorded on the job. The automatic scanning dashboard reports ingestion counters and queue states and links to accepted originals and review drafts. Manual uploads remain available.

The eventual ESP32 sender can POST one image about once per second to this route. It does not need to perform alignment, OMR, student identification, or assessment selection. ESP32 firmware and camera connection configuration are outside this implementation.

## Limitations

The API needs the schema migration and a selected assessment. The worker is a separate process and must be running for queued images to progress. Synthetic detector and route tests cannot establish camera focus, lighting, perspective, feeder behavior, or physical exactly-once reliability. Visually identical pages may be indistinguishable from a prior page using image-only evidence; validate with the actual camera and paper handling before relying on automatic replacement detection.
