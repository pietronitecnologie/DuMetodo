//! Metodo v2 Client: browser desktop personalizzato per Metodo v2.
//!
//! La grafica, i menu del tasto destro e le azioni restano sul SERVER: il client mostra le pagine di
//! Metodo v2, limita la navigazione al server configurato, mostra menu NATIVI composti dalla pagina e
//! salva i download nelle cartelle configurate su questo PC (es. la bolla nella cartella del gestionale).
//! Più pagine si aprono in SCHEDE nella stessa finestra (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+clic sui link).

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod aggiorna;
mod avvio;
mod barra;
mod chat;
mod configurazione;
mod ponte;
mod sessione;
mod sicurezza;

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Mutex;

use muda::{ContextMenu, IconMenuItem, Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoop, EventLoopBuilder, EventLoopProxy};
use tao::window::{Fullscreen, Window, WindowBuilder};
use url::Url;
use wry::{NewWindowResponse, Rect, WebContext, WebView, WebViewBuilder};

use configurazione::Configurazione;
use ponte::Messaggio;
use sicurezza::Destinazione;

const VERSIONE: &str = env!("CARGO_PKG_VERSION");
/// Id riservato al pannello chat (le schede partono da 1).
const ID_CHAT: u32 = 0;

#[derive(Debug)]
enum Evento {
    /// Messaggio da una scheda: `dal_server` è true solo se la pagina è del server configurato.
    DallaPagina {
        scheda: u32,
        testo: String,
        dal_server: bool,
    },
    /// Clic nella barra delle schede.
    DallaBarra(String),
    Titolo {
        scheda: u32,
        titolo: String,
    },
    VoceMenu(MenuId),
    /// Pagina del server da aprire in una nuova scheda (Ctrl+clic, clic centrale, target=_blank).
    NuovaScheda(String),
    /// Il file dell'aggiornamento è stato scaricato (o il download è fallito).
    AggiornamentoScaricato {
        scheda: u32,
        percorso: Option<PathBuf>,
        riuscito: bool,
    },
    DownloadCompletato {
        scheda: u32,
        percorso: Option<PathBuf>,
        riuscito: bool,
    },
}

struct Scheda {
    id: u32,
    pagina: WebView,
    titolo: String,
}

/// Dove si agganciano le pagine: la finestra (Windows, macOS) o un contenitore GTK a posizioni fisse (Linux).
#[cfg(not(target_os = "linux"))]
type Contenitore = ();
#[cfg(target_os = "linux")]
type Contenitore = gtk::Fixed;

/// Server attualmente collegato (None finché l'utente non ne indica uno raggiungibile).
type Origine = Rc<RefCell<Option<Url>>>;

fn argomento(nome: &str) -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == nome {
            return args.next();
        }
        if let Some(v) = a.strip_prefix(&format!("{nome}=")) {
            return Some(v.to_string());
        }
    }
    None
}

/// Registro degli errori accanto alla configurazione (in release su Windows non c'è console).
fn registra(percorso_conf: &Path, messaggio: &str) {
    use std::io::Write;
    if let Some(cartella) = percorso_conf.parent()
        && let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(cartella.join("client.log"))
    {
        let _ = writeln!(f, "{messaggio}");
    }
}

/// Errore che impedisce l'avvio: messaggio visibile all'utente (su Windows una finestra di dialogo).
fn errore_fatale(percorso_conf: &Path, messaggio: &str) -> ! {
    registra(percorso_conf, messaggio);
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};
        let testo: Vec<u16> = messaggio.encode_utf16().chain(std::iter::once(0)).collect();
        let titolo: Vec<u16> = "Metodo v2".encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            MessageBoxW(std::ptr::null_mut(), testo.as_ptr(), titolo.as_ptr(), MB_OK | MB_ICONERROR);
        }
    }
    eprintln!("{messaggio}");
    std::process::exit(1)
}

/// Prova a collegarsi a `url`: se raggiungibile carica Metodo v2, altrimenti mostra una pagina che lo spiega.
fn collega(webview: &WebView, origine: &Origine, url: &Url) {
    match avvio::raggiungibile(url) {
        Ok(()) => {
            *origine.borrow_mut() = Some(url.clone());
            let _ = webview.load_url(url.as_str());
        }
        Err(e) => {
            *origine.borrow_mut() = None;
            let _ = webview.load_html(&avvio::pagina_non_raggiungibile(url.as_str(), &e));
        }
    }
}

