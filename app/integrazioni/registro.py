"""Elenco delle integrazioni configurabili da Impostazioni → Integrazioni.

Per aggiungere un'integrazione: descriverla qui, aggiungere il codice al check constraint
di `integrazione.codice` (migrazione) e, se si può verificare, una funzione in verifiche.py.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Campo:
    nome: str
    etichetta: str
    segreto: bool = False
    obbligatorio: bool = True
    tipo: str = "text"  # text, number, email, select
    opzioni: tuple[str, ...] = ()
    predefinito: str | None = None
    aiuto: str = ""
    variabile_ambiente: str | None = None


@dataclass(frozen=True)
class TipoIntegrazione:
    codice: str
    nome: str
    descrizione: str
    campi: tuple[Campo, ...]
    verificabile: bool
    note: tuple[str, ...] = field(default_factory=tuple)

    def campo(self, nome: str) -> Campo:
        return next(c for c in self.campi if c.nome == nome)


OPENAI = TipoIntegrazione(
    codice="openai",
    nome="OpenAI",
    descrizione=(
        "Intelligenza artificiale per leggere i bandi, valutare la compatibilità con le aziende "
        "e preparare le bozze degli aggiornamenti. Usa l'account OpenAI del consulente, che paga "
        "direttamente l'uso."
    ),
    campi=(
        Campo(
            "api_key",
            "API key",
            segreto=True,
            aiuto="Si crea nella dashboard di OpenAI, sezione API keys. Inizia con «sk-».",
            variabile_ambiente="OPENAI_API_KEY",
        ),
    ),
    verificabile=True,
    note=(
        "Ai modelli di OpenAI arrivano solo il testo pubblico dei bandi e dati anonimi delle "
        "aziende: mai ragione sociale, partita IVA, referenti o contatti.",
        "Imposta anche un limite di spesa direttamente nel tuo account OpenAI.",
    ),
)

SMTP = TipoIntegrazione(
    codice="smtp",
    nome="Email (SMTP)",
    descrizione=(
        "Server di posta per l'email del mattino, gli avvisi e gli aggiornamenti ai clienti "
        "approvati dal consulente."
    ),
    campi=(
        Campo(
            "host", "Server SMTP", aiuto="Es. smtp.tuoprovider.it", variabile_ambiente="SMTP_HOST"
        ),
        Campo(
            "porta",
            "Porta",
            tipo="number",
            predefinito="587",
            aiuto="587 con STARTTLS, 465 con SSL",
            variabile_ambiente="SMTP_PORTA",
        ),
        Campo(
            "sicurezza",
            "Sicurezza",
            tipo="select",
            opzioni=("starttls", "ssl"),
            predefinito="starttls",
            variabile_ambiente="SMTP_SICUREZZA",
        ),
        Campo("utente", "Utente", variabile_ambiente="SMTP_UTENTE"),
        Campo("password", "Password", segreto=True, variabile_ambiente="SMTP_PASSWORD"),
        Campo(
            "mittente",
            "Indirizzo mittente",
            tipo="email",
            aiuto="Es. studio@dominio.it",
            variabile_ambiente="SMTP_MITTENTE",
        ),
        Campo(
            "nome_mittente",
            "Nome mittente",
            obbligatorio=False,
            aiuto="Es. Studio Rossi - Finanza agevolata",
        ),
    ),
    verificabile=True,
    note=("La verifica si collega al server e fa il login, senza inviare email.",),
)

WHATSAPP = TipoIntegrazione(
    codice="whatsapp",
    nome="WhatsApp Business",
    descrizione=(
        "Invio degli aggiornamenti ai clienti tramite la WhatsApp Business Platform di Meta, "
        "con un account intestato al consulente."
    ),
    campi=(
        Campo(
            "phone_number_id",
            "Phone number ID",
            aiuto="Dal pannello WhatsApp dell'app Meta for Developers.",
            variabile_ambiente="WHATSAPP_PHONE_NUMBER_ID",
        ),
        Campo(
            "business_account_id",
            "WhatsApp Business Account ID",
            variabile_ambiente="WHATSAPP_BUSINESS_ACCOUNT_ID",
        ),
        Campo(
            "access_token",
            "Access token",
            segreto=True,
            aiuto="Token permanente di un utente di sistema.",
            variabile_ambiente="WHATSAPP_ACCESS_TOKEN",
        ),
    ),
    verificabile=False,
    note=(
        "I messaggi avviati dall'azienda richiedono template approvati da Meta e hanno un costo "
        "per messaggio, addebitato all'account del consulente.",
        "La verifica della connessione arriverà con il canale WhatsApp (fase 5).",
    ),
)

INTEGRAZIONI: dict[str, TipoIntegrazione] = {i.codice: i for i in (OPENAI, SMTP, WHATSAPP)}
