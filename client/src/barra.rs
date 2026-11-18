//! Barra delle schede: una piccola pagina locale del client, sopra le pagine di DuMetodo.
//!
//! Non contiene dati del server: riceve solo titoli e stato delle schede (`aggiorna`) e invia al client
//! le scelte dell'utente (attiva, chiudi, nuova). Gli id arrivano dal client, i titoli sono inseriti
//! come testo (mai come HTML).

use serde::{Deserialize, Serialize};

/// Altezza della barra in pixel logici.
pub const ALTEZZA: f64 = 34.0;
/// Oltre questo numero di schede "Nuova scheda" non apre altro (memoria del PC di reparto).
pub const MASSIMO_SCHEDE: usize = 16;

#[derive(Debug, Deserialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum Comando {
    Attiva {
        id: u32,
    },
    Chiudi {
        id: u32,
    },
    Nuova,
    /// Apre la schermata di collegamento per indicare un altro server.
    CambiaServer,
}

pub fn interpreta(testo: &str) -> Option<Comando> {
    if testo.len() > 1024 {
        return None;
    }
    serde_json::from_str(testo).ok()
}

#[derive(Debug, Serialize)]
pub struct VoceScheda<'a> {
    pub id: u32,
    pub titolo: &'a str,
    pub attiva: bool,
}