fn main() {
    aggiorna::pulisci_versione_precedente();
    let percorso_conf = argomento("--configurazione")
        .map(PathBuf::from)
        .or_else(configurazione::percorso_predefinito)
        .unwrap_or_else(|| PathBuf::from("client.toml"));
    let (mut conf, errore_conf) = match configurazione::carica(&percorso_conf) {
        Ok(c) => (c, None),
        Err(e) => (Configurazione::default(), Some(e)),
    };
    if let Some(u) = argomento("--url") {
        conf.url = u;
    }
    let mut url_corrente = avvio::normalizza(&conf.url).ok();

    let event_loop: EventLoop<Evento> = EventLoopBuilder::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let window = match WindowBuilder::new()
        .with_title("Metodo v2")
        .with_inner_size(tao::dpi::LogicalSize::new(1366.0, 860.0))
        .with_maximized(true)
        .with_window_icon(icona_finestra())
        .build(&event_loop)
    {
        Ok(w) => w,
        Err(e) => errore_fatale(&percorso_conf, &format!("Impossibile creare la finestra: {e}")),
    };
    if conf.schermo_intero {
        window.set_fullscreen(Some(Fullscreen::Borderless(None)));
    }

    // I clic sulle voci dei menu nativi arrivano su un altro thread: li inoltriamo al ciclo eventi.
    let proxy_menu = Mutex::new(proxy.clone());
    MenuEvent::set_event_handler(Some(move |e: MenuEvent| {
        if let Ok(p) = proxy_menu.lock() {
            let _ = p.send_event(Evento::VoceMenu(e.id));
        }
    }));

    let contenitore = crea_contenitore(&window);
    // Un solo contesto per tutte le schede: stessi cookie, quindi un solo accesso a Metodo v2.
    // Cartella fissa per i dati del motore web (cookie compresi): l'accesso resta valido tra un avvio e l'altro.
    let mut contesto = WebContext::new(percorso_conf.parent().map(|c| c.join("dati-web")));
    let origine: Origine = Rc::new(RefCell::new(None));

    let barra = match costruisci_barra(&contenitore, &window, proxy.clone()) {
        Ok(b) => b,
        Err(e) => errore_fatale(
            &percorso_conf,
            &format!(
                "Impossibile avviare il motore delle pagine web: {e}\n\nSu Windows serve Microsoft Edge WebView2 \
                 Runtime (https://developer.microsoft.com/microsoft-edge/webview2/)."
            ),
        ),
    };

    let pagina_iniziale = match (&errore_conf, &url_corrente) {
        (Some(e), _) => avvio::pagina_indirizzo(&conf.url, e, &percorso_conf),
        (None, None) => avvio::pagina_indirizzo(&conf.url, "", &percorso_conf),
        (None, Some(_)) => "<!doctype html><meta charset=utf-8><title>Metodo v2</title><body>".to_string(),
    };
    let mut prossimo_id: u32 = 1;
    let prima = match costruisci_pagina(
        &contenitore,
        &window,
        &mut contesto,
        &conf,
        origine.clone(),
        prossimo_id,
        Iniziale::Html(&pagina_iniziale),
        proxy.clone(),
    ) {
        Ok(w) => w,
        Err(e) => errore_fatale(&percorso_conf, &format!("Impossibile creare la pagina: {e}")),
    };
    let mut schede: Vec<Scheda> = vec![Scheda { id: prossimo_id, pagina: prima, titolo: "Metodo v2".into() }];
    // Pannello chat: una pagina del server (id 0), sempre caricata anche se nascosta, per le notifiche.
    let chat_web = match costruisci_pagina(
        &contenitore,
        &window,
        &mut contesto,
        &conf,
        origine.clone(),
        ID_CHAT,
        Iniziale::Html("<!doctype html><meta charset=utf-8><title>Chat</title><body>"),
        proxy.clone(),
    ) {
        Ok(w) => w,
        Err(e) => errore_fatale(&percorso_conf, &format!("Impossibile creare il pannello chat: {e}")),
    };
    let _ = chat_web.set_visible(false);
    let mut finestra_attiva = true;
    let mut attiva: usize = 0;
    prossimo_id += 1;

    let mut zoom = conf.zoom.unwrap_or(1.0).clamp(0.3, 3.0);
    if conf.zoom.is_some() {
        let _ = schede[0].pagina.zoom(zoom);
    }
    if errore_conf.is_none()
        && let Some(u) = &url_corrente
    {
        collega(&schede[0].pagina, &origine, u);
    }
    // Riapre le schede dell'ultima volta (solo se il server è lo stesso e risponde).
    let percorso_sessione = sessione::percorso(&percorso_conf);
    if conf.ripristina_schede.unwrap_or(true)
        && let Some(server) = origine_testo(&origine)
        && let Some(salvata) = sessione::carica(&percorso_sessione, &server)
    {
        let _ = schede[0].pagina.load_url(&salvata.schede[0]);
        for indirizzo in salvata.schede.iter().skip(1) {
            match costruisci_pagina(
                &contenitore,
                &window,
                &mut contesto,
                &conf,
                origine.clone(),
                prossimo_id,
                Iniziale::Url(indirizzo),
                proxy.clone(),
            ) {
                Ok(pagina) => {
                    let _ = pagina.zoom(zoom);
                    let _ = pagina.set_visible(false);
                    schede.push(Scheda { id: prossimo_id, pagina, titolo: "Metodo v2".into() });
                    prossimo_id += 1;
                }
                Err(e) => registra(&percorso_conf, &format!("Ripristino scheda non riuscito: {e}")),
            }
        }
        attiva = salvata.attiva.min(schede.len() - 1);
        for (i, s) in schede.iter().enumerate() {
            let _ = s.pagina.set_visible(i == attiva);
        }
    }
    carica_chat(&chat_web, &origine);
    aggiorna_barra(&barra, &schede, attiva, &origine);

    // Il menu nativo deve restare in vita finché è aperto (su Linux la scelta arriva in modo asincrono).
    let mut menu_aperto: Option<(Menu, u32)> = None;
    let chiosco = conf.schermo_intero;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        // Azioni sulle schede decise dentro il match e applicate dopo (servono `schede` mutabile).
        let mut apri: Option<Option<String>> = None;
        let mut chiudi: Option<u32> = None;
        let mut attiva_id: Option<u32> = None;
        let mut cambia_server = false;
        let mut ridisegna = false;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                salva_sessione(&percorso_sessione, &origine, &schede, attiva);
                *control_flow = ControlFlow::Exit;
            }
            Event::WindowEvent { event: WindowEvent::Focused(attiva_ora), .. } => finestra_attiva = attiva_ora,
            Event::WindowEvent { event: WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. }, .. } => {
                disponi(&contenitore, &window, &barra, &schede, &chat_web);
            }
            Event::UserEvent(Evento::DallaBarra(testo)) => match barra::interpreta(&testo) {
                // Clic sulla scheda già attiva: niente da fare (e niente ridisegno della barra).
                Some(barra::Comando::Attiva { id }) if schede.get(attiva).is_some_and(|s| s.id != id) => {
                    attiva_id = Some(id)
                }
                Some(barra::Comando::Attiva { .. }) => {}
                Some(barra::Comando::Chiudi { id }) => chiudi = Some(id),
                Some(barra::Comando::Nuova) => apri = Some(None),
                Some(barra::Comando::CambiaServer) => cambia_server = true,
                Some(barra::Comando::Chat) => {
                    let ora_visibile = !chat::visibile();
                    chat::VISIBILE.with(|v| v.set(ora_visibile));
                    disponi(&contenitore, &window, &barra, &schede, &chat_web);
                    if ora_visibile {
                        let _ = chat_web.focus();
                        let script = match chat::DA_APRIRE.with(|d| d.take()) {
                            Some(con) => format!("window.MetodoChat && window.MetodoChat.apri({con})"),
                            None => "window.MetodoChat && window.MetodoChat.apriNonLetti()".to_string(),
                        };
                        let _ = chat_web.evaluate_script(&script);
                    } else if let Some(s) = schede.get(attiva) {
                        let _ = s.pagina.focus();
                    }
                    ridisegna = true;
                }
                None => {}
            },
            Event::UserEvent(Evento::Titolo { scheda, titolo }) => {
                if let Some(s) = schede.iter_mut().find(|s| s.id == scheda) {
                    // "Ordini · Metodo v2 · Fornitore" -> "Ordini": nella scheda basta la pagina.
                    let breve = titolo.split(" · Metodo v2").next().unwrap_or(&titolo).trim();
                    s.titolo = breve.chars().take(120).collect();
                    salva_sessione(&percorso_sessione, &origine, &schede, attiva);
                    // Dopo l'accesso in una scheda, il pannello chat (fermo sulla pagina di accesso) si ricarica.
                    if chat_web.url().is_ok_and(|u| u.contains("/accesso/")) && !breve.starts_with("Accesso") {
                        carica_chat(&chat_web, &origine);
                    }
                    aggiorna_barra(&barra, &schede, attiva, &origine);
                    if schede.get(attiva).is_some_and(|s| s.id == scheda) {
                        window.set_title(&format!("{} - Metodo v2", schede[attiva].titolo));
                    }
                }
            }
            Event::UserEvent(Evento::NuovaScheda(indirizzo)) => apri = Some(Some(indirizzo)),
            Event::UserEvent(Evento::DallaPagina { scheda: ID_CHAT, testo, dal_server: true }) => {
                match ponte::interpreta(&testo) {
                    Some(Messaggio::ChatNonLetti { n }) => {
                        if n != chat::non_letti() {
                            chat::NON_LETTI.with(|c| c.set(n));
                            aggiorna_barra(&barra, &schede, attiva, &origine);
                        }
                    }
                    Some(Messaggio::ChatNotifica { con, nome, testo, non_letti }) => {
                        chat::NON_LETTI.with(|c| c.set(non_letti));
                        chat::DA_APRIRE.with(|d| d.set(Some(con)));
                        aggiorna_barra(&barra, &schede, attiva, &origine);
                        let nome: String = nome.chars().take(60).collect();
                        // Se la chat è già davanti agli occhi basta il messaggio nel pannello.
                        if !(chat::visibile() && finestra_attiva) {
                            if let Some(s) = schede.get(attiva) {
                                avvisa(
                                    &s.pagina,
                                    &format!("💬 {nome}: {}", testo.chars().take(120).collect::<String>()),
                                );
                            }
                            chat::notifica_di_sistema(&nome, &testo);
                            if !finestra_attiva {
                                window.request_user_attention(Some(tao::window::UserAttentionType::Informational));
                            }
                        }
                    }
                    Some(Messaggio::ChatNascondi) => {
                        chat::VISIBILE.with(|v| v.set(false));
                        disponi(&contenitore, &window, &barra, &schede, &chat_web);
                        if let Some(s) = schede.get(attiva) {
                            let _ = s.pagina.focus();
                        }
                        aggiorna_barra(&barra, &schede, attiva, &origine);
                    }
                    _ => {}
                }
            }
            Event::UserEvent(Evento::DallaPagina { scheda, testo, dal_server }) => {
                let Some(pos) = schede.iter().position(|s| s.id == scheda) else { return };
                let webview = &schede[pos].pagina;
                match ponte::interpreta(&testo) {
                    // Pagine locali del client (primo avvio, server non raggiungibile).
                    Some(Messaggio::ImpostaServer { url }) if !dal_server => match avvio::normalizza(&url) {
                        Ok(u) => {
                            if let Err(e) = avvio::salva_url(&percorso_conf, u.as_str()) {
                                registra(&percorso_conf, &format!("Salvataggio indirizzo non riuscito: {e}"));
                            }
                            url_corrente = Some(u.clone());
                            collega(webview, &origine, &u);
                            carica_chat(&chat_web, &origine);
                            ridisegna = true;
                        }
                        Err(e) => {
                            let _ = webview.load_html(&avvio::pagina_indirizzo(&url, &e, &percorso_conf));
                        }
                    },
                    Some(Messaggio::Riprova) if !dal_server => {
                        if let Some(u) = &url_corrente {
                            collega(webview, &origine, u);
                            carica_chat(&chat_web, &origine);
                            ridisegna = true;
                        }
                    }
                    Some(Messaggio::CambiaServer) if !dal_server => {
                        let attuale = url_corrente.as_ref().map(|u| u.to_string()).unwrap_or_default();
                        let _ = webview.load_html(&avvio::pagina_indirizzo(&attuale, "", &percorso_conf));
                    }
                    // Pagine del server.
                    Some(Messaggio::Menu { voci, intestazione }) if dal_server => {
                        let menu = Menu::new();
                        // In testa: logo e nome dell'azienda (Configurazione > Azienda).
                        if let Some(int) = &intestazione
                            && let Some((rgba, w, h)) = ponte::logo_rgba(int, window.scale_factor())
                            && let Ok(icona) = muda::Icon::from_rgba(rgba, w, h)
                        {
                            let titolo: String = int.titolo.chars().take(60).collect();
                            let voce = IconMenuItem::with_id(MenuId::new("logo"), titolo, true, Some(icona), None);
                            let _ = menu.append(&voce);
                            let _ = menu.append(&PredefinedMenuItem::separator());
                        }
                        for v in voci.iter().take(ponte::MASSIMO_VOCI) {
                            if v.separatore {
                                let _ = menu.append(&PredefinedMenuItem::separator());
                            } else if !v.etichetta.is_empty() && v.id.len() <= 20 {
                                let id = MenuId::new(format!("dm:{}", v.id));
                                let etichetta: String = v.etichetta.chars().take(80).collect();
                                let _ = menu.append(&MenuItem::with_id(id, etichetta, true, None));
                            }
                        }
                        mostra_menu(&window, &menu);
                        menu_aperto.replace((menu, scheda));
                    }
                    Some(Messaggio::Stampa) if dal_server => {
                        let _ = webview.print();
                    }
                    Some(Messaggio::Copia { testo }) if dal_server => {
                        let esito = if testo.len() > ponte::MASSIMO_APPUNTI {
                            Err("testo troppo lungo".to_string())
                        } else {
                            arboard::Clipboard::new().and_then(|mut c| c.set_text(testo)).map_err(|e| e.to_string())
                        };
                        let avviso = match esito {
                            Ok(()) => "Copiato negli appunti".to_string(),
                            Err(e) => {
                                registra(&percorso_conf, &format!("Copia negli appunti non riuscita: {e}"));
                                "Copia non riuscita".to_string()
                            }
                        };
                        avvisa(webview, &avviso);
                    }
                    Some(Messaggio::Incolla) if dal_server => {
                        match arboard::Clipboard::new().and_then(|mut c| c.get_text()) {
                            Ok(testo) if testo.len() <= ponte::MASSIMO_APPUNTI => {
                                let _ = webview.evaluate_script(&format!(
                                    "window.MetodoClient && window.MetodoClient.incolla({})",
                                    ponte::js_stringa(&testo)
                                ));
                            }
                            Ok(_) => avvisa(webview, "Testo negli appunti troppo lungo"),
                            Err(_) => avvisa(webview, "Negli appunti non c'è testo"),
                        }
                    }
                    Some(Messaggio::NuovaScheda { url }) => {
                        // Solo indirizzi del server: un indirizzo esterno non apre schede.
                        let interno = url.filter(|u| {
                            origine
                                .borrow()
                                .as_ref()
                                .is_some_and(|o| matches!(sicurezza::classifica(o, u), Destinazione::Interna))
                        });
                        apri = Some(interno);
                    }
                    Some(Messaggio::ChiudiScheda) => chiudi = Some(scheda),
                    Some(Messaggio::AggiornaClient { versione, sha256, url }) if dal_server => {
                        // Gli aggiornamenti caricati sul server sono eseguibili Windows: su Linux il client
                        // si aggiorna installando il nuovo pacchetto .deb.
                        if !cfg!(windows) {
                            avvisa(webview, "Su Linux il client si aggiorna installando il nuovo pacchetto .deb");
                        } else if !aggiorna::richiesta_valida(&versione, &sha256, &url) {
                            avvisa(webview, "Aggiornamento non valido");
                        } else if let Some(server) = origine_testo(&origine) {
                            let percorso = aggiorna::percorso_download(&versione);
                            aggiorna::IN_CORSO.with(|c| {
                                *c.borrow_mut() =
                                    Some(aggiorna::InAttesa { versione, sha256: sha256.to_ascii_lowercase(), percorso })
                            });
                            avvisa(webview, "Scaricamento dell'aggiornamento…");
                            let _ = webview.load_url(&format!("{server}{url}"));
                        }
                    }
                    Some(Messaggio::CambiaScheda { passo }) => {
                        let n = schede.len() as i64;
                        let nuova = (attiva as i64 + i64::from(passo.signum())).rem_euclid(n) as usize;
                        attiva_id = Some(schede[nuova].id);
                    }
                    Some(Messaggio::SchermoIntero) if !chiosco => {
                        let pieno = window.fullscreen().is_some();
                        window.set_fullscreen(if pieno { None } else { Some(Fullscreen::Borderless(None)) });
                    }
                    Some(Messaggio::Zoom { passo }) => {
                        zoom = if passo == 0.0 { 1.0 } else { (zoom + passo).clamp(0.3, 3.0) };
                        for s in &schede {
                            let _ = s.pagina.zoom(zoom);
                        }
                    }
                    _ => {}
                }
            }
            Event::UserEvent(Evento::VoceMenu(id)) => {
                if let Some((_, scheda)) = &menu_aperto
                    && let Some(v) = id.as_ref().strip_prefix("dm:")
                    && let Some(s) = schede.iter().find(|s| s.id == *scheda)
                {
                    let _ = s.pagina.evaluate_script(&format!(
                        "window.MetodoClient && window.MetodoClient.esegui({})",
                        ponte::js_stringa(v)
                    ));
                }
                menu_aperto.take();
            }
            Event::UserEvent(Evento::AggiornamentoScaricato { scheda, percorso, riuscito }) => {
                let in_corso = aggiorna::IN_CORSO.with(|c| c.borrow_mut().take());
                let pagina = schede.iter().find(|s| s.id == scheda).map(|s| &s.pagina);
                let esito = match (in_corso, percorso, riuscito) {
                    (Some(a), Some(p), true) => match aggiorna::impronta(&p) {
                        Ok(h) if h == a.sha256 => aggiorna::installa(&p).map(|exe| (exe, a.versione)),
                        Ok(_) => {
                            let _ = std::fs::remove_file(&p);
                            Err("il file scaricato non corrisponde (impronta diversa): aggiornamento annullato".into())
                        }
                        Err(e) => Err(format!("file scaricato illeggibile: {e}")),
                    },
                    _ => Err("scaricamento non riuscito".into()),
                };
                match esito {
                    Ok((exe, versione)) => {
                        registra(&percorso_conf, &format!("Client aggiornato alla versione {versione}"));
                        salva_sessione(&percorso_sessione, &origine, &schede, attiva);
                        // Il nuovo client riparte con gli stessi argomenti e riapre le schede.
                        match std::process::Command::new(&exe).args(std::env::args().skip(1)).spawn() {
                            Ok(_) => *control_flow = ControlFlow::Exit,
                            Err(e) => {
                                if let Some(p) = pagina {
                                    avvisa(p, &format!("Aggiornato: riavvia il client ({e})"));
                                }
                            }
                        }
                    }
                    Err(e) => {
                        registra(&percorso_conf, &format!("Aggiornamento del client non riuscito: {e}"));
                        if let Some(p) = pagina {
                            avvisa(p, &format!("Aggiornamento non riuscito: {e}"));
                        }
                    }
                }
            }
            Event::UserEvent(Evento::DownloadCompletato { scheda, percorso, riuscito }) => {
                let testo = match (riuscito, percorso) {
                    (true, Some(p)) => format!("File salvato in {}", p.display()),
                    (true, None) => "Download completato".to_string(),
                    (false, _) => "Download non riuscito".to_string(),
                };
                if let Some(s) = schede.iter().find(|s| s.id == scheda) {
                    avvisa(&s.pagina, &testo);
                }
            }
            _ => {}
        }

        // ---- Applica le azioni sulle schede
        if cambia_server {
            // Resta solo la scheda attiva, scollegata, con la schermata dell'indirizzo (Annulla = torna com'era).
            let tenuta = schede.remove(attiva);
            for s in schede.drain(..) {
                let _ = s.pagina.set_visible(false);
            }
            schede.push(tenuta);
            attiva = 0;
            *origine.borrow_mut() = None;
            let attuale = url_corrente.as_ref().map(|u| u.to_string()).unwrap_or_default();
            let _ = schede[0].pagina.load_html(&avvio::pagina_indirizzo(&attuale, "", &percorso_conf));
            schede[0].titolo = "Cambia server".into();
            let _ = schede[0].pagina.set_visible(true);
            ridisegna = true;
        }
        if let Some(indirizzo) = apri {
            if schede.len() >= barra::MASSIMO_SCHEDE {
                avvisa(&schede[attiva].pagina, "Troppe schede aperte: chiudine qualcuna (Ctrl+W)");
            } else {
                let base = origine.borrow().clone().or_else(|| url_corrente.clone());
                let destinazione = indirizzo.or_else(|| base.map(|u| u.to_string()));
                let iniziale = match &destinazione {
                    Some(u) => Iniziale::Url(u),
                    None => Iniziale::Html("<!doctype html><meta charset=utf-8><title>Nuova scheda</title><body>"),
                };
                match costruisci_pagina(
                    &contenitore,
                    &window,
                    &mut contesto,
                    &conf,
                    origine.clone(),
                    prossimo_id,
                    iniziale,
                    proxy.clone(),
                ) {
                    Ok(pagina) => {
                        let _ = pagina.zoom(zoom);
                        schede.push(Scheda { id: prossimo_id, pagina, titolo: "Nuova scheda".into() });
                        attiva_id = Some(prossimo_id);
                        prossimo_id += 1;
                    }
                    Err(e) => registra(&percorso_conf, &format!("Nuova scheda non riuscita: {e}")),
                }
            }
        }
        if let Some(id) = chiudi
            && let Some(pos) = schede.iter().position(|s| s.id == id)
        {
            if schede.len() == 1 {
                // Ultima scheda: si chiude il client, come un browser (la scheda verrà riaperta).
                salva_sessione(&percorso_sessione, &origine, &schede, attiva);
                *control_flow = ControlFlow::Exit;
            } else {
                let tolta = schede.remove(pos);
                let _ = tolta.pagina.set_visible(false);
                drop(tolta);
                if pos < attiva || attiva >= schede.len() {
                    attiva = attiva.saturating_sub(1);
                }
                attiva_id = Some(schede[attiva].id);
            }
        }
        if ridisegna && attiva_id.is_none() {
            aggiorna_barra(&barra, &schede, attiva, &origine);
        }
        if let Some(id) = attiva_id
            && let Some(pos) = schede.iter().position(|s| s.id == id)
        {
            attiva = pos;
            disponi(&contenitore, &window, &barra, &schede, &chat_web);
            for (i, s) in schede.iter().enumerate() {
                let _ = s.pagina.set_visible(i == attiva);
            }
            let _ = schede[attiva].pagina.focus();
            window.set_title(&format!("{} - Metodo v2", schede[attiva].titolo));
            aggiorna_barra(&barra, &schede, attiva, &origine);
            salva_sessione(&percorso_sessione, &origine, &schede, attiva);
        }
    });
}

