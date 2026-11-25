# 02 — Ordini, Consegna, DDT, Etichette, Tabulati (Metodo v2 functional spec)

Source analysed: `/home/user/metodo-branch` (branch `Metodo`). This document replaces the source code of the
following files for the rewrite:

- `Prod/orders.py` (all), `Prod/views.py` (all), `Prod/etichette.py`, `Prod/ddt.py`, `Prod/allocazione.py`,
  `Prod/templatetags/custom_filters.py`
- order-import paths: `Prod/orders.py` (mail/TXT/Gekosas), `Prod/migrateDb.py`, `Reports/gekosas.py`,
  `Prod/updater.py` (scheduler), `Prod/files.py` (FileOrdini upload), the lot endpoint `vwCreaLottoCommessa`
  and `vwPrint` in `Prod/traccia.py`
- templates `Prod/templates/Ordini/*`, `Prod/templates/allocazioneProd/*`, `Prod/templates/Prod/*`,
  `tplCore.html`, `tplPrint.html`, `tplProductionHome.html`, `Tracciabilita/Lotti/uxReply.html`.

Citations use `file:line` relative to the repo root. **Secrets are not copied.** Where a secret is hard-coded the
location is given and the rewrite must move it to environment/secret storage.

---------------------------------------------------------------------------------------------------------------

## 0. Conventions & cross-cutting rules

### 0.1 URL prefix
`Metodo/urls.py:21` mounts `Prod.urls` under **`/PRODUZIONE/`**. All URLs below are relative to it
(e.g. `vwListaOrdini` → `/PRODUZIONE/vwListaOrdini`). Several templates hard-code absolute paths
(`/PRODUZIONE/...`) and even the production host (`tplCore.html:68-69` →
`https://metodo.gekotecnologie.com/PRODUZIONE/vwListaOrdini?...`). The rewrite must use reverse routing.

### 0.2 Authorisation model (Django groups)
| Helper | Meaning | Defined at |
|---|---|---|
| `isGeko(u)` | user in group **`Geko`** (internal operator) | `orders.py:27`, `views.py:16`, `etichette.py:24`, `ddt.py:23`, `allocazione.py:12` |
| `isGekoAdmin(u)` | user in group **`Geko Admin`** | `orders.py:33`, `views.py:19` |
| `isCustomer(u)` | user in group **`Customer`** (the customer, DMG/Schindler-side) | `orders.py:30` |
| template only | group **`GekoCapRep`** (capo reparto) can see "Gestisci"/"Dividi" | `mdlOrdine.html`, `tplManageOrder.html` |

`user_passes_test` redirects anonymous/unauthorised users to the login page (no 403). Groups are **not
hierarchical**: `setOrdine` requires `Geko`, so a user in `Geko Admin` only cannot run the admin buttons that call
`setOrdine` (Consegna/Annulla). In practice admins are in both groups; the rewrite should model roles explicitly:
`operator` (Geko), `admin` (Geko Admin ⊇ operator), `caporeparto` (GekoCapRep), `customer`.

Note: customers (`Customer`) can see **all** orders (no tenant filter) – single-customer deployment assumption.

### 0.3 Order identity ("commessa key")
There is **no FK** between `Ordine` and `Linea`. An order and its lines are joined by the 4-tuple
**(`rif_ordine`, `serie`, `appendice`, `anno`)** – all `CharField`s, nullable on Linea. There is **no unique
constraint** on this tuple for `Ordine` (duplicates exist in production; see `console.py: ordiniConsegnatiDoppi`).
Every `.get()` on this key can raise `MultipleObjectsReturned`/`DoesNotExist` → HTTP 500.

Value conventions (from the TXT import sample and code):
- `rif_ordine`: 7-digit zero-padded string, e.g. `0004300`. Manual insert pads with `zfill(7)`
  (`orders.py:2094-2096`). Split orders get a suffix `_YYYY-MM-DD` (`orders.py:2094`, `2172`, `2243`).
- `serie`: 5-digit zero-padded, e.g. `00040`; manual insert `zfill(5)` (`orders.py:2098`).
- `appendice`: `'0'` = none (Gekosas/TXT orders), or `T1`, `T2`, `R1`, `R2`, `CL` (manual), or free text after
  "Cambia ordine". Empty string/None also appear (treated as "malformed").
- `anno`: 4-char year string (`Periodo riferimento`). Rows with `len(anno) < 4` are shown struck-through in the list
  (`tplLista.html:211`).

Display formats used (must be preserved because users search by them):
- `Ordine.__str__` (`models.py:178-182`): `serie.rif.appendice-anno`, or `serie.rif-anno` when appendice=='0'.
- List/Consegna display (`tplLista.html:213`, `tplConsegna.html`): `<last 2 chars of serie>.<rif>[-<appendice>]-<anno>`
  e.g. `40.0004300-T1-2026` (Consegna omits `-anno`).
- Board card / ticket key: `serie.rif.appendice.anno` (`_cardCommessaPulsante.html`, `orders.py:1057`).
- Excel export: `serie[3:5].` + rif + `-appendice` (if !='0') + `-anno` (`orders.py:667-673`).
- DDT row: `serie.rif.anno[-appendice]` (`tplDDT.html`).

### 0.4 Business classification (fast / express / standard; reparto by appendice)
There is no explicit field; classification is derived from strings:

| Criterion | Meaning | Where |
|---|---|---|
| serie `43` | **Fast** orders | menu link `vwListaOrdini?serie=43&statenot=CON` (`tplCore.html:68`) |
| serie `42` | **Express** orders | `tplCore.html:69` |
| serie `40`, `41` | **Standard** orders (40 = default) | Consegna/Etichette buttons; XML template default `00040` |
| serie `01` | other/special series | Consegna `c01`, manual insert allowed list (`orders.py:2026`) |
| appendice contains `T` | **Pulsanti** (buttons department) | board quick view (`tplBoardOrdini.html`), board pulsanti (`orders.py:2563`), etichette "-T" |
| appendice contains `R` | **Elettronica** | board quick view |
| appendice `CL` | **Conto Lavoro** | board quick view, XML template |
| appendice `'0'` | order coming from Gekosas/DMG TXT (not regenerated) | `insertLinea`, tabulati exclude `appendice='0'` |

Filters use `serie__contains='40'` etc. (substring match on `00040`), and `appendice__icontains='T'`.

### 0.5 Status codes
`LIST_STATUS` (`models.py:10-17`):

| Code | Label | Meaning |
|---|---|---|
| `INS` | Inserito | imported / new |
| `LAV` | Lavorato con Mancanze | production activated / worked with shortages |
| `SPE` | In spedizione | ready to ship (whole order) |
| `SPP` | Evasione Parziale | partially shipping (some lines flagged) |
| `CON` | Consegnato | delivered (bolla emitted) |
| `ANN` | Annullato | cancelled |

`LIST_URGENZA` `NRM/ALT/24`, `LIST_TIPO` `ASM/PM/CMS/SRV/ING/TRA/PUL`, `LIST_UM` `EA`(pezzi)/`M`(metri).
`createOrderFromGeko` writes invalid codes `'Lav'` and `'BLC'` (`migrateDb.py:112-115`) – dead code but shows
legacy values may exist in DB.

### 0.6 Linea flags (all nullable booleans, `models.py:93-98`)
| Flag | Set by | Meaning |
|---|---|---|
| `evasione_parziale` | setOrdine `evadi`, setOrdine SPE→SPP cascade | line selected for next bolla |
| `emessa_bolla` | vwConsegna / vwConsegnaSingolo | line already put in a delivery file |
| `blocca_linea` | setOrdine `blocca` | excluded from shipping until "Evadi" |
| `duplicata` | insertLinea (same n_riga, different article/variant), setOrdine `duplica` | ignored everywhere |
| `annullata` | insertLinea (re-import), setOrdine `annulla` | ignored everywhere |
| `scorporato` | setOrdine `scorpora`/`reincorpora`, vwScorporazioneMassa | shipped but **not invoiced** (only Reports use it) |

`null` and `False` are equivalent; queries use `~Q(flag=True)` to include nulls. The rewrite should make them
`NOT NULL DEFAULT false`.

### 0.7 Configuration keys used in this area (`Configuration` table, key/value strings)
`EMAIL_GEKO_ADDRESS`, `EMAIL_CUSTOMER_ADDRESS`, `EMAIL_METODO_ADDRESS`, `EMAIL_FIETECNICO_ADDRESS`,
`EMAIL_METODO_USER`, `EMAIL_METODO_PASSWORD` (secret), `EMAIL_SERVER`, `EMAIL_ORDINI_USER`,
`EMAIL_ORDINI_PASSWORD` (secret), `ID_EMAIL` (IMAP cursor), `IDX_SYNC` (Gekosas sync cursor),
`ID_MIGRAZIONE`, `PREZZO_RESA_ORARIA` (€ of price produced per hour – used to convert value into hours).
Missing keys raise `DoesNotExist` → 500 (e.g. `ddt.py:31-32` runs on every GET).

### 0.8 Hard-coded secrets (DO NOT carry over)
- Gekosas MySQL host/user/password in clear text: `Prod/orders.py:1817-1821`, `Prod/migrateDb.py:34-39`,
  `Reports/gekosas.py:5-9, 23-27, 41-45, 59-63, 76-80, 93-97, 110-114, 126-130, 143-147, 168-172`.
- IMAP and SMTP passwords stored in plaintext in `Configuration` (`orders.py:1528-1530`, `mailing.py:24-26`).

### 0.9 Common page context
Every full page passes `site='METODO'`, `app='Production'`, `pageTitle` and extends `tplCore.html`.
`hide_ticket_bar=True` hides the ticket bar (only on Lista Ordini).

---------------------------------------------------------------------------------------------------------------

## 1. State machines

### 1.1 Ordine.stato transitions (complete list of writers)