/// Script che ridisegna la barra con lo stato attuale; `server` = server collegato (host[:porta]), vuoto se nessuno.
pub fn script_aggiorna(voci: &[VoceScheda<'_>], server: &str) -> String {
    let json = serde_json::to_string(voci).unwrap_or_else(|_| "[]".into());
    let server = serde_json::to_string(server).unwrap_or_else(|_| "\"\"".into());
    format!("window.aggiorna && window.aggiorna({json}, {server})")
}

/// Pagina della barra con l'icona di DuMetodo (PNG 32 px incorporato).
pub fn pagina() -> String {
    use base64::Engine;
    let icona = base64::engine::general_purpose::STANDARD.encode(include_bytes!("../../static/img/dumetodo-32.png"));
    MODELLO.replace("{ICONA}", &icona)
}

const MODELLO: &str = r##"<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Schede</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: #e9edf3; user-select: none;
    font: 13px "Segoe UI", system-ui, sans-serif; }
  #icona { width: 20px; height: 20px; margin: 0 6px 6px 4px; flex: 0 0 20px; align-self: center; }
  #schede { display: flex; align-items: flex-end; height: 100%; padding: 4px 6px 0; gap: 2px; }
  .scheda { display: flex; align-items: center; gap: 6px; min-width: 0; flex: 0 1 220px; height: 30px;
    padding: 0 6px 0 12px; border-radius: 8px 8px 0 0; color: #4a5567; background: #dde3ec; cursor: default;
    border: 1px solid #c9d2df; border-bottom: 0; }
  .scheda:hover { background: #eef2f7; color: #18212e; }
  .scheda.attiva { background: #ffffff; color: #18212e; border-color: #c9d2df; box-shadow: inset 0 2px 0 #2f74f0; }
  .titolo { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chiudi { width: 20px; height: 20px; flex: 0 0 20px; border: 0; border-radius: 5px; background: none; color: inherit;
    font-size: 15px; line-height: 1; opacity: .65; }
  .chiudi:hover { background: #d3dbe6; opacity: 1; }
  #nuova { width: 28px; height: 26px; margin: 0 0 2px 4px; border: 0; border-radius: 6px; background: none; color: #5b6676;
    font-size: 20px; line-height: 1; }
  #nuova:hover { background: #dde3ec; color: #18212e; }
  #server { margin: 0 2px 4px auto; align-self: center; display: flex; align-items: center; gap: 6px; max-width: 280px;
    height: 24px; padding: 0 10px; border: 1px solid #c9d2df; border-radius: 6px; background: #fff; color: #4a5567;
    font: inherit; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #server:hover { background: #eef3fc; color: #18212e; border-color: #9fbdf3; }
  #server .punto { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #3ccf7a; }
  #server.scollegato .punto { background: #e0a030; }
</style></head>
<body><div id="schede"><img id="icona" alt="" src="data:image/png;base64,{ICONA}"></div>
<script>
(() => {
  const invia = (m) => window.ipc.postMessage(JSON.stringify(m));
  const contenitore = document.getElementById("schede");
  let ultimo = "";
  // Ridisegna solo se qualcosa è cambiato: ridisegnare durante un clic farebbe perdere il clic.
  window.aggiorna = (schede, server) => {
    const firma = JSON.stringify([schede, server]);
    if (firma === ultimo) return;
    ultimo = firma;
    contenitore.querySelectorAll(".scheda, #nuova, #server").forEach((el) => el.remove());
    for (const s of schede) {
      const d = document.createElement("div");
      d.className = "scheda" + (s.attiva ? " attiva" : "");
      d.dataset.id = s.id;
      d.title = s.titolo;
      const t = document.createElement("span");
      t.className = "titolo";
      t.textContent = s.titolo || "Nuova scheda";
      const x = document.createElement("button");
      x.className = "chiudi";
      x.type = "button";
      x.textContent = "×";
      x.title = "Chiudi scheda (Ctrl+W)";
      d.append(t, x);
      contenitore.append(d);
    }
    const n = document.createElement("button");
    n.id = "nuova";
    n.type = "button";
    n.textContent = "+";
    n.title = "Nuova scheda (Ctrl+T)";
    contenitore.append(n);
    const sv = document.createElement("button");
    sv.id = "server";
    sv.type = "button";
    sv.className = server ? "" : "scollegato";
    sv.title = "Cambia server… (le altre schede vengono chiuse)";
    const punto = document.createElement("span");
    punto.className = "punto";
    const testo = document.createElement("span");
    testo.textContent = server ? "Server: " + server : "Nessun server collegato";
    sv.append(punto, testo);
    contenitore.append(sv);
  };
  // Un solo gestore per tutta la barra, sul clic completo (premi e rilascia).
  contenitore.addEventListener("click", (e) => {
    if (e.target.closest("#nuova")) { invia({ tipo: "nuova" }); return; }
    if (e.target.closest("#server")) { invia({ tipo: "cambia_server" }); return; }
    const scheda = e.target.closest(".scheda");
    if (!scheda) return;
    const id = Number(scheda.dataset.id);
    if (e.target.closest(".chiudi")) invia({ tipo: "chiudi", id });
    else if (!scheda.classList.contains("attiva")) invia({ tipo: "attiva", id });
  });
  contenitore.addEventListener("auxclick", (e) => {
    const scheda = e.target.closest(".scheda");
    if (scheda && e.button === 1) invia({ tipo: "chiudi", id: Number(scheda.dataset.id) });
  });
  contenitore.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); });
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("dblclick", (e) => { if (e.target === contenitore) invia({ tipo: "nuova" }); });
})();
</script></body></html>"##;

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn comandi_e_aggiornamento() {
        assert!(matches!(interpreta(r#"{"tipo":"attiva","id":3}"#), Some(Comando::Attiva { id: 3 })));
        assert!(matches!(interpreta(r#"{"tipo":"nuova"}"#), Some(Comando::Nuova)));
        assert!(matches!(interpreta(r#"{"tipo":"cambia_server"}"#), Some(Comando::CambiaServer)));
        assert!(interpreta(r#"{"tipo":"esegui","cmd":"x"}"#).is_none());
        let s = script_aggiorna(&[VoceScheda { id: 1, titolo: "<b>x</b>\"", attiva: true }], "srv:8000");
        assert!(s.starts_with("window.aggiorna") && s.contains(r#""titolo":"<b>x</b>\"""#));
    }
}
