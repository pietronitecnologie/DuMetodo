// Test display: inserimento veloce con lettore di codici a barre (comportamento di Metodo).
// Il lettore "scrive" nel campo Sito il testo "sito$$articolo": lo si divide, l'esito diventa
// Positivo e, se prodotto, FW e lotto sono già compilati, il modulo parte da solo.
document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("form-display");
  if (!form) return;
  var sito = document.getElementById("sito");
  var articolo = document.getElementById("articolo");
  var esito = document.getElementById("esito");
  var fw = document.getElementById("fw");
  var lotto = document.getElementById("lotto");
  var prodotto = document.getElementById("prodotto");
  var inviato = false;

  [sito, articolo, fw, lotto].forEach(function (campo) {
    campo.addEventListener("focus", function () { this.select(); });
  });

  sito.addEventListener("input", function () {
    var valore = sito.value;
    if (valore.indexOf("$$") === -1) return;
    var parti = valore.split("$$");
    if (!parti[0] || !parti[1]) return;
    sito.value = parti[0];
    articolo.value = parti.slice(1).join("$$");
    esito.value = "POS";
    fw.focus();
    // Piccola attesa: il lettore potrebbe non aver finito di "digitare".
    setTimeout(function () {
      if (!inviato && prodotto.value && fw.value.trim() && lotto.value.trim()) {
        inviato = true;
        form.submit();
      }
    }, 100);
  });

  sito.focus();
});
