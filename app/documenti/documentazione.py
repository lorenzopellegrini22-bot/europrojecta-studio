"""Sezione «Documentazione» di bandi, gare e opportunità: un elenco di link con il nome del
tipo di documento (Bando, Disciplinare, Domanda di partecipazione, Allegati…), come nelle schede
gara delle piattaforme. Cliccando si scarica il file dal sito dell'ente (o dall'app, per i PDF
caricati a mano)."""

import re
from typing import Any

from app.appalti.documenti import TIPI, tipo_documento

# Per i bandi di finanziamento «bando di gara» non è il nome giusto
NOMI_BANDO = {"Bando di gara": "Bando", "Offerta economica e tecnica": "Piano e preventivi"}
ORDINE = {t: i for i, (t, _p) in enumerate(TIPI)}
_ESTENSIONE = re.compile(r"(\s*\(\d+\))?\.(pdf|p7m|zip|rar|7z|docx?|xlsx?|odt|ods|rtf|dwg)$", re.I)

_RUMORE = re.compile(r"\s*\([^()]*\b(?:pdf|kb|mb|si apre|nuova finestra)\b[^()]*\)?", re.I)

# «Bando 26CF -227 KB», «Allegato A – 1,2 MB» in fondo al titolo
_DIMENSIONE = re.compile(r"\s*[-–—]\s*\d+(?:[.,]\d+)?\s*(?:kb|mb)\s*$", re.I)


def pulisci(titolo: str, massimo: int = 70) -> str:
    """Nome leggibile dal titolo o dal nome del file: senza estensioni né trattini bassi."""
    # «(PDF — 1,1 MB - 07.05.2026)», «(si apre in una nuova finestra)» dei siti regionali
    t = _DIMENSIONE.sub("", _RUMORE.sub("", titolo)).strip(" -–—")
    while _ESTENSIONE.search(t):
        t = _ESTENSIONE.sub("", t)
    t = " ".join(t.replace("_", " ").split()) or "Documento"
    return t if len(t) <= massimo else t[: massimo - 1].rstrip() + "…"


def voci(documenti: list[dict[str, Any]], nomi: dict[str, str] | None = None) -> list[dict]:
    """[{"etichetta", "url", "titolo"}] ordinate per tipo. L'etichetta è il tipo; se ci sono più
    documenti dello stesso tipo si aggiunge il titolo per distinguerli."""
    nomi = nomi or {}
    elenco = []
    for d in documenti:
        if not d.get("url"):
            continue
        titolo = (d.get("titolo") or "").strip() or "Documento"
        tipo = d.get("tipo") or tipo_documento(titolo)
        elenco.append({"tipo": tipo, "titolo": titolo, "url": d["url"], "word": d.get("word")})
    conta: dict[str, int] = {}
    for v in elenco:
        conta[v["tipo"]] = conta.get(v["tipo"], 0) + 1
    for v in elenco:
        nome = nomi.get(v["tipo"], v["tipo"])
        if v["tipo"] == "Pagina della gara":
            v["etichetta"] = "Pagina della gara sulla piattaforma"
        elif v["tipo"] == "Altri allegati" or conta[v["tipo"]] > 1:
            # più documenti dello stesso tipo: si distinguono con il loro nome
            v["etichetta"] = pulisci(v["titolo"])
        else:
            v["etichetta"] = nome
    elenco.sort(key=lambda v: ORDINE.get(v["tipo"], len(ORDINE)))
    return elenco


def voci_bando(bando: Any, documenti: list[Any]) -> list[dict]:
    """Documenti PDF del bando (link all'ente, o all'app per quelli caricati a mano) e, in
    fondo, la pagina ufficiale e la piattaforma per le domande."""
    elenco: list[dict[str, Any]] = []
    for d in documenti:
        if d.url.startswith("caricato-a-mano:"):
            if not d.percorso:
                continue
            url = f"/bandi/{d.bando_id}/documenti/{d.id}"
        else:
            url = d.url
        voce = {"titolo": d.titolo or "Documento", "url": url}
        if d.percorso or d.testo_estratto:  # salvato o letto dall'app: si può avere in Word
            voce["word"] = f"/bandi/{d.bando_id}/documenti/{d.id}/word"
        elenco.append(voce)
    risultato = voci(elenco, NOMI_BANDO)
    url_visti = {v["url"] for v in risultato}
    for titolo, url in (
        ("Pagina ufficiale del bando", getattr(bando, "link_ufficiale", None)),
        ("Piattaforma per presentare la domanda", getattr(bando, "link_domande", None)),
    ):
        if url and url not in url_visti:
            risultato.append({"tipo": "Pagina", "titolo": titolo, "url": url, "etichetta": titolo})
            url_visti.add(url)
    return risultato


def voci_gara(appalto: Any) -> list[dict]:
    from app.appalti.documenti import documentazione

    elenco = voci(documentazione(appalto))
    url_visti = {v["url"] for v in elenco}
    for titolo, url in (
        ("Piattaforma per presentare l'offerta", appalto.link_partecipazione),
        ("Avviso ufficiale", appalto.link),
    ):
        if url and url not in url_visti:
            elenco.append({"tipo": "Pagina", "titolo": titolo, "url": url, "etichetta": titolo})
            url_visti.add(url)
    return elenco
