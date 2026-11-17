# DuMetodo

Gestione della **Produzione** del **Fornitore** (l'azienda che possiede DuMetodo): ordini (commesse),
importazione degli ordini inviati dal **Cliente**,
comunicazione con il cliente tramite note. È la riscrittura completa dell'app `Prod` di
[Metodo](https://github.com/pietronitecnologie/Metodo) (branch `Metodo`), con Django 5.2 LTS.

L'analisi funzionale del codice originale, che fa da specifica per la riscrittura, è in
[`docs/analisi-metodo/`](docs/analisi-metodo/).

## Stato dei lavori

| Area | Stato |
|---|---|
| Fondamenta: ruoli, reparti, audit, Docker, CI | ✅ fase 1 |
| **Configurazione** (sostituisce la tabella `Configuration`) | ✅ fase 1 |
| **Ordini**: modello, macchina a stati, azioni sulle righe, divisione, scadenze | ✅ fase 1 |
| **Importazione ordini**: file TXT, casella email, problemi da gestire | ✅ fase 1 |
| **Note / comunicazione con il cliente** | ✅ fase 1 |
| Consegna, bolla ReadyPro, DDT file tecnico, etichette | ⏳ fase 2 |
| Agenda ordini, pianificazione, tabulati | ⏳ fase 3 |
| Magazzino, distinte base, richieste materiali, DDT in ingresso | ⏳ fase 4 |
| Tracciabilità, controllo qualità, reclami, annunci | ⏳ fase 5 |
| Migrazione dei dati da Metodo | ⏳ con la fase 2 |

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

## Ruoli

Personale del Fornitore, con gerarchia: **Amministratore** ⊃ **Caporeparto** ⊃ **Operatore**.
Utenti del Cliente: **Cliente**. I gruppi di Metodo (`Geko Admin`, `GekoCapRep`, `Geko`, `Customer`)
vengono rinominati automaticamente dalla migrazione `core/0002`.

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
