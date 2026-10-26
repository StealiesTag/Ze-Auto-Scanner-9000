This directory contains the Flask-Migrate/Alembic environment and initial PostgreSQL revision.

Run:

python -m flask --app app db history
python -m flask --app app db upgrade

Do not run `db init` again. Back up and inspect any existing database before applying the initial revision; never use `db stamp` to conceal schema drift.
