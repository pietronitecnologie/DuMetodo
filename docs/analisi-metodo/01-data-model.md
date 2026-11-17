# 01 — Data model, configuration and cross-app coupling (Prod / "Produzione")

Source analysed: `/home/user/metodo-branch` (branch `Metodo`), Django 4.2 project `Metodo`.
Scope: `Prod/models.py`, `Prod/admin.py`, `Prod/migrations/`, `Prod/migrateDb.py`, `Prod/management/commands/*`,
`Prod/apps.py`, `Prod/updater.py`, `Prod/maintenance.py`, `Metodo/settings.py`, `Metodo/urls.py`, `AuditTrail/*`,
`Plan/models.py`, `Reports/models.py`, and every cross-app import to/from Prod.
All citations are `file:line` relative to the repo root.

> Secrets: several hard-coded credentials exist in the code. Their **locations** are listed in §9; values are deliberately not reproduced.

---

## 0. Key facts for the rewrite (TL;DR)

1. **Order identity is a composite natural key, not a FK.** An *ordine/commessa* is identified by `(serie, rif_ordine, appendice, anno)`. `Linea` (order row) has **no FK to `Ordine`**; it repeats the 4 key fields and every join is done by string matching (`Prod/views.py:69-77` says so explicitly). Same for `DocumentiTrasporto`, `FileOrdini`, `Errori`. Only `Tracciabilita.rif_ordine` and `StoricoScadenza.ordine/linea` are real FKs.
2. **Money stored as text.** `Linea.prezzo_ea` and all `sconto_*` are `CharField` holding Italian-format decimals ("12,50"); every computation does `Cast(Replace(prezzo_ea, ",", "."), FloatField())` (`Prod/models.py:221`, `Reports/produzione.py:43`) or `float(x.replace(",", "."))` (`Prod/models.py:106`, `Prod/maintenance.py:31`).
3. **No uniqueness on order key.** `Ordine` has an index on `(rif_ordine, serie, appendice, anno)` but **no unique constraint** (`Prod/models.py:247`). The import routine (`Prod/orders.py:1390`) emulates uniqueness in Python.
4. **Migrations are not in git.** `Prod/migrations/` (and Plan/Reports/Timbratura) are in `.gitignore` (`.gitignore:1-5`); the only committed file `Prod/migrations/0001_initial.py` (2024-03-24) is badly out of sync with `models.py` (e.g. `Ordine.rif_ordine unique=True`, `Linea.prezzo_ea FloatField`, `Ordine.linee` M2M — none of which exist now). The **live production schema is unknown from the repo**; DuMetodo must derive it via `inspectdb` / `pg_dump --schema-only` on the real DB before data migration.
5. **Single PostgreSQL DB, no routers** (`Metodo/settings.py:89-100`); Prod shares tables with Plan, Reports, Tickets, Timbratura, AuditTrail, auth.
6. **Hard dependencies on Plan**: `Location` (= *reparto*), `Asset`, `Coding`, `Resource`, `Annunci`, `AllocazionePersonale`. On Reports: `gekoSAS_orders` (legacy import only). On Tickets: `Ticket` (order modal). Reverse: Reports (produzione/dashboard/report/views), AuditTrail, Tickets, Plan read Prod models.
7. **Key/value `Configuration` table** is the system's settings store, including SMTP/IMAP **passwords in plain text** (§8).
8. Background jobs run via **APScheduler inside the web process** (`Prod/apps.py:8-10`), started in `AppConfig.ready()` with no single-instance guard.
9. Authorization = three Django **groups** checked by name: `Geko`, `Geko Admin`, `Customer` (+ `GekoCapRep` in templates only) (§5).
10. AuditTrail logs only `Ordine` and `Linea` via signals; the request-capturing middleware is **not installed**, so `user/ip` are never filled, and its URLs are **not routed** (§7).

---

## 1. Prod models

All models use `BigAutoField` PK `id` (`Metodo/settings.py:150`, `Prod/apps.py:5`). Labels are wrapped in `gettext_lazy`.
Module-level imports in `Prod/models.py:1-8` include unused `enum.unique`, `django.forms.BooleanField/CharField`, `datetime`.

### 1.1 Choice lists (`Prod/models.py:10-49`, `616-623`)

| Constant | Values (code → label) | Used by |
|---|---|---|
| `LIST_STATUS` (`:10`) | `INS` Inserito · `LAV` Lavorato con Mancanze · `CON` Consegnato · `SPE` In spedizione · `SPP` Evasione Parziale · `ANN` Annullato | `Ordine.stato` |
| `LIST_TEST` (`:19`) | `CMP` Campione · `100` 100% · `MIS` Misura | `Test.tipo` |
| `LIST_UM` (`:25`) | `EA` Pezzi · `M` Metri | `Anagrafica.um`, `CodiceComponente.um`, `ComponenteDistinta.um` |
| `LIST_WO_STATUS` (`:30`) | `OPN` Aperto · `CLS` Chiuso | `WorkOrderTraccia.stato` |
| `LIST_URGENZA` (`:35`) | `NRM` Normale · `ALT` Alta · `24` 24 Ore | `Ordine.urgenza` |
| `LIST_TIPO` (`:41`) | `ASM` Assemblaggio · `PM` Project Management · `CMS` Commissioning · `SRV` Servizio · `ING` Ingegneria · `TRA` Trasporto · `PUL` Pulizia | `Ordine.tipo` |
| `LIST_ESITO` (`:616`) | `POS` Positivo · `NEG` Negativo | `DisplayTest.esito` |
| `LIST_PRODOTTI_DISPLAY` (`:621`) | `RAFFAELLO28` Raffaello2.8 | `DisplayTest.prodotto` |

State values written by code but **not in `LIST_STATUS`**: `BLC` and `Lav` (lower-case typo) in `Prod/migrateDb.py:92-94`; `migrations/0001_initial.py:64` also had `RIT`, `BLC`. A rewrite must decide whether these legacy values exist in the live data.

### 1.2 `Ordine` — order / *commessa* header (`Prod/models.py:164-248`)

Business meaning: one customer order (*commessa*) imported from the customer's ERP (GekoSAS/DMG file by mail, XML, or manual entry). Displayed as `serie.rif_ordine[.appendice]-anno`. Appendice `0`/empty = base order; `R`/`T` (and others) = order "appendix" variants created by the customer ERP (`Prod/orders.py:1413`).

| Field | Type | Null/blank | Default / other | Notes |
|---|---|---|---|---|
| `data_consegna` | DateField | null, blank | — | actual delivery date (set when DDT issued / sync) |
| `data_inserimento` | DateTimeField | — | `auto_now_add` | creation timestamp |
| `data_scadenza` | DateField | null, blank | — | due date; history tracked in `StoricoScadenza` |
| `stato` | CharField(50) | — | choices `LIST_STATUS`, default `INS` | indexed |
| `urgenza` | CharField(50) | — | choices `LIST_URGENZA`, default `NRM` | |
| `tipo` | CharField(50) | — | choices `LIST_TIPO`, default `ASM` | |
| `location` | FK `Plan.Location` | null, blank | **on_delete=DO_NOTHING**; verbose "Reparto" | department responsible |
| `note` | M2M `Prod.Note` | blank | — | free notes per order (user + text) |
| `rif_ordine` | CharField(50) | **required** | — | order number, typically zero-padded to 7 digits (`zfill(7)` in `Prod/traccia.py:164,633`) |
| `anno` | CharField(20) | null, blank | — | year **as string** |
| `serie` | CharField(20) | null, blank | — | ERP numbering series |
| `appendice` | CharField(20) | null, blank | — | `'0'`, `''`, `None`, `R`, `T`... all coexist |

Meta (`:241-248`): indexes `idx_ordine_serie(serie)`, `idx_ordine_stato(stato)`, `idx_ordine_dts(data_scadenza)`, `idx_ordine_rif(rif_ordine)`, `idx_ordine_key(rif_ordine, serie, appendice, anno)`. **No unique constraint.**

Methods:
- `__str__` (`:178-182`): `serie.rif_ordine.appendice-anno` if `appendice != '0'`, else `serie.rif_ordine-anno`. (Note: `None`/`''` appendice prints as `.None`/`.` — inconsistent.)
- `get_absolute_url` → `reverse("Ordine_detail")` — **URL name does not exist** (dead).
- `tempo_produzione_teorico(articoli_filtro=None)` (`:187-239`): theoretical production hours = Σ(`qta` × float(`prezzo_ea`)) over the order's `Linea`s (matched by the 4-field key, excluding `annullata`/`duplicata`, optionally filtered by `articolo__in`) ÷ `Configuration["PREZZO_RESA_ORARIA"]` (euro of value produced per hour). Rounded to 2 decimals; returns 0 on missing config / parse error / zero. **This "value ÷ hourly yield = hours" formula is the core planning metric** used in Prod/pianificazione, Prod/allocazione and Reports.

Domain notes: `anno` is a string while `Linea.anno` is too, but Reports filters by `data_scadenza__year` etc. Reverse relations: `storico_scadenze` (StoricoScadenza), `tracciabilita_set`.

### 1.3 `Linea` — order row (`Prod/models.py:72-116`)

