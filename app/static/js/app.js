// Menu laterale su schermi stretti: il pulsante "Menu" apre e chiude la navigazione.
(() => {
  const laterale = document.getElementById("laterale");
  const pulsante = laterale && laterale.querySelector(".apri-menu");
  if (!pulsante) return;
  const imposta = (aperto) => {
    laterale.toggleAttribute("data-aperto", aperto);
    document.documentElement.classList.toggle("menu-aperto", aperto);
    pulsante.setAttribute("aria-expanded", String(aperto));
    pulsante.textContent = aperto ? "Chiudi" : "Menu";
    if (aperto) laterale.scrollTop = 0;
  };
  pulsante.addEventListener("click", () => imposta(!laterale.hasAttribute("data-aperto")));
  // Esc chiude; tornando indietro con il browser il menu riparte chiuso
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && laterale.hasAttribute("data-aperto")) imposta(false);
  });
  window.addEventListener("pageshow", () => imposta(false));
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
  // ambiti predefiniti del tipo di cliente, finché non si toccano le caselle «Dove cercare»
  const PREDEFINITI = { impresa: ["nazionali", "locali", "appalti"], ente_pubblico: ["ue", "nazionali", "locali"] };
  let ambitiToccati = Boolean(form.querySelector("input[name=azienda_id]"));
  form.querySelectorAll("input[name=ambiti_ricerca]").forEach((c) => {
    c.addEventListener("change", () => { ambitiToccati = true; });
  });
  const ambitiPredefiniti = () => {
    if (ambitiToccati) return;
    const scelti = PREDEFINITI[scelta.value] || [];
    form.querySelectorAll("input[name=ambiti_ricerca]").forEach((c) => { c.checked = scelti.includes(c.value); });
  };
  scelta.addEventListener("change", () => { toccato = true; aggiorna(); ambitiPredefiniti(); });
  const nome = form.querySelector("input[name=ragione_sociale]");
  const ENTI = /^(comune|provincia|citt[aà] metropolitana|regione|unione (dei |di )?comuni|comunit[aà] montana)\b/i;
  if (nome) {
    nome.addEventListener("input", () => {
      if (toccato) return;
      scelta.value = ENTI.test(nome.value.trim()) ? "ente_pubblico" : "impresa";
      aggiorna();
      ambitiPredefiniti();
    });
  }
  aggiorna();
})();

// Rotella sui pulsanti dei moduli che salvano o calcolano («Aggiorna adesso» dura qualche
// secondo e senza segnale sembrava non succedere niente). Vale per ogni modulo POST: il
// pulsante premuto mostra la rotella e il testo di attesa (data-attesa), gli altri si
// bloccano per evitare doppi invii. I download sono tutti GET, quindi la pagina cambia sempre.
(function () {
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    if (ev.defaultPrevented || (form.method || "").toLowerCase() !== "post") return;
    if ([...form.attributes].some((a) => a.name.startsWith("hx-"))) return;
    const premuto = ev.submitter || form.querySelector("button[type=submit], button:not([type])");
    // dopo che il browser ha raccolto i dati (un pulsante disattivato non verrebbe inviato)
    setTimeout(() => {
      form.querySelectorAll("button, input[type=submit]").forEach((b) => { b.disabled = true; });
      form.setAttribute("aria-busy", "true");
      if (premuto && premuto.tagName === "BUTTON") {
        premuto.dataset.testoOriginale = premuto.innerHTML;
        premuto.classList.add("in-attesa");
        const testo = premuto.dataset.attesa || premuto.textContent.trim();
        premuto.innerHTML = '<span class="rotella" aria-hidden="true"></span> ' + testo;
      }
    }, 0);
  });
  // tornando indietro con il browser la pagina può riapparire dalla cache: pulsanti di nuovo attivi
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[aria-busy=true]").forEach((form) => {
      form.removeAttribute("aria-busy");
      form.querySelectorAll("button, input[type=submit]").forEach((b) => { b.disabled = false; });
      form.querySelectorAll("button.in-attesa").forEach((b) => {
        b.innerHTML = b.dataset.testoOriginale || b.innerHTML;
        b.classList.remove("in-attesa");
      });
    });
  });
})();

