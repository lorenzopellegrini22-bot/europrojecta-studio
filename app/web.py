"""Template Jinja2 e filtri di formattazione."""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app import NOME_APP
from app.config import get_settings

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _data_ora(valore: datetime | None) -> str:
    if valore is None:
        return "—"
    return valore.astimezone(ZoneInfo(get_settings().fuso_orario)).strftime("%d/%m/%Y %H:%M")


def _data(valore: date | None) -> str:
    return valore.strftime("%d/%m/%Y") if valore else "—"


def _euro(valore: Decimal | float | int | None) -> str:
    if valore is None:
        return "—"
    testo = f"{valore:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {testo}"


_CARTELLA_STATICI = Path(__file__).parent / "static"


def _versione_statico(percorso: str) -> str:
    """Data di modifica del file statico, da aggiungere all'indirizzo (?v=...): dopo un
    aggiornamento il browser scarica subito il CSS e il JS nuovi invece di usare quelli vecchi."""
    try:
        return str(int((_CARTELLA_STATICI / percorso).stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["versione_statico"] = _versione_statico
templates.env.filters["data_ora"] = _data_ora
templates.env.filters["euro"] = _euro
templates.env.filters["data"] = _data
templates.env.globals["NOME_APP"] = NOME_APP


def _puo_vedere(utente: Any, percorso: str) -> bool:
    from app.auth.permessi import puo_vedere

    return utente is not None and puo_vedere(utente, percorso)


templates.env.globals["puo_vedere"] = _puo_vedere


def _puo_modificare(utente: Any, percorso: str, metodo: str = "POST") -> bool:
    from app.auth.permessi import puo_modificare, puo_vedere

    return (
        utente is not None
        and puo_vedere(utente, percorso)
        and puo_modificare(utente, percorso, metodo)
    )


def _menzioni(testo: str) -> Markup:
    """Testo di una nota con le @menzioni evidenziate (il resto è testo semplice, escapato)."""
    import re

    sicuro = str(escape(testo or ""))
    return Markup(
        re.sub(r"(?<![\w.])(@[\w][\w.\-]*)", r'<strong class="menzione">\1</strong>', sicuro)
    )


def _da_leggere(utente: Any) -> int:
    """Menzioni e novità dei bandi seguiti non ancora lette (contatore accanto a «Oggi»).
    Usa la sessione del database già aperta per la richiesta."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import object_session

    from app.models import Commento, Menzione, NovitaSeguito, Seguito
    from app.squadra import note, seguiti

    db = object_session(utente) if utente is not None else None
    if db is None:
        return 0
    try:
        menzioni = db.scalar(
            select(func.count())
            .select_from(Menzione)
            .join(Commento)
            .where(
                Menzione.utente_id == utente.id,
                Menzione.letta_il.is_(None),
                Commento.oggetto.in_(note.visibili(utente)),
            )
        )
        novita = db.scalar(
            select(func.count())
            .select_from(NovitaSeguito)
            .join(Seguito)
            .where(
                Seguito.utente_id == utente.id,
                NovitaSeguito.letta_il.is_(None),
                seguiti.visibili(utente),
            )
        )
    except Exception:  # il contatore non deve mai rompere una pagina
        return 0
    return (menzioni or 0) + (novita or 0)


templates.env.globals["puo_modificare"] = _puo_modificare
templates.env.globals["da_leggere"] = _da_leggere
templates.env.filters["menzioni"] = _menzioni


_DIMENSIONE_FILE = re.compile(r"\s*\(\s*pdf\s*[\d.,]+\s*[kmg]b\s*\)\s*$", re.I)


def _senza_dimensione(titolo: str | None) -> str:
    """«Allegato 4 ( pdf 555.84 KB)» → «Allegato 4»: la dimensione scritta dai siti degli enti."""
    return _DIMENSIONE_FILE.sub("", titolo or "")


templates.env.filters["senza_dimensione"] = _senza_dimensione


def render(
    request: Request, nome: str, contesto: dict[str, Any] | None = None, status_code: int = 200
) -> HTMLResponse:
    """Render con il contesto comune: utente corrente e token CSRF della sessione."""
    sessione = getattr(request.state, "sessione", None)
    ctx: dict[str, Any] = {
        "utente": sessione.utente if sessione and sessione.mfa_verificata else None,
        "csrf_token": sessione.csrf_token if sessione else "",
        "inattivita_minuti": get_settings().sessione_inattivita_minuti,
    }
    ctx.update(contesto or {})
    return templates.TemplateResponse(request, nome, ctx, status_code=status_code)


async def leggi_form(request: Request) -> dict[str, Any]:
    """Il form della richiesta come dizionario campo → valore (le liste per i campi ripetuti).

    Si usa come dipendenza nelle route sincrone: Starlette mette in cache il form, quindi
    anche verifica_csrf lo legge senza rileggere il body."""
    form = await request.form()
    dati: dict[str, Any] = {}
    for chiave in form:
        valori = [v for v in form.getlist(chiave) if isinstance(v, str)]
        dati[chiave] = valori if len(valori) > 1 else (valori[0] if valori else "")
    return dati


def testo(form: dict[str, Any], campo: str) -> str | None:
    """Valore di un campo di testo: spazi tolti, None se vuoto."""
    valore = form.get(campo)
    if isinstance(valore, list):
        valore = valore[0] if valore else ""
    valore = (valore or "").strip()
    return valore or None
