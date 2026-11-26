// Comportamenti comuni dell'interfaccia Metodo v2 (senza dipendenze oltre a htmx).
(function () {
  "use strict";

  // ---- Guscio: menu su telefono, modalità reparto (preferenza di questo PC).
  function salva(chiave, valore) { try { localStorage.setItem(chiave, valore); } catch (e) {} }

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-dm-apri-menu]")) {
      document.body.classList.toggle("dm-menu-aperto");
    } else if (e.target.closest("[data-dm-reparto]")) {
      salva("dm-reparto", document.documentElement.classList.toggle("dm-modo-reparto") ? "1" : "0");
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.body.classList.remove("dm-menu-aperto");
    // "/" porta alla ricerca rapida dell'ordine (se non si sta già scrivendo).
    if (e.key === "/" && !e.target.closest("input, textarea, select, [contenteditable=true]")) {
      var vai = document.getElementById("dm-vai-ordine");
      if (vai) { e.preventDefault(); vai.focus(); }
    }
  });

  // ---- Tabelle su telefono: ogni riga diventa una scheda con le etichette delle colonne.
  // Solo tabelle "semplici" (una riga di intestazione, almeno 4 colonne, senza celle unite); le griglie
  // (pianificazione, materiale per reparto) restano tabelle che scorrono. data-dm-schede="no" per escluderne una.
  function adattaTabelle(radice) {
    (radice || document).querySelectorAll(".dm-contenuto table").forEach(function (t) {
      if (t.dataset.dmSchede === "no" || !t.tHead || t.tHead.rows.length !== 1) return;
      var titoli = Array.prototype.slice.call(t.tHead.rows[0].cells);
      if (titoli.length < 4 || titoli.some(function (c) { return c.colSpan > 1 || c.rowSpan > 1; })) return;
      t.classList.add("dm-schede");
      Array.prototype.forEach.call(t.tBodies, function (corpo) {
        Array.prototype.forEach.call(corpo.rows, function (riga) {
          var i = 0;
          Array.prototype.forEach.call(riga.cells, function (cella) {
            cella.dataset.label = cella.colSpan > 1 ? "" : (titoli[i] ? titoli[i].textContent.trim() : "");
            i += cella.colSpan || 1;
          });
        });
      });
    });
  }
  // ---- Tutte le tabelle: ordinamento con un clic sull'intestazione e filtri per colonna (lente).
  // Si escludono le griglie con celle unite nell'intestazione; data-dm-ordina="no" per escluderne una.
  // Vale per le righe mostrate nella pagina (negli elenchi lunghi: quelle della pagina corrente).
  var RE_DATA = /^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?/;
  var RE_NUMERO = /^[-+]?[\d.]*\d(,\d+)?$/;

  function testoCella(cella) {
    if (!cella) return "";
    var campo = cella.querySelector("input:not([type=hidden]):not([type=checkbox]), select, textarea");
    if (campo) return (campo.tagName === "SELECT" ? (campo.options[campo.selectedIndex] || {}).text || "" : campo.value).trim();
    return (cella.innerText || cella.textContent || "").replace(/\s+/g, " ").trim();
  }
  function chiave(testo) {
    var t = testo.replace(/[€%h]|pz|ore/gi, "").trim();
    var d = t.match(RE_DATA);
    if (d) {
      var anno = d[3].length === 2 ? 2000 + Number(d[3]) : Number(d[3]);
      return { tipo: 0, v: new Date(anno, d[2] - 1, d[1], d[4] || 0, d[5] || 0).getTime() };
    }
    var n = t.replace(/\s/g, "");
    if (RE_NUMERO.test(n)) return { tipo: 0, v: parseFloat(n.replace(/\./g, "").replace(",", ".")) };
    return { tipo: 1, v: testo.toLocaleLowerCase("it") };
  }
  function confronta(a, b) {
    if (a.tipo !== b.tipo) return a.tipo - b.tipo;
    if (a.tipo === 1) return a.v.localeCompare(b.v, "it", { numeric: true });
    return a.v - b.v;
  }
  function righeDati(tabella) {
    var righe = [];
    Array.prototype.forEach.call(tabella.tBodies, function (corpo) {
      Array.prototype.forEach.call(corpo.rows, function (r) {
        if (!(r.cells.length === 1 && r.cells[0].colSpan > 1)) righe.push(r); // "Nessun risultato"
      });
    });
    return righe;
  }
  function filtra(tabella) {
    var filtri = Array.prototype.map.call(tabella.tHead.rows[0].cells, function (th) {
      var i = th.querySelector(".dm-filtro-colonna");
      return i ? i.value.trim().toLocaleLowerCase("it") : "";
    });
    var visibili = 0, totali = 0;
    righeDati(tabella).forEach(function (r) {
      totali++;
      var ok = filtri.every(function (f, i) { return !f || testoCella(r.cells[i]).toLocaleLowerCase("it").indexOf(f) !== -1; });
      r.hidden = !ok;
      if (ok) visibili++;
    });
    var attivi = filtri.some(Boolean);
    tabella.classList.toggle("dm-filtrata", attivi);
    var info = tabella.parentElement.querySelector(":scope > .dm-info-filtro");
    if (attivi) {
      if (!info) {
        info = document.createElement("div");
        info.className = "dm-info-filtro";
        tabella.parentElement.insertBefore(info, tabella.nextSibling);
      }
      info.textContent = visibili + " di " + totali + " righe";
    } else if (info) info.remove();
  }
  function ordina(tabella, indice, th) {
    var verso = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
    Array.prototype.forEach.call(tabella.tHead.rows[0].cells, function (c) { c.removeAttribute("aria-sort"); });
    th.setAttribute("aria-sort", verso === 1 ? "ascending" : "descending");
    Array.prototype.forEach.call(tabella.tBodies, function (corpo) {
      var righe = Array.prototype.filter.call(corpo.rows, function (r) { return !(r.cells.length === 1 && r.cells[0].colSpan > 1); });
      righe
        .map(function (r, pos) { return { r: r, k: chiave(testoCella(r.cells[indice])), pos: pos }; })
        .sort(function (a, b) { return verso * confronta(a.k, b.k) || a.pos - b.pos; })
        .forEach(function (x) { corpo.appendChild(x.r); });
    });
  }
  function preparaTabelle(radice) {
    (radice || document).querySelectorAll(".dm-contenuto table").forEach(function (t) {
      if (t.dataset.dmOrdinabile || t.dataset.dmOrdina === "no" || !t.tHead || t.tHead.rows.length !== 1 || !t.tBodies.length) return;
      var titoli = Array.prototype.slice.call(t.tHead.rows[0].cells);
      if (titoli.some(function (c) { return c.colSpan > 1 || c.rowSpan > 1; })) return;
      t.dataset.dmOrdinabile = "1";
      t.classList.add("dm-ordinabile");
      titoli.forEach(function (th, i) {
        if (!th.textContent.trim() || th.querySelector("input, select, button, a")) return; // colonne di azioni
        th.classList.add("dm-th-ordina");
        th.title = "Clic per ordinare";
        var lente = document.createElement("button");
        lente.type = "button";
        lente.className = "dm-lente";
        lente.title = "Filtra questa colonna";
        lente.setAttribute("aria-label", "Filtra " + th.textContent.trim());
        lente.innerHTML = '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
        var campo = document.createElement("input");
        campo.type = "search";
        campo.className = "dm-filtro-colonna";
        campo.placeholder = "Filtra…";
        campo.setAttribute("aria-label", "Filtra " + th.textContent.trim());
        th.append(lente, campo);
        th.addEventListener("click", function (e) {
          if (e.target.closest(".dm-filtro-colonna")) return;
          if (e.target.closest(".dm-lente")) {
            t.classList.add("dm-filtri-aperti");
            campo.focus();
            return;
          }
          ordina(t, i, th);
        });
        campo.addEventListener("input", function () { filtra(t); });
        campo.addEventListener("keydown", function (e) {
          if (e.key === "Escape") { campo.value = ""; filtra(t); if (!t.classList.contains("dm-filtrata")) t.classList.remove("dm-filtri-aperti"); }
          if (e.key === "Enter") e.preventDefault();
        });
      });
    });
  }

  // Menu in alto: se le voci (che dipendono dai permessi) non ci stanno, prima si tolgono le icone, poi si tengono
  // solo le icone (il nome resta nel suggerimento). Sui telefoni il menu è a tendina e non serve.
  function adattaMenu() {
    var nav = document.querySelector(".dm-menu");
    if (!nav || window.innerWidth < 768) return;
    var corpo = document.documentElement;
    corpo.classList.remove("dm-menu-senza-icone", "dm-menu-solo-icone");
    var troppo = function () { return nav.scrollWidth > nav.clientWidth + 1; };
    if (troppo()) corpo.classList.add("dm-menu-senza-icone");
    if (troppo()) { corpo.classList.remove("dm-menu-senza-icone"); corpo.classList.add("dm-menu-solo-icone"); }
  }
  var attesaMenu = null;
  window.addEventListener("resize", function () { clearTimeout(attesaMenu); attesaMenu = setTimeout(adattaMenu, 80); });
  document.addEventListener("DOMContentLoaded", adattaMenu);
  window.addEventListener("load", adattaMenu);  // dopo i font: le larghezze cambiano
  document.addEventListener("DOMContentLoaded", function () { adattaTabelle(document); preparaTabelle(document); });
  document.addEventListener("htmx:afterSettle", function (e) { adattaTabelle(e.target); preparaTabelle(e.target); });

  // ---- Selezione dell'ordine: si sceglie da un elenco, il form invia la chiave primaria.
  function scegli(box, bottone) {
    box.querySelector("[data-dm-ordine-valore]").value = bottone.dataset.dmOrdinePk;
    box.querySelector("[data-dm-ordine-etichetta]").innerHTML = bottone.querySelector(".dm-chiave-ordine").outerHTML;
    var cerca = box.querySelector("input[type=search]");
    cerca.value = "";
    cerca.required = false;
    box.querySelector(".dm-ordine-elenco").innerHTML = "";
    box.querySelector("[data-dm-ordine-ricerca]").classList.add("d-none");
    box.querySelector("[data-dm-ordine-scelto]").classList.remove("d-none");
    box.dispatchEvent(new CustomEvent("dm:ordine-scelto", { bubbles: true, detail: { pk: bottone.dataset.dmOrdinePk } }));
  }

  document.addEventListener("click", function (e) {
    // Clic fuori: chiude gli elenchi aperti.
    document.querySelectorAll("[data-dm-ordine] .dm-ordine-elenco").forEach(function (el) {
      if (!el.closest("[data-dm-ordine]").contains(e.target)) el.innerHTML = "";
    });
    var box = e.target.closest("[data-dm-ordine]");
    if (!box) return;
    var bottone = e.target.closest("[data-dm-ordine-pk]");
    if (bottone) { scegli(box, bottone); return; }
    if (e.target.closest("[data-dm-ordine-cambia]")) {
      box.querySelector("[data-dm-ordine-valore]").value = "";
      box.querySelector("[data-dm-ordine-scelto]").classList.add("d-none");
      box.querySelector("[data-dm-ordine-ricerca]").classList.remove("d-none");
      var cerca = box.querySelector("input[type=search]");
      cerca.required = box.dataset.dmOrdineObbligatorio === "1";
      cerca.focus();
    }
  });

  document.addEventListener("keydown", function (e) {
    var box = e.target.closest && e.target.closest("[data-dm-ordine]");
    if (!box || e.target.type !== "search") return;
    var voci = Array.prototype.slice.call(box.querySelectorAll("[data-dm-ordine-pk]"));
    if (e.key === "Escape") {
      box.querySelector(".dm-ordine-elenco").innerHTML = "";
    } else if (e.key === "Enter") {
      // Invio: sceglie il primo (o unico) risultato invece di inviare il form a metà.
      if (voci.length) { e.preventDefault(); scegli(box, voci[0]); }
    } else if (e.key === "ArrowDown" && voci.length) {
      e.preventDefault(); voci[0].focus();
    }
  });

  document.addEventListener("keydown", function (e) {
    var voce = e.target.closest && e.target.closest("[data-dm-ordine-pk]");
    if (!voce) return;
    if (e.key === "ArrowDown" && voce.nextElementSibling) { e.preventDefault(); voce.nextElementSibling.focus(); }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      (voce.previousElementSibling || voce.closest("[data-dm-ordine]").querySelector("input[type=search]")).focus();
    }
  });

  // Form che si inviano appena si sceglie l'ordine (es. Cambia ordine).
  document.addEventListener("dm:ordine-scelto", function (e) {
    var form = e.target.closest("form[data-dm-invia-alla-scelta]");
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  });

  document.addEventListener("input", function (e) {
    var box = e.target.closest && e.target.closest("[data-dm-ordine]");
    if (box && e.target.type === "search") box.querySelector("[data-dm-ordine-valore]").value = "";
  });
})();

// Lista ordini: clic sul segnalibro di una riga = «sono arrivato qui» (resta finché si è collegati).
document.addEventListener("click", function (ev) {
  const btn = ev.target.closest(".dm-marca");
  if (!btn) return;
  ev.preventDefault();
  const token = (document.body.getAttribute("hx-headers") || "").match(/"X-CSRFToken":\s*"([^"]+)"/);
  fetch(btn.dataset.url, { method: "POST", headers: { "X-CSRFToken": token ? token[1] : "" }, credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (dati) {
      if (!dati) return;
      document.querySelectorAll("tr.dm-marcata").forEach(function (tr) { tr.classList.remove("dm-marcata"); });
      if (dati.marcato) btn.closest("tr").classList.add("dm-marcata");
    });
});
// All'apertura (e dopo i filtri HTMX) porta in vista la riga marcata.
function dmVaiAllaMarcata() {
  const tr = document.querySelector("tr.dm-marcata");
  if (tr) tr.scrollIntoView({ block: "center" });
}
document.addEventListener("DOMContentLoaded", dmVaiAllaMarcata);
document.addEventListener("htmx:afterSettle", function (ev) {
  if (ev.target && ev.target.id === "tabella-ordini") dmVaiAllaMarcata();
});
