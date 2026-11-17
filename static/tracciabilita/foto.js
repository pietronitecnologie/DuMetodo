// Caricamento foto con ridimensionamento nel browser (come in Metodo: lato lungo max 1024 px,
// JPEG qualità 0,8), così dal telefono si inviano pochi KB invece di foto da 5 MB.
// Vale per i moduli con l'attributo data-ridimensiona; i PDF e le GIF partono così come sono.
// Ogni immagine usa una sua tela e tutte partono in un'unica richiesta (in Metodo due caricamenti
// paralleli condividevano la stessa tela e il redirect poteva interrompere il primo).
(function () {
  var LATO_MASSIMO = 1024;

  function ridimensiona(file) {
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { resolve(file); return; }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var rapporto = Math.min(1, LATO_MASSIMO / Math.max(img.width, img.height));
        if (rapporto === 1 && file.type === "image/jpeg") { URL.revokeObjectURL(url); resolve(file); return; }
        var tela = document.createElement("canvas");
        tela.width = Math.round(img.width * rapporto);
        tela.height = Math.round(img.height * rapporto);
        var ctx = tela.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, tela.width, tela.height);
        ctx.drawImage(img, 0, 0, tela.width, tela.height);
        URL.revokeObjectURL(url);
        tela.toBlob(function (blob) {
          if (!blob) { resolve(file); return; }
          var nome = file.name.replace(/\.[^.]+$/, "") + ".jpg";
          resolve(new File([blob], nome, { type: "image/jpeg" }));
        }, "image/jpeg", 0.8);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.matches || !form.matches("form[data-ridimensiona]") || !window.fetch || !window.FormData) return;
    var dati = new FormData(form);
    var files = dati.getAll("file").filter(function (f) { return f && f.size; });
    if (!files.length) return; // il server risponderà "nessun file"
    ev.preventDefault();
    var bottone = form.querySelector("button[type=submit], button:not([type])");
    if (bottone) { bottone.disabled = true; bottone.dataset.testo = bottone.textContent; bottone.textContent = "Caricamento…"; }
    dati.delete("file");
    Promise.all(files.map(ridimensiona))
      .then(function (lista) {
        lista.forEach(function (f) { dati.append("file", f, f.name); });
        return fetch(form.action || window.location.href, { method: "POST", body: dati, credentials: "same-origin" });
      })
      .then(function (risposta) { window.location.href = risposta.url; })
      .catch(function () {
        alert("Caricamento non riuscito. Controlla la connessione e riprova.");
        if (bottone) { bottone.disabled = false; bottone.textContent = bottone.dataset.testo; }
      });
  });
})();
