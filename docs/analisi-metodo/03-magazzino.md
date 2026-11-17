# 03 – Magazzino (Warehouse / Inventory, BOM, Material Requests, Incoming DDT, Email)

Source analysed: branch `Metodo`, `/home/user/metodo-branch`.
Files: `Prod/inventario.py` (1611 lines), `Prod/mailing.py`, `Prod/management/commands/readMail.py`,
`Prod/management/commands/{elimina_duplicati_richieste,normalizza_codici_richieste,migrate_to_codice_componente}.py`,
`Prod/templates/Inventario/*` (20 templates), the "Magazzino" menu in `Prod/templates/tplCore.html:86-101`,
models in `Prod/models.py:405-588`.

All URLs below are mounted under `/PRODUZIONE/` (`Metodo/urls.py:21`).

---

## 0. Executive summary (read this first)

| Area | What it really is today |
|---|---|
| Inventario | An **append-only log of physical-count entries** (`Inventario` rows: component, qty, timestamp). There are **no stock movements**: nothing decrements stock (no BOM consumption, no shipments) and incoming DDT material (`MaterialeIngresso`) is **not** added to stock. The only "giacenza" figure is the xlsx export `export=1`, which is `SUM(qta)` grouped by the legacy text fields `articolo, variante`. |
| Distinte base (BOM) | Single-level BOM: `DistintaBase` (finished-product code + variant) -> `ComponenteDistinta` rows (component, qty, UM, position, note). No explosion, no multi-level, no costing, no link to `Anagrafica` (finished goods) nor to stock. Soft delete on header. |
| Anagrafica componenti | Master data `CodiceComponente` (codice+variante unique). Created implicitly by almost every screen (`get_or_create`). Edit is done only via Django admin. |
| Anagrafica finiti | List/filter of `Anagrafica` (finished goods) with inline edit of description and reparto (Location), plus toggle of `nuovo_articolo`. |
| Richiesta materiali | Per-department (Pulsanti `GK01-PLS`, Elettronica `GK01-ETT`) list of open material requests. Operators add codes; admins send "Mancante / Urgente / Sollecito" emails to the DMG warehouse with preview modal, mark received, delete. |
| DDT ingresso | `DDTIngresso` header + `MaterialeIngresso` lines. Three entry paths: manual form, CSV/TXT upload (`;`-separated gestionale export), proprietary XML (`<Docs><Doc>…`) upload. |
| OCR | **Not implemented in this branch.** No OCR library is imported anywhere (no pytesseract/easyocr/opencv in code or installed). Only orphan templates (`tplCaricaDDT`, `tplVerificaDDT`, `tplDebugOCR`, `tplRicercaMateriali`) and model fields (`foto_originale`, `foto_binaria`, `errore_elaborazione`) remain. URL names `carica_ddt`, `verifica_ddt`, `debug_ocr`, `ricerca_materiali` are **not registered** in `Prod/urls.py`. See §8 for the intended behaviour reconstructed from templates. |
| XML format | **Not FatturaPA.** A proprietary export of the company ERP ("gestionale"): root `<Docs>`, children `<Doc>` with `DocType, DocNumber, DocDate, Anag/Name, DocRows/DocRow/{Product/Code, Description, Quantity, Price}`. Parsed with stdlib `xml.etree.ElementTree`. |
| Email | `mailing.sendEmail()` – plain smtplib STARTTLS on port 587, credentials from `Configuration` table, logs every successful send in `SentEmail`. `readMail` management command = IMAP fetch of order zip attachments (orders domain, not DDT), with hard-coded credentials. |

Authorization for **every** view in this area: `@user_passes_test(isGeko)` + `@login_required` (user must be in Django group **`Geko`**). Unauthenticated users or non-Geko users are redirected to `LOGIN_URL=/login/`. Some buttons are additionally hidden in templates for users not in group **`Geko Admin`**, but **the server never checks `Geko Admin`** (see bugs).

Common context keys passed to full-page templates: `site='METODO'`, `app='Production'`, `pageTitle`. All full pages extend `tplCore.html` (Bootstrap 5 via django_bootstrap5, jQuery 3.7.1, htmx, bootstrap-table). `tplCore.html` does **not** render Django `messages`; only the DDT templates render them.

---

## 1. Data model used by this area (summary – full model spec is in another document)

| Model | Key fields | Notes |
|---|---|---|
| `CodiceComponente` (`models.py:407`) | codice(50), variante(20, null), descrizione(text), um(3, choices `LIST_UM`= EA "Pezzi" / M "Metri", default EA), prezzo_medio(float), fornitore(100), note, attivo(bool, default True), data_creazione(auto_now_add), data_modifica(auto_now) | `unique_together(codice, variante)`; on PostgreSQL NULL variante is **not** unique -> duplicates `(X, NULL)` possible. `__str__` = `codice` or `codice.variante`. |
| `Inventario` (`models.py:435`) | componente FK CodiceComponente (null, CASCADE), articolo(50, legacy), variante(50, legacy), qta float, data datetime | One row per count entry. Legacy text fields still written. |
| `RichiesteMateriali` (`models.py:455`) | codice(50), variante(50), data_richiesta **DateField**, data_ricezione (never written), numero_richieste int null, mail_inviata_1/2/3 bool (Mancante/Urgente/Sollecito), materiale_ricevuto bool, reparto FK `Plan.Location` (DO_NOTHING), materiale_urgente bool | No uniqueness constraint -> duplicates (a cleanup command exists). |
| `SentEmail` (`models.py:480`) | subject, body, from_address(EmailField), to_address(EmailField – but receives a comma-separated list), cc_address, reply_address, sent_at, attachment_path, attachment_name, success, error_message | Only successful sends are ever recorded. |
| `ProcessedEmail` (`models.py:306`) | message_id **IntegerField unique** (IMAP UID), subject, data_inserimento | Used by readMail and by orders.py fetchers. |
| `DDTIngresso` (`models.py:497`) | numero_ddt(50), data_ddt, fornitore(100), data_elaborazione(auto_now_add), foto_originale (ImageField `ddt_ingresso/`), foto_binaria (`ddt_ingresso/binarie/`), elaborato bool, errore_elaborazione text, utente FK User (CASCADE, required) | foto_*/errore_* only used by the missing OCR feature. |
| `MaterialeIngresso` (`models.py:516`) | ddt FK (CASCADE, related_name `materiali`), codice(50), variante(20), descrizione(**200**), quantita float, unita_misura(10), prezzo_unitario Decimal(10,2), data_ingresso date, verificato bool, note | Not linked to CodiceComponente by FK (text only). |
| `DistintaBase` (`models.py:536`) | codice_padre(50), variante(20), descrizione, data_creazione, data_modifica, utente_creazione/modifica FK User SET_NULL, attiva bool, note | `unique_together(codice_padre, variante)`, includes soft-deleted rows. Ordering `-data_creazione`. |
| `ComponenteDistinta` (`models.py:563`) | distinta FK (CASCADE, related_name `componenti`), componente FK CodiceComponente (null), codice_componente(50, legacy), variante(20), quantita float, um(3, LIST_UM), posizione int, note | Ordering `distinta, posizione, codice_componente`. |
| `Anagrafica` (`models.py:343`) | articolo(50), location FK Plan.Location, um, prezzo, prezzo_ricalcolato, data_prezzo, description, tabulato, risorsa_1..3, nuovo_articolo (default True) | Finished goods. |
| `Plan.Location` | site, area, path FK `locPath(code unique)`, parentPath | Reparto. `GK01-PLS` = Pulsanti, `GK01-ETT` = Elettronica. |
| `Configuration` (`models.py:118`) | chiave(50), valore(**50**) | Key/value store for email addresses and SMTP credentials (plain text). |

