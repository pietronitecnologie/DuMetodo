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
| **Tracciabilità e qualità**: batterie, cavi dai banchi, lotti, controllo qualità, foto | ✅ |
| **Report**: dashboard, analisi produttività, materiale per reparto/settimana, controllo prezzi | ✅ |
| **Utilità**: errori di importazione, ristampa etichette da DDT, annunci, scorporazione di massa, cambia ordine | ✅ |
| **Permessi granulari** per funzione, pagina Utenti e permessi | ✅ |
| **Migrazione dei dati da Metodo** (ordini, magazzino, tracciabilità, utenti) | ✅ |
| Client desktop (browser personalizzato in Rust) | ⏳ da avviare |

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
| Credenziali e infrastruttura | variabili d'ambiente | password SMTP e IMAP, database |
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

* **Elementi tracciati:** ogni elemento fisico (batteria, cavo, generico) ha un codice UNICO e un tipo
  esplicito. In Metodo il tipo si esprimeva con righe d'ordine finte e ogni evento creava un nuovo item.
* **Batterie:** primo ingresso (codice generato `prefisso + 6 caratteri esadecimali`, prefisso in
  Configurazione, unicità garantita dal database) o ricarica (solo di batterie note). Una batteria
  senza eventi da N giorni (default 115) riceve un **work order** di ricarica, chiuso dalla ricarica
  stessa o a mano. Il controllo gira ogni giorno (Celery beat, `configura_pianificazioni`).
* **Tracce generiche:** chiave = valore su un codice; a un codice esistente si aggiungono eventi.
* **Lotto/busta** dalla pagina dell'ordine; un doppio invio non crea doppioni.
* **Etichette:** Code128 generato nel browser (JsBarcode, in `static/vendor`), pagina di stampa.
* **Controllo qualità:** i **test** (con i loro **controlli**) si definiscono in Amministrazione. Un
  controllo è "campione/KO" (numeri interi, KO ≤ campione) oppure "presente/assente". Distinta e
  disegno sono controlli di presenza con codice `distinta` e `disegno` (in Metodo: id 8 e 9 nel codice).
* **Foto e file per ordine:** dal telefono le foto vengono ridotte a 1024 px prima dell'invio. Sono
  ammessi JPEG, PNG, GIF, WebP e PDF (tipo verificato dal contenuto) fino alla dimensione configurata.
  I file stanno in `MEDIA_ROOT` (mai servito pubblicamente) e si scaricano solo da una vista che
  controlla i permessi.
* **Liste:** filtri in GET, paginazione ed **esportazione xlsx degli stessi dati filtrati**; gli
  intervalli di date comprendono il giorno finale.
* **Permessi:** il Fornitore registra; il Cliente consulta batterie, cavi, controlli qualità e quanto
  collegato ai suoi ordini.
* Reclami e test display di Metodo non sono stati riportati: funzioni non più usate.

### Protocollo dei banchi di prova (API cavi)

Sostituisce `insertTracciaCavi` di Metodo, che era una GET anonima e creava ordini vuoti.

1. Un amministratore crea la **postazione** (Amministrazione → Postazioni, ex asset) e genera il
   **token** da *Tracciabilità e qualità → Postazioni e token*. Il token si vede una volta sola; nel
   database c'è solo la sua impronta SHA-256. Rigenerarlo o revocarlo disattiva il precedente.
2. Il banco invia `POST /tracciabilita/api/cavi/` (form-urlencoded) con l'intestazione
   `X-Postazione-Token: <token>`. Solo per banchi che non sanno impostare intestazioni: parametro
   `token=<token>` (finisce nei log del server web: preferire l'intestazione). Le GET sono rifiutate,
   a meno che il parametro *API postazioni: accetta GET* sia attivo.

| Parametro | Obbligatorio | Significato |
|---|---|---|
| `ordine` | sì (oppure `serie`, `numero`, `anno`, `appendice`) | `serie.numero[.appendice]-anno`, es. `00040.0004300-2026` |
| `prodotto` | sì | codice del cavo (chiave della traccia) |
| `misura` | sì | lunghezza misurata (valore) |
| `data` | sì | `AAAA/MM/GG,hh:mm:ss`, `AAAA/MM/GG` o ISO `AAAA-MM-GGThh:mm:ss` (ora locale) |
| `lotto` (o `lottoCavo`) | no | lotto della bobina |
| `reparto` (o `location`) | no | codice reparto; default il reparto della postazione |
| `asset` | no | se presente deve coincidere con il codice della postazione del token |

Risposte JSON:

| Stato | Quando | Corpo |
|---|---|---|
| 201 | traccia creata | `{"esito": "creata", "id": 12, "codice": "CV3F9A1C", "ordine": "00040.0004300-2026"}` |
| 200 | stesso dato già ricevuto (stessa postazione, ordine, data, prodotto, misura) | come sopra, `"esito": "duplicata"` |
| 400 | parametri non validi | `{"errore": "...", "campi": {...}}` |
| 401 | token mancante, errato o postazione disattivata | `{"errore": "..."}` |
| 404 | ordine inesistente (l'ordine **non** viene creato) | `{"errore": "Ordine ... non trovato.", ...}` |
| 405 | metodo non ammesso | `{"errore": "..."}` |

```bash
curl -X POST https://dumetodo.esempio.it/tracciabilita/api/cavi/ -H "X-Postazione-Token: $TOKEN" \
  -d ordine=00040.0004300-2026 -d prodotto=CV-AWG18 -d misura=1250 -d lotto=L-9 -d "data=2026/03/01,10:15:30"
```

Il pulsante **Intestato** della lista cavi registra l'evento sullo stesso cavo, una volta sola (in
Metodo creava un nuovo codice a ogni clic).

## Permessi

Ogni funzione del sito è un permesso assegnabile al singolo utente dalla pagina **Amministrazione → Utenti e
permessi**, raggruppato per sezione (Produzione, Magazzino, Tracciabilità e qualità, Report, Utilità,
Amministrazione). I **profili** (Operatore, Caporeparto, Amministratore, Cliente) sono punti di partenza:
applicati a un utente, gli assegnano un insieme di permessi che poi si personalizza funzione per funzione.
Il catalogo delle funzioni è in `dumetodo/core/permessi.py`; un test controlla che ogni pagina richieda
login e permesso.

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

## Produzione (Docker)

```bash
cp .env.example .env          # compila tutti i valori
docker compose up -d --build  # web (gunicorn), worker Celery, beat, PostgreSQL, Redis
docker compose run --rm web python manage.py createsuperuser
```
