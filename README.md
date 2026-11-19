# DuMetodo

Gestione della **Produzione** del **Fornitore** (l'azienda che possiede DuMetodo): ordini (commesse),
importazione degli ordini inviati dal **Cliente**,
comunicazione con il cliente tramite note. È la riscrittura completa dell'app `Prod` di
[Metodo](https://github.com/pietronitecnologie/Metodo) (branch `Metodo`), con Django 5.2 LTS.

L'analisi funzionale del codice originale, che fa da specifica per la riscrittura, è in
[`docs/analisi-metodo/`](docs/analisi-metodo/).

## Stato dei lavori

Tutte le funzioni dell'app `Prod` di Metodo sono state riscritte.

| Area | Stato |
|---|---|
| Fondamenta: reparti, audit, Docker, CI | ✅ |
| **Configurazione** (sostituisce la tabella `Configuration`), nome azienda e Cliente | ✅ |
| **Ordini**: chiave univoca, macchina a stati, azioni sulle righe, divisione, scadenze | ✅ |
| **Importazione ordini**: file TXT, casella email, problemi da gestire | ✅ |
| **Note / comunicazione con il Cliente** | ✅ |
| **Tracciati**: formato dei file per il gestionale definibile in Configurazione (default ReadyPro) | ✅ |
| **Consegna** con bolla, **DDT** con file tecnico, **etichette** | ✅ |
| **Anagrafica articoli**, **agenda** ordini e pulsanti, **pianificazione** con report email, **tabulati** | ✅ |
| **Magazzino**: componenti, inventario, richieste materiali, distinte base, DDT in ingresso | ✅ |
| **Tracciabilità e qualità**: tracce, lotti/buste, controllo qualità, foto | ✅ |
| **Report**: dashboard, analisi produttività, materiale per reparto/settimana, controllo prezzi | ✅ |
| **Utilità**: errori di importazione, ristampa etichette da DDT, annunci, scorporazione di massa, cambia ordine | ✅ |
| **Permessi granulari** per funzione, pagina Utenti e permessi | ✅ |
| **Migrazione dei dati da Metodo** (ordini, magazzino, tracciabilità, utenti) | ✅ |
| **Client desktop** (browser personalizzato in Rust, cartella `client/`): menu del tasto destro nativi definiti dal server | ✅ |

## Regole di progetto

1. **Un ordine è identificato in modo univoco da `serie.numero.appendice.anno`.**
   Il vincolo è nel database (`ordine_chiave_unica`). L'appendice "nessuna" (`0`, vuota o
   NULL in Metodo) è sempre memorizzata come stringa vuota.
2. **Tutti i dati legati a un ordine lo referenziano con una ForeignKey** verso `Ordine`,
   mai ripetendo i quattro campi. Esempi: righe, note, storico scadenze, problemi di importazione.
   Lo stesso varrà per DDT, tracciabilità, controlli qualità, file e ticket.
   In Metodo questi dati si collegavano per stringhe, a volte con regex o `contains` sul solo
   numero, e mescolavano ordini di anni o serie diversi. La chiave in chiaro compare solo dove
   l'ordine non esiste ancora, ad esempio in un problema di importazione di un ordine scartato.
3. **Nessun segreto nel codice né nel database.** Le credenziali stanno nelle variabili
   d'ambiente (`.env`, vedi `.env.example`).
4. **La logica di business sta nei moduli `servizi.py`.** Le viste li chiamano soltanto.
   Ogni operazione è transazionale, blocca le righe che modifica e verifica i permessi lato server.
5. **Nessuna modifica di dati con GET.** Tutte le azioni sono POST con protezione CSRF.
6. **Ogni modifica a ordini, righe e parametri è storicizzata** con l'utente che l'ha fatta
   (django-simple-history).

## Configurazione: come è cambiata

La tabella chiave/valore `Configuration` di Metodo è stata divisa in quattro parti.

| Cosa | Dove sta ora | Esempi |
|---|---|---|
| Credenziali e infrastruttura | variabili d'ambiente | password SMTP, database |
| Casella ordini (IMAP) | Configurazione → Casella ordini | server, utente, password **cifrata** (mai mostrata né storicizzata), mittenti ammessi, pulsante *Prova connessione* |
| Parametri di business | registro tipizzato `configurazione/registro.py`, modificabile dalla pagina **Configurazione** (solo amministratori), con validazione e storico | resa oraria, indirizzi email, serie Fast/Express |
| Capacità dei reparti | campo `Reparto.soglia_ore_settimanali` | ex `SOGLIA_<AREA>` |
| Cursori tecnici | tabelle dedicate | ex `ID_EMAIL` → `StatoCasella` |

Per importare la vecchia tabella: esportala dall'admin di Metodo in CSV o JSON, poi lancia

```bash
python manage.py importa_configurazione_metodo configuration.csv            # prova a secco
python manage.py importa_configurazione_metodo configuration.csv --applica
```

Le password non vengono importate: il comando dice in quale variabile d'ambiente metterle.

## Importazione ordini

* **Origini:** la casella IMAP (ogni 30 minuti, con un solo lettore alla volta) oppure il
  caricamento manuale di file `.txt` dalla pagina **Importazione**.
* **Ogni file viene conservato** con il suo esito. Un file identico a uno già importato viene saltato.
* **Ogni ordine del file è una fotografia completa** ed è applicato in una transazione propria.
  Il confronto avviene per numero di riga:
  * una riga nuova viene creata;
  * se cambiano solo quantità, prezzo o scadenza, la riga viene aggiornata;
  * se cambia l'articolo, la vecchia riga diventa "sostituita" e ne nasce una nuova;
  * una riga sparita dal file viene annullata.
* **Righe già in bolla:** non vengono mai toccate. Le differenze sono segnalate.
* **Ordini consegnati:** la reimportazione viene rifiutata, come in Metodo.
* **Righe illeggibili:** se un ordine ne ha anche una sola, non viene applicato, così nessuna riga
  viene annullata per errore. L'errore va tra i "problemi da gestire" e, se configurato, parte un'email.
* **Variazioni di scadenza:** finiscono tutte nello storico scadenze.
* **Allegati zip:** sono letti in memoria con limiti di dimensione. Non vengono più estratti in
  cartelle condivise, che esponevano al path traversal e allo zip-slip.

## Note e comunicazione con il cliente

* Una nota **condivisa** avvisa la controparte via email:
  * se la scrive il Fornitore, l'email va al Cliente;
  * se la scrive il Cliente, va al Fornitore.
* Una nota **interna** è visibile solo al Fornitore.
* Le email passano da una coda (`EmailInUscita`), salvata nella stessa transazione della nota e
  inviata in background con tentativi ripetuti. Se l'SMTP non risponde, la nota non si perde e
  l'utente non vede errori.
* Elenco ordini: un badge mostra le note non lette.
* Eliminazione: logica, ammessa solo all'autore o a un amministratore.

## Tracciati (file per il gestionale)

Il formato dei file esportati non è più scritto nel codice: si definisce in **Configurazione**
con un piccolo linguaggio, controllato al salvataggio, con anteprima e guida integrata.

```
nome: ReadyPro
codifica: cp1252
fine_riga: CRLF
nome_file: consegna{documento.selezione}-{documento.oggi|data:AAAAMMGG}

[ordine]
Riferimento |ordine {ordine.serie}.{ordine.numero}

[riga]
{riga.articolo}{riga.variante|senza:_|prefisso:.}||{riga.qta|intero}|PZ|{riga.prezzo|compatto|virgola}
```

Il linguaggio si usa oggi per:

* il **file bolla** della consegna (predefinito: ReadyPro, identico a Metodo);
* il **file tecnico DDT** (predefinito: formato di Metodo con codice Fornitore 3649, quantità
  decimali corrette).

## Consegna e DDT

* **Consegna:** è un'operazione POST, in una transazione con le righe bloccate durante
  l'operazione, quindi un doppio clic non consegna due volte.
  * Il file bolla è conservato e riscaricabile dallo storico.
  * Se il file non si può generare (es. un carattere non rappresentabile), non viene consegnato nulla.
* **Righe consegnate:** sono collegate alla riga d'ordine e quindi all'ordine. Restano in attesa di DDT.
* **DDT:**
  * numero + anno sono univoci;
  * il file tecnico è generato con il tracciato, conservato e inviato tramite la coda email
    con allegato;
  * l'amministratore può reinviarlo.
* **Etichette:** stesso xlsx di Metodo. Il codice Fornitore della colonna I si configura e le date
  sono vere date.

## Magazzino

DuMetodo, come Metodo, **non gestisce i movimenti di magazzino**: la giacenza è l'ultimo conteggio
d'inventario. Quando un materiale arriva con un DDT in ingresso (inserito a mano o importato), le
richieste materiali aperte per lo stesso codice e variante, di qualsiasi reparto, vengono **chiuse
automaticamente**; sulla richiesta resta la riga del DDT che l'ha chiusa. La giacenza non cambia.

## Tracciabilità e qualità

Menu **Tracciabilità e qualità** (app `dumetodo.tracciabilita`). La pagina di ogni ordine mostra
tracce, lotto/busta, controlli qualità, foto e file dell'ordine.

* **Elementi tracciati:** ogni elemento fisico ha un codice UNICO; in Metodo ogni evento creava un nuovo item.
* **Tracce generiche:** chiave = valore su un codice; a un codice esistente si aggiungono eventi.
* **Lotto/busta** dalla pagina dell'ordine; un doppio invio non crea doppioni.
* **Etichette:** Code128 generato nel browser (JsBarcode, in `static/vendor`), pagina di stampa.
* **Controllo qualità:** i **test** (con i loro **controlli**) si definiscono in Amministrazione → Test e controlli qualità. Un
  controllo è "campione/KO" (numeri interi, KO ≤ campione) oppure "presente/assente". Distinta e
  disegno sono controlli di presenza con codice `distinta` e `disegno` (in Metodo: id 8 e 9 nel codice).
* **Foto e file per ordine:** dal telefono le foto vengono ridotte a 1024 px prima dell'invio. Sono
  ammessi JPEG, PNG, GIF, WebP e PDF (tipo verificato dal contenuto) fino alla dimensione configurata.
  I file stanno in `MEDIA_ROOT` (mai servito pubblicamente) e si scaricano solo da una vista che
  controlla i permessi.
* **Liste:** filtri in GET, paginazione ed **esportazione xlsx degli stessi dati filtrati**; gli
  intervalli di date comprendono il giorno finale.
* **Permessi:** il Fornitore registra; il Cliente consulta i controlli qualità e quanto è collegato
  ai suoi ordini.
* Non riportati da Metodo perché non più usati: reclami, test display, batterie (con i work order di
  ricarica), cavi e postazioni dei banchi di prova (API `insertTracciaCavi`), *Attiva produzione*.

## Permessi

Ogni funzione del sito è un permesso assegnabile al singolo utente dalla pagina **Amministrazione → Utenti e
permessi**, raggruppato per sezione (Produzione, Magazzino, Tracciabilità e qualità, Report, Utilità,
Amministrazione). I **profili** (Operatore, Caporeparto, Amministratore, Cliente) sono punti di partenza:
applicati a un utente, gli assegnano un insieme di permessi che poi si personalizza funzione per funzione.
Il catalogo delle funzioni è in `dumetodo/core/permessi.py`; un test controlla che ogni pagina richieda
login e permesso.

**Utenti e permessi** ha quattro permessi propri: *vedere*, *creare e modificare utenti*, *reimpostare le
password*, *assegnare permessi e profili*. Regole: si possono dare solo i permessi che si hanno; un utente
con più permessi di chi lo gestisce (o un superuser) si può solo vedere; non ci si può disattivare né togliersi
i permessi dell'area; il ruolo superuser lo cambia solo un superuser. Le password temporanee (generate,
mostrate una volta) e quelle dei nuovi utenti vanno cambiate al primo accesso; ognuno cambia la propria da
*Cambia password* nel menu utente. Ogni modifica finisce nello storico dell'utente.

