// Service worker della web app (servito come /sw.js per valere su tutto il sito).
// Solo i file statici in /static/ (con ?v= cambiano indirizzo a ogni modifica) finiscono in
// memoria; le pagine dell'app contengono dati dei clienti e passano sempre dalla rete.
const CACHE = "europrojecta-statici-v1";

const OFFLINE = `<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline · Europrojecta Studio</title></head>
<body style="font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f6f7f9;color:#14151c">
<main style="max-width:22rem;padding:1.5rem;text-align:center">
<h1 style="font-size:1.25rem">Sei offline</h1>
<p>Europrojecta Studio ha bisogno della connessione: i dati dei clienti non restano salvati sul telefono.</p>
<p><a href="" style="color:#0544d4">Riprova</a></p></main></body></html>`;

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomi) => Promise.all(nomi.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (evento) => {
  const richiesta = evento.request;
  if (richiesta.method !== "GET") return;
  const url = new URL(richiesta.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    evento.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const salvata = await cache.match(richiesta);
        if (salvata) return salvata;
        const risposta = await fetch(richiesta);
        if (risposta.ok) cache.put(richiesta, risposta.clone());
        return risposta;
      }),
    );
  } else if (richiesta.mode === "navigate") {
    evento.respondWith(
      fetch(richiesta).catch(() => new Response(OFFLINE, {
        status: 503,
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
      })),
    );
  }
});
