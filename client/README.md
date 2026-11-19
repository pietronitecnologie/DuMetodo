# DuMetodo Client

Browser desktop personalizzato per DuMetodo, scritto in Rust (`wry` + `tao` + `muda`).

**La grafica resta sul server**: il client mostra le pagine di DuMetodo e non ha un'interfaccia
propria. Aggiunge solo ciò che un browser normale non può fare:

| Funzione | Dove si configura |
|---|---|
| Menu del tasto destro **nativi**, diversi per tipo di elemento (ordine, riga, pagina…) e filtrati per i permessi dell'utente | sul **server**: Configurazione → Client desktop |
| Aggiorna pagina, Copia tabella / colonna / riga (si incollano in Excel), Copia selezione, Incolla (anche su http) | voci predefinite del menu, modificabili sul server |
| **Logo dell'azienda** in testa al menu del tasto destro (con il nome) | sul **server**: Configurazione → Azienda → Logo |
| **Aggiornamento automatico**: l'amministratore carica il nuovo `dumetodo-client.exe` in *Amministrazione → Aggiornamenti del client* con il numero di versione; ogni client più vecchio mostra «È disponibile un aggiornamento», con *Aggiorna ora* lo scarica, controlla l'impronta SHA-256 e si riavvia riaprendo le schede. Il client deve stare in una cartella dove l'utente può scrivere (non in Programmi) | sul **server** |
| **Riapertura delle schede**: alla chiusura il client ricorda le schede aperte (file `schede.json` accanto a `client.toml`) e le riapre al prossimo avvio sullo stesso server; l'accesso resta valido. `ripristina_schede = false` in `client.toml` per disattivarla | sul client |
| **Scorciatoie da tastiera** (F1 elenco, F2 cerca ordine, F3 ordini, …) | sul **server**: Configurazione → Client desktop → Scorciatoie |
| **Cambia server**: il pulsante a destra nella barra delle schede mostra il server collegato; cliccandolo si indica un altro indirizzo (Annulla = resta su quello attuale). In alternativa `url` in `client.toml` o `--url` | sul client |
| **Schede**: più pagine nella stessa finestra. Ctrl+T nuova, Ctrl+W chiudi, Ctrl+Tab / Ctrl+PagSu-PagGiù per passare, Ctrl+clic o clic centrale su un link e *Apri in nuova scheda* dal tasto destro; clic centrale sulla scheda per chiuderla. Un solo accesso vale per tutte le schede | sul client |
| Navigazione limitata al server configurato; i link esterni si aprono nel browser di sistema | automatico |
| Download salvati in cartelle scelte per regola (es. la bolla direttamente nella cartella di importazione del gestionale) | sul **PC**: `client.toml` |
| Modalità chiosco (schermo intero, senza strumenti di sviluppo) per le postazioni di reparto | sul **PC**: `client.toml` |
| Stampa (Ctrl+P), zoom (Ctrl + / − / 0), schermo intero (F11), indietro/avanti (Alt + ←/→) | automatico |

Le cartelle dei download sono decise **solo** dal file locale: il server non può scegliere dove scrivere
sul PC.

## Come funzionano i menu

1. Le pagine di DuMetodo marcano gli elementi: `data-dm-menu="ordine" data-dm-id="12" data-dm-codice="..."`.
2. Quando la pagina è aperta dal client (che si presenta con `DuMetodoClient/<versione>` nello user agent),
   DuMetodo include `static/client/client.js`, che legge i menu da `/client/configurazione.json`.
3. Al clic destro lo script compone le voci e chiede al client di mostrare un **menu nativo**. Alla scelta
   il client richiama `DuMetodoClient.esegui(id)` nella pagina, che esegue l'azione: naviga, post con
   CSRF, copia, stampa, ricarica, indietro, avanti, schermo intero.

Il client accetta messaggi solo dalle pagine del server configurato e conosce soltanto questi comandi:
mostra menu, stampa, schermo intero, zoom. Non esegue nient'altro.

## Configurazione del PC

Al primo avvio viene creato `client.toml`, da compilare:

- **Windows:** `%APPDATA%\DuMetodo\DuMetodo Client\config\client.toml`
- **Linux:** `~/.config/dumetodoclient/client.toml`

```toml
url = "https://dumetodo.esempio.it"
schermo_intero = false   # true = chiosco
zoom = 1.0

[download]
cartella = ""            # vuoto = cartella Download

[[download.regole]]
contiene = "/consegne/"  # la bolla generata alla consegna...
cartella = 'C:\ReadyPro\Import'   # ...va direttamente qui
```

Opzioni da riga di comando: `--url https://...` e `--configurazione percorso\client.toml`.

## Compilazione

```bash
cargo build --release          # eseguibile in target/release/
cargo test && cargo clippy --all-targets -- -D warnings
```

Su Linux servono `libwebkit2gtk-4.1-dev` e `libgtk-3-dev`. Su Windows serve WebView2, già presente in
Windows 10 e 11. L'eseguibile per Windows viene compilato automaticamente da GitHub Actions
(workflow `client`) e scaricato come artefatto della build.
