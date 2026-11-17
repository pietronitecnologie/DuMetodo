/* DuMetodo – ponte con il client desktop (caricato solo se la pagina è aperta dal client).
 *
 * Clic destro: cerca l'elemento più vicino con data-dm-menu="<tipo>", compone le voci di quel tipo
 * più quelle della "pagina" (definite in Configurazione e già filtrate per i permessi dal server),
 * e chiede al client di mostrare un menu NATIVO. Alla scelta, il client chiama DuMetodoClient.esegui(id).
 */
(() => {
  "use strict";
  if (!window.ipc || typeof window.ipc.postMessage !== "function") return;

  let menu = {};
  let inAttesa = {};

  const invia = (messaggio) => window.ipc.postMessage(JSON.stringify(messaggio));

  const carica = () =>
    fetch("/client/configurazione.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : { menu: {} }))
      .then((d) => { menu = d.menu || {}; })
      .catch(() => { menu = {}; });

  const dati = (el) => {
    const d = {};
    if (!el) return d;
    for (const [k, v] of Object.entries(el.dataset)) {
      if (k.startsWith("dm") && k !== "dmMenu") d[k.slice(2).toLowerCase()] = v;
    }
    return d;
  };

  // Sostituisce {nome}; con codifica=true il valore viene codificato per l'URL.
  const riempi = (testo, d, codifica) =>
    (testo || "").replace(/\{([a-z][a-z0-9_]*)\}/g, (_, n) => {
      const v = d[n] ?? "";
      return codifica ? encodeURIComponent(v) : v;
    });

  const csrf = () => {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || ""; } catch (e) { return ""; }
  };

  const azioni = {
    naviga: (v, d) => { window.location.href = riempi(v.url, d, true); },
    post: (v, d) => {
      if (v.conferma && !window.confirm(riempi(v.conferma, d, false))) return;
      const corpo = new FormData();
      for (const [k, x] of Object.entries(v.campi || {})) corpo.append(k, riempi(x, d, false));
      fetch(riempi(v.url, d, true), { method: "POST", body: corpo, credentials: "same-origin",
                                      headers: { "X-CSRFToken": csrf() } })
        .then(() => window.location.reload());
    },
    copia: (v, d) => {
      const t = riempi(v.testo, d, false);
      (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).catch(() => invia({ tipo: "copia", testo: t }));
    },
    stampa: () => invia({ tipo: "stampa" }),
    ricarica: () => window.location.reload(),
    indietro: () => window.history.back(),
    avanti: () => window.history.forward(),
    schermo_intero: () => invia({ tipo: "schermo_intero" }),
  };

  document.addEventListener("contextmenu", (e) => {
    // Sui campi di testo resta il menu standard (copia/incolla).
    if (e.target.closest("input, textarea, [contenteditable=true]")) return;
    const el = e.target.closest("[data-dm-menu]");
    const tipo = el ? el.dataset.dmMenu : null;
    const d = dati(el);
    const voci = [];
    inAttesa = {};
    const aggiungi = (elenco) => {
      for (const v of elenco || []) {
        if (v.separatore) { voci.push({ separatore: true }); continue; }
        const id = String(voci.length);
        inAttesa[id] = { v, d };
        voci.push({ id, etichetta: riempi(v.etichetta, d, false) });
      }
    };
    if (tipo) { aggiungi(menu[tipo]); if (menu.pagina) voci.push({ separatore: true }); }
    aggiungi(menu.pagina);
    if (!voci.length) return;
    e.preventDefault();
    invia({ tipo: "menu", voci, x: e.clientX, y: e.clientY });
  });

  window.DuMetodoClient = {
    esegui(id) {
      const scelta = inAttesa[id];
      inAttesa = {};
      if (scelta && azioni[scelta.v.azione]) azioni[scelta.v.azione](scelta.v, scelta.d);
    },
    ricarica_menu: carica,
  };

  carica();
})();