Business meaning: one row of a customer order (articolo × qta × prezzo). Rows are re-imported each time the customer re-sends the order; old rows get `annullata=True`, conflicting row numbers get `duplicata=True` (`Prod/orders.py:1443-1464`). Billing (*fatturazione*), planning hours, DDT generation all derive from rows.

| Field | Type | Null/blank | Default | Notes |
|---|---|---|---|---|
| `data_scadenza` | DateField | null, blank | | row due date (normally = order's) |
| `data_inserimento` | DateTimeField | | `auto_now_add` | used to pick "latest import" |
| `data_consegna` | DateField | null, blank | | set to today when DDT/bolla emitted (`Prod/orders.py:1970`) |
| `rif_ordine` | CharField(50) | null, blank | | **order key part, no FK** |
| `anno` | CharField(20) | null, blank | | key part |
| `appendice` | CharField(20) | null, blank | | key part |
| `articolo` | CharField(50) | required | | finished-product code; joined by string to `Anagrafica.articolo` |
| `qta` | FloatField | required | | quantity (pieces or metres per `Anagrafica.um`) |
| `prezzo_ea` | **CharField(10)** | null, blank | | unit price as text with comma decimal — **wrong type** |
| `variante` | CharField(20) | null, blank | | product variant; sometimes contains `_` stripped on export (`Prod/orders.py:1944`) |
| `sconto_1`…`sconto_5`, `sconto_uni` | **CharField(20)** | null, blank | | discounts as text; only `sconto_uni` is written by importer |
| `magazzino` | CharField(20) | null, blank | | warehouse ref (from GekoSAS only) |
| `cliente` | CharField(20) | null, blank | | customer code / "tipo doc. riferimento cliente" |
| `serie` | CharField(20) | null, blank | | key part |
| `n_riga` | CharField(15) | null, blank | | row number **as string** (ordering by it is lexicographic) |
| `evasione_parziale` | BooleanField | null, blank | | row selected for partial shipment (`SPP`) |
| `emessa_bolla` | BooleanField | null, blank | | DDT (bolla) already emitted → delivered |
| `blocca_linea` | BooleanField | null, blank | | row blocked (missing material etc.) |
| `duplicata` | BooleanField | null, blank | | superseded duplicate row |
| `annullata` | BooleanField | null, blank | | cancelled / superseded by re-import |
| `scorporato` | BooleanField | null, blank | `False` | row "split off" into another order (`dividiLinea`, `vwScorporazioneMassa`); exported separately for billing (`Reports/produzione.py:1496`) |

Nullable booleans (tri-state) force every query to use `~Q(annullata=True)` instead of `annullata=False`.

Meta (`:110-116`): indexes `idx_linea_ordine(rif_ordine, serie, appendice, anno)`, `idx_linea_dts(data_scadenza)`, `idx_linea_art(articolo)`, `idx_linea_flags(annullata, duplicata, emessa_bolla)`.

Methods: `__str__` = `articolo: qta`; `get_absolute_url` → `"Linea_detail"` (**undefined URL**); `prezzo_totale()` (`:105-108`) = float(prezzo_ea with comma→dot) × qta — raises on `None`/garbage.

### 1.4 `DocumentiTrasporto` — outgoing DDT rows / *file tecnico* (`Prod/models.py:52-70`)

Business meaning: one row per `Linea` shipped. Created when an order is consigned (`vwConsegna` `Prod/orders.py:1957`, `vwConsegnaSingolo` `:2381`) **without** a DDT number; later the "Genera File Tecnico" page (`Prod/ddt.py:30-66`) assigns `no_ddt` (typed by the user), `data_ddt=today`, `inviato=True`, writes a `@`-separated text file and emails it to `EMAIL_FIETECNICO_ADDRESS` ("Inviato a DMG"). `no_ddt` restarts yearly, so lookups must pair it with `data_ddt__year` (`Prod/etichette.py:111-119`). Also used for label reprint and Reports productivity/DDT analysis.

| Field | Type | Null/blank | Default | Notes |
|---|---|---|---|---|
| `no_ddt` | CharField(20) | null, blank | | DDT number (label typo "Numero dtt") |
| `data_ddt` | DateField | null, blank | | |
| `data_inserimento` | DateField | | `auto_now_add` | **DateField** here vs DateTimeField elsewhere |
| `rif_ordine`, `anno`, `appendice`, `serie` | CharField(50/20/20/20) | null, blank | | copy of order key, no FK |
| `articolo` | CharField(50) | required | | |
| `qta` | FloatField | required | | |
| `variante` | CharField(20) | null, blank | | |
| `cliente` | CharField(20) | null, blank | | |
| `n_riga` | CharField(15) | null, blank | | |
| `inviato` | BooleanField | | `False` | sent to DMG |

No Meta, no indexes (filtered by `inviato`, `no_ddt`, `data_ddt`). No FK to `Linea` → no way to trace back except by key match.
`__str__` (`:66-67`): `self.rif_ordine + ": " + self.articolo` → **TypeError if `rif_ordine` is None**. `get_absolute_url` → `"ddt_detail"` (undefined).

### 1.5 `Configuration` — key/value settings (`Prod/models.py:118-129`)

| Field | Type | Notes |
|---|---|---|
| `chiave` | CharField(50) | **not unique**, not indexed — `.get(chiave=...)` will raise `MultipleObjectsReturned` if duplicated |
| `valore` | CharField(50) | everything is a string (numbers with comma decimals, e-mail lists, passwords, counters). 50 chars limits e-mail lists |

Meta: verbose names only. `get_absolute_url` → `"Configuration_detail"` (undefined). Full key catalogue in §8.

### 1.6 `Stored_Item` — serialised physical item (`Prod/models.py:131-141`)

Business meaning: a physical traced unit (battery, cable reel…) identified by a `Plan.Coding.internalCode` (e.g. battery code generated as `radix + 6 hex chars`, `Prod/traccia.py:596-603`), optionally tied to the `Linea` it was produced for.

| Field | Type | Notes |
|---|---|---|
| `codice` | FK `Plan.Coding` | null, blank, **DO_NOTHING** |
| `linea` | FK `Prod.Linea` | null, blank, **DO_NOTHING** |
| `data_in` | DateTimeField | null, blank — entry date |
| `data_out` | DateTimeField | null, blank — exit date (never written in Prod code) |

`__str__` = `self.codice.internalCode` → **crashes when `codice` is None**. `get_absolute_url` undefined name. Class name breaks PEP8 (`Stored_Item`; admin class misspelled `SotoredItemAdmin`, `Prod/admin.py:17`).

### 1.7 `Tracciabilita` — traceability event (`Prod/models.py:250-266`)

Business meaning: generic key/value trace record: "item X / order Y had property `chiave`=`valore` at time `data` in department `location` on machine `asset`". Used for battery tracing, cable length measurement from test benches (`insertTracciaCavi`, `Prod/traccia.py:605-662`: `chiave`=cable product code, `valore`=measured length), lot/bag registration per order (`chiave`="LOTTO"/"BUSTA", `Prod/traccia.py:757-759`).

| Field | Type | Null/blank | on_delete |
|---|---|---|---|
| `stored_item` | FK `Prod.Stored_Item` | null, blank | **DO_NOTHING** |
| `rif_ordine` | FK `Prod.Ordine` (named like a string field!) | null, blank | SET_NULL |
| `data_inserimento` | DateTimeField `auto_now=True` (label "Date") — **updated on every save**, not insertion time | | |
| `data` | DateTimeField | null, blank | event time |
| `chiave` | CharField(50) | required | |
| `valore` | CharField(50) | required (but code saves `""`) | |
| `location` | FK `Plan.Location` | **null=True, blank=False** | **DO_NOTHING** |
| `asset` | FK `Plan.Asset` | null, blank | **DO_NOTHING** |

No indexes although filtered by `rif_ordine__rif_ordine__iregex`, `chiave`, `data` (`Prod/orders.py:1058`, `Prod/console.py:124-130`). `get_absolute_url` → undefined.

### 1.8 `WorkOrderTraccia` (`Prod/models.py:292-304`)

Business meaning: maintenance work-order on a traced battery whose last trace is older than 115 days (`scCreateWorkOrders`, `Prod/traccia.py:573-585`, **job currently disabled** in `Prod/updater.py:14`). Closed via unauthenticated GET `wotHandle` (`Prod/traccia.py:587-594`). Listed on Plan dashboard (`Plan/views.py:8,24`).

| Field | Type | Notes |
|---|---|---|
| `traccia` | FK `Prod.Tracciabilita` | null, **DO_NOTHING** |
| `stato` | CharField(50) | choices `LIST_WO_STATUS`, default `OPN`, null=True |
| `risorsa` | FK `Plan.Resource` | null, **DO_NOTHING**; never written by code |

`__str__` chains `traccia.stored_item.codice.internalCode` — crashes on any null. Verbose plural "WorkOrderTraccias".

### 1.9 `Note` (`Prod/models.py:143-152`)

Order note. `user` FK `auth.User` **CASCADE** (deleting a user deletes their notes on orders), `data` DateTimeField **`auto_now=True`** (edit time, not creation), `testo` TextField. Linked to orders via `Ordine.note` M2M; created in `Prod/orders.py:1014`, deleted in `vwDeleteNote` (`:2303`). Tickets copy note texts into new tickets (`Tickets/views.py:225`). `__str__` uses `testo[:15]`.

### 1.10 `File` (`Prod/models.py:154-162`) — **dead model**

`nome` CharField(50), `file` FileField(`upload_to=None`). Registered in admin only; no code uses it.

### 1.11 `FileOrdini` — photos/files attached to an order (`Prod/models.py:375-384`)

`file` FileField(`upload_to='file/'`), `rif_ordine` CharField(50) required, `anno`/`serie`/`appendice` CharField(20) null/blank, `data_inserimento` DateTimeField auto_now_add. Created by `vwCreaPhoto` (`Prod/files.py:33`); shown in order modal by **fuzzy match** (`rif_ordine__contains`, `anno__contains`, `serie__contains=serie.lstrip('0')`, `appendice__iregex`, `Prod/orders.py:1088`). No FK to Ordine; no index.

### 1.12 `Reclami` — customer complaints / non-conformities (`Prod/models.py:268-275`)

`codice` CharField(50) "Identificativo", `articolo` CharField(50), `ordine` CharField(50) (**string, no FK**), `data` DateTimeField `auto_now=True` (overwritten on every save), `reparto` FK `Plan.Location` null, **DO_NOTHING**, `note` TextField null/blank, `file` FileField(`upload_to="Reclami"`) **required**. No `__str__`, no Meta.

### 1.13 Quality control: `Test`, `Check`, `ControlloQlta`

- **`Check`** (`:336-341`): `nome` CharField(50), `descrizione` CharField(50). A single control type (e.g. "visual", "electrical"). Code relies on **hard-coded PKs** `Check id=9` = drawing check, `id=8` = BOM (distinta) check (`Prod/traccia.py:104,111`).
- **`Test`** (`:322-334`): test template — `nome`, `descrizione` CharField(50), `tipo` CharField(3) choices `LIST_TEST` default `CMP`, `misura_min`/`misura_max`/`percentuale` FloatField null/blank (unused by code), `checks` M2M `Check` (required, not blank), `checkDistinta`/`checkDisegno` nullable BooleanField (camelCase, inconsistent).
- **`ControlloQlta`** (`:277-290`): one QC result row.
  | Field | Type | Notes |
  |---|---|---|
  | `ordine` | CharField(50) null/blank | "Ordine/Stock", string `zfill(7)`, no FK |
  | `codiceProdotto` | CharField(50) | camelCase |
  | `check_rif` | FK `Prod.Check` null/blank **DO_NOTHING** | |
  | `data_inserimento` | DateTimeField, **no auto, required** | set by view |
  | `campione` | **CharField(50)** | sampled quantity stored as text |
  | `ko` | **CharField(50)** | number of KO pieces *or* "Presente"/"Assente" for drawing/BOM checks — **mixed semantics** |
  | `qtaInOrdine` | CharField(50) null/blank | quantity as text |
  `__str__` returns `self.ordine` (may be None → TypeError). `get_absolute_url` → "Tracciabilita_detail" (copy-paste, wrong). Read by Reports dashboard (`Reports/dashboard.py:37`).

### 1.14 `DisplayTest` — display test log (`Prod/models.py:626-652`)

`prodotto` CharField(50) choices `LIST_PRODOTTI_DISPLAY` default `RAFFAELLO28`; `sito` CharField(100); `articolo` CharField(100); `esito` CharField(3) choices `LIST_ESITO`; `fw`, `lotto` CharField(50) null/blank; `data` DateField; `note` TextField null/blank; `data_inserimento` DateTimeField auto_now_add; `utente` FK User SET_NULL null/blank. Meta ordering `['-data','-data_inserimento']`.
**Bug**: the class has two `get_absolute_url` definitions (`:647-649` and `:651-652`); the second (leftover from `StoricoScadenza`) wins and reverses `"StoricoScadenza_detail"`; there is also unreachable `return f"Modifica del {self.data_modifica}"` (`:649`).

### 1.15 Mail ingestion: `ProcessedEmail`, `Email`, `SentEmail`

- **`ProcessedEmail`** (`:306-312`): `message_id` **IntegerField unique** (it actually stores the IMAP **UID**, valid only per mailbox/UIDVALIDITY), `subject` CharField(250) null, `data_inserimento` DateTimeField **auto_now** (label "Data inserimento"). Dedup table for order-mail import (`Prod/orders.py:1541,1600,1715`, `readMail.py`). `__str__` returns subject (may be None).
- **`Email`** (`:314-320`): `message_id` CharField(255) unique, `subject` CharField(255), `body` TextField. **Dead** in Prod (not referenced by any Prod code).
- **`SentEmail`** (`:480-494`): outbound mail log written by `Prod/mailing.py:61-72` only on success. `subject` CharField(255), `body` TextField, `from_address` EmailField, `to_address` **EmailField but stores comma-separated lists** (`Prod/pianificazione.py:1210`), `cc_address` CharField(500), `reply_address` EmailField null/blank, `sent_at` auto_now_add, `attachment_path` CharField(512) (absolute/relative filesystem path; admin converts it to a `/media/` URL, `Prod/admin.py:162-182`), `attachment_name` CharField(255), `success` Bool default False (never False in practice), `error_message` TextField (never written).

### 1.16 `Errori` — import/anomaly log (`Prod/models.py:386-404`)

`name` CharField(50) (source, e.g. "Importazione Ordini"), `testo` TextField, `rif_ordine`/`serie`/`appendice`/`anno` CharField(50) **required** (but filled with `f"{None}"` strings), `data_errore` DateTimeField auto_now_add, `accettato` Bool default False (acknowledged in Console, `Prod/console.py:143-162`). Verbose plural "Erroris". No FK.

### 1.17 `Anagrafica` — finished-product master (*anagrafica articoli finiti*) (`Prod/models.py:343-373`)

Business meaning: one row per sold product code (`Linea.articolo`), giving the department responsible (*reparto di competenza*), unit of measure, reference price, and which weekly *tabulato* (production sheet) it belongs to. Auto-populated by `scMaintenance` from unseen `Linea.articolo` values (`Prod/maintenance.py:24-41`).

| Field | Type | Notes |
|---|---|---|
| `articolo` | CharField(50) | **not unique** (duplicates are deleted by maintenance job instead of constrained), no index although joined by subquery everywhere (`OuterRef('articolo')`) |
| `location` | FK `Plan.Location` null/blank **DO_NOTHING** | reparto; default `GK01-UDEF` ("undefined") on creation |
| `um` | CharField(3) choices `LIST_UM` default `EA` | |
| `prezzo` | FloatField null/blank | reference price (compared to `Linea.prezzo_ea` in Reports price-check) |
| `risorsa_1/2/3` | FK `Plan.Resource` null/blank **DO_NOTHING**, related_name `risorsa_1/2/3` (pollutes Resource namespace) | main/backup operators; **unused by code** |
| `description` | TextField null/blank | English field name among Italian ones; shown in order modal |
| `tabulato` | CharField(10) choices "0".."5" default "0" null/blank | weekly sheet assignment (`Prod/allocazione.py`) |
| `prezzo_ricalcolato` | FloatField null/blank | recalculated price (Reports storico prezzi) |
| `data_prezzo` | DateField null/blank | |
| `nuovo_articolo` | Bool default True | "Codice Nuovo" flag |

Commented-out M2M `resources` (`:348`). No Meta.

### 1.18 Warehouse / BOM models (newer, cleaner)

**`CodiceComponente`** (`:407-432`) — component master (*anagrafica componenti*).
`codice` CharField(50); `variante` CharField(20) null/blank; `descrizione` TextField null/blank; `um` CharField(3) choices `LIST_UM` default EA; `prezzo_medio` FloatField null/blank; `fornitore` CharField(100) null/blank; `note` TextField; `attivo` Bool default True; `data_creazione` auto_now_add; `data_modifica` auto_now.
Meta: `unique_together=['codice','variante']` (**ineffective when `variante` is NULL** in PostgreSQL — NULLs are distinct; the migration command stores `None` for empty variants, `migrate_to_codice_componente.py:55`), `ordering=['codice','variante']`. `__str__` = `codice.variante`.

**`Inventario`** (`:435-453`) — stock count snapshot.
`componente` FK `CodiceComponente` null/blank **CASCADE**; legacy `articolo` CharField(50), `variante` CharField(50) null/blank (kept "per compatibilità durante la migrazione"); `qta` FloatField; `data` DateTimeField (required, not auto). Plural "Inventarios". No unique per (componente, data).

**`RichiesteMateriali`** (`:455-477`) — material request from a department to the warehouse with up to 3 escalation e-mails.
`codice` CharField(50); `variante` CharField(50) null/blank; `data_richiesta` DateField (label typo "Data Richuesta"); `data_ricezione` DateField null/blank; `numero_richieste` IntegerField null/blank (how many times requested); `mail_inviata_1/2/3` BooleanField **no default** (required on create); `materiale_ricevuto` Bool no default; `reparto` FK `Plan.Location` **DO_NOTHING required**; `materiale_urgente` Bool no default. **Not linked to `CodiceComponente`** (string code); case normalisation and dedup are done by management commands (§6) rather than constraints. E-mails go to `EMAIL_MAGAZZINO_GEKO`, `EMAIL_MAGAZZINO_DMG_1/2` (`Prod/inventario.py:269-272`).

**`DDTIngresso`** (`:497-513`) — incoming supplier DDT (photo + OCR or file/XML import).
`numero_ddt` CharField(50) null/blank; `data_ddt` DateField null/blank; `fornitore` CharField(100) null/blank; `data_elaborazione` auto_now_add; `foto_originale` ImageField(`ddt_ingresso/`); `foto_binaria` ImageField(`ddt_ingresso/binarie/`) (pre-processed for OCR); `elaborato` Bool default False; `errore_elaborazione` TextField; `utente` FK User **CASCADE** required. No uniqueness on (fornitore, numero_ddt, data_ddt) — import code groups by that tuple (`Prod/inventario.py:781,1104`).

**`MaterialeIngresso`** (`:516-533`) — rows of an incoming DDT.
`ddt` FK `DDTIngresso` CASCADE related_name `materiali`; `codice` CharField(50); `variante` CharField(20); `descrizione` CharField(200); `quantita` FloatField; `unita_misura` CharField(10) free text (not `LIST_UM`); `prezzo_unitario` **DecimalField(10,2)** (the only correctly typed price in Prod); `data_ingresso` DateField; `verificato` Bool default False; `note` TextField. Not linked to `CodiceComponente`.

**`DistintaBase`** (`:536-560`) — bill of materials header (*distinta base*) for a finished product.
`codice_padre` CharField(50) (finished product code = `Anagrafica.articolo`, but **no FK**); `variante` CharField(20) null/blank; `descrizione`; `data_creazione` auto_now_add; `data_modifica` auto_now; `utente_creazione`/`utente_modifica` FK User SET_NULL, related_name `distinte_create`/`distinte_modificate`; `attiva` Bool default True; `note`. Meta `unique_together=['codice_padre','variante']` (same NULL caveat), ordering `-data_creazione`.

**`ComponenteDistinta`** (`:563-586`) — BOM line.
`distinta` FK `DistintaBase` CASCADE related_name `componenti`; `componente` FK `CodiceComponente` null/blank **CASCADE** (deleting a component master silently removes it from every BOM); legacy `codice_componente` CharField(50), `variante` CharField(20); `quantita` FloatField; `um` CharField(3) choices `LIST_UM`; `posizione` IntegerField default 0; `note`. Meta ordering `['distinta','posizione','codice_componente']`. No uniqueness per (distinta, componente).

### 1.19 `StoricoScadenza` — due-date change history (`Prod/models.py:589-613`)

"Polymorphic" (comment) — exactly one of `ordine` (FK Ordine CASCADE, related_name `storico_scadenze`) or `linea` (FK Linea CASCADE, same related_name) should be set, **not enforced** (no CheckConstraint). `data_scadenza_precedente`, `data_scadenza_nuova` DateField required; `data_modifica` auto_now_add; `utente` FK User SET_NULL null (blank False); `motivo` TextField ("Motivo del Posticipo"). Meta ordering `-data_modifica`. `__str__` returns `None` if neither FK set. Its `get_absolute_url` accidentally lives in `DisplayTest` (see 1.14). Written by `vwCambiaOrdine` (`Prod/views.py:110-116`) and `Prod/orders.py`; templates reference URL names `storico_scadenze_linea`, `posticipa_scadenza_linea` that **are not defined** in any urls.py.

### 1.20 Entity relationship summary

```
auth.User ─< Note >─M2M─ Ordine ─(FK DO_NOTHING)→ Plan.Location
                           │  ^ (FK SET_NULL)            ^
                           │  └── Tracciabilita ─(DO_NOTHING)→ Plan.Location, Plan.Asset
                           │          │ (DO_NOTHING)
                           │          └→ Stored_Item ─(DO_NOTHING)→ Plan.Coding, Linea
                           │      WorkOrderTraccia ─(DO_NOTHING)→ Tracciabilita, Plan.Resource
                           └< StoricoScadenza >─ Linea
  Ordine ≈ Linea ≈ DocumentiTrasporto ≈ FileOrdini ≈ Errori ≈ ControlloQlta.ordine ≈ Reclami.ordine
        (joined only by (serie, rif_ordine, appendice, anno) strings — "≈")
  Linea.articolo ≈ Anagrafica.articolo ≈ DistintaBase.codice_padre  (string joins)
  Anagrafica ─(DO_NOTHING)→ Plan.Location, Plan.Resource×3
  DistintaBase ─< ComponenteDistinta ─(CASCADE)→ CodiceComponente <─(CASCADE) Inventario
  DDTIngresso ─< MaterialeIngresso      RichiesteMateriali ─(DO_NOTHING)→ Plan.Location
  Test >─M2M─< Check <─(DO_NOTHING) ControlloQlta
  standalone: Configuration, ProcessedEmail, Email, SentEmail, File, DisplayTest→User
```

### 1.21 Cross-cutting data-model defects (for the rewrite)

| # | Problem | Where |
|---|---|---|
| D1 | No FK Linea→Ordine; 4-string composite key duplicated in 6 tables; renaming an order needs a manual `update()` of lines (`Prod/views.py:103`) | models 52-116, 164-176, 375-392 |
| D2 | No unique constraint on order key; duplicates possible and actively detected (`ordiniConsegnatiDoppi`, `Prod/console.py:269`) | `Prod/models.py:241-248` |
| D3 | Prices/discounts/quantities as text with comma decimals | `Linea.prezzo_ea`, `sconto_*`, `ControlloQlta.campione/ko/qtaInOrdine`, `Configuration.valore`, `gekoSAS_orders.Prezzo*` |
| D4 | `anno`, `n_riga` as strings; lexicographic ordering of row numbers (`order_by('n_riga')`, `Prod/orders.py:1066`) | |
| D5 | Tri-state nullable booleans on `Linea` flags | `Prod/models.py:93-98` |
| D6 | `DO_NOTHING` on 14 FKs (Ordine.location, Stored_Item.codice/linea, Tracciabilita.stored_item/location/asset, Reclami.reparto, ControlloQlta.check_rif, WorkOrderTraccia.traccia/risorsa, Anagrafica.location/risorsa_1-3, RichiesteMateriali.reparto) → IntegrityError on parent delete in PostgreSQL | |
| D7 | `auto_now=True` on fields named/used as creation date (`Tracciabilita.data_inserimento`, `Note.data`, `Reclami.data`, `ProcessedEmail.data_inserimento`) | |
| D8 | `unique_together` with nullable `variante` is ineffective (CodiceComponente, DistintaBase) | `:423`, `:551` |
| D9 | `Configuration.chiave` not unique; `Anagrafica.articolo` not unique (dedup by cron job) | `:119`, `:344` |
| D10 | Legacy duplicated columns during half-finished migration (`Inventario.articolo/variante`, `ComponenteDistinta.codice_componente/variante`) | `:438-439`, `:568-569` |
| D11 | Free-text string refs where FKs belong: `RichiesteMateriali.codice`, `MaterialeIngresso.codice`, `DistintaBase.codice_padre`, `Reclami.ordine`, `ControlloQlta.ordine` | |
| D12 | Missing indexes on hot filters: `DocumentiTrasporto(inviato)`, `(no_ddt, data_ddt)`; `Anagrafica.articolo`; `Configuration.chiave`; `Tracciabilita(chiave, data)`; `FileOrdini` key; `RichiesteMateriali(materiale_ricevuto, reparto)` | |
| D13 | `get_absolute_url` reverses non-existent URL names on almost every model; `__str__` crashes on nulls (DocumentiTrasporto, Stored_Item, WorkOrderTraccia, ControlloQlta, ProcessedEmail, StoricoScadenza) | |
| D14 | Naming inconsistencies: Italian/English mix (`description`, `codiceProdotto`, `checkDistinta`), snake vs camel, `Stored_Item`, `Erroris`, `Inventarios`, typo labels ("dtt", "Richuesta") | |
| D15 | Dead models: `File`, `Email` (and `Stored_Item.data_out`, `Anagrafica.risorsa_*`, `Test.misura_*/percentuale`, `WorkOrderTraccia.risorsa` never written) | |
| D16 | Hard-coded PKs in business logic (`Check id 8/9`) | `Prod/traccia.py:104,111` |
| D17 | Committed migration (0001) contradicts models; real migrations untracked | `.gitignore:5`, `Prod/migrations/0001_initial.py:44-70` |

---

## 2. Models required from other apps

### 2.1 Prod → other apps (imports found)

| Import | Where | Purpose |
|---|---|---|
| `Plan.models.Location` | `Prod/models.py:6,171,257,273,345,466`; `Prod/views.py:6`; `Prod/traccia.py:7`; `Prod/console.py:7`; `Prod/pianificazione.py:11`; `Prod/allocazione.py:3`; `Prod/orders.py:823`; `Prod/maintenance.py` (via `from .orders import *`) | *Reparto*/department. Looked up by `path__code` (e.g. `"GK01-UDEF"` default reparto, `Prod/maintenance.py:27`; trace endpoints pass `location` = path code). Displayed by `.area`. Planning iterates `Location.objects.all()` and maps `SOGLIA_<AREA>` thresholds by `area.upper().replace(' ','_')` (`Prod/pianificazione.py:55-66`). |
| `Plan.models.Asset` | `Prod/models.py:6,258`; `Prod/traccia.py:7,625` | Machine/test bench that produced a trace (`Asset.objects.get(code=asset)`). |
| `Plan.models.Coding` | `Prod/models.py:132` ("Plan.Coding"); `Prod/traccia.py:7,180-187,644`; `Prod/views.py:6` | Serial/internal code of traced items (`internalCode` unique, `rawMaterialCode` = cable lot). |
| `Plan.models.Resource` | `Prod/models.py:295,349-351` | Operators on WorkOrderTraccia/Anagrafica (unused in logic). |
| `Plan.models.Annunci` | `Prod/views.py:6,38`; `Prod/annunci.py:4`; `Prod/forms.py:3` | Announcement banner shown to `Geko` users; Prod hosts its CRUD UI. Expiry handled by Plan scheduler (`Plan/updater.py:8-10`). |
| `Plan.models.AllocazionePersonale` | `Prod/allocazione.py:3,165` | Which `Resource`s are allocated to which `Location` (weekly sheets). |
| `Plan.views.index` | `Prod/views.py:13`; `Prod/traccia.py:12,594` | Redirect target after closing a WorkOrderTraccia. |
| `Reports.models.gekoSAS_orders` | `Prod/migrateDb.py:7` | Staging table for the legacy GekoSAS MySQL import (§6). |
| `Tickets.models.Ticket` | `Prod/orders.py:1055` (lazy import) | Order modal lists tickets whose `Ticket.ordine` (CharField) == `"serie.rif_ordine.appendice.anno"`. Templates link `ticket_create_from_order`, `ticket_close_from_order`, `ticket_list`, `ticket_detail`, `ticket_create`, `ticket_month` (Tickets URLs). |
| `django.contrib.auth.models.User` | `Prod/models.py:5` | Note, DDTIngresso, DistintaBase, StoricoScadenza, DisplayTest |
| `auth.Group` (by name) | all Prod views | see §5 |

Timbratura and AuditTrail models are **not imported by Prod**.

### 2.2 Other apps → Prod (reverse coupling)

| Consumer | Prod symbols | Use |
|---|---|---|
| `Reports/produzione.py:12` | `Anagrafica, DocumentiTrasporto, Ordine, Linea, Configuration` | Production list, price checks (Linea.prezzo_ea vs Anagrafica.prezzo), planning, anagrafica export/edit, price history, DDT analysis, order rows export, billing (*fatturazione*: monthly summary, delivered value, scorporate rows), productivity (hours from Timbratura vs DDT value), material per reparto. All rely on `PREZZO_RESA_ORARIA` and string-join `rif_ordine__in` (**ignores serie/anno → cross-year false matches**, e.g. `Reports/produzione.py:31,36`). |
| `Reports/dashboard.py:3,5` | `Ordine, Anagrafica, ControlloQlta, Tracciabilita, Linea`, **and `Prod.admin.AnagraficaAdmin, LineaAdmin`** (importing admin classes in a view module) | KPIs: on-time deliveries (`data_consegna <= data_scadenza`), area breakdown, pieces/metres delivered by `Anagrafica.um`. |
| `Reports/report.py:3,9` | `Ordine, Anagrafica, ControlloQlta, Tracciabilita, Linea`, template filter `Prod.templatetags.custom_filters.toHours` | reports |
| `Reports/views.py:8` | `Ordine, Anagrafica, ControlloQlta, Tracciabilita` | report index/search |
| `AuditTrail/signals.py:5`, `AuditTrail/views.py:7` | `Ordine, Linea` | audit (§7) |
| `Tickets/fetcher.py:22` | `Configuration` | IMAP creds `MAIL_TICKET`, `MAIL_TICKET_PSW`, `EMAIL_SERVER` (`Tickets/fetcher.py:81-92`) |
| `Tickets/views.py:213` | `Ordine` (+ `ordine.note`) | create ticket from order |
| `Plan/views.py:8,24` | `WorkOrderTraccia` | open battery WOs on Plan dashboard |
| `Reports/templates/Reports/produzione/tplLineeOrdine.html`, `Permission/templates/base.html:30` | link to `/PRODUZIONE` | navigation |

### 2.3 Minimal subset DuMetodo must carry over

| Entity | Must carry? | Minimal fields | Rationale |
|---|---|---|---|
| `auth.User`, `auth.Group` | Yes | standard + groups `Geko`, `Geko Admin`, `Customer`, `GekoCapRep` (or replace with roles) | authZ everywhere |
| `Plan.Location` (+ `locPath`, `Site`) | Yes — as a local `Reparto` table | `id`, `area` (name), `path.code` (stable code like `GK01-UDEF`) | FK target of Ordine, Anagrafica, Tracciabilita, Reclami, RichiesteMateriali; lookup by path code from benches; planning thresholds keyed by area name. `Site`/`parentPath` can be flattened. |
| `Plan.Asset` | Yes if traceability (cables) is kept | `code`, `name`, `location` | bench identification in `insertTracciaCavi` |
| `Plan.Coding` | Yes if battery/cable traceability is kept | `internalCode` (unique), `rawMaterialCode` | could be merged into `Stored_Item` as `serial`, `lot` |
| `Plan.Resource` | Optional | `name`, `lastname`, `code`, `user`, `active` | only via unused Anagrafica/WorkOrderTraccia fields and AllocazionePersonale |
| `Plan.AllocazionePersonale` | Yes if weekly *tabulati* are kept | `location` (unique), `resources` M2M | `Prod/allocazione.py:165` |
| `Plan.Annunci` | Optional | `date`, `description`, `active`, `activatedAt`, `permanente` + expiry job | banner feature hosted in Prod UI |
| `Reports.gekoSAS_orders` | **No** | — | legacy MySQL staging, functions not scheduled (§4) |
| `Tickets.Ticket` | Integration point only | link by commessa string | keep an extension point / API |
| `Timbratura` | No (consumed by Reports only) | — | |

---

## 3. Settings (`Metodo/settings.py`)

| Item | Value / behaviour | Line |
|---|---|---|
| `.env` | `python-dotenv` `load_dotenv()` | `:4-5` |
| `SECRET_KEY` | env `SECRET_KEY`, **fallback to an insecure literal** | `:16` |
| `DEBUG` | env `DEBUG`, **defaults to True** | `:17` |
| `ALLOWED_HOSTS` | `127.0.0.1`, production hostname, a public IPv4, `localhost` (hard-coded) | `:19` |
| HTTPS | `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`, HSTS preload/subdomains with `SECURE_HSTS_SECONDS = 5` (meaningless with preload) | `:22-27` |
| `INSTALLED_APPS` | admin/auth/…, `Plan`, `Reports`, `Permission`, `Timbratura`, `Prod`, `Tickets`, `django_bootstrap5`, `import_export`, `widget_tweaks`, `AuditTrail` | `:35-52` |
| `MIDDLEWARE` | Django defaults only — **`AuditTrail.middleware.AuditTrailMiddleware` missing** | `:54-62` |
| Templates | `APP_DIRS=True`, no project DIRS | `:66-80` |
| **Databases** | single `default`: `postgresql_psycopg2`, NAME `metodo`, USER `pt`, PASSWORD env `DB_KEY`, HOST `localhost`, `statement_timeout=30000` ms | `:89-100` |
| DB routers | **none** (no `DATABASE_ROUTERS`, no `dbRouter.py`) | — |
| External DB | GekoSAS **MySQL** reached directly with `mysql.connector` (not a Django DB) | `Prod/migrateDb.py:13-18`, `Prod/orders.py:1817-1821`, `Reports/gekosas.py` |
| Password validators | only `NumericPasswordValidator` (others commented) | `:108-121` |
| i18n | `LANGUAGE_CODE='en-us'`, **`TIME_ZONE='UTC'`**, `USE_TZ=True` (cron times are UTC) | `:127-133` |
| Static | `STATICFILES_DIRS=[BASE_DIR/'Metodo/static']`, `STATIC_ROOT=BASE_DIR/'staticfiles'`, `STATIC_URL='/static/'` | `:140-145` |
| Media | `MEDIA_ROOT = BASE_DIR/'Metodo/media/'`, `MEDIA_URL='/media/'` (+ duplicate `MEDIA_DIR`) | `:156-158` |
| Media served by Django | `static(settings.MEDIA_URL, ...)` appended unconditionally → **all uploads (DDT photos, complaints, order files, mail attachments, logs in `media/logs/`) are publicly downloadable without login** if the web server routes `/media/` to Django or serves the folder | `Metodo/urls.py:27`, `settings.py:182,192,202` |
| Auth URLs | `LOGIN_URL='/login/'`, `LOGIN_REDIRECT_URL='/login/'` | `:153-154` |
| Logging | 3 `TimedRotatingFileHandler` (daily, 30 backups) into `Metodo/media/logs/`: `schedulazioni.log` (logger `schedule`), `ordini.log` (logger `ordini`), `metodo.log` (logger `metodo`) | `:160-228` |
| Import/Export | `IMPORT_EXPORT_FORMATS=[XLSX]` | `:7` |
| E-mail | **No Django `EMAIL_*` settings.** SMTP is done by hand in `Prod/mailing.py:22-74` (port 587 + STARTTLS) with host/user/password from `Configuration` (`EMAIL_SERVER`, `EMAIL_METODO_USER`, `EMAIL_METODO_PASSWORD`). IMAP (imapclient) for order mail uses `EMAIL_ORDINI_USER/PASSWORD` + `EMAIL_SERVER` (`Prod/orders.py:1527-1537`); tickets mailbox uses `MAIL_TICKET/MAIL_TICKET_PSW`. |
| Settings tracked despite ignore | `Metodo/settings.py` is listed in `.gitignore` but is committed | `.gitignore:13` |

Working-directory–relative paths: many modules use `'Metodo/media/...'` relative paths (`Prod/maintenance.py:48`, `Prod/orders.py:1521-1522`, `Prod/console.py:229-239,256,264`, `Prod/pianificazione.py:1203`, `readMail.py:16-17`) — they only work if the process CWD is the repo root. DuMetodo should derive everything from `MEDIA_ROOT`.

### 3.1 Root URL map (`Metodo/urls.py`)

`login/`, four `logout/<APP>` routes (all named `logout` — name collision), `accounts/` (auth), `PLAN/`, `REPORTS/`, `PERMISSION/`, `TIMBRATURA/`, `PRODUZIONE/` → `Prod.urls`, `TICKETS/`, `''` → `Permission.views.home`, `admin/`. **`AuditTrail.urls` is not included** → audit views unreachable.

### 3.2 Media sub-folders used by Prod

`file/` (FileOrdini), `Reclami/`, `ddt_ingresso/`, `ddt_ingresso/binarie/`, `mailAttachments/` (+ `pianificazione_reports/`), `attachmentExtract/` (unpacked order .txt awaiting import — the "stuck orders" queue shown in Console, `Prod/console.py:254-260`), `fileTecnici/` (DDT technical files, purged after 30 days by `scMaintenance`), `logs/`.

---

## 4. Background scheduler (APScheduler)

Started from `ProdConfig.ready()` (`Prod/apps.py:8-10`) → `StartScheduler()` (`Prod/updater.py:12-20`) using a module-global `BackgroundScheduler`. **No guard** against running in every worker / management command / autoreload (contrast with Tickets, `Tickets/apps.py:12-27`, which skips management commands and the autoreloader parent). With N gunicorn workers, every job runs N times concurrently.

| Job name | Callable | Trigger (UTC) | What it does |
|---|---|---|---|
| `FetchOrderByMail` | `Prod.orders.scFetchOrderByMail` (`Prod/orders.py:1518`) | interval 30 min | IMAP login (creds from Configuration), search `NOT DELETED HAS ATTACHMENT`, skip UIDs in `ProcessedEmail`, save `.zip` attachments to `media/mailAttachments`, unzip into `media/attachmentExtract`, delete PDFs, record `ProcessedEmail`, update `Configuration["ID_EMAIL"]` (last UID). Errors → `Errori`. |
| `ImporthOrderByMail` (typo) | `Prod.orders.scImporthOrderByMail` (`:1755`) | interval 1 h | For each `.txt` in `attachmentExtract`: parse `@`-separated file (header UTF-8, rows latin-1) with columns `N.ro Ordine, Periodo riferimento, Serie numerazione, Appendice, N.ro prog. riga, Cod. Articolo, Variante, Quantita, Prezzo finale, Sconto unitario, Data consegna (ddmmyyyy)` → `insertLinea(...)` per row, then delete file. Not transactional: a crash mid-file leaves a half-imported order and the file is retried. |
| `Maintenance` | `Prod.maintenance.scMaintenance` (`Prod/maintenance.py:16`) | interval 10 h | (a) create `Anagrafica` for every `Linea.articolo` not yet present, price = first `prezzo_ea` seen, reparto `GK01-UDEF` (`Location.objects.get` raises if missing — the `if uloc` check is useless); (b) delete duplicate `Anagrafica` rows (O(n²) loop, may delete the "good" row); (c) delete files in `Metodo/media/fileTecnici` older than 30 days. |
| `SendPianificazioneReportEmail` | `Prod.pianificazione.scSendPianificazioneReportEmail` (`Prod/pianificazione.py:1235`) | cron 06:00 **UTC** daily (incl. weekends) | Build 4-week planning XLSX (hours per reparto/week vs `SOGLIA_*` thresholds), save under `media/mailAttachments/pianificazione_reports/`, e-mail to `EMAIL_REPORT_PRODUZIONE_TO` (cc `EMAIL_REPORT_PRODUZIONE_CC`) from `EMAIL_METODO_USER`, reply-to hard-coded address (`:1143`), link hard-coded to production URL (`:1142`). |
| *(disabled)* `CreateWorkOrder` | `Prod.traccia.scCreateWorkOrders` | was 1 h | open WorkOrderTraccia for batteries with last trace >115 days |
| *(disabled)* | `Prod.console.scAddDescriptionFromOrders` | was 20 h (and wrongly named `ImporthOrderByMail`) | fill `Anagrafica.description` from order files |
| imported, never scheduled | `scSyncOrders` (`Prod/orders.py:1792`), `scLoadOrderFromGeko` (`Prod/migrateDb.py:11`) | — | legacy GekoSAS MySQL sync (§6) |

Other schedulers in the same process: Plan (`Plan/updater.py:13-17`: `createPM` every 20 min, `deactivateExpiredAnnunci` every 10 min — the latter affects the Prod announcements banner) and Tickets (`Tickets/updater.py:13-29`: fetch tickets every 5 min).

Manual triggers via Console (`Prod/console.py`, `Geko Admin` only): `?maintenance`, `?readMail`, `?loadOrders`, POST `readMailDateRange` (`scFetchOrderByMailDateRange`, `Prod/orders.py:1611`), `?updateAnagrafica`, `?distinctTraccia` (dedupe Tracciabilita within 5 min), `?deleteAttachment=<filename>`, `?AckError`, `?AckAllErrors`, POST `update_prices` (bulk set `Linea.prezzo_ea` for an article in a date range via `.update()`, `Prod/console.py:168-187`).

---

## 5. Groups and access control

Group checks are per-module copies of `isGeko` / `isGekoAdmin` / `isCustomer` (`user.groups.filter(name=...).exists()`), defined in `Prod/views.py:16-23`, `orders.py:27-34`, `console.py:16-23`, `traccia.py:32-36`, `ddt.py:23-27`, `etichette.py:24-28`, `files.py:16-20`, `inventario.py:24-28`, `pianificazione.py:18-19`, `allocazione.py:12-13`. Template filter `has_group` (`Prod/templatetags/custom_filters.py:8-10`). Decorator order is `@user_passes_test(...)` **outside** `@login_required` — works (AnonymousUser has no groups → redirect to login) but `login_required` is redundant. Note `Geko Admin` is **not** implied by `Geko`: a user in only `Geko Admin` fails most `isGeko` checks.

| Group | Views (url name → function, file:line) |
|---|---|
| **Geko** (staff) | Pianificazione `vwPianificazione` (pianificazione.py:23), AggiornaSoglia (:389), ExportPianificazioneXLSX (:1131), SendPianificazioneReportEmail (:1250); tabulatiSettimanali/Scorte/Anagrafica (allocazione.py:32,176,291); ListaAnnunci/Crea/Modifica/Toggle (annunci.py:11-66); Log `vwLog` (console.py:228); DDT `vwDDT` (ddt.py:30), GeneraBollaDaDDT (:111); Etichette (etichette.py:31), RistampaEtichette (:148); CreaFoto/ListaFoto (files.py:23,57); Inventario, AddInventario, Richiesta Materiale, inserimento_manuale_ddt, materiali_ddt, importa_ddt_file, importa_ddt_xml, Distinte Base, Anagrafica Componenti, Anagrafica Finiti (inventario.py:33-1524); ListaArticoli (orders.py:812), CaricaOrdini `vwloadOrders` (:1139), SettaOrdine `setOrdine` (:1254), AttivaProduzione (:1861), Consegna (:1883), InserimentoManuale (:2021), Cancella Nota (:2303), ConsegnaSingolo (:2337), ImportaOrdiniXML (:2430); TracciaQualita (traccia.py:77), CreaTraccia (:158), TracciaBatteria (:238), ListaTracce (:318), Print (:559), CreaReclamo (:664), CreaLottoCommessa (:734), CreaDisplay (:776) |
| **Geko or Customer** | ListaOrdini (orders.py:38), EsportaOrdini (:421), ModalOrdine `vwOrdine` (:1010; reply-to address chosen by group at :1025-1035), OrdiniBoard (:2438), OrdiniBoardPulsanti (:2541); ListaQualita (traccia.py:41), ListaBatterie (:402), ListaCavi (:477), ListaReclamo (:701), ListaDisplay (:831) |
| **Geko Admin** | Console `vwConsole` (console.py:27), ScorporazioneMassa (orders.py:1172) |
| **Geko or Geko Admin** | cambia_ordine `vwCambiaOrdine` (views.py:64) |
| **GekoCapRep** | template-only: extra controls in `Ordini/mdlOrdine.html:38`, `Ordini/tplManageOrder.html:79` (OR with Geko Admin) |
| `is_superuser` | template-only blocks in `Console/tplConsole.html:54,65` |
| **login only** | storico_scadenze_ordine (views.py:46) |
| **NO authentication** | ProductionHome `vwProductionHome` (views.py:25), AnnuncioGeko (views.py:35; filters content by group), **GeneraCodice `genCodiceBatteria`** (traccia.py:596), **GestioneWOT `wotHandle`** (traccia.py:587 — state-changing GET, closes WO), **InserisciCavo `insertTracciaCavi`** (traccia.py:605 — state-changing GET creating Ordine/Coding/Linea/Stored_Item/Tracciabilita; used by test benches) |

`Geko Admin` additionally gates UI controls in templates: `Inventario/uixListaDistinteBase.html:67-179`, `Inventario/uixListaMateriale.html:33,104`, `tplCore.html:111`, `Ordini/tplLista.html:228,279`, `Ordini/mdlOrdine.html:63,218,248,277`, `Console/tplConsole.html:176` (UI-only — the server endpoints behind them are frequently only `isGeko`).

Template URL names referenced in Prod templates but **defined nowhere**: `carica_ddt`, `debug_ocr`, `verifica_ddt`, `ricerca_materiali`, `storico_scadenze_linea`, `posticipa_scadenza_linea` (rendering those templates raises `NoReverseMatch` unless guarded) — features described in README (OCR DDT, ricerca materiali) appear removed from urls.

---

## 6. Management commands and `migrateDb.py`

| Command | File | Behaviour |
|---|---|---|
| `elimina_duplicati_richieste [--dry-run]` | `Prod/management/commands/elimina_duplicati_richieste.py` | For non-received `RichiesteMateriali`: upper-case `codice`/`variante` (`:24-42`); group by `(codice, variante or '', reparto.id)`; keep the record with highest (mail_3, mail_2, mail_1, data_richiesta, numero_richieste) (`:90-103`); merge: `numero_richieste = Σ(n or 1)`, `materiale_urgente`/`mail_inviata_*` = any (`:106-124`); delete others. No transaction. Dry-run still counts but `normalized` counter only increments when not dry-run. **Implies business rule: at most one open request per (codice, variante, reparto).** |
| `normalizza_codici_richieste` | `.../normalizza_codici_richieste.py` | Upper-cases `codice`/`variante` of **all** RichiesteMateriali with `save(update_fields=...)`. |
| `migrate_to_codice_componente [--dry-run]` | `.../migrate_to_codice_componente.py` | One-off: collect distinct `(articolo, variante)` from `Inventario` and `(codice_componente, variante)` from `ComponenteDistinta`; create missing `CodiceComponente` (`descrizione='Migrato automaticamente'`, `variante=None` when empty); set `Inventario.componente` / `ComponenteDistinta.componente`. Wrapped in `transaction.atomic()` with savepoint rollback for dry-run (`:23-115`). Indicates migration to `CodiceComponente` is in progress; legacy columns still present. |
| `readMail` | `.../readMail.py` | Older duplicate of `scFetchOrderByMail`: IMAP **with hard-coded host/user/password** (`:22-23`), downloads `.zip` attachments, extracts into `attachmentExtract`, removes PDFs, records `ProcessedEmail`. Does not update `ID_EMAIL`. |
| `cleanup_audit_logs [--days=365] [--dry-run]` | `AuditTrail/management/commands/cleanup_audit_logs.py` | Deletes `AuditLog` older than N days. Not scheduled. |

**`Prod/migrateDb.py`** (legacy import from customer's GekoSAS MySQL; *not scheduled*, imported by `updater.py:5` and wildcard-imported by `maintenance.py:4`):
- `scLoadOrderFromGeko()` (`:11-81`): connects to external MySQL with **hard-coded host/user/password/db** (`:13-18`); keeps a cursor `Configuration["ID_MIGRAZIONE"]` (on first run = max id); fetches 50 rows of `inc_ord` with `id <= cursor` descending; `get_or_create` each into `Reports.gekoSAS_orders` matching on **all 31 columns** (bare `except: pass`, `:74-75`); stores last id. Note the cursor walks *backwards* and the `<=` re-reads the boundary row.
- `createOrderFromGeko()` (`:83-105`): for every `gekoSAS_orders` row whose `Ord_num` is not an existing `Ordine.rif_ordine`, map `Status` (`Consegnato→CON`, `Lavorazione→'Lav'` (invalid), `Bloccato→'BLC'` (invalid), else `INS`), parse `cons_geko` `dd/mm/YYYY`, call `insertLinea(..., appendice="0")`.
- Related `scSyncOrders()` (`Prod/orders.py:1792-1857`): round-robin 10 non-delivered orders per run (cursor `Configuration["IDX_SYNC"]`), query MySQL by **string-concatenated SQL** (`:1824,1846`), map `Status` by **column index** `row[20]` / `row[7]`, update `Ordine.stato` / `data_consegna`. Crashes if `fetchone()` is None.

These GekoSAS functions should be treated as **legacy / out of scope** for DuMetodo unless the customer still exposes that MySQL DB.

### 6.1 `insertLinea` — the canonical import algorithm (`Prod/orders.py:1390-1500`)

(Detailed in the orders spec; summarised here because it defines the data invariants.)
1. Find order by `(rif_ordine, anno, serie, appendice)`; if exists and not `CON`/`ANN` → overwrite `data_consegna`, `data_scadenza`, `stato`; if `CON` → log `Errori("Ordine gia consegnato")` and **skip rows**.
2. If only `(rif_ordine, anno, serie)` exists: appendice `R/T…` → new Ordine; else reuse the appendice-0/empty order.
3. If no order → create.
4. Rows: mark all existing non-annulled rows of the same key inserted **before today** as `annullata=True`; a row "exists" if same key+articolo+variante+n_riga not annulled; if same n_riga but different articolo/variante → mark old ones `duplicata=True`; for appendice "0" also match on `qta`. Existing rows get `data_scadenza`, `qta`, `cliente` updated (note: `qta` arrives as **string** from file and is compared to a float → always "changed", generating an audit log entry per row per import). Otherwise create a new `Linea`.
No `transaction.atomic`, no locking — concurrent scheduler instances can create duplicate orders/rows.

---

## 7. AuditTrail

- **Model** `AuditLog` (`AuditTrail/models.py:16-117`): GenericFK (`content_type` FK CASCADE, `object_id` **PositiveIntegerField** — Prod PKs are BigAutoField, OK until 2^31), `action` (`create|update|delete`), `timestamp` auto_now_add (db_index), `user` FK User SET_NULL related_name `audit_logs`, `object_repr` CharField(200) (`str(instance)` is not truncated — a longer repr fails the insert, which is swallowed), `changes` JSONField `{"old": {...}, "new": {...}}`, `ip_address`, `user_agent`. Explicit `db_table='AuditTrail_auditlog'`; indexes on (content_type, object_id), timestamp (duplicate of db_index), user, action. `get_changes_display()` diffs old/new dicts. No migrations committed (`AuditTrail/migrations/` holds only `__init__.py`).
- **Signals** (`AuditTrail/signals.py`, loaded by `AuditTrailConfig.ready()` `AuditTrail/apps.py:9-11`): only for **`Prod.Ordine` and `Prod.Linea`**.
  - `pre_save` (`:78-94`): re-reads the row from DB and stores its field dict in a **thread-local** `previous_state`.
  - `post_save` (`:97-151`): writes `AuditLog(create|update)` with `{"old": previous_state, "new": current}`; FKs stored as pk, dates as ISO strings; M2M (`Ordine.note`) not captured. Swallows all exceptions (logs to module logger, which has no handler configured).
  - `post_delete` (`:154-190`): writes `delete` with old data.
- **Request context**: `AuditTrailMiddleware` (`AuditTrail/middleware.py`) would set the current request in the same thread-local, but it is **not in `MIDDLEWARE`** (`Metodo/settings.py:54-62`) → `user`, `ip_address`, `user_agent` are always NULL. Scheduler threads also have no request.
- **Gaps**: `QuerySet.update()` bypasses signals — e.g. `vwCambiaOrdine` line updates (`Prod/views.py:103`), Console bulk price update (`Prod/console.py:187`), `Errori ... .update(accettato=True)`; no audit on DocumentiTrasporto, Anagrafica, StoricoScadenza (which is itself a manual audit of due dates), Configuration, inventory/BOM. Every `save()` triggers an extra SELECT (pre_save) + INSERT.
- **Views** (`AuditTrail/views.py`; list, per-object, per-ordine, per-linea, all `@login_required` only) — **unreachable**: `AuditTrail.urls` not included in `Metodo/urls.py`. Admin registration exists (`AuditTrail/admin.py`). Retention via `cleanup_audit_logs` (manual).

DuMetodo recommendation: implement audit at the service layer (explicit `record_change(user, entity, diff)` inside the same transaction) or use a maintained library (django-simple-history / pghistory), covering Ordine, Linea, DDT, Anagrafica, Configuration; capture user from request context.

---

## 8. `Configuration` keys catalogue (all string values)

| Key | Meaning | Read at |
|---|---|---|
| `PREZZO_RESA_ORARIA` | € of production value per worked hour (comma decimal) → hours = value / resa | `Prod/models.py:201`; `Prod/allocazione.py:70,74`; `Prod/pianificazione.py:228,470`; `Reports/produzione.py:32,453,2246` |
| `SOGLIA_<AREA>` | weekly capacity threshold (hours) per reparto, key = `area.upper().replace(' ','_')`; written by `vwAggiornaSoglia` with comma decimal | `Prod/pianificazione.py:57,416,461` |
| `EMAIL_SERVER` | SMTP/IMAP host | `Prod/mailing.py:26`; `Prod/orders.py:1530,1638`; `Tickets/fetcher.py:92` |
| `EMAIL_METODO_USER` / `EMAIL_METODO_PASSWORD` | SMTP login (sender) — **password in clear text** | `Prod/mailing.py:24-25`; `Prod/pianificazione.py:1179` |
| `EMAIL_ORDINI_USER` / `EMAIL_ORDINI_PASSWORD` | IMAP mailbox receiving customer orders — **password in clear text** | `Prod/orders.py:1528-1529,1636-1637` |
| `MAIL_TICKET` / `MAIL_TICKET_PSW` | Tickets IMAP — **password in clear text** | `Tickets/fetcher.py:90-91` |
| `ID_EMAIL` | last processed IMAP UID | `Prod/orders.py:1532` |
| `EMAIL_METODO_ADDRESS` | system "from"/reply address | `Prod/ddt.py:32`; `Prod/inventario.py:269`; `Prod/orders.py:1028,1035` |
| `EMAIL_GEKO_ADDRESS`, `EMAIL_CUSTOMER_ADDRESS` | reply-to for order-modal mails depending on user group | `Prod/orders.py:1026-1035` |
| `EMAIL_FIETECNICO_ADDRESS` | recipient of DDT technical file (DMG) | `Prod/ddt.py:31` |
| `EMAIL_MAGAZZINO_GEKO`, `EMAIL_MAGAZZINO_DMG_1`, `EMAIL_MAGAZZINO_DMG_2` | material-request recipients | `Prod/inventario.py:270-272` |
| `EMAIL_REPORT_PRODUZIONE_TO` / `_CC` | planning report recipients (`,`/`;` separated) | `Prod/pianificazione.py:1156,1163` (also `:358,365`) |
| `ARTICOLO_TRACCIA_BATTERIE` | article code identifying battery rows | `Prod/traccia.py:23` (read **at import time**) |
| `ARTICOLO_TRACCIA_CAVI` | article code for cable rows | `Prod/traccia.py:28` (import time), `:639` |
| `ITEM_CODE_CAVI` | prefix for generated cable `Coding.internalCode` | `Prod/traccia.py:640` |
| `ID_MIGRAZIONE` | GekoSAS import cursor | `Prod/migrateDb.py:22` |
| `IDX_SYNC` | GekoSAS sync round-robin cursor | `Prod/orders.py:1794` |

`valore` max length 50 is too short for recipient lists. DuMetodo: split into typed settings (env/secret store for credentials, a typed `Setting` model or django-constance for business parameters, a `Reparto.soglia_ore` column instead of `SOGLIA_*`).

---

## 9. Security and robustness problems

### 9.1 Hard-coded secrets (values intentionally omitted)
| Secret | Location |
|---|---|
| External MySQL (GekoSAS) host/user/**password**/db | `Prod/migrateDb.py:13-18`; `Prod/orders.py:1817-1821`; `Reports/gekosas.py:8,26,44,62,79,96,113,129,146,171` |
| IMAP host, mailbox user and **password** for orders mailbox | `Prod/management/commands/readMail.py:22-23` |
| Insecure fallback `SECRET_KEY`, `DEBUG` default True | `Metodo/settings.py:16-17` |
| Mail passwords stored in plaintext DB rows (`Configuration`) and editable/exportable via admin import-export | `Prod/models.py:118-120`, `Prod/admin.py:48-50` |
| Hard-coded public IP and hostnames | `Metodo/settings.py:19`; production URL in `Prod/pianificazione.py:1142`, `Prod/templates/Console/tplConsole.html:15`; personal reply-to e-mail `Prod/pianificazione.py:1143` |
→ All of these must be rotated; DuMetodo must read credentials from env/secret manager only.

### 9.2 Injection / unsafe I/O
- **SQL injection** via string concatenation in raw MySQL queries: `Prod/orders.py:1824,1846` (values from DB, but still), `Reports/gekosas.py` (multiple `qry` built from input — out of Prod scope).
- **Path traversal**: `deleteAttachment(filename)` → `os.remove(f'Metodo/media/attachmentExtract/{filename}')` with `filename` from `GET` (`Prod/console.py:136-139,263-267`) (Geko Admin only).
- **Zip-slip**: `zipfile.extractall(extract_dir)` on e-mail attachments from external senders (`Prod/orders.py:1574-1575`, `readMail.py:55-56`); attachment filename from e-mail used directly in `os.path.join` (`readMail.py:48`).
- Uploaded files and logs under `MEDIA_ROOT` served without auth (`Metodo/urls.py:27`).
- `vwLog` reads whole log files into memory and renders them (`Prod/console.py:228-250`).
- Unvalidated `SentEmail` addresses; HTML e-mail body interpolates `user_message` without escaping (`Prod/pianificazione.py:1187`).

### 9.3 AuthN/AuthZ
- Unauthenticated, state-changing GET endpoints: `insertTracciaCavi`, `wotHandle`, `genCodiceBatteria` (§5). No CSRF possible on GET; any internet client can insert traces/orders.
- Many state-changing actions are GET with query params (Console actions, `setOrdine`, `wotHandle`) → CSRF-able.
- Authorization duplicated in ~10 modules; `Geko Admin` UI controls not enforced server-side.
- AuditTrail never records user (middleware missing).

### 9.4 Transactions / concurrency
- Only two `transaction.atomic` uses in Prod (`Prod/views.py:102`, `migrate_to_codice_componente.py:23`). Order import (`insertLinea`, `scImporthOrderByMail`), consign (`vwConsegna` creating DocumentiTrasporto + flagging lines), DDT file generation (`vwDDT` marks rows `inviato` then sends mail — mail failure leaves rows marked sent), maintenance dedup, duplicate-request merge: all **non-atomic**.
- Scheduler runs in every web worker → concurrent duplicate imports; no `select_for_update`, no unique constraints to stop it.
- Configuration cursors (`ID_EMAIL`, `IDX_SYNC`, `ID_MIGRAZIONE`) updated without locking.

### 9.5 Robustness
- Module-level DB queries at import time (`Prod/traccia.py:22-30`) — config changes need a restart; breaks `migrate` on a fresh DB (wrapped in bare except).
- Bare `except:` / `except: pass` swallow errors (`Prod/migrateDb.py:74`, `Prod/traccia.py:23-30,169,181,615-618`, AuditTrail signals).
- `Configuration.objects.get(...)` without handling `DoesNotExist` in many views (`Prod/ddt.py:31-32`, `Prod/inventario.py:269-272`, `Prod/mailing.py:24-26`, `Reports/produzione.py:32`) → 500 errors if a key is missing.
- `__str__` methods that raise on nulls (D13) break admin and logging.
- Float arithmetic on money; comma/dot parsing scattered.
- CWD-relative file paths (§3).
- `scMaintenance` dedup deletes newest/oldest arbitrarily and is O(n²).
- `Prod/models.py` imports `django.forms` field classes (shadowing nothing but confusing).
- `Reports/dashboard.py:3` imports `Prod.admin` classes into view code (admin side-effects at import).
- `TIME_ZONE='UTC'` while users are in Italy: cron at 06:00 UTC, `datetime.datetime.now()` (naive) mixed with `timezone.now()` across code; `DateField` comparisons around midnight may shift.

---

## 10. Recommendations for the DuMetodo data model (derived)

1. `Ordine` with `UniqueConstraint(serie, numero, appendice, anno)`; normalise `appendice` (`''` instead of NULL/`'0'` mix, or explicit enum), `anno` as `PositiveSmallIntegerField`.
2. `RigaOrdine(ordine FK PROTECT, n_riga int, articolo FK Articolo nullable, codice_articolo str, variante, qta Decimal, prezzo_unitario Decimal(12,4), sconto Decimal, stato_riga enum{attiva, annullata, duplicata}, bloccata bool, evasione_parziale bool, scorporata_da FK self nullable, data_scadenza, data_consegna)`; keep `import_batch` FK for re-import diffing instead of date-based annulment.
3. `DDT(numero, anno, data, inviato_at)` + `RigaDDT(ddt FK, riga_ordine FK, qta)` — unique `(numero, anno)`.
4. `Articolo` (finished product, ex-Anagrafica) unique `codice`; `Componente` (ex-CodiceComponente) unique `(codice, variante)` with `variante=''` not NULL; `DistintaBase`/`RigaDistinta` FK to both; `Inventario`, `RichiestaMateriale`, `MaterialeIngresso` FK to `Componente`; `UniqueConstraint` on open requests `(componente, reparto) WHERE ricevuto=false`.
5. `Reparto` local model (replaces Plan.Location) with `codice` (ex path code), `nome`, `soglia_ore_settimanali`.
6. Traceability: `ItemTracciato(serial unique, lotto, riga_ordine FK)`, `EventoTraccia(item FK, ordine FK, tipo, valore, timestamp, reparto FK, postazione FK)`.
7. QC: `Test`, `Controllo`, `EsitoControllo` with integer `campione`, `ko` and a separate boolean/enum for presence checks; no hard-coded PKs (use a `codice` slug).
8. Replace `Configuration` with typed settings + secrets in env; `ResaOraria` as a dated setting (history matters for reports).
9. Background jobs in a dedicated worker (Celery/RQ/django-q or a single APScheduler process with a DB lock), idempotent and transactional.
10. Audit with a history library or explicit service-layer audit, user captured.
