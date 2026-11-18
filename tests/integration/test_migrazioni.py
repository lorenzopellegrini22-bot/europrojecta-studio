"""Le migrazioni scritte a mano devono corrispondere ai modelli SQLAlchemy."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

import app.models  # noqa: F401
from app.db import Base

pytestmark = pytest.mark.integration


def test_migrazioni_allineate_ai_modelli(engine_test: Engine):
    with engine_test.connect() as conn:
        contesto = MigrationContext.configure(conn, opts={"compare_type": True})
        differenze = compare_metadata(contesto, Base.metadata)
    assert differenze == [], differenze
