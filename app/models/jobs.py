"""Registro delle esecuzioni dei job del worker."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ESITI_JOB = ("in_corso", "ok", "errore", "saltato")


class JobRun(Base):
    __tablename__ = "job_run"
    __table_args__ = (CheckConstraint(f"esito IN {ESITI_JOB}", name="ck_job_run_esito"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(60), index=True)
    inizio: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fine: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    esito: Mapped[str] = mapped_column(String(20), default="in_corso", server_default="in_corso")
    messaggio: Mapped[str | None] = mapped_column(Text)