---

## 2. Inventario

### 2.1 `GET|POST /PRODUZIONE/vwInventario/` – name `Inventario` – `vwInventario` (`inventario.py:31-135`)

Permission: group `Geko`.

Branches, evaluated in order:

1. **POST (any body)** (`:35-53`): reads `articolo`, `variante`, `qta`, computes `now()` and then **does nothing with them** (dead code). Renders partial `Inventario/uixListaInventario.html` with full list. No UI uses this branch (the add form posts to `vwAddInventario`).
2. **GET `?export=<1|2|3>[&year=YYYY]`** (`:56-118`) -> xlsx download (xlsxwriter, in-memory, `remove_timezone=True`).
   - Filename: `Inventario_<year>.xlsx` if `year` given, else `Inventario.xlsx`.
   - `export=1` "Somma Totale": `Inventario.values('articolo','variante').annotate(tot=Sum('qta'))` – **this is the only "giacenza" computation in the system**: total of all count entries ever recorded per legacy (articolo, variante). Note: it sums *all* historic counts, it does not take the latest count; and it groups on legacy text fields, not on `componente`.
   - `export=3` with `year`: rows of that year `values('articolo','variante','qta','data')` ordered `-data`. `year` is not validated (non-numeric -> 500).
   - any other value (incl. `2`, or `3` without year): all rows `values('articolo','variante','qta','data')` (unordered).
   - Sheet columns: A `Articolo`, B `Variante` (empty if null), C `Quantita` (format `#,##0` – decimals are hidden), D `Data` (only for export 2/3, format `dd/mm/yyyy hh:mm`). Header format bold, bg `#4472C4`, white font. Column widths 20/20/12/18. Each cell write wrapped in bare `try/except: pass` (`:88-108`).
   - Note for `export=3` without `year` header row has column D but data = all rows.
3. **GET default**: `dati = Inventario.objects.all().order_by('-data')`; `codici_componente = CodiceComponente.filter(attivo=True).order_by('codice','variante')`.
   - `?uixList=1` -> partial `uixListaInventario.html`; else full page `tplInventario.html`.

**Template `tplInventario.html`** (full page):
- Left sticky sidebar (col-2):
  - "Aggiungi Articolo" form: `hx-post="/PRODUZIONE/vwAddInventario/"`, `hx-target="#list"`, CSRF header. Fields: `articolo` (text, required, `list="codiciComponenti"` datalist), `variante` (optional), `qta` (number, step 0.01, required).
  - "Esportazioni": links `?export=1` (Somma Totale), `?export=2` (Esporta Tutto).
  - "Ricerca Articolo": client-side filter `#searchInput` (keyup) hiding rows of `#list table tbody tr` whose `cells[0]` or `cells[1]` text does not contain the filter; "Pulisci" clears. Re-initialised on `htmx:afterSwap` of `#list`.
- Main: `<div id="list" hx-get="/PRODUZIONE/vwInventario?uixList=1" hx-trigger="load">`.
- Datalist `codiciComponenti`: `<option value="{{ codice }}">` where `codice` renders `CodiceComponente.__str__` i.e. **`CODICE.VARIANTE`** (see bug B-INV-3), label = descrizione.

**Template `uixListaInventario.html`**: `{% regroup dati by data.year %}` -> one Bootstrap card per year (first expanded, others collapsed), header "Anno YYYY" + badge with row count + "XLS" button linking `?export=3&year=YYYY`. Table columns: Codice Componente (FK `__str__` in blue, or legacy `articolo.variante` + "(legacy)"), Descrizione (FK descrizione or "-"), Quantità (badge), Data (`d/m/Y H:i`).
- Client-side filter uses `cells[1]` which is *Descrizione*, not Variante (harmless mismatch).

### 2.2 `POST /PRODUZIONE/vwAddInventario/` – name `AddInventario` – `vwAddInventario` (`:137-177`)

Permission: `Geko`. Method not enforced (GET also executes and creates a record from missing params).

Params (POST): `articolo`, `variante`, `qta`. **No validation.**

Steps:
1. `data = datetime.datetime.now()` (naive, while `USE_TZ=True` -> RuntimeWarning, stored as UTC-interpreted server local time).
2. `CodiceComponente.get_or_create(codice=articolo, variante=variante or None, defaults={descrizione: "Aggiunto da inventario il dd/mm/YYYY", attivo: True})`.
3. Create `Inventario(componente=codice_comp, articolo=articolo, variante=variante or None, qta=qta, data=now)`.
4. Re-render partial `uixListaInventario.html` with the full list (all years).

Side effects: may create master data. No audit (user not recorded on Inventario row).

### 2.3 Inventory bugs / issues
- **B-INV-1** No stock movement model: incoming DDT, BOMs and shipments never update stock. If the rewrite needs real giacenze, it must be designed (ledger with movement type, sign, source document).
- **B-INV-2** `export=1` sums all counts ever (`inventario.py:67`) – meaningless as "giacenza" if counts are repeated; groups on legacy text fields, ignoring `componente`.
- **B-INV-3** Datalist value is `CODICE.VARIANTE` (`tplInventario.html:148`), so picking a suggestion puts `CODICE.VARIANTE` into `articolo`; with empty `variante` the server creates a **new bogus CodiceComponente** with `codice="CODICE.VARIANTE"` (`inventario.py:147`).
- **B-INV-4** `qta` not validated: missing -> `False` saved as 0.0; non-numeric -> unhandled 500 (`:162`). `articolo` missing -> component with codice `"False"`.
- **B-INV-5** `get_or_create` can raise `MultipleObjectsReturned` when duplicates `(codice, NULL)` exist (Postgres NULL uniqueness) -> 500.
- **B-INV-6** Codes not normalised (case/whitespace) unlike RichiesteMateriali (uppercased).
- **B-INV-7** List not paginated, N+1 queries on `d.componente` (no `select_related`).
- **B-INV-8** Dead POST branch in `vwInventario` (`:35-53`).
- **B-INV-9** Naive datetimes with `USE_TZ=True` (`:38`, `:144`).
- **B-INV-10** `year` unvalidated (`:58,71`).
- **B-INV-11** Quantity number format `#,##0` hides decimals although qta is float (`:82`).

---

## 3. Richiesta Materiali (Pulsanti / Elettronica)

Menu entries: "Richiesta Materiale Pulsanti" -> `/PRODUZIONE/vwRichiestaMateriale/?page=GK01-PLS`; "Richiesta Materiale Elettronica" -> `?page=GK01-ETT` (`tplCore.html:95-96`).

### 3.1 `GET|POST /PRODUZIONE/vwRichiestaMateriale/` – name `Richiesta Materiale` – `vwRichiestaMateriale` (`:179-476`)

Permission: `Geko` (server). Admin-only actions are hidden in template for non `Geko Admin`, **not enforced server side**.

#### 3.1.1 POST – new request / re-request (`:183-235`)
Params: `codice` (uppercased), `variante` (uppercased; `False` if absent, `''` if empty), `reparto` (locPath code; hidden readonly input `GK01-PLS` or `GK01-ETT`), `urgente` (`'on'` if checked).

