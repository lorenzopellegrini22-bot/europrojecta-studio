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
