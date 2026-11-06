# Automated Test Paper Scanning, Grading, and Learning Analytics System

This project is a modular Flask application for automated answer-sheet scanning, grading, analytics, and learning-resource recommendations. It is intentionally structured so each concern lives in a separate layer: routes, database models, services, templates, and static assets.

## Core goals

- Manage students and assessments in PostgreSQL.
- Use the fixed official 20Q_Big and 50-question large-bubble sheets.
- Upload, align, and validate scanned answer sheets.
- Decode bubbles using OpenCV and NumPy.
- Grade submissions and flag ambiguous cases for teacher review.
- Produce class-level analytics and learning-objective recommendations.
- Keep the architecture extensible for future integrations without pretending they exist.

## Project structure

- `app.py`: Flask application factory.
- `config.py`: configuration and environment loading.
- `models/`: SQLAlchemy models for students, assessments, submissions, responses, and learning resources.
- `routes/`: Blueprints for dashboard, students, assessments, uploads, and analytics.
- `services/`: image processing, OMR, grading, recommendation, and scanning support.
- `templates/`: HTML pages for the teacher dashboard and management sections.
- `static/`: CSS and JavaScript assets.
- `uploads/`: server-side image storage.
- `tests/`: pytest tests for validation and database constraints.

## MVP status

The local MVP supports student records and CSV import, assessment and answer-key editing, the fixed 20Q_Big and 50-question large-bubble answer sheets, staged OMR review, PostgreSQL submission persistence, class analytics, optional AI lesson suggestions/PDFs, Canvas grade preview and explicit sync, and an ESP32 capture queue. Each sheet has a matching profile for registration-square geometry, student-ID bubbles, and answer bubbles. Scanner/camera behavior is validated against PDF-rendered and synthetic frames; physical printer, camera, and ejector validation remain outstanding.

The Flask factory registers models before Flask-Migrate inspects metadata. PostgreSQL via Psycopg 3 is required; SQLite is rejected. Revisions `20261101_0003` through `20261105_0012` add AI suggestions, the durable scan queue, saved camera setup and session progress, original test PDF metadata, Canvas mappings and grade history, and question-to-PDF mapping.

This is a local, single-teacher MVP without application login or multi-user authorization. Canvas routes are restricted to loopback requests and require a server-stored encrypted teacher token, but this is not a substitute for institution OAuth or production account isolation. Handwriting recognition and written-answer grading are not included. ESP32/printer integration still needs physical validation.

## Local setup

### 1) Create and activate a virtual environment (Python 3.11+)

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3) Create PostgreSQL databases

On PostgreSQL, create a local database and user:

```sql
CREATE USER grading_user WITH PASSWORD 'choose-a-local-password';
CREATE DATABASE automated_grading OWNER grading_user;
CREATE DATABASE automated_grading_test OWNER grading_user;
```

Copy `.env.example` to `.env`, then set the credentials locally:

```env
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/automated_grading
TEST_DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/automated_grading_test
SECRET_KEY=replace-with-a-random-secret
UPLOAD_FOLDER=uploads
MAX_UPLOAD_SIZE=16777216
MAX_IMAGE_DIMENSION=8000
OMR_FILL_THRESHOLD=0.18
OMR_AMBIGUITY_MARGIN=0.10
RECOMMENDATION_ERROR_THRESHOLD=0.40
AI_BASE_URL=https://api.meta.ai/v1
AI_MODEL=muse-spark-1.3
AI_REASONING_EFFORT=low
MODEL_API_KEY=your-meta-model-api-key
AI_TIMEOUT_SECONDS=180
ESP32_STREAM_URL=http://192.168.1.80:81/stream
ESP32_CAPTURE_URL=http://192.168.1.80/capture
ESP32_REQUEST_TIMEOUT_SECONDS=8
CAMERA_POLL_INTERVAL_SECONDS=3.5
CAMERA_STABLE_FRAME_COUNT=5
CAMERA_BOTTOM_MISSING_FRAME_COUNT=5
CAMERA_MARKER_MOVEMENT_TOLERANCE_PX=3.0
CAMERA_MIN_PAGE_CHANGE_RATIO=0.0015
CAMERA_MIN_PAGE_AREA_RATIO=0.25
CAMERA_AUTO_FINALIZE_MIN_CONFIDENCE=0.80
MUSE_API_KEY=
MUSE_BASE_URL=https://api.meta.ai/v1
MUSE_MODEL=muse-spark-1.3
MUSE_TIMEOUT_SECONDS=12
MUSE_MIN_INTERVAL_SECONDS=2
CAPTURE_QUEUE_CAPACITY=25
FLASK_ENV=development
HOST=127.0.0.1
PORT=5000
```

