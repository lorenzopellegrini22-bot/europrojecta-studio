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
