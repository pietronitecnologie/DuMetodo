// Comportamenti comuni dell'interfaccia DuMetodo (senza dipendenze oltre a htmx).
(function () {
  "use strict";

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