Keep `.env` out of Git; the repository already ignores it.

AI Teaching Insights use Meta Model API by default and are generated only on demand from aggregated finalized assessment results. Student names, student numbers, and submission IDs are excluded from provider requests. Add a Meta Model API key as `MODEL_API_KEY` in `.env`; this is separate from `MUSE_API_KEY` for image location. Without the key, generation remains unavailable while deterministic analytics continue to work. The app uses Meta's OpenAI-compatible chat-completions API and requests `store: false`. Generated materials are suggestions for teacher review and are cached by the analytics data version. The feature requires the `20261101_0003` migration before use; apply it only after taking a database backup and reviewing the migration.

See [AI Teaching Insights setup and migration notes](docs/ai-teaching-insights.md) for the complete data flow and deployment steps.

Muse Spark can optionally help locate sheet-specific registration boxes when the local detector cannot find confident geometry. Configure `MUSE_API_KEY` to enable the fallback; it sends a grayscale copy of a frame or uploaded scan to Meta only when local marker detection fails. The prompt receives the selected profile's mark size and expected corner centers; for 20Q_Big it explicitly describes the enlarged 9 mm squares and warns about the large outlined ID grid. The locator returns bounding boxes only. The app validates their size and four-corner geometry locally before alignment and uses the local OMR scanner to read answers. Repeated nearly identical live frames reuse the previous location, and calls are rate-limited by `MUSE_MIN_INTERVAL_SECONDS`. Leave the key empty to keep all image processing local.

ESP32 still-image scanning requires the Alembic migrations through `20261105_0012`. The Flask app starts its persistent capture-queue consumer automatically; set `AUTO_START_SCAN_WORKER=false` only when managing `python -m services.scan_worker` separately. CameraWebServer JPEG polling still uses `python -m services.camera_poller`; see [CameraWebServer polling](docs/camera-webserver-poller.md). Save the camera ID, MJPEG preview URL, still-capture URL, and timeout once on **Automatic scanning**. For the first scan step, the browser initiates one blank A4 print and waits 16 seconds for printer calibration; later steps wait 12 seconds before requesting a high-resolution ESP32 still. The server queues each image before the browser requests another blank page, including geometry failures that need Muse alignment. The second consecutive capture with the same page signature ends scanning and is not graded twice. Configure silent printing on the scanning computer before starting: Chrome uses `--kiosk-printing`; Edge uses its managed `SilentPrintingEnabled` policy. See the one-time setup instructions on the automatic scanning page. The browser cannot detect or change its own print configuration. Session progress remains active until accepted queue items drain; review and failure totals are shown separately. Physical printer behavior and capture timing need validation. New assessments require the original text-based test PDF; numbered questions are mapped to page/excerpt candidates for teacher review. AI suggestions use the reviewed question map and class-level results. Lesson suggestions can be downloaded as PDFs.

The scan worker automatically grades and links only captures with a confidently read registered student ID, confidently read answers, and no alignment warnings. Other scans remain drafts for teacher review. The fixed answer sheet is selected by assessment question count: 20Q_Big for 20 questions and the large-bubble sheet for 50 questions. Choice E is optional unless it is the correct answer.

