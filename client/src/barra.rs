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
    Attiva { id: u32 },
    Chiudi { id: u32 },
    Nuova,
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

/// Script che ridisegna la barra con lo stato attuale.
pub fn script_aggiorna(voci: &[VoceScheda<'_>]) -> String {
    let json = serde_json::to_string(voci).unwrap_or_else(|_| "[]".into());
    format!("window.aggiorna && window.aggiorna({json})")
}

pub const PAGINA: &str = r#"<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Schede</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: #08111e; user-select: none;
    font: 13px "Segoe UI", system-ui, sans-serif; }
  #schede { display: flex; align-items: flex-end; height: 100%; padding: 4px 6px 0; gap: 2px; }
  .scheda { display: flex; align-items: center; gap: 6px; min-width: 0; flex: 0 1 220px; height: 30px;
    padding: 0 6px 0 12px; border-radius: 8px 8px 0 0; color: #93a1b8; background: #0c1828; cursor: default; }
  .scheda:hover { background: #13223a; color: #dbe3ef; }
  .scheda.attiva { background: #0f1b2d; color: #fff; }
  .titolo { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chiudi { width: 20px; height: 20px; flex: 0 0 20px; border: 0; border-radius: 5px; background: none; color: inherit;
    font-size: 15px; line-height: 1; opacity: .65; }
  .chiudi:hover { background: #24364f; opacity: 1; }
  #nuova { width: 28px; height: 26px; margin: 0 0 2px 4px; border: 0; border-radius: 6px; background: none; color: #93a1b8;
    font-size: 20px; line-height: 1; }
  #nuova:hover { background: #13223a; color: #fff; }
</style></head>
<body><div id="schede"></div>
<script>
(() => {
  const invia = (m) => window.ipc.postMessage(JSON.stringify(m));
  const contenitore = document.getElementById("schede");
  window.aggiorna = (schede) => {
    contenitore.textContent = "";
    for (const s of schede) {
      const d = document.createElement("div");
      d.className = "scheda" + (s.attiva ? " attiva" : "");
      d.title = s.titolo;
      const t = document.createElement("span");
      t.className = "titolo";
      t.textContent = s.titolo || "Nuova scheda";
      const x = document.createElement("button");
      x.className = "chiudi";
      x.textContent = "×";
      x.title = "Chiudi scheda (Ctrl+W)";
      x.addEventListener("click", (e) => { e.stopPropagation(); invia({ tipo: "chiudi", id: s.id }); });
      d.addEventListener("mousedown", (e) => { if (e.button === 0) invia({ tipo: "attiva", id: s.id }); });
      d.addEventListener("auxclick", (e) => { if (e.button === 1) invia({ tipo: "chiudi", id: s.id }); });
      d.append(t, x);
      contenitore.append(d);
    }
    const n = document.createElement("button");
    n.id = "nuova";
    n.textContent = "+";
    n.title = "Nuova scheda (Ctrl+T)";
    n.addEventListener("click", () => invia({ tipo: "nuova" }));
    contenitore.append(n);
  };
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("dblclick", (e) => { if (e.target === contenitore) invia({ tipo: "nuova" }); });
})();
</script></body></html>"#;

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn comandi_e_aggiornamento() {
        assert!(matches!(interpreta(r#"{"tipo":"attiva","id":3}"#), Some(Comando::Attiva { id: 3 })));
        assert!(matches!(interpreta(r#"{"tipo":"nuova"}"#), Some(Comando::Nuova)));
        assert!(interpreta(r#"{"tipo":"esegui","cmd":"x"}"#).is_none());
        let s = script_aggiorna(&[VoceScheda { id: 1, titolo: "<b>x</b>\"", attiva: true }]);
        assert!(s.starts_with("window.aggiorna") && s.contains(r#""titolo":"<b>x</b>\"""#));
    }
}
