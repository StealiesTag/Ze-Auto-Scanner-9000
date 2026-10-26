# Automated Test Paper Scanning, Grading, and Learning Analytics System

This project is a modular Flask application for automated answer-sheet scanning, grading, analytics, and learning-resource recommendations. It is intentionally structured so each concern lives in a separate layer: routes, database models, services, templates, and static assets.

## Core goals

- Manage students and assessments in PostgreSQL.
- Generate printable answer sheets with QR codes and fiducial markers.
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
- `services/`: image processing, OMR, grading, recommendation, and sheet-generation helpers.
- `templates/`: HTML pages for the teacher dashboard and management sections.
- `static/`: CSS and JavaScript assets.
- `uploads/`: server-side image storage.
- `tests/`: pytest tests for validation and database constraints.

## MVP status

The local MVP supports student records and CSV import, assessment and answer-key editing, A4 sheet generation, QR and ArUco identification, perspective alignment, bubble recognition, weighted grading, PostgreSQL submission persistence, teacher correction history, and class analytics with configured-resource recommendations.

The Flask factory registers models before Flask-Migrate inspects metadata. PostgreSQL via Psycopg 3 is required; SQLite is rejected. The included initial Alembic revision is `20261026_0001`. No migration history existed in the scaffold before it was added, so inspect and back up any pre-existing database before applying this revision.

This is a local, single-teacher MVP. It does not include authentication, production deployment safeguards, ESP32/Canvas integration, handwriting recognition, or written-answer grading.

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
FLASK_ENV=development
HOST=127.0.0.1
PORT=5000
```

Keep `.env` out of Git; the repository already ignores it.

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

The automated suite creates synthetic ArUco pages, QR codes, and marked bubbles in memory. These verify QR decoding, marker alignment under rotation/perspective, OMR states, brightness tolerance, PDF output, and grading. A real printer and camera were not available for physical-sheet verification.

To verify migration output without a PostgreSQL server, set `DATABASE_URL` and run `python -m flask --app app db upgrade --sql`. This prints SQL only and does not modify a database.

There is no bundled sample-data/reset script. For a clean test run, create a new empty database with an `_test` suffix rather than dropping or resetting a database that may contain data.

## Security and privacy notes

Student records and submitted answer sheets are sensitive educational data. Handle them according to your school’s privacy policy, retention rules, and classroom-data requirements. The application intentionally keeps the development server bound to localhost by default and keeps credentials in environment variables rather than source control.

## Important implementation notes

- The code avoids string concatenation when interacting with SQL; all database operations use the ORM layer.
- Uploaded filenames are not trusted and should be safely renamed server-side before storage.
- Generated sheets use an A4 coordinate system in millimeters measured from the page's top-left. The canonical processing image is 1240 by 1754 pixels. ArUco IDs 0-3 are 15 mm squares at the four corners; bubble centers are at X=82, 108, 134, and 160 mm, with question rows starting at Y=97 mm at 5.2 mm spacing. The same constants are defined in `services/sheet_layout.py` and used by both PDF generation and OMR.
- Blank responses earn zero and count as unanswered. Multiple marks and ambiguous readings earn no points and keep the submission in review. A teacher can correct the answer or explicitly finalize a review while leaving uncertain responses ungraded.
- The default bubble threshold is a 0.18 dark-pixel ratio with a 0.10 runner-up margin; tune using actual printer and camera samples before relying on grades.
- Learning-resource recommendations are rule-based and transparent; they highlight possible learning gaps rather than proving that a student lacks a skill.
- The project does not implement AI-based grading or handwriting recognition. Those are intentionally excluded from the first version to keep the architecture realistic and maintainable.

## Why the architecture is split this way

- `models/` keeps schema definitions understandable and reviewable.
- `services/` isolates logic for grading, image analysis, generation, and recommendation.
- `routes/` handles HTTP responsibilities and keeps templates thin.
- `templates/` and `static/` separate presentation from behavior.
- `tests/` keeps synthetic processing tests independent of PostgreSQL and marks database integration tests as skipped when no dedicated PostgreSQL test database is available.

This makes it easier to explain the application to beginners and to extend it later with new features without creating a single unmaintainable file.
