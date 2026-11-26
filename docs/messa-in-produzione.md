# Mettere in produzione Metodo v2 sul server dove gira già Metodo

Strategia: Metodo v2 si installa **accanto** al Metodo attuale, sullo stesso server, con un **dominio di prova**
(es. `metodo2.esempio.it`). Si portano i dati, gli utenti provano, e solo alla fine il dominio principale passa a
Metodo v2. Il Metodo attuale resta acceso e intatto fino al passaggio, e si può tornare indietro.

Regole:

- **Casella degli ordini**: Metodo v2 la può leggere insieme al Metodo attuale. Legge in sola lettura, per **data
  di arrivo** (non per la marca «non letto», che il Metodo attuale toglie), e non tocca né segna i messaggi. La
  prima volta parte dai messaggi che arrivano da quel momento: gli ordini vecchi non vengono reimportati.
- **Email in uscita** (file tecnico, note, report): **un solo sistema alla volta**. Durante la prova Metodo v2
  non ha i destinatari delle email: non manda niente a nessuno.

| Fase | Cosa | Fermo per gli utenti |
|---|---|---|
| 0 | Verifica del server | no |
| 1 | Installazione di Metodo v2 sul dominio di prova | no |
| 2 | Prelievo dei dati dal Metodo attuale | no |
| 3 | Prova in parallelo (consigliate 1-2 settimane) | no |
| 4 | Passaggio del dominio principale | circa un'ora, fuori orario |
| 5 | Dopo il passaggio | no |

## Fase 0 — Verifica del server

Sul server, scarica il codice in una cartella provvisoria e lancia la verifica. È di sola lettura: non installa,
non modifica e non riavvia niente.

```bash
git clone --branch v2 https://github.com/pietronitecnologie/DuMetodo.git /tmp/metodo-v2
```

```bash
sudo sh /tmp/metodo-v2/produzione/verifica-server.sh metodo.esempio.it metodo2.esempio.it
```

Mostra sistema, dischi, Docker, porte occupate, web server e siti, certificati, PostgreSQL, dove e come gira il
Metodo attuale, firewall e DNS. Le password sono mascherate; il risultato è anche in `/tmp/verifica-metodo-*.txt`.
Da qui si decidono: porta locale libera, web server (nginx o Apache), come raggiungere il database del Metodo
attuale.

Servono: Docker con il plugin `docker compose`, almeno 4 GB di RAM liberi e 20 GB di disco, e il record DNS del
dominio di prova (tipo A, verso l'IP del server).

Il repository è privato: sul server serve una chiave di sola lettura (GitHub → repository → Settings →
Deploy keys) oppure un token personale con accesso in lettura.

## Fase 1 — Installazione sul dominio di prova

```bash
sudo git clone --branch v2 https://github.com/pietronitecnologie/DuMetodo.git /opt/metodo
```

```bash
cd /opt/metodo && sudo sh produzione/genera-env.sh
```

`genera-env.sh` chiede dominio di prova, porta locale (una libera secondo la verifica, es. 8010) e server SMTP, e
crea `.env` con chiavi e password casuali (permessi 600, mai mostrate). Poi:

```bash
sudo docker compose up -d --build
```

```bash
curl -fsS http://127.0.0.1:8010/salute/ && echo OK
```

**Proxy e HTTPS.** Metodo v2 ascolta solo su `127.0.0.1`: lo pubblica il web server già presente, con un sito in
più che non tocca quello del Metodo attuale. Usa `produzione/nginx-metodo.conf` oppure
`produzione/apache-metodo.conf`: dentro ci sono i comandi, compreso `certbot` per il certificato.

**Primo amministratore:**

```bash
sudo docker compose exec web python manage.py createsuperuser
```

**MetodoConsole** (backup notturni, controlli, interfaccia di amministrazione):

```bash
sudo ./MetodoConsole/installa.sh /opt/metodo
```

In `/etc/metodoconsole/metodoconsole.conf` imposta `url_pubblico = https://metodo2.esempio.it`, poi:

```bash
sudo metodoconsole diagnostica
```

```bash
sudo metodoconsole backup --nota installazione
```

Durante la prova **non** impostare in Metodo v2 gli indirizzi email del Cliente, i destinatari del file tecnico e
quelli del report di pianificazione: il file tecnico e la bolla si scaricano dalle pagine per confrontarli con
quelli del Metodo attuale. La **casella degli ordini** invece si può impostare subito (Configurazione → Casella
ordini): Metodo v2 riceve gli stessi ordini del Metodo attuale, senza disturbarlo. Stato e punto di partenza:

```bash
sudo docker compose exec web python manage.py casella_ordini
```

`--da-ora` riparte dai messaggi in arrivo da adesso; `--dal AAAA-MM-GG` recupera i messaggi da quel giorno
(quelli già elaborati si saltano).

## Fase 2 — Dati dal Metodo attuale

Il prelievo legge il database del Metodo attuale **in sola lettura**, a blocchi, senza appesantirlo. È
riprendibile e incrementale: si ripete quante volte serve e ogni volta porta solo le novità.

**Utente in sola lettura** sul database del Metodo attuale (una volta; scegli tu la password):

```sql
CREATE ROLE metodo_lettura LOGIN PASSWORD 'scegli-una-password';
GRANT CONNECT ON DATABASE metodo TO metodo_lettura;
GRANT USAGE ON SCHEMA public TO metodo_lettura;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO metodo_lettura;
```

**Come lo raggiunge Metodo v2.** Dipende da dove gira il database: la verifica della fase 0 lo dice.

- PostgreSQL installato sul server (il caso più comune): la via che non tocca il database in produzione è
  `./preleva-da-metodo.sh` con «Indirizzo del server» = `127.0.0.1`. Apre un tunnel SSH verso il server stesso
  e chiede i dati del database. Serve solo poter entrare in SSH sul server.
- In alternativa si fa ascoltare PostgreSQL anche sull'indirizzo di Docker (`listen_addresses` e una riga in
  `pg_hba.conf`). Questo richiede di riavviare il database del Metodo attuale: va fatto fuori orario.