| From | To | Trigger | Code |
|---|---|---|---|
| — | `INS` | new order created by any import (`insertLinea` with stato `INS`) | `orders.py:1435-1437` |
| any except CON/ANN | value passed by import (`INS`, or Gekosas-mapped) | re-import of existing order (also resets `data_consegna`, `data_scadenza`) | `orders.py:1398-1405`, `1423-1428` |
| CON | (unchanged) | re-import of delivered order → `Errori("Ordine gia consegnato")` and lines not touched | `orders.py:1406-1410`, `1429-1433` |
| `INS` | `LAV` | "Attiva produzione" for orders with scadenza in a date range | `orders.py:1866-1869` |
| any (UI: not CON) | `LAV` | button "Lav" in Lista Ordini (Geko) | `tplLista.html:262-268` → `setOrdine?a=stato&s=LAV` |
| `CON` | `LAV` | button "Lav" for CON orders (Geko Admin) | `tplLista.html:296-301` |
| not SPP/SPE/CON | `SPE` | "Spedisci" (list, Geko) / "Spedisci Commessa" (modal, Geko) | `tplLista.html:270-276`, `mdlOrdine.html` |
| → `SPE` requested | `SPP` | if **any** line of the order has `blocca_linea=True`: all non-blocked, not-yet-billed lines get `evasione_parziale=True` and the order becomes `SPP` | `orders.py:1323-1332` |
| not SPE/CON | `SPP` | "Evadi" on a line (modal) sends `s=SPP&evadi=1` | `mdlOrdine.html`, `orders.py:1299-1301` |
| SPE/SPP | `INS` | "Rimuovi da Consegna" (Consegna page) | `tplConsegna.html` → `setOrdine?s=INS` |
| not CON | `CON` | "Consegna" button (Geko Admin) – **pure status change, no bolla/no DDT** | `tplLista.html:280-285` |
| `SPE` | `CON` | vwConsegna / vwConsegnaSingolo after generating delivery file; `data_consegna=today` | `orders.py:1978-1986`, `2401-2408` |
| `SPP` | `CON` | same, only if **every** non-annullata non-duplicata line has `evasione_parziale=True` | `orders.py:1988-2002`, `2410-2424` |
| any | `ANN` | "Annulla" (Geko Admin) | `tplLista.html:287-306` |
| Gekosas sync | `CON/LAV/SPE/INS` | `scSyncOrders` maps remote status (not scheduled) | `orders.py:1830-1841` |

There is **no server-side validation of transitions**: `setOrdine` accepts any `s` value (even garbage) and writes
it. UI hides buttons only. Rewrite: implement an explicit transition table and reject others (HTTP 409).

### 1.2 Linea transitions
- Import: new line → all flags null; previous non-annullata lines of the same order inserted **before today**
  are set `annullata=True` at every import (see §3.5 step 5) – the import is "replace whole order".
- `evadi` → `blocca_linea=False, evasione_parziale=True`; `blocca` → `evasione_parziale=False, blocca_linea=True`;
  `duplica` → `duplicata=True`; `annulla` → `annullata=True`; `scorpora` → `scorporato=True`;
  `reincorpora` → `scorporato=False` (`orders.py:1299-1320`). There is no "sblocca": unblocking = Evadi.
- Delivery: `emessa_bolla=True` (+ `data_consegna=today` only in vwConsegna, **not** in vwConsegnaSingolo:
  `orders.py:1970-1972` vs `2394-2395`).
- Split (`dividiLinea`): quantity decreased / new line `n_riga='D'+n_riga` / line moved to another order.
- Cambia ordine: key fields + data_scadenza rewritten in bulk.

---------------------------------------------------------------------------------------------------------------

## 2. Data touched (summary; full model doc is in another chapter)

| Model | Used fields | Notes |
|---|---|---|
| `Ordine` | key, stato, urgenza, tipo, location(FK Plan.Location), note(M2M Note), data_scadenza, data_consegna, data_inserimento(auto) | method `tempo_produzione_teorico(articoli_filtro=None)` (`models.py:187-239`) |
| `Linea` | key, n_riga, articolo, variante, qta(float), prezzo_ea(**string**, comma decimal), sconto_uni, magazzino, cliente, data_scadenza, data_consegna, data_inserimento(auto datetime), flags | `prezzo_totale()` parses comma price |
| `DocumentiTrasporto` | key, articolo, variante, qta, cliente, n_riga, no_ddt, data_ddt, inviato, data_inserimento | outbound DDT "file tecnico" staging rows |
| `Note` | user, data(auto_now), testo | attached to Ordine via M2M |
| `StoricoScadenza` | ordine/linea FK, data_scadenza_precedente, data_scadenza_nuova (NOT NULL), data_modifica, utente, motivo | only written by Cambia Ordine |
| `FileOrdini` | file, key strings, data_inserimento | photos/files per order (upload in `files.py`) |
| `Errori` | name, testo, key strings, data_errore, accettato | import errors shown in Console |
| `ProcessedEmail` | message_id(int, unique), subject | IMAP de-duplication |
| `Anagrafica` | articolo, location, um, description, tabulato('0'..'5') | article master, used for area/UM/description |
| `Tracciabilita` | rif_ordine(FK Ordine), chiave, valore, location, data | lotto/busta per commessa |
| `ControlloQlta` | ordine(string), ... | shown in order modal |
| `Tickets.Ticket` | ordine(string `serie.rif.app.anno`) | shown in order modal |
| `Plan.AllocazionePersonale` | location(unique), resources M2M | list of areas in Tabulato Settimanale |

---------------------------------------------------------------------------------------------------------------

## 3. Order acquisition (import)

### 3.1 The DMG/Gekosas TXT order file format
Source: real sample `Metodo/media/mailAttachments/OCL_4300_40_0.ZIP` (contains `OCL_4300_40.TXT` + PDFs).
Zip naming: `OCL_<ordine>_<serie>_<appendice>.ZIP`; PDFs named `OCL_ORL_<id>_<anno>_<serie>_<ordine>_<app>_<ddmmyyyy>.pdf`.

- Encoding: header decoded **UTF-8**, data rows decoded **latin-1** in mail import (`orders.py:1732-1736`,
  `1769-1773`); manual upload decodes everything as UTF-8 (`orders.py:1508-1511`) → non-ASCII rows crash there.
- Separator: `@`. Line terminator CRLF. First line is the header. Fields are right-padded with spaces.
- Header (18 columns):
  `Periodo riferimento@Serie numerazione@N.ro Ordine@Appendice@N.ro prog. riga@Data registrazione@Data consegna@Cod. Articolo@Variante@Quantita@Prezzo@Sconto 1@Sconto 2@Sconto 3@Sconto 4@Sconto 5@Sconto unitario@Prezzo finale`
- Data rows have **19** values: the extra trailing value (e.g. `701 0004300 00001636` = magazzino + order + customer
  code) has no header. Some files carry an extra header `Tipo doc. riferimento cliente`.
- Example row: `2024@00040@0004300@0  @0000001@22032024@02042024@18090054BR   @__0   @2   @@@@@@@@0,2596   @701 0004300 00001636`

Field mapping into `insertLinea` (all values `.strip()`-ed):

| TXT header | insertLinea arg | Linea/Ordine field | Notes |
|---|---|---|---|
| `N.ro Ordine` | ordine | rif_ordine | kept zero padded |
| `Periodo riferimento` | anno | anno | |
| `Serie numerazione` | serie | serie | `00040` |
| `Appendice` | appendice | appendice | `'0'` for normal |
| `N.ro prog. riga` | n_riga | n_riga | `0000001` |
| `Data consegna` (ddmmyyyy) | data_scadenza | data_scadenza (Ordine and Linea) | parsed `%d%m%Y`; **this is the due date, not delivery** |
| `Cod. Articolo` | articolo | articolo | |
| `Variante` | variante | variante | values like `__0`, `_-1`, `OPN`; underscores stripped only in delivery file |
| `Quantita` | qta | qta | string → float by ORM |
| `Prezzo finale` | prezzo_ea | prezzo_ea | kept as string with comma |
| `Sconto unitario` | sconto | sconto_uni | |
| `Tipo doc. riferimento cliente` or **last value** | cliente | cliente | only in `importFileAndDelete` (`orders.py:1741-1744`); other paths pass `''` |
| `Data registrazione`, `Prezzo`, `Sconto 1..5` | — | ignored | |
| — | data_consegna=None, magazzino='', stato='INS' | | |

Row parsing: `values[i]` for each header index – a row with fewer fields raises `IndexError` and aborts the whole
file (`orders.py:1512-1513`, `1737-1738`). Empty trailing line → `strptime('')` error.

### 3.2 `vwloadOrders` — manual TXT upload
- URL `loadOrders` (name `CaricaOrdini`), GET/POST, group **Geko** (`orders.py:1137-1168`).
- POST multipart `files` (multiple, `accept=.txt`). Each file → `parseOrderFromFiles` (UTF-8) → for each row
  `insertLinea(... cliente='' ...)` (`orders.py:1148-1156`). Counter `linee_importate` = number of rows.
- Output: `Ordini/tplLoadOrder.html`, context `linee_importate`; `messages.success("Importate N righe da file TXT")`.
  Summary card with link to Lista Ordini. Alerts auto-fade after 5 s (JS).
- No transaction: a failing row in the middle leaves the previous rows imported and returns 500.
- `cliente=''` **overwrites** existing `Linea.cliente` (insertLinea updates cliente when different).

### 3.3 `scFetchOrderByMail` — scheduled IMAP fetch (every 30 min)
Scheduled in `Prod/updater.py:15` (APScheduler BackgroundScheduler started in `Prod/apps.py:8-10` →
**runs in every Django process/worker**, so with N workers the import runs N times concurrently → race on the
extract directory and duplicate imports). Also triggerable from Console (`console.py:35-41`).

