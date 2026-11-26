/* Metodo v2 – ponte con il client desktop (caricato solo se la pagina è aperta dal client).
 *
 * Clic destro: cerca l'elemento più vicino con data-dm-menu="<tipo>", compone le voci di quel tipo
 * più quelle della "pagina" (definite in Configurazione e già filtrate per i permessi dal server),
 * e chiede al client di mostrare un menu NATIVO. Alla scelta, il client chiama MetodoClient.esegui(id).
 */
(() => {
  "use strict";
  if (!window.ipc || typeof window.ipc.postMessage !== "function") return;

  let menu = {};
  let intestazione = null; // logo e nome dell'azienda in testa al menu (da Configurazione)
  let scorciatoie = []; // tasti definiti in Configurazione > Client desktop
  let inAttesa = {};
  let campoIncolla = null; // campo su cui è stato aperto il menu (per "Incolla")

  const invia = (messaggio) => window.ipc.postMessage(JSON.stringify(messaggio));

  const carica = () =>
    fetch("/client/configurazione.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : { menu: {} }))
      .then((d) => { menu = d.menu || {}; intestazione = d.intestazione || null; scorciatoie = d.scorciatoie || []; })
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
    const m = document.cookie.match(/(?:^|;\s*)metodo_csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || ""; } catch (e) { return ""; }
  };

  // ---- Tabelle -> testo separato da tabulazioni (si incolla in Excel con le colonne giuste).
  // Intestazioni: textContent, perché innerText restituirebbe il MAIUSCOLO dovuto solo allo stile.
  const pulisci = (cella) =>
    ((cella.tagName === "TH" ? cella.textContent : cella.innerText) || "").replace(/\s+/g, " ").trim();
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
  // Copia negli appunti: prima dalla pagina (funziona con qualunque versione del client), poi tramite il
  // client (che riesce anche dove il browser non dà accesso agli appunti, es. server in http).
  const copiaNellaPagina = (t) => {
    const area = document.createElement("textarea");
    area.value = t;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    return ok;
  };
  const avviso = (testo) => { if (window.__metodoAvviso) window.__metodoAvviso(testo); };
  const copiaTesto = (t) => {
    if (!t) { avviso("Niente da copiare"); return; }
    const righe = t.split("\r\n").length;
    const fatto = () => avviso(righe > 1 ? `Copiate ${righe} righe` : "Copiato");
    if (copiaNellaPagina(t)) { fatto(); return; }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(t).then(fatto, () => invia({ tipo: "copia", testo: t }));
      return;
    }
    invia({ tipo: "copia", testo: t });
  };

  const contesto = { cella: null, selezione: "", link: "" };

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
    nuova_scheda: () => invia({ tipo: "nuova_scheda" }),
    apri_in_nuova_scheda: () => { if (contesto.link) invia({ tipo: "nuova_scheda", url: contesto.link }); },
    incolla: () => {
      // Il client nuovo legge gli appunti e chiama MetodoClient.incolla; in alternativa il browser.
      if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) {
        const campo = campoIncolla;
        navigator.clipboard.readText().then(
          (t) => { campoIncolla = campo; window.MetodoClient.incolla(t); },
          () => invia({ tipo: "incolla" }));
      } else {
        invia({ tipo: "incolla" });
      }
    },
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
    if (azione === "apri_in_nuova_scheda") return !!contesto.link;
    return true;
  };

  document.addEventListener("contextmenu", (e) => {
    const campo = e.target.closest("input, textarea, [contenteditable=true]");
    campoIncolla = scrivibile(campo) ? campo : null;
    contesto.cella = e.target.closest("td, th");
    const link = e.target.closest("a[href]");
    contesto.link = link && link.origin === window.location.origin ? link.href : "";
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
    invia({ tipo: "menu", voci, intestazione, x: e.clientX, y: e.clientY });
  });

  // ---- Scorciatoie da tastiera (definite sul server)
  const combinazione = (e) => {
    const mod = [];
    if (e.ctrlKey || e.metaKey) mod.push("CTRL");
    if (e.altKey) mod.push("ALT");
    if (e.shiftKey) mod.push("SHIFT");
    let tasto = "";
    if (/^F([1-9]|1[0-2])$/.test(e.key)) tasto = e.key;
    else if (/^Key[A-Z]$/.test(e.code)) tasto = e.code.slice(3);
    else if (/^Digit[0-9]$/.test(e.code)) tasto = e.code.slice(5);
    return tasto ? [...mod, tasto].join("+") : "";
  };
  const leggibile = (t) => t.replace("CTRL", "Ctrl").replace("ALT", "Alt").replace("SHIFT", "Maiusc");
  const DEL_CLIENT = [
    ["Ctrl+T", "Nuova scheda"], ["Ctrl+W", "Chiudi scheda"], ["Ctrl+Tab", "Scheda successiva"],
    ["Ctrl+P", "Stampa"], ["F11", "Schermo intero"], ["Ctrl + / − / 0", "Zoom"], ["/", "Cerca un ordine (fuori dai campi)"],
  ];
  const aiuto = () => {
    const vecchio = document.getElementById("dm-aiuto-tasti");
    if (vecchio) { vecchio.remove(); return; }
    const velo = document.createElement("div");
    velo.id = "dm-aiuto-tasti";
    velo.style.cssText = "position:fixed;inset:0;z-index:99998;background:rgba(15,27,45,.35);display:grid;place-items:center";
    const box = document.createElement("div");
    box.style.cssText = "background:#fff;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.25);padding:18px 22px;" +
      "width:min(460px,92vw);max-height:85vh;overflow:auto;font:14px 'Segoe UI',system-ui,sans-serif;color:#18212e";
    const titolo = document.createElement("div");
    titolo.textContent = "Scorciatoie da tastiera" + (versioneInUso ? ` · Metodo v2 Client ${versioneInUso}` : "");
    titolo.style.cssText = "font-weight:650;font-size:16px;margin-bottom:10px";
    box.append(titolo);
    const riga = (tasto, testo) => {
      const r = document.createElement("div");
      r.style.cssText = "display:flex;justify-content:space-between;gap:16px;padding:5px 0;border-bottom:1px solid #eef1f5";
      const k = document.createElement("kbd");
      k.textContent = tasto;
      k.style.cssText = "background:#eef3fc;color:#2160d4;border:1px solid #c9d8f5;border-radius:5px;padding:1px 7px;font:600 12px monospace;white-space:nowrap";
      const t = document.createElement("span");
      t.textContent = testo;
      r.append(t, k);
      box.append(r);
    };
    for (const s of scorciatoie) riga(leggibile(s.tasto), s.etichetta);
    const sotto = document.createElement("div");
    sotto.textContent = "Sempre disponibili";
    sotto.style.cssText = "font-weight:600;font-size:12px;text-transform:uppercase;color:#5b6676;margin:12px 0 2px";
    box.append(sotto);
    for (const [k, t] of DEL_CLIENT) riga(k, t);
    velo.append(box);
    velo.addEventListener("click", (e) => { if (e.target === velo) velo.remove(); });
    document.body.append(velo);
  };
  const azioniTasti = {
    aiuto,
    cerca_ordine: () => {
      const campo = document.getElementById("dm-vai-ordine") || document.querySelector("input[name$='_cerca']");
      if (campo) { campo.focus(); campo.select && campo.select(); }
    },
    naviga: (v) => { window.location.href = v.url; },
    nuova_scheda: () => invia({ tipo: "nuova_scheda" }),
    ricarica: () => window.location.reload(),
    stampa: () => invia({ tipo: "stampa" }),
    indietro: () => window.history.back(),
    avanti: () => window.history.forward(),
    schermo_intero: () => invia({ tipo: "schermo_intero" }),
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { const a = document.getElementById("dm-aiuto-tasti"); if (a) { a.remove(); return; } }
    const c = combinazione(e);
    if (!c) return;
    // Nei campi di testo valgono solo F1-F12 e le combinazioni con Ctrl/Alt (le lettere si scrivono).
    const nelCampo = e.target.closest && e.target.closest("input, textarea, select, [contenteditable=true]");
    if (nelCampo && !/^F/.test(c.split("+").pop()) && !/CTRL|ALT/.test(c)) return;
    const s = scorciatoie.find((x) => x.tasto === c) || (c === "F1" ? { azione: "aiuto" } : null);
    if (!s || !azioniTasti[s.azione]) return;
    e.preventDefault();
    e.stopPropagation();
    azioniTasti[s.azione](s);
  }, true);

  // ---- Aggiornamento del client: il server propone l'ultima versione caricata in Amministrazione.
  const versioneInUso = (navigator.userAgent.match(/MetodoClient\/([\d.]+)/) || [])[1] || "";
  const numeri = (v) => String(v || "").split(".").map((x) => parseInt(x, 10) || 0);
  const piuRecente = (a, b) => {
    const x = numeri(a), y = numeri(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
    }
    return false;
  };
  const controllaAggiornamento = () => {
    if (!versioneInUso) return;
    // Il server distribuisce solo l'eseguibile Windows (i client più vecchi non indicano il sistema: sono Windows).
    const sistema = (navigator.userAgent.match(/MetodoSistema\/(\w+)/) || [])[1] || "windows";
    if (sistema !== "windows") return;
    fetch("/client/aggiornamento.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : {}))
      .then((d) => {
        const a = d && d.aggiornamento;
        if (!a || !piuRecente(a.versione, versioneInUso)) return;
        try { if (sessionStorage.getItem("dm-aggiornamento-rimandato") === a.versione) return; } catch (e) {}
        const box = document.createElement("div");
        box.id = "dm-aggiornamento";
        box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:99997;max-width:360px;background:#fff;color:#18212e;" +
          "border:1px solid #c9d8f5;border-left:4px solid #2f74f0;border-radius:10px;box-shadow:0 8px 28px rgba(16,24,40,.18);" +
          "padding:12px 14px;font:14px 'Segoe UI',system-ui,sans-serif";
        const titolo = document.createElement("div");
        titolo.style.cssText = "font-weight:650;margin-bottom:4px";
        titolo.textContent = `È disponibile Metodo v2 Client ${a.versione}`;
        const testo = document.createElement("div");
        testo.style.cssText = "font-size:13px;color:#46536a;white-space:pre-line";
        testo.textContent = `Versione in uso: ${versioneInUso}.` + (a.note ? `\n${a.note}` : "");
        const pulsanti = document.createElement("div");
        pulsanti.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:10px";
        const dopo = document.createElement("button");
        dopo.textContent = "Più tardi";
        dopo.style.cssText = "border:1px solid #c9d2df;background:#fff;border-radius:6px;padding:5px 12px;cursor:pointer";
        const ora = document.createElement("button");
        ora.textContent = "Aggiorna ora";
        ora.style.cssText = "border:0;background:#2f74f0;color:#fff;border-radius:6px;padding:5px 12px;cursor:pointer;font-weight:600";
        dopo.addEventListener("click", () => {
          try { sessionStorage.setItem("dm-aggiornamento-rimandato", a.versione); } catch (e) {}
          box.remove();
        });
        ora.addEventListener("click", () => {
          ora.disabled = true;
          ora.textContent = "Scaricamento…";
          invia({ tipo: "aggiorna_client", versione: a.versione, sha256: a.sha256, url: a.url });
        });
        pulsanti.append(dopo, ora);
        box.append(titolo, testo, pulsanti);
        document.body.append(box);
      })
      .catch(() => {});
  };

  window.MetodoClient = {
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
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", controllaAggiornamento);
  else controllaAggiornamento();
})();
