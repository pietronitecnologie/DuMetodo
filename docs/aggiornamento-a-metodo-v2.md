# Aggiornare un'installazione di DuMetodo a Metodo v2

Il progetto si chiama ora **Metodo v2**. Il nome tecnico è **`metodo`** e vale per pacchetto Python, database,
utente PostgreSQL, progetto Docker Compose, cartelle, client e pacchetto `.deb`. Le installazioni fatte prima
della rinomina vanno spostate **una volta** sui nuovi nomi. I dati si portano con un backup e un ripristino di
MetodoConsole.

Restano validi, senza fare niente:

- le **password salvate**, per esempio quella della casella ordini, purché si tenga la stessa
  `DJANGO_SECRET_KEY` (o la vecchia `DUMETODO_CHIAVE_SEGRETI`, ancora letta);
- i **codici personali** delle timbrature e i **PC associati** alle postazioni;
- la configurazione di MetodoConsole: la vecchia sezione `[dumetodo]` viene ancora letta.

## Server (Docker su Debian)

Esegui tutti i comandi come root, nella cartella dell'installazione (es. `/opt/dumetodo`).

1. **Backup con la versione attuale**, prima di scaricare il codice nuovo:

   ```bash
   metodoconsole backup --nota prima-di-metodo-v2
   ```

2. **Ferma i container vecchi.** Il progetto Compose si chiamava come la cartella (di solito `dumetodo`). I
   volumi restano: puoi eliminarli quando tutto funziona.

   ```bash
   docker compose -p dumetodo down
   ```

3. **Scarica il codice nuovo** e, se vuoi, rinomina la cartella in `/opt/metodo`:

   ```bash
   git pull
   ```

   ```bash
   cd .. && mv dumetodo metodo && cd metodo
   ```

4. **Aggiorna il file `.env`**:
   - `DATABASE_URL` deve usare utente e database `metodo`, con la stessa password di `POSTGRES_PASSWORD`:
     `DATABASE_URL=postgres://metodo:<POSTGRES_PASSWORD>@db:5432/metodo`;
   - se c'era `DUMETODO_CHIAVE_SEGRETI`, rinominala in `METODO_CHIAVE_SEGRETI` (funziona anche col vecchio nome);
   - lascia **invariata** `DJANGO_SECRET_KEY`.

5. **Reinstalla MetodoConsole** e controlla la configurazione:

   ```bash
   sudo ./MetodoConsole/installa.sh /opt/metodo
   ```

   In `/etc/metodoconsole/metodoconsole.conf`:
   - `cartella` deve essere la nuova cartella;
   - `db_nome` e `db_utente` devono valere `metodo`;
   - `progetto` va lasciato vuoto.

   La cartella dei backup può restare quella vecchia (`/var/backups/dumetodo`): il backup del punto 1 è lì.

6. **Avvia Metodo v2.** Il progetto si chiama `metodo` e crea volumi nuovi, quindi un database vuoto:

   ```bash
   metodoconsole avvia --ricostruisci
   ```

7. **Ripristina i dati** dal backup del punto 1: database e file caricati.

   ```bash
   metodoconsole ripristina prima-di-metodo-v2
   ```

8. **Controlla:**

   ```bash
   metodoconsole diagnostica
   ```

   Accedi con un utente e verifica ordini, tracciature e file. Quando tutto funziona, elimina i volumi vecchi:

   ```bash
   docker volume rm dumetodo_pgdata dumetodo_media
   ```

## Client desktop

Il client si chiama ora **Metodo v2 Client** (`metodo-client.exe`, pacchetto `metodo-client`) e salva la
configurazione in una cartella nuova. L'aggiornamento automatico non può cambiare il nome del programma, quindi
il client va **reinstallato una volta**.

- **Windows**:
  1. Pubblica la nuova versione da **Amministrazione → Aggiornamenti del client** e scaricala.
  2. Copia `%APPDATA%\DuMetodo\DuMetodo Client\config\client.toml` in
     `%APPDATA%\Metodo\Metodo Client\config\`. Così restano indirizzo del server e impostazioni; in alternativa
     al primo avvio il client chiede l'indirizzo.
  3. Elimina il vecchio `dumetodo-client.exe` e i suoi collegamenti.
- **Debian/Ubuntu**: installa il nuovo pacchetto, che sostituisce da solo quello vecchio.

  ```bash
  sudo apt install ./metodo-client_<versione>_amd64.deb
  ```

  Poi copia `~/.config/dumetodoclient/client.toml` nella nuova cartella `~/.config/metodoclient/`.

## Repository GitHub

Il repository GitHub resta **`pietronitecnologie/DuMetodo`**: nessun cambiamento per le copie locali e per
GitHub Actions. Le versioni si distinguono con tag e branch: `v2` (questa versione, a singolo cliente) e `v3`
(multicliente, in sviluppo).