Algorithm (`orders.py:1518-1609`):
1. Ensure `Metodo/media/mailAttachments` exists (relative to CWD!). Extract dir `Metodo/media/attachmentExtract`.
2. Read `EMAIL_ORDINI_USER`, `EMAIL_ORDINI_PASSWORD`, `EMAIL_SERVER` from Configuration; cursor `ID_EMAIL`
   (get_or_create – `int(None)` crashes on first run).
3. IMAP login (TLS default of imapclient), select INBOX, `search(['NOT DELETED','HAS ATTACHMENT'])`
   (non-standard `HAS ATTACHMENT` criterion).
4. If cursor > number of messages → reset 0. Batch = **last 100 UIDs + messages[cursor:cursor+100]**
   (index-based, not UID based; may overlap/duplicate – harmless thanks to ProcessedEmail).
5. For each message whose UID is not in `ProcessedEmail.message_id`: for each attachment `*.zip`:
   save to `mailAttachments/<filename>` (filename from mail, **unsanitised → path traversal**), `extractall` into
   extract dir (**zip-slip possible**), then for every file in extract dir: delete `*.pdf`; import `*.txt` with
   `importFileAndDelete` (errors logged and saved in `Errori`); delete the zip; `ProcessedEmail.create(uid, subject)`.
   A mail with no zip attachment is **never** marked processed (re-fetched every run).
6. `cursor = max(cursor, uid)` – mixes UID with list index (bug: cursor semantic broken); save; logout.
Side effects: DB writes via insertLinea, files, log `ordini`.

### 3.4 `scFetchOrderByMailDateRange(data_da, data_a)` (Console, POST `readMailDateRange`)
Same as 3.3 but IMAP `SINCE data_da BEFORE data_a+1day`, all messages in range, returns
`(mail_processate, ordini_importati, errori[])`; exceptions captured into `errori` (`orders.py:1611-1726`).
`ordini_importati` counts TXT files.

### 3.5 `importFileAndDelete(path)` and `insertLinea(...)` — the core import algorithm
`importFileAndDelete` (`orders.py:1728-1753`): parse as in 3.1, `cliente` from header `Tipo doc. riferimento cliente`
else last value; call insertLinea per row; finally delete file (not deleted on exception → file stays in the extract
dir and is retried by `scImporthOrderByMail`; the Console shows "stuck" files via `checkOrderStucks`).

`scImporthOrderByMail` (`orders.py:1755-1790`, hourly, `updater.py:16`): imports any leftover `*.txt` in the extract
dir with `cliente=''`, deletes file on success; errors only logged.

`insertLinea(ordine, data_consegna, data_scadenza, anno, articolo, qta, prezzo_ea, variante, sconto, magazzino,
cliente, serie, n_riga, stato, appendice)` (`orders.py:1390-1501`), executed **per line, without transaction**:

1. `ordExists` = Ordine with (rif, anno, serie) exists; `ordAppExists` = with (rif, anno, serie, appendice).
2. **Order exists with same appendice**: `get()` it; if stato ∉ {CON, ANN} → set `data_consegna`, `data_scadenza`,
   `stato` (= passed stato, i.e. `INS`: a re-import **resets LAV/SPE/SPP back to INS**) and save.
   If stato == CON → `ordineGiaConsegnato=True`, log error, create `Errori(name="Importazione Ordini",
   testo="Ordine gia consegnato", key...)`. If ANN → nothing (lines still imported!).
3. **Order exists only with another appendice**:
   - incoming appendice not in ('', '0', None) → new unsaved `Ordine(...)` (saved in the next step because stato INS);
   - else → pick the first sibling with empty/'0'/None appendice; **if none exists `ord` is unbound →
     UnboundLocalError** (`orders.py:1417-1423`).
   Then set data_consegna/data_scadenza; save unless CON (→ Errori as above) or ANN.
4. **No order with (rif, anno, serie)** → create `Ordine(stato=passed, ...)`.
5. If not `ordineGiaConsegnato`:
   a. **Annul old lines**: every non-annullata Linea of the exact key with `data_inserimento < today` gets
      `annullata=True` (`orders.py:1443-1447`). Consequence: re-importing an order on a later day annuls all lines
      (including ones already `emessa_bolla`/`evasione_parziale`!) and re-creates them fresh; same-day re-imports
      update in place.
   b. `lineaExists` = non-annullata Linea with same key + articolo + variante + n_riga.
   c. If not exists and a non-annullata Linea with same key + n_riga exists (different article/variant) →
      mark **all** lines with that key+n_riga (also annullate ones) `duplicata=True`.
   d. If still not exists and appendice == '0' → `lineaExists` = Linea with key(app '0') + articolo + qta +
      variante + n_riga (annullate included!) and `fromGekosas=True`.
   e. If exists → for every Linea with key + articolo + variante (**ignoring n_riga**, so all rows of the same
      article in the order) update `data_scadenza`, `qta`, `cliente` when different (qta compared float vs string →
      always "different" → always saved). **No StoricoScadenza row is written** for due-date changes.
   f. Else create Linea(rif, data_scadenza, anno, appendice, articolo, qta, prezzo_ea, variante, sconto_uni=sconto,
      magazzino, cliente, serie, n_riga).

Rewrite requirements derived: wrap each file in one transaction; add unique key on Ordine; replace the "annul by
date" heuristic with an explicit import batch id (annul lines of the order not present in the new file); keep the
CON-protection and `Errori` reporting; record due-date changes in StoricoScadenza.

### 3.6 XML order import — **not implemented**
- `vwImportaOrdiniXML` (URL `vwImportaOrdiniXML/`, name `ImportaOrdiniXML`, Geko) only `redirect('CaricaOrdini')`
  (`orders.py:2428-2434`). No link in menus.
- Orphan template `Ordini/tplImportaOrdiniXML.html` documents the intended contract: POST multipart `xml_file`
  (`.xml`, required) + `serie` (default `00040`, datalist 40/42/43/01); every `<Doc>` → one order with
  **appendice `CL`** (conto lavoro) and stato `INS`; every `<DocRow>` → a Linea; articles missing from
  `Anagrafica` are auto-created; summary context `ordini_importati`, `linee_importate`, `articoli_creati`.
- The only XML parser in the codebase is the inbound-DDT parser `Prod/inventario.py:847-1034`
  (`parse_ddt_xml_file`), structure `<Docs><Doc><DocType/><DocNumber/><DocDate>YYYY-MM-DD</DocDate><Anag><Name/></Anag>
  <DocRows><DocRow><Product><Code/></Product><Description/><Quantity/><Price/></DocRow></DocRows></Doc></Docs>`.
  If Metodo v2 implements XML order import, the natural mapping is: `DocNumber`→rif_ordine (zfill 7),
  `DocDate.year`→anno, form `serie`→serie, `'CL'`→appendice, row index→n_riga, `Product/Code`→articolo,
  `Quantity`→qta, `Price`→prezzo_ea, `Description`→Anagrafica.description. **This is a proposal; confirm with users.**

### 3.7 Gekosas (external MySQL) integration
All connections use hard-coded credentials (see §0.8) to host `gekosas.it`, schema `gekosas_1102815production`,
table `inc_ord` (one row per order line; columns used: `id, Per_rif, Ser_num, Ord_num, Appendice, Row_num, Reg_date,
Cons_date, Cod_art, Variante, Quant, Prezzo, Sc_1..Sc_5, Sc_uni, Prezzo_fin, Arr_date, Status, cons_geko, note,
autnote, notedmg, autdmg, rif_magazzino, rif_serie, cliente, manca, scorta, cl`; positional `row[7]`=Cons_date,
`row[20]`=Status).

- `scSyncOrders()` (`orders.py:1792-1857`) – **imported but not scheduled** (`updater.py:4`). Round-robin over
  non-CON orders 10 at a time (cursor `IDX_SYNC`); for each runs
  `select * from ...inc_ord where Ord_num=<rif> and Per_rif=<anno>` (string concatenation → SQL injection-prone,
  `orders.py:1825`, `1845`); maps `Status`: `Consegnato→CON`, `In lavorazione→LAV`, `In spedizione→SPE`, else `INS`;
  overwrites stato (even ANN!). Then for up to 10 CON orders without `data_consegna` copies `row[7]`.
  `fetchone()` None → TypeError crash.
- `scLoadOrderFromGeko()` (`migrateDb.py:32-102`) – not scheduled; copies 50 rows of `inc_ord` into local mirror
  table `Reports.gekoSAS_orders` (get_or_create on all columns, errors swallowed). Cursor `ID_MIGRAZIONE`
  (logic reads rows `id <= cursor` DESC – walks backwards).
- `createOrderFromGeko()` (`migrateDb.py:104-126`) – dead code; creates orders via insertLinea with appendice `'0'`
  for mirror rows whose Ord_num is not yet an Ordine; status map `Consegnato→CON, Lavorazione→'Lav', Bloccato→'BLC'`
  (invalid codes), `cons_geko` parsed `%d/%m/%Y`.
- `Reports/gekosas.py` (used by `Reports/views.py`, reports chapter): `consegneAltro/Montaggio/Sciolti/Schindler/
  ContoLavoro` (orders "In spedizione" filtered by `rif_magazzino` 701=montaggio, 711=sciolti, `cliente='00001809'`
  = Schindler, `cl=1` = conto lavoro), `suddivisioneTotale` (±15 days), `statisticheTotale`, and three functions
  with **SQL injection from user GET parameters**: `syncOrdini(date)` (`gekosas.py:134`, from `Reports/views.py:262`
  `YEAR`), `statisticheDiProduzione(dateIn,dateOut)` (`gekosas.py:150`, from GET), `getCustomQuery(testo,criterio,len)`
  (`gekosas.py:162-164`, from GET `testo`,`campo`,`len`; unknown criterio → executes empty query).
  Metodo v2: Gekosas appears legacy; if kept, use a read-only account, parameterised queries, secrets in env.

