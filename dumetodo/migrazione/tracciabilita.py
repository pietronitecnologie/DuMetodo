"""Importazione da Metodo di tracciabilità e qualità (passo dell'``Importatore``).

- ``Plan.Asset`` -> ``Postazione`` (codici doppi resi univoci, senza token: va generato);
- ``Plan.Coding`` + ``Stored_Item`` -> ``Elemento`` (uno per codice). Il tipo, che in Metodo stava
  nelle righe d'ordine finte, è dedotto: eventi IN/RC -> batteria; eventi da una postazione o codice
  con il prefisso cavi (``ITEM_CODE_CAVI``) -> cavo; altrimenti generico;
- ``Tracciabilita`` -> ``Traccia`` con l'ordine come ForeignKey (era già una FK in Metodo); i doppioni
  inviati dai banchi e i primi ingressi ripetuti vengono saltati e segnalati;
- ``Check``/``Test`` -> ``Controllo``/``Test``. I Check 8 e 9, che il codice di Metodo usava come
  "distinta" e "disegno", diventano i controlli di presenza con quei codici;
- ``ControlloQlta`` (una riga per check, senza test) -> ``ControlloQualita`` raggruppando le righe della
  stessa registrazione; il test è quello che contiene tutti i controlli registrati;
- ``Reclami``, ``DisplayTest``, ``FileOrdini``: gli ordini scritti come testo vengono cercati per chiave;
  i file restano dove sono: va copiata la cartella media di Metodo (``Reclami/``, ``file/``) in MEDIA_ROOT.
- ``WorkOrderTraccia`` non viene importato (in Metodo il job era disattivato e non c'era interfaccia).
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.utils import timezone
from django.utils.text import slugify

from dumetodo.ordini.models import Ordine
from dumetodo.tracciabilita import servizi
from dumetodo.tracciabilita.models import (
    Chiave,
    Controllo,
    ControlloQualita,
    Elemento,
    EsitoControllo,
    EsitoDisplay,
    FileOrdine,
    Postazione,
    ProdottoDisplay,
    Reclamo,
    Test,
    TestDisplay,
    TipoControllo,
    TipoElemento,
    TipoTest,
    Traccia,
)

if TYPE_CHECKING:
    from .importatore import Importatore

# Chiavi primarie dei Check che il codice di Metodo usava per distinta e disegno (traccia.py:104,111).
CHECK_METODO_PRESENZA = {8: "distinta", 9: "disegno"}

CAMPI_DATA_ORIGINALE = (
    (Postazione, "creato_il"),
    (Elemento, "creato_il"),
    (Traccia, "inserita_il"),
    (ControlloQualita, "creato_il"),
    (Reclamo, "creato_il"),
    (TestDisplay, "creato_il"),
    (FileOrdine, "caricato_il"),
)


def _t(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _aware(v: Any) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return timezone.make_aware(v) if timezone.is_naive(v) else v
    if isinstance(v, dt.date):
        return timezone.make_aware(dt.datetime.combine(v, dt.time.min))
    return None


def _intero(v: Any) -> int | None:
    try:
        d = Decimal(_t(v).replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d != d.to_integral_value():
        return None
    return int(d)


def _decimale(v: Any) -> Decimal | None:
    try:
        d = Decimal(_t(v).replace(",", "."))
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.001")) if d.is_finite() and d >= 0 else None


class ImportaTracciabilita:
    def __init__(self, imp: Importatore) -> None:
        self.imp, self.m, self.r = imp, imp.m, imp.r
        self.postazioni: dict[int, Postazione] = {}
        self.controlli: dict[int, Controllo] = {}

    def esegui(self) -> None:
        from .importatore import _date_originali

        with _date_originali(*CAMPI_DATA_ORIGINALE):
            self._postazioni()
            self._tracce()
            self._test()
            self._controlli_qualita()
            self._reclami()
            self._display()
            self._file()

    # ------------------------------------------------------------------ ordini scritti come testo
    def _ordine_da_testo(self, testo: str, quando: dt.date | None) -> Ordine | None:
        testo = testo.strip()
        if not testo:
            return None
        if servizi.FORMATO_RE.match(testo):
            try:
                return servizi.risolvi_ordine(testo)
            except servizi.OperazioneNonConsentita:
                return None
        anni = [quando.year, quando.year - 1] if quando else []
        m = re.fullmatch(r"(\d{1,5})\.(\d{1,10})", testo)
        if m:  # "SERIE.NUMERO" (come lo scrivevano gli operatori): anno dalla data
            for anno in anni:
                o = servizi.cerca_ordine(m[1], m[2], "", anno)
                if o:
                    return o
            return None
        if testo.isdigit() and anni:  # solo numero: accettato solo se non è ambiguo
            trovati = list(Ordine.objects.filter(numero__in={testo, testo.zfill(7)}, appendice="", anno__in=anni)[:2])
            return trovati[0] if len(trovati) == 1 else None
        return None

    # ------------------------------------------------------------------ postazioni ed elementi
    def _postazioni(self) -> None:
        usati: set[str] = set()
        for a in self.m.righe("Plan_asset"):
            codice = _t(a["code"])[:30] or f"ASSET-{a['id']}"
            if codice in usati:
                self.r.avviso("postazioni", f"asset id {a['id']}: codice {codice} doppio, rinominato")
                codice = f"{codice[:22]}-{a['id']}"
            usati.add(codice)
            self.postazioni[a["id"]] = Postazione.objects.create(
                codice=codice,
                nome=_t(a.get("name"))[:100],
                reparto=self.imp.reparti.get(a.get("location_id")),
                creato_il=timezone.now(),
            )
            self.r.conteggi["postazioni (senza token)"] += 1

    def _tracce(self) -> None:
        prefisso_cavi = ""
        for c in self.m.righe("Prod_configuration"):
            if _t(c["chiave"]) == "ITEM_CODE_CAVI":
                prefisso_cavi = _t(c["valore"])
        codifiche = {c["id"]: c for c in self.m.righe("Plan_coding")}
        item_codifica = {s["id"]: s.get("codice_id") for s in self.m.righe("Prod_stored_item")}
        tracce = list(self.m.righe("Prod_tracciabilita"))
        if not tracce:
            return
        # Tipo di ogni codice, dedotto dagli eventi.
        eventi: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for t in tracce:
            cod = item_codifica.get(t.get("stored_item_id"))
            if cod in codifiche:
                eventi[cod].append(t)
        elementi: dict[int, Elemento] = {}
        for cod, lista in eventi.items():
            codice = _t(codifiche[cod]["internalCode"])[:50]
            if not codice:
                self.r.avviso("tracce", f"codifica id {cod} senza codice: eventi tenuti solo se hanno un ordine")
                continue
            if any(_t(t["chiave"]).upper() in (Chiave.INGRESSO, Chiave.RICARICA) for t in lista):
                tipo = TipoElemento.BATTERIA
            elif any(t.get("asset_id") for t in lista) or (prefisso_cavi and codice.startswith(prefisso_cavi)):
                tipo = TipoElemento.CAVO
            else:
                tipo = TipoElemento.GENERICO
            prima = min(_aware(t.get("data")) or _aware(t.get("data_inserimento")) or timezone.now() for t in lista)
            elementi[cod] = Elemento.objects.create(
                codice=codice, tipo=tipo, lotto=_t(codifiche[cod].get("rawMaterialCode"))[:100], creato_il=prima
            )
        self.r.conteggi["elementi tracciati"] += len(elementi)

        nuove: list[Traccia] = []
        chiavi_banco: set[tuple[Any, ...]] = set()
        ingressi: set[int] = set()
        for t in tracce:
            elemento = elementi.get(item_codifica.get(t.get("stored_item_id")))
            ordine = self.imp.ordini_per_id.get(t.get("rif_ordine_id"))
            if elemento is None and ordine is None:
                self.r.conteggi["tracce senza elemento né ordine (ignorate)"] += 1
                continue
            data = _aware(t.get("data")) or _aware(t.get("data_inserimento")) or timezone.now()
            chiave, valore = _t(t["chiave"])[:50] or "?", _t(t.get("valore"))[:100]
            postazione = self.postazioni.get(t.get("asset_id"))
            if postazione is not None:
                k = (postazione.pk, ordine.pk if ordine else None, data, chiave, valore)
                if k in chiavi_banco:  # il vecchio job di pulizia O(n²) diventa un insieme
                    self.r.conteggi["tracce doppie dei banchi (saltate)"] += 1
                    continue
                chiavi_banco.add(k)
            if chiave == Chiave.INGRESSO and elemento is not None:
                if elemento.pk in ingressi:
                    self.r.avviso("tracce", f"{elemento}: primo ingresso ripetuto (traccia id {t['id']}), saltato")
                    continue
                ingressi.add(elemento.pk)
            nuove.append(
                Traccia(
                    elemento=elemento,
                    ordine=ordine,
                    data=data,
                    chiave=chiave,
                    valore=valore,
                    reparto=self.imp.reparti.get(t.get("location_id")),
                    postazione=postazione,
                    inserita_il=_aware(t.get("data_inserimento")) or data,
                )
            )
        Traccia.objects.bulk_create(nuove, batch_size=2000)
        self.r.conteggi["tracce"] += len(nuove)

    # ------------------------------------------------------------------ qualità
    def _test(self) -> None:
        presenza = {c.codice: c for c in Controllo.objects.filter(codice__in=CHECK_METODO_PRESENZA.values())}
        for c in self.m.righe("Prod_check"):
            if c["id"] in CHECK_METODO_PRESENZA and CHECK_METODO_PRESENZA[c["id"]] in presenza:
                self.controlli[c["id"]] = presenza[CHECK_METODO_PRESENZA[c["id"]]]
                continue
            nome = _t(c["nome"])[:100] or f"Controllo {c['id']}"
            codice = slugify(nome)[:40] or "controllo"
            if Controllo.objects.filter(codice=codice).exists():
                codice = f"{codice}-{c['id']}"
            self.controlli[c["id"]] = Controllo.objects.create(
                codice=codice, nome=nome, descrizione=_t(c.get("descrizione"))[:200], tipo=TipoControllo.QUANTITA
            )
            self.r.conteggi["controlli qualità"] += 1
        legami: dict[int, set[int]] = defaultdict(set)
        for tc in self.m.righe("Prod_test_checks"):
            legami[tc["test_id"]].add(tc["check_id"])
        for t in self.m.righe("Prod_test"):
            nome = _t(t["nome"])[:100] or f"Test {t['id']}"
            codice = slugify(nome)[:40] or "test"
            if Test.objects.filter(codice=codice).exists():
                codice = f"{codice}-{t['id']}"
            test = Test.objects.create(
                codice=codice,
                nome=nome,
                descrizione=_t(t.get("descrizione"))[:200],
                tipo=t.get("tipo") if t.get("tipo") in TipoTest.values else TipoTest.CAMPIONE,
            )
            controlli = [self.controlli[c] for c in legami.get(t["id"], set()) if c in self.controlli]
            for flag, codice_presenza in (("checkDistinta", "distinta"), ("checkDisegno", "disegno")):
                if t.get(flag) and codice_presenza in presenza:
                    controlli.append(presenza[codice_presenza])
            test.controlli.set(controlli)
            self.r.conteggi["test qualità"] += 1

    def _test_per(self, controlli: set[int], cache: dict[str, Any]) -> Test:
        if "test" not in cache:
            cache["test"] = [(t, {c.pk for c in t.controlli.all()}) for t in Test.objects.prefetch_related("controlli")]
        adatti = [(len(c), t.pk, t) for t, c in cache["test"] if controlli <= c]
        if adatti:
            return min(adatti, key=lambda x: (x[0], x[1]))[2]
        if "generico" not in cache:
            cache["generico"] = Test.objects.create(
                codice="importato-da-metodo",
                nome="Importato da Metodo",
                descrizione="Controlli di Metodo che non corrispondono a nessun test.",
                attivo=False,
            )
            self.r.avviso(
                "controlli qualità", "alcune registrazioni non corrispondono a un test: test 'importato-da-metodo'"
            )
        cache["generico"].controlli.add(*controlli)
        return cache["generico"]

    def _controlli_qualita(self) -> None:
        # In Metodo ogni check era una riga a sé, senza riferimento al test: si raggruppano le righe
        # della stessa registrazione (stesso ordine, prodotto e data).
        gruppi: dict[tuple[str, str, Any], list[dict[str, Any]]] = defaultdict(list)
        for r in self.m.righe("Prod_controlloqlta"):
            gruppi[(_t(r.get("ordine"))[:50], _t(r["codiceProdotto"])[:50], r["data_inserimento"])].append(r)
        cache: dict[str, Any] = {}
        for (testo_ordine, articolo, quando), righe in gruppi.items():
            data = _aware(quando)
            giorno = timezone.localtime(data).date() if data else dt.date(2000, 1, 1)
            esiti: dict[int, EsitoControllo] = {}
            for r in righe:
                controllo = self.controlli.get(r.get("check_rif_id"))
                if controllo is None or controllo.pk in esiti:
                    self.r.avviso("controlli qualità", f"riga id {r['id']}: controllo mancante o ripetuto, saltata")
                    continue
                if controllo.tipo == TipoControllo.PRESENZA:
                    esiti[controllo.pk] = EsitoControllo(
                        controllo=controllo, presente=_t(r["ko"]).lower() == "presente"
                    )
                    continue
                campione, ko = _intero(r["campione"]), _intero(r["ko"])
                if campione is None or ko is None or ko > campione:
                    self.r.avviso(
                        "controlli qualità",
                        f"riga id {r['id']}: campione {r['campione']!r} / KO {r['ko']!r} non validi",
                    )
                    continue
                esiti[controllo.pk] = EsitoControllo(controllo=controllo, campione=campione, ko=ko)
            if not esiti:
                continue
            ordine = self._ordine_da_testo(testo_ordine, giorno)
            qta = next((_decimale(r.get("qtaInOrdine")) for r in righe if _t(r.get("qtaInOrdine"))), None)
            cq = ControlloQualita.objects.create(
                ordine=ordine,
                riferimento="" if ordine else (testo_ordine or "?"),
                articolo=articolo or "?",
                qta_in_ordine=qta,
                test=self._test_per(set(esiti), cache),
                data=giorno,
                creato_il=data or timezone.now(),
            )
            for e in esiti.values():
                e.controllo_qualita = cq
            EsitoControllo.objects.bulk_create(esiti.values())
            self.r.conteggi["controlli qualità registrati"] += 1
            if testo_ordine and ordine is None:
                self.r.conteggi["controlli qualità con ordine non trovato (tenuto come riferimento)"] += 1

    # ------------------------------------------------------------------ reclami, display, file
    def _reclami(self) -> None:
        for r in self.m.righe("Prod_reclami"):
            data = _aware(r.get("data"))
            giorno = timezone.localtime(data).date() if data else dt.date(2000, 1, 1)
            testo = _t(r["ordine"])[:50]
            ordine = self._ordine_da_testo(testo, giorno)
            file = _t(r.get("file"))[:255]
            Reclamo.objects.create(
                codice=_t(r["codice"])[:50] or "?",
                ordine=ordine,
                riferimento_ordine="" if ordine else testo,
                articolo=_t(r["articolo"])[:50],
                reparto=self.imp.reparti.get(r.get("reparto_id")),
                data=giorno,
                note=_t(r.get("note")),
                file=file,
                file_nome_originale=file.rsplit("/", 1)[-1],
                file_tipo=(mimetypes.guess_type(file)[0] or "application/octet-stream") if file else "",
                creato_il=data or timezone.now(),
            )
            self.r.conteggi["reclami"] += 1
            if testo and ordine is None:
                self.r.avviso("reclami", f"reclamo id {r['id']}: ordine {testo!r} non trovato, tenuto come testo")

    def _display(self) -> None:
        nuovi = []
        for d in self.m.righe("Prod_displaytest"):
            if d["esito"] not in EsitoDisplay.values or d.get("data") is None:
                self.r.avviso("test display", f"id {d['id']}: esito {d['esito']!r} o data non validi, saltato")
                continue
            nuovi.append(
                TestDisplay(
                    prodotto=d.get("prodotto")
                    if d.get("prodotto") in ProdottoDisplay.values
                    else ProdottoDisplay.RAFFAELLO28,
                    sito=_t(d["sito"])[:100],
                    articolo=_t(d["articolo"])[:100],
                    esito=d["esito"],
                    fw=_t(d.get("fw"))[:50],
                    lotto=_t(d.get("lotto"))[:50],
                    data=d["data"],
                    note=_t(d.get("note")),
                    utente=self.imp.utenti.get(d.get("utente_id")),
                    creato_il=_aware(d.get("data_inserimento")) or timezone.now(),
                )
            )
        TestDisplay.objects.bulk_create(nuovi, batch_size=2000)
        self.r.conteggi["test display"] += len(nuovi)

    def _file(self) -> None:
        for f in self.m.righe("Prod_fileordini"):
            anno = _intero(f.get("anno"))
            ordine = None
            if anno:
                ordine = servizi.cerca_ordine(_t(f.get("serie")), _t(f["rif_ordine"]), _t(f.get("appendice")), anno)
            nome = _t(f["file"])[:255]
            if ordine is None or not nome:
                self.r.avviso("file ordini", f"file id {f['id']} ({nome}): ordine non trovato, saltato")
                continue
            FileOrdine.objects.create(
                ordine=ordine,
                file=nome,
                nome_originale=nome.rsplit("/", 1)[-1],
                tipo_contenuto=mimetypes.guess_type(nome)[0] or "application/octet-stream",
                dimensione=0,
                caricato_il=_aware(f.get("data_inserimento")) or timezone.now(),
            )
            self.r.conteggi["file ordini (da copiare dalla cartella media di Metodo)"] += 1
