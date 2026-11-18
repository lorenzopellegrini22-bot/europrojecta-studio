# Schema dati

Schema completo, confermato in fase 0. Le tabelle entrano nel database fase per fase (colonna **F**), ma la loro forma non dovrebbe più cambiare in modo sostanziale.

## Convenzioni
- Tabelle e colonne in italiano, snake_case. `id` è `bigint generated always as identity`.
- Tabelle principali: `creato_il`, `aggiornato_il` (`timestamptz`, default `now()`).
- Enum: `varchar` + `CHECK (col IN (...))`. Importi: `numeric(14,2)`. Percentuali: `numeric(5,2)`.
- Chiavi esterne vere, mai riferimenti polimorfici. `ON DELETE` esplicito su ogni FK.
- Array Postgres (`text[]`) per le liste di requisiti: `NULL` = nessun vincolo noto; **mai lista vuota** (`CHECK (col IS NULL OR cardinality(col) > 0)`).
- Testi da cercare: indice su `lower(unaccent(...))` dove serve.

## Accesso e sicurezza — F1

| Tabella | Colonne principali | Vincoli e note |
|---|---|---|
| `utente` | email, nome, password_hash, totp_secret_cifrato, totp_attivo, ruolo, attivo, tentativi_falliti, bloccato_fino_al, ultimo_accesso | email univoca (case-insensitive); ruolo ∈ titolare, collaboratore |
| `codice_recupero` | utente_id, codice_hash, usato_il | 10 per utente |
| `sessione` | token_hash, utente_id, mfa_verificata, scade_il, scadenza_assoluta, ultimo_uso, ip, user_agent | token_hash univoco; cancellata al logout |
| `log_accesso` | email_tentata, utente_id?, esito, ip, user_agent, creato_il | esito ∈ ok, password_errata, totp_errato, bloccato, utente_inesistente |
| `audit_log` | utente_id?, entita, entita_id, azione, differenze (jsonb), creato_il | azione ∈ crea, modifica, elimina, esporta, invia |

## Integrazioni — già implementata (migrazione 0002)

| Tabella | Colonne principali | Vincoli e note |
|---|---|---|
| `integrazione` | codice (PK), config (jsonb), segreti (jsonb: campo → valore cifrato), anteprime (jsonb: campo → ultime 4 cifre), verificata_il, verifica_ok, verifica_messaggio, aggiornato_da | codice ∈ openai, smtp, whatsapp. Qui sta anche l'API key di OpenAI (non in `impostazioni_ai`) |

Anche le tabelle di accesso (`utente`, `codice_recupero`, `sessione`, `log_accesso`, `audit_log`) sono già nella migrazione 0002. `utente` ha in più `totp_ultimo_intervallo` (contro il riuso dei codici) e `sessione` ha `csrf_token`.

## CRM — F1

| Tabella | Colonne principali | Vincoli e note |
|---|---|---|
| `azienda` | ragione_sociale, partita_iva, codice_fiscale, forma_giuridica, data_costituzione, dipendenti, fatturato, totale_attivo, anno_dati, dimensione_calcolata, dimensione_manuale, plafond_de_minimis_manuale, stato_cliente, frequenza_aggiornamenti, note, id_crm_origine | partita_iva univoca, 11 cifre (validazione con cifra di controllo nel codice); dimensione ∈ micro, piccola, media, grande; stato_cliente ∈ prospect, attivo, ex; frequenza ∈ nessuno, settimanale, mensile |
| `azienda_ateco` | azienda_id, codice, versione, primario | versione ∈ 2007, 2025; univoco (azienda, codice, versione); al massimo un primario per azienda (indice parziale) |
| `sede` | azienda_id, tipo, indirizzo, cap, comune_codice_istat → `comune`, comune_testo, provincia, regione | tipo ∈ legale, operativa; `comune_testo` conserva il valore importato se non riconosciuto |
| `comune` | codice_istat (PK testo), nome, provincia_sigla, regione, valido_dal, valido_al | tabella di riferimento ISTAT, caricata da file ufficiale (F1) |
| `aiuto_de_minimis` | azienda_id, data_concessione, importo, regolamento, ente, descrizione, fonte | regolamento ∈ generale, agricolo, pesca, sieg; fonte ∈ manuale, rna |
| `massimale_de_minimis` | regolamento, importo, riferimento_normativo, valido_dal | configurabile; generale 300.000, SIEG 750.000, agricolo e pesca da verificare |
| `tag`, `azienda_tag` | nome; (azienda_id, tag_id) | nome univoco |
| `referente` | azienda_id, nome, cognome, ruolo, email, telefono, principale, note, consenso_email, consenso_whatsapp, consenso_il, canale_preferito, disiscritto_il, token_disiscrizione | token_disiscrizione univoco, casuale |
| `progetto` | azienda_id, titolo, descrizione, importo_stimato, inizio_previsto, fine_prevista, stato, embedding (vector), embedding_modello, embedding_hash | stato ∈ idea, in_valutazione, in_corso, concluso, abbandonato |
| `categoria_spesa` | codice (PK), nome, ordine | vocabolario chiuso dei 12 valori del brief, caricato da migrazione |
| `progetto_categoria` | progetto_id, categoria_codice | PK composta |
| `attivita` | azienda_id, progetto_id?, pratica_id?, bando_id?, tipo, data, oggetto, testo, utente_id | tipo ∈ nota, chiamata, email, incontro |
| `scadenza` | azienda_id, progetto_id?, pratica_id?, bando_id?, titolo, data, completata, completata_il, origine | origine ∈ manuale, chiusura_bando |
| `allegato` | azienda_id, progetto_id?, pratica_id?, nome_originale, percorso, mime, dimensione, sha256, caricato_da | percorso con nome casuale |
| `import_job` | file_nome, tipo, stato, codifica, separatore, mappatura (jsonb), righe_totali, importate, scartate, errori (jsonb), prova_a_vuoto | tipo ∈ aziende, referenti |
| `mappatura_import` | nome, tipo, mappatura (jsonb) | mappature salvate e riutilizzabili |