Steps:
1. `reparto = Location.objects.get(path__code=reparto_code)`.
2. Try to find an **open** request: `RichiesteMateriali.get(codice, variante, materiale_ricevuto=False, reparto)`.
   - If found: if `urgente=='on'` set `materiale_urgente=True` (never reset to False); if `numero_richieste` truthy increment by 1, else **no-op** (`:203` bare expression – bug, stays NULL/0); `data_richiesta = now`; save.
3. On **any** exception (bare `except:` `:207`) — DoesNotExist, but also MultipleObjectsReturned, Location errors — create a new row: `codice, variante, data_richiesta=now, mail_inviata_1/2/3=False, materiale_ricevuto=False, numero_richieste=1, reparto, materiale_urgente=(urgente=='on')`.
   - Because MultipleObjectsReturned is swallowed, once 2 duplicates exist every new request creates a third, etc. (origin of the `elimina_duplicati_richieste` command).
   - Note `variante=''` (empty input) vs `None`: lookup uses `''`, stored `''`; legacy rows with NULL won't match -> more duplicates.
4. Response: partial `uixListaMateriale.html` with `dati = open requests of that reparto ordered -data_richiesta`, each annotated with `esiste_in_anagrafica = CodiceComponente.filter(codice=item.codice, variante=item.variante or None).exists()` (N+1). Context lacks `reparto` (so the export link in the swapped partial becomes `?export=1&page=` -> renders with default page GK01-PLS – bug B-RM-9).

#### 3.1.2 GET `?delete=1&id=N` (`:241-253`)
- Deletes row `id` (hard delete; DoesNotExist -> 500). Returns body "Richiesta eliminata" + header `HX-Refresh: true`.
- If `id` missing: builds a response but **does not return it** (`:252-254`), falls through to normal page render.
- UI: "Elimina" button (admin only in template) with `hx-confirm="Vuoi eliminare la Richiesta?"`, `hx-swap="none"`.

#### 3.1.3 GET `?ricezione=1&id=N` (`:256-266`)
- Sets `materiale_ricevuto=True` (does **not** set `data_ricezione`). Returns "Ricevuto" with `HX-Refresh: false`; UI swaps the "Ricevi" button (`hx-target="#btnRic-<id>"`, outerHTML) with the text "Ricevuto". Confirm "Hai veramente ricevuto il materiale?". Row disappears on next reload (only open requests are listed).

#### 3.1.4 GET `?mail=1&...` – email flows (`:268-408`)
Recipients come from the `Configuration` table (key -> value), fetched **before** any parameter check (missing key -> 500):
- `EMAIL_METODO_ADDRESS` -> **From**
- `EMAIL_MAGAZZINO_DMG_1` + `,` + `EMAIL_MAGAZZINO_DMG_2` -> **To** (comma-separated)
- `EMAIL_MAGAZZINO_GEKO` -> **Reply-To** (not a recipient!)
- No CC. Call: `sendEmail(subject, testo, metodoMail, dmgMail, gekoMail)`.

Type mapping (`type` param) -> flag:
| type | flag set | line text (bulk) |
|---|---|---|
| `Mancante` | `mail_inviata_1` | `{code} - Materiale in esaurimento. <br>` |
| `Urgente` | `mail_inviata_2` | `{code} - Materiale Urgente per commessa. <br>` |
| `Sollecito` | `mail_inviata_3` | `{code} - Sollecito Materiale. <br>` |
`code` = `codice.variante` if variante else `codice`.

**(a) Selected ids – the only flow used by the UI** (`ids` + `type`, `:281-344`)
- `ids` = comma-separated ints (non-int -> 500). Query `RichiesteMateriali.filter(id__in=…)` (no reparto / open-status scoping).
- Empty -> body "Nessun articolo selezionato", `HX-Refresh:false`.
- subject = `Richiesta Materiale - {type}`; body HTML: `Salve, <br>Con la presente si richiedono i codici: <br><br>` + one line per item (per table above) + `<br>Saluti<br>`.
- `preview=1` -> render `uxModalPreviewMail.html` with `subject, testo, ids, type, destinatari="{dmgMail}, {gekoMail}", num_articoli`.
- `confirm=1` -> for each item set the flag and save (**before** sending), then `sendEmail(...)`; respond "Mail inviata" + `HX-Refresh: true`.
- Neither preview nor confirm -> falls through to the end -> responds "Mail inviata" with refresh **without sending** (`:403-405`).

**(b) Single id** (`id` + `type`, `:347-372`) – not used by current UI.
- Mancante: subject `Richiesta Materiale - {code} - Materiale in esaurimento`, body `Con la presente si richiede {code} - Materiale in esaurimento. <br>`; send; set flag 1.
- Urgente: subject `Richiesta Materiale Urgente - {code} - Per Commessa`, body `… {code} - Materiale Urgente per commessa. <br>`; flag 2.
- Sollecito: subject `Sollecito di richiesta Materiale - {code} - Materiale Esaurito`, body `… {code} - Materiale Esaurito. <br>`; flag 3.
- Here send happens before flag save.

**(c) All pending of a reparto** (`all` + `type` + `reparto=<code>`, `:375-401`) – not used by current UI.
- Items: `materiale_ricevuto=False` AND all three flags False AND `reparto__path__code=reparto`.
- Subject `Richiesta Materiale`; body like (a) (with `Salve, <br> Con…`); flags saved per item while building; email sent **even if there are zero items** or unknown type.

Final response for any mail call: "Mail inviata", `HX-Refresh: true`.

#### 3.1.5 GET default list / export (`:410-476`)
- `page` (default `GK01-PLS`) -> `Location.get(path__code=page)` (unknown -> 500).
- `dati` = open requests for the reparto, ordered `-data_richiesta`, each with `esiste_in_anagrafica`.
- `?export=1` -> xlsx `RichiesteMateriali_<code>.xlsx`; columns: Codice, Variante, In Anagrafica ("Sì"/"No"), Data Richiesta (`dd/mm/yyyy`), Numero Richieste (`or 0`), Materiale Urgente, Mail Mancante, Mail Urgente, Mail Sollecito (all "Sì"/"No"). No header formatting; a new format object is created per row (`:439`) and esiste_in_anagrafica is re-queried per row.
- `?uixList=1` -> partial `uixListaMateriale.html` (context `dati`, `reparto`).
- Else full page: `tplRichiestaMaterialiPls.html` if `page=="GK01-PLS"`, otherwise `tplRichiestaMaterialiElett.html` (for **any** other code).
- Trailing `else: print('No ID')` (`:407-408`) runs for every non-mail GET – noise.

#### 3.1.6 Templates
- `tplRichiestaMaterialiPls.html` / `tplRichiestaMaterialiElett.html`: identical except colours (primary vs warning) and the hard-coded reparto `GK01-PLS` / `GK01-ETT` (readonly input and info card "Pulsanti"/"Elettronica"). Sidebar form `hx-post="/PRODUZIONE/vwRichiestaMateriale/"` -> `#list`; fields codice (required), variante, reparto (readonly), urgente (switch). Main `#list` `hx-get="…?uixList=1&page=<code>"`, trigger `load, refresh from:body`. Form is not reset after submit.
- `uixListaMateriale.html`:
  - Hidden modal `#modalPreviewMail` (content `#modalPreviewContent`, spinner).
  - Header: "Esporta Excel" (`?export=1&page={{reparto}}`); admin-only buttons "Mail Mancante / Mail Urgente / Mail Sollecito" calling `inviaMailSelezionati(tipo)`.
  - Client search box filters rows by full text.
  - Table: checkbox (select-all with tri-state sync), Codice, Variante, Anagrafica badge ("Presente" green / "Non in anagrafica" yellow), Data Richiesta (`d/m/Y`), Richieste (count badge + red "Urgente"), Comunicazioni (badges Mancante/Urgente/Sollecito or "-"), Azioni (admin only: Ricevi, Elimina).
  - `inviaMailSelezionati`: collects checked ids; alert if none; `htmx.ajax('GET', '/PRODUZIONE/vwRichiestaMateriale?mail=1&ids=…&type=…&preview=1', {target:'#modalPreviewContent'})` and shows modal.
