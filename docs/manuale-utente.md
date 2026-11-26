# Manuale utente di Metodo v2

Metodo v2 gestisce la produzione: gli ordini del Cliente, la pianificazione dei reparti, le consegne con bolla e
DDT, il magazzino, la qualità e la tracciabilità. Questo manuale descrive ogni sezione dell'applicazione.

Ogni pagina di Metodo v2 ha in alto a destra un piccolo pulsante **?**: apre in una scheda separata il capitolo
del manuale che riguarda quella pagina.

Ognuno vede solo le sezioni per cui ha il permesso: se nel tuo menu manca una voce descritta qui, chiedi a un
amministratore di abilitarla (vedi [Utenti e permessi](#utenti-e-permessi)).

## Primi passi

### Accesso e password

- Entra con il nome utente e la password ricevuti dall'amministratore.
- Se la password è stata impostata da un amministratore, al primo accesso Metodo v2 chiede di sceglierne una
  nuova: scrivi la password attuale, poi due volte la nuova. Finché non la cambi le altre pagine non si aprono.
- La password deve avere almeno 10 caratteri, non solo numeri, non troppo simile al nome utente e non tra le
  più comuni.
- Per cambiarla in qualsiasi momento: clicca il cerchio con la tua iniziale (in alto a destra) →
  **Cambia password**. Dallo stesso menu esci con **Esci**.
- Se hai dimenticato la password, chiedi a un amministratore una **password temporanea**.

### La barra in alto

- **Logo e scritta Metodo v2**: riportano alla pagina iniziale.
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

Metodo v2 funziona anche da telefono: il menu si apre con il pulsante ☰, e le tabelle larghe diventano schede
una sotto l'altra. Dal telefono si possono anche scattare foto e allegarle a un ordine, con una tracciatura che
ha un campo *Fotografia* (vedi [Tracciatura](#tracciatura)).

## Il client desktop

Il client desktop (`metodo-client.exe`) è il programma consigliato per lavorare con Metodo v2 sui PC: apre
solo Metodo v2 e aggiunge funzioni che un browser non ha.

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

La pagina **Produzione → Elenco ordini** parte vuota, perché gli ordini sono migliaia: imposta almeno un filtro e
premi **Cerca** (o Invio).

- Filtri: ordine (numero, serie, appendice), articolo, anno, categoria, scadenza dal/al, *Solo aperti*.
- **Azzera** pulisce i filtri.
- Ogni riga mostra stato, scadenza, righe, pezzi, reparto e un segnale se ci sono **note non lette**.
- Clicca un ordine per aprirne il dettaglio.
- **Segnalibro 🔖**: clicca il 🔖 di un ordine (a inizio riga nell'elenco ordini, accanto al numero nelle schede
  dell'**Agenda ordini** e dell'**Agenda pulsanti**) per segnare dove sei arrivato a controllare. L'ordine resta
  evidenziato in giallo, in elenco e in agenda, anche cambiando pagina o filtri (la pagina ci si porta da sola).
  Puoi segnarne quanti vuoi: segnarne un altro non toglie i precedenti. Un segnalibro si toglie solo con un altro
  clic sullo stesso 🔖, oppure tutti insieme uscendo da Metodo v2. Se un ordine segnato è in un'altra pagina
  dell'elenco, in alto compare il link per andarci.

Il pulsante **←** in alto nelle pagine (ordine, DDT, fattura, anagrafiche…) torna alla **pagina da cui sei
arrivato**, con i filtri com'erano, qualunque fosse: elenco ordini, agenda, report, un altro ordine. Se apri la
pagina direttamente (link, nuova scheda) porta all'elenco della sezione.

### Ricerca articoli

**Produzione → Ricerca articoli** usa gli stessi filtri dell'elenco ordini ma mostra una riga per ogni articolo
degli ordini trovati: **articolo** (prima colonna), descrizione, **ordine** (clic per aprirlo), **numero di riga**
nell'ordine, quantità, scadenza e stato. Con il filtro *Articolo* restano solo le righe di quell'articolo (basta
una parte del codice). In alto il totale delle righe e dei pezzi; **Excel** scarica l'elenco.

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

## Spedizioni

### Consegna

**Spedizioni → Consegna** elenca gli ordini **In spedizione** con le righe da consegnare.

1. **Selezione**: scegli le serie da consegnare (nessuna scelta = tutti gli ordini). **Rimuovi** toglie un ordine
   dalla spedizione.
2. **Etichette**: scarica il file delle etichette (Excel) o quello delle sole pulsantiere.
3. **Genera bolla e consegna**: crea il file della bolla per il gestionale (con il tracciato scelto in
   Configurazione) e segna le righe come consegnate.

Dopo la consegna:

- un ordine con tutte le righe consegnate diventa **Consegnato** ed esce dall'elenco;
- un ordine che ha ancora righe da consegnare (bloccate o non messe in evasione) resta in **Evasione parziale**,
  ma esce comunque dall'elenco. Ricompare quando metti in evasione altre sue righe (**Evadi** nella pagina
  dell'ordine).

L'elenco mostra solo gli ordini che hanno davvero qualcosa da consegnare.

**Consegne effettuate** mostra lo storico: numero, data, utente, selezione e file della bolla, sempre
riscaricabile.

### DDT

**Spedizioni → DDT** crea il documento di trasporto per le righe consegnate e invia il file tecnico.

1. Seleziona le righe consegnate in attesa di DDT.
2. Controlla i **dati del trasporto**: causale, aspetto dei beni, colli, peso, trasporto a cura, porto ed
   eventuale vettore. I valori proposti si impostano una volta per tutte nei dati fiscali.
3. **Emetti DDT**: Metodo v2 assegna il **numero di legge** dalla numerazione dei DDT (progressivo dell'anno,
   mostrato prima di emettere), crea il DDT, genera e invia il **file tecnico**.
4. Nella pagina **DDT registrati** compare **Stampa il DDT**: il documento da accompagnare alla merce.

- **Numerazione**: in **Amministrazione → Numerazione DDT** (permesso *Configurazione*) definisci la
  numerazione, cioè il formato (per esempio `{numero}/{anno}`) e da che numero partire, per esempio 41 se fino
  al 40 hai emesso con un altro programma.
  - Il numero riparte ogni anno, non salta e non si ripete.
  - Le date devono seguire i numeri: un DDT non può avere data precedente all'ultimo emesso.
  - Si possono avere più numerazioni (es. una per i resi, `R{numero}`) e scegliere quale usare.
- **Dati di mittente e destinatario**: per legge il DDT li deve riportare. Se mancano, la pagina lo segnala;
  si completano in **Gestionale → Dati fiscali e impostazioni** (profilo Amministrazione).
- **DDT emesso dal gestionale esterno**: apri il riquadro in fondo, scrivi numero e data del DDT e premi
  **Registra e invia il file tecnico**.

**DDT registrati** elenca i DDT con il file tecnico e lo stato dell'invio; **Stampa** riapre la stampa dei DDT
emessi da Metodo v2; **Reinvia** ripete l'invio non riuscito.

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
- **Articoli finiti**: codice, descrizione, reparto, UM e **prezzo di riferimento** (usato da Pianificazione e
  Controllo prezzi), tutti modificabili direttamente nell'elenco: cambia i valori della riga e premi **Salva**.
  Quando cambi il prezzo, *Prezzo dal* diventa la data di oggi; lasciandolo vuoto l'articolo resta senza prezzo.

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

È l'unico modo per registrare controlli e tracce. Le vecchie funzioni **Controlli qualità**, **Nuova traccia**,
**Lotto e busta**, **Foto e file** e l'**archivio delle tracce** sono state sostituite dalle tracciature, e i loro
dati sono stati convertiti:

- ogni **test qualità** è diventato un modello di categoria *Controllo qualità*:
  - un controllo a campione diventa due campi numerici, **campione** e **KO**; un KO maggiore di zero rende la
    tracciatura non conforme;
  - un controllo di presenza (distinta, disegno) diventa un campo **Sì/No** che deve essere Sì;
  - ogni controllo già registrato è diventato una tracciatura dello stesso ordine;
- **foto e file** degli ordini sono tracciature del modello *Foto e file dell'ordine*; i file sono gli stessi;
- **lotto e busta** si registrano con il modello *Lotto e busta*, se lo usavate; altrimenti crea un modello
  con quei due campi;
- le **tracce** registrate in passato, anche quelle importate da Metodo, sono tracciature del modello *Tracce (da
  Metodo)*: evento, valore e reparto sono i campi, il codice dell'elemento tracciato è il *riferimento*. Si
  cercano in **Tracciature** filtrando per modello o per riferimento. Il modello non è attivo, quindi non si usa
  per le nuove registrazioni.

Per un nuovo controllo qualità crea un modello con i campi che servono, per esempio *campione* e *KO* con un
massimo di 0, oppure un campo *Esito OK/KO*. Indica a quali codici si applica: verrà proposto sugli ordini con
quei codici.

### Registrare una tracciatura

**Tracciature → Nuova tracciatura** (permesso *Registrare tracciature*), oppure **Nuova** nel riquadro
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

**Tracciature → Tracciature** le elenca, con filtri per modello, categoria, codice, lotto, riferimento, esito e
periodo. **Excel** scarica i dati; scegliendo un modello si ha una colonna per ogni campo, utile per statistiche e
carte di controllo.

### Modelli di tracciatura

**Tracciature → Modelli di tracciatura** (permesso *Configurare i modelli di tracciatura*).

Ogni modello ha:

- **nome**, **categoria** (controllo qualità, controllo di lotto, operazione, documento, altro) e le istruzioni
  mostrate all'operatore;
- **a cosa si collega**: ordine, DDT, codice articolo (componente), codice finito, lotto, riferimento libero.
  Ognuno può essere *non previsto*, *facoltativo* od *obbligatorio*;
- **a quali codici si applica**: codici articolo o finiti, uno per riga, anche con `*` (es. `18090*` = tutti i
  codici che iniziano così). È così che si stabilisce **quale test va fatto su quale codice**:
  - Metodo v2 propone il modello sugli ordini di quei codici;
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
per avere ore e costi per reparto. Si timbra alle **postazioni**: PC o tablet con il client Metodo v2
all'ingresso dello stabilimento e nei reparti. Un PC associato a una postazione si usa anche per tutto il resto
di Metodo v2: la timbratura si apre dal menu **Timbrature → Timbra**.

### Postazioni di timbratura

**Timbrature → Postazioni** (permesso *Gestire lavoratori, postazioni e correzioni*).

- **Nuova postazione**: nome e reparto.
  - Una postazione **senza reparto** è quella di **ingresso e uscita** dallo stabilimento.
  - Una postazione **di reparto** registra l'inizio e la fine del lavoro in quel reparto.
- **Associare il PC alla postazione**: sul PC della postazione apri **Timbrature → Postazioni** e premi
  **Associa questo PC** sulla postazione giusta.
  - In alternativa basta aprire una volta, su quel PC, il **link segreto** della postazione.
  - Da quel momento, su quel PC, chiunque usi Metodo v2 trova in **Timbrature** la voce **Timbra · nome della
    postazione**, anche senza permessi sulle timbrature. Per timbrare non serve un utente: basta il codice
    personale.
  - Dalla timbratura, **Apri Metodo v2** (in alto a destra) porta al resto del programma.
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

- Alla creazione Metodo v2 genera un codice di 6 cifre e lo mostra **una sola volta**: comunicalo al lavoratore.
- In alternativa si imposta un codice scelto, da 4 a 10 cifre (es. il numero del badge).
- Il codice non è leggibile da nessuno, nemmeno dagli amministratori. Se il lavoratore lo dimentica, se ne genera
  uno nuovo e il vecchio smette di funzionare.
- Un lavoratore non attivo non può timbrare.

Oltre al codice, ogni lavoratore può avere **badge e tessere** (scheda del lavoratore → *Badge e tessere*).

- **Crea un badge da stampare**: Metodo v2 genera un codice (es. `DMHAURVXYC`).
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

- Di solito si timbra l'entrata alla postazione di ingresso. Se la prima timbratura della giornata è in un
  reparto, la postazione la accetta (**Entrata e inizio lavoro**) e registra in automatico anche l'entrata, alla
  stessa ora. Se lo stabilimento non ha una postazione di ingresso, le postazioni di reparto permettono anche
  l'uscita.
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
3. **Emetti DDT**: Metodo v2 assegna il **numero progressivo dell'anno** (1, 2, 3…), genera il **file tecnico** e
   lo invia ai destinatari configurati, come in Spedizioni → DDT.

Il DDT contiene i dati richiesti dal D.P.R. 472/1996 e fotografa tutto al momento dell'emissione: mittente,
destinatario, luogo di destinazione, righe e data e ora di inizio del trasporto. Se poi cambiano i dati fiscali,
il documento già emesso resta com'era. **Stampa** apre il documento pronto per la stampa in A4, con gli spazi
per le firme.

**Bolle (DDT)** elenca tutti i DDT: quelli emessi da Metodo v2 e quelli registrati con il numero del gestionale
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
4. **Emetti**: la fattura riceve il **numero definitivo** e Metodo v2 scrive il file **XML FatturaPA**.
   - Scegli la **numerazione** accanto al pulsante Emetti: il numero è il prossimo di quella serie, nel suo formato
     (es. `1`, `1/2026`, `FT1`). Vedi [Numerazioni](#numerazioni).
   - Il nome del file segue le regole dello SdI, es. `IT01234567897_0IPd3.xml`.
   - Le fatture si numerano in ordine di data: non si emette una fattura con data precedente all'ultima emessa.
   - Con più bozze, in **Fatture e note di credito** spunta le bozze ed **Emetti le bozze selezionate**: Metodo v2 le
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
  - Non si può tornare su un numero che Metodo v2 ha già assegnato.
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

1. Scarica `metodo-client-X.Y.Z.exe` dalla Release su GitHub.
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

### Clienti committenti

L'azienda può produrre per **più clienti committenti**, per esempio DMG e ABB, ognuno con i suoi ordini.
L'anagrafica è in **Amministrazione → Clienti committenti** (permesso *Gestire i clienti committenti*).

- Ogni committente ha un **codice** breve (es. `DMG`), un **nome** e un **colore**, per riconoscerlo a colpo
  d'occhio.
- Il **predefinito** è quello usato quando il committente non è indicato. Per ora vale per le importazioni
  degli ordini e per i dati presi dal vecchio Metodo. **Rendi predefinito** lo cambia.
- Ogni ordine appartiene a un committente. Lo stesso numero d'ordine (serie, numero, appendice, anno) può
  esistere per committenti diversi: sono ordini distinti.
- **Chi vede cosa** (scheda dell'utente, *Clienti committenti visibili*):
  - un **utente del Cliente** vede solo i committenti spuntati;
  - un **utente interno** vede tutti i committenti, oppure solo quelli spuntati, se ce ne sono.
- **Selettore in alto**: con più committenti, accanto alla ricerca degli ordini compare l'elenco dei clienti.
  - Scegliendone uno, tutte le sezioni mostrano solo i suoi dati: ordini, ricerca, agenda, pianificazione,
    tabulati, consegne, DDT, fatture, report, tracciature e utilità.
  - *Tutti i clienti* mostra tutto, con la colonna *Cliente* negli elenchi.
  - Il selettore restringe gli elenchi; quello che un utente può aprire dipende dai committenti visibili
    nella sua scheda.
- **Un documento, un cliente**: una bolla di consegna, un DDT e una fattura sono sempre di un solo
  committente. Con *Tutti i clienti* e una selezione mista Metodo v2 si ferma e chiede di scegliere il
  committente. In *Da fatturare* i DDT sono raggruppati per committente e mese.
- **Report**: con un committente scelto, un riquadro azzurro ricorda che i dati sono parziali.
  - *Analisi produttività* non calcola efficienza e pezzi/ora, perché le ore lavorate sono dell'intera
    azienda.
  - *Efficienza per reparto* è sempre dell'intera azienda, perché le timbrature non sono divise per cliente.
  - In *Pianificazione* il carico è quello del committente; la capacità dei reparti resta quella
    dell'intera azienda.
- **Tracciature**: quelle collegate a un ordine o a un DDT seguono il committente; quelle senza ordine e
  senza DDT (materiali in ingresso, lotti, riferimenti liberi) sono dell'intera azienda e si vedono sempre.
- **Magazzino e timbrature** sono dell'intera azienda: componenti, distinte, inventario e presenze non
  cambiano con il selettore.
- **Impostazioni del cliente** (**Clienti committenti → Impostazioni**): ogni committente può avere valori
  suoi. Un campo vuoto usa il valore generale, scritto in grigio nel campo.
  - *Casella ordini*: una casella propria del cliente (server, utente, password), oppure la casella generale
    con i **mittenti** del cliente: le email che arrivano da quegli indirizzi diventano ordini di quel cliente.
    Gli altri mittenti ammessi in Configurazione portano al predefinito.
  - *Importazione*: chi avvisare per gli errori.
  - *Indirizzi email*: indirizzi del cliente per le note e destinatari del file tecnico dei DDT.
  - *Consegna*: tracciato della bolla, tracciato del file tecnico, codice fornitore sulle etichette.
  - *Dati fiscali del Cliente*, *IVA, pagamento e DDT*: solo per chi ha le impostazioni del Gestionale.
  - Ragione sociale, partita IVA, indirizzi email, codice fornitore e casella **appartengono a un solo
    cliente**: per un committente diverso dal predefinito, se restano vuoti non valgono quelli generali.
    Così ABB non riceve mai i dati fiscali o le email di DMG; senza i suoi dati fiscali, DDT e fatture di
    ABB non si emettono.
  - Il predefinito usa i valori generali (Configurazione e Gestionale → Impostazioni).
- **Flusso produttivo del cliente**: ogni cliente può seguire un flusso diverso. I valori generali sono in
  Configurazione → *Flusso produttivo*; quelli di un cliente in Clienti committenti → Impostazioni.
  - *Stato «Lavorato con mancanze»*: se è spento, gli ordini del cliente non hanno quel pulsante e passano
    direttamente in spedizione.
  - *Consegne parziali*: se è spento, un ordine con righe bloccate non si spedisce finché le righe non sono
    sbloccate o annullate.
  - *Tracciature obbligatorie*: se è spento, per quel cliente le tracciature restano facoltative.
- **Tracciature obbligatorie** (Tracciature → Modelli, nella scheda del modello):
  - *Per l'ordine*: facoltativa, obbligatoria prima della spedizione oppure prima della consegna.
  - *Clienti committenti*: i clienti per cui vale il modello; nessuno = tutti.
  - Vale per gli ordini che contengono i codici indicati nel modello (vuoto = tutti gli ordini).
  - Finché manca una tracciatura conforme, «Spedisci» o la consegna si fermano e dicono quale manca. Una
    tracciatura *non conforme* o annullata non basta.
  - Nella pagina dell'ordine, il riquadro Tracciature mostra in giallo le tracciature obbligatorie da fare.
  - La *consegna forzata* degli amministratori non le controlla.
- **Reparti del cliente** (Amministrazione → Reparti): spunta i clienti per cui lavora il reparto (nessuno =
  tutti). Con un cliente scelto in alto, pianificazione e tabulati propongono solo i suoi reparti.
- **Caricamento manuale degli ordini**: con più committenti, nella pagina Importazione si sceglie di quale
  cliente sono gli ordini del file. Lo stesso file caricato per due clienti crea ordini distinti.
- **Codici d'ordine uguali**: se un codice letto da un codice a barre esiste per due committenti, si apre
  quello del committente scelto in alto (con *Tutti i clienti*, quello del predefinito).

Con un solo committente non cambia niente rispetto a prima: il selettore non compare.

### Reparti

**Amministrazione → Reparti**: codice, nome, **capacità settimanale in ore** (usata dalla Pianificazione), gruppo
per i report e stato attivo/disattivato.

## Domande frequenti

**Non vedo una sezione del menu.** Non hai il permesso: chiedilo a un amministratore.

**La pagina degli ordini è vuota.** È voluto: imposta un filtro e premi Cerca.

**Ho dimenticato la password.** Chiedi a un amministratore una password temporanea.

**Non vedo la sezione Gestionale.** È riservata: serve il profilo **Amministrazione**, che nemmeno il superuser
ha in automatico. Un superuser apre **Amministrazione → Utenti e permessi**, sceglie l'utente e fa **Applica
profilo → Amministrazione**. In alternativa, sul server: `python manage.py assegna_profilo <utente> Amministrazione`.
Per creare i DDT non serve: bastano **Spedizioni → DDT** e la numerazione in **Amministrazione → Numerazione DDT**.

**Nell'elenco delle tracciature compare l'operatore «metodo-sistema».** È l'autore tecnico dei dati storici
(importati da Metodo o convertiti dalle vecchie funzioni) di cui non si conosce chi li ha registrati. Non è una
persona: non compare tra gli utenti e non può accedere.

**Ho sbagliato una fattura già emessa.** Non si cancella: stornala con una nota di credito ed emetti quella giusta.

**Il client dice che c'è un aggiornamento.** Premi *Aggiorna ora*: il client si riavvia da solo con le stesse
schede.

**Non arrivano gli ordini via email.** In Importazione ordini controlla l'ultima lettura e l'ultimo errore della
casella; un amministratore può verificare i dati con *Prova connessione* in Configurazione.

**Come scarico il manuale?** Dalla pagina del manuale, con **Scarica (.md)**.