### 3.8 `vwInserimentoManuale` — manual order entry
- URL `vwInserimentoManuale/` (name `InserimentoManuale`), GET/POST, **Geko** (`orders.py:2019-2119`).
- Header POST fields: `rif_ordine` (required), `serie` ∈ (`40`,`41`,`42`,`43`,`01`), `appendice` (upper-cased) ∈
  (`T1`,`T2`,`R1`,`R2`,`CL`), `anno` = 4 digits (default current year), `dataScadenza` (only used client-side to
  prefill rows). Errors → `messages.error` per failed rule, nothing saved.
- Lines: indexed fields `n_riga_{r}`, `articolo_{r}`, `variante_{r}`, `qta_{r}`, `prezzo_{r}`, `scadenza_{r}` for
  **r in 0..49 only** (client counter keeps growing after deletes → rows with index ≥ 50 silently dropped).
  A row is used only if `scadenza_{r}` is present. No numeric validation of qta/prezzo (prezzo stored as typed).
- Order naming: if the distinct `scadenza` values among rows > 1, **one order per due date** named
  `rif.zfill(7) + "_" + scadenza(YYYY-MM-DD)`, else `rif.zfill(7)`. serie → `zfill(5)`.
- Each row → `insertLinea(..., stato='INS', data_consegna=None, sconto=None, magazzino=None, cliente=None)`.
  Inherits the "annul lines inserted before today" behaviour (re-entering the same rif on another day annuls the
  earlier lines).
- Context: `lineeCount`, `ordiniCount`, `lineeDict`, `allowed_series`, `allowed_appendici`, `current_year`,
  `form_data` (sticky values).
- UI (`tplInserimentoManuale.html`): button "Nuova linea" adds a row via JS (`newLine()`): n_riga read-only = row
  index (starting at 0), all fields required except variante, scadenza prefilled from header date; "Rimuovi" removes
  the row.

---------------------------------------------------------------------------------------------------------------

## 4. Endpoints — order management

### 4.1 `vwListaOrdini` — Lista Ordini
- URL `vwListaOrdini` (`ListaOrdini`), GET/POST, **Geko or Customer** (`orders.py:36-417`).
- Three modes:
  1. **GET `?serie=<s>[&statenot=<code>][&page=n]`** (menu "Fast"/"Express"): `Ordine.serie__contains=s`; if
     `statenot` → exclude that state and ANN. No ordering (paginator warning, unstable pages).
  2. **POST search** (sidebar form). Fields:
     `state` (exact code, from datalist), `priority` (urgenza), `location` (Location.area), `order` (rif),
     `article`, `dtSFrom/dtSTo` (scadenza range, both required to apply), `dtCFrom/dtCTo` (consegna range),
     `serie` (contains, maxlength 2), `appendice` (icontains, maxlength 2), `cliente` (Linea.cliente icontains →
     orders whose rif is in that list, rif only), `stati=lavorazione` (checkbox: stato ∈ {INS, SPP, LAV}),
     `ordini_malformati` (checkbox: serie or appendice null/empty).
     Rules: if `state` given and not `stati` → exact state; else exclude ANN. `order`: leading zeros stripped then
     `rif_ordine__iregex = '[^1-9]' + zif` (raw user input in a regex → regex injection/500 on invalid pattern;
     semantics "contains, preceded by a non-1-9 char"). `article`: key tuples of non-annullate/non-duplicate lines
     with `articolo icontains` whose rif is in the current queryset → OR filter; **if no line matches the empty
     `Q()` filters nothing → all orders returned** (`orders.py:168-175`). Order by `data_scadenza, rif_ordine`.
     Search params stored in `request.session['search_params']`.
  3. **GET `?page=n`** with session params → re-run mode 2 with stored params.
  4. Otherwise empty list.
- Pagination 500/page. For the page: `o.totale` = Σ qta of non-annullata non-duplicata lines (one aggregate query);
  `o.data_scadenza_originale` = min(`StoricoScadenza.data_scadenza_precedente`) for the order.
- Template `Ordini/tplLista.html`, context: `stati`, `ordini` (Page), `reparti`, `urgenze`, `dtsFrom`, `dtsTo`,
  `paginator`, `search_params`, `hide_ticket_bar`.
- UI: collapsible left filter sidebar (toggle button), quick date buttons (week / month / last month) for
  scadenza and consegna, bootstrap-table client sorting/search (custom sorters for numbers and dates via
  `data-sort`), "Mostra Dettagli" toggles column "Tempo Prod. (h)" (`calcola_tempo_produzione` filter → **one
  query per row**), state badges (CON green, INS grey, SPE yellow, LAV blue, SPP red, ANN dark), struck-through
  original due date when changed, notes column (LAV: show texts; else count), "Articoli" = totale.
  Actions per row: "Apri" (HTMX GET `vwOrdine` into `#uxModal` of `#ModalOrder`, modal reset to "Loading..." on
  close); Geko: "Lav" (not CON), "Spedisci" (not SPP/SPE/CON); Geko Admin: "Consegna" + "Annulla" (not CON),
  for CON: "Lav" + "Annulla"; "Cambia Ordine" link (Geko Admin, though the view allows Geko). State buttons are
  `hx-get` with `hx-confirm` and **no hx-target**: the response (empty `uxReply`) replaces the button content →
  the button visually disappears as feedback; the row isn't refreshed. Excel export form copies current filter values
  into hidden inputs and posts to `vwEsportaOrdini`. Warning banner when >3000 results (ZIP export).

### 4.2 `vwEsportaOrdini` — Excel export of orders
- URL `vwEsportaOrdini` (`EsportaOrdini`), **Geko or Customer** (`orders.py:419-808`).
- POST: same filters as 4.1 **except** `stati=lavorazione` means {INS, SPP} (no LAV – inconsistent,
  `orders.py:446` vs `151`). GET (no POST): non-ANN orders with `data_scadenza >= today-365`.
- Batches of 1000 for aggregates: `totale` (Σ qta valid lines) and `note_text` (comma-joined note texts).
- Output: if ≤3000 orders → `ordini_YYYYmmdd_HHMMSS.xlsx`; else ZIP `ordini_<ts>.zip` with files
  `ordini_parte_<i>_di_<n>_<ts>.xlsx` of 3000 rows each.
- Sheet `Ordini`, header (bold white on `#4472C4`, bordered):
  `Ordine | Stato | Data Scadenza | Data Consegna | Data Inserimento | Urgenza | Reparto | Note | N. Articoli`;
  Ordine = `serie[3:5].rif[-app]-anno` (crashes if serie None); dates `dd/mm/yyyy`; widths A20 B15 C:E15 F12 G15 H40
  I12. Logged to logger `ordini`.

### 4.3 `vwListaArticoli` — Lista Articoli (lines aggregated)
- URL `vwListaArticoli` (`ListaArticoli`), GET, **Geko** (`orders.py:810-1006`).
- Params: `search=1` (required to run), `dtSFrom/dtSTo`, `dtCFrom/dtCTo`, `stato` ∈ {`notCon`, `con`, `all`},
  `articolo`, `variante`, `area`, `page`, `export=1`.
- Rules: orders filtered by scadenza range, consegna range; `notCon` → orders INS/LAV/SPP and lines not
  emessa_bolla; `con` → orders CON/SPP and lines emessa_bolla; always exclude ANN orders and annullata/duplicata
  lines; lines also filtered by scadenza range directly. **If no order matches, lines are not restricted by order
  key** (`orders.py:859`) → wrong results. Article/variant icontains.
- Aggregation in Python by (rif, serie, app, anno, articolo, variante, data_scadenza): UM from Anagrafica;
  UM `M` → `totale += 1` (count of pieces/cuts) and `metraggio += qta`; else `totale += qta`.
  `scorporato_stato` = `si` (all rows), `no` (none), `parziale`. Area filter = case-insensitive substring of
  Anagrafica.location.area (articles without anagrafica are excluded when area is set).
- Output: page of 100 → `Ordini/tplListaArticoli.html` (context `linee`, `dtF`,`dtT`,`dtCF`,`dtCT`,`articolo`,
  `area`, `aree` (= Locations having an AllocazionePersonale), `search_executed`, `page_obj`).
  Export → `ListArticoli.xlsx` columns: `Serie, Ordine, Appendice, Anno, Articolo, Variante, Data Scadenza
  (dd/mm/yyyy), Quantita, Metri, Area, Scorporata (Sì/No/Parziale)`. `export=1` without `search=1` → `linee=None`
  → TypeError 500 (`orders.py:955-974`).

### 4.4 `vwOrdine` — order detail modal / print / manage; add note
- URL `vwOrdine` (`ModalOrdine`), GET/POST, **Geko or Customer** (`orders.py:1008-1135`).
- **POST `nota`** (+ hidden `ordine`,`serie`,`appendice`,`anno`): create `Note(user, testo)`, attach to order
  (`get()` on the key), then email: if user ∈ Geko → from `EMAIL_METODO_ADDRESS` to `EMAIL_CUSTOMER_ADDRESS`
  reply-to `EMAIL_GEKO_ADDRESS`; if user ∈ Customer → to `EMAIL_GEKO_ADDRESS` reply-to `EMAIL_CUSTOMER_ADDRESS`
  (both if in both groups). Subject `[METODO][Nota Ordine:<rif>]`, body `Nota: <text>` (HTML body, **text not
  escaped**). Synchronous SMTP; SMTP failure → 500 after the note is saved. Response `Ordini/uxReply.html`
  action `note` swapped into `#note` (the returned fragment's form is broken – no submit button, stray
  `Genera File Tecnico</button>` – so a second note needs reopening the modal).
