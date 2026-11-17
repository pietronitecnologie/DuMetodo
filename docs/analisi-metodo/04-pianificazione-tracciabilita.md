# 04 – Pianificazione, Tracciabilità & Qualità, Console, Annunci, File, Scheduler

Functional specification for the DuMetodo rewrite of the `Prod` app (branch `Metodo`, checkout `/home/user/metodo-branch`).
Scope of this chapter:

| Source file | Lines | Content |
|---|---|---|
| `Prod/pianificazione.py` | 1275 | Capacity planning page, soglie (thresholds), XLSX export, report e-mail (manual + scheduled) |
| `Prod/traccia.py` | 900 | Traceability (batterie, cavi, generale, lotti), controllo qualità, reclami, display test, work-order traccia, barcode print |
| `Prod/files.py` | 90 | Photo/file upload attached to orders (FileOrdini) |
| `Prod/console.py` | 324 | Admin "Console": manual triggers of jobs + maintenance commands + log viewer |
| `Prod/annunci.py` (+ `Prod/views.py::vwAnnuncioGeko`) | 70 | Announcements (popup on Produzione home) |
| `Prod/maintenance.py` | 85 | `scMaintenance` (anagrafica sync, technical file cleanup) |
| `Prod/updater.py` (+ `Prod/apps.py`) | 21 | APScheduler bootstrap |
| `Reports/produzione.py` | 2352 | Reports on Prod data (inventory in §10) |

All URLs below are relative to the mount point `/PRODUZIONE/` (`Metodo/urls.py`: `path('PRODUZIONE/', include("Prod.urls"))`).

Conventions used: **[BUG]**, **[SEC]** (security), **[RACE]**, **[DEAD]**, **[TX]** (missing transaction), **[KEEP]** (UI behaviour worth preserving), **[DECIDE]** (rewrite must take an explicit decision).

---

## 0. Cross-cutting facts

### 0.1 Authorisation model
* Groups are checked by name: `Geko`, `Geko Admin`, `Customer` (helpers `isGeko`, `isGekoAdmin`, `isCustomer` duplicated in `pianificazione.py:18`, `traccia.py:32-36`, `console.py:16-23`, `files.py:15-19`, `annunci.py` imports from `views.isGeko`).
* Pattern everywhere: `@user_passes_test(isX)` **outside** `@login_required`. Effect: `user_passes_test` runs first; anonymous user fails the group test → redirect to `LOGIN_URL=/login/`. A logged-in user without the group is also redirected to the login page (no 403) – confusing UX; rewrite should return 403.
* Superusers are **not** implicitly in any group (checks are pure group membership).
* No global login middleware (`Metodo/settings.py:54-62`), so views without decorators are **public**: `wotHandle`, `genCodiceBatteria`, `insertTracciaCavi` (`traccia.py:587,596,605`), `vwAnnuncioGeko` (checks auth internally).
* Template-only gating (`{% if user.is_superuser %}` in `Console/tplConsole.html:54,65`) is not enforced server-side.

### 0.2 Configuration keys (table `Prod.Configuration`, `chiave`/`valore` both `CharField(max_length=50)`)
| Key | Used by | Meaning / format |
|---|---|---|
| `PREZZO_RESA_ORARIA` | planning, reports | €/hour used to convert line value to hours. Comma or dot decimal. Default 1 if missing/invalid (planning only). |
| `SOGLIA_<AREA_NORMALIZZATA>` | planning | Weekly capacity in hours for an area. Area name normalised `area.upper().replace(' ', '_')`. Stored with comma decimal (`"45,0"`). |
| `EMAIL_REPORT_PRODUZIONE_TO` | planning report | Recipients, separated by `,` or `;`. **Limited to 50 chars by column size** (Postgres enforces) → effectively 1–2 addresses. [DECIDE] use a proper list table. |
| `EMAIL_REPORT_PRODUZIONE_CC` | planning report | CC list, same format and same 50-char limit. |
| `EMAIL_METODO_USER` | mailing | SMTP username, also used as `From`. |
| `EMAIL_METODO_PASSWORD` | mailing (`Prod/mailing.py:25`) | **SMTP password stored in clear text in DB** – value not reproduced here. [SEC] move to secret store/env. |
| `EMAIL_SERVER` | mailing | SMTP host (port hard-coded 587 + STARTTLS, `mailing.py:27,53`). |
| `ARTICOLO_TRACCIA_BATTERIE` | batterie | Pseudo-article code identifying battery items. Read **once at import time** (`traccia.py:22-25`). |
| `ARTICOLO_TRACCIA_CAVI` | cavi | Pseudo-article code identifying cable items. Read at import time (`traccia.py:27-30`) **and** again per request in `insertTracciaCavi` (`:639`). |
| `ITEM_CODE_CAVI` | cavi | Prefix for generated cable item codes. |

Hard-coded master data referenced by code (must exist or the views crash):
* Location `path.code == "GK01-UDEF"` (default/undefined department for new Anagrafica) – `maintenance.py:49`, `console.py:102`.
* Location `"GK01-PLS"` (Pulsanti – used for LOTTO/BUSTA traces) – `traccia.py:752`.
* Location `"GK01-CVI"` and Asset `"GKAST-107"` (cable "Intestato" button) – `Cavi/tplLista.html:112`.
* `Check` rows with **id 8 = Distinta** and **id 9 = Disegno** (`traccia.py:104,111`). [DECIDE] replace by flags/codes, never rely on PKs.
* Absolute production URL `https://metodo.gekotecnologie.com/...` hard-coded in `pianificazione.py:1142`, `Console/tplConsole.html:15,16,58,59,133,143,152,160`, `tplCore.html` (Lista Ordini Fast/Express). Reply-To address hard-coded `pianificazione.py:1143`. [DECIDE] make configurable.

### 0.3 Filesystem paths (all relative to CWD → break if process not started from repo root)
* `Metodo/media/attachmentExtract/` – extracted order TXT files (console, orders import).
* `Metodo/media/logs/{metodo,ordini,schedulazioni}.log` – log files (settings `LOGGING`, `Metodo/settings.py:160-202`).
* `Metodo/media/mailAttachments/pianificazione_reports/` – saved XLSX report attachments.
* `Metodo/media/fileTecnici/` – technical files cleaned up by `scMaintenance`.
* Upload dirs: `MEDIA_ROOT/file/` (FileOrdini), `MEDIA_ROOT/Reclami/` (Reclami.file).
* [SEC] Everything lives under `MEDIA_ROOT` (`Metodo/media/`) and `MEDIA_URL=/media/`; `static()` serves media without auth when `DEBUG` (default `True`, `settings.py:17`). Logs, order attachments and e-mailed reports are therefore downloadable by URL (the console even links `/media/attachmentExtract/<file>` directly, `tplConsole.html:132`). In production it depends on the web server config. Rewrite: keep logs/attachments outside the public media root and serve uploads through an authorised view.

### 0.4 Time handling
* `TIME_ZONE='UTC'`, `USE_TZ=True` (`settings.py:129,133`) but code uses naive `datetime.datetime.now()` everywhere → naive datetimes saved into aware fields (RuntimeWarning, stored as UTC). The planning "working hours 8–17" logic runs in server-local time. APScheduler `BackgroundScheduler()` uses the **host local timezone**, so "06:00" depends on the host. [DECIDE] use Europe/Rome explicitly.

### 0.5 Models touched (summary; details in models chapter)
`Ordine`, `Linea`, `Anagrafica`, `Configuration`, `Note` (via `Ordine.note` M2M), `Stored_Item`, `Tracciabilita`, `WorkOrderTraccia`, `ControlloQlta`, `Test`, `Check`, `Reclami`, `DisplayTest`, `FileOrdini`, `Errori`, `ProcessedEmail`, `SentEmail`; from `Plan`: `Location` (fields `area`, `path→locPath.code`, `site`), `Coding` (`internalCode` unique, `rawMaterialCode`), `Asset` (`code`), `Annunci`, `Resource`.

Field quirks relevant here:
* `Tracciabilita.data_inserimento` is `auto_now=True` → it is "last modified", rewritten on every save.
* `Reclami.data` is `auto_now=True` → user-entered complaint date is **always overwritten** with now (see §4.3). [BUG]
* `Reclami.file` has no `blank=True` but the view allows no file.
* `Linea.prezzo_ea` is a `CharField(10)` with comma decimal; all computations parse it.
* `WorkOrderTraccia.traccia` FK with `DO_NOTHING` (migration 0001 says CASCADE).
* `DisplayTest.prodotto` choices only `RAFFAELLO28` (label "Raffaello2.8"), `esito` choices `POS/NEG`; choices are not validated by the view.

---

## 1. Production planning (`Prod/pianificazione.py`)

### 1.1 `GET /PRODUZIONE/vwPianificazione` → `vwPianificazione` (name `Pianificazione`)
* Auth: group `Geko` (`:21-22`).
* Params: none. Method: any (only GET meaningful).
* Template: `Pianificazione/tplPianificazione.html`.

