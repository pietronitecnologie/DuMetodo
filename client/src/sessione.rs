//! Schede aperte alla chiusura del client, riaperte al prossimo avvio (sullo stesso server).
//!
//! Il file `schede.json` sta accanto a `client.toml`. Si salvano solo indirizzi del server collegato:
//! mai pagine locali del client o siti esterni.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

pub const MASSIMO_SCHEDE: usize = 16;

#[derive(Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct Sessione {
    /// Origine del server (es. "https://metodo.azienda.it").
    pub server: String,
    pub schede: Vec<String>,
    pub attiva: usize,
}

pub fn percorso(percorso_conf: &Path) -> PathBuf {
    percorso_conf.with_file_name("schede.json")
}

pub fn salva(percorso: &Path, sessione: &Sessione) {
    if let Ok(testo) = serde_json::to_string_pretty(sessione) {
        let temporaneo = percorso.with_extension("json.tmp");
        // Scrittura in un file temporaneo e poi rinomina: un'interruzione non lascia un file a metà.
        if std::fs::write(&temporaneo, testo).is_ok() {
            let _ = std::fs::rename(&temporaneo, percorso);
        }
    }
}

/// Sessione salvata per questo server (indirizzi dello stesso server, al massimo 16), se c'è.
pub fn carica(percorso: &Path, server: &str) -> Option<Sessione> {
    let testo = std::fs::read_to_string(percorso).ok()?;
    let mut s: Sessione = serde_json::from_str(&testo).ok()?;
    if s.server != server {
        return None;
    }
    s.schede.retain(|u| u.starts_with(&format!("{server}/")) || u == server);
    s.schede.truncate(MASSIMO_SCHEDE);
    if s.schede.is_empty() {
        return None;
    }
    s.attiva = s.attiva.min(s.schede.len() - 1);
    Some(s)
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn salva_e_ricarica_solo_lo_stesso_server() {
        let cartella = std::env::temp_dir().join(format!("metodo-sessione-{}", std::process::id()));
        std::fs::create_dir_all(&cartella).unwrap();
        let p = cartella.join("schede.json");
        let s = Sessione {
            server: "http://srv:8000".into(),
            schede: vec![
                "http://srv:8000/ordini/".into(),
                "https://altro.it/x".into(),
                "http://srv:8000/consegne/".into(),
            ],
            attiva: 5,
        };
        salva(&p, &s);
        let r = carica(&p, "http://srv:8000").expect("sessione");
        assert_eq!(r.schede, vec!["http://srv:8000/ordini/", "http://srv:8000/consegne/"]);
        assert_eq!(r.attiva, 1);
        assert!(carica(&p, "http://altro:8000").is_none());
        let _ = std::fs::remove_dir_all(&cartella);
    }
}
