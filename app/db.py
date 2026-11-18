"""Engine SQLAlchemy, sessioni e base dei modelli."""

from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache

from sqlalchemy import BigInteger, DateTime, Engine, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Base di tutti i modelli."""


class ConTimestamp:
    """Mixin per le tabelle principali: id bigint identity, creato_il, aggiornato_il."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    creato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dipendenza FastAPI: una sessione per richiesta, chiusa sempre alla fine."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
