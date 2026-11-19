//! Aggiornamento del client: il file scaricato dal server (Amministrazione > Aggiornamenti del client)
//! viene controllato con l'impronta SHA-256 indicata dal server, poi sostituisce l'eseguibile in uso.
//!
//! L'eseguibile in uso non si può sovrascrivere ma si può rinominare (anche su Windows): si rinomina in
//! `.old`, si copia il nuovo al suo posto e lo si avvia; il `.old` viene eliminato al prossimo avvio.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// Download in corso, avviato dall'utente con "Aggiorna ora".
#[derive(Debug, Clone)]
pub struct InAttesa {
    pub versione: String,
    pub sha256: String,
    pub percorso: PathBuf,
}

thread_local! {
    /// Aggiornamento richiesto e non ancora concluso (i gestori dei download girano sul thread principale).
    pub static IN_CORSO: std::cell::RefCell<Option<InAttesa>> = const { std::cell::RefCell::new(None) };
}

/// Se `indirizzo` è il download dell'aggiornamento in corso, il percorso dove salvarlo.
pub fn destinazione(indirizzo: &str) -> Option<PathBuf> {
    if !indirizzo.contains("/client/scarica/") {
        return None;
    }
    IN_CORSO.with(|c| c.borrow().as_ref().map(|a| a.percorso.clone()))
}

/// L'aggiornamento in corso, se `percorso` è il suo file scaricato.
pub fn concluso(percorso: &Path) -> Option<InAttesa> {
    IN_CORSO.with(|c| c.borrow().as_ref().filter(|a| a.percorso == percorso).cloned())
}

/// Controlla i dati arrivati dalla pagina: versione numerica, impronta esadecimale, percorso interno fisso.
pub fn richiesta_valida(versione: &str, sha256: &str, url: &str) -> bool {
    let versione_ok =
        !versione.is_empty() && versione.len() <= 20 && versione.chars().all(|c| c.is_ascii_digit() || c == '.');
    let impronta_ok = sha256.len() == 64 && sha256.chars().all(|c| c.is_ascii_hexdigit());
    let url_ok = url.starts_with("/client/scarica/") && url.len() < 80 && !url.contains("..") && !url.contains("//");
    versione_ok && impronta_ok && url_ok
}

pub fn percorso_download(versione: &str) -> PathBuf {
    std::env::temp_dir().join(format!("dumetodo-client-{versione}.scaricato"))
}

pub fn impronta(percorso: &Path) -> std::io::Result<String> {
    let mut f = fs::File::open(percorso)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Sostituisce l'eseguibile in uso con `nuovo`. Ritorna il percorso dell'eseguibile da avviare.
pub fn installa(nuovo: &Path) -> Result<PathBuf, String> {
    let attuale = std::env::current_exe().map_err(|e| format!("percorso del programma non trovato: {e}"))?;
    let vecchio = attuale.with_extension("old");
    let _ = fs::remove_file(&vecchio);
    fs::rename(&attuale, &vecchio).map_err(|e| {
        format!(
            "non posso sostituire {} ({e}). Se il client è in una cartella protetta (es. Programmi), \
             spostalo in una cartella dell'utente.",
            attuale.display()
        )
    })?;
    if let Err(e) = fs::copy(nuovo, &attuale) {
        let _ = fs::rename(&vecchio, &attuale); // si torna alla versione di prima
        return Err(format!("copia del nuovo programma non riuscita: {e}"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&attuale, fs::Permissions::from_mode(0o755));
    }
    let _ = fs::remove_file(nuovo);
    Ok(attuale)
}

/// All'avvio: elimina l'eseguibile della versione precedente lasciato da un aggiornamento.
pub fn pulisci_versione_precedente() {
    if let Ok(attuale) = std::env::current_exe() {
        let _ = fs::remove_file(attuale.with_extension("old"));
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn richieste() {
        let h = "a".repeat(64);
        assert!(richiesta_valida("0.3.0", &h, "/client/scarica/3/"));
        assert!(!richiesta_valida("0.3.0", &h, "https://altro.it/x.exe"));
        assert!(!richiesta_valida("0.3.0", &h, "/client/scarica/../../admin/"));
        assert!(!richiesta_valida("0.3.0; rm", &h, "/client/scarica/3/"));
        assert!(!richiesta_valida("0.3.0", "zz", "/client/scarica/3/"));
    }

    #[test]
    fn impronta_sha256() {
        let p = std::env::temp_dir().join(format!("dumetodo-impronta-{}", std::process::id()));
        fs::write(&p, b"abc").unwrap();
        assert_eq!(impronta(&p).unwrap(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let _ = fs::remove_file(&p);
    }
}