Tutto si gestisce da DuMetodo: utenti, reparti, test e controlli qualità, articoli. L'admin di Django
(menu *Admin tecnico*, solo superuser) serve solo per interventi tecnici.

Da riga di comando: `python manage.py assegna_profilo <utente> Amministratore`.

Il gruppo **Cliente** indica solo l'identità: chi ne fa parte scrive le note come Cliente e non vede quelle
interne. Nella migrazione da Metodo i gruppi `Geko`, `GekoCapRep`, `Geko Admin` e `Customer` diventano i
profili Operatore, Caporeparto, Amministratore e Cliente (unione dei permessi se l'utente era in più gruppi).

## Migrazione da Metodo

```bash
python manage.py migrate && python manage.py crea_ruoli
python manage.py importa_da_metodo --db postgres://utente:password@host/metodo            # prova a secco + report
python manage.py importa_da_metodo --db postgres://utente:password@host/metodo --applica
python manage.py importa_configurazione_metodo configuration.csv --applica
```

* **Lettura di Metodo:** in sola lettura, controllando prima che lo schema contenga le colonne necessarie.
* **Dati importati:**
  * utenti, con le stesse password e i gruppi convertiti nei nuovi ruoli;
  * reparti, ordini e righe;
  * note, segnate come già lette;
  * storico scadenze;
  * righe consegnate con i relativi DDT.
  * tracciabilità e qualità: postazioni (senza token), elementi e tracce, test e controlli, controlli
    qualità, file per ordine (reclami e test display non vengono importati). I file non vengono
    copiati: copia la cartella `file/` della cartella media di Metodo in `MEDIA_ROOT`.
* **Ordini duplicati in Metodo:** quelli con la stessa chiave vengono fusi in uno solo e segnalati nel report.

## Sviluppo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # imposta DATABASE_URL verso un PostgreSQL locale
python manage.py migrate && python manage.py crea_ruoli && python manage.py configura_pianificazioni
python manage.py runserver
pytest                        # test (richiede PostgreSQL)
ruff check . && ruff format --check .
```

## Avvio rapido su un PC senza Docker (Windows)

Serve solo Python 3.12 o più recente (`winget install Python.Python.3.12`).

```powershell
git clone https://github.com/pietronitecnologie/dumetodo.git
cd dumetodo
powershell -ExecutionPolicy Bypass -File .\avvia-dumetodo-semplice.ps1
```

Lo script crea l'ambiente Python, genera `.env` con segreti casuali, usa un database **SQLite** in `dati\`,
crea l'utente `admin` (password stampata una sola volta) e avvia il server (waitress) su
`http://localhost:8000`: la finestra resta aperta finché DuMetodo è attivo. Per riavviarlo, rilancia lo script.

Per provarlo con **dati di prova realistici** (48 ordini importati da un file del Cliente, consegne,
DDT, note, tracce, controlli qualità, magazzino, annunci, ore lavorate e utenti `operatore`,
`caporeparto`, `cliente`) aggiungi `-DatiDiProva` (`--dati-di-prova` con lo script .sh), oppure
`python manage.py carica_dati_demo`. Funziona solo su un database senza ordini.

Limiti rispetto all'installazione Docker: niente lavori periodici (lettura automatica della casella
ordini e nuovi tentativi automatici delle email non riuscite; la casella si legge col pulsante in Importazione),
e un solo PC fa da server.
Per l'uso in produzione con più utenti preferisci Docker/PostgreSQL. Per fare il backup basta copiare `dati\`.

## Avvio rapido su un PC (Docker)

Serve solo [Docker Desktop](https://www.docker.com/products/docker-desktop/) avviato.

```powershell
git clone https://github.com/pietronitecnologie/dumetodo.git
cd dumetodo
powershell -ExecutionPolicy Bypass -File .\avvia-dumetodo.ps1
```

Su Linux/macOS: `./avvia-dumetodo.sh`. Lo script:

1. crea `.env` con chiavi e password casuali (solo la prima volta; uso in rete locale via http);
2. avvia database, Redis, web, worker e beat (`docker compose up -d --build`) e applica le migrazioni;
3. crea l'utente `admin` con una password casuale e la stampa **una sola volta**;
4. collega il client desktop a `http://localhost:8000` e lo apre (o apre il browser).

Gli altri PC della rete raggiungono DuMetodo su `http://<nome-del-pc>:8000`.
Fermare: `docker compose down` (i dati restano nei volumi). Riavviare: rilanciare lo script.

## Produzione (Docker)

```bash
cp .env.example .env          # compila tutti i valori
docker compose up -d --build  # web (gunicorn), worker Celery, beat, PostgreSQL, Redis
docker compose exec web python manage.py crea_amministratore
```
