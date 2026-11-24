# Manuale utente di DuMetodo

DuMetodo gestisce la produzione: gli ordini del Cliente, la pianificazione dei reparti, le consegne con bolla e
DDT, il magazzino, la qualità e la tracciabilità. Questo manuale descrive ogni sezione dell'applicazione.

Ogni pagina di DuMetodo ha in alto a destra un piccolo pulsante **?**: apre in una scheda separata il capitolo
del manuale che riguarda quella pagina.

Ognuno vede solo le sezioni per cui ha il permesso: se nel tuo menu manca una voce descritta qui, chiedi a un
amministratore di abilitarla (vedi [Utenti e permessi](#utenti-e-permessi)).

## Primi passi

### Accesso e password

- Entra con il nome utente e la password ricevuti dall'amministratore.
- Se la password è stata impostata da un amministratore, al primo accesso DuMetodo chiede di sceglierne una
  nuova: scrivi la password attuale, poi due volte la nuova. Finché non la cambi le altre pagine non si aprono.
- La password deve avere almeno 10 caratteri, non solo numeri, non troppo simile al nome utente e non tra le
  più comuni.
- Per cambiarla in qualsiasi momento: clicca il cerchio con la tua iniziale (in alto a destra) →
  **Cambia password**. Dallo stesso menu esci con **Esci**.
- Se hai dimenticato la password, chiedi a un amministratore una **password temporanea**.

### La barra in alto

- **Logo e scritta DuMetodo**: riportano alla pagina iniziale.
- **Menu**: le sezioni (Ordini, Produzione, Spedizioni, Magazzino, Qualità, Report, Utilità, Amministrazione).
  Le sezioni con più pagine si aprono a tendina.
- **Cerca ordine**: il campo a destra apre subito un ordine. Scrivi una parte del numero, la serie, l'appendice
  o l'anno e scegli dall'elenco: non serve scrivere il codice completo.
- **Modalità reparto** (icona accanto alla ricerca): ingrandisce testi e pulsanti, utile sui PC e sui tablet dei
  reparti. Si ricorda la scelta su quel dispositivo.
- **?**: apre il capitolo del manuale della pagina in cui ti trovi.

In fondo a ogni pagina sono scritte la versione del server e, se usi il client desktop, quella del client:
servono all'assistenza per capire se tutto è aggiornato.

### Scegliere un ordine nei moduli

Dove un modulo chiede un ordine (tracce, controlli qualità, foto, cambia ordine…) non si scrive il codice a
mano: si inizia a scrivere e si sceglie dall'elenco. Un ordine è identificato da **serie, numero, appendice e
anno**, e l'elenco li mostra tutti, con stato e scadenza, così non si confondono ordini con lo stesso numero.
Con il campo vuoto l'elenco propone gli ordini aperti con la scadenza più vicina.

### Tabelle: ordinare e filtrare

Tutte le tabelle si possono:

- **ordinare** cliccando l'intestazione di una colonna (un secondo clic inverte l'ordine);
- **filtrare** scrivendo nel campo sotto l'intestazione: restano solo le righe che contengono il testo.

Ordinamento e filtro agiscono sulle righe mostrate nella pagina; per cercare tra tutti i dati usa i filtri e
il pulsante **Cerca** della pagina.

### Annunci

Gli amministratori possono pubblicare annunci (fermi macchina, scadenze, avvisi): compaiono in cima alla
pagina all'accesso. **Chiudi** li nasconde.

### Uso da telefono e tablet

DuMetodo funziona anche da telefono: il menu si apre con il pulsante ☰, e le tabelle larghe diventano schede
una sotto l'altra. Dal telefono si possono anche scattare foto e caricarle su un ordine (vedi
[Foto e file](#foto-e-file)).

## Il client desktop

Il client desktop (`dumetodo-client.exe`) è il programma consigliato per lavorare con DuMetodo sui PC: apre
solo DuMetodo e aggiunge funzioni che un browser non ha.

### Schede

- **Ctrl+T** o il pulsante **+** apre una nuova scheda; **Ctrl+W** o la **×** la chiude; il clic centrale su
  una scheda la chiude.
- **Ctrl+Tab** / **Ctrl+PagSu** / **Ctrl+PagGiù** passano da una scheda all'altra.
- **Ctrl+clic** o clic centrale su un link, oppure *Apri in nuova scheda* dal tasto destro, aprono la pagina in
  una nuova scheda.
- L'accesso vale per tutte le schede. Alla chiusura il client ricorda le schede aperte e le riapre al
  prossimo avvio.

### Menu del tasto destro

Il tasto destro apre un menu con il logo dell'azienda e le voci adatte a ciò che hai sotto il mouse:

- **Aggiorna pagina**;
- **Copia tabella**, **Copia colonna**, **Copia riga**: copiano i dati in un formato che si incolla
  direttamente in Excel;
- **Copia selezione** e **Incolla**;
- sugli ordini e sulle righe, le azioni rapide (apri ordine, apri in nuova scheda…).

Le voci sono decise dall'amministratore in Configurazione e dipendono dai tuoi permessi.

### Scorciatoie da tastiera

Premi **F1** per vedere l'elenco delle scorciatoie attive. Quelle predefinite:

| Tasto | Azione |
|---|---|
| F1 | Elenco delle scorciatoie |
| F2 | Cerca un ordine |
| F3 | Elenco ordini |
| F4 | Agenda ordini |
| F6 | Consegna |
| F7 | DDT |
| F8 | Importazione ordini |
| F9 | Richiesta materiali |
| F10 | Dashboard |
| Ctrl+Shift+N | Nuova scheda |

Valgono inoltre: **Ctrl+P** stampa, **Ctrl +** / **Ctrl −** / **Ctrl 0** zoom, **F11** schermo intero,
**Alt+←** / **Alt+→** indietro e avanti.

### Cambia server

Il pulsante a destra nella barra delle schede mostra il server collegato (pallino verde). Cliccandolo si può
indicare un altro indirizzo; **Annulla** lascia il server attuale. Cambiando server le altre schede si
chiudono.

### Aggiornamenti del client

Quando l'amministratore pubblica una versione nuova, nella pagina compare l'avviso **È disponibile un
aggiornamento**. Con **Aggiorna ora** il client scarica la nuova versione, controlla che il file sia integro e si
riavvia riaprendo le schede. La versione del client è scritta in fondo alla pagina e nel suggerimento
dell'icona in alto a sinistra.

## Chat

Nel client desktop il pulsante **💬 Chat** nella barra delle schede apre il pannello della chat a destra
(serve il permesso *Usare la chat*).

- Le chat sono **solo 1 a 1**, tra due persone.
- **Persone**: l'elenco dei colleghi, con chi è collegato in quel momento. Cerca un nome e clicca per scrivere.
- **Conversazioni**: le chat in corso, con il numero di messaggi non letti.
- Scrivi in basso e premi **Invio** (o **Invia**).
- I messaggi sono **salvati sul server**: puoi scrivere anche a chi non è collegato, li leggerà al prossimo
  accesso. Chiudere una conversazione (✕) la toglie dall'elenco, ma i messaggi restano.
- All'arrivo di un messaggio: il numero dei non letti compare sul pulsante Chat, la pagina mostra un avviso e,
  se il client non è in primo piano, Windows mostra una notifica e l'icona lampeggia nella barra delle
  applicazioni.

## Ordini

### Elenco ordini

La pagina **Ordini → Elenco ordini** parte vuota, perché gli ordini sono migliaia: imposta almeno un filtro e
premi **Cerca** (o Invio).

- Filtri: ordine (numero, serie, appendice), articolo, anno, categoria, scadenza dal/al, *Solo aperti*.
- **Azzera** pulisce i filtri.
- Ogni riga mostra stato, scadenza, righe, pezzi, reparto e un segnale se ci sono **note non lette**.
- Clicca un ordine per aprirne il dettaglio.

### Stati dell'ordine

| Stato | Significato |
|---|---|
| Inserito | Ordine ricevuto, da lavorare |
| Lavorato con mancanze | In lavorazione, manca qualcosa per completarlo |
| In spedizione | Pronto: compare nella pagina Consegna |
| Evasione parziale | Consegnata solo una parte delle righe |
| Consegnato | Tutte le righe consegnate |
| Annullato | Ordine annullato |

Lo stato si cambia dal dettaglio dell'ordine; i passaggi non ammessi vengono rifiutati con un messaggio.

### Dettaglio dell'ordine

Il dettaglio mostra codice completo, scadenza, data di consegna, pezzi, tempo teorico (ore calcolate con la
resa oraria), reparto e cliente. Sotto:

- **Righe**: articolo, variante, quantità, prezzo, scadenza e stato. *Mostra anche annullate/sostituite*
  visualizza anche le righe non più attive.
- **Storico scadenze**: ogni cambio di scadenza con data, utente e motivo. **Posticipa** sposta la scadenza.
- **Note**: la comunicazione con il Cliente (vedi [Note e comunicazione con il Cliente](#note-e-comunicazione-con-il-cliente)).
- **Tracciabilità, controlli qualità, foto e file** dell'ordine.

### Azioni sulle righe

| Azione | Cosa fa |
|---|---|
| Evadi | Segna la riga come pronta da consegnare |
| Blocca | Blocca la riga: non viene consegnata finché non la sblocchi |
| Scorpora | La riga resta consegnata ma è esclusa dalla fatturazione |
| Reincorpora | Annulla uno scorporo |
| Annulla | Annulla la riga |
| Dividi | Sposta una parte della quantità su una nuova riga dello stesso ordine, con una scadenza diversa |

Una riga già inserita in una bolla non si modifica più; sugli ordini in spedizione o chiusi le righe le
modifica solo chi ha il permesso di annullare gli ordini.

### Note e comunicazione con il Cliente

Nel dettaglio dell'ordine, il riquadro **Note** raccoglie i messaggi tra il Fornitore e il Cliente su
quell'ordine.

- Scrivi la nota e salvala: resta legata all'ordine, con autore e data.
- Le **note interne** sono visibili solo al personale del Fornitore, non agli utenti del Cliente.
- Nell'elenco ordini un segnale indica gli ordini con note non lette.

## Produzione

### Agenda

**Produzione → Agenda ordini** mostra gli ordini aperti come schede, divisi tra commesse grandi e commesse
piccole, con pezzi, scadenza e numero di note. **Agenda pulsanti** mostra la stessa vista per le pulsantiere.

- Filtra per ordine e appendice, oppure per tipo (Pulsanti, Elettronica, Conto lavoro, Tutti).
- Il campo *Cerca nella pagina* evidenzia gli ordini che contengono il testo; **Esc** lo pulisce.
- Clicca una scheda per aprire l'ordine.

### Pianificazione

**Produzione → Pianificazione** confronta il carico di lavoro di ogni reparto con la sua capacità, settimana per
settimana.

- Le ore sono calcolate come *quantità × prezzo / resa oraria* sulle righe non ancora in bolla degli ordini
  *Inserito* o *Evasione parziale*.
- La colonna **Arretrato** raccoglie le righe già scadute. La settimana corrente conta solo le ore lavorative
  rimaste.
- Ogni cella è colorata: **OK** (verde), **Attenzione** (giallo), **Critico** (rosso), in base alla percentuale
  di capacità occupata.
- **Capacità settimanale dei reparti**: modifica le ore disponibili e salva.
- **Esporta Excel** scarica la tabella; **Invia report via email** la spedisce ai destinatari configurati (o a
  quelli che scrivi).
- Se la resa oraria non è configurata le ore non si possono calcolare: va impostata in Configurazione.

### Tabulati

- **Tabulato settimanale**: articoli da produrre in una settimana per un reparto, con ore totali. Scegli reparto e
  settimana (‹ precedente / successiva ›), poi **Stampa** o **Excel**.
- **Tabulato scorte**: come il settimanale, solo per le commesse piccole.

## Spedizioni

### Consegna

**Spedizioni → Consegna** elenca gli ordini **In spedizione** con le righe da consegnare.

1. **Selezione**: scegli le serie da consegnare (nessuna scelta = tutti gli ordini). **Rimuovi** toglie un ordine
   dalla spedizione.
2. **Etichette**: scarica il file delle etichette (Excel) o quello delle sole pulsantiere.
3. **Genera bolla e consegna**: crea il file della bolla per il gestionale (con il tracciato scelto in
   Configurazione) e segna le righe come consegnate.

**Consegne effettuate** mostra lo storico: numero, data, utente, selezione e file della bolla, sempre
riscaricabile.

### DDT

**Spedizioni → DDT** collega le righe consegnate al DDT emesso dal gestionale.

1. Seleziona le righe consegnate in attesa di DDT.
2. Scrivi **numero** e **data** del DDT (numero + anno devono essere unici).
3. **Registra e invia**: DuMetodo crea il file tecnico del DDT e lo invia.

**DDT registrati** elenca i DDT con il file tecnico e lo stato dell'invio; **Reinvia** ripete l'invio non
riuscito.

Se il DDT lo emette DuMetodo, con numerazione automatica e stampa di legge, si usa invece
[Gestionale → DDT da emettere](#ddt-di-legge). Il file tecnico parte allo stesso modo.

### Importazione ordini

Gli ordini del Cliente arrivano come file `.txt`:

- **dalla casella email**, letta automaticamente ogni 30 minuti (**Leggi ora** forza una lettura);
- **a mano**, con **Carica file ordine** e **Importa**.

Ogni ordine nel file è la versione completa: le righe nuove vengono create, quelle cambiate aggiornate, quelle
non più presenti annullate. Le righe già in bolla non vengono mai toccate. Un file identico a uno già importato
viene saltato.

- **File elaborati**: ogni file con l'esito (ordini nuovi, aggiornati, scartati). Dal dettaglio puoi scaricare
  il file originale o **Rielaborarlo**.
- **Problemi da gestire**: righe o ordini che non è stato possibile importare. Leggi il problema, correggi (di
  solito nell'ordine o nel file) e premi **Gestito**.

## Magazzino

### Richiesta materiali

Ogni reparto chiede al magazzino i codici che gli mancano.

1. Apri **Magazzino → Richiesta materiali** e scegli il reparto.
2. **Nuova richiesta**: scrivi codice e variante, spunta *Urgente* se serve, **Richiedi**. Se il codice è già
   richiesto e non ancora arrivato, aumenta il numero di richieste (sollecito).
3. Seleziona le richieste e comunicale al magazzino via email: si vede l'**anteprima** prima di confermare.
4. All'arrivo del materiale premi **Ricevi**; **Elimina** cancella una richiesta.

Le richieste si chiudono da sole quando arriva un DDT in ingresso con quei codici. **Esporta Excel** scarica
l'elenco.

### Inventario

- **Registra conteggio**: codice, variante e quantità contata. Un codice nuovo viene aggiunto ai componenti.
- Le tabelle mostrano l'ultimo conteggio per componente e lo storico, filtrabili per codice e anno.
- **Esportazioni**: giacenze (ultimo conteggio), somma dei conteggi, tutti i conteggi.

### Distinte base

Una distinta elenca i componenti che servono per un articolo finito.

- **Nuova distinta**: codice articolo, variante, descrizione. Un articolo che non esiste viene aggiunto
  all'anagrafica.
- Nella distinta: **Aggiungi componente** (codice, quantità, UM, note), modifica le righe, spostale con ↑ ↓,
  eliminale.
- La ricerca trova le distinte anche per codice di un componente. **Esporta Excel** scarica tutte le distinte.

### DDT in ingresso

Registra il materiale che arriva dai fornitori.

- **Importa da file**: carica il file del gestionale (CSV/TXT con separatore `;` o XML). Le righe sono
  raggruppate per fornitore, numero e data del DDT; reimportare lo stesso file non duplica nulla.
- **Nuovo DDT**: inserimento a mano di numero, data e fornitore, poi delle righe dei materiali.
- Nel DDT le righe sono **Da verificare** finché non le controlli: seleziona e **Segna verificate**, oppure
  **Verifica tutto il DDT**.
- Il DDT mostra le richieste materiali chiuse dal suo arrivo e quelle ancora aperte per gli stessi codici.
- **Ricerca materiali**: trova i materiali arrivati per codice, fornitore e periodo, esportabili in Excel.

### Componenti e articoli

- **Componenti**: anagrafica di codici, varianti, descrizione, UM, prezzo medio e fornitore. Un componente non
  attivo non viene più proposto, ma resta nelle distinte e nello storico.
- **Articoli finiti**: codice, descrizione, reparto, UM, tabulato e prezzo di riferimento (usato da Pianificazione
  e Controllo prezzi). *Nuovo* segna gli articoli appena introdotti.

## Qualità e tracciabilità

### Controlli qualità

**Qualità → Nuovo controllo**: scegli l'ordine (o lo stock), il codice prodotto e il **test**. DuMetodo mostra i
controlli da eseguire:

- per i controlli a numeri, scrivi il **campione** controllato e quanti pezzi sono **KO**;
- per i controlli di presenza, indica **Presente** o **Assente**.

**Registra controllo** salva. **Controlli qualità** elenca i controlli per periodo, esportabili in Excel.

### Tracce

Una traccia registra la storia di un elemento (scheda, lotto, componente) con un codice.

- **Nuova traccia**: codice, data, chiave e valore dell'evento, ordine e reparto. Se il codice esiste già,
  l'evento si aggiunge alla sua storia.
- Dalla traccia: **Aggiungi evento** e **Stampa etichetta**.
- Nel dettaglio dell'ordine si registrano anche **lotto di produzione** e **busta**.

### Foto e file

- **Carica foto**: scegli l'ordine e scatta una foto (da telefono o tablet) o scegli una foto o un PDF. Le foto
  vengono ridotte prima dell'invio.
- **Foto e file**: tutti i file caricati, per ordine e periodo. I file si vedono e si caricano anche dal
  dettaglio dell'ordine.

## Report

- **Dashboard**: ordini in ritardo, carico della settimana, ordini da consegnare, righe consegnate senza DDT,
  problemi di importazione, pezzi consegnati nelle ultime 12 settimane e prossime scadenze.
- **Produttività**: pezzi consegnati per settimana con valore e ore teoriche. Inserisci le **ore lavorate** per
  avere pezzi/ora ed efficienza; il dettaglio mostra i singoli giorni.
- **Materiale per reparto**: ore a scadenza e ore consegnate per reparto e settimana.
- **In scadenza e consegnato per settimana**: gli stessi dati in un'altra forma.
  - Righe: le settimane, dalla corrente alle 8 precedenti.
  - Il numero di settimane predefinito si imposta in **Configurazione → Produzione**; nella pagina si può cambiare
    di volta in volta.
  - Colonne: i reparti; per ognuno, le ore in scadenza (da evadere) e le ore consegnate nella settimana.
  - Totali per settimana e per reparto.
  - In rosso dove si è consegnato meno di quanto scadeva.
- **Controllo prezzi**: confronta il prezzo delle righe con il prezzo di riferimento dell'articolo, sulle righe
  in lavorazione (da correggere prima di consegnare) o su quelle già consegnate.

### Controllo della produttività (per settimana)

- **Efficienza per reparto**: per ogni settimana e reparto confronta le **ore timbrate** nel reparto (modulo
  Timbrature) con le **ore prodotte**, cioè il valore delle righe consegnate di articoli del reparto diviso la
  resa oraria.
  - **Efficienza** = ore prodotte / ore timbrate: verde da 95%, giallo da 80%, rosso sotto.
  - Nel totale ci sono anche le ore timbrate fuori da ogni reparto e le consegne di articoli senza reparto.
  - Chi ha il permesso dei costi vede anche il **costo della manodopera** e il **costo per ora prodotta**.
- **Puntualità e arretrato**: per ogni settimana, le righe consegnate **entro la scadenza** e **in ritardo**, la
  percentuale di puntualità (verde da 95%, giallo da 85%) e il ritardo medio in giorni.
  - Mostra anche l'**arretrato a fine settimana**: righe già scadute e non ancora consegnate a quella data, in
    righe e in ore. Per la settimana in corso l'arretrato è calcolato ad oggi.
- **Ore per lavoratore** (permesso di vedere le timbrature): ore lavorate per settimana e quota passata in un
  **reparto** (verde da 90%, giallo da 75%).
  - Una quota bassa vuol dire ore di presenza non attribuite a nessun reparto, oppure timbrature di reparto
    dimenticate.
  - Ci sono anche la media settimanale e le anomalie (timbrature mancanti o incoerenti).

Nei report scegli il periodo (*Fino alla settimana del*, numero di settimane) e premi **Aggiorna**; **Excel**
scarica i dati.

## Utilità

- **Errori di importazione**: i problemi dei file d'ordine, da verificare e segnare come gestiti.
- **Ristampa etichette da DDT**: scrivi numero e anno del DDT, scegli gli ordini e rigenera il file delle
  etichette.
- **Gestione annunci**: crea i messaggi mostrati a tutti gli utenti. Un annuncio *Permanente* resta finché non lo
  disattivi; altrimenti è visibile per un giorno.
- **Scorporazione di massa**: cerca le righe consegnate di un articolo in un periodo e scegli quali escludere
  dalla fatturazione.
- **Cambia ordine**: corregge serie, numero, appendice, anno e scadenza di un ordine (con il motivo, che finisce
  nello storico scadenze). Righe, note, tracce e file seguono l'ordine automaticamente.

## Tracciatura

Il modulo **Tracciatura** registra qualsiasi tipo di controllo o evento: un controllo qualità, il controllo di un
lotto, un'operazione su un ordine, un documento, una fotografia. Che cosa si registra lo decidi tu con i
**modelli**; gli operatori compilano il modulo che ne risulta.

### Registrare una tracciatura

**Tracciatura → Nuova tracciatura** (permesso *Registrare tracciature*), oppure **Nuova** nel riquadro
«Tracciature» della pagina di un ordine o di un DDT.

1. **Scegli il modello**. Se parti da un ordine, in evidenza («previsto») ci sono i modelli che valgono per i
   codici dell'ordine.
2. **Compila i collegamenti** richiesti dal modello, con `*` per quelli obbligatori:
   - l'ordine si sceglie con il selettore;
   - il DDT si scrive come numero/anno, es. `12/2026`;
   - codici articolo e codici finiti devono esistere in anagrafica;
   - lotto e riferimento (es. numero di serie) sono testo libero.
3. **Compila i campi**:
   - i numeri accettano la virgola;
   - foto e documenti si caricano anche dal telefono, che apre direttamente la fotocamera;
   - le foto vengono ridotte prima dell'invio.
4. **Registra**, oppure **Registra e inseriscine un'altra** per controllare un pezzo dopo l'altro sullo stesso
   ordine.

L'**esito** si calcola da solo e la tracciatura è **non conforme** se:

- un numero è fuori dai limiti;
- un campo OK/KO è KO;
- una scelta è tra quelle non conformi;
- un sì/no è diverso dalla risposta attesa.

Se il modello non ha controlli, la tracciatura è solo «registrata».

Le tracciature non si cancellano: si **annullano** con il motivo. Le proprie le annulla chi le ha registrate, tutte
le annulla chi configura i modelli.

**Tracciatura → Tracciature** le elenca, con filtri per modello, categoria, codice, lotto, riferimento, esito e
periodo. **Excel** scarica i dati; scegliendo un modello si ha una colonna per ogni campo, utile per statistiche e
carte di controllo.

### Modelli di tracciatura

**Tracciatura → Modelli di tracciatura** (permesso *Configurare i modelli di tracciatura*).

Ogni modello ha:

- **nome**, **categoria** (controllo qualità, controllo di lotto, operazione, documento, altro) e le istruzioni
  mostrate all'operatore;
- **a cosa si collega**: ordine, DDT, codice articolo (componente), codice finito, lotto, riferimento libero.
  Ognuno può essere *non previsto*, *facoltativo* od *obbligatorio*;
- **a quali codici si applica**: codici articolo o finiti, uno per riga, anche con `*` (es. `18090*` = tutti i
  codici che iniziano così). È così che si stabilisce **quale test va fatto su quale codice**:
  - DuMetodo propone il modello sugli ordini di quei codici;
  - non permette di usarlo per codici diversi.

  Vuoto = vale per tutti;
- i **campi**, nell'ordine in cui compaiono (si spostano con ↑ ↓):

| Tipo | Uso |
|---|---|
| Testo breve, testo lungo | Descrizioni, osservazioni |
| Numero intero | Conteggi (es. pezzi controllati) |
| Numero decimale | Misure; con **unità di misura** e **minimo/massimo accettabile** |
| Sì / No | Domande; con la **risposta attesa** diventa un controllo |
| Scelta da un elenco | Valori fissi; le **scelte non conformi** rendono la tracciatura non conforme |
| Data, data e ora | Date di produzione, scadenze |
| Esito OK / KO | Controlli a vista |
| Fotografia | Scattata dal telefono o caricata |
| Documento | PDF o immagine (certificati, schede) |

Ogni campo può essere obbligatorio e avere le sue istruzioni. **Prova il modulo** mostra il modulo come lo vedrà
l'operatore.

Un campo già usato non cambia tipo e non si elimina: si disattiva, e i valori registrati restano visibili nelle
tracciature vecchie. Un modello non più usato si disattiva.

## Timbrature

Il modulo **Timbrature** registra le entrate e le uscite dei lavoratori e **il tempo passato in ogni reparto**,
per avere ore e costi per reparto. Si timbra alle **postazioni**: PC o tablet con il client DuMetodo
all'ingresso dello stabilimento e nei reparti. Un PC associato a una postazione si usa anche per tutto il resto
di DuMetodo: la timbratura si apre dal menu **Timbrature → Timbra**.

### Postazioni di timbratura

**Timbrature → Postazioni** (permesso *Gestire lavoratori, postazioni e correzioni*).

- **Nuova postazione**: nome e reparto.
  - Una postazione **senza reparto** è quella di **ingresso e uscita** dallo stabilimento.
  - Una postazione **di reparto** registra l'inizio e la fine del lavoro in quel reparto.
- **Associare il PC alla postazione**: sul PC della postazione apri **Timbrature → Postazioni** e premi
  **Associa questo PC** sulla postazione giusta.
  - In alternativa basta aprire una volta, su quel PC, il **link segreto** della postazione.
  - Da quel momento, su quel PC, chiunque usi DuMetodo trova in **Timbrature** la voce **Timbra · nome della
    postazione**, anche senza permessi sulle timbrature. Per timbrare non serve un utente: basta il codice
    personale.
  - Dalla timbratura, **Apri DuMetodo** (in alto a destra) porta al resto del programma.
  - **Scollega questo PC** toglie l'associazione.
- **PC solo per timbrare** (chiosco): nel `client.toml` del PC si mette come indirizzo il link segreto, a
  schermo intero. Il client apre subito la timbratura. La pagina Postazioni mostra le due configurazioni da
  copiare.
- Se il link finisce nelle mani sbagliate o il PC viene sostituito, **Nuovo link** disattiva il vecchio. Anche
  i PC associati smettono di essere postazioni: vanno associati di nuovo.
- Lo stato **collegata / non collegata** indica se la postazione ha contattato il server negli ultimi 15 minuti.
- **Come ci si identifica**: per ogni postazione si attivano uno o più modi.
  - **Tastierino sullo schermo**: il codice personale, con il dito o il mouse.
  - **Tastiera e lettori USB**: lettori di **codici a barre**, di **QR** e di **tessere NFC/RFID**. Quasi tutti
    funzionano come una tastiera: scrivono il codice letto e premono Invio. Non serve installare niente:
    si collegano al PC della postazione e funzionano subito. La postazione riconosce la lettura veloce del lettore
    e la invia da sola, anche se il lettore non manda Invio.
  - **Fotocamera**: per tablet o PC con webcam, si inquadra il QR del badge. Il browser o il client chiede il
    permesso di usare la fotocamera la prima volta. Su un PC fisso è più affidabile un lettore USB.

### Lavoratori e codici

**Timbrature → Lavoratori**: nome, cognome, matricola, reparto abituale e **costo orario** (costo aziendale di
un'ora, serve ai costi per reparto: lo vede solo chi ha il permesso dei costi).

Ogni lavoratore ha un **codice personale** da digitare alla postazione.

- Alla creazione DuMetodo genera un codice di 6 cifre e lo mostra **una sola volta**: comunicalo al lavoratore.
- In alternativa si imposta un codice scelto, da 4 a 10 cifre (es. il numero del badge).
- Il codice non è leggibile da nessuno, nemmeno dagli amministratori. Se il lavoratore lo dimentica, se ne genera
  uno nuovo e il vecchio smette di funzionare.
- Un lavoratore non attivo non può timbrare.

Oltre al codice, ogni lavoratore può avere **badge e tessere** (scheda del lavoratore → *Badge e tessere*).

- **Crea un badge da stampare**: DuMetodo genera un codice (es. `DMHAURVXYC`).
  - **Stampa** produce una tessera 85,6 × 54 mm con nome, matricola, **codice a barre** e **QR** dello stesso
    codice. Va bene per una stampante per badge o per un cartoncino da plastificare.
- **Associa una tessera NFC/RFID** o un badge che il lavoratore ha già (es. il badge della mensa):
  1. collega un lettore al PC da cui lavori;
  2. clicca nel campo *Codice* e passa la tessera: il codice si scrive da solo.
- Un badge perso si **disattiva**: non identifica più nessuno, e si può riattivare. Per ogni badge si vede l'ultimo
  uso.
- Codici personali e badge non possono coincidere, e ogni badge o tessera appartiene a un solo lavoratore.

### Timbrare

Alla postazione il lavoratore digita il codice e preme **OK**, oppure passa il badge o la tessera sul lettore,
oppure inquadra il QR del badge. Compaiono il suo nome, dove si trova e **solo le
timbrature possibili in quel momento**: la prima, evidenziata, è la più probabile.

| Postazione | Timbrature |
|---|---|
| Ingresso e uscita | Entrata, oppure **Entrata e inizio lavoro** in un reparto a scelta; poi Uscita, Inizio mensa, Inizio pausa, Fine lavoro nel reparto attuale e **Inizio lavoro in qualsiasi altro reparto**; dalla mensa o dalla pausa, la loro fine |
| Reparto | Inizio lavoro nel reparto (anche passando da un altro reparto), Fine lavoro, Inizio mensa e pausa |

- Prima di lavorare in un reparto si timbra l'entrata alla postazione di ingresso. Se lo stabilimento non ha una
  postazione di ingresso, le postazioni di reparto permettono anche entrata e uscita.
- **Mensa e pausa** sospendono il reparto: alla fine il tempo torna al reparto di prima, senza timbrare di nuovo.
- Un doppio tocco non registra due volte la stessa timbratura. Dopo 5 codici sbagliati la postazione si blocca
  per un minuto.
- Se un collega passa il badge mentre la postazione mostra ancora le scelte di un altro, la postazione passa
  subito al nuovo lavoratore. I tasti premuti per sbaglio invece non contano.

### Presenze e correzioni

**Timbrature → Presenze** mostra per ogni lavoratore, per un giorno o un periodo:

- dove si trova adesso;
- entrata e uscita;
- **ore lavorate** (presenza meno mensa e pause), divise **per reparto** più le ore «non assegnate» a un reparto;
- le **anomalie**: uscita mancante, entrata doppia, entrata mancante.

**Timbrature e correzioni** elenca le timbrature.

- Una timbratura dimenticata si **aggiunge a mano**, con il motivo.
- Una timbratura sbagliata si **annulla**, con il motivo: resta visibile barrata.
- Ogni correzione registra chi l'ha fatta.

Un turno senza uscita si chiude all'ultima timbratura e viene segnalato: correggilo aggiungendo l'uscita. Un
turno di notte appartiene al giorno dell'entrata.

### Costi per reparto

**Timbrature → Costi per reparto** (permesso *Costi orari e costi per reparto*). Per un periodo, per ogni reparto:

| Colonna | Significato |
|---|---|
| Ore lavorate | Dalle timbrature |
| Costo | Ore × costo orario di chi le ha lavorate |
| Ore teoriche consegnate | Valore delle righe consegnate dal reparto / resa oraria |
| Efficienza | Ore teoriche / ore lavorate (in rosso sotto l'80%) |
| Costo per ora teorica | Quanto costa un'ora di produzione consegnata |

La riga «Non assegnato a un reparto» raccoglie il tempo di presenza fuori dai reparti. La pagina segnala i
lavoratori senza costo orario. **Excel** scarica i dati.

> Le timbrature sono dati personali: prima di usarle informa i lavoratori (informativa privacy) e verifica gli
> eventuali accordi richiesti per il controllo delle presenze.

## Gestionale

La sezione **Gestionale** emette i documenti fiscali partendo dalle righe consegnate in
[Spedizioni → Consegna](#consegna):

1. i **DDT di legge**, con numerazione progressiva e stampa;
2. le **fatture differite** in formato elettronico **FatturaPA** (XML da inviare allo SdI);
3. le **note di credito**, per stornare una fattura.

È **riservata** al profilo **Amministrazione**: non la vedono gli altri utenti, nemmeno gli amministratori di
sistema.

Il **Riepilogo** mostra a che punto è il lavoro (righe senza DDT, DDT da fatturare, bozze, fatture da inviare)
e avvisa se mancano dati fiscali.

### DDT di legge

**Gestionale → DDT da emettere** elenca le righe consegnate che non hanno ancora un DDT.

1. Spunta le righe da mettere nel documento. Di solito sono tutte quelle consegnate nella giornata.
2. Compila i dati del trasporto. I valori predefiniti si impostano in [Impostazioni](#dati-fiscali-e-impostazioni):
   - data;
   - causale (vendita, conto lavorazione, reso…);
   - aspetto esteriore dei beni;
   - numero di colli e peso;
   - trasporto a cura del mittente, del destinatario o di un vettore (indica il vettore e il suo indirizzo);
   - porto (franco o assegnato);
   - annotazioni.
3. **Emetti DDT**: DuMetodo assegna il **numero progressivo dell'anno** (1, 2, 3…), genera il **file tecnico** e
   lo invia ai destinatari configurati, come in Spedizioni → DDT.

Il DDT contiene i dati richiesti dal D.P.R. 472/1996 e fotografa tutto al momento dell'emissione: mittente,
destinatario, luogo di destinazione, righe e data e ora di inizio del trasporto. Se poi cambiano i dati fiscali,
il documento già emesso resta com'era. **Stampa** apre il documento pronto per la stampa in A4, con gli spazi
per le firme.

**Bolle (DDT)** elenca tutti i DDT: quelli emessi da DuMetodo e quelli registrati con il numero del gestionale
esterno. I numeri già usati da un DDT registrato a mano vengono saltati.

**Annullare un DDT.** Un DDT non ancora fatturato si annulla dal suo dettaglio, indicando il motivo. Il numero
resta consumato: non si riusa. Le righe tornano tra quelle da emettere. Il file tecnico già inviato non viene
ritirato, quindi avvisa chi lo ha ricevuto.

### Fatture elettroniche

**Gestionale → Da fatturare** raggruppa per mese i DDT di vendita non ancora fatturati.

1. Spunta i DDT del mese e scegli **come dividerli** e **che data dare** alle fatture.

   | Come dividere | Risultato |
   |---|---|
   | Una fattura per tutti i DDT selezionati | Una sola fattura (la classica fattura di fine mese) |
   | Una fattura per ogni giorno | Una fattura con i DDT di ciascun giorno |
   | Una fattura per ogni DDT | Una fattura per bolla |
   | Al massimo N righe per fattura | Le righe, nell'ordine dei DDT, divise in fatture di N righe; un DDT può finire su più fatture, e ognuna lo cita |

   | Data delle fatture | Risultato |
   |---|---|
   | La data indicata | Tutte le fatture con la data scelta |
   | La data dell'ultimo DDT della fattura | Comoda con «una per giorno» o «una per DDT» |
   | L'ultimo giorno del mese dei DDT | La data tipica della fattura differita |

   La data non può essere precedente ai DDT della fattura né futura.
   - La **fattura differita** (tipo TD24) raccoglie le consegne di un mese e va emessa **entro il 15 del mese
     successivo**. La pagina indica il termine e lo segnala in rosso se è passato.
2. **Crea le bozze**: una riga per ogni riga consegnata, con il prezzo e lo sconto dell'ordine e l'aliquota
   IVA delle impostazioni.
   - Le righe **scorporate** non si fatturano.
   - Le righe **senza prezzo** bloccano la bozza: correggi prima l'ordine.
3. Controlla la bozza. Puoi cambiare data e causale, vedere l'**anteprima** o eliminarla (i DDT tornano da
   fatturare).
   - Se mancano dati obbligatori (partita IVA, CAP, codice destinatario…) la pagina li elenca e l'emissione
     resta bloccata.
4. **Emetti**: la fattura riceve il **numero definitivo** e DuMetodo scrive il file **XML FatturaPA**.
   - Scegli la **numerazione** accanto al pulsante Emetti: il numero è il prossimo di quella serie, nel suo formato
     (es. `1`, `1/2026`, `FT1`). Vedi [Numerazioni](#numerazioni).
   - Il nome del file segue le regole dello SdI, es. `IT01234567897_0IPd3.xml`.
   - Le fatture si numerano in ordine di data: non si emette una fattura con data precedente all'ultima emessa.
   - Con più bozze, in **Fatture e note di credito** spunta le bozze ed **Emetti le bozze selezionate**: DuMetodo le
     emette in ordine di data, così i numeri seguono le date. Si ferma alla prima che ha un problema e dice quale.

Dopo l'emissione:

- **Scarica XML** e invia il file allo **SdI** dal portale «Fatture e Corrispettivi» dell'Agenzia delle Entrate o
  tramite il tuo intermediario. Poi segna l'esito nel riquadro **Invio allo SdI**: inviata, consegnata o scartata,
  con eventuali note.
- **Copia di cortesia**: la versione stampabile da mandare al cliente. Non ha valore fiscale.
- La fattura emessa **non si modifica e non si elimina**. Per correggerla si usa **Storna con nota di credito**:
  - nasce una bozza di nota di credito (tipo TD04) con le stesse righe;
  - per uno storno parziale riduci le quantità (0 toglie la riga);
  - poi emettila come una fattura.

L'XML segue le specifiche FatturaPA 1.2.2 (formato FPR12, tra privati). Contiene:

- i riferimenti ai DDT (`DatiDDT`);
- i riepiloghi per aliquota;
- la natura e il riferimento normativo per le operazioni senza IVA;
- il **bollo virtuale** da 2 € quando è dovuto;
- le condizioni di pagamento con scadenza e IBAN.

Prima di usarlo in produzione, controlla la prima fattura con il servizio di verifica dell'Agenzia delle Entrate.

### Numerazioni

**Gestionale → Numerazioni** gestisce le serie di numerazione (sezionali) di DDT e fatture. Ogni serie ha un
progressivo che riparte da 1 ogni anno.

- All'inizio ci sono due numerazioni predefinite: **DDT** e **Fatture**, con formato `{numero}`.
  - `{numero}` è il progressivo, `{anno}` l'anno.
  - Esempi di formato: `{numero}/{anno}` → 10/2026, `FT{numero}` → FT10.
- **Partire da un numero**: nella riga dell'anno scrivi il prossimo numero e premi **Imposta**. Esempio: 10, se
  fino al 9 hai emesso con un altro programma.
  - Si imposta anche per l'anno successivo, in anticipo.
  - Non si può tornare su un numero che DuMetodo ha già assegnato.
- **Nuova numerazione**: es. «Fatture estero» con formato `{numero}/E`.
  - Due numerazioni dello stesso tipo devono avere formati diversi, così due documenti non hanno mai lo stesso
    numero.
  - Una sola è **predefinita**.
  - Una numerazione non più usata si può disattivare.
  - Il formato di una numerazione già usata non si cambia: creane una nuova.

Quando emetti un DDT o una fattura, o più bozze insieme, **scegli la numerazione**. È proposta la predefinita.
Le date devono seguire i numeri all'interno di ciascuna numerazione.

### Dati fiscali e impostazioni

**Gestionale → Dati fiscali e impostazioni** raccoglie i dati che finiscono su DDT e fatture.

| Riquadro | Cosa contiene |
|---|---|
| Fornitore (cedente) | Ragione sociale, partita IVA, codice fiscale, sede, regime fiscale, iscrizione REA e capitale sociale, contatti |
| Cliente (cessionario e destinatario) | Ragione sociale, partita IVA o codice fiscale, sede, **codice destinatario SdI** (7 caratteri) o PEC con codice `0000000`, luogo di destinazione delle merci se diverso dalla sede |
| IVA, pagamento e DDT | Aliquota IVA, oppure natura e riferimento normativo con aliquota 0. Esigibilità (anche split payment). Modalità e giorni di pagamento, fine mese, IBAN e banca. Valori predefiniti dei DDT |

Ogni modifica è registrata nello storico in fondo alla pagina. Questi dati non compaiono in Configurazione.

## Amministrazione

### Configurazione

**Amministrazione → Configurazione** raccoglie i parametri di funzionamento in riquadri chiusi: clicca un titolo
per aprirlo, oppure **Espandi tutto**.

- **Azienda**: nome del Fornitore e del Cliente, **logo** (compare in alto e nel menu del tasto destro).
- **Casella ordini (IMAP)**: server, utente, password, mittenti ammessi. **Prova connessione** verifica i dati
  senza leggere i messaggi. La password è salvata cifrata e non viene mai mostrata.
- **Produzione**, **Ordini**, **Importazione ordini**, **Pianificazione e tabulati**, **Magazzino**,
  **Tracciabilità**: i parametri di calcolo e di funzionamento di ciascuna sezione (resa oraria, serie
  Fast/Express, reparti per le richieste materiali…).
- **Indirizzi email** e **Note e comunicazioni**: destinatari dei report, delle richieste materiali e degli
  avvisi; *Destinatari avvisi del server* ricevono le email di MetodoConsole (servizi fermi, backup non
  riusciti).
- **Consegna e file per il gestionale**: i **tracciati** dei file per il gestionale (bolla, DDT).
  **Anteprima con dati d'esempio** mostra il risultato; **Guida al linguaggio** spiega come scriverli.
- **Client desktop**: voci del menu del tasto destro e scorciatoie da tastiera.

Ogni parametro è controllato prima del salvataggio; **Ripristina predefinito** torna al valore iniziale.
**Storico modifiche** mostra chi ha cambiato cosa e quando.

### Aggiornamenti del client desktop

**Amministrazione → Aggiornamenti del client** distribuisce le nuove versioni del client.

1. Scarica `dumetodo-client-X.Y.Z.exe` dalla Release su GitHub.
2. **Nuova versione**: scrivi il numero di versione (es. 0.3.0), scegli il file e, se vuoi, le novità da mostrare
   agli utenti. **Carica**.
3. I client con una versione più vecchia propongono l'aggiornamento.

Viene sempre proposta la versione più alta tra quelle attive. **Ritira** smette di proporre una versione (per
tornare indietro); **Riattiva** la ripropone; **Elimina** la cancella con il suo file e libera il numero di
versione.

### Utenti e permessi

**Amministrazione → Utenti e permessi** gestisce gli accessi senza passare dall'admin tecnico.

- **Nuovo utente**: nome utente, nome, email, password iniziale e un **profilo** di partenza (Operatore,
  Caporeparto, Amministratore, Amministrazione, Cliente). Al primo accesso l'utente dovrà cambiare la password.
- *Utente del Cliente*: scrive le note come Cliente e non vede le note interne.
- **Password**: *Genera password temporanea* (consigliato) mostra una password **una sola volta**, da comunicare
  all'utente, che la cambierà al primo accesso. In alternativa puoi impostarne una.
- **Attivo**: togli la spunta per impedire l'accesso senza cancellare l'utente.
- **Permessi**: ogni funzione si abilita singolarmente, divisa per sezione. **Applica profilo** sostituisce tutti i
  permessi con quelli di un profilo; *inverti* rovescia le spunte di una sezione.
- **Storico modifiche**: ogni cambio di dati, password e permessi, con chi l'ha fatto.

Regole di sicurezza: non puoi assegnare permessi che tu stesso non hai, né modificare chi ne ha più di te.
Il **superuser** ha tutti i permessi e accede all'admin tecnico di Django: va dato solo a chi gestisce il server.

La sezione **Gestionale** è **riservata**. I suoi permessi li ha solo chi li riceve esplicitamente, di solito con
il profilo **Amministrazione** (l'ufficio amministrativo). Non li hanno il profilo Amministratore, il superuser e
chi «ha tutto». Li assegna un superuser.

### Reparti

**Amministrazione → Reparti**: codice, nome, **capacità settimanale in ore** (usata dalla Pianificazione), gruppo
per i report e stato attivo/disattivato.

### Test e controlli qualità

**Amministrazione → Test e controlli qualità** definisce cosa si verifica nei controlli qualità.

- Un **controllo** è una singola verifica: a numeri (campione / KO) oppure di presenza (presente / assente).
- Un **test** è l'insieme dei controlli da eseguire su un tipo di prodotto (es. *Pulsantiere*: visivo,
  funzionale, distinta, disegno).

Crea prima i controlli, poi i test che li usano.

## Domande frequenti

**Non vedo una sezione del menu.** Non hai il permesso: chiedilo a un amministratore.

**La pagina degli ordini è vuota.** È voluto: imposta un filtro e premi Cerca.

**Ho dimenticato la password.** Chiedi a un amministratore una password temporanea.

**Non vedo la sezione Gestionale.** È riservata: serve il profilo Amministrazione, che assegna un superuser.

**Ho sbagliato una fattura già emessa.** Non si cancella: stornala con una nota di credito ed emetti quella giusta.

**Il client dice che c'è un aggiornamento.** Premi *Aggiorna ora*: il client si riavvia da solo con le stesse
schede.

**Non arrivano gli ordini via email.** In Importazione ordini controlla l'ultima lettura e l'ultimo errore della
casella; un amministratore può verificare i dati con *Prova connessione* in Configurazione.

**Come scarico il manuale?** Dalla pagina del manuale, con **Scarica (.md)**.
