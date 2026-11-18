"""Elenco delle integrazioni configurabili da Impostazioni → Integrazioni.

Per aggiungere un'integrazione: descriverla qui, aggiungere il codice al check constraint
di `integrazione.codice` (migrazione) e, se si può verificare, una funzione in verifiche.py.
Ogni integrazione ha una guida al collegamento (pulsante "i"): solo link ufficiali verificati.
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
    etichette_opzioni: tuple[str, ...] = ()  # testo mostrato per ogni opzione (stesso ordine)
    predefinito: str | None = None
    aiuto: str = ""
    variabile_ambiente: str | None = None

    def etichetta_opzione(self, valore: str) -> str:
        if valore in self.opzioni and len(self.etichette_opzioni) == len(self.opzioni):
            return self.etichette_opzioni[self.opzioni.index(valore)]
        return valore.upper()


@dataclass(frozen=True)
class TipoIntegrazione:
    codice: str
    nome: str
    descrizione: str
    campi: tuple[Campo, ...]
    verificabile: bool
    note: tuple[str, ...] = field(default_factory=tuple)
    # Guida al collegamento (pulsante "i"): passaggi in ordine e link ufficiali verificati
    guida: tuple[str, ...] = field(default_factory=tuple)
    link_guida: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Le integrazioni a pagamento per chiamata partono disattivate finché il titolare non le attiva
    attiva_predefinita: bool = True

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
    guida=(
        "Accedi (o registrati) alla piattaforma per sviluppatori di OpenAI con l'account dello "
        "studio.",
        "In Billing aggiungi un metodo di pagamento e del credito: senza credito le chiamate "
        "falliscono con «quota insufficiente».",
        "Imposta un limite mensile di spesa (Limits) per non avere sorprese.",
        "Apri la pagina API keys e premi «Create new secret key». Dagli un nome riconoscibile, "
        "per esempio «Europrojecta Studio».",
        "Copia la chiave (inizia con «sk-»): OpenAI la mostra una sola volta.",
        "Incollala qui nel campo «API key», premi Salva e poi «Verifica connessione».",
    ),
    link_guida=(("Pagina delle API key di OpenAI", "https://platform.openai.com/api-keys"),),
)

SMTP = TipoIntegrazione(
    codice="smtp",
    nome="Email (SMTP)",
    descrizione=(
        "Server di posta per inviare le comunicazioni ai clienti dalle pratiche, i promemoria "
        "delle scadenze e, più avanti, l'email del mattino."
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
            etichette_opzioni=("STARTTLS (porta 587)", "SSL/TLS (porta 465)"),
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
    guida=(
        "Recupera i dati SMTP della casella da cui vuoi inviare: li trovi nel pannello del tuo "
        "provider di posta (Aruba, Register, Google Workspace, Microsoft 365…) alla voce "
        "«configurazione del client di posta» o «SMTP».",
        "Server e porta: di solito porta 587 con STARTTLS oppure 465 con SSL.",
        "Utente: in genere è l'indirizzo email completo.",
        "Password: con Google Workspace e Microsoft 365, se l'account ha la verifica in due "
        "passaggi, serve una «password per le app» creata nelle impostazioni di sicurezza "
        "dell'account, non la password normale.",
        "Mittente: l'indirizzo che i clienti vedranno, uguale (o autorizzato) alla casella.",
        "Salva e premi «Verifica connessione»: l'app fa il login senza inviare nulla. Poi puoi "
        "mandarti un'email di prova con il pulsante dedicato.",
    ),
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
    guida=(
        "Serve un account Meta Business verificato e un'app di tipo «Business» su Meta for "
        "Developers, con il prodotto WhatsApp aggiunto.",
        "Nel pannello WhatsApp dell'app, sezione API Setup, trovi «Phone number ID» e "
        "«WhatsApp Business Account ID».",
        "Per il token crea un utente di sistema in Business Manager (Impostazioni → Utenti di "
        "sistema), assegnagli l'app e genera un token permanente con i permessi "
        "whatsapp_business_messaging e whatsapp_business_management.",
        "Copia i tre valori qui e salva. L'invio dei messaggi arriverà con il canale WhatsApp.",
    ),
    link_guida=(
        (
            "Guida introduttiva alla WhatsApp Cloud API (Meta)",
            "https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started",
        ),
    ),
)

REGISTRO_IMPRESE = TipoIntegrazione(
    codice="registro_imprese",
    nome="Registro Imprese (Openapi)",
    descrizione=(
        "Compila la scheda di un cliente partendo dalla partita IVA o dal codice fiscale: "
        "ragione sociale, sede legale, PEC, codice SDI, forma giuridica, ATECO, dipendenti e "
        "fatturato. Usa il servizio «Company» di Openapi S.p.A. con l'account del consulente."
    ),
    campi=(
        Campo(
            "token",
            "Token",
            segreto=True,
            aiuto="Il token creato nella console di Openapi, sezione OAuth.",
            variabile_ambiente="OPENAPI_COMPANY_TOKEN",
        ),
        Campo(
            "ambiente",
            "Ambiente",
            tipo="select",
            opzioni=("sandbox", "produzione"),
            etichette_opzioni=("Sandbox (prove gratuite, aziende di esempio)", "Produzione"),
            predefinito="sandbox",
        ),
        Campo(
            "livello",
            "Dati da richiedere",
            tipo="select",
            opzioni=("advanced", "start"),
            etichette_opzioni=(
                "Completi: anche ATECO, PEC, forma giuridica, dipendenti, fatturato",
                "Base: anagrafica, sede e codice SDI (costa meno)",
            ),
            predefinito="advanced",
        ),
        Campo(
            "partita_iva_verifica",
            "Partita IVA per la verifica",
            obbligatorio=False,
            predefinito="12485671007",
            aiuto="L'azienda cercata da «Verifica connessione». Predefinita: Openapi S.p.A.",
        ),
    ),
    verificabile=True,
    attiva_predefinita=False,
    note=(
        "Ogni ricerca in produzione ha un costo, addebitato sul conto Openapi del consulente: "
        "controlla i prezzi nella console prima di attivarla. Anche «Verifica connessione» fa "
        "una ricerca vera.",
        "Al servizio arriva solo la partita IVA (o il codice fiscale) cercata: nessun altro dato "
        "dei clienti.",
    ),
    guida=(
        "Registrati su console.openapi.com con i dati dello studio e ricarica il borsellino "
        "(«wallet»), oppure attiva un abbonamento per il servizio «Company».",
        "Vai nella sezione OAuth della console e crea un nuovo token.",
        "Negli scope del token seleziona almeno GET:company.openapi.com/IT-start e "
        "GET:company.openapi.com/IT-advanced. Per provare gratis aggiungi anche gli scope "
        "dell'ambiente di test: GET:test.company.openapi.com/IT-start e "
        "GET:test.company.openapi.com/IT-advanced.",
        "Scegli una scadenza lunga (per esempio un anno) e segna in agenda la data per rinnovarlo.",
        "Copia il token e incollalo qui nel campo «Token».",
        "Per le prime prove lascia l'ambiente «Sandbox»: risponde solo per le aziende di "
        "esempio elencate da Openapi, senza costi. Poi passa a «Produzione».",
        "Salva, premi «Verifica connessione» e, se l'esito è positivo, attiva l'integrazione con "
        "l'interruttore. Da quel momento nella scheda cliente compare il pulsante «Compila dal "
        "Registro Imprese».",
    ),
    link_guida=(
        ("Console di Openapi: sezione OAuth (token)", "https://console.openapi.com/it/oauth"),
        (
            "Documentazione del servizio Company",
            "https://console.openapi.com/it/apis/company/documentation",
        ),
        (
            "Aziende di esempio per la sandbox",
            "https://docs.openapi.it/company-sandbox-examples.html",
        ),
    ),
)

INTEGRAZIONI: dict[str, TipoIntegrazione] = {
    i.codice: i for i in (OPENAI, SMTP, WHATSAPP, REGISTRO_IMPRESE)
}
