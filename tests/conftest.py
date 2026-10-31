import os

import pytest
from sqlalchemy.engine import make_url

from app import create_app, db


@pytest.fixture
def app():
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("PostgreSQL test database is not configured.")
    parsed_url = make_url(test_url)
    if parsed_url.drivername != "postgresql+psycopg" or not parsed_url.database.endswith(("_test", "_testing")):
        pytest.fail("TEST_DATABASE_URL must name a dedicated database ending in _test or _testing.")
    development_url = os.getenv("DATABASE_URL")
    if development_url:
        development = make_url(development_url)
        test_target = (parsed_url.host, parsed_url.port or 5432, parsed_url.database)
        development_target = (development.host, development.port or 5432, development.database)
        if test_target == development_target:
            pytest.fail("TEST_DATABASE_URL must not point to the configured development database.")

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        engine = db.engine
        connection = engine.connect()
        transaction = connection.begin()
        db.session.remove()
        # Flask-SQLAlchemy's Session.get_bind() resolves through db.engines,
        # so configuring the scoped session's default bind alone is ignored.
        db.engines[None] = connection
        db.session.configure(join_transaction_mode="create_savepoint")
        try:
            yield app
        finally:
            db.session.remove()
            transaction.rollback()
            connection.close()
            db.engines[None] = engine
            db.session.configure(join_transaction_mode="conditional_savepoint")


@pytest.fixture
def client(app):
    return app.test_client()
