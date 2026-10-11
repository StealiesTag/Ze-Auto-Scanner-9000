This folder is reserved for Flask-Migrate/Alembic migration files.

Run:

flask --app app db init
flask --app app db migrate -m "initial schema"
flask --app app db upgrade

The migration files will be created here after the database connection is configured.
