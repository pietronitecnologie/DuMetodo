//! DuMetodo Client: browser desktop personalizzato per DuMetodo.
//!
//! La grafica, i menu del tasto destro e le azioni restano sul SERVER: il client mostra le pagine di
//! DuMetodo, limita la navigazione al server configurato, mostra menu NATIVI composti dalla pagina e
//! salva i download nelle cartelle configurate su questo PC (es. la bolla nella cartella del gestionale).

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod configurazione;
mod ponte;
mod sicurezza;

use std::path::PathBuf;
use std::sync::Mutex;

use muda::{ContextMenu, Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoop, EventLoopBuilder, EventLoopProxy};
use tao::window::{Fullscreen, Window, WindowBuilder};
use url::Url;
use wry::{NewWindowResponse, WebView, WebViewBuilder};

use configurazione::Configurazione;
use ponte::Messaggio;
use sicurezza::Destinazione;

const VERSIONE: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug)]
enum Evento {
    DallaPagina(String),
    VoceMenu(MenuId),
    Naviga(String),
    DownloadCompletato { percorso: Option<PathBuf>, riuscito: bool },
}

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

fn pagina_errore(titolo: &str, dettaglio: &str, percorso: &str) -> String {
    let esc = |s: &str| s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    format!(
        "<!doctype html><meta charset=utf-8><title>DuMetodo</title>\
         <body style=\"font-family:system-ui,sans-serif;max-width:640px;margin:60px auto;line-height:1.5\">\
         <h1 style=\"font-size:22px\">{}</h1><p>{}</p>\
         <p>File di configurazione:<br><code>{}</code></p>\
         <p>Imposta <code>url</code> con l'indirizzo del server DuMetodo e riavvia il programma, oppure avvialo con \
         <code>--url https://...</code>.</p></body>",
        esc(titolo),
        esc(dettaglio),
        esc(percorso)
    )
}

fn main() {
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
    let origine = Url::parse(conf.url.trim()).ok().filter(|u| matches!(u.scheme(), "http" | "https"));

    let event_loop: EventLoop<Evento> = EventLoopBuilder::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let window = WindowBuilder::new()
        .with_title("DuMetodo")
        .with_inner_size(tao::dpi::LogicalSize::new(1366.0, 860.0))
        .with_maximized(true)
        .build(&event_loop)
        .expect("impossibile creare la finestra");
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

    let webview = costruisci_webview(&window, &conf, origine.as_ref(), errore_conf, &percorso_conf, proxy);
    if let Some(z) = conf.zoom {
        let _ = webview.zoom(z.clamp(0.3, 3.0));
    }

    // Il menu nativo deve restare in vita finché è aperto (su Linux la scelta arriva in modo asincrono).
    let mut menu_aperto: Option<Menu> = None;
    let mut zoom = conf.zoom.unwrap_or(1.0);
    let chiosco = conf.schermo_intero;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => *control_flow = ControlFlow::Exit,
            Event::UserEvent(Evento::DallaPagina(testo)) => match ponte::interpreta(&testo) {
                Some(Messaggio::Menu { voci }) => {
                    let menu = Menu::new();
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
                    menu_aperto.replace(menu);
                }
                Some(Messaggio::Stampa) => {
                    let _ = webview.print();
                }
                Some(Messaggio::SchermoIntero) if !chiosco => {
                    let attivo = window.fullscreen().is_some();
                    window.set_fullscreen(if attivo { None } else { Some(Fullscreen::Borderless(None)) });
                }
                Some(Messaggio::Zoom { passo }) => {
                    zoom = if passo == 0.0 { 1.0 } else { (zoom + passo).clamp(0.3, 3.0) };
                    let _ = webview.zoom(zoom);
                }
                _ => {}
            },
            Event::UserEvent(Evento::VoceMenu(id)) => {
                if let Some(v) = id.as_ref().strip_prefix("dm:") {
                    let _ = webview.evaluate_script(&format!(
                        "window.DuMetodoClient && window.DuMetodoClient.esegui({})",
                        ponte::js_stringa(v)
                    ));
                }
                menu_aperto.take();
            }
            Event::UserEvent(Evento::Naviga(indirizzo)) => {
                let _ = webview.load_url(&indirizzo);
            }
            Event::UserEvent(Evento::DownloadCompletato { percorso, riuscito }) => {
                let testo = match (riuscito, percorso) {
                    (true, Some(p)) => format!("File salvato in {}", p.display()),
                    (true, None) => "Download completato".to_string(),
                    (false, _) => "Download non riuscito".to_string(),
                };
                let _ = webview.evaluate_script(&format!(
                    "window.__duMetodoAvviso && window.__duMetodoAvviso({})",
                    ponte::js_stringa(&testo)
                ));
            }
            _ => {}
        }
    });
}