- Database in un container Docker: si collega al container con la rete di Docker. I passi esatti dipendono da
  come è avviato.

Poi, con `METODO_DB_URL` nel `.env` (vedi `.env.example`):

```bash
sudo metodoconsole prelievo verifica
```

```bash
sudo metodoconsole prelievo avvia --max-kb 0
```

`--max-kb 0` toglie il limite di banda: sullo stesso server non serve. `metodoconsole prelievo stato` e
`metodoconsole prelievo log -f` mostrano l'avanzamento; se si interrompe, `prelievo avvia` riprende.

## Fase 3 — Prova in parallelo

Il Metodo attuale resta l'unico ufficiale. Gli utenti provano Metodo v2 sul dominio di prova, con una copia dei
dati che si aggiorna rilanciando il prelievo (anche ogni sera).

Da controllare prima del passaggio:

- ordini, righe, stati e scadenze uguali al Metodo attuale;
- **bolla e file tecnico identici** a quelli del Metodo attuale (scaricali dalle pagine e confrontali);
- **numerazione dei DDT**: in Gestionale → Numerazioni imposta il prossimo numero, che continua quello del
  Metodo attuale (il numero dei DDT è regolato dalla legge);
- utenti, profili e permessi; postazioni delle timbrature;
- client desktop: per la prova va puntato al dominio di prova.

## Fase 4 — Passaggio (fuori orario, circa un'ora)

1. Avvisa gli utenti dell'orario di fermo.
2. **Metodo attuale:** ferma l'applicazione (con lei si fermano la lettura della casella e gli invii di
   email). Il database resta acceso per l'ultimo prelievo.
3. Backup di entrambi:

   ```bash
   sudo -u postgres pg_dump -Fc metodo -f /var/backups/metodo-attuale-prima-del-passaggio.dump
   ```

   ```bash
   sudo metodoconsole backup --nota prima-del-passaggio
   ```

4. Ultimo prelievo, solo le novità:

   ```bash
   sudo metodoconsole prelievo avvia --max-kb 0
   ```

5. In Metodo v2:
   - Configurazione → indirizzi email (Cliente, file tecnico, avvisi) e, se non l'hai già fatto, casella degli
     ordini;
   - Gestionale → controlla il prossimo numero dei DDT.
6. **Dominio principale a Metodo v2:**
   - nel web server, sposta `server_name` (nginx) o `ServerName` (Apache) del dominio principale dal sito del
     Metodo attuale a quello di Metodo v2;
   - nel `.env` aggiungi il dominio a `DJANGO_ALLOWED_HOSTS` e a `DJANGO_CSRF_TRUSTED_ORIGINS` (separati da
     virgola) e mettilo in `SITE_URL`;
   - riavvia:

   ```bash
   sudo docker compose up -d
   ```

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

   Il certificato del dominio principale c'è già e si riusa.
7. Verifica: accesso, un ordine, una consegna di prova, `sudo metodoconsole diagnostica`. In
   `/etc/metodoconsole/metodoconsole.conf` aggiorna `url_pubblico`.
8. Il client desktop, se usa il dominio principale, non va toccato.

**Tornare indietro** (se qualcosa non va): rimetti `server_name` al sito del Metodo attuale, ricarica il web
server, togli la casella ordini da Metodo v2 e riattiva lettura e invii del Metodo attuale. Gli ordini arrivati
nel frattempo su Metodo v2 vanno ripresi a mano: per questo la finestra di passaggio deve essere breve.

## Fase 5 — Dopo il passaggio

- Lascia il Metodo attuale fermo (o in sola lettura su un altro nome) per qualche settimana, poi dismettilo.
- Togli `METODO_DB_URL` dal `.env` e cancella l'utente `metodo_lettura`:

  ```sql
  DROP OWNED BY metodo_lettura;
  DROP ROLE metodo_lettura;
  ```

- Configura la **copia remota dei backup** (`copia_remota` in MetodoConsole): un backup sullo stesso disco non
  protegge dalla rottura del disco.
- Apri `sudo metodoconsole`, scheda Sicurezza: firewall, SSH, aggiornamenti automatici, antivirus.
