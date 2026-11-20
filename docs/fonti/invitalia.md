# Invitalia (incentivi nazionali)

Verifica del 20/11/2026.

## Perché
Il file open data di Incentivi Italia non contiene alcune misure gestite da Invitalia molto
richieste dai clienti: Resto al Sud 2.0, Autoimpiego Centro-Nord, Brevetti+, Voucher cloud e
cybersecurity, Cultura Cresce.

## Pagine lette
- `https://www.invitalia.it/per-le-imprese/incentivi-e-strumenti` (109 schede, 10 pagine)
- `https://www.invitalia.it/per-chi-vuole-fare-impresa/incentivi-e-strumenti` (31 schede)

Le pagine sono in HTML (nessuna API, niente JavaScript), paginate con `?page=0,1,…`. Ogni
scheda (`div.card-body`) contiene: titolo (`h3 a`), stato («Attivo», «In apertura», «Chiuso»),
una riga di presentazione, «Data apertura» e «Data chiusura» (a volte assenti). Basta l'elenco:
circa 12 richieste a notte, una al secondo come per le altre fonti.

## robots.txt e note legali
- robots.txt vieta solo aree tecniche (`/admin/`, `/search/`, `/user/…`, `/node/add/`…), non le
  pagine degli incentivi. Non c'è Crawl-delay.
- Le note legali (`/note-legali`) vietano la riproduzione dei contenuti a uso commerciale e
  chiedono di citare la fonte. Si salvano quindi solo i dati essenziali (titolo, stato, date, la
  riga di presentazione) e il link alla pagina ufficiale; la scheda del bando cita Invitalia.
  I PDF si leggono dalla pagina ufficiale come per gli altri bandi.

## Interpretazione
- Stato: Attivo → aperto, In apertura → in arrivo, Chiuso → chiuso (le date, se ci sono,
  prevalgono, come per le altre fonti).
- Territorio: nazionale, tranne le misure con «Sud»/«Mezzogiorno» (regioni del Sud) o
  «Centro-Nord» nel nome; sempre «da verificare», come beneficiari e importi, che l'AI ricava
  dai documenti.
- Esclusi i servizi di Invitalia che non sono incentivi (albi fornitori, piattaforma gare).
- Doppioni: le stesse misure nel dataset nazionale (ente Ministero delle Imprese o Invitalia)
  si collegano alla scheda Invitalia (app/bandi/doppioni.py).

Prova del 20/11/2026: 117 schede, 31 aperte e 1 in arrivo; 9 misure nazionali collegate
(ON, Smart&Start Italia, Nuova Sabatini, Imprenditoria femminile, Digital Transformation…).