/// Icona di Metodo v2 (quadrante di misura), 64×64 RGBA, ricavata da static/img/metodo-64.png.
fn icona_finestra() -> Option<tao::window::Icon> {
    tao::window::Icon::from_rgba(include_bytes!("icona-64.rgba").to_vec(), 64, 64).ok()
}

/// Origine del server collegato, es. "http://srv:8000" (None se nessun server è collegato).
fn origine_testo(origine: &Origine) -> Option<String> {
    origine.borrow().as_ref().map(|u| u.origin().ascii_serialization())
}

/// Salva gli indirizzi delle schede del server collegato (le pagine locali del client sono escluse).
fn salva_sessione(percorso: &Path, origine: &Origine, schede: &[Scheda], attiva: usize) {
    let Some(server) = origine_testo(origine) else { return };
    let mut indirizzi = Vec::new();
    let mut nuova_attiva = 0;
    for (i, s) in schede.iter().enumerate() {
        if let Ok(u) = s.pagina.url()
            && (u.starts_with(&format!("{server}/")) || u == server)
        {
            if i == attiva {
                nuova_attiva = indirizzi.len();
            }
            indirizzi.push(u);
        }
    }
    if indirizzi.is_empty() {
        return;
    }
    sessione::salva(percorso, &sessione::Sessione { server, schede: indirizzi, attiva: nuova_attiva });
}

