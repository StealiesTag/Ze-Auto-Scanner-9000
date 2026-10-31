# Automated Test Paper Scanning, Grading, and Learning Analytics System

This project is a modular Flask application for automated answer-sheet scanning, grading, analytics, and learning-resource recommendations. It is intentionally structured so each concern lives in a separate layer: routes, database models, services, templates, and static assets.

## Core goals

- Manage students and assessments in PostgreSQL.
- Serve the supplied Paparazzi Standard V3 PDF templates and scan profiles.
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

The local MVP supports student records and CSV import, assessment and answer-key editing, Paparazzi Standard V3 sheet downloads and staged OMR review, PostgreSQL submission persistence, teacher correction history, and class analytics with configured-resource recommendations. V3 templates and their source JSON profile are stored unchanged under `static/answer_sheets/v3/`; measured vector origins are in a separate addendum. Recognition is validated on PDF-derived synthetic images, while physical print/camera validation remains outstanding. See [docs/paparazzi-standard-v3.md](docs/paparazzi-standard-v3.md) for geometry, migration, and validation status.

The Flask factory registers models before Flask-Migrate inspects metadata. PostgreSQL via Psycopg 3 is required; SQLite is rejected. Alembic revisions are `20261026_0001` and `20261101_0002`; the latter adds optional choice E and V3 scan drafts without changing existing answer data. Back up any existing database before applying migrations.

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

Automated tests render the supplied V3 PDFs and verify their page boxes, registration marks, profile-derived bubble centers, student ID grid, and synthetic answer extraction for all three templates. The legacy ArUco/QR pipeline remains separately tested. Physical printed sheets and camera captures still need validation.

To verify migration output without a PostgreSQL server, set `DATABASE_URL` and run `python -m flask --app app db upgrade --sql`. This prints SQL only and does not modify a database.

There is no bundled sample-data/reset script. For a clean test run, create a new empty database with an `_test` suffix rather than dropping or resetting a database that may contain data.

## Security and privacy notes

Student records and submitted answer sheets are sensitive educational data. Handle them according to your school’s privacy policy, retention rules, and classroom-data requirements. The application intentionally keeps the development server bound to localhost by default and keeps credentials in environment variables rather than source control.

## Important implementation notes

- The code avoids string concatenation when interacting with SQL; all database operations use the ORM layer.
- Uploaded filenames are not trusted and should be safely renamed server-side before storage.
- The ArUco alignment, QR identity, fixed A4 transform, and four-choice OMR belong only to the legacy sheet workflow. Paparazzi Standard V3 uses a separate profile-driven square-mark detector and A–E grid.
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
