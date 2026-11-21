// Modulo di tracciatura: le foto (campi con data-foto) vengono ridotte nel browser prima dell'invio
// (lato lungo massimo 1600 px, JPEG 0,85), così dal telefono si inviano pochi KB invece di foto da 5 MB.
// I documenti e gli altri campi partono così come sono; tutto in un'unica richiesta.
(function () {
  var LATO = 1600;
  function riduci(file) {
    return new Promise(function (ok) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { ok(file); return; }
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var r = Math.min(1, LATO / Math.max(img.width, img.height));
        if (r === 1 && file.type === "image/jpeg") { URL.revokeObjectURL(url); ok(file); return; }
        var tela = document.createElement("canvas");
        tela.width = Math.round(img.width * r); tela.height = Math.round(img.height * r);
        var ctx = tela.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, tela.width, tela.height);
        ctx.drawImage(img, 0, 0, tela.width, tela.height); URL.revokeObjectURL(url);
        tela.toBlob(function (b) { ok(b ? new File([b], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" }) : file); }, "image/jpeg", 0.85);
      };
      img.onerror = function () { URL.revokeObjectURL(url); ok(file); };
      img.src = url;
    });
  }
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.matches || !form.matches("form[data-tracciatura]") || !window.fetch || !window.FormData) return;
    var foto = Array.prototype.filter.call(form.querySelectorAll("input[type=file][data-foto]"), function (i) { return i.files.length; });
    if (!foto.length) return;
    ev.preventDefault();
    var dati = new FormData(form, ev.submitter || undefined);
    var bottoni = form.querySelectorAll("button"); bottoni.forEach(function (b) { b.disabled = true; });
    Promise.all(foto.map(function (i) { return riduci(i.files[0]).then(function (f) { dati.set(i.name, f, f.name); }); }))
      .then(function () { return fetch(form.action || location.href, { method: "POST", body: dati, credentials: "same-origin" }); })
      .then(function (r) { return r.redirected ? (location.href = r.url) : r.text().then(function (html) { document.open(); document.write(html); document.close(); }); })
      .catch(function () { alert("Invio non riuscito. Controlla la connessione e riprova."); bottoni.forEach(function (b) { b.disabled = false; }); });
  });
})();
