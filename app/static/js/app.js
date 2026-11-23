// Menu laterale su schermi stretti: il pulsante "Menu" apre e chiude la navigazione.
(() => {
  const laterale = document.getElementById("laterale");
  const pulsante = laterale && laterale.querySelector(".apri-menu");
  if (!pulsante) return;
  pulsante.addEventListener("click", () => {
    const aperto = laterale.toggleAttribute("data-aperto");
    pulsante.setAttribute("aria-expanded", String(aperto));
    pulsante.textContent = aperto ? "Chiudi" : "Menu";
  });
})();

// Tendine del menu laterale: ricordano quali sono aperte (solo in questo browser).
// Quella con la pagina corrente parte sempre aperta.
(() => {
  for (const sezione of document.querySelectorAll("details.sezione[data-sezione]")) {
    const chiave = `menu:${sezione.dataset.sezione}`;
    if (!sezione.hasAttribute("data-corrente")) {
      try {
        const salvato = localStorage.getItem(chiave);
        if (salvato !== null) sezione.open = salvato === "1";
      } catch { /* archiviazione non disponibile: restano chiuse */ }
    }
    sezione.addEventListener("toggle", () => {
      try { localStorage.setItem(chiave, sezione.open ? "1" : "0"); } catch { /* ignora */ }
    });
  }
})();

// Pulsanti "Copia il testo": copiano il contenuto dell'elemento indicato in data-copia.
document.addEventListener("click", async (evento) => {
  const pulsante = evento.target.closest("[data-copia]");
  if (!pulsante) return;
  const sorgente = document.getElementById(pulsante.dataset.copia);
  if (!sorgente) return;
  const originale = pulsante.textContent;
  try {
    await navigator.clipboard.writeText(sorgente.textContent);
    pulsante.textContent = "Copiato";
  } catch {
    pulsante.textContent = "Copia non riuscita: seleziona il testo a mano";
  }
  setTimeout(() => { pulsante.textContent = originale; }, 2000);
});

// Avviso di scadenza della sessione per inattività (minuti in data-inattivita sul body).
// 5 minuti prima compare un avviso con «Resta collegato»; allo scadere si va al login.
(() => {
  const minuti = Number(document.body.dataset.inattivita || 0);
  const avviso = document.getElementById("avviso-sessione");
  if (!minuti || !avviso) return;
  const testo = document.getElementById("avviso-sessione-testo");
  const anticipo = Math.min(5, Math.max(1, Math.floor(minuti / 3)));
  let ultimaAttivita = Date.now();

  const rinnova = async () => {
    try {
      const r = await fetch("/sessione/rinnova", { credentials: "same-origin", cache: "no-store" });
      if (r.status === 204) {
        ultimaAttivita = Date.now();
        avviso.hidden = true;
        return;
      }
    } catch { /* rete assente: riprova al prossimo clic */ }
    window.location.href = "/login";
  };
  document.getElementById("resta-collegato").addEventListener("click", rinnova);
  // Ogni richiesta HTMX conta come attività (il server aggiorna l'ultimo uso)
  document.body.addEventListener("htmx:afterRequest", () => { ultimaAttivita = Date.now(); });

  setInterval(() => {
    const restanti = minuti * 60000 - (Date.now() - ultimaAttivita);
    if (restanti <= 0) {
      window.location.href = "/login";
    } else if (restanti <= anticipo * 60000) {
      const m = Math.ceil(restanti / 60000);
      testo.textContent = `La sessione scade tra ${m} minut${m === 1 ? "o" : "i"} per inattività.`;
      avviso.hidden = false;
    }
  }, 15000);
})();

