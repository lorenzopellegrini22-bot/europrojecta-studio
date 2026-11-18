import pytest
from pydantic import ValidationError

from app.config import Settings


def test_secret_key_troppo_corta_rifiutata():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://a:b@h/db", secret_key="corta", _env_file=None)


def test_secret_key_non_compare_nella_repr():
    s = Settings(
        database_url="postgresql+psycopg://a:b@h/db",
        secret_key="x" * 40,
        _env_file=None,
    )
    assert "x" * 40 not in repr(s)
    assert s.secret_key.get_secret_value() == "x" * 40


@pytest.mark.parametrize(
    "url",
    [
        "postgres://a:b@h.neon.tech/db?sslmode=require",
        "postgresql://a:b@h.neon.tech/db?sslmode=require",
        "postgresql+psycopg://a:b@h.neon.tech/db?sslmode=require",
    ],
)
def test_url_dei_servizi_gestiti_usa_psycopg(url):
    s = Settings(database_url=url, secret_key="x" * 40, _env_file=None)
    assert s.database_url == "postgresql+psycopg://a:b@h.neon.tech/db?sslmode=require"


def test_ambiente_non_valido_rifiutato():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://a:b@h/db",
            secret_key="x" * 40,
            ambiente="boh",
            _env_file=None,
        )