- **GET `order`,`appendice`,`serie`,`anno`** (exact strings; missing → empty context):
  - `ordine = Ordine.objects.get(key)`.
  - tickets: `Ticket.ordine == "serie.rif.appendice.anno"` ordered by `-data_inserimento`.
  - tracce: `Tracciabilita` where FK order `rif_ordine iregex '[^1-9]<rif without leading 0>$'`, same anno and serie
    (appendice ignored).
  - linee: all non-annullata non-duplicata lines of the key, annotated `description` from Anagrafica (first match),
    ordered `n_riga, -data_inserimento`; each line gets `colore` cycling over a 6-colour palette whenever the
    article changes (`nextColor`, a **process-global counter** → colours differ between requests).
  - qlta: `ControlloQlta.ordine iregex '<serie without leading 0>.<rif without leading 0>$'` and year ≥ anno.
  - `lineeSum` = aggregate Σ qta → `{'qta__sum': x}`.
  - files: `FileOrdini` rif contains, anno contains, serie contains (stripped), appendice iregex (`.*` if `0`).
  - `data_scadenza_originale` = `data_scadenza_precedente` of the oldest StoricoScadenza.
  - `tempo_produzione` = `ordine.tempo_produzione_teorico()` (Σ qta×price / PREZZO_RESA_ORARIA, rounded 2).
- Rendering: `print=1` → `tplPrint.html` action `ordine` (compact printable sheet: header strip with ordine, serie,
  appendice, anno, scadenza, inserimento, articoli, stato, utente; table articolo/variante/qta/descrizione/riga with
  row colours: evasione+bolla green, evasione yellow, bloccata red, duplicate notice; files table; "Stampa" link);
  `manage=1` → `Ordini/tplManageOrder.html`; else `Ordini/mdlOrdine.html` (modal body).