#### Algorithm (`:36-351`; identical logic duplicated in `_build_pianificazione_data` `:440-623` – **[DECIDE] single implementation**)
1. `oggi = date.today()`; `inizio_settimana_1 = Monday of current week`.
2. Build `NUM_SETTIMANE_REPORT = 4` weeks (`:16`): for i=0..3: `inizio = monday + i weeks`, `fine = inizio + 6 days` (Sunday). Label `"Sett. {i+1} ({dd/mm} - {dd/mm})"`.
3. Areas = all `Location` ordered by `area`.
4. Soglie: every `Configuration` with `chiave` starting `SOGLIA_`; key suffix = area name; value parsed float (comma→dot), invalid → 0. Per area: `soglia = soglie.get(area.area.upper().replace(' ','_'), 0)`.
5. `fResa = float(PREZZO_RESA_ORARIA)` (comma→dot); missing/invalid → 1.
6. Remaining working hours of current week (`ore_rimanenti_settimana_corrente`), fixed calendar Mon–Fri 08:00–17:00 = 9 h/day, 45 h/week:
   * Sat/Sun → 0.
   * Today: before 08:00 → 9; between 08:00 and 17:00 → `17 - (hour + minute/60)`; after 17:00 → 0.
   * Plus `(4 - weekday) * 9` for the remaining days.
   * No holidays, no per-area calendars, server-local clock.
