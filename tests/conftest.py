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

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        connection = db.engine.connect()
        transaction = connection.begin()
        db.session.remove()
        db.session.configure(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield app
        finally:
            db.session.remove()
            transaction.rollback()
            connection.close()


@pytest.fixture
def client(app):
    return app.test_client()