`pratica` e `pratica_evento` sono descritte più sotto (F2, perché dipendono da `bando`).

## Bandi — F2

| Tabella | Colonne principali | Vincoli e note |
|---|---|---|
| `fonte` | codice, nome, url, attiva, fallimenti_consecutivi, ultimo_successo, ultimo_etag, ultimo_last_modified | codice univoco |
| `fonte_run` | fonte_id, inizio, fine, esito, nuovi, modificati, chiusi, invariati, scomparsi, errori, messaggio, dettagli (jsonb) | esito ∈ ok, invariato, fermato_sanita, errore |
| `bando` | fonte_id, id_esterno, id_rna, titolo, ente, link_ufficiale, link_domande, data_apertura, data_chiusura, note_apertura_chiusura, stato, raw (jsonb), hash_contenuto, visto_la_prima_volta, ultima_versione_vista, versioni_assenti, scomparso_dal_feed_il, bando_canonico_id → bando | univoco (fonte_id, id_esterno); indice su id_rna; stato ∈ in_arrivo, aperto, chiuso, sconosciuto |
| `bando_versione` | bando_id, raw, hash_contenuto, rilevata_il | storico a ogni modifica |
| `documento_bando` | bando_id, url, percorso, sha256, mime, testo_estratto, scaricato_il, errore | univoco (bando_id, sha256) |
| `bando_requisiti` | (vedi sotto) | uno per bando |
| `bando_override` | bando_id, campo, valore (jsonb), nota, utente_id | univoco (bando_id, campo) |
| `duplicato_candidato` | bando_a_id, bando_b_id, punteggio, motivo, decisione, deciso_da, deciso_il | decisione ∈ da_decidere, duplicato, distinti |

**`bando_requisiti`** (PK = bando_id)
- territorio ∈ nazionale, regionale, provinciale, comunale, sconosciuto; regioni[], province[], comuni[] (codici ISTAT)
- **aree_speciali[]** (Aree interne, ZES, Zone sismiche, Zone franche, Emergenza climatica): aggiunto dopo la verifica della fonte
- dimensioni[], ateco_ammessi[], ateco_esclusi[], forme_giuridiche[], tipologie_soggetto[], anzianita_min_mesi, anzianita_max_mesi
- categorie_spesa[], forma_agevolazione[], percentuale, importo_min, importo_max, dotazione
- regime ∈ de_minimis, esenzione, notificato, altro, sconosciuto; modalita ∈ sportello, graduatoria, click_day, altro, sconosciuto
- sintesi, embedding, embedding_modello, embedding_hash
- provenienza (jsonb: campo → fonte, llm, manuale), da_verificare[]
- risolto_il, hash_risoluzione

## Estrazione LLM — F3

