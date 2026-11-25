//! Stato del pannello chat nel client (la chat vera è una pagina del server: /chat/).

use std::cell::Cell;

/// Larghezza del pannello in pixel logici.
pub const LARGHEZZA: f64 = 340.0;

thread_local! {
    pub static VISIBILE: Cell<bool> = const { Cell::new(false) };
    pub static NON_LETTI: Cell<u32> = const { Cell::new(0) };
    /// Conversazione da aprire quando si mostra il pannello (ultima notificata).
    pub static DA_APRIRE: Cell<Option<u64>> = const { Cell::new(None) };
}

pub fn visibile() -> bool {
    VISIBILE.with(Cell::get)
}

pub fn non_letti() -> u32 {
    NON_LETTI.with(Cell::get)
}

/// Notifica di Windows (o del desktop su Linux). Se il sistema non la supporta, si ignora.
pub fn notifica_di_sistema(nome: &str, testo: &str) {
    let corpo: String = testo.chars().take(180).collect();
    let _ = notify_rust::Notification::new()
        .appname("Metodo v2")
        .summary(&format!("Metodo v2 · messaggio da {nome}"))
        .body(&corpo)
        .show();
}