- `uxModalPreviewMail.html`: shows Destinatari, Oggetto, Corpo (`{{testo|safe}}`), "Stai per inviare … N articolo/i"; buttons Annulla / "Conferma e Invia" (`hx-get …&confirm=1`, `hx-swap=none`, closes modal; server's `HX-Refresh:true` reloads the page).

#### 3.1.7 Related management commands
- `elimina_duplicati_richieste [--dry-run]`: uppercases codice/variante for open requests, groups open requests by `(codice, variante or '', reparto_id)`, keeps the one with highest `(mail3, mail2, mail1, data_richiesta, numero_richieste)`, sets on it `numero_richieste = Σ(n or 1)`, urgent = any, mail flags = any; deletes the others. Not in a transaction.
- `normalizza_codici_richieste`: uppercases codice/variante on all rows.
- Rewrite should replace both with a DB unique constraint (partial unique index on `(codice, coalesce(variante,''), reparto) WHERE NOT materiale_ricevuto`) and normalisation on input.

#### 3.1.8 Bugs / issues
- **B-RM-1** Bare `except:` (`:207`) turns MultipleObjectsReturned / any error into a new duplicate row; race between concurrent requests also creates duplicates (no unique constraint, no `select_for_update`).
- **B-RM-2** `numero_richieste` not incremented when NULL/0 (`:202-203`).
- **B-RM-3** Admin actions (delete, ricezione, mail) enforced only in template (`uixListaMateriale.html:33,121`), server allows any `Geko` user.
- **B-RM-4** State-changing GETs (delete, ricezione, mail confirm) – CSRF-able via a link/img.
- **B-RM-5** Stored HTML injection: user-entered `codice`/`variante` are concatenated into the HTML email body and rendered with `|safe` in the preview modal (`uxModalPreviewMail.html:31`) -> XSS.
- **B-RM-6** Preview shows `destinatari = DMG, GEKO` but GEKO is only Reply-To (`:309`) – misleading.
- **B-RM-7** Flags saved before sending in bulk flows (`:328-338`, `:384-399`): SMTP failure -> 500, flags already true, no SentEmail error record.
- **B-RM-8** Mail endpoint responds "Mail inviata" even when nothing was sent (missing preview/confirm, unknown type, zero items); "all" flow sends empty emails.
- **B-RM-9** POST response lacks `reparto` in context -> export link in the refreshed partial points to default page.
- **B-RM-10** `data_ricezione` never set on receipt (`:259-261`).
- **B-RM-11** `delete` without id builds but doesn't return a response (`:252`).
- **B-RM-12** `ids` not scoped to reparto/open state; int parsing unguarded.
- **B-RM-13** Configuration lookups done for every mail call; missing key -> 500. `Configuration.valore` max 50 chars limits email lists.
- **B-RM-14** Any `page` other than GK01-PLS renders the Elettronica template whose form always posts `GK01-ETT`.
- **B-RM-15** `urgente` can never be reset to False once set on an open request.

---

## 4. Distinte Base (BOM)

### 4.1 `GET|POST /PRODUZIONE/vwDistinteBase/` – name `Distinte Base` – `vwDistinteBase` (`:1193-1455`)

Permission: `Geko`; UI edit/delete/add-component visible only to `Geko Admin` (not enforced server side).

#### POST – create / update header (`:1201-1250`)
Params: `distinta_id` (hidden; empty = create), `codice_padre` (required, stripped), `variante`, `descrizione`, `note`.
- Missing codice_padre -> partial `uixListaDistinteBase.html` with `error` and **no `distinte`** (list disappears until refresh).
- Update: `DistintaBase.get(id)`; set codice_padre, variante (or None), descrizione, note, `utente_modifica=request.user`; save. Not found -> silently ignored.
- Create: new `DistintaBase(…, utente_creazione=utente_modifica=user)`.
- Duplicate `(codice_padre, variante)` (including soft-deleted rows) -> IntegrityError -> 500 (**B-DB-1**).
- Response: partial list (`distinte` = active ordered `-data_creazione`, `codici_componente` active).
- Codes are not uppercased/normalised. No link to `Anagrafica` (finished goods) – codice_padre is free text.

#### GET actions (all state-changing GETs, return `HX-Refresh: true`)
- `?add_component=1&distinta_id&codice_componente&variante&quantita(default '0')&um(default 'EA')&note_comp` (`:1257-1304`):
  1. `distinta = get(id)`.
  2. `CodiceComponente.get_or_create(codice, variante or None, defaults={descrizione: "Aggiunto da distinta base <codice_padre> il dd/mm/YYYY", um, attivo True})`.
  3. `next_pos = max(posizione)+1` (race: concurrent adds get the same position).
  4. Create `ComponenteDistinta(distinta, componente=FK, codice_componente=text, variante, quantita=float(quantita), um, posizione, note)`.
  5. Touch distinta (`utente_modifica`, save -> `data_modifica`).
  - DoesNotExist/ValueError swallowed; response always "Componente aggiunto". `um` not validated against LIST_UM. Same component may be added twice (no uniqueness).
- `?delete_component=1&component_id` (`:1307-1322`): hard delete the line; touch distinta; "Componente eliminato".
- `?edit_component=1&component_id&codice_componente&variante&quantita&um&note_comp` (`:1325-1364`): get_or_create component (descrizione default "Aggiornato da distinta base il …"), update line fields, touch distinta. **No UI calls it** (dead endpoint).
- `?delete=1&id` (`:1367-1380`): soft delete `attiva=False`. Components kept; the `(codice_padre, variante)` remains reserved forever (cannot recreate – B-DB-1).
- `?uixList=1` (`:1383-1394`): partial list with `prefetch_related('componenti')`.
- `?export=1` (`:1397-1441`): xlsx `distinte_base_YYYYMMDD_HHMMSS.xlsx`, sheet "Distinte Base", header bold grey bordered: `Codice Padre, Variante, Descrizione, Codice Componente, Variante Componente, Quantità, UM, Note Componente`; one row per component (legacy `codice_componente`/`variante` text, raw `um` code), one empty-component row for BOMs without lines; ordered by codice_padre.
- Default: full page `tplDistinteBase.html` (context `codici_componente`).

#### Templates
- `tplDistinteBase.html`: sidebar form (hx-post -> `#list`) with hidden `distinta_id`; `editDistinta(id, codice, variante, descrizione, note)` fills form, switches button label to "Aggiorna Distinta" and header colour; `resetForm()`. Main `#list` loads `?uixList=1` on `load, refresh from:body`.
- `uixListaDistinteBase.html`: search box filters accordion items by `data-search-text` (codice padre, variante, descrizione and all component codes). Accordion per BOM: header `codice_padre . variante`, badge `N componenti`, created / modified timestamps (modified always shown because auto_now ≠ auto_now_add). Body: descrizione, note, admin buttons Modifica (JS) / Elimina (hx-get delete, confirm). Admin "Aggiungi Componente" inline form: codice (datalist `codiciComponentiDB` of component codes; `oninput` -> `aggiornaVarianti<ID>()` populates per-BOM variant datalist from a JS map `codiciVariantiMap[codice] = [varianti]`; auto-fill if exactly one variant), variante, quantità (default 1), UM select (EA/M hard-coded), note; button calls `aggiungiComponente(id)` -> jQuery `$.ajax GET` with `add_component=1…` then `location.reload()`. Components table: #, code (FK or legacy), descrizione, quantità, UM display, note, admin Elimina (hx-get delete_component, confirm).

#### Bugs / issues
- **B-DB-1** IntegrityError 500 on duplicate codice/variante; soft-deleted rows block re-creation.
- **B-DB-2** All component mutations via GET (CSRF-able), errors swallowed while reporting success.
- **B-DB-3** JS injection: `editDistinta('{{id}}', '{{ codice_padre }}', '{{ variante }}' …)` in an `onclick` attribute without `escapejs` for codice/variante (`uixListaDistinteBase.html:63`); HTML entities are decoded before JS runs.
- **B-DB-4** `posizione` race (`:1282`); no reordering UI.
- **B-DB-5** Server doesn't check `Geko Admin`.
- **B-DB-6** Export uses legacy text fields, not FK.
- **B-DB-7** N+1 (`componenti.count`, `componenti.all` evaluated multiple times, `componente` FK per row) and per-BOM generated JS functions.
- **B-DB-8** `edit_component` dead.
- **B-DB-9** No BOM explosion / requirement calculation / costing anywhere.

---

## 5. Anagrafica Componenti

### 5.1 `GET|POST /PRODUZIONE/vwAnagraficaComponenti/` – name `Anagrafica Componenti` (`:1456-1521`)

Permission: `Geko`.

- **POST** (upsert, `:1461-1494`): `codice` (required, stripped), `variante`, `descrizione`, `um` (default EA, not validated), `prezzo_medio` (float), `fornitore`, `note`.
  - `get_or_create(codice, variante or None, defaults={…, attivo True})`; if it existed, **overwrite** descrizione, um, prezzo_medio (blank -> None), fornitore, note (blank values wipe data).
  - Any exception -> `print` only, no user feedback. Empty codice -> silently ignored.
  - No redirect after POST (renders the page directly; F5 re-submits).
- **GET**: `search` -> icontains on codice OR variante OR descrizione OR fornitore; else all (active and inactive), ordered codice, variante. Context `componenti, search, list_um=LIST_UM`.
  - `?uixList=1` -> `uixListaComponenti.html`; else `tplAnagraficaComponenti.html`.
- `tplAnagraficaComponenti.html`: search form (GET), clear button, `#list` hx-get `…?uixList=1&search={{search}}` (**not URL-encoded**, B-AC-2), modal `#modalNuovoComponente` with classic POST form (codice*, variante, descrizione, um select from LIST_UM, prezzo_medio step 0.01, fornitore, note).
- `uixListaComponenti.html`: columns Codice Completo, Descrizione (truncatewords 10), UM display, Prezzo Medio (badge € 2 decimals), Fornitore, Data Creazione, Attivo, Azioni = link to Django admin `/admin/Prod/codicecomponente/<id>/change/` (new tab). Footer total count. **Editing/deactivating is only possible through Django admin** – the rewrite needs a proper edit form (incl. `attivo`).

Bugs: B-AC-1 upsert overwrites fields with blanks; errors swallowed. B-AC-2 search not urlencoded. B-AC-3 no PRG. B-AC-4 no pagination.

---

## 6. Anagrafica Codici Finiti

### 6.1 `GET|POST /PRODUZIONE/vwAnagraficaFiniti/` – name `Anagrafica Finiti` (`:1522-1611`)

Permission: `Geko`.

- **POST** (`:1527-1554`): `articolo_id`, `description`, `location_id`, `azione`.
  - `azione=='toggle_nuovo'`: flip `nuovo_articolo`, save(update_fields), success message "Articolo X impostato come Nuovo/Non nuovo".
  - else: `description` (blank -> None); `location_id` -> `Location.get(id)` (not found -> None; blank -> None); save(update_fields=['description','location']); message "Articolo X aggiornato".
  - Article not found -> error message. Messages are **not rendered** by this page (tplCore doesn't show messages) – they surface on the next page that renders messages.
  - No redirect after POST: page re-rendered; **F5 re-toggles** `nuovo_articolo`.
- **Filters** (GET or POST, POST hidden fields preserve them): `search` (icontains articolo OR description), `f_articolo` (icontains), `f_descrizione` (icontains), `f_area` (location_id exact; non-int -> 500), `f_um` (exact), `nuovo` (`all`|`1`|`0`). Ordered by articolo, `select_related('location')`. `aree` = all Locations ordered area, path code.
- Context: `articoli, search, filtro_*, filtro_nuovo, aree, list_um`.
- `?uixList=1` -> `uixListaAnagraficaFiniti.html`; else `tplAnagraficaFiniti.html` (header, "Nuovo Articolo" link to `/admin/Prod/anagrafica/add/`, `#list` hx-get with filters (not urlencoded)).
- `uixListaAnagraficaFiniti.html`: table with header filter row (inputs bound by `form="filtri-colonna"` GET form; funnel button applies, X resets). Columns: Articolo (bold + hidden per-row POST form `form-art-<id>` carrying filters), Descrizione (inline text input), Area (select of Locations "area - path.code"), UM display, Prezzo (badge), Prezzo ricalcolato (badge), Nuovo (badge + "Toggle" submit `azione=toggle_nuovo`), Azioni ("Salva" submit; "Admin" link). Footer total + "Gestisci in Admin" link.
- Prices/risorse/tabulato are read-only here (edited in admin or other modules).

---

## 7. DDT Ingresso

Menu: "Inserimento Manuale DDT" (`tplCore.html:98`). Import pages are reachable only from buttons on the manual-entry page.

### 7.1 `GET|POST /PRODUZIONE/inserimentoManualeDDT/` – name `inserimento_manuale_ddt` – `vwInserimentoManualeDDT` (`:478-616`)

Permission: `Geko`.

**GET** (`:583-616`): prefill params `ddt_id, numero_ddt, data_ddt, fornitore`. Context:
- `ddt_disponibili` = **all** DDTIngresso ordered `-data_elaborazione` (unbounded dropdown, each option evaluates `materiali.count` -> N+1);
- `ddt_materiali` = lines of `ddt_id` ordered `-data_ingresso` (if exists);
- `codici_componente` active; `list_um` = `[{value,label}]` from LIST_UM; echo of prefill params.
- Template `tplInserimentoManualeDDT.html`.

**POST** (`:484-581`) – one material line per submit:
Params: header `numero_ddt, data_ddt (YYYY-MM-DD), fornitore, ddt_id`; line `codice*, variante, descrizione, quantita*, unita_misura (default EA), prezzo_unitario, data_ingresso (YYYY-MM-DD, default today), note`; `continua_inserimento` (submit button value).
1. If `codice` or `quantita` empty -> error message, redirect to form.
2. Parse dates (`ValueError` -> message "Errore nei dati inseriti", redirect).
3. If `ddt_id`: `DDTIngresso.get(pk)` – header fields in POST are **ignored** (client makes them readonly). Else create `DDTIngresso(numero_ddt or None, data_ddt, fornitore or None, elaborato=True, utente=request.user)`.
4. Create `MaterialeIngresso(ddt, codice, variante or None, descrizione or None, quantita=float, unita_misura, prezzo_unitario=float or None, data_ingresso, verificato=True, note or None)`.
5. `CodiceComponente.get_or_create(codice, variante or None, defaults={descrizione, um=unita_misura, prezzo_medio=prezzo, fornitore})` – existing components are **not** updated (prezzo_medio is never recalculated as an average).
6. Success message `Materiale inserito: <codice> - Qta: <q> nel DDT <numero or id>`.
7. If `continua_inserimento`: redirect to `inserimento_manuale_ddt?ddt_id=…&numero_ddt=…&data_ddt=…&fornitore=…` (values not URL-encoded). Else `redirect('ricerca_materiali')` – **URL name does not exist** -> `NoReverseMatch` is caught by the generic `except Exception` -> user sees "Errore durante il salvataggio: Reverse for 'ricerca_materiali' not found" although the line **was saved** (**B-DDT-1**, `:568`).
- No transaction: `float(quantita)` fails *after* a new DDT header has been created -> orphan empty DDT (**B-DDT-2**).
- No inventory movement is generated.

**Template behaviour** (`tplInserimentoManualeDDT.html`):
- Messages rendered as dismissible alerts. Header buttons "Importa da File" / "Importa da XML".
- Select `ddt_select` "Seleziona DDT Esistente" (`-- Nuovo DDT --` + options "DDT <num|id> - <fornitore|N/A> (dd/mm/YYYY) - N materiali" with data-attributes). `onchange`: `caricaDDTEsistente()` copies id/numero/data/fornitore into the form and makes header fields readonly (reset when "Nuovo"), and htmx `hx-get` `materiali_ddt` -> `#materialiDDT`.
- Codice input with datalist `codiciComponentiDB` (options carry data-variante/descrizione/um/prezzo); `oninput` -> `aggiornaVariantiDDT()` (variant datalist from a JS array of all components; auto-fill if one) and `caricaDatiComponente()` (auto-fill descrizione, UM, prezzo when codice+variante match an option).
- Buttons: "Salva e Continua" (`continua_inserimento=true`), "Salva e Termina", "Annulla". Data ingresso defaults to today. Lines table of the selected DDT (Codice, Variante, Descrizione, Quantità, UM, Prezzo, Data Ingresso, Verificato badge).
- JS array built with `"{{ comp.descrizione }}"`: a newline in a description breaks the whole script (**B-DDT-8**).

### 7.2 `GET /PRODUZIONE/inserimentoManualeDDT/materiali/` – name `materiali_ddt` – `vwMaterialiDDT` (`:618-638`)
Param `ddt_select` (DDT id). Returns partial `uixMaterialiDDT.html` with `materiali` (ordered `-data_ingresso`) or info "Nessun materiale ancora inserito in questo DDT." Non-int id -> 500 (ValueError not caught).

### 7.3 CSV/TXT import

#### Helper `parse_ddt_import_file(file_content: str) -> (righe, scartate)` (`:641-729`)
- `csv.DictReader(delimiter=';', quotechar='"')`; logs header and row 2 at INFO.
- Column mapping (exact header names, Italian gestionale export):
  | CSV header | field |
  |---|---|
  | `Numero documento e appendice` | numero_ddt |
  | `Data` (`dd/mm/YYYY`) | data_ddt (invalid/empty -> **today**, silently) |
  | `Ragione sociale` | fornitore |
  | `Codice articolo` | codice (required) |
  | `Descrizione articolo/conto` | descrizione |
  | `Unita' di misura` | unita_misura (default `EA`; values like `NR` kept as-is) |
  | `Quantita'` (decimal comma) | quantita |
- Scarti (with row number, reason, short data): codice missing; quantity not parseable; quantity == 0. Negative quantities are **accepted** (unlike XML).
- Thousands separators (`1.000,5`) are not handled -> parse error -> row discarded.

#### `GET|POST /PRODUZIONE/importaDDTFile/` – name `importa_ddt_file` – `vwImportaDDTDaFile` (`:732-844`)
POST file field `file_ddt`:
1. Missing -> error. Extension must be `.txt`/`.csv` (case-insensitive) – no size or content-type check ("max 10MB" in UI is not enforced).
2. Decode **UTF-8 only** (Windows-1252 export -> UnicodeDecodeError -> generic error). UTF-8 BOM not stripped -> first header becomes `﻿"Numero documento…"` -> numero_ddt empty for every row (**B-DDT-4**).
3. Each scarto -> one `messages.warning` (unbounded).
4. No valid rows -> error.
5. Group rows by `(numero_ddt, fornitore, data_ddt)`.
6. Per group: `DDTIngresso.get_or_create(numero_ddt, fornitore, data_ddt, defaults={elaborato True, utente})`.
7. Per row: skip if a `MaterialeIngresso(ddt, codice, data_ingresso=data_ddt)` exists (**two lines with the same code in one DDT -> second silently dropped and not counted**, B-DDT-5); else create with `verificato=False`, no variante, no price; then `CodiceComponente.get_or_create(codice=…, defaults={descrizione, um, fornitore})` – lookup **without variante** -> `MultipleObjectsReturned` if the code has several variants -> whole import aborts midway (B-DDT-6). `um` from file (e.g. `NR`) is stored in `CodiceComponente.um` (choices EA/M, max_length 3 -> values >3 chars raise DataError).
8. Success message "Importazione completata: X DDT creati, Y materiali importati"; redirect to manual entry page. Any exception -> logged + error message + redirect back. **No transaction** – partial imports persist.
- Template `tplImportaDDTFile.html`: instructions, file input (`accept=.txt,.csv`), example:
  ```
  "Numero documento e appendice";"Data";"Ragione sociale";"Codice articolo";"Descrizione articolo/conto";"Unita' di misura";"Quantita'"
  "33";09/01/2026;"GEKO S.R.L.";"18140075BR";"Capsula Braille Puls. Quadrato DMG 2016";"NR";10,000
  ```

### 7.4 XML import (proprietary gestionale format, not FatturaPA)

#### Helper `parse_ddt_xml_file(file_content: str) -> (righe, scartate)` (`:847-1041`)
Parser: `xml.etree.ElementTree.fromstring` (stdlib; not hardened – use `defusedxml` in rewrite).
Structure and mapping:
```
<Docs>                                  root must be "Docs" else single scarto, return
  <Doc>                                 iterated with findall('Doc') (direct children only)
    <DocType>5</DocType>                read but IGNORED (no filtering by document type)
    <DocNumber>5</DocNumber>            -> numero_ddt (missing -> "XML_<doc index>")
    <DocDate>2026-01-07</DocDate>       -> data_ddt (YYYY-MM-DD; missing/invalid -> today)
    <Anag><Name>…</Name>…</Anag>        -> fornitore (Address, VatNumber ignored)
    <DocRows>                           missing -> scarto "Nessuna riga trovata nel documento"
      <DocRow>
        <RowType>1</RowType>            IGNORED (descriptive/comment rows not filtered except by missing Product)
        <Product><Code>…</Code></Product> -> codice (missing Product/Code -> scarto)
        <Description>…</Description>    -> descrizione (optional)
        <Quantity>100</Quantity>        -> quantita (float with '.', missing/invalid/<=0 -> scarto)
        <Price>5.50</Price>             -> prezzo_unitario (optional; invalid e.g. "5,50" -> None silently)
```
`unita_misura` is always `'EA'`. Exceptions per row/document are captured as scarti and logged.

#### `GET|POST /PRODUZIONE/importaDDTXML/` – name `importa_ddt_xml` – `vwImportaDDTDaXML` (`:1044-1190`)
POST file field `file_ddt_xml`:
1. Missing -> error; extension must be `.xml`.
2. Decode UTF-8, fallback windows-1252 (the sample declares `encoding="windows-1252"`).
3. Show at most 10 scarti as warnings + "… e altre N righe scartate".
4. Group by `(numero_ddt, fornitore, data_ddt)`; `DDTIngresso.get_or_create(...)` (counts created vs "aggiornati" = already existing).
5. Per row: skip if `MaterialeIngresso(ddt, codice, data_ingresso)` exists (counted as "già esistenti"); else create with `prezzo_unitario`, `verificato=True`; `CodiceComponente.get_or_create(codice, defaults={descrizione, um, fornitore, prezzo_medio})` (no variante -> same MultipleObjectsReturned risk); if existing and its descrizione empty, fill it.
6. Message "Importazione completata: X DDT creati, Y DDT aggiornati, Z materiali importati, W materiali già esistenti"; redirect to manual entry. Exceptions -> logged with traceback, error message, redirect back. No transaction.
- Template `tplImportaDDTXML.html`: instructions + sample XML + notes ("stesso numero, fornitore e data -> aggiunti solo i nuovi materiali", "materiali duplicati non reinseriti", "quantità zero/negative scartate").

### 7.5 DDT bugs / issues summary
- **B-DDT-1** Redirect to non-existent `ricerca_materiali` -> false error after successful save (`:568`).
- **B-DDT-2** No `transaction.atomic` in any DDT write path (`:519-561`, `:781-830`, `:1104-1161`): orphan headers, partial imports.
- **B-DDT-3** Encoding: CSV only UTF-8, no BOM handling.
- **B-DDT-4** Dedup key `(ddt, codice, data_ingresso)` collapses legitimate repeated lines; quantities lost silently.
- **B-DDT-5** `CodiceComponente.get_or_create(codice=…)` without variante (`:819`, `:1145`) -> MultipleObjectsReturned aborts the import.
- **B-DDT-6** Field-length overflow (descrizione 200, fornitore 100, numero 50, um 3 on CodiceComponente) -> DataError on PostgreSQL, aborts import.
- **B-DDT-7** Concurrency: two simultaneous imports of the same file can create duplicate DDT headers (no unique constraint on `(numero_ddt, fornitore, data_ddt)`; get_or_create race) and duplicate lines.
- **B-DDT-8** JS breaks on descriptions with newlines/quotes (`tplInserimentoManualeDDT.html:263-273`).
- **B-DDT-9** DDT CSV import marks lines `verificato=False` but there is no screen to verify them (OCR verification page missing).
- **B-DDT-10** Unbounded dropdown of all DDTs and component datalists on every page load.
- **B-DDT-11** XML: `DocType` and `RowType` ignored – any document type (invoices, outgoing DDT) would be imported as incoming; uses stdlib ET (XXE is not possible with stdlib, but entity-expansion DoS hardening depends on expat version) – use defusedxml.
- **B-DDT-12** No upload size limit; whole file read in memory.
- **B-DDT-13** Incoming material never updates inventory nor closes `RichiesteMateriali` (natural integration point for the rewrite: on receipt of code X, propose closing open requests for X).

---

## 8. OCR pipeline, DDT verification, debug OCR, ricerca materiali (NOT IMPLEMENTED – orphan templates)

Findings:
- No view functions, URL patterns or OCR library exist in this branch. `grep` for pytesseract/easyocr/tesseract/cv2 returns nothing; installed packages contain none. The git checkout has a single commit (`9ec8759`), so history of the removed implementation is unavailable.
- URL names referenced but unregistered: `carica_ddt`, `verifica_ddt` (arg ddt.id), `debug_ocr` (arg ddt.id), `ricerca_materiali`. Rendering any of these templates would raise `NoReverseMatch`.
- Remnant model fields: `DDTIngresso.foto_originale` (upload `ddt_ingresso/`), `foto_binaria` (`ddt_ingresso/binarie/`, readonly in admin `Prod/admin.py:197-198` – suggests a binarised/pre-processed image produced by an OpenCV-like step), `elaborato`, `errore_elaborazione`.
- `tplDebugOCR.html` uses `{% block content %}` while tplCore defines `contents` (it would render empty) and Bootstrap-4 classes (`badge-success`) – it was never functional with the current base template.

Intended behaviour reconstructed from templates (for the rewrite to implement or drop):

| Page | Expected URL/context | Behaviour |
|---|---|---|
| `tplCaricaDDT.html` (carica_ddt) | context `pageTitle`, `ddt_recenti` (list of DDTIngresso), messages | POST multipart `foto_ddt` (image; mobile camera via `capture="environment"` toggle). Server: save `foto_originale`, preprocess -> `foto_binaria`, OCR, extract numero/data/fornitore and lines (codice, quantità, descrizione) into `MaterialeIngresso(verificato=False)`, set `elaborato` or `errore_elaborazione`. UI shows spinner + estimated time (10-30 s by file size). Sidebar "DDT Recenti" with badge Elaborato/In elaborazione and "Verifica" link; button "Ricerca Materiali". |
| `tplVerificaDDT.html` (verifica_ddt/<id>) | `ddt`, `materiali` | One POST form: header numero/data/fornitore; table of lines with inputs `codice_<id>`, `variante_<id>`, `descrizione_<id>`, `quantita_<id>`, `um_<id>` (options PZ/M/KG/LT – inconsistent with LIST_UM EA/M), `verificato_<id>` checkbox; JS "Aggiungi Materiale" adds rows named `*_new_<timestamp>`; "remove" deletes the row client-side only (server must treat missing ids as deletions). Shows original photo. Links back and to Debug OCR. |
| `tplDebugOCR.html` (debug_ocr/<id>) | `ddt`, `materiali`, `testo_ocr` (raw OCR text) | Displays header info, image, extracted lines, raw OCR text; POST `rielabora` re-runs extraction ("algoritmo migliorato"). Tips for better photos (300 DPI, no shadows, horizontal text). |
| `tplRicercaMateriali.html` (ricerca_materiali) | `materiali` (MaterialeIngresso with ddt), GET filters | Filters: `codice` (codice or variante contains), `data_da`, `data_a` (on data_ingresso), `fornitore` (ddt.fornitore contains), `solo_verificati` checkbox; `export=1` -> Excel of results. Columns: Codice, Variante, Descrizione, Quantità, UM, Data Ingresso, DDT number, Fornitore, Stato (Verificato / Da verificare), Azioni (edit -> verifica_ddt, view photo). |

Recommendation: the rewrite should at minimum implement **Ricerca materiali** (needed as the "Salva e Termina" target) and a **Verifica DDT** screen (needed for CSV-imported lines with `verificato=False`). OCR is optional/new scope; if implemented, run it asynchronously (job queue) – the old UI expected 10-30 s synchronous requests.

---

## 9. Email infrastructure

### 9.1 `Prod/mailing.py`
- `extractBodyText(email_message)` (`:9-20`): concatenates decoded `text/plain` parts. **Unused anywhere** (dead code).
- `sendEmail(subject, message, fromAddress, toAddress, replyAddress, attachment_path=None, filename=None, ccAddress=None)` (`:22-71`):
  1. Reads `Configuration` keys `EMAIL_METODO_USER` (SMTP user), `EMAIL_METODO_PASSWORD` (SMTP password, **stored in plain text in the DB** – value not reproduced here), `EMAIL_SERVER` (host). Port hard-coded **587**.
  2. Builds `MIMEMultipart`: From, To (string as given, may be comma-separated), optional Cc, Subject, `reply-to` header; body `MIMEText(message, 'html')`.
  3. Optional attachment read from local `attachment_path`, sent as `application/octet-stream`, base64, `Content-Disposition: attachment; filename=<filename>` (filename not quoted/encoded).
  4. `smtplib.SMTP(server, 587)` + `starttls()` + `login` + `send_message(msg, to_addrs=[split To + Cc on ','])`; redundant `quit()` inside `with`.
  5. Inserts `SentEmail(success=True, …)`.
  - Errors propagate to caller (no try/except): failures are never logged in `SentEmail` (field `error_message` unused). Synchronous inside HTTP request. One SMTP connection per message. No timeout set.
  - Callers: `inventario.py` (richiesta materiali), `ddt.py:53` (File Tecnico DDT with attachment), `orders.py:1029,1036` (note notifications), `pianificazione.py:1213`, `etichette.py` (import).
  - `SentEmail.to_address` is an `EmailField` (254) storing comma-separated lists.

### 9.2 `Prod/management/commands/readMail.py` (`python manage.py readMail`)
Purpose: order intake (not DDT) – downloads zip attachments from the orders mailbox.
1. Dirs: `Metodo/media/mailAttachments` (created if missing), `Metodo/media/attachmentExtract` (not created – `os.listdir` fails if missing).
2. **Hard-coded IMAP host, username and password at `readMail.py:22-23`** (secret in source – must be moved to env/secret store and rotated). `use_uid=True`, folder INBOX.
3. Loads all processed UIDs from `ProcessedEmail`.
4. `search(['NOT DELETED', 'HAS ATTACHMENT'])` (`HAS ATTACHMENT` is not a standard IMAP search key – server-specific), then **fetches full RFC822 of every matching message every run** (no date bound; heavy).
5. For each message whose UID is not processed: for each part with Content-Disposition and filename ending `.zip`: write to `mailAttachments/<filename>` (**path traversal**: filename from email used unsanitised), `ZipFile.extractall(extract_dir)` (**zip-slip**), then delete all `.pdf` in extract dir, log, `ProcessedEmail.create(message_id=uid, subject)`.
   - Messages without zip are never marked processed -> re-downloaded forever.
   - A message with 2 zips -> second `create` hits unique constraint -> caught as "Error saving attachment".
   - UID used as identity across UIDVALIDITY changes (unsafe); shared `ProcessedEmail` table with the `orders.py` fetchers (`scFetchOrderByMail`, `:1536`, `:1640`) which read IMAP host/credentials from `Configuration` instead.
6. Not invoked by any code (`call_command` absent); Console uses `orders.scFetchOrderByMail` instead. Likely superseded/dead – keep only if a cron calls it externally.

---

## 10. Cross-cutting issues (rewrite checklist)

1. **Authorization**: single group check `Geko`; `Geko Admin` only cosmetic. Define explicit permissions: `magazzino.view`, `magazzino.inventario.add`, `richieste.create`, `richieste.manage` (receive/delete/mail), `distinte.edit`, `componenti.edit`, `ddt.import`.
2. **State-changing GET endpoints** (richieste delete/ricezione/mail confirm, BOM add/edit/delete component, BOM delete) -> convert to POST/DELETE with CSRF.
3. **No transactions** anywhere in the area; imports and multi-row updates must be atomic.
4. **Uniqueness**: add DB constraints (CodiceComponente with non-null variante `''` or partial indexes; open RichiesteMateriali; DDTIngresso natural key; ComponenteDistinta (distinta, componente)).
5. **Normalisation**: uppercase/trim codes consistently (only richieste do it today).
6. **Legacy dual fields** (`articolo/variante` on Inventario, `codice_componente/variante` on ComponenteDistinta) – migrate to FK only (see `migrate_to_codice_componente` command which maps `(codice, variante or '')` -> CodiceComponente, creating missing ones with descrizione "Migrato automaticamente", in one atomic transaction with dry-run savepoint).
7. **XSS/JS injection**: `|safe` mail preview, unescaped values in `onclick`/inline JS arrays.
8. **Errors swallowed** (`except: pass`, `print`) and false success messages.
9. **Timezones**: naive `datetime.now()` with `USE_TZ=True`.
10. **Performance**: unpaginated lists, N+1 queries, whole master data shipped to every page as datalists/JS arrays.
11. **Secrets**: IMAP credentials hard-coded in `readMail.py:22-23`; SMTP password and addresses in `Configuration` table (`EMAIL_METODO_PASSWORD`, `EMAIL_METODO_USER`, `EMAIL_SERVER`, `EMAIL_METODO_ADDRESS`, `EMAIL_MAGAZZINO_GEKO`, `EMAIL_MAGAZZINO_DMG_1`, `EMAIL_MAGAZZINO_DMG_2`). Move to environment/secret manager; keep only non-secret recipient lists configurable.
12. **Email**: send asynchronously, record failures in `SentEmail`, set flags only after success, escape user content in HTML bodies.

## 11. Endpoint index

| URL (`/PRODUZIONE/…`) | Name | View | Methods | Output |
|---|---|---|---|---|
| `vwInventario/` | Inventario | vwInventario | GET (+dead POST) | tplInventario / uixListaInventario / xlsx (`export=1|2|3`, `year`) |
| `vwAddInventario/` | AddInventario | vwAddInventario | POST (GET not blocked) | uixListaInventario |
| `vwRichiestaMateriale/` | Richiesta Materiale | vwRichiestaMateriale | GET (`page`, `uixList`, `export`, `delete`, `ricezione`, `mail`+`ids|id|all`,`type`,`preview|confirm`,`reparto`), POST (new request) | tplRichiestaMaterialiPls / Elett / uixListaMateriale / uxModalPreviewMail / text+HX-Refresh / xlsx |
| `vwDistinteBase/` | Distinte Base | vwDistinteBase | GET (`uixList`, `export`, `add_component`, `edit_component`, `delete_component`, `delete`), POST (create/update header) | tplDistinteBase / uixListaDistinteBase / text+HX-Refresh / xlsx |
| `vwAnagraficaComponenti/` | Anagrafica Componenti | vwAnagraficaComponenti | GET (`search`, `uixList`), POST (upsert) | tplAnagraficaComponenti / uixListaComponenti |
| `vwAnagraficaFiniti/` | Anagrafica Finiti | vwAnagraficaFiniti | GET (filters, `uixList`), POST (edit / toggle_nuovo) | tplAnagraficaFiniti / uixListaAnagraficaFiniti |
| `inserimentoManualeDDT/` | inserimento_manuale_ddt | vwInserimentoManualeDDT | GET (prefill), POST (add line) | tplInserimentoManualeDDT / redirect |
| `inserimentoManualeDDT/materiali/` | materiali_ddt | vwMaterialiDDT | GET (`ddt_select`) | uixMaterialiDDT |
| `importaDDTFile/` | importa_ddt_file | vwImportaDDTDaFile | GET, POST (`file_ddt`) | tplImportaDDTFile / redirect |
| `importaDDTXML/` | importa_ddt_xml | vwImportaDDTDaXML | GET, POST (`file_ddt_xml`) | tplImportaDDTXML / redirect |
| (missing) | carica_ddt, verifica_ddt, debug_ocr, ricerca_materiali | – | – | orphan templates (§8) |
| CLI | – | readMail, elimina_duplicati_richieste, normalizza_codici_richieste, migrate_to_codice_componente | – | – |