fn costruisci_webview(
    window: &Window,
    conf: &Configurazione,
    origine: Option<&Url>,
    errore_conf: Option<String>,
    percorso_conf: &std::path::Path,
    proxy: EventLoopProxy<Evento>,
) -> WebView {
    let mut builder = WebViewBuilder::new()
        .with_user_agent(format!(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 DuMetodoClient/{VERSIONE}"
        ))
        .with_devtools(conf.strumenti_sviluppatore && !conf.schermo_intero)
        .with_clipboard(true);

    match origine {
        None => {
            let (titolo, dettaglio) = match errore_conf {
                Some(e) => ("Configurazione non valida", e),
                None => ("Indirizzo del server non configurato", String::new()),
            };
            builder = builder.with_html(pagina_errore(titolo, &dettaglio, &percorso_conf.display().to_string()));
        }
        Some(origine) => {
            let origine_nav = origine.clone();
            let origine_finestre = origine.clone();
            let origine_ipc = origine.clone();
            let proxy_ipc = proxy.clone();
            let proxy_fin = proxy.clone();
            let proxy_dl = proxy;
            let regole_dl = conf.download.clone();
            builder = builder
                .with_url(origine.as_str())
                .with_initialization_script(ponte::SCRIPT_INIZIALE)
                .with_navigation_handler(move |indirizzo| match sicurezza::classifica(&origine_nav, &indirizzo) {
                    Destinazione::Interna => true,
                    Destinazione::Esterna => {
                        let _ = open::that_detached(&indirizzo);
                        false
                    }
                    Destinazione::Bloccata => false,
                })
                .with_new_window_req_handler(move |indirizzo, _| {
                    match sicurezza::classifica(&origine_finestre, &indirizzo) {
                        // Un solo "browser": le pagine del server si aprono nella stessa finestra.
                        Destinazione::Interna => {
                            let _ = proxy_fin.send_event(Evento::Naviga(indirizzo));
                        }
                        Destinazione::Esterna => {
                            let _ = open::that_detached(&indirizzo);
                        }
                        Destinazione::Bloccata => {}
                    }
                    NewWindowResponse::Deny
                })
                .with_ipc_handler(move |richiesta| {
                    // Si accettano messaggi solo dalle pagine del server configurato.
                    let da = richiesta.uri().to_string();
                    if matches!(sicurezza::classifica(&origine_ipc, &da), Destinazione::Interna) {
                        let _ = proxy_ipc.send_event(Evento::DallaPagina(richiesta.body().clone()));
                    }
                })
                .with_download_started_handler(move |indirizzo, percorso| {
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
                    let _ = proxy_dl.send_event(Evento::DownloadCompletato { percorso, riuscito });
                });
        }
    }

    #[cfg(any(target_os = "windows", target_os = "macos"))]
    let webview = builder.build(window);
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let webview = {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        builder.build_gtk(window.default_vbox().expect("vbox della finestra"))
    };
    webview.expect("impossibile creare la webview")
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