- `mdlOrdine.html` UI (to preserve): 4 collapsible cards – **Testata** (dates with struck original, articoli, tempo
  teorico badge, serie/app/stato, "Gestisci" button for Geko Admin / GekoCapRep opening manage page in new tab),
  **Note** (list with user/date, delete "X" for Geko Admin via HTMX `vwDeleteNote`, textarea + "Salva Nota" HTMX POST),
  **Ticket** ("Apri Ticket" HTMX POST to `ticket_create_from_order`, list of linked tickets with state badge, close
  form with persone/ore for Geko → `ticket_close_from_order`), **Lotto/Busta** (inputs `lotto`, `busta` → HTMX POST
  `creaLottoCommessa` into `#LottoResults`). Lines table: row classes `linea-evasa` (green #28a745) /
  `linea-spedizione` (orange #fd7e14) / `linea-bloccata` (red #dc3545) / `linea-scorporata` (teal #17a2b8); article
  tooltip = description; collapsible columns (data inserimento, data consegna, riga + duplicate warning, cliente,
  prezzo DMG for Geko Admin). Line commands (only if order not SPE/CON, line not evasione_parziale, user Geko):
  **Evadi** (`s=SPP&evadi=1`), **Blocca** (if not blocked), **Scorpora** / **Re-incorpora**, **Annulla** (Geko Admin,
  hidden in collapsible column). Evasione lines show "Linea Evasa"/"Linea in Spedizione". "Spedisci Commessa" button
  (Geko, order not SPE/CON). Sections Tracciabilità, Controlli Qualità, Files; footer Close + Stampa (new tab).

### 4.5 `setOrdine` — state and line actions (HTMX)
- URL `setOrdine` (`SettaOrdine`), GET (and POST for split), **Geko** (`orders.py:1252-1388`). **State changes via GET
  without CSRF protection.**
- Params: `ordine`,`appendice`,`serie`,`anno` (normalised: trimmed; `''`/`none`/`null` → None, matched as
  "null or empty"), `a` ∈ {`stato`,`dividi`}, `s` (target state), `idLinea`, flags `evadi`,`blocca`,`annulla`,
  `duplica`,`scorpora`,`reincorpora` (truthy strings), POST `dividiQta`, `nuovaScadenza`, GET
  `dataScadenzaOriginale`.
- Order lookup `.filter(key).first()` → 404 "Ordine non trovato".
- **a=stato**: if `idLinea` → line of this order or 404; apply flags (§1.2) and save. Then if `s=='SPE'` and any line
  of the order `blocca_linea` → set `evasione_parziale=True` on every non-blocked line without `emessa_bolla`, and
  `s='SPP'`. Finally `ordine.stato = s; save()`.
  **Bug**: line-only actions (Blocca/Scorpora/Re-incorpora/Annulla in `mdlOrdine.html`) send no `s` → `stato=None`
  → `IntegrityError` (NOT NULL) → 500 **after** the line was saved (`orders.py:1292`, `1334`). Users see an error but
  the line change persists. Rewrite: line actions must not touch order state (except evadi → SPP).
- **a=dividi** with `idLinea`: without `dividiQta` → `uxManageOrder.html` action `dividiForm` (inputs dividiQta
  step 0.01, nuovaScadenza default = `dataScadenzaOriginale`; HTMX POST back with `a=dividi&manage=1` into
  `#manageContents`, confirm). With `dividiQta` → `dividiLinea(linea, qta, nuova_scadenza)`, then `oldLinea.save()`,
  `newLinea.save()` (no transaction) → action `results` (old/new line, info "nuovo ordine creato") or action `error`
  with the ValueError message.
- Default response: `Ordini/uxReply.html` with no action → empty body.

### 4.6 `dividiLinea(linea, qta, nuova_scadenza=None)` helper (`orders.py:2121-2299`)
1. `fQta=float(qta)`; if `fQta > linea.qta` → ValueError (Italian message). (No check for ≤0.)
2. `nuova_scadenza` default = line due date; string parsed `%Y-%m-%d` (bad/empty → ValueError, shown to user).
3. `scadenza_diversa = nuova != linea.data_scadenza`.
4. **fQta == linea.qta (move whole line)**: if scadenza_diversa → target rif = `rif.split('_')[0] + '_' + YYYY-MM-DD`;
   if an order with (target rif, anno, serie, appendice) exists reuse it, else create it copying `urgenza`, `tipo`,
   `location` from the original (`Ordine.objects.get` → may 500), stato INS, data_scadenza = new date. Re-key the line.
   Set line `data_scadenza`. Return `(linea, linea, created, name)`.
5. **fQta < linea.qta (split)**: `linea.qta -= fQta`; new Linea with same data, `qta=fQta`, `n_riga = 'D' + n_riga`
   (repeated splits produce `DD...`), placed in the same order or in the date-suffixed order (created as above).
6. Name format returned: `serie.rif.appendice-anno`.
Notes: original order's data_scadenza is never changed; empty original order may remain; no StoricoScadenza;
UI only reachable from manage page (Geko Admin / GekoCapRep, order not SPE/CON, line not in evasione).

### 4.7 `vwDeleteNote`
- URL `vwDeleteNote/` (`Cancella Nota`), GET, **Geko** (UI shows only to Geko Admin) (`orders.py:2301-2323`).
- Params `order`,`appendice`,`serie`,`anno`,`nota` (id). Removes the M2M link only (Note row remains). Response
  `uxReply.html` action `deleteNote` → "Nota Cancellata" (swapped into the X button). GET mutation, no ownership check.

### 4.8 `vwScorporazioneMassa` — mass "scorporo" (invoice exclusion)
- URL `vwScorporazioneMassa/` (`ScorporazioneMassa`), GET/POST, **Geko Admin** (`orders.py:1170-1250`); menu
  Toolbox › Fatturazione (admin only).
- GET search: `articolo` (required to search, icontains), `variante` (icontains), `dtFrom`/`dtTo`
  (default today-30 … today) applied to **Linea.data_consegna** (only delivered lines). Lines (annullate included,
  shown dark) ordered `-data_consegna, rif_ordine`. For each: matched Ordine (by rif+anno, then key with ''
  normalisation) → `ordine_display`, `consegnato` (order CON).
- POST: hidden search fields + `linea_ids` (all displayed ids) + `scorpora` (checked ids). For each id (excluding
  annullate): `scorporato = id in scorpora`; save changed ones; message "Aggiornate N righe." / "Nessuna modifica da
  salvare.". Ids are not re-validated against the search (any id can be posted).
- Template `Ordini/tplScorporazioneMassa.html`: quick period buttons (settimana corrente/passata, mese corrente/
  passato), table with per-row HTMX modal loading `vwOrdine` into `#modalOrdineContent<n>`, checkbox "Scorpora",
  "Salva Scorporazione".

### 4.9 `vwAttivaProduzione` — activate production
- URL `attivaProduzione` (`AttivaProduzione`), GET, **Geko** (`orders.py:1859-1879`); not linked in the menu.
- Any GET with query string: orders with `data_scadenza` in [`dtFrom`,`dtTo`] and stato `INS` → `LAV` (one save each,
  no transaction). Missing one date → 500. Template `Ordini/tplAttivaProduzione.html` (two date inputs + "Attiva");
  `ordini` in context but not displayed. Rewrite: POST + confirmation + count shown.

### 4.10 `vwOrdiniBoard` — Agenda Ordini (4-column board)
- URL `vwOrdiniBoard` (`OrdiniBoard`), GET/POST, **Geko or Customer** (`orders.py:2436-2537`).
- Filters (POST or GET): `ordine` (rif icontains), `serie`, `appendice`, `anno` (all icontains). Board loads only if a
  filter is set or POST. Quick views: `?appendice=CL&anno=<year>` (Conto Lavoro), `appendice=R` (Elettronica),
  `appendice=T` (Pulsanti).
- Orders stato ∈ {INS, LAV, SPP}, annotated `note_count`, ordered `data_scadenza, rif`. `numero_pezzi` = Σ qta of valid
  lines (aggregate by key).
- Columns by `data_scadenza` (week = Monday..Sunday of today): **Backlog** (< this Monday), **Questa Settimana**,
  **Prossima Settimana**, **Fra Due Settimane**. Orders without date or beyond 3 weeks are not shown.
- Template `Ordini/tplBoardOrdini.html` + `_colonnaOrdini.html` + `_cardCommessaPulsante.html` + `_agendaSearch*.html`.
  Card: key `serie.rif.app.anno`, pezzi chip, note chip, state badge (Ins/Lav/Parz), due `d/m`; left border colour
  LAV `#0dcaf0`, SPP `#fd7e14`, else black; click opens the order modal via HTMX. "Cerca" toggle: client-side
  substring search on `data-search`, highlights matches (yellow outline) and scrolls to the first; Esc clears.

### 4.11 `vwOrdiniBoardPulsanti` — Agenda Pulsanti
- URL `vwOrdiniBoardPulsanti` (`OrdiniBoardPulsanti`), GET, **Geko or Customer** (`orders.py:2539-2616`).
- Orders stato ∈ {INS, LAV, SPP} with `appendice icontains 'T'`; same `numero_pezzi`.
- 3 columns: **Scadute** (< this Monday), **Questa Settimana**, **Prossima Settimana**; each split into
  **Commesse Grandi** (`numero_pezzi > SOGLIA_PEZZI = 60`) and **Commesse Piccole** (≤ 60), sorted by due date
  (`_colonnaPulsanti.html`). Same card and search UI.

### 4.12 `vwConsegna` — delivery (ReadyPro bolla file)
- URL `consegna` (`Consegna`), GET, **Geko** (`orders.py:1881-2017`). **Mutating GET.**
- No params: list orders stato SPE or SPP with `totale` subquery (Σ qta valid lines) → `Ordini/tplConsegna.html`.
  Row actions: Apri (modal), "Rimuovi da Consegna" (`setOrdine s=INS`), "Consegna" (`consegnaSingolo?order=<id>`).
  Sidebar: Consegna Tutto/40/41/42/43/01; Etichette Tutte/40..43/01; Etichette -T variants.
- With `consegnaTutto`, `c40`, `c41`, `c42`, `c43`, `c01` (filters cumulative AND on `serie__contains`):
  filename `consegna<suffix>-<YYYYmmdd>.txt` (suffix `Tutto` or the series codes).
  For each selected order:
  1. Lines = non-annullata non-duplicata lines of the key.
  2. Header `Riferimento |ordine <serie>.<rif>\r\n` written if order SPE, or SPP with ≥1 line
     `evasione_parziale and not emessa_bolla`.
  3. For each line eligible = `((SPP and evasione_parziale and not emessa_bolla) or SPE) and not blocca_linea and not
     duplicata and not annullata and not emessa_bolla`:
     - write `<articolo>[.<variante without '_'>]||<int(qta)>|PZ|<prezzo_ea>\r\n` (variant omitted if empty/None;
       `int()` truncates decimals – meters lose fractions; `scorporato` lines are included at full price);
     - create `DocumentiTrasporto(key, articolo, qta, variante, cliente, n_riga)` (inviato=False, no DDT number yet);
     - line `emessa_bolla=True`, `data_consegna=today`.
  4. After the file: SPE → `CON` + `data_consegna=today`; SPP → `CON` + date only if every valid line has
     `evasione_parziale`.
  Response: `text/plain` attachment (the ReadyPro import file).
- Risks: double click / browser prefetch / two users → no locking; lines marked even if the download fails.
  Rewrite: POST, single transaction, `select_for_update`, generate a persisted "consegna batch" that can be
  re-downloaded.

### 4.13 `vwConsegnaSingolo` — delivery of a single order
- URL `consegnaSingolo` (`ConsegnaSingolo`), GET `order=<Ordine.id>`, **Geko** (`orders.py:2335-2426`).
- Same algorithm as 4.12 for one order, filename `consegna<serie>-<YYYYmmdd>.txt`; **does not set
  `Linea.data_consegna`** (inconsistent with 4.12). Invalid id → 500.

### 4.14 Lotti commessa — `vwCreaLottoCommessa` (traccia.py)
- URL `creaLottoCommessa` (`CreaLottoCommessa`), POST (HTMX from order modal), **Geko** (`Prod/traccia.py:734-771`).
- Params `ordine` (zfill 7), `serie`, `appendice`, `anno`, `lotto`, `busta`. Order lookup
  `rif_ordine__contains=ordine` + exact serie/appendice/anno (exceptions swallowed → not found).
  Location fixed `Location(path.code="GK01-PLS")` (Pulsanti). Creates two `Tracciabilita` rows:
  (`chiave="LOTTO"`, valore=lotto) and (`chiave="BUSTA"`, valore=busta), `data=now`. No validation of empty
  lotto/busta. Response `Tracciabilita/Lotti/uxReply.html`: "Tracciatura creata." / "Tracciatura non creata,
  Controllare i dati.". **GET → UnboundLocalError (context undefined) → 500** (`traccia.py:771`).

### 4.15 Scadenze history and Cambia Ordine (`Prod/views.py`)
- `storico_scadenze_ordine` — URL `ordine/<int:ordine_id>/storico-scadenze/`, GET, **login only** (any
  authenticated user, no group check) (`views.py:45-59`). 404 if missing. Context `ordine`, `storico`
  (StoricoScadenza of the order, newest first) → `Prod/tplStoricoScadenzeOrdine.html` (info card; table data
  modifica `d/m/Y H:i:s`, precedente → nuova, utente, motivo; buttons "Cambia ordine", "Lista ordini").
- `vwCambiaOrdine` — URL `ordine/<int:ordine_id>/cambia-ordine/` (`cambia_ordine`), GET/POST, **Geko or Geko
  Admin** (`views.py:62-148`); UI link only for Geko Admin (`tplLista.html:228`) and Toolbox menu item points to
  Lista Ordini.
  - Form `CambiaOrdineForm` (`Prod/forms.py:14-43`): `rif_ordine` (required, ≤50), `serie` (≤20, optional),
    `appendice` (≤20, optional), `data_scadenza` (date, optional), `motivo` (textarea optional). Anno is not editable.
  - Lines to update = Linea with the **current** key (rif, serie, appendice, anno).
  - Validation: another Ordine with the new (rif, serie, appendice, same anno) → non-field error
    "Esiste già un altro ordine con lo stesso Numero/Serie/Appendice per l'anno X".
  - In one `transaction.atomic()`: bulk update lines (rif, serie, appendice, **data_scadenza** of all lines =
    new date); if old date existed and differs → `StoricoScadenza(ordine, prev, new, utente, motivo or
    'Cambio ordine (numero/serie/appendice)')`; update order. Success message "Ordine aggiornato: N linee correlate
    allineate ai nuovi dati." → redirect `ListaOrdini`.
  - Bugs: clearing the date with an existing one → `data_scadenza_nuova=None` → IntegrityError (rolled back, 500);
    linked string-keyed data (`DocumentiTrasporto`, `FileOrdini`, `Ticket.ordine`, `ControlloQlta.ordine`) is not
    re-keyed → orphaned; inputs not normalised (padding).
  - Template `Prod/tplCambiaOrdine.html`: current data card, form card with JS confirm "Confermi la modifica
    dell'ordine e delle N linee collegate?", table of lines to be updated, history table with link to full history.
- Dead templates: `Prod/tplPosticipoScadenzaLinea.html` and `Prod/tplStoricoScadenzeLinea.html` (line-level postpone
  and history) reference URL names `posticipa_scadenza_linea` / `storico_scadenze_linea` that do not exist; no view
  renders them. StoricoScadenza supports `linea` FK for this unimplemented feature. Metodo v2 may implement
  "posticipa scadenza linea" (fields: `nuova_data_scadenza`, `motivo`) properly.

---------------------------------------------------------------------------------------------------------------

## 5. DDT (outbound "file tecnico") — `Prod/ddt.py`

### 5.1 `vwDDT` — Genera File Tecnico
- URL `vwddt` (`DDT`), GET/POST, **Geko** (`ddt.py:29-66`). Menu Ordini › Comandi › "File Tecnico DDT".
- Loads `EMAIL_FIETECNICO_ADDRESS` (to) and `EMAIL_METODO_ADDRESS` (from & reply-to) on every request.
- GET: first **500** `DocumentiTrasporto` with `inviato=False` (no ordering!) + total count → `Ordini/tplDDT.html`
  (table ordine `serie2.rif.anno[-app]`, articolo, variante, qta, appendice, data inserimento, checkbox
  `linea_<pk>` default checked, "Seleziona tutto"; sidebar: `noDDT` text required, "Genera e Invia", "Genera Bolla";
  warning when total > 500).
- POST `noDDT` + checked `linea_<pk>`: for each of the (re-queried) first 500 unsent rows that is checked:
  `no_ddt = noDDT`, `data_ddt = today`, `inviato = True`, save. Then `generate_file(rows, noDDT)` and
  `sendEmail("File Tecnico", "Come da Oggetto", from, to, reply, file_path, filename)`; redirect to `DDT`.
- **DDT numbering is manual**: the operator types the number of the DDT issued by the ERP; there is no generator, no
  uniqueness check (same number can be reused), no year handling (number repeats every year; ristampa uses
  `data_ddt__year`). Rows are marked sent before the email is attempted; no transaction; SMTP failure → 500 with rows
  already `inviato=True`; empty selection still sends an empty file.

### 5.2 `generate_file(ddts, no_ddt)` (`ddt.py:68-108`)
- Filename `DDT_3649_<current year>_<no_ddt>.txt` written to `MEDIA_DIR/fileTecnici/` (`no_ddt` unsanitised in
  filename). (Old files are purged by `scMaintenance`, other chapter.)
- One line per row, CRLF, fields separated by `@`; first record type `T+R`, following ones `RIG`:
  `<T+R|RIG>@3649@<data_ddt dd/mm/YYYY>@<no_ddt>@<articolo>@<variante or ''>@<qta>@<anno>.<serie>.<rif>[.<appendice>].<n_riga>,00@1`
  - `3649` = fixed supplier code (Geko at the customer).
  - qta = `str(qta).replace(".0","").replace(".",",")` → **bug**: `100.05` → `1005`, `1.05` → `15`
    (`ddt.py:78`). Correct rule: integer if whole, else comma decimal.
  - serie, rif, appendice, n_riga with leading zeros stripped; `-` when None; appendice segment omitted when `'0'`
    (but `'-'` printed when None).
- Returns `(file_path, filename)`.

### 5.3 `vwGeneraBollaDaDDT` — regenerate a bolla file from DDT rows
- URL `vwddt/generaBolla` (`GeneraBollaDaDDT`), POST only (GET → redirect `DDT`), **Geko** (`ddt.py:110-154`).
  Triggered by the "Genera Bolla" button (`formaction`, `formnovalidate`) of the DDT page.
- Selected unsent rows (`linea_<pk>`, all unsent rows considered, not only first 500) grouped by
  (serie, rif, anno, appendice) in order of appearance → same ReadyPro format as Consegna:
  `Riferimento |ordine <serie>.<rif>` then `<articolo>[.<variante w/o '_'>]||<int(qta)>|PZ|<prezzo>`; price taken from
  the matching Linea (key+articolo+variante+n_riga, first) or empty. Filename `bolla-<YYYYmmdd>.txt`.
  **Read-only**: no email, no flag changes.

---------------------------------------------------------------------------------------------------------------

## 6. Labels / barcodes — `Prod/etichette.py`, `vwPrint`

### 6.1 Label export format (shared by both views)
xlsx, single sheet, **no header row**, one row per order (data source for an external label-printing mail merge):

| Col | Content |
|---|---|
| A (0) | `rif_ordine` without leading zeros |
| B (1) | `data_consegna` (date written without number format → shows as Excel serial) |
| C (2) | `data_scadenza` (same) |
| D (3) | state label (`LIST_STATUS`) |
| E (4) | '' |
| F (5) | appendice |
| G (6) | '' |
| H (7) | anno |
| I (8) | `'0003659'` fixed customer/supplier code (note: DDT file uses `3649`) |
| J (9) | (not written) |
| K (10) | serie without leading zeros |
| L (11) | **barcode** = `anno + serie[2:] + rif.lstrip('0').zfill(5)` e.g. 2024/00040/0004300 → `202404004300` |

### 6.2 `vwEtichette`
- URL `vwEtichette/` (`Etichette`), GET, **Geko** (`etichette.py:30-108`).
- Orders SPE or SPP; params `etichetteTutto`, `e40`, `e41`, `e42`, `e43` (serie contains), optional `appendice`
  (contains, e.g. `T`); ordered by rif. Filename `Etichette[_T_]<suffix>.xlsx`. If no recognised flag → redirect
  `/PRODUZIONE/consegna`. **`e01` links exist in `tplConsegna.html` but `e01` is not handled** → just redirects.
  Must be called **before** Consegna (after Consegna orders are CON and disappear).

### 6.3 `vwRistampaEtichette` — reprint labels by DDT
- URL `vwRistampaEtichette/` (`RistampaEtichette`), GET/POST, **Geko** (`etichette.py:146-236`); menu Toolbox › Stampe.
- GET `no_ddt` (+ `anno_ddt`, default current year, invalid → current): orders whose key appears in
  `DocumentiTrasporto` with that `no_ddt` and `data_ddt.year == anno_ddt` (`_cerca_ordini_per_ddt`).
  If none: error message, listing other years where that number exists (`_anni_disponibili_per_ddt`) with quick
  links `?no_ddt=X&anno_ddt=Y`.
- POST `generate_labels=1`, `no_ddt`, `anno_ddt`, `ordini` (list of Ordine ids, checkboxes default checked):
  xlsx `Etichette_DDT_<no>_<anno>.xlsx` in format 6.1. No selection → error and form re-shown.
- Template `Ordini/tplRistampaEtichette.html` (search form, table ordine/serie/anno/app/stato badge/scadenza/
  consegna/reparto, select-all).

### 6.4 Barcode printing (`vwPrint`, traccia.py) and QR
- URL `print` (`Print`), GET `barcode=<value>`, **Geko** (`traccia.py:557-570`) → `tplPrint.html` action `barcode`:
  SVG **Code128** rendered client-side by JsBarcode (`jsbarcode-format="code128"`, bold font, textmargin 0) on load.
  Without `barcode` → UnboundLocalError 500.
- `tplPrint.html` action `ordine`: printable order sheet (see 4.4).
- **No QR code generation exists** anywhere in the app.

---------------------------------------------------------------------------------------------------------------

## 7. Tabulati / allocation — `Prod/allocazione.py`

Common: `get_previous_and_next_week(d)` (`allocazione.py:16-28`) returns
(prev Mon, prev Sun, this Mon, this Sun, next Mon, next Sun) for the week containing `d`. `week` param =
ISO date (invalid → 500). Value→time: `tempo (h) = Σ(qta × float(prezzo_ea with ',' → '.')) / PREZZO_RESA_ORARIA`
(computed in SQL; a non-numeric price breaks the cast).

Aggregation used by settimanale & scorte (`allocazione.py:82-114`, `230-262`): lines annotated with `um` (first
Anagrafica UM), `location` (Anagrafica.location.area), grouped by (location, articolo, variante, um,
`rif_ordine_filtered`) where `rif_ordine_filtered = rif_ordine` only when UM = `M` (cables/meters are listed per
order) else NULL (pieces aggregated across orders). Output per group: `qtaSum`, `sumPrezzo`, `tempo`;
`oreTotali = Σ tempo`.

### 7.1 `vwTabulatiSettimanali` — weekly workload per area
- URL `tabulatiSettimanali/` (`tabulatiSettimanali`), GET, **Geko** (`allocazione.py:30-171`); menu Tabulati › Settimanale.
- Without `articoli`: page `allocazioneProd/tplTabulatoSettimanale.html` listing every `AllocazionePersonale` (area +
  coloured resource badges); clicking an area does HTMX GET `?articoli=1&location=<id>` into `#lista`.
- With `articoli=1&location=<id>[&week=YYYY-MM-DD][&print=1][&export=excel]`:
  - Orders with `data_scadenza` in the week, appendice ≠ `'0'`, stato **not** in {CON, ANN, LAV, SPE} (i.e. INS and
    SPP; LAV excluded – the commented SPP exclusion `allocazione.py:61`).
  - Lines in the week matching those order keys, not annullata/duplicata/emessa_bolla, appendice ≠ '0', articolo in
    the area's Anagrafica articles. **If there are no orders the empty `Q()` matches every line** (`allocazione.py:73-85`).
  - Output: `allocazioneProd/uxArticoli.html` (HTMX fragment: area, total hours, prev/next week buttons via HTMX,
    "Stampa" new tab, "Excel"; table Reparto, Articolo, Variante, Ordine, Quantità, Tempo (h), total footer) or
    `printArticoli.html` (standalone printable) or xlsx `Articoli_<area>_<start>_<end>.xlsx` columns
    `Reparto, Articolo, Variante, Ordine, Qta, Tempo (ore)` (rounded 2).
  - `location` missing → `iLocation=None` → articles with null location.

### 7.2 `vwTabulatiScorte` — stock tabulato (small orders, Pulsanti)
- URL `tabulatiScorte/` (`tabulatiScorte`), GET, **Geko** (`allocazione.py:174-286`); not linked in menu.
- Area fixed **Location id 4** ("Area Pulsanti", `allocazione.py:178`) – environment specific.
- Orders in the week with `totale` (Σ qta of lines not annullata/duplicata/emessa_bolla) **≤ 60** (orders with no
  such lines have NULL total → excluded). Intended exclusion "appendice='0' and stato in CON/ANN/SPE/SPP" is written
  as a single `exclude(appendice='0', stato__in=[...])` = NOT(A AND B) (`allocazione.py:214-216`) → CON/SPE/SPP orders
  with a real appendice are **included** and appendice-'0' orders in INS/LAV are included. Lines then filtered by
  `rif_ordine__in` (rif only, ignoring serie/appendice/anno).
- Output `allocazioneProd/tplTabulatoScorte.html` (full page, prev/next week links, Stampa) or `printArticoli.html`.
  Title: "solo commesse con meno di 60 pezzi".

### 7.3 `vwCommessePerTabulati` — orders per tabulato (Anagrafica.tabulato 0-5)
- URL `tabulatiAnagrafica/` (`tabulatiAnagrafica`), GET, **Geko** (`allocazione.py:289-402`); menu Tabulati › Anagrafica.
- Params `tabulato`, `start_date`, `end_date` (ISO), `export=excel`. If any missing → tabulato 0 and current week,
  but `articoli_tabulato` stays empty (computed before the default) → no results.
- Orders with `data_scadenza` in range, appendice ≠ '0', stato ∉ {CON, ANN, SPE}, having ≥1 line (any flag, even
  annullata) whose article belongs to the tabulato.
- Output `allocazioneProd/tplCommessePerTabulati.html` (select of distinct tabulato values, date range, collapsible
  list of tabulato articles, table rif/serie/app/anno/scadenza/stato + per-row modal loading `vwOrdine` via HTMX,
  Excel link) or xlsx `CommesseTabulato_<tab>_<start>_<end>.xlsx` columns
  `Commessa (serie.rif.app-anno), Articolo, Variante, Quantità, Stato (code), Data Scadenza (dd/mm/YYYY)` — exports
  **all** valid lines of each order, not only tabulato articles. N+1 query per order.
- `allocazioneProd/error.html` is unused (and uses a non-existent block `content`).

---------------------------------------------------------------------------------------------------------------

## 8. Template tags (`Prod/templatetags/custom_filters.py`)
| Filter | Behaviour |
|---|---|
| `has_group(user, name)` | group membership test (DB query per call) |
| `percentage(v)` | `'{:.2%}'` |
| `toHours(td)` | timedelta → hours `'%.2f'`; falsy → 0 |
| `div`, `mul`, `sub` | float arithmetic → `'%.2f'` string; error → None (`mul`/`sub` don't catch TypeError) |
| `get(mapping, key)` | `mapping.get(key, [])` |
| `lstrip(v)` | **`v.strip('0')`** – strips both ends despite the name |
| `toInt(v)` | `int(v)` |
| `calcola_tempo_produzione(ordine)` | `ordine.tempo_produzione_teorico()` → `'%.2f'`, error → `ERR: <msg[:20]>` (1 query per call) |
| `hours_to_hhmm(h)` | decimal hours → `HH:MM` (rounded minutes) |
| `timedelta_to_hhmm(td)` | timedelta → `HH:MM` |

---------------------------------------------------------------------------------------------------------------

## 9. Layout templates
- `tplCore.html`: Bootstrap 5 (django-bootstrap5), jQuery 3.7.1, JsBarcode, bootstrap-table, htmx (static files).
  Dark navbar `{{site}}@{{app}}` with menus: **Tracciatura** (batterie, qualità, generale, file/foto, display),
  **Ordini** (Lista Ordini, Agenda Ordini, Agenda Pulsanti, Lista Ordini Fast, Lista Ordini Express, Lista Articoli;
  Importa Ordini, Inserimento Manuale; Consegna, File Tecnico DDT; Pianificazione, Tabulato Settimanale, Anagrafica),
  **Magazzino** (inventory chapter), **Toolbox** (Cambia Ordine→Lista, Ristampa Etichette, Scorporazione di Massa
  (Geko Admin), Annunci, Console, /admin), **Ticket**, Logout. Fixed bottom **ticket bar** (HTMX GET `/TICKETS/bar/`
  on load and every 60 s; dismissed ticket ids kept in `localStorage['tk_dismissed']`, max 500) unless
  `hide_ticket_bar`.
- `tplProductionHome.html` (`vwProductionHome`, URL `''`, **no auth**): empty page that HTMX-loads `AnnuncioGeko`
  into a modal and shows it if non-empty (announcements chapter).
- `tplPrint.html`: standalone printable page (barcode / order sheet).

---------------------------------------------------------------------------------------------------------------

## 10. Consolidated defects, risks and dead code

### Security
1. Hard-coded Gekosas MySQL credentials (`orders.py:1817-1821`, `migrateDb.py:34-39`, `Reports/gekosas.py` ×10).
2. SQL injection on external MySQL from HTTP params: `Reports/gekosas.py:134` (`syncOrdini`), `:150`
   (`statisticheDiProduzione`), `:162-164` (`getCustomQuery`, also `limit <len>`); string-built queries in
   `orders.py:1825, 1845` (DB-sourced values).
3. Regex injection (user text into `__iregex`): `orders.py:166, 299, 461` (rif), `1059`, `1081` (DB values).
4. State-changing GET endpoints without CSRF: `setOrdine`, `vwDeleteNote`, `vwConsegna`, `vwConsegnaSingolo`,
   `vwAttivaProduzione`, `vwEtichette` (read-only), `wotHandle`.
5. Mail attachment filename and zip contents written unsanitised (`orders.py:1568-1575`, `1680-1687`): path
   traversal / zip-slip. `no_ddt` in file name (`ddt.py:70`).
6. Note text inserted raw into an HTML email (`orders.py:1029, 1036`).
7. `storico_scadenze_ordine` only requires login (any user, incl. non-Geko) (`views.py:45`); `vwProductionHome`
   no auth; `wotHandle`, `genCodiceBatteria`, `insertTracciaCavi` (traccia, other chapter) no auth.
8. Customers see every order (no customer scoping).

### Correctness
9. `setOrdine` line actions without `s` set `Ordine.stato=None` → IntegrityError after saving the line
   (`orders.py:1292, 1334`).
10. No transition validation; any `s` accepted (`orders.py:1334`).
11. `insertLinea`: UnboundLocalError when order exists only with other appendices (`orders.py:1417-1423`); re-import
    annuls all lines inserted before today including shipped ones (`1443-1447`); update by article ignores n_riga
    (`1474-1494`); re-import resets LAV/SPE/SPP to INS (`1398-1405`); ANN orders still get lines; no
    StoricoScadenza on due-date change; qta compared as float vs str (`1485`).
12. `parseOrderFromFiles` UTF-8 decoding vs latin-1 in mail path (`orders.py:1508-1511`); short rows → IndexError.
13. `scFetchOrderByMail` cursor mixes IMAP UID and list index (`orders.py:1546-1551, 1602`); mails without zip never
    marked processed; `int(None)` on first run (`1533`).
14. Empty `Q()` OR-filters meaning "everything": `orders.py:168-175` (article search), `859` (ListaArticoli),
    `allocazione.py:73-85` (settimanale), `allocazione.py:330-341`.
15. `vwTabulatiScorte` wrong exclude semantics (`allocazione.py:214-216`), rif-only match (`232`), hard-coded
    location id 4 (`178`).
16. `vwCommessePerTabulati` defaulting leaves `articoli_tabulato` empty (`allocazione.py:300-308`); export includes
    non-tabulato lines (`370-386`).
17. DDT qta formatting bug `.replace(".0","")` (`ddt.py:78`); DDT rows marked `inviato` before email; no DDT number
    uniqueness; `vwDDT` POST only processes first 500 unordered rows (`ddt.py:37, 43-50`).
18. Consegna: `int(qta)` truncation (`orders.py:1952-1954, 2376-2378`); header written for SPE even with no eligible
    lines; `vwConsegnaSingolo` does not set `Linea.data_consegna` (`2394-2395`); no locking / idempotency.
19. `vwEtichette` ignores `e01` (`etichette.py:36-79` vs `tplConsegna.html`); dates written without format.
20. `vwListaOrdini` GET `serie` mode unordered pagination (`orders.py:48-59`); `vwEsportaOrdini` `lavorazione`
    excludes LAV (`446`) and `serie[3:5]` crashes on None (`669`, `760`).
21. `vwListaArticoli` `export` without `search` → TypeError (`orders.py:955-974`).
22. `vwOrdine` `.get()` 500 on missing/duplicate order (`1022`, `1054`); `print` shadows builtin (`1126`); note reply
    fragment broken (`Ordini/uxReply.html`).
23. `dividiLinea` / `setOrdine` split not atomic (`orders.py:1355-1356`); negative qta accepted (`2145`).
24. `vwCambiaOrdine` clearing date → IntegrityError (`views.py:110-117`); string-keyed related records not re-keyed.
25. `vwAttivaProduzione` crashes on partial params (`orders.py:1866`); GET mutation.
26. `vwCreaLottoCommessa` GET → UnboundLocalError (`traccia.py:771`); `vwPrint` without barcode → UnboundLocalError
    (`traccia.py:570`).
27. `scSyncOrders` overwrites ANN, crashes on missing remote row (`orders.py:1828-1841`).
28. Scheduler started in `AppConfig.ready()` of every process (`apps.py:8-10`) → duplicated concurrent imports.

### Performance
29. N+1: `calcola_tempo_produzione` and `o.note.all/count` per row in Lista Ordini; per-order line queries in
    Consegna, CommessePerTabulati export; huge OR-of-tuples filters (500-1000 terms) built repeatedly.
30. Lack of FK between Ordine and Linea forces 4-column joins everywhere.

### Dead / unused
31. `vwImportaOrdiniXML` (redirect) + orphan `Ordini/tplImportaOrdiniXML.html`; `tplPosticipoScadenzaLinea.html`,
    `tplStoricoScadenzeLinea.html` (broken URL names); `allocazioneProd/error.html`; `createOrderFromGeko`,
    `scLoadOrderFromGeko`, `scSyncOrders` (not scheduled); `parseOrderFromFiles` duplicated parsing logic in 3 places;
    unused imports (`TEMPORARY_REDIRECT`, `TAKEN_FROM_ARGUMENT4`, `urllib.response`) in `orders.py:1-2`,
    `etichette.py:1-2`; `vwAttivaProduzione` and `vwTabulatiScorte` unlinked; `Linea.sconto_1..5` never filled.

---------------------------------------------------------------------------------------------------------------

## 11. Rewrite checklist (Metodo v2)
1. Add `Ordine` PK FK on `Linea` + unique (rif, serie, appendice, anno); keep the 4 string fields for
   display/search; migrate data (resolve duplicates found by `ordiniConsegnatiDoppi`).
2. Explicit state machine (§1.1) enforced server-side; line actions separate from order state.
3. Import service (TXT / mail / future XML) with per-file transaction, import batch id, CON protection + `Errori`,
   StoricoScadenza on due-date changes, idempotent mail processing by UID, sanitized attachment handling,
   single scheduler instance (e.g. separate worker/cron).
4. Delivery as POST command creating a persisted `Consegna` batch (lines, file content, user, timestamp), atomic with
   row locks; downloadable again; DocumentiTrasporto created in the same transaction.
5. DDT: store DDT number + year with unique constraint; correct qta formatting; send email after commit (outbox).
6. Preserve exact file formats: ReadyPro bolla (§4.12), DDT file tecnico (§5.2), label xlsx (§6.1), Excel exports
   (§4.2, 4.3, 7.1, 7.3).
7. Preserve UI behaviours: HTMX order modal from every list/board, confirm dialogs, board columns and Grandi/Piccole
   split (threshold 60 configurable), quick filters (Fast=43, Express=42, CL/R/T), print views, ticket bar.
8. Move secrets/config (resa oraria, supplier codes 3649/0003659, location ids, thresholds) to settings.
