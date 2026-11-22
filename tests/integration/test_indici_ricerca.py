"""La ricerca di bandi e appalti usa gli indici a trigrammi (migrazione 0025) dove c'è pg_trgm.

L'indice serve solo se l'espressione della query è identica a quella indicizzata: se un
giorno _colonna cambiasse (o tornasse a passare le costanti come parametri), questo test
lo segnala. Senza pg_trgm (PostgreSQL di sviluppo senza contrib) il test si salta.
"""

import pytest
from sqlalchemy import Engine, select, text

from app.models import Bando
from app.models.appalti import Appalto
from app.utils.ricerca import condizione

pytestmark = pytest.mark.integration


def _piano(conn, query) -> str:  # noqa: ANN001
    sql = query.compile(conn, compile_kwargs={"render_postcompile": True})
    righe = conn.exec_driver_sql(f"EXPLAIN {sql}", sql.params).scalars().all()
    return "\n".join(righe)


def test_ricerca_usa_gli_indici_a_trigrammi(engine_test: Engine):
    with engine_test.connect() as conn:
        if not conn.scalar(text("select count(*) from pg_extension where extname = 'pg_trgm'")):
            pytest.skip("pg_trgm non disponibile in questo PostgreSQL")
        conn.execute(text("SET enable_seqscan = off"))
        cerca_bandi = condizione("ristrutturazione scuola", Bando.titolo, Bando.ente)
        bandi = _piano(conn, select(Bando.id).where(cerca_bandi))
        cerca_gare = condizione("asfalto", Appalto.titolo, Appalto.stazione_appaltante)
        appalti = _piano(conn, select(Appalto.id).where(cerca_gare))
    assert "ix_bando_titolo_trgm" in bandi and "ix_bando_ente_trgm" in bandi, bandi
    assert "ix_appalto_titolo_trgm" in appalti, appalti
