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
