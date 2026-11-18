"""Canali di invio degli aggiornamenti: interfaccia comune `invia(destinatario, contenuto)`.

- Email: SMTP dell'integrazione «smtp» (registrato in notifica_email).
- WhatsApp: WhatsApp Cloud API di Meta, solo messaggi da modello approvato (i messaggi avviati
  dall'azienda fuori dalla finestra di 24 ore devono usare un modello). Formato verificato sulla
  documentazione Meta il 18/11/2026:
    POST https://graph.facebook.com/{versione}/{phone_number_id}/messages
    Authorization: Bearer <token>
    {"messaging_product": "whatsapp", "to": "<numero>", "type": "template",
     "template": {"name": ..., "language": {"code": "it"},
                  "components": [{"type": "body", "parameters": [{"type": "text", ...}]}]}}
  risposta: {"messages": [{"id": "wamid..."}]}.
"""

import re
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.integrazioni import servizio as integrazioni

LIMITE_PARAMETRO_WHATSAPP = 1000  # i parametri del corpo hanno un limite di lunghezza


@dataclass
class Esito:
    ok: bool
    id_messaggio: str | None = None
    errore: str | None = None


class Canale(Protocol):
    nome: str

    def disponibile(self) -> bool: ...

    def invia(self, destinatario: str, oggetto: str, testo: str, nome_referente: str) -> Esito: ...


class CanaleEmail:
    nome = "email"

    def __init__(self, db: Session, utente_id: int | None = None) -> None:
        self.db = db
        self.utente_id = utente_id

    def disponibile(self) -> bool:
        return integrazioni.utilizzabile(self.db, "smtp")

    def invia(self, destinatario: str, oggetto: str, testo: str, nome_referente: str) -> Esito:
        from app.comunicazioni import invia_registrando
        from app.models import Utente

        utente = self.db.get(Utente, self.utente_id) if self.utente_id else None
        ok, messaggio = invia_registrando(
            self.db,
            tipo="aggiornamento_cliente",
            destinatario=destinatario,
            oggetto=oggetto,
            testo=testo,
            utente=utente,
            rispondi_a=utente.email if utente else None,
        )
        return Esito(ok=ok, errore=None if ok else messaggio)


def numero_whatsapp(telefono: str | None) -> str | None:
    """Numero in formato internazionale senza «+» (i cellulari italiani senza prefisso
    ricevono il 39)."""
    if not telefono:
        return None
    cifre = re.sub(r"\D", "", telefono)
    if telefono.strip().startswith("00"):
        cifre = cifre[2:]
    elif not telefono.strip().startswith("+") and cifre.startswith("3") and len(cifre) == 10:
        cifre = "39" + cifre
    return cifre if 8 <= len(cifre) <= 15 else None


class CanaleWhatsApp:
    nome = "whatsapp"

    def __init__(self, db: Session, client: httpx.Client | None = None) -> None:
        self.db = db
        self.client = client

    def disponibile(self) -> bool:
        return integrazioni.utilizzabile(self.db, "whatsapp")

    def invia(self, destinatario: str, oggetto: str, testo: str, nome_referente: str) -> Esito:
        numero = numero_whatsapp(destinatario)
        if numero is None:
            return Esito(ok=False, errore="Numero di telefono non valido per WhatsApp.")
        v = integrazioni.leggi_valori(self.db, "whatsapp")
        url = (
            f"https://graph.facebook.com/{v.get('versione_api') or 'v27.0'}/"
            f"{v['phone_number_id']}/messages"
        )
        corpo = " ".join(testo.split())
        if len(corpo) > LIMITE_PARAMETRO_WHATSAPP:
            corpo = corpo[: LIMITE_PARAMETRO_WHATSAPP - 1] + "…"
        dati = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "template",
            "template": {
                "name": v["nome_modello"],
                "language": {"code": v.get("lingua_modello") or "it"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": nome_referente or "cliente"},
                            {"type": "text", "text": corpo},
                        ],
                    }
                ],
            },
        }
        client = self.client or httpx.Client(timeout=30)
        try:
            r = client.post(
                url, json=dati, headers={"Authorization": f"Bearer {v['access_token']}"}
            )
        except httpx.HTTPError as e:
            return Esito(ok=False, errore=f"WhatsApp non raggiungibile: {type(e).__name__}")
        finally:
            if self.client is None:
                client.close()
        try:
            risposta = r.json()
        except ValueError:
            risposta = {}
        if r.status_code >= 400 or "error" in risposta:
            errore = (risposta.get("error") or {}).get("message") or f"HTTP {r.status_code}"
            return Esito(ok=False, errore=f"WhatsApp: {errore}"[:500])
        messaggi = risposta.get("messages") or [{}]
        return Esito(ok=True, id_messaggio=messaggi[0].get("id"))
