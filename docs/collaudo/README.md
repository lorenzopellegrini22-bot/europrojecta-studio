# Collaudo prima della consegna

Requisito del contratto: prova su **20 bandi reali e 10 aziende**. Almeno **8 match su 10**
devono essere pertinenti e **nessun bando chiaramente compatibile** deve essere scartato.

## Procedura
1. Preparare il set (fatto il 18/11/2026: `set-proposto.json`, 20 bandi aperti presi dal
   database e 10 aziende di prova realistiche, 200 coppie):
   ```bash
   python -m app.cli collaudo-prepara --file docs/collaudo/set-proposto.json
   ```
   Il set va rivisto con il consulente: si possono sostituire bandi e aziende con casi
   che conosce bene (anche suoi clienti reali, resi anonimi).
2. **Etichettare insieme al consulente**: per ogni coppia il campo `atteso` diventa
   `compatibile`, `non_compatibile` o `dubbio` (i dubbi non entrano nel calcolo).
3. Eseguire:
   ```bash
   python -m app.cli collaudo --file docs/collaudo/set-proposto.json --report collaudo.md
   ```
   Il comando esce con codice 0 se il collaudo è superato. Il report in Markdown contiene
   precisione, esclusioni sbagliate con il motivo, proposte non pertinenti e la tabella
   completa delle coppie.
4. Correggere regole o soglie sui casi sbagliati e ripetere.

## Cosa ha già trovato il collaudo (18/11/2026, prima dell'etichettatura)
- Un bando per le imprese **esportatrici** veniva escluso per tutti come «bando per il
  settore sport» (la parola «sport» dentro «esportatrici»): corretto, ora le parole del
  settore si cercano solo a inizio parola.
- I bandi di **enti locali** senza territorio indicato (es. «Comune di Treviglio») venivano
  proposti a tutti: ora restano visibili ma con punteggio sotto la soglia.

## Collaudo con il Comune di Vignanello (24/11/2026)
Prova fatta da Claude, non ancora con il consulente: tutti i 1.201 bandi aperti o in arrivo
del database locale abbinati al Comune di Vignanello (VT, circa 4.600 abitanti). Etichettate
204 coppie (proposte, sotto soglia e le escluse del Lazio o che parlano di comuni) leggendo
il testo dei bandi: `vignanello.json`, report in `vignanello.md`.

```bash
python -m app.cli collaudo --file docs/collaudo/vignanello.json --report docs/collaudo/vignanello.md
```

- **Prima delle correzioni: non superato.** 13 proposte, 3 sbagliate (77%):
  - i due fondi di garanzia del Credito Sportivo, che nel testo valgono per i «soggetti
    pubblici diversi dagli enti territoriali»;
  - «Energia in Vetta», solo per chi gestisce impianti di risalita e piste da sci.
- **Mancava** «L'Italia delle donne» (Pari Opportunità): la fonte non dice i beneficiari,
  il testo sì («possono presentare… gli enti locali»).
- **Dopo le correzioni: superato**, 10 proposte pertinenti su 10, nessuna esclusione
  sbagliata. Resta sotto soglia il Fondo nazionale efficienza energetica (aperto alla PA):
  la scheda Invitalia ha solo il titolo finché la lettura notturna delle pagine non
  scarica il testo, poi la nuova regola lo riconosce.
- Trovato anche: i parcheggi del Lazio escludono i comuni capoluogo, che ora non lo ricevono.
- I bandi Horizon per città e quartieri sono etichettati «dubbio»: ammessi, ma servono
  consorzi di ricerca internazionali.

## Set del contratto etichettato da Claude (24/11/2026)
`set-etichettato.json` è `set-proposto.json` (10 aziende di prova × 20 bandi, ancora tutti
aperti) con le etichette messe leggendo i bandi; report in `set-etichettato.md`. Da rivedere
con il consulente.

- **Prima delle correzioni: non superato**, 4 pertinenti su 24 proposte giudicate (17%):
  - bandi nazionali non collegati ai progetti arrivavano a 45 esatti (Conto Termico a una
    software house, finanziamenti per l'estero a chi non esporta): ora −25;
  - Workers Buyout del Lazio a una ditta appena nata, indennizzo dell'alluvione 2023 a ogni
    impresa di Firenze e Pisa, fondo umbro in cui la domanda la fanno i Confidi: ora restano
    sotto la soglia con il motivo;
  - Conto Termico (interventi negli edifici) al progetto «Flotta green»: ora conta solo se un
    progetto riguarda immobili e opere.
- **Dopo: superato**, 4 pertinenti su 4, nessuna esclusione sbagliata; altre 4 proposte sono
  «dubbio» (filiera autobus elettrici, formazione continua, impresa femminile).
