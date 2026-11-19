//! Configurazione LOCALE del client (per PC): indirizzo del server, chiosco, zoom e cartelle di download.
//! I menu e le azioni NON stanno qui: li definisce il server (Configurazione > Client desktop).

use std::fs;
use std::path::{Path, PathBuf};

use directories::ProjectDirs;
use serde::Deserialize;

pub const MODELLO: &str = r#"# Configurazione del client DuMetodo (questo PC).
# Indirizzo del server DuMetodo, ad esempio: url = "https://dumetodo.esempio.it"
url = ""

# Modalità chiosco per le postazioni di reparto: schermo intero, senza strumenti di sviluppo.
schermo_intero = false

# Zoom iniziale della pagina (1.0 = 100%).
zoom = 1.0

# Riapre le schede che erano aperte alla chiusura del client (sullo stesso server).
ripristina_schede = true

[download]
# Cartella dei file scaricati (vuoto = cartella Download dell'utente).
cartella = ""

# Regole: se l'indirizzo del file contiene il testo indicato, il file va nella cartella indicata.
# Esempio: la bolla generata alla consegna finisce direttamente nella cartella del gestionale.
# [[download.regole]]
# contiene = "/consegne/"
# cartella = 'C:\ReadyPro\Import'
"#;

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
pub struct Configurazione {
    pub url: String,
    pub schermo_intero: bool,
    pub zoom: Option<f64>,
    pub strumenti_sviluppatore: bool,
    /// None = sì (predefinito).
    pub ripristina_schede: Option<bool>,
    pub download: Download,
}

#[derive(Debug, Deserialize, Default, Clone)]
#[serde(default)]
pub struct Download {
    pub cartella: String,
    pub regole: Vec<Regola>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Regola {
    pub contiene: String,
    pub cartella: PathBuf,
}

pub fn percorso_predefinito() -> Option<PathBuf> {
    ProjectDirs::from("it", "DuMetodo", "DuMetodo Client").map(|d| d.config_dir().join("client.toml"))
}

/// Legge la configurazione; se il file non esiste ne crea uno di esempio da compilare.
pub fn carica(percorso: &Path) -> Result<Configurazione, String> {
    if !percorso.exists() {
        if let Some(cartella) = percorso.parent() {
            let _ = fs::create_dir_all(cartella);
        }
        let _ = fs::write(percorso, MODELLO);
    }
    let testo = fs::read_to_string(percorso).map_err(|e| format!("Impossibile leggere {}: {e}", percorso.display()))?;
    toml::from_str(&testo).map_err(|e| format!("Errore in {}: {e}", percorso.display()))
}

impl Download {
    /// Cartella di destinazione per un file scaricato da `url` (None = lascia decidere al sistema).
    pub fn cartella_per(&self, url: &str) -> Option<PathBuf> {
        if let Some(r) = self.regole.iter().find(|r| !r.contiene.is_empty() && url.contains(&r.contiene)) {
            return Some(r.cartella.clone());
        }
        (!self.cartella.trim().is_empty()).then(|| PathBuf::from(self.cartella.trim()))
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn modello_valido_e_regole() {
        let c: Configurazione = toml::from_str(MODELLO).unwrap();
        assert!(c.url.is_empty() && !c.schermo_intero);
        let c: Configurazione = toml::from_str(
            "url = \"https://x.it\"\n[download]\ncartella = \"/tmp/scaricati\"\n\
             [[download.regole]]\ncontiene = \"/consegne/\"\ncartella = \"/gestionale/import\"\n",
        )
        .unwrap();
        assert_eq!(c.download.cartella_per("https://x.it/consegne/3/file/"), Some(PathBuf::from("/gestionale/import")));
        assert_eq!(c.download.cartella_per("https://x.it/report/x.xlsx"), Some(PathBuf::from("/tmp/scaricati")));
    }
}
