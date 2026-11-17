//! DuMetodo Client: browser desktop personalizzato per DuMetodo.
//!
//! La grafica, i menu del tasto destro e le azioni restano sul SERVER: il client mostra le pagine di
//! DuMetodo, limita la navigazione al server configurato, mostra menu NATIVI composti dalla pagina e
//! salva i download nelle cartelle configurate su questo PC (es. la bolla nella cartella del gestionale).

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod avvio;
mod configurazione;
mod ponte;
mod sicurezza;

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;
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
    /// Messaggio dalla pagina: `dal_server` è true solo se la pagina è del server configurato.
    DallaPagina {
        testo: String,
        dal_server: bool,
    },
    VoceMenu(MenuId),
    Naviga(String),
    DownloadCompletato {
        percorso: Option<PathBuf>,
        riuscito: bool,
    },
}

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
        let titolo: Vec<u16> = "DuMetodo".encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            MessageBoxW(std::ptr::null_mut(), testo.as_ptr(), titolo.as_ptr(), MB_OK | MB_ICONERROR);
        }
    }
    eprintln!("{messaggio}");
    std::process::exit(1)
}

/// Prova a collegarsi a `url`: se raggiungibile carica DuMetodo, altrimenti mostra una pagina che lo spiega.
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
        .with_title("DuMetodo")
        .with_inner_size(tao::dpi::LogicalSize::new(1366.0, 860.0))
        .with_maximized(true)
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

    let origine: Origine = Rc::new(RefCell::new(None));
    let pagina_iniziale = match (&errore_conf, &url_corrente) {
        (Some(e), _) => avvio::pagina_indirizzo(&conf.url, e, &percorso_conf),
        (None, None) => avvio::pagina_indirizzo(&conf.url, "", &percorso_conf),
        (None, Some(_)) => "<!doctype html><meta charset=utf-8><title>DuMetodo</title><body>".to_string(),
    };
    let webview = match costruisci_webview(&window, &conf, origine.clone(), &pagina_iniziale, proxy) {
        Ok(w) => w,
        Err(e) => errore_fatale(
            &percorso_conf,
            &format!(
                "Impossibile avviare il motore delle pagine web: {e}\n\nSu Windows serve Microsoft Edge WebView2 \
                 Runtime (https://developer.microsoft.com/microsoft-edge/webview2/)."
            ),
        ),
    };
    if let Some(z) = conf.zoom {
        let _ = webview.zoom(z.clamp(0.3, 3.0));
    }
    if errore_conf.is_none()
        && let Some(u) = &url_corrente
    {
        collega(&webview, &origine, u);
    }

    // Il menu nativo deve restare in vita finché è aperto (su Linux la scelta arriva in modo asincrono).
    let mut menu_aperto: Option<Menu> = None;
    let mut zoom = conf.zoom.unwrap_or(1.0);
    let chiosco = conf.schermo_intero;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => *control_flow = ControlFlow::Exit,
            Event::UserEvent(Evento::DallaPagina { testo, dal_server }) => match ponte::interpreta(&testo) {
                // Pagine locali del client (primo avvio, server non raggiungibile).
                Some(Messaggio::ImpostaServer { url }) if !dal_server => match avvio::normalizza(&url) {
                    Ok(u) => {
                        if let Err(e) = avvio::salva_url(&percorso_conf, u.as_str()) {
                            registra(&percorso_conf, &format!("Salvataggio indirizzo non riuscito: {e}"));
                        }
                        url_corrente = Some(u.clone());
                        collega(&webview, &origine, &u);
                    }
                    Err(e) => {
                        let _ = webview.load_html(&avvio::pagina_indirizzo(&url, &e, &percorso_conf));
                    }
                },
                Some(Messaggio::Riprova) if !dal_server => {
                    if let Some(u) = &url_corrente {
                        collega(&webview, &origine, u);
                    }
                }
                Some(Messaggio::CambiaServer) if !dal_server => {
                    let attuale = url_corrente.as_ref().map(|u| u.to_string()).unwrap_or_default();
                    let _ = webview.load_html(&avvio::pagina_indirizzo(&attuale, "", &percorso_conf));
                }
                // Pagine del server.
                Some(Messaggio::Menu { voci }) if dal_server => {
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
                    avvisa(&webview, &avviso);
                }
                Some(Messaggio::Incolla) if dal_server => {
                    match arboard::Clipboard::new().and_then(|mut c| c.get_text()) {
                        Ok(testo) if testo.len() <= ponte::MASSIMO_APPUNTI => {
                            let _ = webview.evaluate_script(&format!(
                                "window.DuMetodoClient && window.DuMetodoClient.incolla({})",
                                ponte::js_stringa(&testo)
                            ));
                        }
                        Ok(_) => avvisa(&webview, "Testo negli appunti troppo lungo"),
                        Err(_) => avvisa(&webview, "Negli appunti non c'è testo"),
                    }
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
                avvisa(&webview, &testo);
            }
            _ => {}
        }
    });
}

/// Piccolo avviso in basso a destra nella pagina (lo disegna lo script iniziale).
fn avvisa(webview: &WebView, testo: &str) {
    let _ = webview
        .evaluate_script(&format!("window.__duMetodoAvviso && window.__duMetodoAvviso({})", ponte::js_stringa(testo)));
}

fn costruisci_webview(
    window: &Window,
    conf: &Configurazione,
    origine: Origine,
    pagina_iniziale: &str,
    proxy: EventLoopProxy<Evento>,
) -> wry::Result<WebView> {
    let origine_nav = origine.clone();
    let origine_fin = origine.clone();
    let origine_ipc = origine;
    let proxy_ipc = proxy.clone();
    let proxy_fin = proxy.clone();
    let proxy_dl = proxy;
    let regole_dl = conf.download.clone();

    let builder = WebViewBuilder::new()
        .with_user_agent(format!(
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 DuMetodoClient/{VERSIONE}"
        ))
        .with_devtools(conf.strumenti_sviluppatore && !conf.schermo_intero)
        .with_clipboard(true)
        .with_html(pagina_iniziale)
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
                    // Un solo "browser": le pagine del server si aprono nella stessa finestra.
                    Destinazione::Interna => {
                        let _ = proxy_fin.send_event(Evento::Naviga(indirizzo));
                    }
                    Destinazione::Esterna => {
                        let _ = open::that_detached(&indirizzo);
                    }
                    Destinazione::Bloccata => {}
                }
            }
            NewWindowResponse::Deny
        })
        .with_ipc_handler(move |richiesta| {
            let da = richiesta.uri().to_string();
            let dal_server = origine_ipc.borrow().as_ref().is_some_and(|o| {
                matches!(sicurezza::classifica(o, &da), Destinazione::Interna) && da.starts_with("http")
            });
            let _ = proxy_ipc.send_event(Evento::DallaPagina { testo: richiesta.body().clone(), dal_server });
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

    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return builder.build(window);
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        match window.default_vbox() {
            Some(vbox) => builder.build_gtk(vbox),
            None => Err(wry::Error::MessageSender),
        }
    }
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