// Telefono: le tabelle degli elenchi diventano schede (CSS sotto i 640px). Ogni cella riceve
// come etichetta il titolo della sua colonna; le celle vuote («—») si nascondono.
const preparaTabelle = (radice) => {
  for (const tabella of radice.querySelectorAll(".overflow-auto > table, table.a-schede-sempre")) {
    if (tabella.classList.contains("a-schede") || tabella.matches(".checklist, .barre, .scheda-gara")) continue;
    const titoli = [...tabella.querySelectorAll("thead tr:last-child > *")].map((c) => c.textContent.trim());
    if (titoli.length < 2) continue;
    for (const riga of tabella.querySelectorAll("tbody tr")) {
      let colonna = 0;
      for (const cella of riga.children) {
        const etichetta = titoli[colonna] || "";
        if (etichetta) cella.dataset.label = etichetta;
        else cella.classList.add("senza-etichetta");
        const testo = cella.textContent.trim();
        if ((testo === "" || testo === "—") && !cella.querySelector("input, button, select, a, img")) {
          cella.classList.add("vuota");
        }
        colonna += Number(cella.getAttribute("colspan") || 1);
      }
    }
    tabella.classList.add("a-schede");
  }
};
preparaTabelle(document);
document.body.addEventListener("htmx:afterSwap", (e) => preparaTabelle(e.target));

// Telefono: i filtri occupavano tutto il primo schermo. Restano visibili il primo campo e
// i pulsanti; «Filtri» apre gli altri e dice quanti sono già impostati.
(() => {
  if (!window.matchMedia("(max-width: 700px)").matches) return;
  for (const form of document.querySelectorAll("form.filtri")) {
    const campi = [...form.querySelectorAll("input:not([type=hidden]), select")];
    if (campi.length < 3) continue;
    const primo = form.querySelector(":scope > :first-child");
    const nascosti = campi.filter((c) => !(c === primo || (primo && primo.firstElementChild === c)));
    // attivi = impostati nell'indirizzo (un menu può avere già scelta una voce predefinita)
    const parametri = new URLSearchParams(window.location.search);
    const attivi = nascosti.filter((c) => c.name && (parametri.get(c.name) || "").trim() !== "").length;
    const azioni = form.querySelector(".azioni-filtri");
    if (!azioni) continue;
    const pulsante = document.createElement("button");
    pulsante.type = "button";
    pulsante.className = "secondary outline apri-filtri";
    pulsante.setAttribute("aria-expanded", "false");
    pulsante.textContent = attivi ? `Filtri (${attivi})` : "Filtri";
    pulsante.addEventListener("click", () => {
      const chiusi = form.classList.toggle("compatti");
      pulsante.setAttribute("aria-expanded", String(!chiusi));
      pulsante.textContent = chiusi ? (attivi ? `Filtri (${attivi})` : "Filtri") : "Meno filtri";
    });
    azioni.prepend(pulsante);
    form.classList.add("compatti");
  }
})();

// Web app: il service worker tiene in memoria solo i file statici (CSS, JS, font, icone) e
// mostra una pagina «Sei offline» senza rete. Le pagine con i dati non si salvano mai.
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* senza: l'app funziona uguale */ });
  });
}

// Modulo cliente: per gli enti pubblici si nascondono i campi da impresa (ATECO, dimensione,
// bilancio) e compaiono gli abitanti. Su un cliente nuovo «Comune di …» sceglie l'ente da sé.
(function () {
  const scelta = document.querySelector("select[data-tipo-cliente]");
  if (!scelta) return;
  const form = scelta.form;
  const aggiorna = () => {
    const ente = scelta.value === "ente_pubblico";
    form.querySelectorAll("[data-solo-ente]").forEach((el) => { el.hidden = !ente; });
    form.querySelectorAll("[data-solo-impresa]").forEach((el) => { el.hidden = ente; });
  };
  let toccato = Boolean(form.querySelector("input[name=azienda_id]"));
  scelta.addEventListener("change", () => { toccato = true; aggiorna(); });
  const nome = form.querySelector("input[name=ragione_sociale]");
  const ENTI = /^(comune|provincia|citt[aà] metropolitana|regione|unione (dei |di )?comuni|comunit[aà] montana)\b/i;
  if (nome) {
    nome.addEventListener("input", () => {
      if (toccato) return;
      scelta.value = ENTI.test(nome.value.trim()) ? "ente_pubblico" : "impresa";
      aggiorna();
    });
  }
  aggiorna();
})();