7. `articolo_location_map`: for each `Anagrafica` row (first occurrence per `articolo` wins, iteration order undefined) → `location_id`, `location_area`. Articles without Anagrafica, or with `location = NULL` (location_id None → never matches an area) are **silently dropped**.
8. `ordine_key_stato_map`: `(rif_ordine, serie, appendice, anno) → stato` over **all** Ordine rows (full table scan into memory; duplicates: last wins).
9. **Arretrati (backlog)**: `Linea` with `data_scadenza ∈ [monday − 8 weeks, monday)` (note: code uses **8 weeks**, docstring `:32` and template text say 4 → [BUG] doc mismatch; XLSX legend says 8), excluding `annullata=True`, `duplicata=True`, `emessa_bolla=True`, `appendice='0'`.
   For each line: skip if order stato ∈ `{CON, ANN, LAV, SPE}` (lines whose Ordine doesn't exist are **included**); skip if no article location; `tempo = qta * float(prezzo_ea) / fResa` (parse error → skip). Accumulate per `location_id`: `tempo` sum, set of order keys `"rif|serie|app|anno"`.
10. **Future weeks**: same filters for `data_scadenza ∈ [monday, sunday of week 4]`; each line put in the week whose range contains `data_scadenza`. Accumulate per (location, week): tempo + set of order keys.
    Note: week 1 also contains lines due Monday..yesterday (already late) – they are counted in week 1, not in arretrato.
11. Per area (only areas with `totale > 0 or arretrato > 0` are shown):
    * `arretrato = round(sum,2)`, `num_ordini_arretrati = |distinct orders|`.
    * Per week i: `tempo = round(sum,2)`, `num_ordini`, capacity `soglia_corrente = soglia × ore_rimanenti/45` for week 1, `soglia` for weeks 2..4. `percentuale = tempo / soglia_corrente × 100`. Status: `> 100` → `danger`, `> 80` → `warning`, else `success`; capacity 0 → `secondary`, pct 0.
    * `totale = Σ week tempo`.
    * Total capacity `soglia_totale = soglia × ore_rimanenti/45 + soglia × 3` (0 if soglia ≤ 0).
    * Overall status on `(totale + arretrato) / soglia_totale × 100` with same thresholds → `danger/Critico`, `warning/Attenzione`, `success/OK`, else `secondary/N/A`.
    * `area_data['soglia']` is **replaced** by `soglia_totale` (rounded) – the page column "Ore Residue" shows this.
12. `can_manage_soglie = isGeko(user)` – always true because the view already requires Geko.
13. Suggested e-mails: parse `EMAIL_REPORT_PRODUZIONE_TO` / `_CC` (split on `,`/`;`), rendered `;`-joined in the modal.

DB access: read-only. Perf: loads all Ordine and Anagrafica into memory; `area_articoli_map` (`:106-108`) runs **one query per area and is never used** [DEAD].

#### UI (`tplPianificazione.html`)
* Header text says **"Prossime 3 Settimane"**, "Totale (3 sett.)", "+ 2 settimane intere", arretrato "ultime 4 settimane", and "Settimane future: non annullate né duplicate" – all inconsistent with code (4 weeks, 8-week backlog, also excludes `emessa_bolla` and appendice 0). [BUG] text drift.
* Table columns: Area | Arretrato (badge warning with tooltip "N ordini arretrati") | one column per week (badge coloured by status, tooltip "N ordini | P% soglia (S h)") | Totale | Ore Residue (total capacity or N/D) | Stato badge with %.
* Buttons: **Esporta XLSX** (link to `ExportPianificazioneXLSX`), **Invia Report via Email** (modal), **Gestisci Soglie** (modal).
* Modal "Gestione Soglie": one row per area (all Locations), number input `min=0 step=0.5`, "Salva" → `fetch POST AggiornaSoglia` with `X-CSRFToken`; on success shows alert and **reloads the page after 1 s**. [KEEP]
* Modal "Invia Report": fields `to` (prefilled with configured TO, editable, required), CC read-only (display only, not posted), `subject` (default "Report Pianificazione Produzione"), `message` (default "In allegato il report di pianificazione produzione."). JS expects JSON `{status:'ok'}`.
* Bootstrap tooltips initialised on load. Error texts inserted via `innerHTML` (server error message unescaped → minor XSS vector).

### 1.2 `POST /PRODUZIONE/vwAggiornaSoglia` → `vwAggiornaSoglia` (name `AggiornaSoglia`)
* Auth: Geko. Method: POST only (else 405 JSON).
* Params: `area_id` (Location PK), `soglia` (float).
* Steps: missing params → 400; `Location.get(id)` (404 JSON if missing); key `SOGLIA_{AREA.upper().replace(' ','_')}`; `float(soglia)` (ValueError → 400); store `str(float).replace('.',',')` (e.g. `"45,0"`) via `update_or_create(chiave=…)`.
* Response `{success, message, area, soglia}`; any other exception → 500 with `str(e)` leaked.
* Validation gaps: negative values, `nan`, `inf` accepted server-side (`float('nan')` parses) [BUG]; key may exceed 50 chars for long area names; areas whose normalised names collide share one threshold; `update_or_create` will raise `MultipleObjectsReturned` if duplicate keys exist (no unique constraint on `chiave`).
* No audit of who changed the threshold.

### 1.3 `GET /PRODUZIONE/exportPianificazioneXLSX` → `export_pianificazione_xlsx` (name `ExportPianificazioneXLSX`)
* Auth: Geko. Returns `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `Content-Disposition: attachment; filename="pianificazione_YYYYMMDD.xlsx"`.
* Data: `_build_pianificazione_data()` (same numbers as page, without per-week status) → `_build_pianificazione_xlsx_bytes()` (openpyxl).

#### Export-only aggregation (`:716-781`)
Areas whose `area.strip().upper()` ∈ {`AREA CATENE`, `AREA CAVI FLESSIBILI`, `AREA ELETTRONICA`, `AREA JUNIOR`, `AREA KIT COMMESSE`, `AREA QUADRI`} are merged into one row labelled **"Area Quadrisitca"** (sic – probable typo of "Quadristica"; [DECIDE] keep label or fix). Merging sums arretrato, n° ordini arretrati (note: distinct-order counts are summed, so an order spanning two merged areas is double counted), totale, total capacity, and per week tempo/num_ordini/capacity; per-week % recomputed = tempo/capacity×100. Row order = first appearance in Location order.

#### Sheet 1 "Pianificazione" layout
* Row 1: merged A1:last – "PIANIFICAZIONE PRODUZIONE" (white bold 16 on `#0A5796`), height 32.
* Row 2: "Generato il dd/mm/yyyy | Periodo: dd/mm/yyyy → dd/mm/yyyy" (on `#BDD7EE`).
* Row 3: legend "VERDE = OK (≤ 80% capacità) GIALLO = Attenzione (80–100%) ROSSO = Critico (> 100%) | Arretrato = ore scadute non ancora evase (ultime 8 settimane)".
* Row 4: A4:C4 blank; per week a 4-column merged group with the week label; last 3 columns merged "Riepilogo Totale (4 settimane)".
* Row 5 headers (each with an Excel cell comment explaining the formula, author "METODO", 220×90):
  1. `Area di Produzione`
  2. `Arretrato (ore)` (header fill `#D4A017`)
  3. `N° Ord. Arretrati` (header fill `#D4A017`)
  4. For each of 4 weeks: `Ore Pianificate (h)`, `N° Ordini`, `Capacità Disp. (h)`, `Saturaz. (%)`
  5. `Totale 4 Sett. (h)`, `Capacità Tot. (h)`, `Stato Complessivo`
  → 22 columns (A..V).
* Rows 6+: one row per (aggregated) area. Arretrato cells amber `#FFF8E1` + bold dark-yellow font when > 0. Week cells coloured light green/yellow/red (`#D1E7DD/#FFF3CD/#F8D7DA`, fonts `#0F5132/#664D03/#842029`) by % (>100 red, >80 yellow), grey `#E9ECEF` when no capacity; capacity cells italic grey; % format `0.0"%"`; hours `0.00`.
  Totale coloured on `(totale+arretrato)/capTot`. Stato: `CRITICO` (red fill white font) / `ATTENZIONE` (yellow) / `OK` (green) / `N/D`.
* Column widths: 22,12,10, then per week 13,9,12,10, then 13,13,12. Freeze panes at `B6`.

#### Sheet 2 "Commesse Bloccate" (`_get_commesse_bloccate_for_export`, `:626-696`)
* Selection: `Ordine.stato ∈ {INS, LAV, SPP}` AND at least one related `Note.testo` icontains `manca` / `mancante` / `bloccato` / `bloccata` (note "manca" already matches "mancante"). Ordered by `data_scadenza, anno, serie, rif_ordine, appendice`.
* Columns (row 4 header): `Commessa` = `"{serie}.{rif}.{appendice}-{anno}"` or `"{serie}.{rif}-{anno}"` when appendice empty/`'0'`; `Stato`; `Data Scadenza` (DD/MM/YYYY); `N° Oggetti` = Σ`qta` of the order's Linea not annullata/duplicata/emessa_bolla (one aggregate query per order – N+1); `Note Bloccanti` = matching notes joined by `" | "` (case-insensitive match in Python).
* Row 1 title "COMMESSE BLOCCATE", row 2 "Generato il … | Criteri: Stato in INS/LAV/SPP e note contenenti Manca, Mancante, Bloccato, Bloccata". Empty → merged A5:E5 "Nessuna commessa bloccata trovata con i criteri selezionati.". Widths 24/12/16/12/90, freeze `A5`.

### 1.4 `POST /PRODUZIONE/sendPianificazioneReportEmail` → `send_pianificazione_report_email` (name `SendPianificazioneReportEmail`)
* Auth: Geko. POST only (405 JSON otherwise).
* Params: `to` (required, `,`/`;` separated), `subject` (default "Report Pianificazione Produzione"), `message` (optional).
* Calls `_send_pianificazione_report_email(to_override=to, subject, user_message)`; `ValueError` → 400 JSON; other → 500 JSON `"Errore invio email: …"`.
* **[BUG – critical] the view has no `return` on success** (`:1265-1274` ends after the `try/except`) → Django raises "view didn't return an HttpResponse" → HTTP 500 **after the e-mail has already been sent**. The UI shows an error and users retry → duplicate e-mails. Rewrite must return `{"status":"ok", "to":[…], "cc":[…]}`.
* [SEC] Any Geko user can send the company planning + blocked-orders workbook to **any** external address (no domain allow-list); `subject` unvalidated (header-injection risk with compat32 email policy); `message` inserted raw into HTML body (`:1190`) → HTML injection in outgoing mail.

#### `_send_pianificazione_report_email` (`:1173-1232`) – shared by manual + scheduled send
1. Recipients (`_get_pianificazione_report_recipients`, `:1152-1170`): TO = parsed override; if empty → `EMAIL_REPORT_PRODUZIONE_TO`. CC = `EMAIL_REPORT_PRODUZIONE_CC` (always added, even with override). Dedup preserving order; CC entries also in TO are removed. No e-mail syntax validation.
2. No TO → `ValueError('Destinatari principali non configurati')`. `EMAIL_METODO_USER` missing → `ValueError('Configurazione email mittente mancante')`.
3. Build data + XLSX (same as export).
4. HTML body: optional `<p>{user_message}</p>`; "In allegato il report di pianificazione produzione per il periodo **dd/mm/yyyy – dd/mm/yyyy**, generato il **dd/mm/yyyy**."; link to `PIANIFICAZIONE_URL` (hard-coded prod URL); footer "-- METODO | Gekotecnologie --".
5. Writes XLSX to `Metodo/media/mailAttachments/pianificazione_reports/pianificazione_YYYYMMDD_HHMMSS.xlsx` (dir created), then `sendEmail(subject, body, from=EMAIL_METODO_USER, to=', '.join(TO), replyTo=PIANIFICAZIONE_REPLY_TO (hard-coded gmail address, `:1143`), attachment, cc)`.
6. On exception the file is deleted and re-raised; on success the file is **kept forever** (and referenced from `SentEmail.attachment_path`) – unbounded disk growth, stored under public media [SEC].
7. `sendEmail` (`Prod/mailing.py:22-…`): SMTP `EMAIL_SERVER:587` STARTTLS login with `EMAIL_METODO_USER`/`EMAIL_METODO_PASSWORD` (DB clear text), MIME HTML, attachment as `application/octet-stream`, envelope = TO+CC, then persists a `SentEmail` record (subject, body, from, to, cc, reply, attachment path/name).

### 1.5 Scheduled job `scSendPianificazioneReportEmail` (`:1235-1245`)
* Registered in `Prod/updater.py:19`: `cron hour=6 minute=0`, every day **including weekends/holidays**, host-local timezone.
* Calls `_send_pianificazione_report_email()` with configured recipients and default subject, no user message. Logs success to logger `schedule` (`To | Cc | File`) or error. No retry.
* [RACE] see §9: the scheduler starts in every process → one e-mail per worker process.

---

## 2. Traceability – common model of a "traccia"

Concept (as implemented):
* An **item** is a `Plan.Coding` (unique `internalCode`, optional `rawMaterialCode` = supplier lot) wrapped by a `Stored_Item` (`codice` FK, optional `linea` FK used as "item type", `data_in`).
* A **traccia** (`Tracciabilita`) is an event: optional `stored_item`, optional `rif_ordine` (FK Ordine), `data` (event datetime), `chiave` (event type / product), `valore` (value), `location` (department), optional `asset` (machine), `data_inserimento` (auto_now).
* "Item type" is expressed by linking `Stored_Item.linea` to a **pseudo-`Linea`** whose `articolo` equals `ARTICOLO_TRACCIA_BATTERIE` or `ARTICOLO_TRACCIA_CAVI` (`qta` 0/1, `prezzo_ea` 0, no order). These fake lines pollute the `Linea` table. [DECIDE] DuMetodo should model item type explicitly (enum/table) and never create order lines.
* Barcodes: Code128 via JsBarcode (no QR code exists anywhere in this area). Payload is plain CSV text: batteries `"{item},{chiave},{data}"`, generic `"{item},{chiave},{valore},{data}"`.
* Link to orders: in the order modal (`Prod/orders.py:1059`, other analyst) traces are shown with `rif_ordine__rif_ordine__iregex=r'[^1-9]{rif-without-leading-zeros}$'` + anno + serie; ControlloQlta with `ordine__iregex=r'{serie}.{rif}$'` (user input interpolated into regex unescaped); FileOrdini with `contains`. Lot/busta capture is in the order modal (§3.6).

---

## 3. Traceability views (`Prod/traccia.py`)

### 3.1 Batterie – create: `GET|POST /PRODUZIONE/TracciaBatteria` → `vwTracciaBatteria` (name `TracciaBatteria`)
* Auth: Geko. Template `Tracciabilita/Batterie/tplCrea.html`.
* GET params: `codice` (optional; prefilled + readonly, used by list "Crea" button for a recharge). Context: `locations` (all), `now`.
* POST fields: `codice` (required), `ordine` (optional), `data` (date, required), `location` (Location `path.code`, via datalist, required), `radioChiave` = `IN` ("Primo Ingresso") or `RC` ("Ricarica") (required).
* Steps (`:239-298`):
  1. `ordine = POST['ordine'].zfill(7)`; `rOrdine = Ordine.get(rif_ordine=ordine)` – any exception (not found **or MultipleObjectsReturned**, which is common since rif repeats across serie/anno) → no order link (silent).
  2. Pseudo line: `Linea.get(articolo=ARTICOLO_BATTERIE)`; on **any** exception (incl. MultipleObjectsReturned) create a new pseudo Linea (qta 0, prezzo 0) → duplicates accumulate. [BUG]
  3. `Location.get(path__code=location)` → unhandled DoesNotExist → 500.
  4. `Coding.get(internalCode=codice)`: exists and chiave `IN` → error "Codice esistente, ingresso non possibile."; not exists → create Coding (also for `RC`, i.e. recharge of an unknown battery silently creates it).
  5. If OK: create **new** `Stored_Item(codice, linea=pseudo, data_in=now)` – one Stored_Item per event, not per physical item.
  6. Create `Tracciabilita(stored_item, rif_ordine?, data=POST date (midnight naive), chiave=IN|RC, valore="", location)`.
  [TX] Coding/Stored_Item/Tracciabilita created without a transaction; [RACE] Coding uniqueness only enforced by DB unique (IntegrityError → 500 on double submit).
* Response re-renders form with result panel: "Success|Error {error}", a one-row table (Item, Ordine, Inserimento, Data, Chiave, Valore, Reparto, Asset), buttons **Genera Barcode** (`JsBarcode(".barcode").init()` on an inline SVG Code128 with `{item},{chiave},{data}`) and **Print** (opens `print?barcode=…` in new tab). [KEEP]
* Form has a **Genera Codice** button: `hx-get="/PRODUZIONE/genCode?radix=DMGBT"` swapping the `#codice` input (outerHTML). [KEEP]

### 3.2 `GET /PRODUZIONE/genCode` → `genCodiceBatteria` (name `GeneraCodice`)
* **No auth** [SEC low].
* Param `radix` (required; missing → `None + str` TypeError 500 [BUG]).
* Code format: `radix + uuid4().hex[:6].upper()` → e.g. `DMGBT` + 6 uppercase hex chars (`DMGBT3F9A1C`). Uniqueness **not checked** against `Coding` (collision probability small but non-zero; then IN fails with "Codice esistente").
* Renders `Batterie/uxReply.html`: `<input id="codice" name="codice" value="…" required>` (editable, not readonly).

### 3.3 Batterie – list/export: `GET|POST /PRODUZIONE/ListaBatterie` → `vwListBatterie` (name `ListaBatterie`)
* Auth: Geko **or Customer** (customers see all battery data, unscoped).
* Base: `Tracciabilita` where `stored_item.linea.articolo == ARTICOLO_BATTERIE` (import-time value).
* POST filters: `item` (internalCode contains), `ordine` (rif contains), `dataFrom`+`dataTo` (both required → `data__range`), `dataInserimentoFrom`+`To` → **filters `data` instead of `data_inserimento`** (`:422`) [BUG].
* Date ranges use date strings against DateTimeFields → upper bound is midnight, the "to" day is excluded [BUG].
* `GET ?export=1` → XLSX (xlsxwriter) `ListaBatterie.xlsx`: columns `N, Codice, Ordine, Data Inserimento, Data Ricarica, Azione, Area` (dates dd/mm/yyyy). Filters are read from POST only, so the export link (which passes them as GET) **exports all battery traces** [BUG]. `instance.stored_item.codice` None → crash.
* Template: collapsible filter form; bootstrap-table with search/sort; per-row **Crea** → `/PRODUZIONE/TracciaBatteria?codice=<code>` (to register a recharge). Template refers to `dataForm`/`dataInserimentoFrom` but context provides `dataFrom`/`dataIFrom` → filters not re-populated [BUG]. The `<script>` after `{% endblock %}` is never rendered [DEAD].

### 3.4 Generale – create: `GET|POST /PRODUZIONE/creaTraccia` → `vwCreaTraccia` (name `CreaTraccia`)
* Auth: Geko. Template `Generale/tplCrea.html`.
* GET prefill params: `codice` (readonly if present), `ordine`, `chiave`, `valore`.
* POST fields: `codice` (required, free text – **no generator**), `ordine`, `data` (date, required), `chiave`, `valore`, `location` (path.code, required).
* Steps (`:159-212`): `ordine.zfill(7)`; `rOrdine = Ordine.filter(rif_ordine__contains=ordine).first()` (arbitrary match across serie/anno – [BUG] wrong-order linking); `Location.get` (500 if missing); if `Coding` with that code exists → error "Codice esistente." (so the list's **Crea** button, which passes an existing code, **always fails** – adding a second event to an existing generic item is impossible [BUG]); else create Coding, Stored_Item (no linea), Tracciabilita with **`valore=""` – the user-entered `valore` is discarded** (`:198,200`) [BUG].
* Result panel + barcode/print as batteries (payload `{item},{chiave},{valore},{data}`).

### 3.5 Generale – list/export: `GET|POST /PRODUZIONE/listaTracce` → `vwListaTracce` (name `ListaTracce`)
* Auth: Geko.
* All `Tracciabilita` (all types). POST filters `item`, `ordine` (contains). Date range on `data`: from POST (or GET when `export`) `dataFrom`/`dataTo`, default **today−2 days → today** (today excluded because of midnight bound) [BUG]. Empty strings submitted from the form → `ValidationError` 500 [BUG]. Optional `dataInserimentoFrom/To` on `data_inserimento`.
* `GET ?export=1` → `ListaTracce.xlsx`: `N, Codice ("None" if no item), Ordine, Data Inserimento, Data, Chiave, Valore, Area`. `instance.location` None → crash.
* Template rows have **Crea** → `/PRODUZIONE/creaTraccia?codice=…` (broken, see §3.4); export link passes `order=` (wrong name).

### 3.6 Lotto/Busta per commessa: `POST /PRODUZIONE/creaLottoCommessa` → `vwCreaLottoCommessa` (name `CreaLottoCommessa`)
* Auth: Geko. Called by HTMX from the order modal (`Ordini/mdlOrdine.html:163-176`, button `hx-post` inside the form, target `#LottoResults`).
* Fields: `ordine` (zfill 7), `serie`, `appendice`, `anno`, `lotto`, `busta`.
* Steps: `Ordine.get(rif_ordine__contains=ordine, serie, appendice, anno)` (exceptions swallowed → `tr=False`); location hard-coded `GK01-PLS` (500 if missing); creates **two** traces (no stored_item): `chiave="LOTTO", valore=lotto` and `chiave="BUSTA", valore=busta`, `data=now`. Empty lotto/busta are still saved. Repeated submits create duplicates. [TX]
* Response `Lotti/uxReply.html`: "Tracciatura creata." / "Tracciatura non creata, Controllare i dati.".
* GET → `context` undefined → `UnboundLocalError` 500 [BUG].

### 3.7 Cavi – machine/HTMX insert: `GET /PRODUZIONE/insertTracciaCavi` → `insertTracciaCavi` (name `InserisciCavo`)
* **No authentication, state-changing GET** (`:605`) [SEC]. Probably intended for a cable-cutting machine integration (asset/location params). [DECIDE] provide an authenticated API (token per asset) in DuMetodo.
* Params: `ordine`, `lottoCavo` (default `'None'`), `prodotto` (cable product code → stored as `chiave`, default `'---'`), `misura` (length → `valore`), `data` (`%Y/%m/%d,%H:%M:%S` or `%Y/%m/%d`; missing/invalid → unhandled exception 500), `asset` (Asset.code), `location` (Location.path.code).
* Steps:
  1. Parse date; `anno = str(data.year)`.
  2. `Asset.get(code)`, `Location.get(path__code)` → 500 if missing (Asset.code not unique → MultipleObjectsReturned possible).
  3. If `ordine` matches `^[0-9]{5}\.([0-9]{7})-[0-9]{4}` → take the 7-digit group (unanchored at end).
  4. `Ordine.get_or_create(rif_ordine=ordine.zfill(7), anno=anno)` – **creates a bare Ordine** (stato INS, no serie/appendice) when not found [BUG – pollutes orders, these then appear in planning-status lookups / order lists]; MultipleObjectsReturned when the rif exists in several serie → 500.
  5. Dedup: if a trace with same `(rif_ordine=o, data, chiave=prodotto, valore=misura)` exists → nothing.
  6. Else: code = `ITEM_CODE_CAVI + uuid4().hex[:6].upper()`; `Coding.get_or_create(internalCode=code)`, set `rawMaterialCode = lottoCavo`; pseudo `Linea.get_or_create(articolo=ARTICOLO_TRACCIA_CAVI, qta=1, prezzo_ea=0)`; `Stored_Item.get_or_create(codice, linea, data_in=data)`; `Tracciabilita(stored_item, rif_ordine=o, data, chiave=prodotto, valore=misura, location, asset)`.
  [TX] none; [RACE] dedup check-then-insert.
* Response `Cavi/uxReply.html` = `{{traccia}}` (→ `str(Tracciabilita)` = order rif or chiave; `[]` when duplicate).

### 3.8 Cavi – list/export: `GET|POST /PRODUZIONE/ListaCavi` → `vwListCavi` (name `ListaCavi`)
* Auth: Geko or Customer.
* Base: traces whose `stored_item.linea.articolo == ARTICOLO_CAVI`. Filters like §3.5 (default last 2 days, same midnight bug).
* Table columns: Item, Ordine, **Lotto** (`codice.rawMaterialCode`), Inserimento, Data, Prodotto (`chiave`), Misura (`valore`), Reparto, Asset, Comandi.
* Row button **Intestato** (HTMX `hx-get insertTracciaCavi?lottoCavo=<lotto>&ordine=<rif>&prodotto=<item code!>&misura=Intestato&asset=GKAST-107&location=GK01-CVI&data=<render-time now>`, swap outerHTML). Semantics: records a "cable terminated" event. Issues: passes the **item code** as `prodotto` (so `chiave` becomes the item code), generates a **new** item code instead of reusing the cable's item, and `data` is the page-render time, not click time (repeated clicks are deduplicated only by that timestamp). [BUG]/[DECIDE] model "Intestato" as a status event on the existing item.
* `GET ?export=1` → `ListaCavi.xlsx`: headers `N, Codice, Ordine, Data Inserimento, Data Lavorazione (dd/mm/yyyy hh:mm), Lunghezza, Area` – column 5 is written twice (`Azione` then `Lunghezza`; data `chiave` overwritten by `valore`), so the **product code is lost** in export [BUG]. Item/ordine filters ignored on export.
* Template `<table class="table"` missing `>` (malformed HTML) [BUG].

### 3.9 Print page: `GET /PRODUZIONE/print?barcode=…` → `vwPrint` (name `Print`)
* Auth: Geko. Renders `tplPrint.html` with `action='barcode'`: standalone page with a Code128 SVG of the `barcode` text, auto-initialised on `window.onload`; `metodo.css` also as print stylesheet. [KEEP] (label printing from the browser).
* Missing `barcode` → `context` undefined → 500 [BUG].

### 3.10 WorkOrderTraccia (battery maintenance work orders)
* `scCreateWorkOrders()` (`:573-585`) – **not scheduled** (commented out in `updater.py:14`) [DEAD]. Logic: for every battery trace `t` (iterates all traces, N+1), take the most recent trace of the same `internalCode`; if its `data.date() <= today − 115 days` and there is no `WorkOrderTraccia` with `stato="OPN"` for that code → create `WorkOrderTraccia(traccia=t)` (pointing to the iterated trace, not the latest). I.e. "battery not recharged in 115 days → open a recharge WO". `data` None → crash.
* `GET /PRODUZIONE/wotHandle?action=close&id=<pk>` → `wotHandle` (name `GestioneWOT`): **no auth**, GET state change; sets `stato="CLS"`; `get` 500 if missing; redirects to `Plan.views.index`.
* `Plan/views.py:24` passes open WOTs (`wot`) to the Plan home context but **no template uses it** → no UI [DEAD]. `risorsa` FK never set.
* [DECIDE] either implement properly (scheduled, deduplicated per item, UI with close action) or drop.

---

## 4. Quality, complaints, display tests, photos

### 4.1 Controllo qualità – create: `GET|POST /PRODUZIONE/tracciaQualita` → `vwControlloQlta` (name `TracciaQualita`)
* Auth: Geko. Template `Qualita/tplCrea.html`.
* Master data (`Test`, `Check`) maintained only via Django admin. `Test`: `nome`, `descrizione`, `tipo` (CMP Campione / 100 100% / MIS Misura), `misura_min/max`, `percentuale`, `checks` M2M Check, `checkDistinta`, `checkDisegno`. **`tipo`, `misura_*`, `percentuale` are never used** by the logic (no acceptance evaluation) [DEAD/DECIDE].
* GET (no `idTest`): form with `ordine` ("Ordine/Stock" free text, required), `codiceProdotto` (required), `qtaInOrdine` (required, text), `data_inserimento` (date, default today), select `idTest` with HTMX `hx-get="/PRODUZIONE/tracciaQualita"` target `#testValue` (on change sends `idTest`). [KEEP]
* GET `?idTest=<id>` → partial `Qualita/uxReply.html`: a table row per `Check` of the test with two required text inputs `qtaControllo_<checkId>` (placeholder "Campione") and `qtaKo_<checkId>` ("Ko"); plus checkbox `distinta` (checked by default) if `checkDistinta`, checkbox `disegno` if `checkDisegno`. `Test.get` 500 if missing.
* POST: `Test.get(int(idTest))` (500 if empty/invalid). For each check of the test: if both `qtaControllo_<id>` and `qtaKo_<id>` non-empty → `ControlloQlta(ordine=ordine.zfill(7), campione, ko, codiceProdotto, check_rif=check, data_inserimento=<user date>, qtaInOrdine)`. If `checkDisegno` → extra row with `Check id=9`, `campione='0'`, `ko='Presente'|'Assente'` (checkbox `on`); if `checkDistinta` → extra row with `Check id=8` likewise (these rows have no `qtaInOrdine`). Numbers are free text (no numeric validation, ko ≤ campione not checked). [TX] none.
* Response: re-renders the empty form (no success feedback shown; `now` not in POST context so date empty) [BUG UX]. Note `ordine.zfill(7)` is applied even to stock descriptions.
* Downstream: shown in order modal by regex `{serie}.{rif}$` → users are expected to type `SERIE.RIF` in `ordine` (e.g. `43.0001234`); zfill on that string is a no-op when ≥7 chars.

### 4.2 Controllo qualità – list: `GET|POST /PRODUZIONE/listaQualita` → `vwListaQlta` (name `ListaQualita`)
* Auth: Geko or Customer.
* GET → empty list (must filter first). POST filters `ordine` (contains), `codiceProdotto` (contains), `dataInserimentoFrom`+`To` (range). If POST with no filter, `controlli` is the bare **Manager**, not a queryset → template iteration TypeError 500 (likely) [BUG].
* Columns: ordine, codiceProdotto, qtaInOrdine, check_rif, data_inserimento (d-m-Y), campione, ko. No export.

### 4.3 Reclami – create: `GET|POST /PRODUZIONE/creaReclamo` → `vwCreaReclamo` (name `CreaReclamo`)
* Auth: Geko. Multipart form: `rif` (Identificativo, required), `ordine` (required), `codiceProdotto` (required), `location` (path.code datalist, required), `data` (date, default today), `note`, `file` (optional).
* POST: `Location.get` (500 if invalid); `Reclami(codice=rif, articolo, ordine, data, reparto, note, file)` saved; **`data` is ignored because the model field is `auto_now=True`** [BUG]. No file type/size validation; stored in `MEDIA_ROOT/Reclami/` and linked publicly [SEC]. No success message; form re-rendered.

### 4.4 Reclami – list: `GET /PRODUZIONE/listaReclamo` → `vwListaReclamo` (name `ListaReclamo`)
* Auth: Geko or Customer.
* GET filters: `codiceProdotto` (articolo contains), `ordine` (contains), `rif` (codice contains), `dataFrom` default today−30d, `dataTo` default now. Submitting the form with blank date inputs sends `''` → `ValidationError` 500 [BUG]; date-string `dataTo` excludes that day.
* Columns: ordine, articolo, reparto, codice, data, note, file (download link). No export.

### 4.5 Display test – create: `GET|POST /PRODUZIONE/creaDisplay` → `vwCreaDisplay` (name `CreaDisplay`)
* Auth: Geko. Template `Display/tplCrea.html`.
* Fields: `prodotto` (free text input – model expects choice `RAFFAELLO28`; not validated), `sito` (required, autofocus), `articolo` (required), `esito` (select POS/NEG, required), `fw`, `lotto`, `data` (date, default today), `note`.
* POST: creates `DisplayTest(..., utente=request.user)`; no validation (invalid date → 500). Re-renders form with success alert "Test salvato: {sito} - {articolo} - Esito: {label}" and **sticky** `prodotto`, `fw`, `lotto`, `data` (sito/articolo/esito/note cleared). [KEEP]
* **Barcode-scanner workflow** [KEEP]: focus starts in `sito`; when the `sito` field receives a value containing `$$` (keyboard-wedge scan of `"<sito>$$<articolo>"`), JS splits it, fills `sito` and `articolo`, sets `esito=POS`, focuses `fw`, and after 100 ms **auto-submits** if `prodotto`, `fw` and `lotto` are all non-empty. Fields select their content on focus. A separate sidebar form's "Salva Test" submits the main form.
* `Display/uxReply.html` exists but is unused [DEAD].

### 4.6 Display test – list/export: `GET|POST /PRODUZIONE/listaDisplay` → `vwListaDisplay` (name `ListaDisplay`)
* Auth: Geko or Customer.
* All DisplayTest (ordering `-data, -data_inserimento`), unpaginated. POST filters: `prodotto` (exact), `sito` (icontains), `articolo` (icontains), `esito` (exact), `dataFrom`+`dataTo` (range on `data`).
* `GET ?export=1` → `TestDisplay.xlsx` columns: `N, Prodotto (label), Sito, Articolo, Esito (label), FW, Lotto, Data (dd/mm/yyyy), Note, Data Inserimento (dd/mm/yyyy hh:mm), Utente (username)`. Export ignores filters (they're POST) [BUG].
* Table: Prodotto, Sito, Articolo, Esito (badge green/red), FW, Lotto, Data, Note, Utente; bootstrap-table search/sort.

### 4.7 Foto/file per ordine – upload: `GET|POST /PRODUZIONE/vwCreaFoto/` → `files.vwCreaPhoto` (name `CreaFoto`)
* Auth: Geko (only `user_passes_test`, no `login_required`; equivalent effect).
* POST: `file` (multipart), `rif_ordine` (required), `anno`, `serie`, `appendice` → `FileOrdini.create(...)` (upload to `MEDIA_ROOT/file/`), redirect `ListaFoto`. Missing file or rif → silently re-renders form. No content-type/size validation [SEC]; link to order is by strings only (no FK).
* Client behaviour [KEEP]: two file inputs `accept="image/*" capture="camera"` (mobile camera), the first required; on submit JS resizes each image to max 1024 px on the long side, re-encodes JPEG q=0.8, posts each as a separate `fetch` POST (same form fields, `file` replaced by blob); redirects to the list after the (last) successful upload. [RACE] both images share one canvas and uploads run in parallel; with 2 files the redirect after upload 2 can abort upload 1 in flight; non-image files will fail to load.
* CSRF: the FormData includes the `csrfmiddlewaretoken` from the form, so it works.

### 4.8 Foto – list: `GET|POST /PRODUZIONE/vwListaFoto/` → `files.vwListaPhoto` (name `ListaFoto`)
* Auth: Geko.
* GET: files with `data_inserimento ∈ [now−2d, now+1d]`. POST filters (`contains`): `order`, `serie`, `anno`, `appendice`, `dataInserimentoFrom`+`To`. POST with no filter → bare Manager → template error (likely) [BUG].
* Columns: Ordine, Inserimento, Serie, Anno, Appendice, File (download link).
* Also shown in the order modal (`orders.py:1088`).

---

## 5. Console (`Prod/console.py`) – `GET|POST /PRODUZIONE/vwConsole/` → `vwConsole` (name `Console`)

* Auth: **`Geko Admin`** group. Template `Console/tplConsole.html`; command results are returned as the partial `Console/uxReply.html` (`<h6>{{console}}</h6>`) swapped into `#console` by HTMX (indicator `#indicator` "Loading...").
* Commands are selected by query/body parameters and executed **in sequence within the same request** (several can fire at once). Almost all are **state-changing GETs** without CSRF protection [SEC] – e.g. an `<img src=".../vwConsole?AckAllErrors=1">` visited by an admin triggers them. Commands run synchronously in the web request (long IMAP reads → request timeouts) and concurrently with the scheduler jobs [RACE].

| # | Trigger | UI | What it does | Output | Issues |
|---|---|---|---|---|---|
| 1 | `GET ?maintenance=1` | none (hidden) | Runs `scMaintenance()` (§7.1). | Falls through to full page (no uxReply). | Hidden command. |
| 2 | `GET ?readMail=1` | "Leggi Mail" | Counts `ProcessedEmail` before/after `scFetchOrderByMail()` (IMAP fetch of order e-mails, extract attachments – see orders chapter). | "Lettura Mail Ordini effettuata. Ci sono N nuovi ordini." | Count = processed e-mails, not orders. |
| 3 | `GET ?loadOrders=1` | "Crea Ordini" | Counts `.txt` in `attachmentExtract/` before/after `scImporthOrderByMail()` (parses `@`-separated TXT files, `insertLinea` per row, deletes processed files). | "Ordini Caricati: {after − before}" | [BUG] successfully imported files are deleted, so the result is **negative/zero** (should be before − after). |
| 4 | `POST readMailDateRange=1, data_da, data_a` (YYYY-MM-DD) | "Lettura Mail per Range di Date" form | `scFetchOrderByMailDateRange(da, a)` → `(mail_processate, ordini_importati, errori)`. | "Completato! Mail processate: X, Ordini importati: Y" or with "Errori: n - first 3". Date parse error → message. | No check `da ≤ a`. |
| 5 | `GET ?updateAnagrafica=1` | "Aggiorna Anagrafica" (superuser-only in UI) | Same as anagrafica part of `scMaintenance`: for each distinct `(articolo, prezzo_ea)` of `Linea` whose articolo is not in Anagrafica → create `Anagrafica(articolo, prezzo=float(prezzo_ea))`, set `location=GK01-UDEF`; then de-duplicate Anagrafica by articolo (§7.1). | "Anagrafica aggioranata con N nuovi oggetti." (N = distinct (art,prezzo) pairs) | [BUG] `Location.get` raises if GK01-UDEF missing (the `if uloc` is useless); `float()` on bad price aborts mid-way; [TX] none; dedupe may delete the curated row (see §7.1). |
| 6 | `GET ?distinctTraccia=1` | "Controlla Tracciature" (superuser-only in UI) | For **every** Tracciabilita t: if there is more than one trace with same `rif_ordine, chiave, valore` and `data ∈ [t.data, t.data+180 s]` → delete t (comment says 5 minutes). Keeps the last of each burst. | "Tracciature Manutenuti" | O(n²) queries over whole table; `t.data` None → TypeError 500; deletes ignore `stored_item` (two different items with same chiave/valore/order within 3 min are merged!) [BUG]; `WorkOrderTraccia` FK DO_NOTHING → possible FK violation. |
| 7 | `GET ?deleteAttachment=<filename>` | "Rimuovi File" per stuck file | `os.remove('Metodo/media/attachmentExtract/' + filename)`. | "Allegato X eliminato." | **[SEC – critical] path traversal**: `filename` is unvalidated (`console.py:136-141, 263-267`) – `?deleteAttachment=../../settings.py` (or any `../` path) deletes arbitrary files writable by the app user; via GET, i.e. also CSRF-able. Missing file → 500. Rewrite: accept only names from the listed directory (basename + existence in listing). |
| 8 | `GET ?AckError=<id>` | "Accetta" per error row (normal link, full page) | `Errori.get(id).accettato=True` (exceptions swallowed). | Full page re-render. | Bare `except`. |
| 9 | `GET ?AckAllErrors=1` | "Accetta Tutti" (hx-confirm) | `Errori` with `data_errore ≥ now−30d` and not accepted → `accettato=True` (bulk update). | "Accettati N errori." | |
| 10 | `POST update_prices=1, articolo, nuovo_prezzo, data_da, data_a` | "Aggiornamento Prezzi Massivo" (superuser-only in UI, JS confirm) | `Linea` with `articolo` and `data_scadenza ∈ [da,a]`, excluding annullata/duplicata → `update(prezzo_ea = str(nuovo_prezzo).replace('.',','))`. Logged to `metodo` logger. | "Aggiornate N linee per l'articolo X al prezzo P€" | No numeric validation (any string ≤10 chars, longer → DB error); includes lines already invoiced (`emessa_bolla`) – affects invoicing reports; no audit trail beyond log; server-side only Geko Admin (UI says superuser). |

Page data (always computed, also for command requests – wasteful):
* `ordersStuck` = every file name in `attachmentExtract/` ("Ordini in errore di importazione"), each a download link `/media/attachmentExtract/<name>` and a remove button. Missing dir → 500.
* `errori` = `Errori` of last 30 days not accepted (columns name, date `Y-m-d, h:m:s` – note `m` is month, bug in format, text, `serie.rif.appendice-anno`, Accetta).
* `ordinidoppi` (`ordiniConsegnatiDoppi`, `:269-293`): orders of the current year whose key `(anno, serie, appendice, rif_ordine)` exists both with `stato=CON` and with a stato not in {CON, ANN} → "Commesse Doppie - Da controllare" table with "Apri Ordini" (POST to `/PRODUZIONE/vwListaOrdini` with `order=rif`, only for Geko Admin via `has_group` filter).
* `articoli_list` (all distinct Anagrafica.articolo → datalist), `today`.
* Embedded log panel: `hx-get .../vwLog?log=ordini` on load.
* Template issues: hard-coded absolute prod URLs in `hx-get` (console unusable on any other host) [BUG]; duplicate `id="console"`; stray `</body></html>` inside the block; unclosed table/row.

### 5.1 Log viewer: `GET /PRODUZIONE/vwLog/?log=ordini|scheduler|metodo` → `vwLog` (name `Log`)
* Auth: **Geko** (weaker than the console itself – any Geko user can read all logs, which contain e-mail subjects, file paths, errors) [SEC].
* Maps `ordini` → `Metodo/media/logs/ordini.log`, `scheduler` → `schedulazioni.log`, anything else → `metodo.log` (whitelist – no traversal). Reads the **whole file** into memory (no tail/rotation awareness) [perf]; missing file → 500; file handle not closed on exception.
* Renders `Console/uxLog.html`: each line followed by `<br>`, wrapped in a green span if it contains `INFO`, red if `ERROR` (unbalanced `<span>` tags when neither). Django auto-escapes line content.
* [DECIDE] DuMetodo: tail N lines, newest first, level filter, admin-only.

### 5.2 Helpers in `console.py`
* `checkOrderStucks()` – list dir (see above).
* `deleteAttachment(filename)` – see command 7.
* `ordiniConsegnatiDoppi()` – see above.
* `scAddDescriptionFromOrders()` (`:295-325`): for every `.txt` in `attachmentExtract/`, parse header line (utf-8, `@` separated) and rows (latin-1); for each row with `articolo` set `Anagrafica.description = descrizione`. Not scheduled (commented in `updater.py:17`, and that line reuses the job name `ImporthOrderByMail`) [DEAD]. Note the header names it expects (`articolo`, `descrizione`) differ from those used by the importer (`Cod. Articolo`), so it is likely non-functional.

---

## 6. Annunci (announcements)

Model `Plan.Annunci`: `date` (DateField), `description` (Text, HTML), `active` (bool), `activatedAt` (DateTime, non-editable), `permanente` (bool, default False). `save()`: if active and no `activatedAt` → set now; if not active → `activatedAt=None`. Property `isExpired`: active, not permanente, and now ≥ activatedAt + 1 day.

| URL | View | Auth | Behaviour |
|---|---|---|---|
| `GET /PRODUZIONE/annunci` (`ListaAnnunci`) | `vwListaAnnunci` | Geko | All announcements ordered `-date, -id`; table Data (d/m/Y), Descrizione (`truncatewords_html:25`), Stato badges (Attivo/Disattivo + Permanente), Attivato il, actions Modifica / Attiva-Disattiva. |
| `GET|POST /PRODUZIONE/annunci/nuovo` (`CreaAnnuncio`) | `vwCreaAnnuncio` | Geko | `AnnuncioForm` (fields `date`, `description`, `active`, `permanente`); initial `date=today, active=True`; valid → save, redirect list. |
| `GET|POST /PRODUZIONE/annunci/<id>/modifica` (`ModificaAnnuncio`) | `vwModificaAnnuncio` | Geko | Edit same form; 404 if missing. Note: re-saving an active announcement does **not** reset `activatedAt` (expiry counted from first activation). |
| `GET /PRODUZIONE/annunci/<id>/toggle` (`ToggleAnnuncio`) | `vwToggleAnnuncio` | Geko | Flip `active` and save (activation timestamp set/cleared by model). **State change via GET** (CSRF-able link) [SEC]. |
| `GET /PRODUZIONE/annuncioGeko` (`AnnuncioGeko`) | `views.vwAnnuncioGeko` (`Prod/views.py:35-42`) | none; returns empty unless authenticated Geko | Announcements with `active=True` and (`permanente` or `activatedAt ≥ now−1 day`); renders `annuncioGeko.html`: one Bootstrap `modal-content` per announcement (header "Annuncio" warning-coloured, date, `description|safe`, Chiudi). |

* Form UI [KEEP]: minimal rich-text editor – toolbar buttons Bold/Italic/Underline/Elenco/Elenco Num./Link (prompt for URL)/Pulisci using `document.execCommand` on a `contenteditable` div; the hidden textarea `id_description` is filled with `innerHTML` on submit.
* Display [KEEP]: Produzione home (`tplProductionHome.html`) loads `annuncioGeko` via HTMX on page load into `#dialog`; after swap, if non-empty, opens Bootstrap modal `#modal`; on `hidden.bs.modal` clears it. Multiple announcements stack inside one modal-dialog.
* Expiry job: `Plan/updater.py:8-16` `deactivateExpiredAnnunci` every 10 min: active, non-permanent, `activatedAt ≤ now−1d` → `active=False, activatedAt=None` (via `update`, bypassing `save`).
* [SEC] Stored XSS: description is raw HTML rendered `|safe` to every Geko user; any Geko user can author it. Rewrite: sanitize (allow-list b/i/u/ul/ol/li/a[href http(s)]/p/br).
* Plan home (`Plan/views.py:28`) also lists `Annunci.filter(active=True)` (other app).

---

## 7. Maintenance and scheduler

### 7.1 `scMaintenance()` (`Prod/maintenance.py:37-106`)
Scheduled every 10 h (`updater.py:18`) and via console `?maintenance=1`.
1. **Anagrafica sync** (logs say "AVVIO MANUALE" even when scheduled):
   * `ll` = distinct `(articolo, prezzo_ea)` of Linea whose `articolo` not in Anagrafica.
   * For each: create `Anagrafica(articolo, prezzo=float(prezzo_ea comma→dot))`, then set `location = Location(path.code='GK01-UDEF')` (raises DoesNotExist if missing – crashes whole job; the `if uloc` guard is ineffective). Bad price → ValueError aborts the job mid-way. One article with N different prices creates N rows.
   * **De-dupe**: iterate `Anagrafica.objects.all().reverse()` (queryset evaluated lazily *after* inserts; no ordering defined so `reverse()` is a no-op and order is DB-arbitrary); for each row, if `count(articolo) > 1` delete it. Result: exactly one row per article survives, but **which** one is arbitrary – it can delete the curated row (with real location/resources/tabulato/description) and keep the auto-created UDEF one [BUG – data loss]. O(n) count queries.
   * [TX] none; [RACE] runs concurrently with console command 5 and with user edits.
   * Rewrite: unique constraint on `Anagrafica.articolo`; `get_or_create`; never delete.
2. **Technical files cleanup**: in `Metodo/media/fileTecnici/`, delete regular files with mtime older than 30 days; log each deletion and total MB freed; missing dir → warning.

### 7.2 Scheduler bootstrap (`Prod/updater.py`, `Prod/apps.py`)
* `ProdConfig.ready()` → `updater.StartScheduler()`: global `BackgroundScheduler()`, `remove_all_jobs()`, add jobs, `start()`.
* Jobs (in-memory jobstore, host-local TZ):

| Job name | Function | Trigger | Area |
|---|---|---|---|
| `FetchOrderByMail` | `orders.scFetchOrderByMail` | every 30 min | orders chapter |
| `ImporthOrderByMail` | `orders.scImporthOrderByMail` | every 1 h | orders chapter |
| `Maintenance` | `maintenance.scMaintenance` | every 10 h | §7.1 |
| `SendPianificazioneReportEmail` | `pianificazione.scSendPianificazioneReportEmail` | cron 06:00 daily | §1.5 |
| (commented) `CreateWorkOrder` | `traccia.scCreateWorkOrders` | every 1 h | §3.10 [DEAD] |
| (commented, name clash) | `console.scAddDescriptionFromOrders` | every 20 h | §5.2 [DEAD] |

* Also imported but unused: `scSyncOrders`, `scLoadOrderFromGeko` [DEAD imports].
* Other apps' schedulers: `Plan/updater.py` (`createPM` every 20 min, `deactivateExpiredAnnunci` every 10 min), `Tickets/updater.py` (FetchTickets every 5 min, has a `scheduler.running` guard).
* [RACE – important] No single-instance guard: `ready()` runs in **every** Django process – each gunicorn/uwsgi worker, `runserver` (twice with autoreloader), `manage.py migrate/shell/…`. Consequences: N concurrent IMAP fetches/imports of the same files (duplicate order lines), N daily planning e-mails, N maintenance runs racing on Anagrafica de-dupe. Also importing `traccia.py` at startup runs DB queries at import time (`traccia.py:22-30`), which fails on a fresh DB and freezes config values until restart.
* [DECIDE] DuMetodo: dedicated worker (Celery beat / django-q / systemd timer / `django-apscheduler` with DB lock), explicit Europe/Rome TZ, idempotent jobs, job run log.

---

## 8. Reclami/Qualità/Tracce in other reports (read-only consumers)
* `Reports/report.py:308,311,416,428` and `Reports/dashboard.py:37,40` count/list `ControlloQlta` and `Tracciabilita` by `data_inserimento` range (dashboards; other analyst). Note `Tracciabilita.data_inserimento` is auto_now ("last modified") so such counts shift when a trace is re-saved.

---

## 9. Consolidated defect / risk list (this area)

Security
1. `console.py:136-141,263-267` – path traversal + arbitrary file delete via `?deleteAttachment=` (GET, CSRF-able). **Critical.**
2. `traccia.py:587,596,605` – `wotHandle`, `genCodiceBatteria`, `insertTracciaCavi` have no auth; `insertTracciaCavi` writes Ordine/Coding/Linea/Stored_Item/Tracciabilita from anonymous GET.
3. State-changing GETs: all console commands (`console.py:31-165`), `ToggleAnnuncio` (`annunci.py:57-63`), `wotHandle`, `insertTracciaCavi`.
4. `pianificazione.py:1250-1274` – report can be mailed to arbitrary external addresses; unescaped `message` in HTML body (`:1190`); subject unvalidated.
5. `annuncioGeko.html:7` – `description|safe` stored XSS.
6. `vwLog` (`console.py:226-252`) readable by any Geko user; logs + attachments + mailed reports under public `MEDIA_ROOT` (`settings.py:157-158`, `Metodo/urls.py` `static()`), DEBUG defaults True.
7. SMTP password in DB clear text (`Configuration.EMAIL_METODO_PASSWORD`, read in `mailing.py:25`).
8. Uploads (FileOrdini, Reclami) without type/size validation, served publicly.
9. Customer group sees all batteries/cavi/qualità/reclami/display records (no customer scoping) – `traccia.py:39,400,475,699,829`.
10. Exception messages returned to client (`pianificazione.py:431,1274`).

Functional bugs
11. `pianificazione.py:1265-1274` – no success response → 500 after sending → duplicate e-mails.
12. Text vs code drift: 3 vs 4 weeks, 4 vs 8 weeks backlog, exclusion criteria (`tplPianificazione.html:10,46-47,68,75,83,88`; docstring `pianificazione.py:32`).
13. `traccia.py:198,200` – generic trace discards user `valore`.
14. `traccia.py:182-185` + `Generale/tplLista.html` "Crea" – existing code always rejected; cannot add events to an existing generic item.
15. `traccia.py:175` – `rif_ordine__contains(...).first()` links arbitrary order; `:253` `get` fails on duplicates → silently no link.
16. `traccia.py:257-261` – pseudo battery Linea re-created on MultipleObjectsReturned; `:647` pseudo cable Linea; both pollute `Linea`.
17. `traccia.py:632` – `insertTracciaCavi` creates bare Ordine rows.
18. `traccia.py:422` – batteries "data inserimento" filter applies to `data`.
19. `traccia.py:519,534` – cable export overwrites column 5 (product code lost).
20. Exports ignore filters (filters POST, export GET): `traccia.py:404-424,479-503,833-859`.
21. Date ranges with date strings on DateTimeFields exclude the end day; empty date strings → 500 (`traccia.py:331-337,490-496,705-717`).
22. `traccia.py:559-570` (`vwPrint` without barcode) and `:734-771` (`vwCreaLottoCommessa` GET) → UnboundLocalError 500.
23. `traccia.py:597-602` – `genCode` without `radix` → 500; generated codes not checked for uniqueness.
24. `Reclami.data` auto_now overwrites user date (`models.py:272`).
25. Bare Manager iterated in templates when POST without filters (`traccia.py:50-62`, `files.py:68-81`).
26. Hard-coded Check ids 8/9 (`traccia.py:104,111`).
27. Cavi "Intestato" button semantics (`Cavi/tplLista.html:112`) – wrong `prodotto`, new item per click, render-time timestamp.
28. Console "Crea Ordini" count sign (`console.py:46-57`); hard-coded absolute URLs in console template.
29. `scMaintenance` / console `updateAnagrafica` arbitrary de-dup → data loss (`maintenance.py:60-62`, `console.py:116-118`); crash if GK01-UDEF missing or price unparsable.
30. `distinctTraccia` (`console.py:124-133`) – O(n²), ignores stored_item, crashes on null `data`, window 180 s vs comment 5 min.
31. `vwAggiornaSoglia` accepts negative/NaN/inf.
32. Template filter values not re-populated (context/template name mismatches) in Batterie/Cavi/Generale/Foto lists; malformed HTML in Cavi list; scripts outside blocks never rendered.
33. `Errori` date format `"Y-m-d, h:m:s"` shows month in minutes position (`tplConsole.html:149`).

Concurrency / transactions
34. No `transaction.atomic` anywhere in this area: multi-row creates (Coding+Stored_Item+Tracciabilita; LOTTO+BUSTA; ControlloQlta batch; Anagrafica sync).
35. Check-then-insert races: Coding uniqueness, cable dedup, WOT "exists OPN".
36. Scheduler started in every process (`apps.py:8-10`, `updater.py:12-20`) → duplicated jobs/e-mails; manual console triggers race scheduled jobs on the same files.
37. Module-level DB queries at import (`traccia.py:22-30`) – stale config, startup failure on empty DB.

Dead code
38. `area_articoli_map` (`pianificazione.py:105-108`), duplicated planning computation (`:36-351` vs `:440-623`), `scCreateWorkOrders` (not scheduled), `WorkOrderTraccia` UI (no template uses `wot`), `scAddDescriptionFromOrders`, `Display/uxReply.html`, empty `Foto/Generale/Reclamo uxReply.html`, `TracciabilitaForm` import, `from pickle import FALSE` (`traccia.py:1`), `render_to_string`, `Max/Count`, `settings` imports, unused `Test.tipo/misura_min/misura_max/percentuale`, `Reports/produzione.py::vwProduzione` and `vwPianificazione` (not routed, §10).

---

## 10. `Reports/produzione.py` – reports on Prod data (candidates to move into DuMetodo)

All require **`Geko Admin`**. Mounted under `/REPORTS/` (`Reports/urls.py:19-31`). Prod models used: `Anagrafica`, `DocumentiTrasporto`, `Ordine`, `Linea`, `Configuration`; plus non-Prod `Plan.Location`, `Tickets.Ticket`, `Timbratura.Timbratura`, `Reports.ResourcePayrollSettings`.

| Report (view) | URL | Routed | Prod models queried | What it computes | Move to DuMetodo? |
|---|---|---|---|---|---|
| `vwProduzione` (`:24-99`) | – | **No** (dead) | Ordine, Linea, Configuration(PREZZO_RESA_ORARIA) | Lines with `data_scadenza` in range (default today..+14d), orders not ANN, appendice contains T or R, not annullata/duplicata; per line value Σ(qta×prezzo)/resa → hours, ISO week; XLSX `ListaProduzione.xlsx` (Serie, Ordine, Appendice, Anno, Articolo, Variante, Data Scadenza, Quantita, Tempo, Settimana). | Superseded by §1; drop. |
| `vwControlloPrezzi` (`:102-279`) | `produzione/controlloPrezzi` | yes | Anagrafica, DocumentiTrasporto, Linea | Invoiced lines (`emessa_bolla`, not annullata/duplicata, `data_scadenza` range default −14d..today, optional article list partial match) whose `prezzo_ea` ≠ `Anagrafica.prezzo`; enriched with DDT number (`DocumentiTrasporto` keyed by serie/anno/appendice/rif/n_riga); filters on DDT/anagrafica price ranges and DDT number; export. | Yes (pure Prod data). |
| `vwControlloPrezziOrdiniInseriti` (`:281-448`) | `produzione/controlloPrezziOrdiniInseriti` | yes | Anagrafica, Ordine, Linea | Same comparison for not-yet-invoiced lines of orders in stato INS/LAV/SPP/SPE. | Yes. |
| `vwPianificazione` (`:450-607`) | – | **No** (dead) | Configuration, Ordine, Linea, Anagrafica | Older weekly planning by ISO week (articles or totals per area, appendice T/R, excluding CON/ANN/SPE/SPP); broken when date params are passed (string arithmetic in `rangeofdates`) and `float()` without comma handling. | Drop (superseded by §1). |
| `vwAnagrafica` (`:613-710`) | `produzione/anagrafica` | yes | Anagrafica (+Location via FK) | List/search Anagrafica (filters `solo_nuovi`, `solo_con_prezzo_ricalcolato`, `search` on articolo/reparto); POST JSON `{articolo, prezzo_ricalcolato, data_prezzo}` updates those fields (no validation). | Yes (Anagrafica maintenance). |
| `vwStoricoPrezziArticolo` (`:712-828`) | `produzione/storicoPrezziArticolo` | yes | Anagrafica, Linea | Price history of an article from `Linea.prezzo_ea` over time; `show_changed` lists articles with >1 distinct price in period (default last 30 d). | Yes. |
| `vwAnalisiDDT` (`:830-956`) | `produzione/analisiDDT` | yes | DocumentiTrasporto | DDT lines in `data_ddt` range (default −30 d) with many `icontains` filters (no_ddt, rif, serie, anno, appendice, articolo, variante, cliente, n_riga, inviato). | Yes. |
| `vwLineeOrdine` (`:958-1124`) | `produzione/lineeOrdine` | yes | Linea | Raw order-line browser with filters (dates on data_scadenza, flags emessa_bolla/annullata/blocca_linea/scorporato, text filters). | Yes. |
| `_build_riepilogo_mensile_fatturazione` + `vwDatiFatturazione` (`:1133-1266`) | `produzione/datiFatturazione` | yes | Linea, Anagrafica (+Ticket, Timbratura, ResourcePayrollSettings) | Monthly billing summary for a year: Fatturato Netto (Σ qta×prezzo of `emessa_bolla` lines by `data_consegna`), Numero Articoli T, Valore Commesse R, Numero Ticket, Conguaglio Bolle (Σ qta×(prezzo anagrafica − prezzo linea) by `data_scadenza`), Ore Risorse (hourly-paid resources minus "Mensa"), Valore da Scorporare (scorporato lines). | Mixed – Prod parts yes; ticket/timbratura parts need cross-system data. [DECIDE] |
| `exportTimbratureFatturazione` (`:1268-1383`) | `…/exportTimbrature` | yes | none (Timbratura only) | Timbrature export. | No (not Prod). |
| `exportOrdiniFatturazione` (`:1385-1492`) | `…/exportOrdini` | yes | Linea, Ordine | Lines delivered (`data_consegna`) in period (default YTD), not annullata/duplicata, value per line, joined to Ordine via subquery; XLSX. | Yes. |
| `exportLineeScorporateFatturazione` (`:1494-1578`) | `…/exportLineeScorporate` | yes | Linea | Lines `scorporato=True` delivered in period; XLSX with dates, keys, articolo, variante, qta, prezzo, cliente, magazzino. | Yes. |
| `vwAnalisiProduttivita` (`:1752-2182`) + helpers `_build_period_summary`, `_summarize_delivered_values`, `_weighted_hours_by_resource` | `produzione/analisiProduttivita` | yes | DocumentiTrasporto, Linea (+Timbratura, ResourcePayrollSettings) | Weekly productivity (last ~2 months + compare week): objects delivered by DDT (`Coalesce(data_ddt, data_inserimento)`) and by Linea `data_consegna` (excl. annullata/duplicata/scorporato), split by appendice T/R/CL/other, delivered value, worked hours (weighted by payroll %), productivity = objects/hours; XLSX export. | Partially (needs hours from Timbratura). [DECIDE] |
| `vwAnalisiProduttivitaGiornaliero` (`:1719-1750`) | `…/analisiProduttivita/giornaliero` | yes | same as above | AJAX JSON: per-day breakdown of a week (`week_start=YYYY-MM-DD`, 400 on bad input). | With the above. |
| `vwAnalisiMateriale` (`:2235-…`) + `_get_reparti_materiale`, `_build_materiale_week` (`:2184-2233`) | `produzione/materialeReparto` | yes | Configuration, Anagrafica, Linea | Per reparto (Anagrafica.location.area) and per week (default last 8 weeks): hours "a scadenza" (lines by data_scadenza) vs "consegnato" (lines by data_consegna), excl. annullata/duplicata, hours = Σ qta×prezzo / PREZZO_RESA_ORARIA. | Yes – natural companion of §1 planning. |

Note the inconsistent definition of "hours" across reports and §1 (appendice T/R filter here vs. appendice ≠ '0' in §1; status filters differ; scorporato excluded only in productivity). [DECIDE] one shared "line workload" function in DuMetodo with explicit, documented filters.

---

## 11. Rewrite checklist (what must survive)
* Planning page with 4 configurable weeks (make N configurable), backlog window (make configurable; current 8 weeks), per-area weekly capacities with current-week pro-rating on a working calendar, status thresholds 80 %/100 %, XLSX with the exact two-sheet layout (incl. "Quadrisitca" grouping – make grouping configurable), manual mail + daily 06:00 scheduled mail to configured TO/CC.
* Traceability: item codes (prefix + 6 uppercase hex, uniqueness enforced), event types IN/RC for batteries, generic key/value events, LOTTO/BUSTA per commessa, cable events from machines (authenticated API) with lot (`rawMaterialCode`), length and asset; Code128 label print page.
* Quality checks per Test template (checks with campione/ko, distinta/disegno presence), complaints with attachment, display test with barcode-wedge `sito$$articolo` fast entry and sticky fields, photo upload with client-side resize and mobile camera.
* Lists with filters + XLSX exports (filters must apply to exports), customer read-only access (scoped if possible).
* Console replaced by an admin "Operations" page: run import/fetch jobs (async with status), acknowledge errors, view stuck files safely, mass price update with audit, log tail viewer.
* Announcements with sanitized rich text, 1-day expiry unless permanent, popup on home.
