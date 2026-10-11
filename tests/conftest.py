import os

import pytest

from app import create_app, db


@pytest.fixture
def app():
    test_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not test_url:
        pytest.skip("PostgreSQL test database is not configured.")

    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()
