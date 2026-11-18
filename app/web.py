"""Template Jinja2 e filtri di formattazione."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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


templates.env.filters["data_ora"] = _data_ora
templates.env.filters["euro"] = _euro
templates.env.filters["data"] = _data
templates.env.globals["NOME_APP"] = NOME_APP


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
