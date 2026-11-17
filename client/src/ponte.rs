//! Messaggi dalla pagina (client.js servito da DuMetodo) al client.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum Messaggio {
    /// Mostra un menu nativo con queste voci; la scelta torna alla pagina con `DuMetodoClient.esegui(id)`.
    Menu {
        voci: Vec<Voce>,
    },
    Stampa,
    SchermoIntero,
    /// Copia negli appunti del sistema (funziona anche su http, dove navigator.clipboard non c'è).
    Copia {
        testo: String,
    },
    /// Legge gli appunti e li passa alla pagina con `DuMetodoClient.incolla(testo)`.
    Incolla,
    /// Apre una nuova scheda (senza indirizzo: la pagina iniziale del server).
    NuovaScheda {
        #[serde(default)]
        url: Option<String>,
    },
    ChiudiScheda,
    /// Passa alla scheda successiva (+1) o precedente (-1).
    CambiaScheda {
        passo: i32,
    },
    Zoom {
        passo: f64,
    },
    /// Solo dalle pagine locali del client: indirizzo del server scritto dall'utente.
    ImpostaServer {
        url: String,
    },
    Riprova,
    CambiaServer,
}

#[derive(Debug, Deserialize)]
pub struct Voce {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub etichetta: String,
    #[serde(default)]
    pub separatore: bool,
}

pub const MASSIMO_VOCI: usize = 60;
/// Limite del testo copiato o incollato (una tabella grande resta ben sotto).
pub const MASSIMO_APPUNTI: usize = 4 * 1024 * 1024;

pub fn interpreta(testo: &str) -> Option<Messaggio> {
    if testo.len() > MASSIMO_APPUNTI + 1024 {
        return None;
    }
    serde_json::from_str(testo).ok()
}

/// Stringa JavaScript sicura (JSON) da inserire in uno script.
pub fn js_stringa(testo: &str) -> String {
    serde_json::to_string(testo).unwrap_or_else(|_| "\"\"".into())
}

/// Script iniettato in ogni pagina del server: scorciatoie da tastiera e piccoli avvisi del client.
/// Non contiene interfaccia: la grafica resta quella del server.
pub const SCRIPT_INIZIALE: &str = r#"
(() => {
  const invia = (m) => window.ipc && window.ipc.postMessage(JSON.stringify(m));
  document.addEventListener("keydown", (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.key === "F11") { e.preventDefault(); invia({ tipo: "schermo_intero" }); }
    else if (ctrl && e.key.toLowerCase() === "p") { e.preventDefault(); invia({ tipo: "stampa" }); }
    else if (ctrl && (e.key === "+" || e.key === "=")) { e.preventDefault(); invia({ tipo: "zoom", passo: 0.1 }); }
    else if (ctrl && e.key === "-") { e.preventDefault(); invia({ tipo: "zoom", passo: -0.1 }); }
    else if (ctrl && e.key === "0") { e.preventDefault(); invia({ tipo: "zoom", passo: 0 }); }
    else if (ctrl && !e.shiftKey && e.key.toLowerCase() === "t") { e.preventDefault(); invia({ tipo: "nuova_scheda" }); }
    else if (ctrl && e.key.toLowerCase() === "w") { e.preventDefault(); invia({ tipo: "chiudi_scheda" }); }
    else if (ctrl && (e.key === "Tab" || e.key === "PageDown" || e.key === "PageUp")) {
      e.preventDefault();
      invia({ tipo: "cambia_scheda", passo: (e.key === "PageUp" || (e.key === "Tab" && e.shiftKey)) ? -1 : 1 });
    }
    else if (e.altKey && e.key === "ArrowLeft") { history.back(); }
    else if (e.altKey && e.key === "ArrowRight") { history.forward(); }
  }, true);
  window.__duMetodoAvviso = (testo) => {
    const d = document.createElement("div");
    d.textContent = testo;
    d.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:99999;background:#212529;color:#fff;" +
      "padding:10px 14px;border-radius:6px;font:14px system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.3)";
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 5000);
  };
})();
"#;

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn messaggi() {
        let m = interpreta(r#"{"tipo":"menu","voci":[{"id":"0","etichetta":"Apri"},{"separatore":true}],"x":1}"#);
        assert!(matches!(m, Some(Messaggio::Menu { ref voci }) if voci.len() == 2 && voci[1].separatore));
        assert!(matches!(interpreta(r#"{"tipo":"stampa"}"#), Some(Messaggio::Stampa)));
        assert!(interpreta(r#"{"tipo":"esegui_comando","cmd":"rm"}"#).is_none());
        assert!(
            matches!(interpreta(r#"{"tipo":"copia","testo":"a\tb"}"#), Some(Messaggio::Copia { ref testo }) if testo == "a\tb")
        );
        assert!(matches!(interpreta(r#"{"tipo":"incolla"}"#), Some(Messaggio::Incolla)));
        assert!(matches!(interpreta(r#"{"tipo":"nuova_scheda"}"#), Some(Messaggio::NuovaScheda { url: None })));
        assert!(matches!(
            interpreta(r#"{"tipo":"cambia_scheda","passo":-1}"#),
            Some(Messaggio::CambiaScheda { passo: -1 })
        ));
        assert_eq!(js_stringa("a\"</script>"), "\"a\\\"</script>\"");
    }
}