/// Contenuto iniziale di una scheda.
enum Iniziale<'a> {
    Url(&'a str),
    Html(&'a str),
}

#[cfg(not(target_os = "linux"))]
fn crea_contenitore(_window: &Window) -> Contenitore {}

#[cfg(target_os = "linux")]
fn crea_contenitore(window: &Window) -> Contenitore {
    use gtk::prelude::*;
    use tao::platform::unix::WindowExtUnix;
    let fixed = gtk::Fixed::new();
    if let Some(vbox) = window.default_vbox() {
        vbox.pack_start(&fixed, true, true, 0);
    }
    fixed.show_all();
    fixed
}

/// Posizioni: la barra in alto, le pagine sotto (tutte nello stesso posto, visibile solo l'attiva).
/// Aree della finestra: barra delle schede, pagine e (se aperto) pannello chat a destra.
fn aree(window: &Window) -> (Rect, Rect, Rect) {
    let scala = window.scale_factor();
    let dim = window.inner_size().to_logical::<f64>(scala);
    let altezza = (dim.height - barra::ALTEZZA).max(1.0);
    let chat_l = if chat::visibile() { chat::LARGHEZZA.min(dim.width * 0.6) } else { 0.0 };
    let barra = Rect {
        position: tao::dpi::LogicalPosition::new(0.0, 0.0).into(),
        size: tao::dpi::LogicalSize::new(dim.width, barra::ALTEZZA).into(),
    };
    let pagina = Rect {
        position: tao::dpi::LogicalPosition::new(0.0, barra::ALTEZZA).into(),
        size: tao::dpi::LogicalSize::new((dim.width - chat_l).max(1.0), altezza).into(),
    };
    let pannello = Rect {
        position: tao::dpi::LogicalPosition::new(dim.width - chat_l, barra::ALTEZZA).into(),
        size: tao::dpi::LogicalSize::new(chat_l.max(1.0), altezza).into(),
    };
    (barra, pagina, pannello)
}

fn disponi(contenitore: &Contenitore, window: &Window, barra_web: &WebView, schede: &[Scheda], chat_web: &WebView) {
    let (b, p, c) = aree(window);
    posiziona(contenitore, barra_web, b);
    for s in schede {
        posiziona(contenitore, &s.pagina, p);
    }
    posiziona(contenitore, chat_web, c);
    let _ = chat_web.set_visible(chat::visibile());
}

/// Sposta e ridimensiona una pagina. Su Linux il solo `set_bounds` è temporaneo (GTK ripristina la misura
/// richiesta al primo ridisegno): si aggiornano anche posizione e misura nel contenitore a posizioni fisse.
fn posiziona(contenitore: &Contenitore, webview: &WebView, area: Rect) {
    let _ = webview.set_bounds(area);
    #[cfg(target_os = "linux")]
    {
        use gtk::prelude::*;
        use wry::WebViewExtUnix;
        let widget = webview.webview();
        let scala = widget.scale_factor() as f64;
        let (x, y): (i32, i32) = area.position.to_logical::<i32>(scala).into();
        let (l, a): (i32, i32) = area.size.to_logical::<i32>(scala).into();
        contenitore.move_(&widget, x, y);
        widget.set_size_request(l.max(1), a.max(1));
    }
    #[cfg(not(target_os = "linux"))]
    let _ = contenitore;
}

/// Carica il pannello chat del server collegato (resta attivo anche nascosto, per le notifiche).
fn carica_chat(chat_web: &WebView, origine: &Origine) {
    if let Some(server) = origine_testo(origine) {
        let _ = chat_web.load_url(&format!("{server}/chat/"));
    }
}

fn aggiorna_barra(barra_web: &WebView, schede: &[Scheda], attiva: usize, origine: &Origine) {
    let voci: Vec<barra::VoceScheda<'_>> = schede
        .iter()
        .enumerate()
        .map(|(i, s)| barra::VoceScheda { id: s.id, titolo: &s.titolo, attiva: i == attiva })
        .collect();
    let server = origine
        .borrow()
        .as_ref()
        .map(|u| match (u.host_str(), u.port()) {
            (Some(h), Some(p)) => format!("{h}:{p}"),
            (Some(h), None) => h.to_string(),
            _ => String::new(),
        })
        .unwrap_or_default();
    let _ = barra_web.evaluate_script(&barra::script_aggiorna(&voci, &server, chat::non_letti(), chat::visibile()));
}

/// Piccolo avviso in basso a destra nella pagina (lo disegna lo script iniziale).
fn avvisa(webview: &WebView, testo: &str) {
    let _ = webview
        .evaluate_script(&format!("window.__metodoAvviso && window.__metodoAvviso({})", ponte::js_stringa(testo)));
}

fn aggancia(builder: WebViewBuilder<'_>, window: &Window, contenitore: &Contenitore) -> wry::Result<WebView> {
    #[cfg(not(target_os = "linux"))]
    {
        let _ = contenitore;
        builder.build_as_child(window)
    }
    #[cfg(target_os = "linux")]
    {
        use wry::WebViewBuilderExtUnix;
        let _ = window;
        builder.build_gtk(contenitore)
    }
}

fn costruisci_barra(contenitore: &Contenitore, window: &Window, proxy: EventLoopProxy<Evento>) -> wry::Result<WebView> {
    let (b, _, _) = aree(window);
    let builder = WebViewBuilder::new()
        .with_bounds(b)
        .with_html(barra::pagina())
        .with_devtools(false)
        // La barra non naviga mai altrove.
        .with_navigation_handler(|indirizzo| indirizzo.starts_with("about:") || indirizzo.starts_with("data:"))
        .with_new_window_req_handler(|_, _| NewWindowResponse::Deny)
        .with_ipc_handler(move |richiesta| {
            let _ = proxy.send_event(Evento::DallaBarra(richiesta.body().clone()));
        });
    aggancia(builder, window, contenitore)
}

#[allow(clippy::too_many_arguments)]
fn costruisci_pagina(
    contenitore: &Contenitore,
    window: &Window,
    contesto: &mut WebContext,
    conf: &Configurazione,
    origine: Origine,
    id: u32,
    iniziale: Iniziale<'_>,
    proxy: EventLoopProxy<Evento>,
) -> wry::Result<WebView> {
    let origine_nav = origine.clone();
    let origine_fin = origine.clone();
    let origine_ipc = origine;
    let proxy_ipc = proxy.clone();
    let proxy_fin = proxy.clone();
    let proxy_tit = proxy.clone();
    let proxy_dl = proxy;
    let regole_dl = conf.download.clone();
    let (_, area, _) = aree(window);

    let builder = WebViewBuilder::new_with_web_context(contesto)
        .with_bounds(area)
        .with_user_agent(format!(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 MetodoClient/{VERSIONE} MetodoSistema/{}",
            std::env::consts::OS
        ))
        .with_devtools(conf.strumenti_sviluppatore && !conf.schermo_intero)
        .with_clipboard(true)
        .with_initialization_script(ponte::SCRIPT_INIZIALE)
        .with_navigation_handler(move |indirizzo| {
            let Some(o) = origine_nav.borrow().clone() else {
                // Nessun server collegato: solo le pagine locali del client.
                return indirizzo.starts_with("about:") || indirizzo.starts_with("data:");
            };
            match sicurezza::classifica(&o, &indirizzo) {
                Destinazione::Interna => true,
                Destinazione::Esterna => {
                    let _ = open::that_detached(&indirizzo);
                    false
                }
                Destinazione::Bloccata => false,
            }
        })
        .with_new_window_req_handler(move |indirizzo, _| {
            if let Some(o) = origine_fin.borrow().clone() {
                match sicurezza::classifica(&o, &indirizzo) {
                    // Le pagine del server si aprono in una nuova scheda della stessa finestra.
                    Destinazione::Interna => {
                        let _ = proxy_fin.send_event(Evento::NuovaScheda(indirizzo));
                    }
                    Destinazione::Esterna => {
                        let _ = open::that_detached(&indirizzo);
                    }
                    Destinazione::Bloccata => {}
                }
            }
            NewWindowResponse::Deny
        })
        .with_document_title_changed_handler(move |titolo| {
            let _ = proxy_tit.send_event(Evento::Titolo { scheda: id, titolo });
        })
        .with_ipc_handler(move |richiesta| {
            let da = richiesta.uri().to_string();
            let dal_server = origine_ipc.borrow().as_ref().is_some_and(|o| {
                matches!(sicurezza::classifica(o, &da), Destinazione::Interna) && da.starts_with("http")
            });
            let _ =
                proxy_ipc.send_event(Evento::DallaPagina { scheda: id, testo: richiesta.body().clone(), dal_server });
        })
        .with_download_started_handler(move |indirizzo, percorso| {
            // L'aggiornamento del client va nella cartella temporanea, mai dove dice una regola.
            if let Some(dest) = aggiorna::destinazione(&indirizzo) {
                let _ = std::fs::remove_file(&dest);
                *percorso = dest;
                return true;
            }
            // Le regole sono locali a questo PC: il server non può scegliere dove scrivere.
            if let Some(cartella) = regole_dl.cartella_per(&indirizzo)
                && std::fs::create_dir_all(&cartella).is_ok()
            {
                let nome = percorso.file_name().map(|n| n.to_owned()).unwrap_or_else(|| "download".into());
                *percorso = cartella.join(nome);
            }
            true
        })
        .with_download_completed_handler(move |_, percorso, riuscito| {
            let aggiornamento = percorso.as_deref().and_then(aggiorna::concluso).is_some()
                || (!riuscito && aggiorna::IN_CORSO.with(|c| c.borrow().is_some()) && percorso.is_none());
            let evento = if aggiornamento {
                Evento::AggiornamentoScaricato { scheda: id, percorso, riuscito }
            } else {
                Evento::DownloadCompletato { scheda: id, percorso, riuscito }
            };
            let _ = proxy_dl.send_event(evento);
        });
    let builder = match iniziale {
        Iniziale::Url(u) => builder.with_url(u),
        Iniziale::Html(h) => builder.with_html(h),
    };
    aggancia(builder, window, contenitore)
}

fn mostra_menu(window: &Window, menu: &Menu) {
    #[cfg(target_os = "windows")]
    {
        use tao::platform::windows::WindowExtWindows;
        unsafe {
            menu.show_context_menu_for_hwnd(window.hwnd() as isize, None);
        }
    }
    #[cfg(target_os = "macos")]
    {
        use tao::platform::macos::WindowExtMacOS;
        unsafe {
            menu.show_context_menu_for_nsview(window.ns_view() as _, None);
        }
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        use tao::platform::unix::WindowExtUnix;
        menu.show_context_menu_for_gtk_window(window.gtk_window().as_ref(), None);
    }
}
