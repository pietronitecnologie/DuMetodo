// Disegna i codici a barre Code128 (<svg class="barcode" jsbarcode-value="...">) con JsBarcode.
document.addEventListener("DOMContentLoaded", function () {
  if (window.JsBarcode && document.querySelector(".barcode")) {
    JsBarcode(".barcode").init();
  }
  var stampa = document.getElementById("stampa-etichetta");
  if (stampa) {
    stampa.addEventListener("click", function () { window.print(); });
  }
});