| Tabella | Colonne principali | Note |
|---|---|---|
| `estrazione_bando` | bando_id, modello, versione_prompt, versione_schema, hash_input, output (jsonb), stato_revisione, token_ingresso, token_uscita, costo_stimato, errore | stato_revisione ∈ da_revisionare, revisionato |
| `impostazioni_ai` | modello_estrazione, modello_scoring, modello_embeddings, budget_mensile_eur, prezzi (jsonb: modello → prezzo per milione di token in ingresso e in uscita), sospesa_fino_al, motivo_sospensione | una sola riga (`CHECK (id = 1)`) |
| `uso_llm` | operazione, modello, token_ingresso, token_uscita, costo_stimato, esito, errore, creato_il | operazione ∈ estrazione, scoring, embedding, aggiornamento |

## Matching — F4

| Tabella | Colonne principali | Note |
|---|---|---|
| `match_run` | inizio, fine, esito, coppie_valutate, escluse, con_punteggio, chiamate_llm, costo_stimato, errori | |
| `match` | azienda_id, bando_id, progetto_id?, run_id, filtri_superati, esiti_filtri (jsonb), similarita, punteggio, motivazione, requisiti (jsonb: soddisfatti, da_verificare, mancanti), modello, versione_prompt, hash_input, hash_bando_feedback, notificato_il | univoco (azienda_id, bando_id); si salvano **anche le esclusioni** |
| `match_feedback` | match_id, valutazione, nota, utente_id | valutazione ∈ utile, non_pertinente, gia_noto |
| `collaudo_etichetta` | azienda_id, bando_id, atteso, nota | atteso ∈ chiaramente_compatibile, compatibile, non_compatibile; set di collaudo |

## Pratiche e comunicazioni — F2 (pratica), F5, F6

| Tabella | Colonne principali | Note |
|---|---|---|
| `pratica` | azienda_id, bando_id, progetto_id?, match_id?, stato, importo_richiesto, importo_concesso, protocollo, scadenza_presentazione, note | stato ∈ individuato, proposto, in_preparazione, presentata, ammessa, respinta, rendicontazione, chiusa |
| `pratica_evento` | pratica_id, da_stato, a_stato, nota, utente_id, creato_il | |
| `notifica_email` | tipo, destinatario, oggetto, corpo, match_inclusi (bigint[]), inviata_il, esito, errore | tipo ∈ riepilogo_mattutino, avviso_fonte, avviso_ai, promemoria |
| `aggiornamento_cliente` | azienda_id, referente_id, canale, oggetto, testo, bandi_citati (bigint[]), pratiche_citate (bigint[]), stato, approvato_da, approvato_il, inviato_il, id_messaggio_canale, errore | canale ∈ email, whatsapp; stato ∈ bozza, approvato, inviato, errore |
| `report_bando` | azienda_id, bando_id, contenuto (jsonb), percorso_pdf, stato | F6; stato ∈ bozza, approvato, inviato |
| `job_run` | job, inizio, fine, esito, messaggio, dettagli (jsonb) | F5: registro di ogni esecuzione del worker |

## Differenze rispetto al brief
- **Aggiunte:** `aree_speciali[]` e `tipologie_soggetto[]` in `bando_requisiti`; `massimale_de_minimis`; `duplicato_candidato`; `mappatura_import`; `collaudo_etichetta`; `job_run`; `token_disiscrizione` sul referente; `versioni_assenti`/`ultima_versione_vista` sul bando (scomparsa misurata in versioni del file, non in giorni); `ultimo_etag`/`ultimo_last_modified` sulla fonte.
- **Vincoli:** liste mai vuote; `impostazioni_ai` a riga singola.
- **Embeddings:** colonna `vector` senza dimensione (vedi `decisioni.md`).
- **Pratiche a fasi (migrazione 0005, decisa il 18/11/2026):**
  - `modello_pratica` (nome univoco, descrizione, attivo) → `modello_fase` (numero, nome, descrizione) → `modello_operazione` (numero, titolo, tipo ∈ attivita, documenti, dati, comunicazione; giorni_previsti; documenti[]; campi[]; oggetto_email; testo_email);
  - la pratica ne copia il contenuto in `pratica_fase` (stato ∈ da_iniziare, in_corso, completata, saltata), `pratica_operazione` (stato ∈ da_fare, fatta, non_applicabile; scadenza; assegnata_a), `pratica_documento` (stato ∈ mancante, richiesto, ricevuto, verificato) e `pratica_dato` (etichetta, valore);
  - `pratica` ha in più codice (es. 2026-0001), bando_testo (bando non raccolto), modello_id e responsabile_id.
