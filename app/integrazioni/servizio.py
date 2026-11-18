"""Lettura e salvataggio delle integrazioni.

Precedenza: valore salvato nel database (dall'app) > variabile d'ambiente > predefinito.
I segreti sono cifrati nel database e non tornano mai nella pagina: si mostra solo
l'anteprima (ultime 4 cifre).
"""

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app import audit
from app.integrazioni.registro import INTEGRAZIONI, TipoIntegrazione
from app.models import Integrazione, Utente
from app.sicurezza.cifratura import ErroreCifratura, anteprima, cifra, decifra


def _scopo(codice: str, campo: str) -> str:
    return f"integrazione:{codice}:{campo}"


@dataclass
class StatoCampo:
    valore: str | None  # in chiaro per i campi normali, None per i segreti (mai mostrati)
    anteprima: str | None  # solo per i segreti
    provenienza: str | None  # "app", "ambiente", "predefinito" o None se mancante


@dataclass
class StatoIntegrazione:
    tipo: TipoIntegrazione
    campi: dict[str, StatoCampo]
    configurata: bool
    mancanti: list[str]
    verificata_il: datetime | None = None
    verifica_ok: bool | None = None
    verifica_messaggio: str | None = None
    errori: list[str] = field(default_factory=list)


def _riga(db: Session, codice: str) -> Integrazione | None:
    return db.get(Integrazione, codice)


def leggi_valori(db: Session, codice: str) -> dict[str, str | None]:
    """Tutti i valori in chiaro (segreti inclusi), per l'uso interno del server.
    Da non passare mai ai template."""
    tipo = INTEGRAZIONI[codice]
    riga = _riga(db, codice)
    valori: dict[str, str | None] = {}
    for campo in tipo.campi:
        valore: str | None = None
        if riga is not None:
            if campo.segreto and campo.nome in riga.segreti:
                valore = decifra(riga.segreti[campo.nome], _scopo(codice, campo.nome))
            elif not campo.segreto:
                valore = riga.config.get(campo.nome) or None
        if valore is None and campo.variabile_ambiente:
            valore = os.environ.get(campo.variabile_ambiente) or None
        if valore is None:
            valore = campo.predefinito
        valori[campo.nome] = valore
    return valori


def stato(db: Session, codice: str) -> StatoIntegrazione:
    tipo = INTEGRAZIONI[codice]
    riga = _riga(db, codice)
    campi: dict[str, StatoCampo] = {}
    mancanti: list[str] = []
    errori: list[str] = []
    for campo in tipo.campi:
        provenienza: str | None = None
        valore: str | None = None
        ant: str | None = None
        if riga is not None and campo.segreto and campo.nome in riga.segreti:
            provenienza, ant = "app", riga.anteprime.get(campo.nome, "…")
            try:
                decifra(riga.segreti[campo.nome], _scopo(codice, campo.nome))
            except ErroreCifratura:
                errori.append(
                    f"«{campo.etichetta}» non si può decifrare (la SECRET_KEY è cambiata?): "
                    "reinseriscilo."
                )
        elif riga is not None and not campo.segreto and riga.config.get(campo.nome):
            provenienza, valore = "app", str(riga.config[campo.nome])
        elif campo.variabile_ambiente and os.environ.get(campo.variabile_ambiente):
            provenienza = "ambiente"
            if campo.segreto:
                ant = anteprima(os.environ[campo.variabile_ambiente])
            else:
                valore = os.environ[campo.variabile_ambiente]
        elif campo.predefinito is not None:
            provenienza, valore = "predefinito", campo.predefinito
        if provenienza is None and campo.obbligatorio:
            mancanti.append(campo.etichetta)
        campi[campo.nome] = StatoCampo(valore=valore, anteprima=ant, provenienza=provenienza)
    return StatoIntegrazione(
        tipo=tipo,
        campi=campi,
        configurata=not mancanti and not errori,
        mancanti=mancanti,
        verificata_il=riga.verificata_il if riga else None,
        verifica_ok=riga.verifica_ok if riga else None,
        verifica_messaggio=riga.verifica_messaggio if riga else None,
        errori=errori,
    )


class ErroreValidazione(Exception):
    def __init__(self, errori: dict[str, str]):
        super().__init__("Dati non validi")
        self.errori = errori


def salva(
    db: Session,
    codice: str,
    dati: dict[str, str],
    da_rimuovere: set[str],
    utente: Utente,
) -> None:
    """Salva i campi inviati dal form.

    - Campo segreto lasciato vuoto: si mantiene quello già salvato.
    - Campo segreto in `da_rimuovere`: si cancella (torna a valere la variabile d'ambiente).
    - Campo normale vuoto: si cancella.
    L'audit registra quali campi sono cambiati, mai i valori dei segreti.
    """
    tipo = INTEGRAZIONI[codice]
    errori: dict[str, str] = {}
    riga = _riga(db, codice)
    if riga is None:
        riga = Integrazione(codice=codice, config={}, segreti={}, anteprime={})
        db.add(riga)

    config = dict(riga.config)
    segreti = dict(riga.segreti)
    anteprime = dict(riga.anteprime)
    differenze: dict[str, Any] = {}

    for campo in tipo.campi:
        valore = (dati.get(campo.nome) or "").strip()
        if campo.segreto:
            if campo.nome in da_rimuovere:
                if campo.nome in segreti:
                    segreti.pop(campo.nome)
                    anteprime.pop(campo.nome, None)
                    differenze[campo.nome] = "rimosso"
            elif valore:
                segreti[campo.nome] = cifra(valore, _scopo(codice, campo.nome))
                anteprime[campo.nome] = anteprima(valore)
                differenze[campo.nome] = "modificato"
            continue

        if campo.tipo == "number" and valore and not valore.isdigit():
            errori[campo.nome] = "Deve essere un numero intero."
        if campo.tipo == "select" and valore and valore not in campo.opzioni:
            errori[campo.nome] = "Valore non ammesso."
        if campo.tipo == "email" and valore and "@" not in valore:
            errori[campo.nome] = "Indirizzo email non valido."
        precedente = config.get(campo.nome)
        if valore:
            config[campo.nome] = valore
        else:
            config.pop(campo.nome, None)
        if (precedente or None) != (valore or None):
            differenze[campo.nome] = {"da": precedente, "a": valore or None}

    if errori:
        db.rollback()
        raise ErroreValidazione(errori)

    riga.config, riga.segreti, riga.anteprime = config, segreti, anteprime
    riga.aggiornato_da = utente.id
    if differenze:
        # La configurazione è cambiata: la verifica precedente non vale più.
        riga.verificata_il = None
        riga.verifica_ok = None
        riga.verifica_messaggio = None
        audit.registra(db, utente, "integrazione", codice, "modifica", differenze)
    db.commit()


def registra_verifica(db: Session, codice: str, ok: bool, messaggio: str) -> None:
    riga = _riga(db, codice)
    if riga is None:
        riga = Integrazione(codice=codice, config={}, segreti={}, anteprime={})
        db.add(riga)
    riga.verificata_il = datetime.now(UTC)
    riga.verifica_ok = ok
    riga.verifica_messaggio = messaggio
    db.commit()
