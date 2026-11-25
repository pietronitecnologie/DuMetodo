//! Regole di navigazione: il client mostra SOLO il server configurato.

use url::Url;

pub enum Destinazione {
    /// Stesso server: si naviga nel client.
    Interna,
    /// Sito esterno (http/https/mailto): si apre nel browser di sistema.
    Esterna,
    /// Tutto il resto (file:, javascript:, schemi sconosciuti): bloccato.
    Bloccata,
}

pub fn classifica(origine: &Url, indirizzo: &str) -> Destinazione {
    if indirizzo.starts_with("about:blank") || indirizzo.starts_with("data:text/html") {
        return Destinazione::Interna;
    }
    match Url::parse(indirizzo) {
        Ok(u) if u.origin() == origine.origin() => Destinazione::Interna,
        Ok(u) if matches!(u.scheme(), "http" | "https" | "mailto") => Destinazione::Esterna,
        _ => Destinazione::Bloccata,
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn solo_il_server_configurato() {
        let o = Url::parse("https://metodo.esempio.it").unwrap();
        assert!(matches!(classifica(&o, "https://metodo.esempio.it/ordini/"), Destinazione::Interna));
        assert!(matches!(classifica(&o, "http://metodo.esempio.it/ordini/"), Destinazione::Esterna));
        assert!(matches!(classifica(&o, "https://metodo.esempio.it.evil.com/"), Destinazione::Esterna));
        assert!(matches!(classifica(&o, "https://www.google.com/"), Destinazione::Esterna));
        assert!(matches!(classifica(&o, "file:///etc/passwd"), Destinazione::Bloccata));
        assert!(matches!(classifica(&o, "javascript:alert(1)"), Destinazione::Bloccata));
    }
}
