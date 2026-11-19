# Paparazzi system audit

The audit uses commit `13f9e58` as the comparison point. This prototype uses browser-triggered printer steps and accepts official 20- and 50-question sheets on the camera path. It also retains the earlier camera ingestion API and V3 geometry. Assessment creation, manual intake, camera intake, draft review, grading, analytics, and teaching suggestions share the existing Flask and PostgreSQL data model.

| Severity | Finding | Change |
| --- | --- | --- |
| Critical | Camera API requests could read images and enqueue work without credentials. Teacher routes exposed grades and scans on network deployments. | Require an API bearer token. Restrict passwordless teacher access to the host computer and support password-protected remote access. |
| Critical | Competing captures could share a filename, and rollback cleanup could remove a different request's original. | Reserve the unique capture ID before writing, write an exclusive file with an independent name, and flush it to disk before committing. |
| Critical | Automatic grading could accept a matching student number from the wrong class. Multiple review requests could try to finalize one draft. | Route class mismatches to teacher review and lock the draft during confirmation. |
| High | Concurrent printer-step requests could each act on the same persisted step. Poller interruptions could leave partial replacement evidence active. | Hold the scanner-control row lock through step validation and commit the job with the step. Require fresh replacement evidence after an interruption. |
| High | The browser opened the MJPEG connection automatically while the scanner requested still images. Existing V3 paper lacked an explicit intake path. | Open preview on demand and expose the unchanged V3 sheets and layout choices in manual upload. |
| High | Malformed provider lists could crash suggestion generation or editing. Optional image-location requests could send a complete student sheet externally. | Validate list types before iteration and require authorization for external scan-image and source-PDF sharing. |
| Medium | Registration alignment rendered a full color page for every orientation and could score an orientation whose projected corners were outside the frame. | Score only valid projections and render the winning color warp once. Log still-request, queue-wait, and processing duration. |
| Low | Status polling could overlap slow requests, and saved suggestions could disappear from view when class results changed. | Schedule the next poll after completion and explain when the assessment needs fresh suggestions. |

## Database and deployment

The checked-in Alembic head is `20261105_0013`. That revision rewrites earlier assessment version labels as V4. The deployed revision could not be read because this checkout has no configured `DATABASE_URL`. No migration was generated or applied. The integration suite uses a new, isolated PostgreSQL test database created from the models.

Set `PAPARAZZI_CAMERA_API_TOKEN` for API clients and `PAPARAZZI_TEACHER_PASSWORD` for remote teacher access. Use HTTPS with a reverse proxy and set the password whenever a proxy fronts Flask. The teacher credential is shared application access. Multi-teacher accounts and per-class authorization require further work.

Set `MUSE_ALLOW_SCAN_IMAGE_SHARING=true` only if sending full answer-sheet images to the configured provider is authorized. Set `AI_ALLOW_TEST_PDF_SHARING=true` only for approved assessment source documents. Teacher-entered prompts, choices, and objectives can appear in AI requests. Keep student information out of those fields.

## Remaining validation and operational limits

- Manual V3 upload accepts Standard V3 at 20, 50, or 100 questions and the large-bubble 50-question variant. Automatic printer-step capture still uses official 20- and 50-question sheets.
- The browser printer loop still uses fixed waits and duplicate evidence. The camera API and poller require marker loss and page change. Similar pages, camera motion, or interruptions can produce ambiguous observations. Physical exactly-once capture remains unverified.
- A crash between persisting a file and committing the database can leave an unreferenced original. A reconciliation report and retention policy would make these files easier to manage safely.
- Capture timing logs and queue counts provide diagnostics. Sustained job throughput, database load, failure rates across time, peak memory, and browser responsiveness under classroom load need a representative deployment and workload.
- Validate with the actual ESP32 firmware, A4 prints, and a multi-sheet printer feed. Include similar consecutive sheets, momentary marker occlusion, low light, reconnects during ejection, and a retry after a failed still. Capture observed missed and duplicate sheets before relying on unattended grading.

## Scanner timing

The local timing exercise ran alignment, answer recognition, and seven-digit ID recognition on one 240-DPI render per Standard V3 layout using one OpenCV thread. Illumination correction was the repeated hotspot in stack samples. The updated scanner estimates the lighting field at one quarter of the frame width and height, then applies it to the full-resolution image. Marker geometry, bubble coordinates, and classification thresholds are preserved.

| V3 layout | Original commit | Updated |
| --- | ---: | ---: |
| 20 questions | 270.60 s | 2.75 s |
| 50 questions | 32.55 s | 3.32 s |
| 100 questions | 33.97 s | 2.90 s |

These are single local samples collected while other validation jobs were running. The 20-question baseline was an outlier. These measurements cover software processing time and leave sustained throughput and field accuracy open.

## Test results

The final full suite passed all 111 tests in 158.99 seconds on Windows with Python 3.12 and an isolated PostgreSQL 18 instance. The only warning is a deprecation notice for the `fitz` import. Windows blocked some native dependency wheels, so this run used SQLAlchemy 2.0.44 and Psycopg's Python implementation with the installed PostgreSQL client library.

- Software and PostgreSQL integration tests covered API authorization, queue capacity and recovery, duplicate capture IDs preserving originals, class-mismatch review, grading and corrections, analytics, and mocked AI failures and privacy settings. Earlier runs surfaced stale test setup for the current printer confirmation, poller lock, and explicit V3 layout selection. The final run includes those corrected fixtures.
- Scanner tests used synthetic marks on PDF renders and supplied image fixtures for 20-, 50-, and 100-question V3 layouts. They cover rotated or perspective-distorted pages, low contrast, uneven light, blur, noise, ambiguous or multiple marks, and seven-digit ID recognition.
- Live ESP32, physical print, and multi-sheet feed tests remain pending. No live AI requests were made by the test suite.

## Next priorities

1. Read the deployment database's Alembic revision and review the pending migration chain against a backup before planning rollout.
2. Run a physical feed trial with the installed ESP32 firmware and printer. Record missed sheets, duplicates, and manual-review rates across representative marks and student IDs.
3. Add V3 layout selection to automatic sessions after the physical feed trial. Verify the selected profile from capture through queue processing, including 100-question sheets.
4. Measure queue latency, failures, and memory over a representative classroom batch. Use the results to guide query tuning and queue retention.

## Changed files

- Application access and configuration — `app.py`, `config.py`, `routes/camera_api_routes.py`, `scripts/test_camera_ingest.py`, and `docs/camera-image-ingestion.md`.
- Assessment intake and grading — `services/assessment_service.py`, `routes/scanning_routes.py`, `services/paparazzi_v3_scanner.py`, `services/paparazzi_v3_submission_service.py`, and the assessment, answer-sheet, and upload templates.
- Capture and queue — `routes/automatic_scanning_routes.py`, `services/camera_ingestion.py`, `services/capture_queue.py`, `services/capture_state_machine.py`, `services/scan_worker.py`, `templates/automatic_scanning.html`, and `static/js/app.js`.
- Teaching insights — `routes/analytics_routes.py`, `services/teaching_insights_service.py`, `services/muse_marker_locator.py`, and `templates/analytics.html`.
- Tests — `tests/conftest.py`, `tests/test_automatic_scanning.py`, `tests/test_paparazzi_v3.py`, `tests/test_services.py`, `tests/test_stage1_database.py`, and `tests/test_teaching_insights.py`.
