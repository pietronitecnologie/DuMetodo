//! Messaggi dalla pagina (client.js servito da DuMetodo) al client.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum Messaggio {
    /// Mostra un menu nativo con queste voci; la scelta torna alla pagina con `DuMetodoClient.esegui(id)`.
    Menu {
        voci: Vec<Voce>,
        /// Logo e nome dell'azienda (Configurazione), mostrati in testa al menu.
        #[serde(default)]
        intestazione: Option<Intestazione>,
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
pub struct Intestazione {
    #[serde(default)]
    pub titolo: String,
    /// PNG in base64 per lato in pixel ("16", "24", "32").
    #[serde(default)]
    pub loghi: std::collections::HashMap<String, String>,
}

/// Lato massimo accettato per l'icona del logo (difesa: sono dati che arrivano dalla pagina).
const LATO_MASSIMO_LOGO: u32 = 64;

/// Decodifica il PNG del logo nella misura più adatta alla scala dello schermo, in RGBA.
pub fn logo_rgba(intestazione: &Intestazione, scala: f64) -> Option<(Vec<u8>, u32, u32)> {
    use base64::Engine;
    let preferito = if scala >= 1.75 {
        "32"
    } else if scala >= 1.25 {
        "24"
    } else {
        "16"
    };
    let dati = intestazione.loghi.get(preferito).or_else(|| intestazione.loghi.values().next())?;
    if dati.len() > 64 * 1024 {
        return None;
    }
    let png_bytes = base64::engine::general_purpose::STANDARD.decode(dati).ok()?;
    let mut decoder = png::Decoder::new(std::io::Cursor::new(png_bytes));
    decoder.set_transformations(png::Transformations::normalize_to_color8() | png::Transformations::ALPHA);
    let mut lettore = decoder.read_info().ok()?;
    let (w, h) = (lettore.info().width, lettore.info().height);
    if w == 0 || h == 0 || w > LATO_MASSIMO_LOGO || h > LATO_MASSIMO_LOGO {
        return None;
    }
    let mut buf = vec![0; lettore.output_buffer_size()];
    let info = lettore.next_frame(&mut buf).ok()?;
    buf.truncate(info.buffer_size());
    let rgba = match info.color_type {
        png::ColorType::Rgba => buf,
        png::ColorType::GrayscaleAlpha => buf.chunks(2).flat_map(|p| [p[0], p[0], p[0], p[1]]).collect(),
        _ => return None,
    };
    (rgba.len() == (w * h * 4) as usize).then_some((rgba, w, h))
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
        assert!(
            matches!(m, Some(Messaggio::Menu { ref voci, intestazione: None }) if voci.len() == 2 && voci[1].separatore)
        );
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
        // logo: PNG 2x2 RGBA valido; dati non validi -> nessuna icona
        let png_2x2 =
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z8DQwMDAwMDEAAUAGCUBg0aIlScAAAAASUVORK5CYII=";
        let mut loghi = std::collections::HashMap::new();
        loghi.insert("16".to_string(), png_2x2.to_string());
        let (rgba, w, h) = logo_rgba(&Intestazione { titolo: "X".into(), loghi }, 1.0).expect("png valido");
        assert_eq!((w, h, rgba.len()), (2, 2, 16));
        let mut rotti = std::collections::HashMap::new();
        rotti.insert("16".to_string(), "non-un-png".to_string());
        assert!(logo_rgba(&Intestazione { titolo: "X".into(), loghi: rotti }, 1.0).is_none());
    }
}