Canvas sync is available from **Canvas** in the navigation. The local teacher connects a Canvas personal access token, maps each assessment to a course and published points assignment, checks the roster matches, previews grades, resolves existing-grade overwrites, then explicitly selects records to sync. The token is encrypted on the local server. Updates are verified by reading scores back from Canvas and are recorded in sync history. AI prompts contain class-level aggregates and question context, not student names, student numbers, or individual submissions. See [Canvas grade sync setup](docs/canvas-grade-sync.md) for scope and limitations.

### 4) Apply the included migration

```powershell
python -m flask --app app db history
python -m flask --app app db upgrade
```

`migrations/` already exists and contains the initial revision. Do not run `db init` over it. Do not use `db stamp` to hide migration drift. For an existing database, take a backup and inspect its Alembic version and schema before upgrading; this project does not automatically reconcile manually-created tables.

### 5) Start the app

```powershell
python -m flask --app app run --host 127.0.0.1 --port 5000
```

or:

```bash
python app.py
```

The app binds to `127.0.0.1` and runs with the debugger disabled by default. It has no authentication, so keep it local and do not expose it to a network.

## Test workflow

Run the tests with a dedicated, empty PostgreSQL test database. The test fixture requires a `postgresql+psycopg` URL whose database name ends in `_test` or `_testing`; it never falls back to `DATABASE_URL`.

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://username:password@localhost:5432/automated_grading_test'
python -m pytest -q
```

Database integration tests are skipped when `TEST_DATABASE_URL` is not configured. The fixture uses a transaction rollback and does not drop tables or delete existing records. Run migrations against the dedicated test database before testing when it already exists.

## Synthetic verification

Automated tests render V3 and generated V4 PDFs and verify page boxes, registration marks, profile-derived bubble centers, student IDs, and synthetic answer extraction. Canvas API and grading logic use mocked HTTPS calls; no real Canvas account or grade has been tested. The legacy ArUco/QR pipeline remains separately tested. Physical printed sheets and camera captures still need validation.

To verify migration output without a PostgreSQL server, set `DATABASE_URL` and run `python -m flask --app app db upgrade --sql`. This prints SQL only and does not modify a database.

There is no bundled sample-data/reset script. For a clean test run, create a new empty database with an `_test` suffix rather than dropping or resetting a database that may contain data.

## Security and privacy notes

Student records and submitted answer sheets are sensitive educational data. Handle them according to your school’s privacy policy, retention rules, and classroom-data requirements. The application intentionally keeps the development server bound to localhost by default and keeps credentials in environment variables rather than source control.

## Important implementation notes

- The code avoids string concatenation when interacting with SQL; all database operations use the ORM layer.
- Uploaded filenames are not trusted and should be safely renamed server-side before storage.
- The ArUco alignment, QR identity, fixed A4 transform, and four-choice OMR belong only to legacy scanner internals. Teachers use the fixed profile-driven 20Q_Big or 50-question large-bubble sheet with square registration marks and A-E bubbles.
- Blank responses earn zero and count as unanswered. Multiple marks and ambiguous readings earn no points and keep the submission in review. A teacher can correct the answer or explicitly finalize a review while leaving uncertain responses ungraded.
- The default bubble threshold is a 0.18 dark-pixel ratio with a 0.10 runner-up margin; tune using actual printer and camera samples before relying on grades.
- Learning-resource recommendations are rule-based and transparent; they highlight possible learning gaps rather than proving that a student lacks a skill.
- The project does not implement AI-based grading or handwriting recognition. Those are intentionally excluded from the first version to keep the architecture realistic and maintainable.

## Why the architecture is split this way

- `models/` keeps schema definitions understandable and reviewable.
- `services/` isolates logic for grading, image analysis, and recommendation.
- `routes/` handles HTTP responsibilities and keeps templates thin.
- `templates/` and `static/` separate presentation from behavior.
- `tests/` keeps synthetic processing tests independent of PostgreSQL and marks database integration tests as skipped when no dedicated PostgreSQL test database is available.

This makes it easier to explain the application to beginners and to extend it later with new features without creating a single unmaintainable file.
