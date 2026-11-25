//! Pagine locali del client (nessun server ancora raggiungibile) e verifiche di avvio.

use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use url::Url;

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

const STILE: &str = "body{font-family:system-ui,Segoe UI,sans-serif;background:#f6f7f9;margin:0}\
main{max-width:620px;margin:70px auto;background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:28px 32px}\
h1{font-size:22px;margin-top:0}input{width:100%;box-sizing:border-box;font-size:16px;padding:8px 10px;\
border:1px solid #ced4da;border-radius:6px}button{font-size:15px;padding:8px 16px;border-radius:6px;\
border:1px solid #0d6efd;background:#0d6efd;color:#fff;cursor:pointer;margin-top:12px;margin-right:8px}\
button.sec{background:#fff;color:#0d6efd}code{background:#f1f3f5;padding:1px 4px;border-radius:3px}\
.err{color:#b02a37}.mut{color:#6c757d;font-size:14px}";

const SCRIPT: &str = "<script>const invia=m=>window.ipc&&window.ipc.postMessage(JSON.stringify(m));\
function salva(e){e.preventDefault();invia({tipo:'imposta_server',url:document.getElementById('url').value.trim()});\
document.getElementById('stato').textContent='Connessione in corso…';}</script>";

/// Schermata di primo avvio (o "cambia indirizzo"): chiede l'indirizzo del server e lo salva.
pub fn pagina_indirizzo(attuale: &str, errore: &str, percorso_conf: &Path) -> String {
    format!(
        "<!doctype html><meta charset=utf-8><title>Metodo v2</title><style>{STILE}</style>{SCRIPT}<main>\
         <h1>Collegamento a Metodo v2</h1>\
         <p>Indica l'indirizzo del server Metodo v2, ad esempio <code>https://metodo.miaazienda.it</code> \
         oppure, se Metodo v2 gira su un PC della rete, <code>http://192.168.1.20:8000</code>.</p>\
         <form onsubmit=\"salva(event)\"><input id=url type=url required autofocus placeholder=\"https://…\" value=\"{}\">\
         <button>Collega</button>{}</form><p id=stato class=err>{}</p>\
         <p class=mut>L'indirizzo viene salvato in <code>{}</code>.<br>Il client mostra solo Metodo v2: \
         serve un server Metodo v2 installato e acceso (vedi README del progetto, \"Produzione (Docker)\").</p></main>",
        esc(attuale),
        if attuale.is_empty() {
            ""
        } else {
            " <button type=button class=sec onclick=\"invia({tipo:'riprova'})\">Annulla</button>"
        },
        esc(errore),
        esc(&percorso_conf.display().to_string())
    )
}

/// Server configurato ma non raggiungibile.
pub fn pagina_non_raggiungibile(url: &str, dettaglio: &str) -> String {
    format!(
        "<!doctype html><meta charset=utf-8><title>Metodo v2</title><style>{STILE}</style>{SCRIPT}<main>\
         <h1>Server Metodo v2 non raggiungibile</h1>\
         <p>Il client non riesce a collegarsi a <code>{}</code>.</p><p class=err>{}</p>\
         <ul class=mut><li>il server Metodo v2 è acceso e avviato?</li><li>l'indirizzo e la porta sono corretti?</li>\
         <li>questo PC è nella stessa rete (o in VPN) del server?</li></ul>\
         <button onclick=\"invia({{tipo:'riprova'}})\">Riprova</button>\
         <button class=sec onclick=\"invia({{tipo:'cambia_server'}})\">Cambia indirizzo</button></main>",
        esc(url),
        esc(dettaglio)
    )
}

/// Normalizza e valida l'indirizzo scritto dall'utente.
pub fn normalizza(testo: &str) -> Result<Url, String> {
    let t = testo.trim();
    if t.is_empty() {
        return Err("Indirizzo vuoto.".into());
    }
    let con_schema = if t.contains("://") { t.to_string() } else { format!("https://{t}") };
    let u = Url::parse(&con_schema).map_err(|_| format!("Indirizzo non valido: {t}"))?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return Err(format!("Indirizzo non valido: {t} (serve http:// o https://)"));
    }
    Ok(u)
}

/// Verifica rapida che il server accetti connessioni (prima di caricare la pagina).
pub fn raggiungibile(u: &Url) -> Result<(), String> {
    let host = u.host_str().ok_or("indirizzo senza host")?;
    let porta = u.port_or_known_default().unwrap_or(443);
    let indirizzi =
        (host, porta).to_socket_addrs().map_err(|e| format!("Nome del server non risolto ({host}): {e}"))?;
    let mut ultimo = String::from("nessun indirizzo");
    for a in indirizzi {
        match TcpStream::connect_timeout(&a, Duration::from_secs(4)) {
            Ok(_) => return Ok(()),
            Err(e) => ultimo = format!("{a}: {e}"),
        }
    }
    Err(format!("Connessione non riuscita ({ultimo})."))
}

/// Salva l'indirizzo nel file di configurazione mantenendo il resto (commenti compresi).
pub fn salva_url(percorso: &Path, url: &str) -> std::io::Result<()> {
    let testo = std::fs::read_to_string(percorso).unwrap_or_default();
    let riga = format!("url = {}", toml::Value::String(url.to_string()));
    let mut trovata = false;
    let mut righe: Vec<String> = testo
        .lines()
        .map(|l| {
            if !trovata && l.trim_start().starts_with("url") && l.contains('=') {
                trovata = true;
                riga.clone()
            } else {
                l.to_string()
            }
        })
        .collect();
    if !trovata {
        righe.insert(0, riga);
    }
    std::fs::write(percorso, righe.join("\n") + "\n")
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn normalizzazione() {
        assert_eq!(normalizza("metodo.it").unwrap().as_str(), "https://metodo.it/");
        assert_eq!(normalizza("http://192.168.1.20:8000").unwrap().port(), Some(8000));
        assert!(normalizza("").is_err() && normalizza("file:///c:/x").is_err() && normalizza("javascript:x").is_err());
    }

    #[test]
    fn salvataggio_mantiene_il_resto() {
        let dir = std::env::temp_dir().join("metodo-client-test");
        let _ = std::fs::create_dir_all(&dir);
        let p = dir.join("client.toml");
        std::fs::write(&p, crate::configurazione::MODELLO).unwrap();
        salva_url(&p, "https://x.it/").unwrap();
        let t = std::fs::read_to_string(&p).unwrap();
        assert!(t.contains("url = \"https://x.it/\"") && t.contains("[download]") && t.contains("# Configurazione"));
        let c: crate::configurazione::Configurazione = toml::from_str(&t).unwrap();
        assert_eq!(c.url, "https://x.it/");
    }

    #[test]
    fn porta_chiusa_non_raggiungibile() {
        assert!(raggiungibile(&Url::parse("http://127.0.0.1:9").unwrap()).is_err());
    }
}
