# MetodoConsole

Console di controllo del server di produzione di Metodo v2: Docker Compose su Debian, con PostgreSQL nello stesso
compose. Un solo comando, `metodoconsole`, per:

- vedere lo **stato** dei servizi;
- fare una **diagnostica** completa del server, di Docker e dell'applicazione;
- fare **backup**, verificarli e **ripristinarli** (database PostgreSQL, file caricati, `.env`);
- **aggiornare** Metodo v2 in sicurezza (backup prima, controllo dopo);
- ricevere **avvisi via email** quando qualcosa non va.

Usa solo Python 3.11 o più recente (quello di Debian 12) e i comandi `docker compose`, `git`, `tar` e,
facoltativamente, `rsync`. Non serve installare altro.

## Installazione

Sul server, dalla cartella di Metodo v2 (es. `/opt/metodo`, quella con `compose.yaml` e `.env`):

```bash
sudo ./MetodoConsole/installa.sh
```

Lo script:

- crea il comando `/usr/local/bin/metodoconsole`;
- scrive la configurazione `/etc/metodoconsole/metodoconsole.conf` (solo se non c'è già);
- crea la cartella dei backup `/var/backups/metodo` (accessibile solo a root);
- mette i permessi `600` al file `.env`, che contiene le password;
- attiva due timer di systemd:

| Timer | Quando | Cosa fa |
|---|---|---|
| `metodoconsole-backup.timer` | ogni notte alle 02:30 | backup, verifica, rotazione, copia remota; email se non riesce |
| `metodoconsole-controllo.timer` | ogni 15 minuti | diagnostica completa; email se trova problemi |

Poi controlla la configurazione e, in Metodo v2, imposta chi riceve gli avvisi:
**Configurazione → Indirizzi email → Destinatari avvisi del server**.

## Comandi

| Comando | Cosa fa |
|---|---|
| `metodoconsole stato` | Servizi in esecuzione e ultimo backup |
| `metodoconsole diagnostica` | Controllo completo (vedi sotto). Uscita: 0 tutto ok, 1 attenzione, 2 errore. `--json` per altri strumenti di monitoraggio |
| `metodoconsole backup` | Backup completo, verificato. `--nota testo` per un backup da conservare (es. prima di una modifica importante), `--solo-database` |
| `metodoconsole backup-elenco` | Elenco dei backup con dimensione, versione di Metodo v2 e nota |
| `metodoconsole backup-verifica NOME` | Controlla che un backup sia integro e leggibile |
| `metodoconsole ripristina NOME` | Ripristina database e file da un backup (chiede di scrivere RIPRISTINA). `--solo-database` lascia i file caricati come sono |
| `metodoconsole aggiorna` | Scarica il codice nuovo, fa un backup, ricostruisce e riavvia, poi la diagnostica |
| `metodoconsole avvia` / `ferma` / `riavvia [servizio]` | Avvia, ferma o riavvia Metodo v2 o un solo servizio (`db`, `redis`, `web`, `worker`, `beat`). `avvia --ricostruisci` ricostruisce l'immagine |
| `metodoconsole log [servizio] [-f]` | Log dei servizi; `--errori` mostra solo gli errori delle ultime 24 ore; `--da 2h` |
| `metodoconsole manage COMANDO` | Comando di Django, es. `metodoconsole manage changepassword admin` |
| `metodoconsole psql` | Console SQL del database |
| `metodoconsole prelievo verifica` / `avvia` / `stato` / `log [-f]` / `ferma` | Preleva i dati dal Metodo in produzione (vedi sotto) |

Dove serve un `NOME` di backup basta l'inizio (`20261120`), una parte della nota (`prima-di-abc1234`) oppure
`ultimo`.

## Prelievo dei dati da Metodo

Importa anni di produzione direttamente dal database di Metodo: lo fa a blocchi, con un limite di banda, e si
può interrompere e riprendere (dettagli nel README di Metodo v2, «Prelievo dal Metodo in produzione»).

1. Metti l'indirizzo del database di Metodo nel `.env` di Metodo v2 e riavvia (`metodoconsole riavvia`):
   `METODO_DB_URL=postgres://utente-sola-lettura:password@host:5432/metodo`.
   * Se non lo vuoi nel `.env`, MetodoConsole lo chiede senza mostrarlo a schermo, oppure lo legge dalla
     variabile `METODO_DB_URL`.
   * Se il database di Metodo non è raggiungibile in rete, apri un tunnel SSH sul server:
     `ssh -C -N -L 0.0.0.0:15432:localhost:5432 utente@server-metodo`. Nell'indirizzo usa poi
     `host.docker.internal:15432`.
2. `metodoconsole prelievo verifica`: controlla la connessione e lo schema, e mostra quanti record ci sono.
3. `metodoconsole prelievo avvia`: fa un backup di sicurezza del database, poi avvia il prelievo in un
   container dedicato.
   * Il prelievo continua anche se chiudi la sessione SSH.
   * Opzioni: `--max-kb 256 --pausa 1` per pesare meno sulla rete di Metodo; `--ricomincia ordini righe
     consegne` per rileggere e aggiornare i dati già presi.
4. Per seguirlo:
   * `metodoconsole prelievo log -f` mostra l'avanzamento, con percentuale e tempo stimato;
   * `metodoconsole prelievo stato` mostra a che punto è ogni passo.
5. `metodoconsole prelievo ferma` lo interrompe in modo pulito: il blocco in corso viene annullato.
   `prelievo avvia` riparte da dove era arrivato.

Rilanciato nei giorni seguenti, `prelievo avvia` prende solo i record nuovi. Il giorno del passaggio basta un
ultimo lancio con Metodo fermo. Tutti gli avvisi del prelievo (ordini duplicati, righe senza ordine…)
finiscono nel file `prelievo-metodo.log` nella cartella dei file caricati, che è inclusa nei backup.
`metodoconsole diagnostica` segnala se un prelievo si è interrotto. A migrazione finita togli
`METODO_DB_URL` dal `.env`.

## Diagnostica

**Server e Docker**

- Docker attivo.
- `.env` presente e non leggibile da altri utenti.
- Ogni servizio in esecuzione, sano e senza riavvii continui.
- Migrazione all'avvio riuscita.
- Risposta di `/salute/`, cioè database e cache raggiungibili dall'applicazione.
- Errori nei log dell'ultima ora.
- Indirizzo pubblico raggiungibile e giorni di validità del certificato https, se indicato nella configurazione.
- Spazio libero sul disco di sistema, su quello di Docker e su quello dei backup.
- Età dell'ultimo backup.
- Sincronizzazione dell'orologio.

**Applicazione** (comando `manage.py diagnostica` dentro il container)

- PostgreSQL: versione, dimensione, connessioni.
- Migrazioni non applicate.
- Redis.
- Worker Celery che rispondono.
- Lavori periodici eseguiti di recente.
- Casella ordini: ultima lettura ed errori.
- Email in errore o ferme in coda.
- Problemi di importazione da gestire.
- Cartella dei file caricati scrivibile.
- Impostazioni di sicurezza: DEBUG, chiave segreta, https, presenza di un amministratore.

Gli avvisi email partono quando compare un problema o cambia, si ripetono ogni 6 ore finché dura, e un'ultima
email avvisa quando tutto è tornato a posto.

## Backup

Ogni backup è una cartella in `/var/backups/metodo/AAAAMMGG-HHMMSS[-nota]/` con:

| File | Contenuto |
|---|---|
| `database.dump` | il database (`pg_dump` in formato custom, compresso) |
| `file.tar.gz` | i file caricati: foto, allegati, bolle |
| `env` | copia del `.env` (permessi 600): contiene la chiave che cifra la password della casella ordini |
| `manifesto.json` | data, versione di Metodo v2 e di PostgreSQL, numero di tabelle e file, impronte SHA-256 |

Un backup si scrive in una cartella temporanea e diventa visibile solo se è completo. Subito dopo viene
verificato: impronte e lettura con `pg_restore`.

**Rotazione.** Si tiene l'ultimo backup di ognuno degli ultimi 14 giorni, 8 settimane e 12 mesi (si cambia
in configurazione). L'ultimo non viene mai eliminato.

