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

## Stage status

### Stage 1: completed foundation

The application now includes:

- Flask application factory
- PostgreSQL-ready SQLAlchemy configuration using `DATABASE_URL`
- SQLAlchemy models for Student, Assessment, Question, Submission, Response, and LearningResource
- Unique constraints and indexes for duplicate prevention
- A migration-ready project structure using Flask-Migrate/Alembic conventions
- Basic project tests that are automatically skipped if the PostgreSQL test database is not configured

### Future stages

The remaining project stages are scaffolded at the module level and are intentionally built as small, testable services so they can be expanded without ending up in one giant file.

## Local setup

### 1) Create and activate a virtual environment

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

### 3) Create PostgreSQL database

On PostgreSQL, create a local database and user:

```sql
CREATE DATABASE automated_grading;
CREATE DATABASE automated_grading_test;
```

Then add the connection string to a `.env` file:

```env
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/automated_grading
TEST_DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/automated_grading_test
SECRET_KEY=replace-with-a-random-secret
UPLOAD_FOLDER=uploads
MAX_UPLOAD_SIZE=16777216
FLASK_ENV=development
HOST=127.0.0.1
PORT=5000
```

Keep `.env` out of Git; the repository already ignores it.

### 4) Initialize migrations

```bash
flask --app app db init
flask --app app db migrate -m "initial schema"
flask --app app db upgrade
```

### 5) Start the app

```bash
flask --app app run --host 127.0.0.1 --port 5000
```

or:

```bash
python app.py
```

## Test workflow

Run the project tests with a configured PostgreSQL test database:

```bash
$env:TEST_DATABASE_URL='postgresql+psycopg://username:password@localhost:5432/automated_grading_test'
python -m pytest -q
```

The database tests are intentionally skipped when no test database is configured so the project remains safe to run in a development environment without a local PostgreSQL server.

## Safe reset and sample data

To reset the development database safely:

```sql
DROP DATABASE IF EXISTS automated_grading;
CREATE DATABASE automated_grading;
```

Then re-run the migration upgrade step above.

## Security and privacy notes

Student records and submitted answer sheets are sensitive educational data. Handle them according to your school’s privacy policy, retention rules, and classroom-data requirements. The application intentionally keeps the development server bound to localhost by default and keeps credentials in environment variables rather than source control.

## Important implementation notes

- The code avoids string concatenation when interacting with SQL; all database operations use the ORM layer.
- Uploaded filenames are not trusted and should be safely renamed server-side before storage.
- Learning-resource recommendations are rule-based and transparent; they highlight possible learning gaps rather than proving that a student lacks a skill.
- The project does not implement AI-based grading or handwriting recognition. Those are intentionally excluded from the first version to keep the architecture realistic and maintainable.

## Why the architecture is split this way

- `models/` keeps schema definitions understandable and reviewable.
- `services/` isolates logic for grading, image analysis, generation, and recommendation.
- `routes/` handles HTTP responsibilities and keeps templates thin.
- `templates/` and `static/` separate presentation from behavior.
- `tests/` keeps validation and data integrity checks independent of the web layer.

This makes it easier to explain the application to beginners and to extend it later with new features without creating a single unmaintainable file.