// Elenco clienti: selezione multipla ed eliminazione definitiva con conferma scritta
(function () {
  const form = document.getElementById("selezione-clienti");
  if (!form) return;
  const caselle = () => [...document.querySelectorAll('input[name="ids"][form="selezione-clienti"]')];
  const tutti = [...document.querySelectorAll("[data-seleziona-tutti]")];
  const barra = form.querySelector(".barra-selezione");
  const dialogo = form.querySelector("dialog");
  const campo = form.querySelector("[data-campo-conferma]");
  const invia = form.querySelector("[data-conferma-invia]");
  const aggiorna = () => {
    const scelte = caselle().filter((c) => c.checked).length;
    form.querySelectorAll("[data-conteggio]").forEach((el) => { el.textContent = scelte; });
    barra.hidden = scelte === 0;
    tutti.forEach((t) => {
      t.checked = scelte > 0 && scelte === caselle().length;
      t.indeterminate = scelte > 0 && scelte < caselle().length;
    });
  };
  document.addEventListener("change", (ev) => {
    if (tutti.includes(ev.target)) caselle().forEach((c) => { c.checked = ev.target.checked; });
    if (tutti.includes(ev.target) || caselle().includes(ev.target)) aggiorna();
  });
  form.querySelector("[data-deseleziona]").addEventListener("click", () => {
    caselle().forEach((c) => { c.checked = false; });
    aggiorna();
  });
  form.querySelector("[data-apri-conferma]").addEventListener("click", () => {
    campo.value = "";
    invia.disabled = true;
    dialogo.showModal();
    campo.focus();
  });
  form.querySelector("[data-chiudi-conferma]").addEventListener("click", () => dialogo.close());
  campo.addEventListener("input", () => {
    invia.disabled = campo.value.trim().toUpperCase() !== "ELIMINA";
  });
  aggiorna();
})();

// Automazioni: i campi che dipendono da «Quando parte» e «Per quali clienti»
(function () {
  const evento = document.querySelector("select[data-evento-automazione]");
  if (!evento) return;
  const form = evento.form;
  const destinatari = form.querySelector("select[data-destinatari-automazione]");
  const aggiorna = () => {
    form.querySelectorAll("[data-solo-evento]").forEach((el) => {
      el.hidden = el.dataset.soloEvento !== evento.value;
    });
    form.querySelectorAll("[data-solo-destinatari]").forEach((el) => {
      el.hidden = el.dataset.soloDestinatari !== destinatari.value;
    });
  };
  // cambiando «Quando parte» il testo predefinito segue, se non è stato modificato
  const modelli = JSON.parse(form.dataset.modelli || "{}");
  let precedente = evento.value;
  evento.addEventListener("change", () => {
    const vecchio = modelli[precedente];
    const nuovo = modelli[evento.value];
    const oggetto = form.querySelector("[name=oggetto]");
    const testo = form.querySelector("[name=testo]");
    if (vecchio && nuovo && oggetto.value === vecchio.oggetto && testo.value === vecchio.testo) {
      oggetto.value = nuovo.oggetto;
      testo.value = nuovo.testo;
    }
    precedente = evento.value;
    aggiorna();
  });
  destinatari.addEventListener("change", aggiorna);
  aggiorna();
})();

// Moduli con data-conferma: chiedono conferma prima di inviare (es. eliminare)
document.addEventListener("submit", (ev) => {
  const testo = ev.target instanceof HTMLFormElement ? ev.target.dataset.conferma : null;
  if (testo && !window.confirm(testo)) ev.preventDefault();
}, true);
