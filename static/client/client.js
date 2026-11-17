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
  let campoIncolla = null; // campo su cui è stato aperto il menu (per "Incolla")

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

  // ---- Tabelle -> testo separato da tabulazioni (si incolla in Excel con le colonne giuste).
  const pulisci = (cella) => (cella.innerText || "").replace(/[\t\r\n]+/g, " ").trim();
  const righeVisibili = (tabella) =>
    Array.from(tabella.rows).filter((r) => r.offsetParent !== null && r.closest("table") === tabella);
  // Indice "visivo" della colonna, contando le celle unite (colspan).
  const colonnaDi = (cella) => {
    let i = 0;
    for (const c of cella.parentElement.cells) { if (c === cella) return i; i += c.colSpan || 1; }
    return -1;
  };
  const cellaInColonna = (riga, indice) => {
    let i = 0;
    for (const c of riga.cells) { const n = c.colSpan || 1; if (indice < i + n) return c; i += n; }
    return null;
  };
  const tsv = (righe) => righe.map((r) => r.join("\t")).join("\r\n");
  const copiaTesto = (t) => { if (t) invia({ tipo: "copia", testo: t }); };

  const contesto = { cella: null, selezione: "" };

  const azioni = {
    copia_tabella: () => {
      const tabella = contesto.cella && contesto.cella.closest("table");
      if (tabella) copiaTesto(tsv(righeVisibili(tabella).map((r) => Array.from(r.cells).map(pulisci))));
    },
    copia_colonna: () => {
      const c = contesto.cella;
      if (!c) return;
      const indice = colonnaDi(c);
      const righe = righeVisibili(c.closest("table"))
        .map((r) => cellaInColonna(r, indice)).filter(Boolean).map((x) => [pulisci(x)]);
      copiaTesto(tsv(righe));
    },
    copia_riga: () => {
      const c = contesto.cella;
      if (c) copiaTesto(tsv([Array.from(c.parentElement.cells).map(pulisci)]));
    },
    copia_selezione: () => copiaTesto(contesto.selezione),
    incolla: () => invia({ tipo: "incolla" }),
    naviga: (v, d) => { window.location.href = riempi(v.url, d, true); },
    post: (v, d) => {
      if (v.conferma && !window.confirm(riempi(v.conferma, d, false))) return;
      const corpo = new FormData();
      for (const [k, x] of Object.entries(v.campi || {})) corpo.append(k, riempi(x, d, false));
      fetch(riempi(v.url, d, true), { method: "POST", body: corpo, credentials: "same-origin",
                                      headers: { "X-CSRFToken": csrf() } })
        .then(() => window.location.reload());
    },
    copia: (v, d) => copiaTesto(riempi(v.testo, d, false)),
    stampa: () => invia({ tipo: "stampa" }),
    ricarica: () => window.location.reload(),
    indietro: () => window.history.back(),
    avanti: () => window.history.forward(),
    schermo_intero: () => invia({ tipo: "schermo_intero" }),
  };

  const scrivibile = (el) =>
    el && !el.disabled && !el.readOnly &&
    (el.matches("textarea, [contenteditable=true]") ||
     (el.matches("input") && /^(text|search|number|email|tel|url|password|)$/.test(el.type)));

  // Le voci che hanno senso solo in un certo punto della pagina.
  const disponibile = (azione) => {
    if (["copia_tabella", "copia_colonna", "copia_riga"].includes(azione)) return !!contesto.cella;
    if (azione === "copia_selezione") return !!contesto.selezione;
    if (azione === "incolla") return !!campoIncolla;
    return true;
  };

  document.addEventListener("contextmenu", (e) => {
    const campo = e.target.closest("input, textarea, [contenteditable=true]");
    campoIncolla = scrivibile(campo) ? campo : null;
    contesto.cella = e.target.closest("td, th");
    if (campo && typeof campo.selectionStart === "number" && campo.type !== "password") {
      contesto.selezione = campo.value.slice(campo.selectionStart, campo.selectionEnd);
    } else {
      contesto.selezione = String(window.getSelection() || "");
    }
    const el = e.target.closest("[data-dm-menu]");
    const tipo = el ? el.dataset.dmMenu : null;
    const d = dati(el);
    const voci = [];
    inAttesa = {};
    const aggiungi = (elenco) => {
      for (const v of elenco || []) {
        if (v.separatore) {
          // Niente separatori doppi o in testa (le voci non disponibili vengono saltate).
          if (voci.length && !voci[voci.length - 1].separatore) voci.push({ separatore: true });
          continue;
        }
        if (!disponibile(v.azione)) continue;
        const id = String(voci.length);
        inAttesa[id] = { v, d };
        voci.push({ id, etichetta: riempi(v.etichetta, d, false) });
      }
    };
    if (tipo) { aggiungi(menu[tipo]); if (menu.pagina) voci.push({ separatore: true }); }
    aggiungi(menu.pagina);
    while (voci.length && voci[voci.length - 1].separatore) voci.pop();
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
    // Chiamata dal client con il testo degli appunti: lo inserisce nel campo al posto della selezione.
    incolla(testo) {
      const c = campoIncolla;
      campoIncolla = null;
      if (!c || !document.contains(c)) return;
      c.focus();
      if (typeof c.selectionStart === "number") {
        const ini = c.selectionStart, fine = c.selectionEnd;
        c.setRangeText(testo, ini, fine, "end");
      } else {
        document.execCommand("insertText", false, testo);
      }
      c.dispatchEvent(new Event("input", { bubbles: true }));
    },
  };

  carica();
})();