**Copia su un altro computer.** Un backup sullo stesso disco del server non protegge da un guasto del disco.
Indica in `copia_remota` una destinazione rsync, es. `backup@nas.azienda.local:/backup/metodo`. Serve una
chiave SSH senza password per root verso quel computer. In alternativa, fai copiare `/var/backups/metodo`
dal sistema di backup aziendale.

## Ripristino

```bash
metodoconsole backup-elenco
```

```bash
metodoconsole ripristina 20261120-023000
```

MetodoConsole:

1. verifica il backup;
2. fa un **backup di sicurezza** dei dati attuali (nota `prima-del-ripristino`);
3. ferma web, worker e beat;
4. ricrea il database e carica i dati;
5. rimette i file caricati;
6. riavvia Metodo v2, che applica le migrazioni del codice attuale.

Se un ripristino è stato un errore, si torna indietro ripristinando il backup `prima-del-ripristino`.

**Su un server nuovo** (es. dopo un guasto):

1. Installa Docker e Metodo v2.
2. Copia la cartella del backup in `/var/backups/metodo/`.
3. Copia il file `env` del backup in `.env` di Metodo v2. Così restano valide la chiave segreta e la password
   cifrata della casella ordini.
4. Esegui `metodoconsole avvia` e poi `metodoconsole ripristina NOME`.

## Aggiornamento

```bash
metodoconsole aggiorna
```

1. Controlla che non ci siano file modificati a mano.
2. Scarica il codice nuovo (`git fetch`). Se non c'è niente di nuovo si ferma.
3. Fa un backup con la nota `prima-di-<versione>`.
4. Aggiorna il codice, ricostruisce le immagini e riavvia; le migrazioni partono da sole.
5. Aspetta che Metodo v2 torni in salute ed esegue la diagnostica.

Se qualcosa va storto stampa i comandi esatti per tornare alla versione precedente con i dati di prima.

## Configurazione

Il file è `/etc/metodoconsole/metodoconsole.conf`; l'esempio commentato è
[`metodoconsole.conf.example`](metodoconsole.conf.example). Si può usare un altro file con
`metodoconsole --conf FILE …` o la variabile `METODOCONSOLE_CONF`.

I log dei controlli automatici si leggono con:

```bash
journalctl -u metodoconsole-controllo -u metodoconsole-backup
```
